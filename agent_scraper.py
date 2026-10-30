import base64
from playwright.sync_api import sync_playwright

def generate_session_string():
    with sync_playwright() as p:
        # Tries standard Google Chrome first; falls back to Playwright Chromium if not installed
        try:
            browser = p.chromium.launch(
                headless=False,
                channel="chrome",
                args=["--disable-blink-features=AutomationControlled"]
            )
        except Exception:
            print("Standard Chrome not found. Launching bundled Chromium browser instead...")
            browser = p.chromium.launch(
                headless=False,
                args=["--disable-blink-features=AutomationControlled"]
            )

        context = browser.new_context()
        page = context.new_page()

        print("\n=== Manual Authentication Setup ===")
        print("1. Sign into accounts.google.com first.")
        print("2. Type target site URLs in the address bar & click 'Continue with Google'.")
        print("3. Return here and press ENTER once done.")
        
        page.goto("https://accounts.google.com") 
        
        input("\n>>> Press ENTER here in the terminal AFTER completing all logins...")

        context.storage_state(path="state.json")
        browser.close()

    with open("state.json", "rb") as f:
        encoded_string = base64.b64encode(f.read()).decode("utf-8")

    print("\n=== COPY THIS BASE64 STRING TO GITHUB SECRETS ===")
    print(encoded_string)

if __name__ == "__main__":
    generate_session_string()