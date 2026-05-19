#!/usr/bin/env python3
"""
HantavirusMap.site data builder
Updates docs/data.json and injects fresh data into docs/index.html.

Usage:
    python build.py

Environment variables:
    MYSHIPTRACKING_API_KEY — optional API key for MyShipTracking API v2
    GITHUB_TOKEN — optional, used by CI to push commits
"""

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any

import urllib.request
import urllib.error

DOCS = Path(__file__).parent / "docs"
DATA_JSON = DOCS / "data.json"
INDEX_HTML = DOCS / "index.html"
CRUISE_HTML = DOCS / "cruise-2026.html"

WHO_SOURCE = "WHO DON601"
WHO_SOURCE_URL = "https://www.who.int/emergencies/disease-outbreak-news/item/2026-DON601"
WHO_AS_OF = "2026-05-13"

# DON601 figures
COUNTS = {
    "cases_total": 11,
    "cases_confirmed": 8,
    "cases_suspected": 2,
    "cases_inconclusive": 1,
    "deaths": 3,
    "critical": 0,
    "on_board": 25,
    "passengers": 0,
    "crew": 25,
    "as_of": WHO_AS_OF,
    "source": WHO_SOURCE,
    "source_url": WHO_SOURCE_URL,
}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_ais_myshiptracking_api(mmsi: str, api_key: str) -> Optional[Dict[str, Any]]:
    url = f"https://api.myshiptracking.com/api/v2/vessel?mmsi={mmsi}&response=extended"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.load(resp)
        # MyShipTracking wraps response in an envelope
        if isinstance(payload, dict) and payload.get("data"):
            d = payload["data"]
            if isinstance(d, list) and d:
                d = d[0]
            lat = float(d.get("lat") or d.get("latitude") or 0)
            lng = float(d.get("lon") or d.get("lng") or d.get("longitude") or 0)
            heading = float(d.get("heading") or d.get("course") or 0)
            speed = float(d.get("speed") or 0)
            return {
                "lat": round(lat, 5),
                "lng": round(lng, 5),
                "heading_deg": round(heading, 1) if heading else 0.0,
                "speed_knots": round(speed, 1),
                "fetched_at": now_iso(),
                "provider": "myshiptracking_api",
            }
    except Exception as e:
        print(f"[AIS] MyShipTracking API failed: {e}")
    return None


def fetch_ais_myshiptracking_scrape(mmsi: str) -> Optional[Dict[str, Any]]:
    """Lightweight scrape of the public vessel page for lat/lon."""
    url = f"https://www.myshiptracking.com/vessels/hondius-mmsi-{mmsi}-imo-9818709"
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; HantavirusMapBot/1.0)"
        })
        with urllib.request.urlopen(req, timeout=20) as resp:
            html = resp.read().decode("utf-8", errors="ignore")

        # Look for coordinates in the page
        m = re.search(r"coordinates\s*([\d.]+)°\s*/\s*([-\d.]+)°", html, re.IGNORECASE)
        if not m:
            m = re.search(r"lat.*?(\d+\.\d+).*?lon.*?(-?\d+\.\d+)", html, re.IGNORECASE | re.DOTALL)
        if m:
            lat = float(m.group(1))
            lng = float(m.group(2))
            heading = 0.0
            hm = re.search(r"course.*?([\d.]+)", html, re.IGNORECASE | re.DOTALL)
            if hm:
                heading = float(hm.group(1))
            return {
                "lat": round(lat, 5),
                "lng": round(lng, 5),
                "heading_deg": round(heading, 1),
                "speed_knots": 0.0,
                "fetched_at": now_iso(),
                "provider": "myshiptracking_scrape",
            }
    except Exception as e:
        print(f"[AIS] MyShipTracking scrape failed: {e}")
    return None


def fetch_ais(mmsi: str = "244327000") -> Dict[str, Any]:
    """Try API first, then scrape, then fallback to last known."""
    api_key = os.environ.get("MYSHIPTRACKING_API_KEY", "")
    if api_key:
        pos = fetch_ais_myshiptracking_api(mmsi, api_key)
        if pos:
            print(f"[AIS] Updated via MyShipTracking API: {pos['lat']}, {pos['lng']}")
            return pos

    pos = fetch_ais_myshiptracking_scrape(mmsi)
    if pos:
        print(f"[AIS] Updated via MyShipTracking scrape: {pos['lat']}, {pos['lng']}")
        return pos

    # Fallback: keep existing position from data.json but preserve original timestamp
    print("[AIS] All fetch methods failed; keeping last known position.")
    data = json.loads(DATA_JSON.read_text(encoding="utf-8"))
    old = data.get("vessel", {}).get("position", {})
    return {
        "lat": old.get("lat", 38.26135),
        "lng": old.get("lng", -12.36930),
        "heading_deg": old.get("heading_deg", 17.5),
        "speed_knots": old.get("speed_knots", 0.0),
        "fetched_at": old.get("fetched_at", now_iso()),
        "provider": old.get("provider", "fallback"),
    }


def load_data() -> Dict[str, Any]:
    return json.loads(DATA_JSON.read_text(encoding="utf-8"))


def save_data(data: Dict[str, Any]) -> None:
    DATA_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def update_data_json() -> Dict[str, Any]:
    data = load_data()
    now = now_iso()
    data["generated_at"] = now

    # Update vessel counts from WHO DON601
    vessel = data.setdefault("vessel", {})
    vessel["counts"] = {**COUNTS, "fetched_at": now}

    # Update AIS position
    pos = fetch_ais()
    vessel["position"] = pos

    # Add new spread arcs for France & Spain if missing
    arcs = data.setdefault("spread_arcs", [])
    existing_to = {a["to"] for a in arcs}
    if "France" not in existing_to:
        arcs.append({
            "from": "Argentina",
            "to": "France",
            "from_latlng": [-38.4, -63.6],
            "to_latlng": [46.2, 2.2],
            "label": "Repatriated passenger, confirmed case"
        })
    if "Spain" not in existing_to:
        arcs.append({
            "from": "Argentina",
            "to": "Spain",
            "from_latlng": [-38.4, -63.6],
            "to_latlng": [40.4, -3.7],
            "label": "Hondius port of disembarkation & confirmed case"
        })

    # Append latest track point (keep last 50)
    track = vessel.setdefault("track", [])
    if not track or track[-1].get("lat") != pos["lat"] or track[-1].get("lng") != pos["lng"]:
        track.append({
            "lat": pos["lat"],
            "lng": pos["lng"],
            "fetched_at": pos["fetched_at"],
        })
    if len(track) > 50:
        vessel["track"] = track[-50:]

    save_data(data)
    print(f"[data.json] Updated at {now}")
    return data


def inject_into_index_html(data: dict) -> None:
    html = INDEX_HTML.read_text(encoding="utf-8")
    now = now_iso()
    d = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    vessel = data.get("vessel", {})
    counts = vessel.get("counts", {})
    pos = vessel.get("position", {})

    # 1. Update HUD date
    html = re.sub(
        r'<div class="sub">Updated [^<]+</div>',
        f'<div class="sub">Updated {d} · rebuilds hourly</div>',
        html,
    )

    # 2. Update stat tiles (top-right)
    html = re.sub(
        r'title="Hondius cluster — confirmed \+ suspected — WHO DON599">',
        f'title="Hondius cluster — confirmed + suspected — {WHO_SOURCE}">',
        html,
    )
    html = re.sub(
        r'<div class="v warn">7</div>\s+<div class="k">Cases · Hondius</div>',
        f'<div class="v warn">{counts.get("cases_total", 11)}</div>\n        <div class="k">Cases · Hondius</div>',
        html,
    )
    # Deaths tile title
    html = re.sub(
        r'title="Hondius cluster fatalities — WHO DON599">',
        f'title="Hondius cluster fatalities — {WHO_SOURCE}">',
        html,
    )

    # 3. Update outbreak vessel live counters
    html = re.sub(
        r'<div class="v warn">7</div>\s+<div class="k">Total cases</div>',
        f'<div class="v warn">{counts.get("cases_total", 11)}</div>\n          <div class="k">Total cases</div>',
        html,
    )
    html = re.sub(
        r'<div class="v" style="color: var\(--good\);">2</div>\s+<div class="k">Lab confirmed</div>',
        f'<div class="v" style="color: var(--good);">{counts.get("cases_confirmed", 8)}</div>\n          <div class="k">Lab confirmed</div>',
        html,
    )
    html = re.sub(
        r'<div class="v warn">5</div>\s+<div class="k">Suspected</div>',
        f'<div class="v warn">{counts.get("cases_suspected", 2)}</div>\n          <div class="k">Suspected</div>',
        html,
    )
    html = re.sub(
        r'<div class="v alert">1</div>\s+<div class="k">Critical</div>',
        f'<div class="v alert">{counts.get("critical", 0)}</div>\n          <div class="k">Critical</div>',
        html,
    )
    html = re.sub(
        r'<div class="v">147</div>\s+<div class="k">On board \(88\+59\)</div>',
        f'<div class="v">{counts.get("on_board", 25)}</div>\n          <div class="k">On board ({counts.get("passengers", 0)}+{counts.get("crew", 25)})</div>',
        html,
    )

    # 4. Update position / heading / AIS fix
    html = re.sub(
        r'<div style="font-size: 18px; font-variant-numeric: tabular-nums;">\s*31\.462°,\s*-14\.735°\s*</div>',
        f'<div style="font-size: 18px; font-variant-numeric: tabular-nums;">\n              {pos.get("lat", 0):.3f}°,\n              {pos.get("lng", 0):.3f}°\n            </div>',
        html,
    )
    html = re.sub(
        r'<div style="font-size: 18px; font-variant-numeric: tabular-nums;">\s*17°\s*</div>',
        f'<div style="font-size: 18px; font-variant-numeric: tabular-nums;">\n              {pos.get("heading_deg", 0):.1f}°\n            </div>',
        html,
    )
    html = re.sub(
        r'<div style="font-size: 13px; color: var\(--good\);">\s*2026-05-12 14:57 UTC\s*</div>',
        f'<div style="font-size: 13px; color: var(--good);">\n              {datetime.fromisoformat(pos.get("fetched_at", now).replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M UTC")}\n            </div>',
        html,
    )

    # 5. Update footer line
    html = re.sub(
        r'Counters as of 2026-05-04, scraped from\s+<a href="https://www\.who\.int/emergencies/disease-outbreak-news/item/2026-DON599"[^>]*>WHO DON599</a>',
        f'Counters as of {counts.get("as_of", WHO_AS_OF)}, scraped from\n        <a href="{WHO_SOURCE_URL}" target="_blank" rel="noopener">{WHO_SOURCE}</a>',
        html,
    )

    # 6. Replace window.HANTA object entirely
    hanta_json = json.dumps({
        "countries": data.get("country_index", []),
        "arcs": data.get("spread_arcs", []),
        "vessel": vessel,
    }, ensure_ascii=False)

    html = re.sub(
        r'window\.HANTA = \{[\s\S]*?\};',
        f'window.HANTA = {hanta_json};',
        html,
    )

    INDEX_HTML.write_text(html, encoding="utf-8")
    print("[index.html] Injected fresh data.")


def update_cruise_html() -> None:
    if not CRUISE_HTML.exists():
        return
    html = CRUISE_HTML.read_text(encoding="utf-8")

    # Update existing DON599 link to DON601 if present in the references list
    if "2026-DON601" not in html:
        # Insert DON601 after the timeline DON599 entry or in the references list
        # Look for the references <ul> near DON600
        html = re.sub(
            r'(<li><a href="https://www\.who\.int/emergencies/disease-outbreak-news/item/2026-DON600"[^>]*>WHO Disease Outbreak News DON600 \(7 May 2026\)</a></li>)',
            r'\1\n    <li><a href="https://www.who.int/emergencies/disease-outbreak-news/item/2026-DON601" target="_blank" rel="noopener">WHO Disease Outbreak News DON601 (13 May 2026)</a></li>',
            html,
        )

    CRUISE_HTML.write_text(html, encoding="utf-8")
    print("[cruise-2026.html] Updated references.")


def git_commit_and_push() -> None:
    """If running in CI, commit and push changes."""
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("[git] No GITHUB_TOKEN; skipping push.")
        return

    # Configure git for CI
    os.system('git config user.email "deploy@hantavirusmap.site"')
    os.system('git config user.name "HantavirusMap Deploy"')

    # Add changed files
    os.system("git add docs/data.json docs/index.html docs/cruise-2026.html")

    # Check if there is anything to commit
    diff = os.popen("git diff --cached --quiet || echo changed").read().strip()
    if diff != "changed":
        print("[git] No changes to commit.")
        return

    ret = os.system(f'git commit -m "Auto-update data.json + index.html @ {now_iso()}"')
    if ret != 0:
        print("[git] Commit failed.")
        return

    # Push using the token embedded in the remote URL
    remote_url = os.popen("git remote get-url origin").read().strip()
    # Replace any existing token or use the provided one
    if "github.com" in remote_url:
        # Build authenticated URL
        auth_url = f"https://x-access-token:{token}@github.com/jimmyxu07/hantavirus-tracker.git"
        os.system(f"git remote set-url origin {auth_url}")

    ret = os.system("git push origin main")
    if ret == 0:
        print("[git] Pushed successfully.")
    else:
        print("[git] Push failed.")


def main() -> int:
    print("=" * 50)
    print("HantavirusMap.site Builder")
    print("=" * 50)

    data = update_data_json()
    inject_into_index_html(data)
    update_cruise_html()
    git_commit_and_push()

    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
