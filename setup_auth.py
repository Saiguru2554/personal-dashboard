import base64

from playwright.sync_api import sync_playwright


def generate_session_string():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        print("=== Manual Authentication Setup ===")
        print("Opening browser... Log into your target portal if required.")
        page.goto("https://www.linkedin.com/login") 
        
        input("\n>>> Complete your login in the browser window.\n>>> Press ENTER here once logged in...")

        context.storage_state(path="state.json")
        browser.close()

    with open("state.json", "rb") as f:
        encoded_string = base64.b64encode(f.read()).decode("utf-8")

    print("\n=== BASE64 SESSION STRING (Copy everything below) ===")
    print(encoded_string)

if __name__ == "__main__":
    generate_session_string()