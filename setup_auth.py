import base64
import json

def convert_cookies_to_base64():
    try:
        with open("cookies.json", "r") as f:
            raw_cookies = json.load(f)

        formatted_cookies = []
        for c in raw_cookies:
            # Format raw browser cookies into Playwright storage_state format
            same_site = c.get("sameSite", "Lax")
            if same_site == "no_restriction":
                same_site = "None"
            elif same_site == "strict":
                same_site = "Strict"
            elif same_site == "lax":
                same_site = "Lax"

            cookie = {
                "name": c.get("name"),
                "value": c.get("value"),
                "domain": c.get("domain"),
                "path": c.get("path", "/"),
                "expires": c.get("expirationDate", -1),
                "httpOnly": c.get("httpOnly", False),
                "secure": c.get("secure", False),
                "sameSite": same_site
            }
            formatted_cookies.append(cookie)

        playwright_state = {
            "cookies": formatted_cookies,
            "origins": []
        }

        # Save formatted state locally
        with open("state.json", "w") as f:
            json.dump(playwright_state, f, indent=2)

        # Generate Base64 string
        with open("state.json", "rb") as f:
            encoded_string = base64.b64encode(f.read()).decode("utf-8")

        print("\n" + "="*60)
        print("SUCCESS! COPY THIS STRING TO YOUR GITHUB 'SESSION_STATE_BASE64' SECRET:")
        print("="*60 + "\n")
        print(encoded_string)

    except FileNotFoundError:
        print("\n[ERROR] Could not find 'cookies.json'. Make sure you saved the exported JSON file in this folder!")
    except Exception as e:
        print(f"\n[ERROR] Failed to process cookies: {e}")

if __name__ == "__main__":
    convert_cookies_to_base64()