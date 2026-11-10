import os
import json
import asyncio
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime
import pyotp
from playwright.async_api import async_playwright
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# ---------------------------------------------------------
# 0. Servidor HTTP de salud y visor de capturas de error
# ---------------------------------------------------------
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/error.png":
            if os.path.exists("error_login.png"):
                self.send_response(200)
                self.send_header("Content-type", "image/png")
                self.end_headers()
                with open("error_login.png", "rb") as f:
                    self.wfile.write(f.read())
                return
            else:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"No hay capturas de error guardadas.")
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
# 1. Variables de Entorno
# ---------------------------------------------------------
TOTP_SECRET = os.environ.get("CROAMIS_2FA_SECRET")
USUARIO = os.environ.get("CROAMIS_USER")
PASSWORD = os.environ.get("CROAMIS_PASSWORD")
GOOGLE_JSON_STR = os.environ.get("GOOGLE_CREDENTIALS_JSON")

DRIVE_FOLDER_ID = "1AuP_PBoGOTDugGKRsefAcS_QtU6exJ4X"

POSTAS = [
    # Norteamérica
    "MIA", "MCO", "BOS", "JFK", "ATL", "LAX", "SEA", "MEX", "NLU", "ORD", "HSV", "KIN",
    # Europa
    "MAD", "FRA", "CDG", "BCN", "FCO", "MXP", "AMS", "BRU", "LGG", "ZAZ", "JNB", "CPT", "LIS", "LHR",
    # Oceanía
    "SYD", "MEL", "AKL",
    # Centroamérica
    "PTY", "SDQ", "GEO", "SJO", "GUA"
]

URL_REPORT = "https://croamis.latamcargo.com/croamis/reportFlightSchedule?a=SCH"

def log(mensaje):
    """Imprime mensajes de log forzando el volcado inmediato al buffer de Render."""
    print(mensaje, flush=True)

def subir_a_google_drive(ruta_local, nombre_archivo):
    """Suba un archivo local a la carpeta especificada en Google Drive."""
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
            'parents': [DRIVE_FOLDER_ID]
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

async def obtener_locator_login(context, selector, timeout=45000):
    """Busca un selector dinámico a través de todas las páginas y frames activos."""
    start_time = asyncio.get_event_loop().time()
    while (asyncio.get_event_loop().time() - start_time) < (timeout / 1000):
        for p in context.pages:
            try:
                loc = p.locator(selector).first
                if await loc.count() > 0:
                    return loc, p
                for frame in p.frames:
                    f_loc = frame.locator(selector).first
                    if await f_loc.count() > 0:
                        return f_loc, frame
            except Exception:
                pass
        await asyncio.sleep(0.5)
    raise TimeoutError(f"No se encontró el selector '{selector}' en ningún frame ni página activa.")

async def run():
    log("[INFO] Iniciando ejecución de tarea_diaria.py...")
    if not TOTP_SECRET or not USUARIO or not PASSWORD:
        raise ValueError("Error: Faltan variables de entorno requeridas en Render.")

    carpeta_destino = os.path.join(os.getcwd(), "barrido hdc")
    os.makedirs(carpeta_destino, exist_ok=True)

    totp = pyotp.TOTP(TOTP_SECRET)
    token_2fa = totp.now()
    fecha_hoy = datetime.now().strftime("%d-%b-%Y").upper()
    log(f"[INFO] Código 2FA generado exitosamente: {token_2fa[:2]}***")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled"
            ]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            accept_downloads=True,
            viewport={"width": 1280, "height": 720}
        )
        page = await context.new_page()

        log("[INFO] 1. Navegando al portal Croamis...")
        try:
            await page.goto(URL_REPORT, wait_until="commit", timeout=45000)
        except Exception as e:
            log(f"[INFO] Estado navegación inicial: {e}")

        await asyncio.sleep(4)

        # Clic forzado directo en "LOGIN WITH SSO"
        log("[INFO] Buscando y forzando clic en botón 'LOGIN WITH SSO'...")
        sso_clicked = False
        selectors_sso = [
            'xpath=//*[contains(text(), "LOGIN WITH SSO")]',
            'xpath=//button[contains(., "SSO")]',
            'xpath=//a[contains(., "SSO")]',
            'xpath=//input[contains(@value, "SSO")]',
            'text="LOGIN WITH SSO"'
        ]

        for _ in range(8):
            for p_active in context.pages:
                for f in [p_active] + p_active.frames:
                    for sel in selectors_sso:
                        try:
                            loc_sso = f.locator(sel).first
                            if await loc_sso.count() > 0:
                                log(f"[INFO] ✅ Botón SSO localizado con selector '{sel}'. Forzando clic...")
                                await loc_sso.click(force=True)
                                sso_clicked = True
                                break
                        except Exception:
                            pass
                    if sso_clicked:
                        break
                if sso_clicked:
                    break
            if sso_clicked:
                break
            await asyncio.sleep(1)

        log("[INFO] Esperando 6s la redirección a Microsoft SSO...")
        await asyncio.sleep(6)

        log("[INFO] Buscando formulario de autenticación Microsoft...")
        try:
            # 1. EMAIL
            selector_email = 'input[type="email"], input[name="loginfmt"], input[id="i0116"]'
            email_input, frame_email = await obtener_locator_login(context, selector_email, timeout=30000)
            log("[INFO] Campo de correo localizado. Rellenando...")
            await email_input.fill(USUARIO)
            await asyncio.sleep(1)

            btn_next = frame_email.locator('input[type="submit"], input[id="idSIButton9"], button[id="idSIButton9"]').first
            await btn_next.click()
            
            log("[INFO] Procesando redirección post-correo...")
            await asyncio.sleep(5)

            # BYPASS FIDO/PASSKEY SI ES NECESARIO
            for _ in range(5):
                for p_active in context.pages:
                    for f in [p_active] + p_active.frames:
                        try:
                            btn_switch = f.locator('#idA_PWD_SwitchToPassword, a:has-text("Use your password instead"), a:has-text("Usar tu contraseña"), a:has-text("Password"), #cancelLink').first
                            if await btn_switch.is_visible():
                                log("[INFO] Haciendo clic en 'Usar contraseña en su lugar' / 'Cancelar FIDO'...")
                                await btn_switch.click()
                                await asyncio.sleep(3)
                                break
                        except Exception:
                            pass
                await asyncio.sleep(1)

            # 2. CONTRASEÑA
            log("[INFO] Ingresando contraseña...")
            selector_pass = 'input[name="passwd"], input[type="password"]'
            pass_input, frame_pass = await obtener_locator_login(context, selector_pass, timeout=45000)
            await pass_input.fill(PASSWORD, force=True)
            await asyncio.sleep(1)

            btn_pass_submit = frame_pass.locator('input[type="submit"], input[id="idSIButton9"], button[id="idSIButton9"]').first
            await btn_pass_submit.click()
            await asyncio.sleep(4)

            # 3. TOKEN 2FA
            log("[INFO] 2. Ingresando token 2FA...")
            selector_otp = 'input[name="otpcodes"], input[name="otc"]'
            otp_input, frame_otp = await obtener_locator_login(context, selector_otp, timeout=30000)
            await otp_input.fill(token_2fa, force=True)
            await asyncio.sleep(1)

            btn_otp_submit = frame_otp.locator('input[type="submit"], input[id="idSIButton9"], button[id="idSIButton9"]').first
            await btn_otp_submit.click()

            # Confirmación "Keep me signed in"
            try:
                await asyncio.sleep(3)
                btn_stay = frame_otp.locator('#idSIButton9, input[value="Yes"]').first
                if await btn_stay.is_visible(timeout=5000):
                    await btn_stay.click()
            except Exception:
                pass

        except Exception as e:
            log(f"❌ Error en autenticación: {e}")
            active_p = context.pages[-1] if context.pages else page
            await active_p.screenshot(path="error_login.png")
            raise e

        log("[INFO] 3. Esperando la carga de Flight Schedule Report (SCH012)...")
        report_page = context.pages[-1] if context.pages else page
        try:
            await report_page.goto(URL_REPORT, wait_until="commit", timeout=60000)
        except Exception:
            pass

        await report_page.wait_for_selector('text="Flight Schedule Report"', timeout=60000)

        # Bucle de descarga por Posta
        for i, posta in enumerate(POSTAS, 1):
            log(f"[{i}/{len(POSTAS)}] 🔄 Procesando posta: {posta}...")

            inputs_fecha = report_page.locator('input[title*="Flight Period"], input[name*="date"], input[type="text"]')
            await inputs_fecha.nth(0).fill(fecha_hoy)
            await inputs_fecha.nth(1).fill(fecha_hoy)

            input_brd_pt = report_page.locator('xpath=//td[contains(., "Brd Pt")]/following-sibling::td//input | input[name*="brdPt"]').first
            await input_brd_pt.clear()
            await input_brd_pt.fill(posta)

            async with report_page.expect_download() as download_info:
                flecha_desplegable = report_page.locator('xpath=//button[contains(@class, "ui-splitbutton")]//span | div:has-text("Generate") button | .ui-menu-button').first
                await flecha_desplegable.click()

                opcion_excel = report_page.locator('text="Generate As EXCEL"')
                await opcion_excel.click()

            download = await download_info.value

            nombre_archivo = f"Barrido_HDC_{posta}_{fecha_hoy}.xlsx"
            ruta_guardado = os.path.join(carpeta_destino, nombre_archivo)
            await download.save_as(ruta_guardado)
            log(f"   ✅ Descargado localmente: {nombre_archivo}")

            subir_a_google_drive(ruta_guardado, nombre_archivo)
            await asyncio.sleep(1.5)

        log(f"\n🎉 [COMPLETADO] Proceso finalizado exitosamente para las 28 postas.")
        await browser.close()

if __name__ == "__main__":
    t = threading.Thread(target=run_health_server, daemon=True)
    t.start()

    asyncio.run(run())
