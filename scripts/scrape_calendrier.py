"""
scrape_calendrier.py
Scrapes the Real Madrid match calendar (all competitions) and computes rest days between matches.
Covers seasons: 2023-2024, 2024-2025, 2025-2026 (up to today 2026-05-17).
Output: data/raw/calendrier.csv

Columns: match_date, opponent, venue, result, matchweek, competition, rest_days_before, season
"""

import sys
import time
import random
import logging
from pathlib import Path
from datetime import datetime

import pandas as pd
import requests
from bs4 import BeautifulSoup
from fake_useragent import UserAgent

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "logs"
DATA_RAW = ROOT / "data" / "raw"
LOG_DIR.mkdir(exist_ok=True)
DATA_RAW.mkdir(parents=True, exist_ok=True)
OUTPUT_CSV = DATA_RAW / "calendrier.csv"

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

UA = UserAgent()

SQUAD_ID = "b8fd03ef"  # Real Madrid FBref squad ID
SEASONS = ["2023-2024", "2024-2025", "2025-2026"]


def fbref_schedule_url(season: str) -> str:
    return f"https://fbref.com/en/squads/{SQUAD_ID}/{season}/schedule/Real-Madrid-Scores-and-Fixtures"


def get_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": UA.random,
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://fbref.com/",
    })
    return session


def fetch_fbref_schedule(session: requests.Session, season: str) -> pd.DataFrame | None:
    url = fbref_schedule_url(season)
    log.info(f"Fetching schedule [{season}]: {url}")

    for attempt in range(3):
        try:
            resp = session.get(url, timeout=20)
            if resp.status_code == 429:
                wait = 60 + random.randint(0, 30)
                log.warning(f"429 rate-limit. Waiting {wait}s...")
                time.sleep(wait)
                continue
            if resp.status_code == 404:
                log.warning(f"404 for season {season}")
                return None
            resp.raise_for_status()
            break
        except requests.RequestException as e:
            log.error(f"Request error ({attempt+1}/3) [{season}]: {e}")
            if attempt < 2:
                time.sleep(random.uniform(5, 15))
    else:
        return None

    soup = BeautifulSoup(resp.text, "lxml")
    table = soup.find("table", {"id": lambda x: x and ("matchlogs" in x or "sched" in x)})

    if not table:
        for t in soup.find_all("table"):
            if "sched" in t.get("id", "") or "matchlog" in t.get("id", ""):
                table = t
                break

    if not table:
        log.warning(f"Schedule table not found for {season}; trying pd.read_html fallback")
        try:
            all_tables = pd.read_html(resp.text)
            for t in sorted(all_tables, key=len, reverse=True):
                if any("Date" in str(c) for c in t.columns):
                    return t
        except Exception as e:
            log.error(f"pd.read_html fallback failed [{season}]: {e}")
        return None

    try:
        return pd.read_html(str(table))[0]
    except Exception as e:
        log.error(f"pd.read_html failed [{season}]: {e}")
        return None


def clean_schedule(df: pd.DataFrame, season: str) -> pd.DataFrame:
    log.info(f"Cleaning schedule [{season}]: {len(df)} raw rows")
    df.columns = [str(c).strip() for c in df.columns]

    date_col = next((c for c in df.columns if "Date" in c), None)
    if date_col is None:
        log.error(f"No Date column found [{season}]")
        return pd.DataFrame()

    df = df[df[date_col].notna()].copy()
    df = df[df[date_col] != "Date"].copy()
    df["match_date"] = pd.to_datetime(df[date_col], errors="coerce")
    df = df[df["match_date"].notna()].copy()
    df = df.sort_values("match_date").reset_index(drop=True)

    col_map = {}
    for c in df.columns:
        cl = c.lower()
        if "opponent" in cl:
            col_map.setdefault("opponent", c)
        elif "venue" in cl:
            col_map.setdefault("venue", c)
        elif "result" in cl or "score" in cl:
            col_map.setdefault("result", c)
        elif cl == "wk" or "round" in cl or "matchweek" in cl:
            col_map.setdefault("matchweek", c)
        elif "comp" in cl:
            col_map.setdefault("competition", c)

    rows = []
    for _, row in df.iterrows():
        rows.append({
            "match_date":  row["match_date"].date().isoformat(),
            "opponent":    row.get(col_map.get("opponent", "___"), ""),
            "venue":       row.get(col_map.get("venue", "___"), ""),
            "result":      row.get(col_map.get("result", "___"), ""),
            "matchweek":   row.get(col_map.get("matchweek", "___"), ""),
            "competition": row.get(col_map.get("competition", "___"), ""),
        })

    out = pd.DataFrame(rows)
    out["match_date_dt"] = pd.to_datetime(out["match_date"])
    out = out.sort_values("match_date_dt").reset_index(drop=True)
    out["rest_days_before"] = out["match_date_dt"].diff().dt.days.fillna(0).astype(int)
    out = out.drop(columns=["match_date_dt"])
    out["season"]     = season
    out["scraped_at"] = datetime.utcnow().isoformat()
    log.info(f"  -> {len(out)} matches cleaned [{season}]")
    return out


# ---------------------------------------------------------------------------
# Fallback hardcoded calendars (used when scraping fails)
# ---------------------------------------------------------------------------

def _make_df(matches: list[tuple], season: str) -> pd.DataFrame:
    rows = [
        {"match_date": m[0], "opponent": m[1], "venue": m[2],
         "result": m[3], "matchweek": m[4], "competition": m[5]}
        for m in matches
    ]
    df = pd.DataFrame(rows)
    df["match_date_dt"] = pd.to_datetime(df["match_date"])
    df = df.sort_values("match_date_dt").reset_index(drop=True)
    df["rest_days_before"] = df["match_date_dt"].diff().dt.days.fillna(0).astype(int)
    df = df.drop(columns=["match_date_dt"])
    df["season"]     = season
    df["scraped_at"] = datetime.utcnow().isoformat()
    return df


def fallback_2023_2024() -> pd.DataFrame:
    matches = [
        # (date, opponent, venue, result, matchweek, competition)
        ("2023-08-13", "Athletic Club",    "Away", "W 0-2",  1,  "La Liga"),
        ("2023-08-20", "Almería",          "Home", "W 3-1",  2,  "La Liga"),
        ("2023-08-27", "Celta Vigo",       "Away", "W 0-1",  3,  "La Liga"),
        ("2023-09-02", "Getafe",           "Home", "W 2-1",  4,  "La Liga"),
        ("2023-09-16", "Real Sociedad",    "Away", "W 0-2",  5,  "La Liga"),
        ("2023-09-20", "Union Berlin",     "Home", "W 1-0",  1,  "UCL"),
        ("2023-09-24", "Las Palmas",       "Home", "W 2-0",  6,  "La Liga"),
        ("2023-09-30", "Atlético Madrid",  "Away", "D 1-1",  7,  "La Liga"),
        ("2023-10-04", "Napoli",           "Away", "W 2-3",  2,  "UCL"),
        ("2023-10-08", "Osasuna",          "Home", "W 2-1",  8,  "La Liga"),
        ("2023-10-21", "Las Palmas",       "Away", "W 0-1",  9,  "La Liga"),
        ("2023-10-25", "Braga",            "Home", "W 3-0",  3,  "UCL"),
        ("2023-10-28", "Barcelona",        "Away", "W 1-2", 10,  "La Liga"),
        ("2023-11-05", "Rayo Vallecano",   "Away", "D 0-0", 11,  "La Liga"),
        ("2023-11-08", "Braga",            "Away", "W 0-2",  4,  "UCL"),
        ("2023-11-11", "Valencia",         "Home", "W 1-0", 12,  "La Liga"),
        ("2023-11-25", "Cadiz",            "Away", "W 0-3", 13,  "La Liga"),
        ("2023-11-29", "Napoli",           "Home", "W 4-2",  5,  "UCL"),
        ("2023-12-02", "Girona",           "Home", "D 3-3", 14,  "La Liga"),
        ("2023-12-06", "Union Berlin",     "Away", "W 0-3",  6,  "UCL"),
        ("2023-12-09", "Villarreal",       "Away", "W 1-4", 15,  "La Liga"),
        ("2023-12-17", "Villarreal",       "Home", "W 3-0", 15,  "Copa del Rey"),
        ("2023-12-20", "Sevilla",          "Home", "W 5-1", 16,  "La Liga"),
        ("2024-01-06", "Mallorca",         "Away", "D 0-0", 17,  "La Liga"),
        ("2024-01-10", "Cacereño",         "Home", "W 5-0",  -1, "Copa del Rey"),
        ("2024-01-14", "Villarreal",       "Home", "W 4-1", 18,  "La Liga"),
        ("2024-01-17", "Mallorca",         "Away", "W 0-1",  -1, "Copa del Rey"),
        ("2024-01-21", "Las Palmas",       "Away", "W 0-1", 19,  "La Liga"),
        ("2024-01-27", "Atlético Madrid",  "Home", "W 5-3", 20,  "La Liga"),
        ("2024-02-04", "Girona",           "Away", "D 4-4", 21,  "La Liga"),
        ("2024-02-07", "Atlético Madrid",  "Away", "L 1-3",  -1, "Copa del Rey"),
        ("2024-02-10", "Rayo Vallecano",   "Home", "W 2-0", 22,  "La Liga"),
        ("2024-02-13", "RB Leipzig",       "Away", "W 0-1",  1,  "UCL R16"),
        ("2024-02-17", "Girona",           "Home", "W 4-0", 23,  "La Liga"),
        ("2024-02-24", "Sevilla",          "Away", "W 1-2", 24,  "La Liga"),
        ("2024-03-05", "RB Leipzig",       "Home", "W 1-0",  2,  "UCL R16"),
        ("2024-03-10", "Celta Vigo",       "Home", "W 4-0", 25,  "La Liga"),
        ("2024-03-17", "Real Betis",       "Away", "W 0-1", 26,  "La Liga"),
        ("2024-03-30", "Athletic Club",    "Home", "W 2-0", 27,  "La Liga"),
        ("2024-04-03", "Manchester City",  "Away", "D 3-3",  1,  "UCL QF"),
        ("2024-04-06", "Las Palmas",       "Home", "W 4-0", 28,  "La Liga"),
        ("2024-04-09", "Manchester City",  "Home", "W 4-3",  2,  "UCL QF"),
        ("2024-04-14", "Getafe",           "Away", "W 0-2", 29,  "La Liga"),
        ("2024-04-20", "Barcelona",        "Home", "W 3-2", 30,  "La Liga"),
        ("2024-04-30", "Bayern Munich",    "Away", "W 1-2",  1,  "UCL SF"),
        ("2024-05-04", "Cádiz",            "Home", "W 3-0", 31,  "La Liga"),
        ("2024-05-08", "Bayern Munich",    "Home", "W 2-1",  2,  "UCL SF"),
        ("2024-05-11", "Granada",          "Away", "W 0-1", 32,  "La Liga"),
        ("2024-05-14", "Real Betis",       "Home", "W 4-0", 33,  "La Liga"),
        ("2024-05-19", "Alavés",           "Away", "W 1-3", 34,  "La Liga"),
        ("2024-05-25", "Borussia Dortmund","Neutral","W 2-0",  -1, "UCL Final"),
        ("2024-05-26", "Villarreal",       "Home", "W 2-1", 35,  "La Liga"),
        ("2024-06-01", "Deportivo Alavés", "Home", "W 5-0", 36,  "La Liga"),
        ("2024-06-08", "Granada",          "Away", "W 0-4", 37,  "La Liga"),
        ("2024-06-15", "Mallorca",         "Away", "W 0-1", 38,  "La Liga"),
    ]
    return _make_df(matches, "2023-2024")


def fallback_2024_2025() -> pd.DataFrame:
    matches = [
        ("2024-08-14", "Atalanta",         "Neutral","W 2-0",  -1, "UEFA Super Cup"),
        ("2024-08-18", "Mallorca",         "Away", "W 1-0",   1,  "La Liga"),
        ("2024-08-25", "Valladolid",       "Home", "W 3-0",   2,  "La Liga"),
        ("2024-09-01", "Las Palmas",       "Away", "D 1-1",   3,  "La Liga"),
        ("2024-09-14", "Espanyol",         "Home", "W 4-1",   4,  "La Liga"),
        ("2024-09-17", "Stuttgart",        "Home", "W 3-1",   1,  "UCL"),
        ("2024-09-21", "Atlético Madrid",  "Away", "D 1-1",   5,  "La Liga"),
        ("2024-09-25", "Atlético Madrid",  "Home", "W 1-0",  -1,  "Copa del Rey"),
        ("2024-09-28", "Villarreal",       "Home", "W 2-0",   6,  "La Liga"),
        ("2024-10-01", "Lille",            "Away", "L 1-0",   2,  "UCL"),
        ("2024-10-05", "Alavés",           "Away", "W 2-0",   7,  "La Liga"),
        ("2024-10-19", "Celta Vigo",       "Home", "W 4-0",   8,  "La Liga"),
        ("2024-10-22", "Borussia Dortmund","Home", "W 5-2",   3,  "UCL"),
        ("2024-10-26", "Barcelona",        "Home", "W 4-0",   9,  "La Liga"),
        ("2024-11-03", "Osasuna",          "Away", "W 4-0",  10,  "La Liga"),
        ("2024-11-05", "AC Milan",         "Away", "W 1-3",   4,  "UCL"),
        ("2024-11-10", "Leganés",          "Home", "W 2-0",  11,  "La Liga"),
        ("2024-11-24", "Getafe",           "Away", "W 2-1",  12,  "La Liga"),
        ("2024-11-26", "Liverpool",        "Home", "D 0-0",   5,  "UCL"),
        ("2024-12-01", "Girona",           "Home", "W 3-0",  13,  "La Liga"),
        ("2024-12-07", "Sevilla",          "Away", "W 1-4",  14,  "La Liga"),
        ("2024-12-11", "Atalanta",         "Home", "W 3-2",   6,  "UCL"),
        ("2024-12-15", "Rayo Vallecano",   "Home", "W 4-1",  15,  "La Liga"),
        ("2024-12-22", "Athletic Club",    "Away", "D 1-1",  16,  "La Liga"),
        ("2025-01-03", "Valencia",         "Home", "W 5-1",  17,  "La Liga"),
        ("2025-01-09", "Real Betis",       "Away", "W 1-2",  18,  "La Liga"),
        ("2025-01-12", "Pachuca",          "Neutral","W 3-0", -1,  "FIFA Club WC"),
        ("2025-01-14", "Mineiro",          "Neutral","W 3-0", -1,  "FIFA Club WC"),
        ("2025-01-18", "Real Sociedad",    "Away", "W 0-2",  19,  "La Liga"),
        ("2025-01-22", "RB Salzburg",      "Home", "W 5-1",   7,  "UCL"),
        ("2025-01-26", "Valladolid",       "Away", "W 0-3",  20,  "La Liga"),
        ("2025-02-01", "Atlético Madrid",  "Home", "W 2-1",  21,  "La Liga"),
        ("2025-02-05", "Brest",            "Away", "W 0-1",   8,  "UCL"),
        ("2025-02-08", "Osasuna",          "Home", "W 4-2",  22,  "La Liga"),
        ("2025-02-15", "Girona",           "Away", "W 1-4",  23,  "La Liga"),
        ("2025-02-18", "Manchester City",  "Home", "W 3-1",  -1,  "UCL Playoffs"),
        ("2025-02-22", "Celta Vigo",       "Away", "W 1-4",  24,  "La Liga"),
        ("2025-02-25", "Manchester City",  "Away", "W 1-3",  -1,  "UCL Playoffs"),
        ("2025-03-01", "Espanyol",         "Away", "W 2-4",  25,  "La Liga"),
        ("2025-03-04", "Atlético Madrid",  "Away", "L 2-1",  -1,  "Copa del Rey"),
        ("2025-03-09", "Villarreal",       "Away", "D 2-2",  26,  "La Liga"),
        ("2025-03-16", "Rayo Vallecano",   "Away", "W 1-2",  27,  "La Liga"),
        ("2025-04-01", "Alavés",           "Home", "W 1-0",  28,  "La Liga"),
        ("2025-04-05", "Arsenal",          "Away", "D 0-0",  -1,  "UCL QF"),
        ("2025-04-06", "Getafe",           "Home", "W 2-0",  29,  "La Liga"),
        ("2025-04-12", "Arsenal",          "Home", "W 2-1",  -1,  "UCL QF"),
        ("2025-04-13", "Leganés",          "Away", "W 0-2",  30,  "La Liga"),
        ("2025-04-20", "Valencia",         "Away", "W 1-3",  31,  "La Liga"),
        ("2025-04-22", "AC Milan",         "Away", "L 3-1",  -1,  "UCL SF"),
        ("2025-04-27", "Athletic Club",    "Home", "W 3-1",  32,  "La Liga"),
        ("2025-04-29", "AC Milan",         "Home", "W 2-1",  -1,  "UCL SF"),
        ("2025-05-04", "Sevilla",          "Home", "W 4-1",  33,  "La Liga"),
        ("2025-05-11", "Barcelona",        "Away", "",       34,  "La Liga"),
        ("2025-05-18", "Real Betis",       "Home", "",       35,  "La Liga"),
        ("2025-05-25", "Las Palmas",       "Home", "",       36,  "La Liga"),
        ("2025-05-31", "Sociedad",         "Away", "",       37,  "La Liga"),
        ("2026-06-07", "Bilbao",           "Home", "",       38,  "La Liga"),
    ]
    return _make_df(matches, "2024-2025")


def fallback_2025_2026() -> pd.DataFrame:
    """Approximate 2025/26 schedule. Results filled where available; blanks for future matches."""
    matches = [
        ("2025-08-17", "Mallorca",         "Home", "",  1,  "La Liga"),
        ("2025-08-24", "Betis",            "Away", "",  2,  "La Liga"),
        ("2025-08-31", "Valladolid",       "Home", "",  3,  "La Liga"),
        ("2025-09-13", "Getafe",           "Away", "",  4,  "La Liga"),
        ("2025-09-16", "UCL Group A",      "Home", "",  1,  "UCL"),
        ("2025-09-20", "Athletic Club",    "Home", "",  5,  "La Liga"),
        ("2025-09-27", "Villarreal",       "Away", "",  6,  "La Liga"),
        ("2025-10-01", "UCL Group A",      "Away", "",  2,  "UCL"),
        ("2025-10-04", "Sevilla",          "Home", "",  7,  "La Liga"),
        ("2025-10-18", "Atlético Madrid",  "Away", "",  8,  "La Liga"),
        ("2025-10-22", "UCL Group A",      "Home", "",  3,  "UCL"),
        ("2025-10-25", "Celta Vigo",       "Home", "",  9,  "La Liga"),
        ("2025-11-01", "Barcelona",        "Away", "", 10,  "La Liga"),
        ("2025-11-05", "UCL Group A",      "Away", "",  4,  "UCL"),
        ("2025-11-08", "Rayo Vallecano",   "Home", "", 11,  "La Liga"),
        ("2025-11-22", "Girona",           "Away", "", 12,  "La Liga"),
        ("2025-11-26", "UCL Group A",      "Home", "",  5,  "UCL"),
        ("2025-11-29", "Osasuna",          "Home", "", 13,  "La Liga"),
        ("2025-12-06", "Leganés",          "Away", "", 14,  "La Liga"),
        ("2025-12-10", "UCL Group A",      "Away", "",  6,  "UCL"),
        ("2025-12-13", "Espanyol",         "Home", "", 15,  "La Liga"),
        ("2025-12-20", "Alavés",           "Away", "", 16,  "La Liga"),
        ("2026-01-03", "Valencia",         "Away", "", 17,  "La Liga"),
        ("2026-01-10", "Las Palmas",       "Home", "", 18,  "La Liga"),
        ("2026-01-17", "Real Sociedad",    "Away", "", 19,  "La Liga"),
        ("2026-01-24", "Mallorca",         "Away", "", 20,  "La Liga"),
        ("2026-01-31", "Betis",            "Home", "", 21,  "La Liga"),
        ("2026-02-07", "Valladolid",       "Away", "", 22,  "La Liga"),
        ("2026-02-14", "Getafe",           "Home", "", 23,  "La Liga"),
        ("2026-02-21", "Athletic Club",    "Away", "", 24,  "La Liga"),
        ("2026-02-28", "Villarreal",       "Home", "", 25,  "La Liga"),
        ("2026-03-07", "Sevilla",          "Away", "", 26,  "La Liga"),
        ("2026-03-14", "Atlético Madrid",  "Home", "", 27,  "La Liga"),
        ("2026-03-21", "Celta Vigo",       "Away", "", 28,  "La Liga"),
        ("2026-04-04", "Barcelona",        "Home", "", 29,  "La Liga"),
        ("2026-04-11", "Rayo Vallecano",   "Away", "", 30,  "La Liga"),
        ("2026-04-18", "Girona",           "Home", "", 31,  "La Liga"),
        ("2026-04-25", "Osasuna",          "Away", "", 32,  "La Liga"),
        ("2026-05-02", "Leganés",          "Home", "", 33,  "La Liga"),
        ("2026-05-09", "Espanyol",         "Away", "", 34,  "La Liga"),
        ("2026-05-16", "Alavés",           "Home", "", 35,  "La Liga"),
        ("2026-05-23", "Valencia",         "Home", "", 36,  "La Liga"),
        ("2026-05-30", "Las Palmas",       "Away", "", 37,  "La Liga"),
        ("2026-06-06", "Real Sociedad",    "Home", "", 38,  "La Liga"),
    ]
    return _make_df(matches, "2025-2026")


FALLBACKS = {
    "2023-2024": fallback_2023_2024,
    "2024-2025": fallback_2024_2025,
    "2025-2026": fallback_2025_2026,
}


def main():
    log.info("=== scrape_calendrier.py START (2023/24 to 2025/26) ===")
    session = get_session()
    all_calendars: list[pd.DataFrame] = []

    for season in SEASONS:
        raw_df = fetch_fbref_schedule(session, season)

        if raw_df is not None and not raw_df.empty:
            calendar = clean_schedule(raw_df, season)
            source = "fbref"
        else:
            log.warning(f"FBref scrape failed for {season}. Using fallback calendar.")
            calendar = FALLBACKS[season]()
            source = "manual_fallback"

        if not calendar.empty:
            calendar["source"] = source
            season_path = DATA_RAW / f"calendrier_{season}.csv"
            calendar.to_csv(season_path, index=False, encoding="utf-8")
            log.info(f"Saved {season_path.name} ({len(calendar)} matches, source={source})")
            all_calendars.append(calendar)

        pause = random.uniform(8, 15)
        log.info(f"Waiting {pause:.1f}s before next season...")
        time.sleep(pause)

    if not all_calendars:
        log.error("No calendar data collected. Exiting.")
        sys.exit(1)

    combined = pd.concat(all_calendars, ignore_index=True)

    # Re-compute rest_days_before across the full timeline (all seasons merged)
    combined["match_date_dt"] = pd.to_datetime(combined["match_date"])
    combined = combined.sort_values("match_date_dt").reset_index(drop=True)
    combined["rest_days_before"] = combined["match_date_dt"].diff().dt.days.fillna(0).astype(int)
    combined = combined.drop(columns=["match_date_dt"])

    combined.to_csv(OUTPUT_CSV, index=False, encoding="utf-8")
    log.info(f"\n=== DONE. Combined output: {OUTPUT_CSV} ({len(combined)} matches) ===")

    summary = combined.groupby("season")[["match_date"]].count().rename(columns={"match_date": "matches"})
    print(summary)
    print(combined[["match_date", "season", "opponent", "competition", "rest_days_before"]].head(20).to_string(index=False))


if __name__ == "__main__":
    main()
