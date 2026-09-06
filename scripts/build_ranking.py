#!/usr/bin/env python3
"""Build the unified TE4 ranking from WTSL and Mana Games.

Usage:
    python scripts/build_ranking.py [--mana-login]

Scrapes both tours, merges identities, computes the unified score, and
writes data/current.json + data/history/<week>.json. Points are never
written to those public files - they only live in data/_private/ for
debugging, which is gitignored.

If either tour fails to scrape, the script aborts before writing anything.
"""
import argparse
import datetime
import json
import re
import sys
import unicodedata
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
HISTORY_DIR = DATA_DIR / "history"
PRIVATE_DIR = DATA_DIR / "_private"
ALIASES_PATH = DATA_DIR / "aliases.json"
COUNTRIES_PATH = DATA_DIR / "countries.json"
CURRENT_PATH = DATA_DIR / "current.json"
MANA_PROFILE_DIR = PRIVATE_DIR / "mana_browser_profile"

WTSL_URL = "https://www.playwtsl.com/TE4/pages/rankings.php?tour=TE4"
MANA_URL = "https://www.managames.com/Forum/OT_Rankings.php"

TOP_N_FOR_NORMALIZATION = 100
BLEND_WEIGHT = 0.35

MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


class ScrapeError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# WTSL scraper
# --------------------------------------------------------------------------

def scrape_wtsl():
    resp = requests.get(WTSL_URL, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    tables = soup.find_all("table")
    if len(tables) < 4:
        raise ScrapeError(f"WTSL: expected at least 4 tables, found {len(tables)}")
    table = tables[3]
    rows = table.find_all("tr")[1:]

    players = []
    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 5:
            continue

        avatar_img = cells[2].find("img")
        avatar_url = None
        if avatar_img and avatar_img.get("src"):
            avatar_url = requests.compat.urljoin(WTSL_URL, avatar_img["src"])

        name_cell = cells[3]
        link = name_cell.find("a")
        name = (link.get_text(strip=True) if link else name_cell.get_text(strip=True)).strip()
        if not name:
            continue

        country_img = name_cell.find("img")
        country_name = None
        if country_img:
            country_name = (country_img.get("title") or country_img.get("alt") or "").strip() or None

        points_text = re.sub(r"[^0-9]", "", cells[4].get_text())
        points = int(points_text) if points_text else 0

        players.append({
            "name": name,
            "country_name": country_name,
            "avatar": avatar_url,
            "points": points,
        })

    if len(players) < 50:
        raise ScrapeError(f"WTSL: suspiciously few players parsed ({len(players)})")
    return players


# --------------------------------------------------------------------------
# Mana Games scraper (needs JS + logged-in session -> Playwright persistent context)
# --------------------------------------------------------------------------

def _looks_like_rankings(html):
    return "OT_Player.php" in html and "<table" in html.lower()


def scrape_mana(force_headed=False):
    from playwright.sync_api import sync_playwright

    MANA_PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    html = ""
    with sync_playwright() as p:
        def open_context(headless):
            context = p.chromium.launch_persistent_context(
                str(MANA_PROFILE_DIR),
                headless=headless,
                viewport={"width": 1280, "height": 900},
            )
            page = context.new_page()
            page.goto(MANA_URL, wait_until="networkidle", timeout=60000)
            return context, page

        context, page = open_context(headless=not force_headed)
        html = page.content()

        if not _looks_like_rankings(html):
            context.close()
            print("Mana Games: no se detecto sesion valida, abriendo navegador para login manual...")
            context, page = open_context(headless=False)
            html = page.content()
            if not _looks_like_rankings(html):
                input("Inicia sesion en el foro de Mana Games en la ventana abierta y pulsa Enter aqui para continuar...")
                page.goto(MANA_URL, wait_until="networkidle", timeout=60000)
                html = page.content()

        context.close()

    if not _looks_like_rankings(html):
        raise ScrapeError("Mana Games: no se pudo obtener la tabla de rankings ni tras el login manual")

    soup = BeautifulSoup(html, "html.parser")
    table = None
    for t in soup.find_all("table"):
        if t.find("th", string=re.compile("rank", re.I)):
            table = t
            break
    if table is None:
        raise ScrapeError("Mana Games: no se encontro la tabla de rankings")

    rows = table.find_all("tr")[1:]
    players = []
    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 5:
            continue

        name_cell = cells[2]
        link = name_cell.find("a")
        name = (link.get_text(strip=True) if link else name_cell.get_text(strip=True)).strip()
        if not name:
            continue

        country_name = cells[3].get_text(strip=True) or None
        points_text = re.sub(r"[^0-9]", "", cells[4].get_text())
        points = int(points_text) if points_text else 0

        players.append({
            "name": name,
            "country_name": country_name,
            "points": points,
        })

    if len(players) < 50:
        raise ScrapeError(f"Mana Games: suspiciously few players parsed ({len(players)})")
    return players


# --------------------------------------------------------------------------
# Identity matching (normalized name + aliases.json)
# --------------------------------------------------------------------------

def normalize_name(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def load_aliases():
    raw = json.loads(ALIASES_PATH.read_text(encoding="utf-8"))
    return {normalize_name(k): normalize_name(v) for k, v in raw.items()}


def canonical_key(name, aliases):
    norm = normalize_name(name)
    return aliases.get(norm, norm)


def levenshtein(a, b):
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if la == 0:
        return lb
    if lb == 0:
        return la
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        curr = [i] + [0] * lb
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            curr[j] = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost)
        prev = curr
    return prev[lb]


def find_alias_candidates(wtsl_players, mana_players, aliases):
    already = set(aliases.items()) | {(v, k) for k, v in aliases.items()}
    wtsl_norms = sorted({normalize_name(p["name"]) for p in wtsl_players})
    mana_norms = sorted({normalize_name(p["name"]) for p in mana_players})

    # Short names (<4 chars) make both the prefix rule and edit-distance-3 match
    # almost anything, which drowns real candidates in noise -- skip those.
    MIN_LEN = 4

    candidates = []
    for w in wtsl_norms:
        for m in mana_norms:
            if w == m or (w, m) in already:
                continue
            if len(w) < MIN_LEN or len(m) < MIN_LEN:
                continue
            if w.startswith(m) or m.startswith(w):
                candidates.append((w, m, "prefix"))
                continue
            d = levenshtein(w, m)
            if d <= 3:
                candidates.append((w, m, f"levenshtein={d}"))
    return candidates


# --------------------------------------------------------------------------
# Country name -> ISO 3166-1 alpha-2
# --------------------------------------------------------------------------

def repair_mojibake(s):
    # Mana Games double-encodes some user-entered text (e.g. emoji flags), so a
    # correctly-decoded UTF-8 response still contains UTF-8 bytes reinterpreted
    # as Latin-1 codepoints. Undo that where possible; leave untouched strings
    # that were never mangled (they won't round-trip through latin-1).
    try:
        return s.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s


def strip_accents(s):
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def clean_country_text(s):
    s = repair_mojibake(s)
    s = strip_accents(s)
    s = re.sub(r"[^A-Za-z .\-]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def resolve_country(raw_name, countries_map, unresolved):
    if not raw_name:
        return None
    cleaned = clean_country_text(raw_name)
    if not cleaned:
        return None
    key = cleaned.lower()
    if key in countries_map:
        return countries_map[key]
    if "-" in cleaned:
        parts = [p.strip().lower() for p in cleaned.split("-") if p.strip()]
        for part in reversed(parts):
            if part in countries_map:
                return countries_map[part]
    unresolved.add(raw_name)
    return None


# --------------------------------------------------------------------------
# Week bookkeeping
# --------------------------------------------------------------------------

def compute_week_info(now):
    iso_year, iso_week, _ = now.isocalendar()
    week_id = f"{iso_year}-W{iso_week:02d}"
    week_label = f"Week {iso_week} · {MONTH_NAMES[now.month - 1]} {iso_year}"
    return week_id, week_label


def find_previous_week_id(current_week_id):
    if not HISTORY_DIR.exists():
        return None
    candidates = sorted(p.stem for p in HISTORY_DIR.glob("*.json") if p.stem < current_week_id)
    return candidates[-1] if candidates else None


# --------------------------------------------------------------------------
# Main pipeline
# --------------------------------------------------------------------------

def build(force_mana_login=False):
    print("Scraping WTSL...")
    wtsl_players = scrape_wtsl()
    print(f"  {len(wtsl_players)} players")

    print("Scraping Mana Games...")
    mana_players = scrape_mana(force_headed=force_mana_login)
    print(f"  {len(mana_players)} players")

    aliases = load_aliases()

    # --- merge identities ---
    merged = {}
    for p in wtsl_players:
        key = canonical_key(p["name"], aliases)
        entry = merged.setdefault(key, {"key": key})
        entry["wtsl_name"] = p["name"]
        entry["wtsl_points"] = p["points"]
        entry["avatar"] = p["avatar"]
        entry["wtsl_country"] = p["country_name"]

    for p in mana_players:
        key = canonical_key(p["name"], aliases)
        entry = merged.setdefault(key, {"key": key})
        entry["mana_name"] = p["name"]
        entry["mana_points"] = p["points"]
        entry["mana_country"] = p["country_name"]

    # --- normalization anchor: top 100 of each tour, by that tour's own order ---
    sw = sum(p["points"] for p in wtsl_players[:TOP_N_FOR_NORMALIZATION])
    sm = sum(p["points"] for p in mana_players[:TOP_N_FOR_NORMALIZATION])
    if sw <= 0 or sm <= 0:
        raise ScrapeError(f"Top-{TOP_N_FOR_NORMALIZATION} point sums look wrong (SW={sw}, SM={sm})")
    ref = (sw + sm) / 2
    kw = ref / sw
    km = ref / sm

    for entry in merged.values():
        has_w = "wtsl_points" in entry
        has_m = "mana_points" in entry
        a = kw * entry["wtsl_points"] if has_w else 0.0
        m = km * entry["mana_points"] if has_m else 0.0
        big, small = max(a, m), min(a, m)
        entry["a"], entry["m"] = a, m
        entry["A"], entry["B"] = big, small
        entry["U"] = big + BLEND_WEIGHT * small
        entry["tours"] = (["WTSL"] if has_w else []) + (["MANA"] if has_m else [])
        entry["name"] = entry.get("wtsl_name") or entry.get("mana_name")
        entry["country_name"] = entry.get("wtsl_country") or entry.get("mana_country")
        entry.setdefault("avatar", None)

    ordered = sorted(merged.values(), key=lambda e: (-e["U"], -e["A"], e["name"].lower()))

    # --- countries ---
    countries_map = json.loads(COUNTRIES_PATH.read_text(encoding="utf-8"))
    unresolved_countries = set()
    for e in ordered:
        e["country"] = resolve_country(e["country_name"], countries_map, unresolved_countries)

    # --- week / history bookkeeping ---
    now = datetime.datetime.now(datetime.timezone.utc)
    current_week_id, week_label = compute_week_info(now)
    prev_week_id = find_previous_week_id(current_week_id)

    prev_map = {}
    if prev_week_id:
        prev_doc = json.loads((HISTORY_DIR / f"{prev_week_id}.json").read_text(encoding="utf-8"))
        for pl in prev_doc["players"]:
            prev_map[canonical_key(pl["name"], aliases)] = pl["rank"]

    ever_seen = set()
    if HISTORY_DIR.exists():
        for hist_file in HISTORY_DIR.glob("*.json"):
            if hist_file.stem >= current_week_id:
                continue
            try:
                doc = json.loads(hist_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            for pl in doc.get("players", []):
                ever_seen.add(canonical_key(pl["name"], aliases))

    public_players = []
    for idx, e in enumerate(ordered, start=1):
        prev_rank = prev_map.get(e["key"])
        if prev_rank is not None:
            move = prev_rank - idx
            status = "up" if move > 0 else "down" if move < 0 else "same"
        else:
            move = None
            status = "return" if e["key"] in ever_seen else "new"

        public_players.append({
            "rank": idx,
            "name": e["name"],
            "country": e["country"],
            "country_name": e["country_name"],
            "avatar": e["avatar"],
            "tours": e["tours"],
            "prev_rank": prev_rank,
            "move": move,
            "status": status,
        })

    current_doc = {
        "week": current_week_id,
        "week_label": week_label,
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "previous_week": prev_week_id,
        "players": public_players,
    }

    private_doc = {
        "week": current_week_id,
        "sw": sw, "sm": sm, "ref": ref, "kw": kw, "km": km,
        "players": [
            {
                "rank": idx,
                "name": e["name"],
                "wtsl_points": e.get("wtsl_points"),
                "mana_points": e.get("mana_points"),
                "a": e["a"], "m": e["m"], "A": e["A"], "B": e["B"], "U": e["U"],
            }
            for idx, e in enumerate(ordered, start=1)
        ],
    }

    return current_doc, private_doc, unresolved_countries, wtsl_players, mana_players, aliases


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mana-login", action="store_true",
                         help="Force opening a headed browser to (re)authenticate on Mana Games")
    args = parser.parse_args()

    try:
        current_doc, private_doc, unresolved_countries, wtsl_players, mana_players, aliases = build(
            force_mana_login=args.mana_login
        )
    except ScrapeError as exc:
        print(f"ABORT: {exc}", file=sys.stderr)
        sys.exit(1)

    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    PRIVATE_DIR.mkdir(parents=True, exist_ok=True)

    CURRENT_PATH.write_text(json.dumps(current_doc, ensure_ascii=False, indent=2), encoding="utf-8")
    (HISTORY_DIR / f"{current_doc['week']}.json").write_text(
        json.dumps(current_doc, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (PRIVATE_DIR / f"{current_doc['week']}_debug.json").write_text(
        json.dumps(private_doc, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"\nWrote data/current.json and data/history/{current_doc['week']}.json "
          f"({len(current_doc['players'])} players)")

    if unresolved_countries:
        print(f"\nCountries not found in data/countries.json ({len(unresolved_countries)}):")
        for c in sorted(unresolved_countries):
            print(f"  - {c!r}")

    candidates = find_alias_candidates(wtsl_players, mana_players, aliases)
    if candidates:
        print(f"\nUnresolved alias candidates ({len(candidates)}) - review by hand, add to data/aliases.json:")
        for w, m, reason in candidates:
            print(f"  WTSL:{w!r} <-> MANA:{m!r}  ({reason})")


if __name__ == "__main__":
    main()
