"""
scrape_fbref.py  (uses Understat as FBref replacement — FBref is Cloudflare-blocked)
Scrapes Real Madrid player stats from Understat for 3 seasons: 2023/24, 2024/25, 2025/26.

Strategy: Playwright loads the team page (gets session cookie), then calls
/getTeamData/{team}/{year} from within the browser. The response contains
both match dates AND a 'players' array with per-season stats.

Output: data/raw/fbref_stats.csv
"""

import sys
import asyncio
import random
import logging
from pathlib import Path
from datetime import datetime

import pandas as pd
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "logs"
DATA_RAW = ROOT / "data" / "raw"
LOG_DIR.mkdir(exist_ok=True)
DATA_RAW.mkdir(parents=True, exist_ok=True)
OUTPUT_CSV = DATA_RAW / "fbref_stats.csv"

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

# Understat season key = start year
SEASONS = {
    "2023-2024": "2023",
    "2024-2025": "2024",
    "2025-2026": "2025",
}


def players_to_df(players: list, season_label: str) -> pd.DataFrame:
    rows = []
    for p in players:
        rows.append({
            "Player":       p.get("player_name", ""),
            "player_id":    p.get("id", ""),
            "position":     p.get("position", ""),
            "games":        p.get("games", 0),
            "Min":          p.get("time", 0),
            "Gls":          p.get("goals", 0),
            "Ast":          p.get("assists", 0),
            "shots":        p.get("shots", 0),
            "key_passes":   p.get("key_passes", 0),
            "xG":           p.get("xG", 0),
            "xA":           p.get("xA", 0),
            "npg":          p.get("npg", 0),
            "npxG":         p.get("npxG", 0),
            "xGChain":      p.get("xGChain", 0),
            "xGBuildup":    p.get("xGBuildup", 0),
            "yellow_cards": p.get("yellow_cards", 0),
            "red_cards":    p.get("red_cards", 0),
            "season":       season_label,
            "source":       "understat",
            "scraped_at":   datetime.now().isoformat(),
        })
    return pd.DataFrame(rows)


async def run():
    log.info("=== scrape_fbref.py (Understat/Playwright) START ===")
    all_dfs: list[pd.DataFrame] = []

    async with async_playwright() as p:
        br = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await br.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
        )
        page = await context.new_page()

        for season_label, year in SEASONS.items():
            team_url = f"https://understat.com/team/Real_Madrid/{year}"
            log.info(f"Loading [{season_label}]: {team_url}")

            try:
                await page.goto(team_url, wait_until="networkidle", timeout=30000)
                await page.wait_for_timeout(random.randint(2000, 3000))
            except PWTimeout:
                log.warning(f"Page load timeout [{season_label}]")
                continue
            except Exception as e:
                log.error(f"Error loading [{season_label}]: {e}")
                continue

            # Fetch the data API from within the browser (has session cookies)
            api_url = f"/getTeamData/Real%20Madrid/{year}"
            data = await page.evaluate(f"""async () => {{
                try {{
                    const r = await fetch('{api_url}', {{
                        headers: {{'X-Requested-With': 'XMLHttpRequest'}}
                    }});
                    if (!r.ok) return null;
                    return await r.json();
                }} catch(e) {{ return null; }}
            }}""")

            if not data or "players" not in data:
                log.warning(f"No players data in API response [{season_label}]")
                continue

            players = data["players"]
            log.info(f"  -> {len(players)} players [{season_label}]")

            df = players_to_df(players, season_label)
            path = DATA_RAW / f"fbref_stats_{season_label}.csv"
            df.to_csv(path, index=False, encoding="utf-8")
            log.info(f"Saved {path.name}")
            all_dfs.append(df)

            delay = random.uniform(4, 8)
            log.info(f"Waiting {delay:.1f}s...")
            await asyncio.sleep(delay)

        await br.close()

    if not all_dfs:
        log.error("No data collected.")
        sys.exit(1)

    final = pd.concat(all_dfs, ignore_index=True)
    final.to_csv(OUTPUT_CSV, index=False, encoding="utf-8")
    log.info(f"\n=== DONE. Output: {OUTPUT_CSV} ({len(final)} rows) ===")
    summary = final.groupby("season")[["Player"]].count().rename(columns={"Player": "players"})
    print(summary)
    print(final[["Player", "season", "Min", "Gls", "xG"]].head(15))


if __name__ == "__main__":
    asyncio.run(run())
