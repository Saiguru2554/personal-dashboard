"""
AI Scraper Agent - High-Performance Single-Pass Engine
Optimized for ultra-fast execution, rate-limit resilience, and threat mitigation.
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

# Global Memory Caches for Optimization
DISCOVERED_MODELS_CACHE = None
EXHAUSTED_429_MODELS = set()


def get_available_models():
    """
    Queries ListModels ONCE per script run and caches the result.
    Filters out models blacklisted by 429 quota errors.
    """
    global DISCOVERED_MODELS_CACHE

    if DISCOVERED_MODELS_CACHE is not None:
        return [m for m in DISCOVERED_MODELS_CACHE if m not in EXHAUSTED_429_MODELS]

    if not GEMINI_API_KEY:
        return []

    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_API_KEY}"
    try:
        res = requests.get(url, timeout=10)
        if res.status_code == 200:
            data = res.json()
            available = []
            for m in data.get("models", []):
                methods = m.get("supportedGenerationMethods", [])
                if "generateContent" in methods:
                    model_name = m.get("name", "").replace("models/", "")
                    available.append(model_name)

            priority_order = [
                "gemini-3.5-flash",
                "gemini-3.8-flash",
                "gemini-3.1-flash-lite",
                "gemini-flash-latest",
                "gemini-2.5-flash"
            ]

            sorted_models = [m for m in priority_order if m in available]
            for m in available:
                if m not in sorted_models and "preview" not in m and "tts" not in m:
                    sorted_models.append(m)

            print(f" Cached {len(sorted_models)} models for run: {sorted_models[:4]}")
            DISCOVERED_MODELS_CACHE = sorted_models
            return [m for m in DISCOVERED_MODELS_CACHE if m not in EXHAUSTED_429_MODELS]
    except Exception as e:
        print(f"⚠️ Dynamic discovery failed: {e}")

    DISCOVERED_MODELS_CACHE = ["gemini-3.5-flash", "gemini-3.8-flash", "gemini-3.1-flash-lite"]
    return DISCOVERED_MODELS_CACHE


def generate_content_fast(prompt_text):
    """
    Fast LLM call using a 10s timeout, cached model list, and dynamic 429 blacklisting.
    """
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY environment variable is missing.")

    models_to_try = get_available_models()
    attempt_errors = []

    for model_name in models_to_try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={GEMINI_API_KEY}"
        headers = {"Content-Type": "application/json"}
        payload = {"contents": [{"parts": [{"text": prompt_text}]}]}

        for retry_attempt in range(2):
            try:
                # 10-second timeout for fast failure
                res = requests.post(url, json=payload, headers=headers, timeout=10)

                if res.status_code == 200:
                    data = res.json()
                    text = data["candidates"][0]["content"]["parts"][0]["text"]
                    if text:
                        return text

                elif res.status_code == 429:
                    print(f"⚠️ Model '{model_name}' hit 429 Quota Exceeded. Blacklisting for this run.")
                    EXHAUSTED_429_MODELS.add(model_name)
                    break

                elif res.status_code == 503:
                    wait_time = (retry_attempt + 1) * 2
                    print(f"⚠️ {model_name} 503 spike. Retrying in {wait_time}s...")
                    time.sleep(wait_time)
                    continue

                else:
                    err_msg = f"{model_name} HTTP {res.status_code}"
                    attempt_errors.append(err_msg)
                    break

            except requests.exceptions.Timeout:
                print(f"⚡ {model_name} timed out (>10s). Skipping to next candidate.")
                attempt_errors.append(f"{model_name} Timeout")
                break
            except Exception as e:
                attempt_errors.append(f"{model_name} Exception: {e}")
                break

    raise RuntimeError("All models failed: " + " | ".join(attempt_errors[:2]))


def sanitize_storage_state(state):
    """Sanitizes exported cookie sameSite attributes for Playwright schema compliance."""
    if not isinstance(state, dict):
        return state
    for cookie in state.get("cookies", []):
        raw_same_site = str(cookie.get("sameSite", "")).lower().strip()
        if raw_same_site == "strict":
            cookie["sameSite"] = "Strict"
        elif raw_same_site in ["none", "no_restriction"]:
            cookie["sameSite"] = "None"
        else:
            cookie["sameSite"] = "Lax"
    return state


def restore_session():
    """Restores Playwright session state from Base64 string."""
    if not SESSION_BASE64:
        return None
    try:
        decoded_bytes = base64.b64decode(SESSION_BASE64)
        raw_state = json.loads(decoded_bytes.decode('utf-8'))
        clean_state = sanitize_storage_state(raw_state)
        file_path = "sanitized_state.json"
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(clean_state, f, indent=2)
        return file_path
    except Exception as e:
        print(f"⚠️ Error restoring session: {e}")
        return None


def fetch_dynamic_config():
    """Fetches active scraping targets configured in Google Apps Script."""
    try:
        url = f"{APPS_SCRIPT_WEBHOOK}?action=getConfig"
        response = requests.get(url, timeout=15)
        return response.json() if response.status_code == 200 else []
    except Exception as e:
        print(f" Error fetching target config: {e}")
        return []


def send_jobs(jobs_list):
    """POSTs scraped job listings back to Apps Script webhook."""
    if not jobs_list:
        return
    try:
        payload = {"action": "addJobs", "jobs": jobs_list}
        requests.post(APPS_SCRIPT_WEBHOOK, json=payload, timeout=15)
    except Exception as e:
        print(f" Error pushing jobs: {e}")


def send_insights(target_url, summary):
    """POSTs web intelligence/event summaries back to Apps Script webhook."""
    if not summary:
        return
    try:
        payload = {"action": "addInsights", "url": target_url, "summary": summary}
        requests.post(APPS_SCRIPT_WEBHOOK, json=payload, timeout=15)
    except Exception as e:
        print(f" Error pushing insights: {e}")


def run_scraper():
    """Main execution workflow optimized for speed and resilience."""
    if not APPS_SCRIPT_WEBHOOK:
        sys.exit(1)

    active_targets = fetch_dynamic_config()
    if not active_targets:
        return

    session_file = restore_session()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
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
                page.goto(target_url, wait_until="domcontentloaded", timeout=30000)

                if "authwall" in page.url.lower():
                    send_insights(target_url, "⚠️ Authwall encountered. Refresh session cookies.")
                    continue

                # Scroll to load dynamic content
                for _ in range(3):
                    page.mouse.wheel(0, 800)
                    page.wait_for_timeout(500)

                body_text = page.inner_text("body")
                clean_text = " ".join(body_text.split())[:10000]
                print(f"Extracted {len(clean_text)} characters.")

                # Anti-bot detection check
                if len(clean_text) < 400:
                    print(f"⚠️ Anti-Bot or empty response on {target_url} ({len(clean_text)} chars).")
                    send_insights(target_url, "⚠️ Blocked by Anti-Bot protection or dynamic content failed to render.")
                    continue

                if category == "JOBS":
                    # SINGLE-PASS PROMPT: Extract jobs AND digest in ONE call
                    unified_prompt = f"""
                    Analyze this job portal text. Return ONLY a JSON object:
                    {{
                      "jobs": [
                        {{
                          "role": "Title",
                          "company": "Company",
                          "location": "Location",
                          "techStack": "Skills",
                          "applyUrl": "URL"
                        }}
                      ],
                      "digest": "Two bullet points summarizing top hiring trends or salaries."
                    }}

                    DOM Text:
                    {clean_text}
                    """
                    try:
                        raw_response = generate_content_fast(unified_prompt)
                        cleaned_json = raw_response.replace("```json", "").replace("```", "").strip()
                        parsed_data = json.loads(cleaned_json)

                        jobs = parsed_data.get("jobs", [])
                        digest = parsed_data.get("digest", "")

                        if isinstance(jobs, list):
                            for j in jobs:
                                if not j.get("applyUrl"):
                                    j["applyUrl"] = target_url
                            collected_jobs.extend(jobs)

                        if digest:
                            send_insights(target_url, digest)
                    except Exception as err:
                        send_insights(target_url, f"⚠️ Extraction Error: {str(err)[:120]}")

                else:
                    tech_prompt = f"Summarize top 3 tech updates in 2 executive bullet points.\n\nDOM Text:\n{clean_text}"
                    try:
                        summary = generate_content_fast(tech_prompt).strip()
                        send_insights(target_url, summary)
                    except Exception as err:
                        send_insights(target_url, f"⚠️ Summary Error: {str(err)[:120]}")

            except Exception as e:
                send_insights(target_url, f"⚠️ Scraper Error: {str(e)[:100]}")
                continue

        browser.close()

        if session_file and os.path.exists(session_file):
            os.remove(session_file)

        if collected_jobs:
            send_jobs(collected_jobs)


if __name__ == "__main__":
    run_scraper()