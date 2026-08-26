import re
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


DATA_FILE = Path("data/processed/steam_top100_all.csv")
RAW_DIR = Path("data/raw/weekly")
REGIONS = {
    "CN": {"url_code": "CN", "country": "CN"},
    "JP": {"url_code": "JP", "country": "JP"},
    "US": {"url_code": "US", "country": "US"},
    "GLOBAL": {"url_code": "global", "country": "GLOBAL"},
}
MAX_RETRIES = 2

FIELDS = [
    "week_start", "country", "rank", "title", "appid", "url",
    "price_info", "rank_change", "extra"
]


def latest_completed_week_start(today=None):
    if today is None:
        today = date.today()
    current_tuesday = today - timedelta(days=(today.weekday() - 1) % 7)
    return current_tuesday - timedelta(days=7)


def make_driver():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument(
        "user-agent=Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
    )
    return webdriver.Chrome(options=options)


def safe_quit(driver):
    if driver is not None:
        try:
            driver.quit()
        except Exception:
            pass


def click_show_100(driver):
    xpath = (
        "//button["
        "contains(., '全100件を表示') "
        "or contains(., '100件すべてを表示') "
        "or contains(., 'See all 100')"
        "]"
    )
    button = WebDriverWait(driver, 20).until(
        EC.element_to_be_clickable((By.XPATH, xpath))
    )
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", button)
    time.sleep(1)
    driver.execute_script("arguments[0].click();", button)
    WebDriverWait(driver, 20).until(
        lambda d: len(d.find_elements(By.CSS_SELECTOR, "tbody tr")) >= 100
    )


def fetch_top100(region, week_start, driver):
    cfg = REGIONS[region]
    url = f"https://store.steampowered.com/charts/topsellers/{cfg['url_code']}/{week_start}"
    driver.get(url)

    WebDriverWait(driver, 25).until(
        EC.presence_of_element_located((By.TAG_NAME, "tbody"))
    )
    time.sleep(2)

    initial_rows = driver.find_elements(By.CSS_SELECTOR, "tbody tr")
    print(f"[{region}][{week_start}] initial rows: {len(initial_rows)}")

    click_show_100(driver)
    rows = driver.find_elements(By.CSS_SELECTOR, "tbody tr")
    print(f"[{region}][{week_start}] rows after click: {len(rows)}")

    if len(rows) != 100:
        raise RuntimeError(f"Expected 100 rows, got {len(rows)}")

    results = []
    for row in rows:
        cells = row.find_elements(By.TAG_NAME, "td")
        if len(cells) < 6:
            continue

        rank = cells[1].text.strip()
        title = cells[2].text.strip()
        price_info = cells[3].text.strip()
        rank_change = cells[4].text.strip()
        extra = cells[5].text.strip()

        try:
            link_elem = row.find_element(By.CSS_SELECTOR, "a[href*='/app/']")
            game_url = (link_elem.get_attribute("href") or "").strip()
        except Exception:
            game_url = ""

        match = re.search(r"/app/(\d+)/", game_url)
        appid = match.group(1) if match else ""

        results.append({
            "week_start": week_start,
            "country": cfg["country"],
            "rank": rank,
            "title": title,
            "appid": appid,
            "url": game_url,
            "price_info": price_info,
            "rank_change": rank_change,
            "extra": extra,
        })

    if len(results) != 100:
        raise RuntimeError(f"Parsed {len(results)} records instead of 100")

    return results


def fetch_with_retry(region, week_start):
    last_error = None
    for attempt in range(MAX_RETRIES + 1):
        driver = None
        try:
            if attempt:
                print(f"Retry {attempt}/{MAX_RETRIES}: {region} {week_start}")
            driver = make_driver()
            return fetch_top100(region, week_start, driver)
        except Exception as exc:
            last_error = exc
            print(f"Attempt {attempt + 1}/{MAX_RETRIES + 1} failed for {region} {week_start}: {exc}")
            time.sleep(3)
        finally:
            safe_quit(driver)
    raise RuntimeError(f"All attempts failed for {region} {week_start}: {last_error}")


def validate(df):
    df = df.copy()
    df["week_start"] = pd.to_datetime(df["week_start"], errors="raise")
    df["rank"] = pd.to_numeric(df["rank"], errors="raise").astype(int)
    df["country"] = df["country"].astype(str).str.upper().str.strip()

    dupes = df.duplicated(["week_start", "country", "rank"], keep=False)
    if dupes.any():
        raise RuntimeError(f"Duplicate week/country/rank rows found: {dupes.sum()}")

    counts = df.groupby(["week_start", "country"]).size()
    bad = counts[counts != 100]
    if not bad.empty:
        raise RuntimeError(f"Groups not equal to 100 rows:\n{bad}")

    return df


def main():
    if not DATA_FILE.exists():
        raise FileNotFoundError(
            f"Missing {DATA_FILE}. Upload the historical steam_top100_all.csv first."
        )

    RAW_DIR.mkdir(parents=True, exist_ok=True)

    existing = pd.read_csv(DATA_FILE, dtype={"appid": "string"})
    existing = existing[FIELDS].copy()
    existing["week_start"] = pd.to_datetime(existing["week_start"], errors="raise")
    existing["country"] = existing["country"].astype(str).str.upper().str.strip()

    # Use the latest week that exists for all four regions as the baseline.
    coverage = existing.groupby("week_start")["country"].nunique().sort_index()
    complete_weeks = coverage[coverage >= 4].index
    if len(complete_weeks) == 0:
        raise RuntimeError("No historical week contains all four regions.")

    latest_existing = complete_weeks.max().date()
    latest_available = latest_completed_week_start()

    print(f"Latest complete week in file: {latest_existing}")
    print(f"Latest completed Steam week expected: {latest_available}")

    if latest_existing >= latest_available:
        print("Already up to date. Nothing to do.")
        return

    weeks = []
    d = latest_existing + timedelta(days=7)
    while d <= latest_available:
        weeks.append(d)
        d += timedelta(days=7)

    new_rows = []
    for week in weeks:
        week_text = week.isoformat()
        week_rows = []
        print(f"\n===== {week_text} =====")
        for region in REGIONS:
            data = fetch_with_retry(region, week_text)
            week_rows.extend(data)
            new_rows.extend(data)
            print(f"Completed {region} {week_text}: {len(data)} rows")

        raw_file = RAW_DIR / f"steam_top100_{week_text}.csv"
        pd.DataFrame(week_rows, columns=FIELDS).to_csv(raw_file, index=False, encoding="utf-8-sig")
        print(f"Saved weekly raw file: {raw_file}")

    new_df = pd.DataFrame(new_rows, columns=FIELDS)
    new_df["week_start"] = pd.to_datetime(new_df["week_start"])

    combined = pd.concat([existing, new_df], ignore_index=True)
    combined = combined.drop_duplicates(["week_start", "country", "rank"], keep="last")
    combined = validate(combined)
    combined = combined.sort_values(["week_start", "country", "rank"]).reset_index(drop=True)
    combined["week_start"] = combined["week_start"].dt.strftime("%Y-%m-%d")

    combined.to_csv(DATA_FILE, index=False, encoding="utf-8-sig")
    print(f"Updated {DATA_FILE}: {len(combined)} rows")


if __name__ == "__main__":
    main()
