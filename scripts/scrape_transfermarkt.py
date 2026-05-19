"""
scrape_transfermarkt.py
Scrapes injury history (seasons 2023/24, 2024/25, 2025/26) for the full Real Madrid squad.
Output: data/raw/transfermarkt_injuries.csv

Anti-block: undetected-chromedriver, 45s timeout, retry per player, rotating delays.
Progress is saved after each player so a crash doesn't lose data.
"""

import sys
import time
import random
import logging
import csv
from pathlib import Path
from datetime import datetime

import pandas as pd
from bs4 import BeautifulSoup
from fake_useragent import UserAgent
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "logs"
DATA_RAW = ROOT / "data" / "raw"
LOG_DIR.mkdir(exist_ok=True)
DATA_RAW.mkdir(parents=True, exist_ok=True)
OUTPUT_CSV = DATA_RAW / "transfermarkt_injuries.csv"
PROGRESS_FILE = DATA_RAW / ".tm_progress.txt"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "scraping.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Real Madrid squad 2023/24 → 2025/26  (Transfermarkt player-profile slugs + IDs)
# Format: (display_name, tm_slug, tm_id)
# Nacho Fernández and Toni Kroos excluded (both left in summer 2024).
# ---------------------------------------------------------------------------
REAL_MADRID_SQUAD = [
    ("Thibaut Courtois",      "thibaut-courtois",      "161751"),
    ("Andriy Lunin",          "andriy-lunin",           "394471"),
    ("Fran González",         "fran-gonzalez",          "685227"),
    ("Dani Carvajal",         "dani-carvajal",          "138927"),
    ("Éder Militão",          "eder-militao",           "374246"),
    ("Antonio Rüdiger",       "antonio-rudiger",        "167385"),
    ("Ferland Mendy",         "ferland-mendy",          "342229"),
    ("David Alaba",           "david-alaba",            "57091"),
    ("Lucas Vázquez",         "lucas-vazquez",          "153729"),
    ("Raúl Asencio",          "raul-asencio",           "776581"),
    ("Fran García",           "fran-garcia",            "471581"),
    ("Luka Modric",           "luka-modric",            "27992"),
    ("Federico Valverde",     "federico-valverde",      "349472"),
    ("Aurélien Tchouaméni",   "aurelien-tchouameni",    "483746"),
    ("Eduardo Camavinga",     "eduardo-camavinga",      "532538"),
    ("Jude Bellingham",       "jude-bellingham",        "581678"),
    ("Dani Ceballos",         "dani-ceballos",          "163340"),
    ("Brahim Díaz",           "brahim-diaz",            "357662"),
    ("Arda Güler",            "arda-guler",             "835028"),
    ("Vinícius Júnior",       "vinicius-junior",        "371998"),
    ("Rodrygo",               "rodrygo",                "412363"),
    ("Kylian Mbappé",         "kylian-mbappe",          "550154"),  # joined summer 2024
    ("Endrick",               "endrick",                "940001"),  # joined summer 2024
]

BASE_URL = "https://www.transfermarkt.com/{slug}/verletzungen/spieler/{id}"
# Season start years: 2023 = 2023/24, 2024 = 2024/25, 2025 = 2025/26
SEASONS = ["2025", "2024", "2023"]

UA = UserAgent()


# ---------------------------------------------------------------------------
# Driver helpers
# ---------------------------------------------------------------------------
def build_driver() -> uc.Chrome:
    options = uc.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    return uc.Chrome(options=options, use_subprocess=True)


def random_delay(min_s: float = 3.0, max_s: float = 8.0):
    t = random.uniform(min_s, max_s)
    log.debug(f"Sleeping {t:.1f}s")
    time.sleep(t)


# ---------------------------------------------------------------------------
# Scraping
# ---------------------------------------------------------------------------
def scrape_player_injuries(driver: uc.Chrome, name: str, slug: str, player_id: str) -> list[dict]:
    url = BASE_URL.format(slug=slug, id=player_id)
    log.info(f"Fetching injuries for {name}: {url}")

    try:
        driver.get(url)
        random_delay(2, 4)
        # Accept cookie banner if present
        try:
            btn = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.XPATH, "//button[contains(text(),'Agree') or contains(text(),'Accept') or contains(text(),'Akzeptieren')]"))
            )
            btn.click()
            time.sleep(1)
        except TimeoutException:
            pass

        WebDriverWait(driver, 45).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "table.items"))
        )
    except TimeoutException:
        log.warning(f"Timeout loading page for {name} — bot-detection likely, skipping")
        return []
    except WebDriverException as e:
        log.error(f"WebDriver error for {name}: {e}")
        return []

    soup = BeautifulSoup(driver.page_source, "lxml")
    records = []

    table = soup.find("table", class_="items")
    if not table:
        log.warning(f"No injury table found for {name}")
        return []

    rows = table.find("tbody").find_all("tr") if table.find("tbody") else []
    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 6:
            continue
        try:
            season      = cells[0].get_text(strip=True)
            injury      = cells[1].get_text(strip=True)
            date_from   = cells[2].get_text(strip=True)
            date_until  = cells[3].get_text(strip=True)
            days_out    = cells[4].get_text(strip=True)
            games_missed= cells[5].get_text(strip=True)

            # Filter to last 3 seasons only
            # Transfermarkt uses both "2024/25" and "24/25" formats
            season_year = season.split("/")[0] if "/" in season else season
            if len(season_year) == 2:
                season_year = "20" + season_year
            if season_year not in SEASONS:
                continue

            records.append({
                "player":       name,
                "player_id":    player_id,
                "season":       season,
                "injury":       injury,
                "date_from":    date_from,
                "date_until":   date_until,
                "days_out":     days_out,
                "games_missed": games_missed,
                "scraped_at":   datetime.utcnow().isoformat(),
            })
        except Exception as e:
            log.debug(f"Row parse error for {name}: {e}")
            continue

    log.info(f"  -> {len(records)} injury records found for {name}")
    return records


# ---------------------------------------------------------------------------
# Progress tracking
# ---------------------------------------------------------------------------
def load_progress() -> set:
    if PROGRESS_FILE.exists():
        return set(PROGRESS_FILE.read_text(encoding="utf-8").splitlines())
    return set()


def save_progress(done: set):
    PROGRESS_FILE.write_text("\n".join(sorted(done)), encoding="utf-8")


def append_to_csv(records: list[dict]):
    if not records:
        return
    fieldnames = ["player", "player_id", "season", "injury", "date_from", "date_until",
                  "days_out", "games_missed", "scraped_at"]
    write_header = not OUTPUT_CSV.exists()
    with open(OUTPUT_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        writer.writerows(records)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    log.info("=== scrape_transfermarkt.py START ===")
    done = load_progress()
    log.info(f"Already scraped: {len(done)}/{len(REAL_MADRID_SQUAD)} players")

    remaining = [(n, s, i) for n, s, i in REAL_MADRID_SQUAD if n not in done]
    if not remaining:
        log.info("All players already scraped. Delete data/raw/.tm_progress.txt to re-run.")
        return

    driver = build_driver()
    try:
        for idx, (name, slug, player_id) in enumerate(remaining, 1):
            log.info(f"[{idx}/{len(remaining)}] Processing: {name}")
            try:
                records = scrape_player_injuries(driver, name, slug, player_id)
                append_to_csv(records)
                done.add(name)
                save_progress(done)
            except Exception as e:
                log.error(f"Unhandled error for {name}: {e}")

            # Longer pause between players; extra pause every 5 players
            if idx % 5 == 0:
                pause = random.uniform(15, 30)
                log.info(f"Batch pause: {pause:.0f}s")
                time.sleep(pause)
            else:
                random_delay(3, 8)

    finally:
        driver.quit()

    log.info(f"=== DONE. Output: {OUTPUT_CSV} ===")
    if OUTPUT_CSV.exists():
        df = pd.read_csv(OUTPUT_CSV)
        log.info(f"Total rows: {len(df)}")
        print(df.head())
    else:
        log.warning("No injury records written — CSV not created (all players had 0 records in target seasons).")


if __name__ == "__main__":
    main()
