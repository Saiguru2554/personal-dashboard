import base64
import json
import os
import requests
from google import genai
from playwright.sync_api import sync_playwright

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
APPS_SCRIPT_WEBHOOK = os.environ.get("APPS_SCRIPT_WEBHOOK")
SESSION_BASE64 = os.environ.get("SESSION_STATE_BASE64")

def restore_session():
    """Decodes the Base64 session string from GitHub Secrets to state.json"""
    if SESSION_BASE64:
        try:
            with open("state.json", "wb") as f:
                f.write(base64.b64decode(SESSION_BASE64))
            return "state.json"
        except Exception as e:
            print(f"Error restoring session state: {e}")
            return None
    return None

def fetch_dynamic_config():
    """Fetches active targets configured in the Apps Script Dashboard"""
    try:
        url = f"{APPS_SCRIPT_WEBHOOK}?action=getConfig"
        response = requests.get(url, timeout=15)
        if response.status_code == 200:
            return response.json()
        print(f"Failed to fetch config, status code: {response.status_code}")
        return []
    except Exception as e:
        print(f"Error fetching dynamic target config: {e}")
        return []

def send_alert(platform_name, message):
    payload = {"action": "authAlert", "platform": platform_name, "message": message}
    requests.post(APPS_SCRIPT_WEBHOOK, json=payload)

def send_jobs(jobs_list):
    payload = {"action": "addJobs", "jobs": jobs_list}
    requests.post(APPS_SCRIPT_WEBHOOK, json=payload)

def send_insights(target_url, summary):
    payload = {"action": "addInsights", "url": target_url, "summary": summary}
    requests.post(APPS_SCRIPT_WEBHOOK, json=payload)

def run_scraper():
    active_targets = fetch_dynamic_config()
    if not active_targets:
        print("No active scraping targets returned from dashboard. Exiting.")
        return

    session_file = restore_session()
    client = genai.Client(api_key=GEMINI_API_KEY)

    # HEADLESS IS MANDATORY FOR GITHUB ACTIONS RUNNERS
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=session_file if session_file else None)
        page = context.new_page()

        collected_jobs = []

        for target in active_targets:
            target_url = target.get("url")
            category = target.get("category", "TECH").upper()

            print(f"Scraping [{category}]: {target_url}")

            try:
                page.goto(target_url, wait_until="domcontentloaded", timeout=60000)

                current_url = page.url.lower()
                if "login" in current_url or "signin" in current_url:
                    print(f"Session expired or redirect to login detected on {target_url}")
                    send_alert(target_url, "Session expired or authentication required.")
                    continue

                page.evaluate("window.scrollBy(0, 1000)")
                page.wait_for_timeout(3000)
                page_text = page.inner_text("body")[:15000]

                if category == "JOBS":
                    prompt = """
                    You are a tech recruiter matching job openings.
                    Extract up to 3 relevant matching job openings from this text.
                    Return ONLY a raw JSON array of objects:
                    [
                      {
                        "role": "Job Title",
                        "company": "Company Name",
                        "location": "Location / Remote",
                        "techStack": "Matched Tech Stack",
                        "applyUrl": "Direct Apply URL or Page URL"
                      }
                    ]
                    """
                    response = client.models.generate_content(
                        model='gemini-1.5-flash',
                        contents=[prompt + "\n\nDOM Text:\n" + page_text]
                    )
                    cleaned = response.text.replace("```json", "").replace("```", "").strip()
                    try:
                        jobs = json.loads(cleaned)
                        collected_jobs.extend(jobs)
                    except Exception as parse_err:
                        print(f"JSON parsing error on {target_url}: {parse_err}")

                else:
                    prompt = "Summarize the top 3 tech updates or key insights from this page into 2 short bullet points."
                    response = client.models.generate_content(
                        model='gemini-1.5-flash',
                        contents=[prompt + "\n\nDOM Text:\n" + page_text]
                    )
                    summary = response.text.strip()
                    send_insights(target_url, summary)

            except Exception as e:
                print(f"Scrape error on {target_url}: {e}")
                continue

        browser.close()

        if collected_jobs:
            send_jobs(collected_jobs)
            print(f"Successfully pushed {len(collected_jobs)} jobs to dashboard.")

if __name__ == "__main__":
    run_scraper()