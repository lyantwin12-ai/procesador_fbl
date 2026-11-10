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
# 0. Servidor dummy para mantener vivo el Web Service en Render
# ---------------------------------------------------------
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
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

URL_LOGIN = "https://croamis.latamcargo.com/croamis/"
URL_REPORT = "https://croamis.latamcargo.com/croamis/reportFlightSchedule?a=SCH"

def subir_a_google_drive(ruta_local, nombre_archivo):
    """Suba un archivo local a la carpeta especificada en Google Drive."""
    if not GOOGLE_JSON_STR:
        print("⚠️ [ADVERTENCIA] No se configuró GOOGLE_CREDENTIALS_JSON. Omitiendo subida.")
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

        print(f"   ☁️ Subido con éxito a Google Drive (ID: {archivo_subido.get('id')})")
    except Exception as e:
        print(f"   ❌ Error subiendo a Google Drive: {e}")

async def run():
    if not TOTP_SECRET or not USUARIO or not PASSWORD:
        raise ValueError("Error: Faltan variables de entorno requeridas en Render.")

    carpeta_destino = os.path.join(os.getcwd(), "barrido hdc")
    os.makedirs(carpeta_destino, exist_ok=True)

    totp = pyotp.TOTP(TOTP_SECRET)
    token_2fa = totp.now()
    fecha_hoy = datetime.now().strftime("%d-%b-%Y").upper()
    print(f"[INFO] Código 2FA generado exitosamente.")

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

        print("[INFO] 1. Navegando al portal Croamis...")
        await page.goto(URL_LOGIN, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)

        # Manejar redirección a nueva pestaña o iframe tras clic en Login
        target_page = page
        try:
            btn_login = page.locator('button:has-text("Login"), a:has-text("Login"), input[type="submit"]').first
            if await btn_login.is_visible(timeout=5000):
                print("[INFO] Haciendo clic en inicio de sesión del portal...")
                async with context.expect_page(timeout=5000) as new_page_info:
                    await btn_login.click()
                target_page = await new_page_info.value
                await target_page.wait_for_load_state("domcontentloaded")
                print("[INFO] Redirigido a la nueva ventana de autenticación.")
        except Exception:
            # Si no abre ventana emergente, continuamos con la misma página
            pass

        print("[INFO] Buscando formulario de autenticación Microsoft...")
        try:
            # 1. EMAIL
            email_input = target_page.locator('input[type="email"], input[name="loginfmt"], input[id="i0116"]').first
            await email_input.wait_for(state="visible", timeout=30000)
            await email_input.fill(USUARIO)
            await target_page.wait_for_timeout(1000)
            
            btn_next = target_page.locator('input[type="submit"], input[id="idSIButton9"], button[id="idSIButton9"]').first
            await btn_next.click()

            await target_page.wait_for_timeout(3000)

            # Si Microsoft ofrece opción de contraseña directa
            try:
                btn_use_pass = target_page.locator('a[id="idA_PWD_SwitchToPassword"]').first
                if await btn_use_pass.is_visible(timeout=3000):
                    await btn_use_pass.click()
                    await target_page.wait_for_timeout(2000)
            except Exception:
                pass

            # 2. CONTRASEÑA
            print("[INFO] Ingresando contraseña...")
            pass_input = target_page.locator('input[name="passwd"], input[type="password"]').first
            await pass_input.wait_for(state="visible", timeout=30000)
            await pass_input.fill(PASSWORD)
            await target_page.wait_for_timeout(1000)
            
            btn_pass_submit = target_page.locator('input[type="submit"], input[id="idSIButton9"], button[id="idSIButton9"]').first
            await btn_pass_submit.click()

            await target_page.wait_for_timeout(3000)

            # 3. TOKEN 2FA
            print("[INFO] 2. Ingresando token 2FA...")
            otp_input = target_page.locator('input[name="otpcodes"], input[name="otc"]').first
            await otp_input.wait_for(state="visible", timeout=30000)
            await otp_input.fill(token_2fa)
            await target_page.wait_for_timeout(1000)
            
            btn_otp_submit = target_page.locator('input[type="submit"], input[id="idSIButton9"], button[id="idSIButton9"]').first
            await btn_otp_submit.click()

            # Confirmación de "Mantener la sesión iniciada"
            try:
                await target_page.wait_for_timeout(3000)
                btn_stay = target_page.locator('#idSIButton9, input[value="Yes"]').first
                if await btn_stay.is_visible(timeout=5000):
                    await btn_stay.click()
            except Exception:
                pass

        except Exception as e:
            print(f"❌ Error en autenticación: {e}")
            await target_page.screenshot(path="error_login.png")
            raise e

        print("[INFO] Redirigiendo al reporte SCH012...")
        await target_page.goto(URL_REPORT, wait_until="load", timeout=60000)
        await target_page.wait_for_selector('text="Flight Schedule Report"', timeout=60000)

        # Bucle de descarga por Posta
        for i, posta in enumerate(POSTAS, 1):
            print(f"[{i}/{len(POSTAS)}] 🔄 Procesando posta: {posta}...")

            inputs_fecha = target_page.locator('input[title*="Flight Period"], input[name*="date"], input[type="text"]')
            await inputs_fecha.nth(0).fill(fecha_hoy)
            await inputs_fecha.nth(1).fill(fecha_hoy)

            input_brd_pt = target_page.locator('xpath=//td[contains(., "Brd Pt")]/following-sibling::td//input | input[name*="brdPt"]').first
            await input_brd_pt.clear()
            await input_brd_pt.fill(posta)

            async with target_page.expect_download() as download_info:
                flecha_desplegable = target_page.locator('xpath=//button[contains(@class, "ui-splitbutton")]//span | div:has-text("Generate") button | .ui-menu-button').first
                await flecha_desplegable.click()

                opcion_excel = target_page.locator('text="Generate As EXCEL"')
                await opcion_excel.click()

            download = await download_info.value

            nombre_archivo = f"Barrido_HDC_{posta}_{fecha_hoy}.xlsx"
            ruta_guardado = os.path.join(carpeta_destino, nombre_archivo)
            await download.save_as(ruta_guardado)
            print(f"   ✅ Descargado localmente: {nombre_archivo}")

            subir_a_google_drive(ruta_guardado, nombre_archivo)
            await target_page.wait_for_timeout(1500)

        print(f"\n🎉 [COMPLETADO] Proceso finalizado exitosamente para las 28 postas.")
        await browser.close()

if __name__ == "__main__":
    t = threading.Thread(target=run_health_server, daemon=True)
    t.start()

    asyncio.run(run())
