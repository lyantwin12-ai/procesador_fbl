import os
import json
import io
from fastapi import FastAPI, UploadFile, File, HTTPException
import pdfplumber
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

app = FastAPI(title="Procesador FBL PDF")

# Configurar credenciales desde la variable de entorno de Render
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

@app.get("/")
def read_root():
    return {"status": "ok", "message": "Servidor de procesamiento FBL activo"}

@app.post("/process-pdf")
async def process_pdf(
    spreadsheet_id: str,
    sheet_name: str = "Hoja 1",
    file: UploadFile = File(...)
):
    try:
        # Leer contenido del PDF cargado
        pdf_bytes = await file.read()
        
        extracted_data = []
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables()
                for table in tables:
                    for row in table:
                        # Filtrar filas vacías
                        if any(row):
                            extracted_data.append(row)
                            
        if not extracted_data:
            raise HTTPException(status_code=400, detail="No se pudo extraer información o tablas del PDF.")
        
        # Conectar con Google Sheets
        gc = get_gspread_client()
        sh = gc.open_by_key(spreadsheet_id)
        
        try:
            worksheet = sh.worksheet(sheet_name)
        except Exception:
            worksheet = sh.add_worksheet(title=sheet_name, rows="1000", cols="20")
            
        # Limpiar e insertar datos en la hoja
        worksheet.append_rows(extracted_data)
        
        return {
            "status": "success",
            "message": f"Se insertaron {len(extracted_data)} filas en Google Sheets correctamente.",
            "spreadsheet_id": spreadsheet_id
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
