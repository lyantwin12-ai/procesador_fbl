import os
import json
import io
import re
import tempfile
from datetime import datetime
from fastapi import FastAPI, UploadFile, File, HTTPException
import pdfplumber
import pandas as pd
import numpy as np
import gspread
from gspread_dataframe import set_with_dataframe
from gspread_formatting import CellFormat, TextFormat, Color, format_cell_range
from google.oauth2.service_account import Credentials

app = FastAPI(title="Procesador FBL PDF - Render")

def get_gspread_client():
    creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
    if not creds_json:
        raise ValueError("No se encontró la variable de entorno GOOGLE_CREDENTIALS_JSON")
    
    info = json.loads(creds_json)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    credentials = Credentials.from_service_account_info(info, scopes=scopes)
    return gspread.authorize(credentials)

def limpiar_a_float(valor_str):
    if not valor_str:
        return 0.0
    v = str(valor_str).strip().replace(" ", "")
    if "\\" in v:
        v = v.split("\\")[0]

    if "," in v and "." not in v:
        v = v.replace(",", ".")
    elif "," in v and "." in v:
        v = v.replace(",", "")

    try:
        return float(v)
    except ValueError:
        return 0.0

def procesar_pdf_bytes(pdf_bytes, nombre_pdf):
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            paginas_texto = []
            tablas_paginas = []

            for pagina in pdf.pages:
                texto = pagina.extract_text()
                if texto:
                    paginas_texto.append(texto)

                tabla = pagina.extract_tables()
                if tabla:
                    tablas_paginas.extend(tabla)

            texto_completo = "\n".join(paginas_texto)
    except Exception as e:
        return None, f"Error al leer PDF: {e}"

    if not texto_completo:
        return None, "PDF vacío o inaccesible"

    # 1. DETECTAR VUELO Y FECHA
    vuelo = "SIN_VUELO"
    fecha_vuelo = datetime.now().strftime("%Y%m%d")

    vuelo_pattern = re.compile(r'\b(LA|XL|L7|M3|UC)\s*[- ]?\s*(\d+)\b', re.IGNORECASE)
    fecha_pattern = re.compile(r'(\d{2}-[A-Z]{3}-\d{2,4})', re.IGNORECASE)

    m_vuelo = vuelo_pattern.search(texto_completo)
    if m_vuelo:
        vuelo = f"{m_vuelo.group(1).upper()}{m_vuelo.group(2)}"

    m_fecha = fecha_pattern.search(texto_completo)
    if m_fecha:
        fecha_str = m_fecha.group(1)
        for fmt in ("%d-%b-%Y", "%d-%b-%y"):
            try:
                fecha_vuelo = datetime.strptime(fecha_str, fmt).strftime("%Y%m%d")
                break
            except ValueError:
                pass

    hoja_vuelo = f"{vuelo}_{fecha_vuelo}"
    es_capacity_cut = "CAPACITY CUT" in texto_completo.upper() or "DOCUMENT NUMBER" in texto_completo.upper()
    dict_registros = {}

    # 2. EXTRAER REGISTROS CON SOPORTE MULTILÍNEA
    if es_capacity_cut:
        awb_actual = None
        for tabla in tablas_paginas:
            idx_pcs_col = -1
            idx_weight_col = -1

            for fila in tabla:
                fila_l = [c.replace('\n', ' ').strip() if c else "" for c in fila]
                for idx, celda in enumerate(fila_l):
                    celda_upper = celda.upper()
                    if "PIECES BOOKED" in celda_upper or "PCS BOOKED" in celda_upper:
                        idx_pcs_col = idx
                    elif "WEIGHT BOOKED" in celda_upper or "WT BOOKED" in celda_upper:
                        if "CHW" not in celda_upper and "CHARGEABLE" not in celda_upper:
                            idx_weight_col = idx

            for fila in tabla:
                fila_l = [c.replace('\n', ' ').strip() if c else "" for c in fila]

                idx_awb = -1
                for i in range(min(4, len(fila_l) - 1)):
                    if re.fullmatch(r'\d{3}', fila_l[i]) and re.fullmatch(r'\d{8}', fila_l[i+1]):
                        idx_awb = i
                        break

                if idx_awb != -1:
                    awb_actual = f"{fila_l[idx_awb]}-{fila_l[idx_awb+1]}"
                    prioridad = 1
                    if idx_awb + 2 < len(fila_l) and fila_l[idx_awb+2].isdigit():
                        prioridad = int(fila_l[idx_awb+2])

                    pcs = 0
                    peso = 0.0

                    if idx_pcs_col != -1 and idx_pcs_col < len(fila_l):
                        pcs = int(limpiar_a_float(fila_l[idx_pcs_col]))

                    if idx_weight_col != -1 and idx_weight_col < len(fila_l):
                        peso = limpiar_a_float(fila_l[idx_weight_col])

                    if pcs == 0 and peso == 0.0:
                        nums = [limpiar_a_float(elem) for elem in fila_l[idx_awb+3:] if limpiar_a_float(elem) > 0]
                        if len(nums) >= 2:
                            pcs = int(nums[0])
                            peso = nums[1]

                    if awb_actual not in dict_registros:
                        dict_registros[awb_actual] = ["", "", "", prioridad, pcs, peso]
                    else:
                        dict_registros[awb_actual][4] += pcs
                        dict_registros[awb_actual][5] += peso

                elif awb_actual is not None:
                    pcs_extra = 0
                    peso_extra = 0.0

                    if idx_pcs_col != -1 and idx_pcs_col < len(fila_l):
                        pcs_extra = int(limpiar_a_float(fila_l[idx_pcs_col]))

                    if idx_weight_col != -1 and idx_weight_col < len(fila_l):
                        peso_extra = limpiar_a_float(fila_l[idx_weight_col])

                    if pcs_extra > 0 or peso_extra > 0:
                        dict_registros[awb_actual][4] += pcs_extra
                        dict_registros[awb_actual][5] += peso_extra
    else:
        lineas = texto_completo.splitlines()
        for linea in lineas:
            linea_clean = linea.strip()
            if re.match(r'^\d{3}-\d{8}', linea_clean):
                tokens = linea_clean.split()
                awb = tokens[0]
                prioridad = 1
                pcs = 0
                peso = 0.0

                idx_hk = next((i for i, t in enumerate(tokens) if t in ("HK", "WL", "BK")), None)
                if idx_hk is not None and idx_hk >= 3:
                    peso = limpiar_a_float(tokens[idx_hk - 2])
                    pcs = int(limpiar_a_float(tokens[idx_hk - 3]))

                for i in range(1, len(tokens)):
                    if re.fullmatch(r'\d{1,2}', tokens[i]):
                        val_prio = int(tokens[i])
                        if 1 <= val_prio <= 99:
                            prioridad = val_prio
                            break

                dict_registros[awb] = ["", "", "", prioridad, pcs, peso]

    registros_finales = [[awb] + datos for awb, datos in dict_registros.items()]
    if not registros_finales:
        return None, "No se encontraron AWBs válidos en el PDF."

    df_vuelo = pd.DataFrame(
        registros_finales,
        columns=["AWB", "ORIG", "DEST", "PRODUCT", "P", "PCS", "PESO"]
    )

    df_vuelo["P"] = pd.to_numeric(df_vuelo["P"], errors="coerce").fillna(1).astype(int)
    df_vuelo["PCS"] = pd.to_numeric(df_vuelo["PCS"], errors="coerce").fillna(0).astype(int)
    df_vuelo["PESO"] = pd.to_numeric(df_vuelo["PESO"], errors="coerce").fillna(0.0).astype(float)

    return {
        "df": df_vuelo,
        "nombre_pdf": nombre_pdf,
        "vuelo": vuelo,
        "fecha_vuelo": fecha_vuelo,
        "hoja_vuelo": hoja_vuelo
    }, None

def guardar_resultados(sh, resultado):
    df_vuelo = resultado['df']
    nombre_pdf = resultado['nombre_pdf']
    vuelo = resultado['vuelo']
    fecha_vuelo = resultado['fecha_vuelo']
    hoja_vuelo = resultado['hoja_vuelo']

    # Asegurar hojas base
    for nombre in ["CONTROL_FBL", "BOOKING_HISTORICO"]:
        try:
            sh.worksheet(nombre)
        except Exception:
            sh.add_worksheet(title=nombre, rows=1000, cols=30)

    ws_control = sh.worksheet("CONTROL_FBL")
    ws_hist = sh.worksheet("BOOKING_HISTORICO")

    if ws_control.acell("A1").value is None:
        ws_control.update(range_name="A1:F1", values=[["ARCHIVO", "VUELO", "FECHA_VUELO", "AWBS", "PESO_TOTAL", "FECHA_PROCESO"]])

    if ws_hist.acell("A1").value is None:
        ws_hist.update(range_name="A1:J1", values=[["VUELO", "FECHA_VUELO", "ARCHIVO", "AWB", "ORIG", "DEST", "PRODUCT", "P", "PCS", "PESO"]])

    # Estadísticas
    total_awb = len(df_vuelo)
    peso_total = float(df_vuelo['PESO'].sum())

    stats = df_vuelo.groupby('P').agg(
        AWBS=('AWB', 'count'),
        PESO=('PESO', 'sum')
    ).reset_index()

    peso_p1 = float(stats.loc[stats['P'] == 1, 'PESO'].sum()) if 1 in stats['P'].values else 0
    peso_p2 = float(stats.loc[stats['P'] == 2, 'PESO'].sum()) if 2 in stats['P'].values else 0
    peso_p3 = float(stats.loc[stats['P'] == 3, 'PESO'].sum()) if 3 in stats['P'].values else 0

    awb_p1 = int(stats.loc[stats['P'] == 1, 'AWBS'].sum()) if 1 in stats['P'].values else 0
    awb_p2 = int(stats.loc[stats['P'] == 2, 'AWBS'].sum()) if 2 in stats['P'].values else 0
    awb_p3 = int(stats.loc[stats['P'] == 3, 'AWBS'].sum()) if 3 in stats['P'].values else 0

    peso_p1_p2 = peso_p1 + peso_p2
    awb_p1_p2 = awb_p1 + awb_p2

    pct_p1 = round((peso_p1 / peso_total * 100), 2) if peso_total else 0
    pct_p2 = round((peso_p2 / peso_total * 100), 2) if peso_total else 0
    pct_p3 = round((peso_p3 / peso_total * 100), 2) if peso_total else 0
    pct_p1_p2 = round((peso_p1_p2 / peso_total * 100), 2) if peso_total else 0

    # Crear hoja del vuelo
    hoja_nombre = hoja_vuelo[:31]
    try:
        ws_vuelo = sh.worksheet(hoja_nombre)
    except Exception:
        ws_vuelo = sh.add_worksheet(title=hoja_nombre, rows=1000, cols=20)

    ws_vuelo.clear()

    resumen_data = [
        ["VUELO", vuelo],
        ["FECHA", fecha_vuelo],
        ["AWBS", int(total_awb)],
        ["PESO TOTAL", round(peso_total, 2)],
        [],
        ["PRIORIDAD", "AWBS", "PESO", "% PESO"],
        ["P1", int(awb_p1), round(peso_p1, 2), pct_p1],
        ["P2", int(awb_p2), round(peso_p2, 2), pct_p2],
        ["P3", int(awb_p3), round(peso_p3, 2), pct_p3],
        ["P1+P2", int(awb_p1_p2), round(peso_p1_p2, 2), pct_p1_p2],
        ["TOTAL", int(total_awb), round(peso_total, 2), 100.0]
    ]

    ws_vuelo.update(range_name="A1:D11", values=resumen_data)

    set_with_dataframe(ws_vuelo, df_vuelo, row=13, include_index=False)

    # Formato visual
    if len(df_vuelo) > 0:
        format_cell_range(ws_vuelo, "A1:B4", CellFormat(textFormat=TextFormat(bold=True, fontSize=12)))

        gris = CellFormat(backgroundColor=Color(0.90, 0.90, 0.90), textFormat=TextFormat(bold=True))
        rojo = CellFormat(backgroundColor=Color(1, 0.85, 0.85))
        amarillo = CellFormat(backgroundColor=Color(1, 1, 0.80))
        verde = CellFormat(backgroundColor=Color(0.85, 1, 0.85))

        format_cell_range(ws_vuelo, "A6:D6", gris)
        format_cell_range(ws_vuelo, "A7:D7", rojo)
        format_cell_range(ws_vuelo, "A8:D8", amarillo)
        format_cell_range(ws_vuelo, "A9:D9", verde)
        format_cell_range(ws_vuelo, "A10:D10", gris)
        format_cell_range(ws_vuelo, "A11:D11", gris)
        format_cell_range(ws_vuelo, "A13:G13", gris)

        rangos_p1 = []
        rangos_p2 = []
        rangos_p3 = []

        for fila_excel, prioridad in enumerate(df_vuelo['P'].values, start=14):
            rango = f"A{fila_excel}:G{fila_excel}"
            if prioridad == 1:
                rangos_p1.append(rango)
            elif prioridad == 2:
                rangos_p2.append(rango)
            elif prioridad == 3:
                rangos_p3.append(rango)

        if rangos_p1:
            format_cell_range(ws_vuelo, ",".join(rangos_p1), rojo)
        if rangos_p2:
            format_cell_range(ws_vuelo, ",".join(rangos_p2), amarillo)
        if rangos_p3:
            format_cell_range(ws_vuelo, ",".join(rangos_p3), verde)

        ws_vuelo.freeze(rows=13)

    sh.batch_update({
        "requests": [
            {
                "autoResizeDimensions": {
                    "dimensions": {
                        "sheetId": ws_vuelo.id,
                        "dimension": "COLUMNS",
                        "startIndex": 0,
                        "endIndex": 7
                    }
                }
            }
        ]
    })

    # Actualizar Histórico
    df_hist = df_vuelo.copy()
    df_hist.insert(0, "ARCHIVO", nombre_pdf)
    df_hist.insert(0, "FECHA_VUELO", fecha_vuelo)
    df_hist.insert(0, "VUELO", vuelo)

    hist_values = df_hist.values.tolist()
    if len(ws_hist.get_all_values()) <= 1:
        set_with_dataframe(ws_hist, df_hist, include_index=False)
    else:
        ws_hist.append_rows(hist_values)

    # Control FBL
    ws_control.append_row([
        nombre_pdf,
        vuelo,
        fecha_vuelo,
        int(total_awb),
        round(peso_total, 2),
        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ])

    return hoja_vuelo, total_awb

@app.get("/")
def read_root():
    return {"status": "ok", "message": "Servidor de procesamiento FBL activo"}

@app.post("/process-pdf")
async def process_pdf(
    spreadsheet_id: str,
    file: UploadFile = File(...)
):
    try:
        pdf_bytes = await file.read()
        nombre_pdf = file.filename or "corte.pdf"

        resultado, error = procesar_pdf_bytes(pdf_bytes, nombre_pdf)
        if error:
            raise HTTPException(status_code=400, detail=error)

        gc = get_gspread_client()
        sh = gc.open_by_key(spreadsheet_id)

        hoja, awbs = guardar_resultados(sh, resultado)

        return {
            "status": "success",
            "message": f"Vuelo {resultado['vuelo']} ({resultado['fecha_vuelo']}) procesado correctamente. Hoja '{hoja}' generada con {awbs} AWBs.",
            "hoja": hoja
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
