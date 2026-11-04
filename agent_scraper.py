import base64
import json
import os
import sys

import requests
from google import genai
from playwright.sync_api import sync_playwright

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
APPS_SCRIPT_WEBHOOK = os.environ.get("APPS_SCRIPT_WEBHOOK")
SESSION_BASE64 = os.environ.get("SESSION_STATE_BASE64")

def restore_session():
    if SESSION_BASE64:
        try:
            with open("state.json", "wb") as f:
                f.write(base64.b64decode(SESSION_BASE64))
            return "state.json"
        except Exception as e:
            print(f"Error restoring session: {e}")
    return None

def fetch_dynamic_config():
    try:
        url = f"{APPS_SCRIPT_WEBHOOK}?action=getConfig"
        print(f"Fetching scraping targets from: {url}")
        response = requests.get(url, timeout=20)
        if response.status_code == 200:
            config = response.json()
            print(f"Active targets loaded ({len(config)}): {config}")
            return config
        return []
    except Exception as e:
        print(f"Error fetching target config: {e}")
        return []

def send_jobs(jobs_list):
    payload = {"action": "addJobs", "jobs": jobs_list}
    r = requests.post(APPS_SCRIPT_WEBHOOK, json=payload, timeout=20)
    print(f"Pushed {len(jobs_list)} jobs to dashboard: Status {r.status_code}")

def send_insights(target_url, summary):
    payload = {"action": "addInsights", "url": target_url, "summary": summary}
    r = requests.post(APPS_SCRIPT_WEBHOOK, json=payload, timeout=20)
    print(f"Pushed insight for {target_url}: Status {r.status_code}")

def run_scraper():
    if not APPS_SCRIPT_WEBHOOK:
        print("ERROR: APPS_SCRIPT_WEBHOOK is missing.")
        sys.exit(1)

    active_targets = fetch_dynamic_config()
    if not active_targets:
        print("No active scraping targets returned from Apps Script backend.")
        return

    session_file = restore_session()
    client = genai.Client(api_key=GEMINI_API_KEY)

    with sync_playwright() as p:
        # Launch Chromium with anti-detection flags
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox"
            ]
        )
        
        # Set real desktop browser viewport and User-Agent
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
            storage_state=session_file if session_file else None
        )
        
        page = context.new_page()
        
        # Hide navigator.webdriver flag from anti-bot detectors
        page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        collected_jobs = []

        for target in active_targets:
            target_url = target.get("url")
            category = target.get("category", "TECH").upper()

            print(f"\n Scraped Target [{category}]: {target_url}")

            try:
                # Navigate and wait for DOM load
                page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
                
                # Check for forced auth wall redirect
                current_url = page.url.lower()
                if "authwall" in current_url:
                    print(f"⚠️ Hit anti-bot authwall on {target_url}. Skipping.")
                    continue

                # Scroll down to trigger client-side rendering for SPAs
                page.evaluate("window.scrollBy(0, 800)")
                page.wait_for_timeout(4000)  # Wait 4s for React/Next.js hydration

                page_text = page.inner_text("body")[:15000]
                print(f"Extracted {len(page_text)} chars of text content.")

                if category == "JOBS":
                    prompt = """
                    You are a tech recruiter matching software engineering / AI / IoT job openings.
                    Extract up to 3 relevant matching job openings from this raw webpage text.
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
                        print(f" Extracted {len(jobs)} jobs from {target_url}")
                    except Exception as parse_err:
                        print(f"JSON Parse Error on {target_url}: {parse_err}")

                else:
                    prompt = "Summarize the top 3 tech updates or key insights from this page into 2 short bullet points."
                    response = client.models.generate_content(
                        model='gemini-1.5-flash',
                        contents=[prompt + "\n\nDOM Text:\n" + page_text]
                    )
                    summary = response.text.strip()
                    send_insights(target_url, summary)

            except Exception as e:
                print(f" Error scraping {target_url}: {e}")
                continue

        browser.close()

        if collected_jobs:
            send_jobs(collected_jobs)

if __name__ == "__main__":
    run_scraper()