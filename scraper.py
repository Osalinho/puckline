#!/usr/bin/env python3
"""
PUCKLINE scraper.py
====================

Co robi:
1. Dla każdej z 12 lig pobiera terminarz/wyniki sezonu 2026/27 z 24score.com
   (dwuetapowo: strona -> data_key -> /backend/load_page_data.php).
2. Merguje świeże dane z istniejącym data.json — NADPISUJE tylko sezon 2026-27
   (preseason + mainSeason) danej ligi. Sezony historyczne (wpisane ręcznie)
   i inne ligi zostają nietknięte.
3. Dla meczów, które się rozegrały: zapisuje score/p1/over15/played=true
   (i od tego momentu przestaje dla nich liczyć prognozę — zamrożona jako
   "co model przewidywał przed meczem").
4. Dla meczów jeszcze nierozegranych: przelicza prob/components/homePct/awayPct
   na bazie modelu wielosezonowego (patrz sekcja MODEL).
5. Zapisuje data.json. Jeśli któraś liga padnie (blok/zmiana strony), reszta
   i tak się zapisuje — na końcu skrypt kończy się kodem !=0, żeby Action
   pokazał czerwony X, ale dane pozostałych lig są bezpieczne.

WAŻNE — wymaga kalibracji na żywo:
Sekcje oznaczone `# === WYMAGA KALIBRACJI ===` zawierają parsowanie
konkretnej struktury 24score.com (regex na data_key, parsowanie odpowiedzi
backendu). Napisane wg opisu mechanizmu, ale strona mogła się zmienić od
ostatniego razu. Uruchom z DEBUG=1 (patrz niżej) i podeślij zrzuty z
/tmp/puckline_debug/, jeśli parser nie złapie danych za pierwszym razem.

Użycie:
    python scraper.py                  # wszystkie 12 lig
    python scraper.py --only NHL,AHL   # tylko wybrane ligi
    DEBUG=1 python scraper.py --only NHL   # zrzuca surowe odpowiedzi do /tmp/puckline_debug/
"""

import argparse
import json
import math
import os
import re
import sys
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

# ----------------------------------------------------------------------------
# KONFIGURACJA
# ----------------------------------------------------------------------------

DATA_JSON_PATH = os.environ.get("PUCKLINE_DATA_JSON", "data.json")
DEBUG = os.environ.get("DEBUG", "") == "1"
DEBUG_DIR = "/tmp/puckline_debug"

CURRENT_SEASON_KEY = "2026-27"
CURRENT_SEASON_SLUG = "2026-2027"

H2H_WEIGHT = 0.12
H2H_MIN_MATCHES = 2
HIST_DECAY = 0.25
CURRENT_WEIGHT_TAU = 25  # current_weight(n) = 0.9 * (1 - e^(-n/tau))
CURRENT_WEIGHT_MAX = 0.9

# klucz ligi -> {path w URL 24score.com, label do wyświetlenia (None dla NHL/AHL,
# bo index.html traktuje je specjalnie i nie ma dla nich pola "label")}
LEAGUES = {
    "NHL":         {"path": "usa/nhl",                       "label": None},
    "AHL":         {"path": "usa/ahl",                       "label": None},
    "CZECH":       {"path": "czech_republic/extraliga",      "label": "Czech Republic Extraliga"},
    "DENMARK":     {"path": "denmark/al-bank_ligaen",        "label": "Denmark AL-Bank Ligaen"},
    "FRANCE":      {"path": "france/ligue_magnus",           "label": "France Ligue Magnus"},
    "FINLAND":     {"path": "finland/sm-liiga",               "label": "Finland SM-Liiga"},
    "CANADA_OHL":  {"path": "canada_/ohl",                    "label": "Canada OHL"},
    "GERMANY":     {"path": "germany/del",                    "label": "Germany DEL"},
    "NORWAY":      {"path": "norway/ehl",                     "label": "Norway EHL"},
    "SLOVAKIA":    {"path": "slovakia/st_extraliga",          "label": "Slovakia ST Extraliga"},
    "SWITZERLAND": {"path": "switzerland/nla",                "label": "Switzerland NLA"},
    "SWEDEN":      {"path": "sweden/allsvenskan",             "label": "Sweden Allsvenskan"},
}

FIXTURES_URL_TMPL = "https://en.24score.com/ice_hockey/{path}/{season}/regular_season/fixtures/"
BACKEND_URL_TMPL = "https://en.24score.com/backend/load_page_data.php?data_key={key}"

HEADERS_PAGE = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Accept-Language": "en-US,en;q=0.9",
}


# ----------------------------------------------------------------------------
# WARSTWA SIECIOWA / PARSOWANIE 24score.com  === WYMAGA KALIBRACJI ===
# ----------------------------------------------------------------------------

def debug_dump(name, content):
    if not DEBUG:
        return
    os.makedirs(DEBUG_DIR, exist_ok=True)
    path = os.path.join(DEBUG_DIR, name)
    mode = "wb" if isinstance(content, (bytes, bytearray)) else "w"
    with open(path, mode, encoding=None if mode == "wb" else "utf-8") as f:
        f.write(content)
    print(f"  [debug] zapisano {path}", file=sys.stderr)


def extract_data_key(html: str) -> str:
    """
    Szuka data_key w HTML-u strony fixtures. Strona ładuje tabelę meczów
    przez AJAX pod /backend/load_page_data.php?data_key=XXXXX — sam klucz
    jest gdzieś zaszyty w inline <script> na stronie.

    Próbujemy kilku wzorców, bo dokładna nazwa zmiennej JS nie jest pewna
    bez podglądu strony na żywo. Jeśli żaden nie trafi, podnosimy wyjątek
    z fragmentem HTML w logu, żeby łatwo dograć właściwy regex.
    """
    patterns = [
        r'data_key["\']?\s*[:=]\s*["\']([a-zA-Z0-9_-]+)["\']',
        r'load_page_data\.php\?data_key=([a-zA-Z0-9_-]+)',
        r'dataKey["\']?\s*[:=]\s*["\']([a-zA-Z0-9_-]+)["\']',
    ]
    for pat in patterns:
        m = re.search(pat, html)
        if m:
            return m.group(1)
    raise RuntimeError(
        "Nie znaleziono data_key w HTML-u strony. Strona mogła zmienić "
        "mechanizm ładowania danych — potrzebny świeży podgląd źródła strony."
    )


def fetch_league_season_raw(session: requests.Session, league_key: str, season_slug: str):
    """
    Dwuetapowe pobranie: (1) strona fixtures -> data_key, (2) backend -> dane meczów.
    Zwraca surową listę meczów w formacie:
        {"home": str, "away": str, "date": "DD.MM.YYYY", "sortDate": "YYYY-MM-DD",
         "played": bool, "score": "H:A" | None, "p1": "H:A" | None}
    """
    conf = LEAGUES[league_key]
    page_url = FIXTURES_URL_TMPL.format(path=conf["path"], season=season_slug)

    resp = session.get(page_url, headers=HEADERS_PAGE, timeout=25)
    resp.raise_for_status()
    debug_dump(f"{league_key}_{season_slug}_page.html", resp.text)

    data_key = extract_data_key(resp.text)

    backend_url = BACKEND_URL_TMPL.format(key=data_key)
    backend_headers = dict(HEADERS_PAGE)
    backend_headers.update({
        "X-Requested-With": "XMLHttpRequest",
        "Referer": page_url,
        "Accept": "application/json, text/javascript, */*; q=0.01",
    })
    bresp = session.get(backend_url, headers=backend_headers, timeout=25)
    bresp.raise_for_status()
    debug_dump(f"{league_key}_{season_slug}_backend.raw", bresp.content)

    return parse_backend_response(bresp)


def parse_backend_response(resp: requests.Response):
    """
    Parsuje odpowiedź /backend/load_page_data.php. Próbuje najpierw JSON,
    potem HTML fragment (typowe dla tego typu AJAX-owych "widgetów tabeli").

    === WYMAGA KALIBRACJI ===
    Dokładny kształt odpowiedzi nie jest znany bez live-testu. Poniżej
    zaimplementowane są dwie sensowne ścieżki + haczyki na typowe warianty;
    jeśli żadna nie zadziała, ustaw DEBUG=1 i podeślij zawartość
    *_backend.raw — dopiszę wtedy dokładny parser pod realny format.
    """
    # Ścieżka A: JSON
    try:
        data = resp.json()
        return parse_matches_from_json(data)
    except (ValueError, json.JSONDecodeError):
        pass

    # Ścieżka B: HTML fragment
    html = resp.text
    return parse_matches_from_html(html)


def parse_matches_from_json(data):
    """
    Zakłada strukturę zbliżoną do: {"matches": [{...}, ...]} albo listę
    bezpośrednio. Dopasuj klucze po zobaczeniu prawdziwej odpowiedzi.
    """
    raw_list = None
    if isinstance(data, list):
        raw_list = data
    elif isinstance(data, dict):
        for key in ("matches", "fixtures", "data", "items", "rows"):
            if key in data and isinstance(data[key], list):
                raw_list = data[key]
                break
    if raw_list is None:
        raise RuntimeError("Nieznana struktura JSON z backendu — potrzebna kalibracja parse_matches_from_json().")

    out = []
    for row in raw_list:
        m = normalize_json_row(row)
        if m:
            out.append(m)
    return out


def normalize_json_row(row: dict):
    """Mapuje jeden wiersz JSON z backendu na nasz format. Nazwy kluczy do potwierdzenia."""
    home = row.get("home_team") or row.get("home") or row.get("team1")
    away = row.get("away_team") or row.get("away") or row.get("team2")
    date_raw = row.get("date") or row.get("match_date") or row.get("start_date")
    if not (home and away and date_raw):
        return None

    sort_date, display_date = normalize_date(date_raw)

    score_full = row.get("score") or row.get("final_score")
    score_p1 = row.get("p1_score") or row.get("period1_score") or row.get("score_1st")
    played = bool(score_full) or str(row.get("status", "")).lower() in ("finished", "ft", "ended")

    return {
        "home": clean_team_name(home),
        "away": clean_team_name(away),
        "date": display_date,
        "sortDate": sort_date,
        "played": played,
        "score": normalize_score(score_full) if played else None,
        "p1": normalize_score(score_p1) if played else None,
    }


def parse_matches_from_html(html: str):
    """
    Fallback: odpowiedź backendu to fragment HTML z tabelą/listą meczów.
    Heurystyka: szukamy bloków zawierających datę, dwie nazwy drużyn i
    ewentualnie wynik w formacie "N:N" (dla pełnego meczu i dla 1. tercji).

    === WYMAGA KALIBRACJI === (selektory CSS do dopasowania po zobaczeniu realnego HTML-a)
    """
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select(".match-row, .fixture-row, tr, .event-row")
    out = []
    score_re = re.compile(r"^\d+\s*[:\-]\s*\d+$")

    for row in rows:
        text_parts = [t.strip() for t in row.stripped_strings]
        if len(text_parts) < 3:
            continue
        # heurystyka: pierwsza data-podobna wartość, dwie nazwy drużyn, opcjonalne wyniki
        date_candidates = [t for t in text_parts if re.match(r"\d{1,2}[./]\d{1,2}[./]\d{2,4}", t)]
        scores = [t for t in text_parts if score_re.match(t.replace("-", ":"))]
        names = [t for t in text_parts if t not in date_candidates and t not in scores and len(t) > 1]
        if not date_candidates or len(names) < 2:
            continue

        sort_date, display_date = normalize_date(date_candidates[0])
        home, away = names[0], names[1]
        played = len(scores) >= 1
        score_full = scores[0].replace("-", ":") if len(scores) >= 1 else None
        score_p1 = scores[1].replace("-", ":") if len(scores) >= 2 else None

        out.append({
            "home": clean_team_name(home),
            "away": clean_team_name(away),
            "date": display_date,
            "sortDate": sort_date,
            "played": played,
            "score": score_full if played else None,
            "p1": score_p1 if played else None,
        })

    if not out:
        raise RuntimeError("Parser HTML nie znalazł żadnych meczów — potrzebna kalibracja parse_matches_from_html().")
    return out


def clean_team_name(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip()


def normalize_score(raw):
    if not raw:
        return None
    raw = str(raw).strip().replace("-", ":")
    if re.match(r"^\d+\s*:\s*\d+$", raw):
        a, b = raw.split(":")
        return f"{int(a)}:{int(b)}"
    return None


def normalize_date(raw: str):
    """Zwraca (sortDate 'YYYY-MM-DD', display 'DD.MM.YYYY') z różnych formatów wejściowych."""
    raw = str(raw).strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%d.%m.%Y", "%d/%m/%Y", "%d.%m.%y"):
        try:
            dt = datetime.strptime(raw[:len(fmt) if "T" not in fmt else 19], fmt)
            return dt.strftime("%Y-%m-%d"), dt.strftime("%d.%m.%Y")
        except ValueError:
            continue
    # ostatnia deska ratunku: spróbuj wyciągnąć samymi cyframi
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", raw)
    if m:
        y, mo, d = m.groups()
        return f"{y}-{mo}-{d}", f"{d}.{mo}.{y}"
    raise ValueError(f"Nie rozpoznano formatu daty: {raw!r}")


# ----------------------------------------------------------------------------
# data.json — IO
# ----------------------------------------------------------------------------

def load_data_json(path):
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Nie znaleziono {path}. Scraper aktualizuje istniejący plik, "
            "nie tworzy go od zera — upewnij się, że data.json jest w repo."
        )
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_data_json(path, data):
    data["lastUpdated"] = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


# ----------------------------------------------------------------------------
# MODEL — statystyki drużyn i prognoza Over 1,5 P1
# ----------------------------------------------------------------------------

def compute_over15(match_row):
    if not match_row.get("p1"):
        return None
    h, a = match_row["p1"].split(":")
    return (int(h) + int(a)) >= 2


def current_weight(n: int) -> float:
    return CURRENT_WEIGHT_MAX * (1 - math.exp(-n / CURRENT_WEIGHT_TAU))


def aggregate_current_season_stats(played_matches):
    """
    Buduje per-drużynowe statystyki bieżącego sezonu (mecze rozegrane do tej pory)
    potrzebne do ważenia modelu: matches, overallPct, homePct, awayPct, recent5Pct.
    """
    per_team = {}

    def bucket(name):
        if name not in per_team:
            per_team[name] = {"matches": 0, "overs": 0,
                               "homeMatches": 0, "homeOvers": 0,
                               "awayMatches": 0, "awayOvers": 0,
                               "chrono": []}
        return per_team[name]

    for m in sorted(played_matches, key=lambda x: x["sortDate"]):
        over = m["over15"]
        h = bucket(m["home"]); a = bucket(m["away"])
        h["matches"] += 1; h["homeMatches"] += 1
        a["matches"] += 1; a["awayMatches"] += 1
        if over:
            h["overs"] += 1; h["homeOvers"] += 1
            a["overs"] += 1; a["awayOvers"] += 1
        h["chrono"].append(over)
        a["chrono"].append(over)

    out = {}
    for name, s in per_team.items():
        last5 = s["chrono"][-5:]
        out[name] = {
            "n": s["matches"],
            "overallPct": (s["overs"] / s["matches"] * 100) if s["matches"] else None,
            "homePct": (s["homeOvers"] / s["homeMatches"] * 100) if s["homeMatches"] else None,
            "awayPct": (s["awayOvers"] / s["awayMatches"] * 100) if s["awayMatches"] else None,
            "recent5Pct": (sum(last5) / len(last5) * 100) if last5 else None,
        }
    return out


def historical_blend_for_team(league_data, team_name, stat_key):
    """Ważona (HIST_DECAY) średnia danej statystyki z zakończonych sezonów, najnowszy = waga 1."""
    completed = sorted(
        [k for k, v in league_data["seasons"].items() if v.get("status") == "completed"],
        reverse=True,
    )
    weights, values = [], []
    for i, sk in enumerate(completed):
        season = league_data["seasons"][sk]
        team_row = next((t for t in season.get("teams", []) if t["name"] == team_name), None)
        if not team_row:
            continue
        if stat_key == "recent5Pct":
            trend = team_row.get("trend5") or []
            val = (trend.count("O") / len(trend) * 100) if trend else team_row.get("overallPct", 0)
        else:
            val = team_row.get(stat_key)
        if val is None:
            continue
        weights.append(HIST_DECAY ** i)
        values.append(val)
    if not weights:
        return None
    total_w = sum(weights)
    return sum(w * v for w, v in zip(weights, values)) / total_w


def blended_stat(current_val, n, hist_val):
    """current_weight(n) na bieżący sezon, reszta na historię (decay)."""
    if hist_val is None:
        return current_val
    if n == 0 or current_val is None:
        return hist_val
    cw = current_weight(n)
    return cw * current_val + (1 - cw) * hist_val


def build_team_blend(league_data, team_name, current_stats_for_team):
    cur = current_stats_for_team or {"n": 0, "overallPct": None, "homePct": None, "awayPct": None, "recent5Pct": None}
    n = cur["n"]
    blend = {}
    for key in ("overallPct", "homePct", "awayPct", "recent5Pct"):
        hist = historical_blend_for_team(league_data, team_name, key)
        blend[key] = blended_stat(cur[key], n, hist)
    blend["n"] = n
    blend["hasAnyData"] = any(blend[k] is not None for k in ("overallPct", "homePct", "awayPct", "recent5Pct"))
    return blend


def h2h_lookup(league_data, home, away):
    """Historia bezpośrednich starć z ukończonych sezonów: (pct_over, n)."""
    games = []
    for sk, season in league_data["seasons"].items():
        if season.get("status") != "completed":
            continue
        for m in season.get("matches", []):
            if {m["home"], m["away"]} == {home, away}:
                games.append(m)
    if len(games) < H2H_MIN_MATCHES:
        return None, len(games)
    overs = sum(1 for g in games if g.get("over15"))
    return (overs / len(games) * 100), len(games)


def predict_match(league_data, home, away, current_stats):
    home_blend = build_team_blend(league_data, home, current_stats.get(home))
    away_blend = build_team_blend(league_data, away, current_stats.get(away))

    if not home_blend["hasAnyData"] or not away_blend["hasAnyData"]:
        return None  # brak wystarczających danych -> "brak" w UI

    base_prob = 0.5 * (0.4 * home_blend["overallPct"] + 0.4 * home_blend["homePct"] + 0.2 * home_blend["recent5Pct"]) \
              + 0.5 * (0.4 * away_blend["overallPct"] + 0.4 * away_blend["awayPct"] + 0.2 * away_blend["recent5Pct"])

    h2h_pct, h2h_n = h2h_lookup(league_data, home, away)
    if h2h_pct is not None:
        prob = (1 - H2H_WEIGHT) * base_prob + H2H_WEIGHT * h2h_pct
    else:
        prob = base_prob

    both_fresh = (current_stats.get(home, {}).get("n", 0) == 0 and current_stats.get(away, {}).get("n", 0) == 0)

    return {
        "prob": round(max(0, min(100, prob)), 1),
        "homePct": round(home_blend["homePct"], 1) if home_blend["homePct"] is not None else None,
        "awayPct": round(away_blend["awayPct"], 1) if away_blend["awayPct"] is not None else None,
        "homeOverallPct": round(home_blend["overallPct"], 1) if home_blend["overallPct"] is not None else None,
        "awayOverallPct": round(away_blend["overallPct"], 1) if away_blend["overallPct"] is not None else None,
        "components": {
            "homeOverall": round(home_blend["overallPct"], 1) if home_blend["overallPct"] is not None else None,
            "homeRole": round(home_blend["homePct"], 1) if home_blend["homePct"] is not None else None,
            "awayOverall": round(away_blend["overallPct"], 1) if away_blend["overallPct"] is not None else None,
            "awayRole": round(away_blend["awayPct"], 1) if away_blend["awayPct"] is not None else None,
            "h2h": round(h2h_pct, 1) if h2h_pct is not None else None,
        },
        "modelBasis": ("Model startowy: brak jeszcze meczów bieżącego sezonu, "
                        "prognoza w 100% na bazie sezonów historycznych"
                        if both_fresh else
                        f"Model ważony: bieżący sezon ({current_stats.get(home, {}).get('n', 0)}/"
                        f"{current_stats.get(away, {}).get('n', 0)} meczów gospodarz/gość) + sezony historyczne"),
        "dataSource": "prev_season" if both_fresh else None,
    }


# ----------------------------------------------------------------------------
# MERGE: świeże wyniki -> data.json (tylko sezon 2026-27 danej ligi)
# ----------------------------------------------------------------------------

def merge_league_current_season(league_data, fresh_matches):
    """
    fresh_matches: lista surowych meczów z scrapera (patrz fetch_league_season_raw)
    dla CAŁEGO sezonu 2026/27 danej ligi (presezon + main season razem, jeśli
    dotyczy — podział zostaje taki, jaki już jest zapisany w data.json, wg
    progu daty NHL: 06.10.2026, dla pozostałych lig brak podziału).
    """
    season = league_data["seasons"].get(CURRENT_SEASON_KEY)
    if season is None:
        raise RuntimeError(f"Brak sezonu {CURRENT_SEASON_KEY} w istniejącym data.json dla tej ligi.")

    fresh_by_key = {(m["home"], m["away"], m["sortDate"]): m for m in fresh_matches}

    def update_pool(pool):
        if not pool:
            return
        for existing in pool:
            key = (existing["home"], existing["away"], existing["sortDate"])
            fresh = fresh_by_key.get(key)
            if not fresh or not fresh["played"]:
                continue
            over15 = compute_over15(fresh)
            if over15 is None:
                continue  # mecz oznaczony jako rozegrany, ale brak wyniku 1. tercji -> pomijamy na razie
            existing["played"] = True
            existing["score"] = fresh["score"]
            existing["p1"] = fresh["p1"]
            existing["over15"] = over15

    update_pool(season.get("preseason", {}).get("matches"))
    update_pool(season.get("mainSeason", {}).get("matches"))

    all_matches = (season.get("preseason", {}).get("matches") or []) + (season.get("mainSeason", {}).get("matches") or [])
    played = [m for m in all_matches if m.get("played")]

    current_stats = aggregate_current_season_stats(played)

    unplayed = [m for m in all_matches if not m.get("played")]
    for m in unplayed:
        pred = predict_match(league_data, m["home"], m["away"], current_stats)
        if pred is None:
            for k in ("prob", "homePct", "awayPct", "homeOverallPct", "awayOverallPct", "components", "modelBasis", "dataSource"):
                m.pop(k, None)
            continue
        m.update(pred)

    for pool_name in ("preseason", "mainSeason"):
        pool = season.get(pool_name)
        if pool is not None:
            pool["count"] = len(pool.get("matches", []))

    season["lastUpdated"] = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
    return len(played)


# ----------------------------------------------------------------------------
# WALIDACJA — nigdy nie zapisujemy data.json, jeśli coś "zniknęło"
# ----------------------------------------------------------------------------

class DataIntegrityError(RuntimeError):
    pass


def validate_data_integrity(original: dict, updated: dict):
    """
    Twarda kontrola przed zapisem: scraper może DODAWAĆ/AKTUALIZOWAĆ dane,
    ale nigdy nie może z data.json zniknąć liga, sezon, ani skurczyć się
    liczba meczów w już istniejącym sezonie/puli. Jeśli coś takiego wykryjemy,
    przerywamy z błędem i NIC nie zapisujemy — lepszy nieaktualny plik niż
    okrojony.
    """
    orig_leagues = set(original.get("leagues", {}).keys())
    upd_leagues = set(updated.get("leagues", {}).keys())
    missing_leagues = orig_leagues - upd_leagues
    if missing_leagues:
        raise DataIntegrityError(f"Zniknęłyby ligi z data.json: {sorted(missing_leagues)}")

    for lg in orig_leagues:
        orig_seasons = set(original["leagues"][lg].get("seasons", {}).keys())
        upd_seasons = set(updated["leagues"][lg].get("seasons", {}).keys())
        missing_seasons = orig_seasons - upd_seasons
        if missing_seasons:
            raise DataIntegrityError(f"[{lg}] zniknęłyby sezony: {sorted(missing_seasons)}")

        for sk in orig_seasons:
            orig_season = original["leagues"][lg]["seasons"][sk]
            upd_season = updated["leagues"][lg]["seasons"][sk]

            if orig_season.get("status") == "completed":
                orig_n = len(orig_season.get("matches", []))
                upd_n = len(upd_season.get("matches", []))
                if upd_n < orig_n:
                    raise DataIntegrityError(
                        f"[{lg}/{sk}] sezon zakończony skurczyłby się z {orig_n} do {upd_n} meczów"
                    )
            else:
                for pool_name in ("preseason", "mainSeason"):
                    orig_pool = orig_season.get(pool_name)
                    upd_pool = upd_season.get(pool_name)
                    if orig_pool is None:
                        continue
                    if upd_pool is None:
                        raise DataIntegrityError(f"[{lg}/{sk}] zniknęłaby pula '{pool_name}'")
                    orig_n = len(orig_pool.get("matches", []))
                    upd_n = len(upd_pool.get("matches", []))
                    if upd_n < orig_n:
                        raise DataIntegrityError(
                            f"[{lg}/{sk}/{pool_name}] terminarz skurczyłby się z {orig_n} do {upd_n} meczów"
                        )


# ----------------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------------

def process_league(session, data, league_key):
    print(f"[{league_key}] pobieram...", file=sys.stderr)
    raw = fetch_league_season_raw(session, league_key, CURRENT_SEASON_SLUG)
    print(f"[{league_key}] pobrano {len(raw)} meczów, merguję...", file=sys.stderr)
    league_data = data["leagues"].get(league_key)
    if league_data is None:
        raise RuntimeError(f"Ligi {league_key} nie ma jeszcze w data.json — dodaj ją ręcznie (teamMeta + sezony) przed pierwszym scrapem.")
    n_played = merge_league_current_season(league_data, raw)
    print(f"[{league_key}] OK — {n_played} rozegranych meczów w sezonie {CURRENT_SEASON_KEY}.", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", help="Przecinkiem oddzielone klucze lig, np. NHL,AHL", default=None)
    args = parser.parse_args()

    only = set(k.strip().upper() for k in args.only.split(",")) if args.only else None
    league_keys = [k for k in LEAGUES if (only is None or k in only)]

    data = load_data_json(DATA_JSON_PATH)
    original_snapshot = json.loads(json.dumps(data))  # głęboka kopia do walidacji "przed/po"
    session = requests.Session()

    failures = []
    for league_key in league_keys:
        try:
            process_league(session, data, league_key)
        except Exception as e:
            print(f"[{league_key}] BŁĄD: {e}", file=sys.stderr)
            failures.append(league_key)
        time.sleep(1.5)  # uprzejmy odstęp między requestami

    try:
        validate_data_integrity(original_snapshot, data)
    except DataIntegrityError as e:
        print(f"KONTROLA INTEGRALNOŚCI NIE PRZESZŁA — data.json NIE ZOSTAŁ ZAPISANY: {e}", file=sys.stderr)
        sys.exit(2)

    save_data_json(DATA_JSON_PATH, data)
    print(f"Zapisano {DATA_JSON_PATH}.", file=sys.stderr)

    if failures:
        print(f"Ligi z błędem (dane NIEZAKTUALIZOWANE): {', '.join(failures)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
