"""
wake_app.py
-----------
Headless Playwright skript pro probuzení Streamlit appky na Community Cloud.

Logika:
  1. Otevře URL appky v headless Chromiu (plný JS render + WebSocket → počítá jako
     reálná návštěva i pro Streamlit).
  2. Počká na vykreslení stránky.
  3. Pokud najde tlačítko „Yes, get this app back up!" → klikne a ověří probuzení.
  4. Pokud tlačítko není přítomno → appka je živá, ukončí se úspěšně.
"""

import os
import sys
import time

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

STREAMLIT_URL: str = os.environ.get("STREAMLIT_APP_URL", "").strip()
WAKE_BUTTON_TEXT = "Yes, get this app back up"
INITIAL_WAIT_S = 6          # čas na JS render po načtení stránky
BUTTON_TIMEOUT_MS = 10_000  # jak dlouho čekat na tlačítko / jeho zmizení
PAGE_TIMEOUT_MS = 25_000    # timeout pro goto()


def main() -> None:
    if not STREAMLIT_URL:
        print("❌ Proměnná prostředí STREAMLIT_APP_URL není nastavena.")
        sys.exit(1)

    print(f"🌐 Navštěvuji: {STREAMLIT_URL}")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()

        try:
            page.goto(STREAMLIT_URL, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
        except PlaywrightTimeout:
            print("⚠️  Stránka se nenačetla v limitu – appka se může teprve probouzet.")

        # Dáme Streamlitu čas na inicializaci JS / WebSocket
        print(f"⏳ Čekám {INITIAL_WAIT_S} s na render...")
        time.sleep(INITIAL_WAIT_S)

        try:
            button = page.get_by_role("button", name=WAKE_BUTTON_TEXT)
            button.wait_for(state="visible", timeout=BUTTON_TIMEOUT_MS)

            print("🔔 Appka spí – klikám na tlačítko probuzení...")
            button.click()

            try:
                button.wait_for(state="hidden", timeout=BUTTON_TIMEOUT_MS)
                print("✅ Appka se probouzí (tlačítko zmizelo).")
            except PlaywrightTimeout:
                print("❌ Tlačítko po kliknutí nezmizelo – možná chyba. Zkontroluj logy.")
                sys.exit(1)

        except PlaywrightTimeout:
            # Tlačítko vůbec neexistuje → appka je živá
            print("✅ Appka je živá – žádné probouzení není třeba.")

        finally:
            context.close()
            browser.close()

    print("🏁 Hotovo.")


if __name__ == "__main__":
    main()
