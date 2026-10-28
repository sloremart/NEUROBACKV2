"""
Abre Chrome real, espera que inicies sesión en Colsanitas manualmente,
luego exporta las cookies en formato JSON portable (funciona en Linux).
"""
import asyncio
import os
from playwright.sync_api import sync_playwright

LOGIN_URL = "https://portal.colsanitas.com/sso/login?service=https%3A%2F%2Fappcore.colsanitas.com%2FValidadorDerechos%2Fpages%2Fgestion%2FValidacionDerechos.seam%3FflagConsulta%3D1"
OUT_FILE = os.path.join(os.path.dirname(__file__), "data", "colsanitas_state.json")

def main():
    os.makedirs(os.path.join(os.path.dirname(__file__), "data"), exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled", "--start-maximized", "--no-sandbox"]
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
            no_viewport=True
        )
        page = context.new_page()
        page.goto(LOGIN_URL)

        print("\n" + "="*60)
        print("  INSTRUCCIONES:")
        print("  1. Inicia sesion con: 1121926977.prest / czzTbfEQ")
        print("  2. Si aparece CAPTCHA de Radware, resuelvelo")
        print("  3. Las cookies se guardan AUTOMATICAMENTE cuando")
        print("     llegues al Validador de Derechos")
        print("="*60)

        # Esperar hasta 3 minutos a que el usuario llegue al validador
        import time
        print("\n  Esperando que inicies sesion...", flush=True)
        for i in range(180):
            try:
                url = page.url
                if "ValidacionDerechos" in url or "appcore.colsanitas.com" in url:
                    print(f"\n  Validador detectado. Exportando cookies...")
                    break
            except:
                pass
            time.sleep(1)
            if i % 10 == 9:
                print(f"  ({i+1}s) Aun esperando...", flush=True)

        context.storage_state(path=OUT_FILE)
        print(f"\n  Cookies exportadas: {OUT_FILE}")
        browser.close()

if __name__ == "__main__":
    main()
