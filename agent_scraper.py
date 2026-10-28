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
    if SESSION_BASE64:
        with open("state.json", "wb") as f:
            f.write(base64.b64decode(SESSION_BASE64))
        return "state.json"
    return None

def fetch_dynamic_config():
    try:
        url = f"{APPS_SCRIPT_WEBHOOK}?action=getConfig"
        response = requests.get(url, timeout=15)
        if response.status_code == 200:
            return response.json()
        return []
    except Exception as e:
        print(f"Error connecting to Apps Script config: {e}")
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
        print("No active scraping targets found in Google Sheet dashboard. Exiting.")
        return

    session_file = restore_session()
    client = genai.Client(api_key=GEMINI_API_KEY)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=session_file if session_file else None)
        page = context.new_page()

        collected_jobs = []

        for target in active_targets:
            target_url = target.get("url")
            category = target.get("category", "GENERAL").upper()

            print(f"Processing [{category}]: {target_url}")

            try:
                page.goto(target_url, wait_until="networkidle", timeout=60000)

                current_url = page.url.lower()
                if "login" in current_url or "signin" in current_url:
                    send_alert(target_url, "Session expired or authentication required.")
                    continue

                page_text = page.inner_text("body")[:15000]

                if category == "JOBS":
                    prompt = """
                    You are a tech recruiter matching job openings for a candidate specializing in:
                    AI/NLP, Computer Vision, Embedded Systems/IoT (ESP32/Raspberry Pi), or Full-Stack (Python/FastAPI/React).

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
                    jobs = json.loads(cleaned)
                    collected_jobs.extend(jobs)

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

        browser.close()

        if collected_jobs:
            send_jobs(collected_jobs)
            print(f"Pushed {len(collected_jobs)} jobs to Google Sheets dashboard.")

if __name__ == "__main__":
    run_scraper()