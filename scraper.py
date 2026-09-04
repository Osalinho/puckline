"""
Scraper + budowa danych dla PUCKLINE (12 lig hokejowych).
Pobiera terminarze z 24score.com dla wszystkich lig, liczy statystyki over 1,5
gola w 1. tercji i model prognozy, zapisuje jeden wspolny data.json.

Zoptymalizowany: Stare completed sezony wczytuje z cache (data.json),
a pobiera z sieci TYLKO mecze bieżącego sezonu.
"""
import re, json, math, time, hashlib
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,pl;q=0.8",
    "Referer": "https://en.24score.com/",
}

FIXTURES_URL = "https://en.24score.com/ice_hockey/{path}/{season}/regular_season/fixtures/"

HIST_DECAY = 0.25
H2H_WEIGHT = 0.12
H2H_MIN_MATCHES = 2
CURRENT_SEASON_RAMP = 25

NHL_TEAM_META = {
    "Anaheim Ducks":("ANA","#F47A38"),"Boston Bruins":("BOS","#FFB81C"),
    "Buffalo Sabres":("BUF","#003087"),"Calgary Flames":("CGY","#C8102E"),
    "Carolina Hurricanes":("CAR","#CC0000"),"Chicago Blackhawks":("CHI","#CF0A2C"),
    "Colorado Avalanche":("COL","#6F263D"),"Columbus Blue Jackets":("CBJ","#002654"),
    "Dallas Stars":("DAL","#006847"),"Detroit Red Wings":("DET","#CE1126"),
    "Edmonton Oilers":("EDM","#FF4C00"),"Florida Panthers":("FLA","#C8102E"),
    "Los Angeles Kings":("LAK","#A2AAAD"),"Minnesota Wild":("MIN","#A6192E"),
    "Montreal Canadiens":("MTL","#AF1E2D"),"Nashville Predators":("NSH","#FFB81C"),
    "New Jersey Devils":("NJD","#CE1126"),"New York Islanders":("NYI","#00539B"),
    "New York Rangers":("NYR","#0038A8"),"Ottawa Senators":("OTT","#C52032"),
    "Philadelphia Flyers":("PHI","#F74902"),"Pittsburgh Penguins":("PIT","#FCB514"),
    "San Jose Sharks":("SJS","#006D75"),"Seattle Kraken":("SEA","#99D9D9"),
    "St. Louis Blues":("STL","#002F87"),"Tampa Bay Lightning":("TBL","#002868"),
    "Toronto Maple Leafs":("TOR","#00205B"),"Utah Mammoth":("UTA","#71AFE5"),
    "Vancouver Canucks":("VAN","#00205B"),"Vegas Golden Knights":("VGK","#B4975A"),
    "Washington Capitals":("WSH","#C8102E"),"Winnipeg Jets":("WPG","#041E42"),
}

def color_for(name):
    h = int(hashlib.md5(name.encode()).hexdigest(), 16)
    return f"hsl({h % 360}, 55%, 45%)"

def abbr_for(name):
    words = [w for w in re.split(r"\s+", name) if w]
    return words[0][:3].upper() if len(words) == 1 else "".join(w[0] for w in words[:3]).upper()

def season3(y1):
    return {"id": f"{y1}-{str(y1+1)[2:]}", "label": f"{y1}/{str(y1+1)[2:]}", "url": f"{y1}-{y1+1}"}

LEAGUES = {
    "NHL": {"path": "usa/nhl", "aliases": {}, "team_meta": NHL_TEAM_META,
            "completed": [season3(2024), season3(2025)], "upcoming": season3(2026),
            "preseason_cutoff": "2026-10-06"},
    "AHL": {"path": "usa/ahl", "aliases": {"Bridgeport": "Hamilton AHL"}, "team_meta": None,
            "completed": [season3(2024), season3(2025)], "upcoming": season3(2026),
            "preseason_cutoff": None},
    "CZECH": {"path": "czech_republic/extraliga", "aliases": {}, "team_meta": None,
              "completed": [season3(2023), season3(2024), season3(2025)], "upcoming": season3(2026),
              "preseason_cutoff": None},
    "DENMARK": {"path": "denmark/al-bank_ligaen", "aliases": {}, "team_meta": None,
                "completed": [season3(2023), season3(2024), season3(2025)], "upcoming": season3(2026),
                "preseason_cutoff": None},
    "FRANCE": {"path": "france/ligue_magnus", "aliases": {}, "team_meta": None,
               "completed": [season3(2023), season3(2024), season3(2025)], "upcoming": season3(2026),
               "preseason_cutoff": None},
    "FINLAND": {"path": "finland/sm-liiga", "aliases": {}, "team_meta": None,
                "completed": [season3(2023), season3(2024), season3(2025)], "upcoming": season3(2026),
                "preseason_cutoff": None},
    "CANADA_OHL": {"path": "canada_/ohl", "aliases": {}, "team_meta": None,
                   "completed": [season3(2023), season3(2024), season3(2025)], "upcoming": season3(2026),
                   "preseason_cutoff": None},
    "GERMANY": {"path": "germany/del", "aliases": {}, "team_meta": None,
                "completed": [season3(2023), season3(2024), season3(2025)], "upcoming": season3(2026),
                "preseason_cutoff": None},
    "NORWAY": {"path": "norway/ehl", "aliases": {}, "team_meta": None,
               "completed": [season3(2023), season3(2024), season3(2025)], "upcoming": season3(2026),
               "preseason_cutoff": None},
    "SLOVAKIA": {"path": "slovakia/st_extraliga", "aliases": {}, "team_meta": None,
                 "completed": [season3(2023), season3(2024), season3(2025)], "upcoming": season3(2026),
                 "preseason_cutoff": None},
    "SWITZERLAND": {"path": "switzerland/nla", "aliases": {}, "team_meta": None,
                    "completed": [season3(2023), season3(2024), season3(2025)], "upcoming": season3(2026),
                    "preseason_cutoff": None},
    "SWEDEN": {"path": "sweden/allsvenskan", "aliases": {}, "team_meta": None,
               "completed": [season3(2023), season3(2024), season3(2025)], "upcoming": season3(2026),
               "preseason_cutoff": None},
}

def fetch_html_session(session, url, params=None, referer=None, retries=3):
    last_err = None
    extra_headers = {"Referer": referer} if referer else {}
    for attempt in range(retries):
        try:
            r = session.get(url, params=params, headers=extra_headers, timeout=30)
            print(f"[fetch] {r.url} -> status {r.status_code}, {len(r.text)} znakow")
            r.raise_for_status()
            return r.text
        except Exception as e:
            last_err = e
            print(f"[fetch] proba {attempt+1} nieudana: {e}")
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Nie udalo sie pobrac {url}: {last_err}")

def norm_team(name, aliases):
    return aliases.get(name, name)

def date_key(d):
    dd, mm, yyyy = d.split(".")
    return f"{yyyy}-{mm}-{dd}"

def parse_fixtures_html(html, aliases):
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    if not tables:
        print("=== BRAK TABELI - fragment (pierwsze 1500 znakow) ===")
        print(html[:1500])
        raise RuntimeError("Nie znaleziono tabeli z meczami.")
    rows = tables[0].find_all("tr")
    matches = []
    current_date = None
    for r in rows:
        cells = [c.get_text(strip=True) for c in r.find_all(["td", "th"])]
        if len(cells) < 4:
            continue
        date_cell, teams_cell, score_cell, periods_cell = cells[0], cells[1], cells[2], cells[3]
        if date_cell:
            current_date = date_cell
        
        # Odporność na różne rodzaje myślników (-, –, —)
        if not re.search(r'[\-–—]', teams_cell) or not current_date:
            continue
        
        teams_split = re.split(r'\s*[\-–—]\s*', teams_cell, 1)
        if len(teams_split) < 2:
            continue

        home = norm_team(teams_split[0].strip(), aliases)
        away = norm_team(teams_split[1].strip(), aliases)
        score_match = re.match(r"(\d+):(\d+)", score_cell)
        periods = re.findall(r"(\d+):(\d+)", periods_cell)
        m = {"date": current_date, "sortDate": date_key(current_date), "home": home, "away": away, "played": False}
        if score_match and len(periods) >= 3:
            m["played"] = True
            m["home_score"] = int(score_match.group(1))
            m["away_score"] = int(score_match.group(2))
            m["p1_home"], m["p1_away"] = int(periods[0][0]), int(periods[0][1])
            m["p2_home"], m["p2_away"] = int(periods[1][0]), int(periods[1][1])
            m["p3_home"], m["p3_away"] = int(periods[2][0]), int(periods[2][1])
            m["over15"] = (m["p1_home"] + m["p1_away"]) >= 2
        matches.append(m)
    return matches

def fetch_season_matches(path, url_season, aliases):
    url = FIXTURES_URL.format(path=path, season=url_season)
    session = requests.Session()
    session.headers.update(HEADERS)
    html = fetch_html_session(session, url)

    key_match = re.search(r'data_key["\']?\s*:\s*["\']([A-Za-z0-9]+)["\']', html)
    if not key_match:
        print("=== BRAK data_key - fragment (pierwsze 1500 znakow) ===")
        print(html[:1500])
        raise RuntimeError("Nie znaleziono klucza data_key.")
    data_key = key_match.group(1)
    print(f"[fetch] data_key: {data_key}")

    backend_url = "https://en.24score.com/backend/load_page_data.php"
    frag = fetch_html_session(session, backend_url, params={"data_key": data_key}, referer=url)
    return parse_fixtures_html(frag, aliases)

def build_completed_season(season_cfg, path, aliases):
    played = [m for m in fetch_season_matches(path, season_cfg["url"], aliases) if m["played"]]
    played.sort(key=lambda m: m["sortDate"])
    teams = sorted(set(m["home"] for m in played) | set(m["away"] for m in played))

    def team_games(team):
        return [m for m in played if m["home"] == team or m["away"] == team]

    team_stats = {}
    for t in teams:
        games = team_games(t)
        n = len(games)
        overs = sum(1 for m in games if m["over15"])
        home_games = [m for m in games if m["home"] == t]
        away_games = [m for m in games if m["away"] == t]
        home_overs = sum(1 for m in home_games if m["over15"])
        away_overs = sum(1 for m in away_games if m["over15"])
        last5 = games[-5:]
        recent5_overs = sum(1 for m in last5 if m["over15"])
        recent5_pct = round(recent5_overs / len(last5) * 100, 1) if last5 else 0
        last_val = games[-1]["over15"] if games else False
        streak = 0
        for m in reversed(games):
            if m["over15"] == last_val:
                streak += 1
            else:
                break
        streak_str = f"{'O' if last_val else 'U'}{streak}"
        overall_pct = round(overs / n * 100, 1) if n else 0
        home_pct = round(home_overs / len(home_games) * 100, 1) if home_games else 0
        away_pct = round(away_overs / len(away_games) * 100, 1) if away_games else 0
        streak_bonus = min(streak, 5) * 1.6 * (1 if last_val else -1)
        form_score = round(max(0, min(100, 0.5*overall_pct + 0.3*recent5_pct + 0.2*(50+streak_bonus))), 1)
        trend5 = ["O" if m["over15"] else "U" for m in last5]
        team_stats[t] = {
            "name": t, "matches": n, "overs": overs, "overallPct": overall_pct,
            "homeMatches": len(home_games), "homeOvers": home_overs, "homePct": home_pct,
            "awayMatches": len(away_games), "awayOvers": away_overs, "awayPct": away_pct,
            "recent5Pct": recent5_pct, "trend5": trend5, "streak": streak_str, "formScore": form_score,
        }

    def h2h(a, b):
        games = [m for m in played if {m["home"], m["away"]} == {a, b}]
        n = len(games)
        overs = sum(1 for m in games if m["over15"])
        pct = round(overs/n*100, 1) if n else None
        return {"matches": n, "overs": overs, "pct": pct}

    match_list = []
    for m in sorted(played, key=lambda x: x["sortDate"], reverse=True):
        ht, at = team_stats[m["home"]], team_stats[m["away"]]
        home_score_calc = 0.4*ht["overallPct"] + 0.4*ht["homePct"] + 0.2*ht["recent5Pct"]
        away_score_calc = 0.4*at["overallPct"] + 0.4*at["awayPct"] + 0.2*at["recent5Pct"]
        base_prob = (home_score_calc + away_score_calc) / 2
        hh = h2h(m["home"], m["away"])
        prob = round(0.75*base_prob + 0.25*hh["pct"], 1) if hh["matches"] >= 3 else round(base_prob, 1)
        prob = max(0, min(100, prob))
        match_list.append({
            "date": m["date"], "sortDate": m["sortDate"], "home": m["home"], "away": m["away"],
            "score": f"{m['home_score']}:{m['away_score']}", "p1": f"{m['p1_home']}:{m['p1_away']}",
            "over15": m["over15"], "played": True, "homePct": ht["homePct"], "awayPct": at["awayPct"],
            "homeOverallPct": ht["overallPct"], "awayOverallPct": at["overallPct"],
            "prob": prob, "h2hMatches": hh["matches"], "h2hPct": hh["pct"],
            "components": {"homeOverall": ht["overallPct"], "homeRole": ht["homePct"],
                           "awayOverall": at["overallPct"], "awayRole": at["awayPct"],
                           "h2h": hh["pct"] if hh["matches"] >= 3 else None},
            "modelBasis": "sezon biezacy",
        })

    return {"id": season_cfg["id"], "label": season_cfg["label"], "status": "completed",
            "matchCount": len(played), "teams": list(team_stats.values()), "matches": match_list}

def historical_weights(completed_desc, current_w):
    L = len(completed_desc)
    if L == 0:
        return []
    raw = [HIST_DECAY ** i for i in range(L)]
    total_raw = sum(raw)
    hist_total = 1 - current_w
    return [hist_total * r / total_raw for r in raw]

def current_season_weight(n_played):
    if n_played <= 0:
        return 0.0
    return 0.9 * (1 - math.exp(-n_played / CURRENT_SEASON_RAMP))

def blended_team_stats(team, completed_desc, current_games=None):
    current_games = current_games or []
    n_current = len(current_games)
    cw = current_season_weight(n_current)
    hist_ws = historical_weights(completed_desc, cw)
    parts = []
    if n_current > 0:
        overs = sum(1 for m in current_games if m["over15"])
        home_g = [m for m in current_games if m["home"] == team]
        away_g = [m for m in current_games if m["away"] == team]
        home_overs = sum(1 for m in home_g if m["over15"])
        away_overs = sum(1 for m in away_g if m["over15"])
        parts.append((cw, overs/n_current*100,
                      home_overs/len(home_g)*100 if home_g else overs/n_current*100,
                      away_overs/len(away_g)*100 if away_g else overs/n_current*100, "biezacy sezon"))
    for w, season in zip(hist_ws, completed_desc):
        idx = {t["name"]: t for t in season["teams"]}
        if team not in idx:
            continue
        t = idx[team]
        parts.append((w, t["overallPct"], t["homePct"], t["awayPct"], season["label"]))
    if not parts:
        return None
    total_w = sum(p[0] for p in parts)
    def wavg(pos):
        return round(sum(p[0]*p[pos] for p in parts)/total_w, 1)
    basis = [{"label": p[4], "weight": round(p[0]/total_w*100, 1)} for p in parts]
    return {"overallPct": wavg(1), "homePct": wavg(2), "awayPct": wavg(3), "basis": basis}

def blended_h2h(a, b, completed_desc, current_games=None):
    current_games = current_games or []
    all_games = [m for m in current_games if {m["home"], m["away"]} == {a, b}]
    for season in completed_desc:
        all_games += [m for m in season["matches"] if {m["home"], m["away"]} == {a, b}]
    n = len(all_games)
    if n == 0:
        return {"matches": 0, "pct": None}
    overs = sum(1 for g in all_games if g["over15"])
    return {"matches": n, "pct": round(overs/n*100, 1)}

def format_basis(basis_home, basis_away):
    merged = {}
    for b in basis_home + basis_away:
        merged[b["label"]] = merged.get(b["label"], 0) + b["weight"]/2
    parts = sorted(merged.items(), key=lambda x: -x[1])
    return " + ".join(f"{label} ({w:.0f}%)" for label, w in parts)

def build_upcoming_season(season_cfg, completed_desc, path, aliases, preseason_cutoff):
    matches = fetch_season_matches(path, season_cfg["url"], aliases)
    matches.sort(key=lambda m: m["sortDate"])
    if preseason_cutoff:
        preseason = [m for m in matches if m["sortDate"] < preseason_cutoff]
        main = [m for m in matches if m["sortDate"] >= preseason_cutoff]
    else:
        preseason, main = [], matches
    current_played = [m for m in main if m["played"]]

    def games_for(team):
        return [m for m in current_played if m["home"] == team or m["away"] == team]

    def slim(m):
        home_ref = blended_team_stats(m["home"], completed_desc, games_for(m["home"]))
        away_ref = blended_team_stats(m["away"], completed_desc, games_for(m["away"]))
        out = {"date": m["date"], "sortDate": m["sortDate"], "home": m["home"], "away": m["away"],
               "played": m["played"], "dataSource": "blended"}
        if m["played"]:
            out.update({"score": f"{m['home_score']}:{m['away_score']}", "p1": f"{m['p1_home']}:{m['p1_away']}",
                        "over15": m["over15"]})
        if home_ref and away_ref:
            home_score = 0.5 * home_ref["overallPct"] + 0.5 * home_ref["homePct"]
            away_score = 0.5 * away_ref["overallPct"] + 0.5 * away_ref["awayPct"]
            stats_prob = (home_score + away_score) / 2
            h2h = blended_h2h(m["home"], m["away"], completed_desc, current_played)
            if h2h["matches"] >= H2H_MIN_MATCHES:
                prob = (1 - H2H_WEIGHT) * stats_prob + H2H_WEIGHT * h2h["pct"]
            else:
                prob = stats_prob
            prob = round(max(0, min(100, prob)), 1)
            out.update({
                "homeOverallPct": home_ref["overallPct"], "homePct": home_ref["homePct"],
                "awayOverallPct": away_ref["overallPct"], "awayPct": away_ref["awayPct"],
                "prob": prob, "h2hMatches": h2h["matches"], "h2hPct": h2h["pct"],
                "components": {"homeOverall": home_ref["overallPct"], "homeRole": home_ref["homePct"],
                               "awayOverall": away_ref["overallPct"], "awayRole": away_ref["awayPct"],
                               "h2h": h2h["pct"] if h2h["matches"] >= H2H_MIN_MATCHES else None},
                "modelBasis": format_basis(home_ref["basis"], away_ref["basis"]),
            })
        return out

    return {"id": season_cfg["id"], "label": season_cfg["label"], "status": "upcoming",
            "preseason": {"matches": [slim(m) for m in preseason], "count": len(preseason)},
            "mainSeason": {"matches": [slim(m) for m in main], "count": len(main)}}

def build_league(league_key, cfg, existing_league_data=None):
    print(f"\n===== Liga: {league_key} =====")
    completed_seasons = []
    
    for sc in cfg["completed"]:
        # Cache Check: Użycie zapisanych starych sezonów z data.json zamiast ciągłego pobierania
        if (existing_league_data and 
            "seasons" in existing_league_data and 
            sc["id"] in existing_league_data["seasons"]):
            print(f"[CACHE] Używam zapisanych danych dla zakończonego sezonu {sc['id']}")
            completed_seasons.append(existing_league_data["seasons"][sc["id"]])
        else:
            completed_seasons.append(build_completed_season(sc, cfg["path"], cfg["aliases"]))

    completed_desc = list(reversed(completed_seasons))
    seasons_out = {sc["id"]: cs for sc, cs in zip(cfg["completed"], completed_seasons)}
    seasons_out[cfg["upcoming"]["id"]] = build_upcoming_season(
        cfg["upcoming"], completed_desc, cfg["path"], cfg["aliases"], cfg["preseason_cutoff"]
    )

    all_team_names = set()
    for s in seasons_out.values():
        if s["status"] == "completed":
            all_team_names.update(t["name"] for t in s["teams"])
        else:
            for m in s["mainSeason"]["matches"] + s["preseason"]["matches"]:
                all_team_names.add(m["home"])
                all_team_names.add(m["away"])

    team_meta = {}
    for name in all_team_names:
        if cfg["team_meta"] and name in cfg["team_meta"]:
            abbr, color = cfg["team_meta"][name]
        else:
            abbr, color = abbr_for(name), color_for(name)
        team_meta[name] = {"abbr": abbr, "color": color}

    return {"teamMeta": team_meta, "seasons": seasons_out}

def main():
    # Odczyt dotychczasowych danych z data.json dla użycia cache
    try:
        with open("data.json", encoding="utf-8") as f:
            existing = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        existing = {"leagues": {}}

    existing_leagues = existing.get("leagues", {})
    leagues_out = {}
    failed = []

    for league_key, cfg in LEAGUES.items():
        try:
            prev_data = existing_leagues.get(league_key)
            leagues_out[league_key] = build_league(league_key, cfg, existing_league_data=prev_data)
        except Exception as e:
            print(f"!!! BLAD przy lidze {league_key}: {e}")
            failed.append(league_key)

    now = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
    for league in leagues_out.values():
        for s in league["seasons"].values():
            s["lastUpdated"] = now

    existing.setdefault("leagues", {})
    existing["leagues"].update(leagues_out)
    existing["modelConfig"] = {"h2hWeight": H2H_WEIGHT, "h2hMinMatches": H2H_MIN_MATCHES}
    existing["lastUpdated"] = now

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False)

    print("\n===== Podsumowanie =====")
    for lg, league in leagues_out.items():
        for sid, s in league["seasons"].items():
            if s["status"] == "completed":
                print(f"{lg} {sid}: {len(s['teams'])} druzyn, {len(s['matches'])} meczow")
            else:
                print(f"{lg} {sid}: presezon {s['preseason']['count']}, main {s['mainSeason']['count']}")
    if failed:
        print(f"\n!!! Ligi ktore sie NIE zaktualizowaly (blad): {failed}")
        print("Pozostale dane (dla tych lig) zostaly bez zmian w data.json.")
    print("Zapisano data.json,", now)
    if failed:
        raise SystemExit(1)

if __name__ == "__main__":
    main()
