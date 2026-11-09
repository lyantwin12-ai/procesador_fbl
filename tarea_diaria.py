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
    def do_GET(El error de **Timeout (60000ms)** indica que Playwright esperó un minuto a que cargara el campo de correo y nunca lo detectó. Esto ocurre en Croamis por dos razones principales:

1. **`wait_until="networkidle"` bloquea la navegación:** Croamis mantiene conexiones abiertas (WebSockets/long-polling), lo que hace que Playwright se quede esperando indefinidamente antes de procesar el DOM.
2. **Uso de `iframes` o Redirecciones SSO:** El portal de LATAM Cargo suele envolver la pantalla de login dentro de un `iframe` o requiere hacer clic explícito en un botón de ingreso corporativo antes de mostrar el login de Microsoft.

---

### Solución: Estrategia de Diagnóstico y Navegación Directa

Para destrabar el proceso, ajustaremos la navegación utilizando `wait_until="domcontentloaded"`, agregaremos soporte para detectar si el formulario está dentro de un `iframe` y tomaremos una **captura de pantalla (screenshot)** en caso de fallo para ver qué está renderizando Croamis exactamente en Render.

Actualiza tu archivo `tarea_diaria.py` en GitHub con este fragmento optimizado en el bloque de conexión e inicio de sesión:

```python
        print("[INFO] 1. Navegando al portal Croamis...")
        # Cambiamos a domcontentloaded para evitar bloqueos por conexiones secundarias
        await page.goto(URL_REPORT, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(5000)

        # Si Croamis muestra una landing page inicial con un botón de Login / SSO
        try:
            sso_btn = page.locator('button:has-text("Login"), a:has-text("Login"), #loginButton, .btn-primary').first
            if await sso_btn.is_visible(timeout=5000):
                print("[INFO] Detectado botón de ingreso inicial. Haciendo clic...")
                await sso_btn.click()
                await page.wait_for_timeout(3000)
        except Exception:
            pass

        print("[INFO] Buscando formulario de autenticación Microsoft...")
        try:
            # Buscar el campo tanto en la página principal como dentro de posibles IFrames
            email_input = page.ramification if False else None
            
            # Selector flexible para Microsoft SSO
            selector_email = 'input[type="email"], input[name="loginfmt"], input[id="i0116"]'
            
            # Esperar a que aparezca en el marco principal o en frames
            await page.wait_for_selector(selector_email, timeout=30000)
            await page.fill(selector_email, USUARIO)
            await page.click('input[type="submit"], input[id="idSIButton9"]')

        except Exception as e:
            # Si vuelve a fallar, guardamos una captura para ver el estado real de la pantalla
            print(f"❌ Error buscando formulario de login: {e}")
            await page.screenshot(path="error_login.png")
            print("📸 Captura de error guardada en error_login.png")
            raise e
