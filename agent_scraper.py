"""
AI Scraper Agent - Playwright & Direct REST API Engine
Fetches target URLs from Apps Script Webhook, restores authenticated browser sessions,
scrapes dynamic DOM content, extracts structured job/tech insights via Gemini REST API,
and posts results back to the Personal Command Center dashboard.
"""

import base64
import json
import os
import sys
import time
import requests
from playwright.sync_api import sync_playwright

# Environment Variables fetched from GitHub Secrets
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
APPS_SCRIPT_WEBHOOK = os.environ.get("APPS_SCRIPT_WEBHOOK")
SESSION_BASE64 = os.environ.get("SESSION_STATE_BASE64")


def generate_content_safe(contents):
    """
    Direct REST API call to Google's Gemini endpoint using explicit 'models/' URL paths.
    """
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY environment variable is missing.")

    prompt_text = contents[0] if isinstance(contents, list) else contents
    
    # Google REST endpoint requires explicit 'models/' prefix in the URL path
    models_to_try = [
        "models/gemini-2.5-flash",
        "models/gemini-2.0-flash",
        "models/gemini-1.5-flash"
    ]

    attempt_errors = []

    for model_path in models_to_try:
        url = f"https://generativelanguage.googleapis.com/v1beta/{model_path}:generateContent?key={GEMINI_API_KEY}"
        headers = {"Content-Type": "application/json"}
        payload = {
            "contents": [{
                "parts": [{"text": prompt_text}]
            }]
        }

        try:
            print(f" Attempting REST API call for URL path: '{model_path}'...")
            res = requests.post(url, json=payload, headers=headers, timeout=30)
            
            if res.status_code == 200:
                data = res.json()
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                if text:
                    print(f" Successfully generated content via REST API using: '{model_path}'")
                    return text
            else:
                err_msg = f"{model_path} (HTTP {res.status_code}): {res.text}"
                print(f"⚠️ {err_msg}")
                attempt_errors.append(err_msg)
        except Exception as e:
            err_msg = f"{model_path} Exception: {e}"
            print(f"⚠️ {err_msg}")
            attempt_errors.append(err_msg)
            continue

    # Return full untruncated error log if all candidates fail
    raise RuntimeError(" | ".join(attempt_errors))


def sanitize_storage_state(state):
    """Sanitizes exported cookie sameSite attributes for Playwright schema compliance."""
    if not isinstance(state, dict):
        return state

    cookies = state.get("cookies", [])
    for cookie in cookies:
        raw_same_site = str(cookie.get("sameSite", "")).lower().strip()
        if raw_same_site == "strict":
            cookie["sameSite"] = "Strict"
        elif raw_same_site == "lax":
            cookie["sameSite"] = "Lax"
        elif raw_same_site in ["none", "no_restriction"]:
            cookie["sameSite"] = "None"
        else:
            cookie["sameSite"] = "Lax"

    return state


def restore_session():
    """Restores Playwright session state from Base64 string."""
    if not SESSION_BASE64:
        print("No SESSION_STATE_BASE64 secret provided. Running in guest mode.")
        return None

    try:
        decoded_bytes = base64.b64decode(SESSION_BASE64)
        raw_state = json.loads(decoded_bytes.decode('utf-8'))
        clean_state = sanitize_storage_state(raw_state)
        
        file_path = "sanitized_state.json"
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(clean_state, f, indent=2)
            
        print(" Successfully restored session state.")
        return file_path
    except Exception as e:
        print(f"⚠️ Error restoring session state: {e}.")
        return None


def fetch_dynamic_config():
    """Fetches active scraping targets configured in Google Apps Script."""
    try:
        url = f"{APPS_SCRIPT_WEBHOOK}?action=getConfig"
        print(f"Fetching active scraping targets from: {url}")
        response = requests.get(url, timeout=20)
        
        if response.status_code == 200:
            config = response.json()
            print(f" Loaded {len(config)} active target(s) from backend.")
            return config
        else:
            print(f"⚠️ Backend returned HTTP status: {response.status_code}")
            return []
    except Exception as e:
        print(f" Error fetching target config: {e}")
        return []


def send_jobs(jobs_list):
    """POSTs scraped job listings back to Apps Script webhook."""
    if not jobs_list:
        return
    try:
        payload = {"action": "addJobs", "jobs": jobs_list}
        r = requests.post(APPS_SCRIPT_WEBHOOK, json=payload, timeout=20)
        print(f" Pushed {len(jobs_list)} job(s) to dashboard DB (Status: {r.status_code}).")
    except Exception as e:
        print(f" Error pushing jobs to dashboard: {e}")


def send_insights(target_url, summary):
    """POSTs web intelligence/event summaries back to Apps Script webhook."""
    if not summary:
        return
    try:
        payload = {"action": "addInsights", "url": target_url, "summary": summary}
        r = requests.post(APPS_SCRIPT_WEBHOOK, json=payload, timeout=20)
        print(f" Pushed insight digest for {target_url} (Status: {r.status_code}).")
    except Exception as e:
        print(f" Error pushing insights for {target_url}: {e}")


def run_scraper():
    """Main execution workflow for headless browser scraping & AI processing."""
    if not APPS_SCRIPT_WEBHOOK:
        print("❌ CRITICAL ERROR: APPS_SCRIPT_WEBHOOK environment variable is missing.")
        sys.exit(1)

    active_targets = fetch_dynamic_config()
    if not active_targets:
        print("No active targets found. Exiting runner execution.")
        return

    session_file = restore_session()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox"
            ]
        )
        
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            viewport={"width": 1440, "height": 900},
            storage_state=session_file if session_file else None
        )
        
        page = context.new_page()
        page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        collected_jobs = []

        for target in active_targets:
            target_url = target.get("url")
            category = target.get("category", "TECH").upper()

            print(f"\n--- Scraping [{category}]: {target_url} ---")

            try:
                page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
                
                if "authwall" in page.url.lower():
                    print(f"⚠️ Hit anti-bot authwall at {target_url}.")
                    send_insights(target_url, "⚠️ Authwall encountered. Refresh session cookies.")
                    continue

                for _ in range(4):
                    page.mouse.wheel(0, 1000)
                    page.wait_for_timeout(1000)

                body_text = page.inner_text("body")
                clean_text = " ".join(body_text.split())[:20000]
                print(f"Extracted {len(clean_text)} characters of DOM content.")

                if not GEMINI_API_KEY:
                    print("⚠️ GEMINI_API_KEY missing. Skipping AI processing.")
                    send_insights(target_url, "⚠️ Target scraped, but GEMINI_API_KEY is missing for AI digest.")
                    continue

                if category == "JOBS":
                    job_prompt = """
                    You are an expert tech recruiter matching job openings.
                    Analyze this raw text extracted from a job search portal and extract up to 5 relevant engineering/tech job listings.
                    Return ONLY a valid JSON array of objects with no markdown formatting or commentary:
                    [
                      {
                        "role": "Job Title",
                        "company": "Company Name",
                        "location": "Location or Remote/Hybrid",
                        "techStack": "Required Skills / Tech Stack",
                        "applyUrl": "Direct Apply Link or Page URL"
                      }
                    ]
                    """
                    try:
                        raw_response = generate_content_safe(
                            [job_prompt + "\n\nDOM Text:\n" + clean_text]
                        )
                        cleaned_json = raw_response.replace("```json", "").replace("```", "").strip()
                        jobs = json.loads(cleaned_json)
                        if isinstance(jobs, list):
                            for j in jobs:
                                if not j.get("applyUrl"):
                                    j["applyUrl"] = target_url
                            collected_jobs.extend(jobs)
                            print(f" Extracted {len(jobs)} jobs from {target_url}")
                    except Exception as parse_err:
                        print(f"⚠️ JSON Parsing Error on {target_url}: {parse_err}")

                    insight_prompt = """
                    Summarize the top hiring trends, salary ranges, or active tech role types from this job listing text in 2 concise bullet points.
                    Return plain text only.
                    """
                    try:
                        job_digest = generate_content_safe(
                            [insight_prompt + "\n\nDOM Text:\n" + clean_text]
                        ).strip()
                        send_insights(target_url, job_digest)
                    except Exception as err:
                        send_insights(target_url, f"⚠️ Insight Gen Error: {str(err)}")

                else:
                    prompt = """
                    Summarize the top 3 tech updates, key events, or hiring trends from this text in 2 concise, executive bullet points.
                    Return plain text only.
                    """
                    try:
                        summary = generate_content_safe(
                            [prompt + "\n\nDOM Text:\n" + clean_text]
                        ).strip()
                        send_insights(target_url, summary)
                    except Exception as err:
                        send_insights(target_url, f"⚠️ Summary Gen Error: {str(err)}")

            except Exception as e:
                print(f" Error scraping target {target_url}: {e}")
                send_insights(target_url, f"⚠️ Scraper Execution Error: {str(e)}")
                continue

        browser.close()

        if session_file and os.path.exists(session_file):
            os.remove(session_file)

        if collected_jobs:
            send_jobs(collected_jobs)


if __name__ == "__main__":
    run_scraper()