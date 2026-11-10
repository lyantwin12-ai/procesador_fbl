import os
import json
import asyncio
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timedelta
from playwright.async_api import async_playwright
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# ---------------------------------------------------------
# 0. Servidor HTTP de salud y visor de capturas
# ---------------------------------------------------------
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/error.png":
            if os.path.exists("error_login.png"):
                self.send_response(200)
                self.send_header("Content-type", "image/png")
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                self.end_headers()
                with open("error_login.png", "rb") as f:
                    self.wfile.write(f.read())
                return
            else:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"No hay capturas de pantalla guardadas aun.")
                return

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK - Croamis Bot Running")

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

def run_health_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever()

# ---------------------------------------------------------
# 1. Variables de Entorno y Configuración
# ---------------------------------------------------------
GOOGLE_JSON_STR = os.environ.get("GOOGLE_CREDENTIALS_JSON")
STORAGE_STATE_STR = os.environ.get("CROAMIS_STORAGE_STATE")

DRIVE_FOLDER_ID = "1AuP_PBoGOTDugGKRsefAcS_QtU6exJ4X"

# Postas específicas para la carpeta 'depurador barrido'
POSTAS_DEPURADOR = ["GRU", "LIM", "SCL"]

URL_REPORT = "https://croamis.latamcargo.com/croamis/reportFlightSchedule?a=SCH"

def log(mensaje):
    """Imprime mensajes de log forzando el volcado inmediato al buffer de Render."""
    print(mensaje, flush=True)

async def guardar_captura_final(context, fallback_page=None, etiqueta=""):
    try:
        page_to_capture = context.pages[-1] if (context and context.pages) else fallback_page
        if page_to_capture:
            await page_to_capture.screenshot(path="error_login.png", full_page=True)
            log(f"📸 [CAPTURA GUARDADA] Captura generada ({etiqueta}). Disponible en /error.png")
    except Exception as e:
        log(f"⚠️ Error generando captura de pantalla: {e}")

def subir_a_google_drive(ruta_local, nombre_archivo, parent_id=DRIVE_FOLDER_ID):
    if not GOOGLE_JSON_STR:
        log("⚠️ [ADVERTENCIA] No se configuró GOOGLE_CREDENTIALS_JSON. Omitiendo subida.")
        return

    try:
        info_credenciales = json.loads(GOOGLE_JSON_STR)
        creds = Credentials.from_service_account_info(
            info_credenciales, 
            scopes=['https://www.googleapis.com/auth/drive']
        )
        service = build('drive', 'v3', credentials=creds)

        file_metadata = {
            'name': nombre_archivo,
            'parents': [parent_id]
        }
        media = MediaFileUpload(
            ruta_local, 
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            resumable=True
        )

        archivo_subido = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id'
        ).execute()

        log(f"   ☁️ Subido con éxito a Google Drive (ID: {archivo_subido.get('id')})")
    except Exception as e:
        log(f"   ❌ Error subiendo a Google Drive: {e}")

async def descargar_reporte(page, fecha_manana, posta_codigo, ruta_destino):
    """Descarga el reporte Excel. Si posta_codigo es None o '', limpia el campo para traer todas las postas."""
    inputs_fecha = page.locator('input[title*="Flight Period"], input[name*="date"], input[type="text"]')
    await inputs_fecha.nth(0).fill(fecha_manana)
    await inputs_fecha.nth(1).fill(fecha_manana)

    input_brd_pt = page.locator('xpath=//td[contains(., "Brd Pt")]/following-sibling::td//input | input[name*="brdPt"]').first
    await input_brd_pt.clear()
    
    if posta_codigo:
        await input_brd_pt.fill(posta_codigo)

    async with page.expect_download() as download_info:
        flecha_desplegable = page.locator('xpath=//button[contains(@class, "ui-splitbutton")]//span | div:has-text("Generate") button | .ui-menu-button').first
        await flecha_desplegable.click()

        opcion_excel = page.locator('text="Generate As EXCEL"')
        await opcion_excel.click()

    download = await download_info.value
    await download.save_as(ruta_destino)

async def run():
    log("[INFO] Iniciando ejecución de tarea_diaria.py...")
    
    # 1. Crear carpetas locales de destino
    dir_barrido_hdc = os.path.join(os.getcwd(), "barrido hdc")
    dir_depurador = os.path.join(os.getcwd(), "depurador barrido")
    os.makedirs(dir_barrido_hdc, exist_ok=True)
    os.makedirs(dir_depurador, exist_ok=True)

    # 2. Calcular la fecha del día siguiente a la fecha local
    fecha_manana_dt = datetime.now() + timedelta(days=1)
    fecha_manana_str = fecha_manana_dt.strftime("%d-%b-%Y").upper()
    log(f"[INFO] 📅 Fecha de consulta (Mañana): {fecha_manana_str}")

    # 3. Cargar estado de sesión guardado si existe en Render
    storage_state_obj = None
    if STORAGE_STATE_STR:
        try:
            storage_state_obj = json.loads(STORAGE_STATE_STR)
            log("[INFO] ✅ Estado de sesión pre-autenticado cargado correctamente.")
        except Exception as e:
            log(f"⚠️ No se pudo procesar CROAMIS_STORAGE_STATE: {e}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled"
            ]
        )
        
        context_kwargs = {
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "accept_downloads": True,
            "viewport": {"width": 1280, "height": 720}
        }
        if storage_state_obj:
            context_kwargs["storage_state"] = storage_state_obj

        context = await browser.new_context(**context_kwargs)
        page = await context.new_page()

        log("[INFO] Navegando directamente al reporte de Croamis...")
        await page.goto(URL_REPORT, wait_until="commit", timeout=60000)
        await asyncio.sleep(5)

        # Verificar si la sesión guardada ingresó directamente al portal
        try:
            await page.wait_for_selector('text="Flight Schedule Report"', timeout=20000)
            log("🎉 ¡Acceso directo exitoso a Croamis utilizando la sesión guardada!")
        except Exception:
            log("⚠️ La sesión guardada expiró o requirió interacción. Tomando captura de diagnóstico...")
            await guardar_captura_final(context, page, "Sesion Expirada")
            raise RuntimeError("La sesión expiró. Es necesario renovar CROAMIS_STORAGE_STATE.")

        await guardar_captura_final(context, page, "Portal Croamis Cargado Exitosamente")

        # ---------------------------------------------------------
        # BLOQUE 1: Archivo General Consolidado (Todas las 28 postas en un solo Excel)
        # ---------------------------------------------------------
        log(f"\n📂 [1/4] Descargando Archivo General Consolidado para fecha {fecha_manana_str}...")
        nombre_general = f"Barrido_HDC_General_{fecha_manana_str}.xlsx"
        ruta_general = os.path.join(dir_barrido_hdc, nombre_general)

        # Se envía None en posta para que extraiga el consolidado completo
        await descargar_reporte(page, fecha_manana_str, None, ruta_general)
        log(f"   ✅ Guardado en 'barrido hdc': {nombre_general}")
        subir_a_google_drive(ruta_general, nombre_general)
        await asyncio.sleep(2)

        # ---------------------------------------------------------
        # BLOQUE 2: Archivos Específicos (GRU, LIM, SCL) -> Carpeta 'depurador barrido'
        # ---------------------------------------------------------
        log(f"\n📂 [2/4] Descargando reportes para 'depurador barrido' (GRU, LIM, SCL)...")
        for i, posta in enumerate(POSTAS_DEPURADOR, 2):
            log(f" [{i}/4] 🔄 Descargando reporte específico: {posta}...")
            nombre_depurador = f"Depurador_{posta}_{fecha_manana_str}.xlsx"
            ruta_depurador = os.path.join(dir_depurador, nombre_depurador)

            await descargar_reporte(page, fecha_manana_str, posta, ruta_depurador)
            log(f"   ✅ Guardado en 'depurador barrido': {nombre_depurador}")
            await asyncio.sleep(2)

        log(f"\n🎉 [COMPLETADO] Proceso finalizado exitosamente. Los 4 archivos fueron descargados y organizados.")
        await browser.close()

if __name__ == "__main__":
    t = threading.Thread(target=run_health_server, daemon=True)
    t.start()

    asyncio.run(run())
