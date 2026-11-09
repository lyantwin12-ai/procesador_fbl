import os
import asyncio
from datetime import datetime
import pyotp
from playwright.async_api import async_playwright

# ---------------------------------------------------------
# 1. Variables de entorno (Configuradas en Render)
# ---------------------------------------------------------
TOTP_SECRET = os.environ.get("CROAMIS_2FA_SECRET")
USUARIO = os.environ.get("CROAMIS_USER")
PASSWORD = os.environ.get("CROAMIS_PASSWORD")

# Parámetro opcional: Posta a consultar (por defecto BOG)
POSTA_BRD_PT = os.environ.get("CROAMIS_BRD_PT", "BOG")

# URL directa a la pantalla de reportes
URL_REPORT = "https://croamis.latamcargo.com/croamis/reportFlightSchedule?a=SCH"

async def run():
    # Validación de credenciales requeridas
    if not TOTP_SECRET or not USUARIO or not PASSWORD:
        raise ValueError("Error: Faltan variables de entorno (CROAMIS_2FA_SECRET, CROAMIS_USER o CROAMIS_PASSWORD).")

    # ---------------------------------------------------------
    # 2. Generación del código 2FA dinámico
    # ---------------------------------------------------------
    totp = pyotp.TOTP(TOTP_SECRET)
    token_2fa = totp.now()
    print(f"[INFO] Código 2FA generado exitosamente.")

    # Formato de fecha esperado por Croamis (Ejemplo: 09-NOV-2026)
    fecha_hoy = datetime.now().strftime("%d-%b-%Y").upper()

    async with async_playwright() as p:
        # En Render headless=True; context con descargas habilitadas
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(accept_downloads=True)
        page = await context.new_page()

        print("[INFO] 1. Navegando al portal Croamis...")
        await page.goto(URL_REPORT)

        # ---------------------------------------------------------
        # 3. Autenticación Microsoft 365
        # ---------------------------------------------------------
        # Paso 3.1: Correo electrónico
        await page.wait_for_selector('input[type="email"]')
        await page.fill('input[type="email"]', USUARIO)
        await page.click('input[type="submit"]')

        # Paso 3.2: Contraseña
        await page.wait_for_selector('input[type="password"]')
        await page.fill('input[type="password"]', PASSWORD)
        await page.click('input[type="submit"]')

        # Paso 3.3: Código TOTP de 6 dígitos
        print("[INFO] 2. Ingresando token 2FA...")
        await page.wait_for_selector('input[name="otpcodes"]', timeout=10000)
        await page.fill('input[name="otpcodes"]', token_2fa)
        await page.click('input[type="submit"]')

        # Paso 3.4: Omitir la ventana "Stay signed in?" si aparece
        try:
            await page.wait_for_selector('#idSIButton9', timeout=5000)
            await page.click('#idSIButton9')
        except Exception:
            pass  # Si no solicita mantener sesión, se omite sin error

        # ---------------------------------------------------------
        # 4. Diligenciamiento de Formulario SCH012
        # ---------------------------------------------------------
        print("[INFO] 3. Esperando la carga de Flight Schedule Report (SCH012)...")
        await page.wait_for_selector('text="Flight Schedule Report"', timeout=30000)

        print(f"[INFO] 4. Configurando filtros: Fecha = {fecha_hoy}, Brd Pt = {POSTA_BRD_PT}...")

        # Rellenar fechas de inicio y fin (Flight Period)
        inputs_fecha = page.locator('input[title*="Flight Period"], input[name*="date"], input[type="text"]')
        await inputs_fecha.nth(0).fill(fecha_hoy)
        await inputs_fecha.nth(1).fill(fecha_hoy)

        # Rellenar la posta de origen (Brd Pt)
        input_brd_pt = page.locator('xpath=//td[contains(., "Brd Pt")]/following-sibling::td//input | input[name*="brdPt"]').first
        await input_brd_pt.fill(POSTA_BRD_PT)

        # ---------------------------------------------------------
        # 5. Despliegue del Menú y Descarga del Reporte Excel
        # ---------------------------------------------------------
        print("[INFO] 5. Desplegando menú y solicitando descarga en formato Excel...")

        async with page.expect_download() as download_info:
            # Hace clic en la flecha pequeña desplegable al lado del botón Generate
            flecha_desplegable = page.locator('xpath=//button[contains(@class, "ui-splitbutton")]//span | div:has-text("Generate") button | .ui-menu-button').first
            await flecha_desplegable.click()

            # Hace clic en la opción flotante "Generate As EXCEL"
            opcion_excel = page.locator('text="Generate As EXCEL"')
            await opcion_excel.click()

        download = await download_info.value

        # Definir ruta y nombre final del archivo generado
        nombre_archivo = f"Barrido_HDC_{POSTA_BRD_PT}_{fecha_hoy}.xlsx"
        ruta_guardado = os.path.join(os.getcwd(), nombre_archivo)
        await download.save_as(ruta_guardado)

        print(f"✅ [ÉXITO] Reporte guardado correctamente en: {ruta_guardado}")

        await browser.close()

if __name__ == "__main__":
    asyncio.run(run())
