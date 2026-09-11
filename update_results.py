import json
import re
import datetime
import unicodedata
import requests

DATA_FILE = "data.json"

# Słownik mapowania nazw (Flashscore / Skróty -> Nazwa w Twoim data.json)
TEAM_ALIASES = {
    "hifk": "ifk helsinki",
    "ifk helsinki": "ifk helsinki",
    "ässät": "assat",
    "assat": "assat",
    "herlev eagles": "herlev",
    "herlev": "herlev",
    "esbjerg energy": "esbjerg",
    "esbjerg": "esbjerg",
    "kiekko-espoo": "kiekko-espoo",
    "pelicans": "pelicans",
    "kalpa": "kalpa",
    "jukurit": "jukurit",
    "kookoo": "kookoo",
    "lukko": "lukko",
    "jyp": "jyp",
    "jokerit": "jokerit"
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "x-fsign": "SW1hZ2luZSB3aXRob3V0IGEgZmF0ZWZ1bCBzaWduYXR1cmU=",
    "Referer": "https://www.flashscore.pl/hokej/"
}

def clean_text(text):
    """Usuwa polskie/fińskie znaki diakrytyczne i sprowadza do małych liter."""
    if not text:
        return ""
    text = unicodedata.normalize('NFKD', str(text)).encode('ASCII', 'ignore').decode('utf-8')
    return text.lower().strip()

def get_canonical_name(name):
    cleaned = clean_text(name)
    return TEAM_ALIASES.get(cleaned, cleaned)

def fetch_flashscore_by_offset(offset):
    url = f"https://local-global.flashscore.ninja/3/x/feed/r_3_{offset}"
    try:
        res = requests.get(url, headers=HEADERS, timeout=12)
        if res.status_code != 200 or not res.text:
            return []
        
        matches = []
        blocks = res.text.split("~")
        current = {}
        
        for block in blocks:
            if not block.strip(): 
                continue
            parts = block.split("÷")
            if len(parts) < 2: 
                continue
            
            k, v = parts[0], parts[1]
            if k == "AA":
                if "home" in current and "p1_home" in current:
                    matches.append(current)
                current = {"id": v}
            elif k == "AO": 
                current["home"] = v
            elif k == "AP": 
                current["away"] = v
            elif k == "PD":
                scores = re.findall(r'\d+', v)
                if len(scores) >= 2:
                    current["p1_home"] = int(scores[0])
                    current["p1_away"] = int(scores[1])
                    
        if "home" in current and "p1_home" in current:
            matches.append(current)
            
        return matches
    except Exception:
        return []

def run_update():
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"[CRITICAL] Błąd otwarcia pliku {DATA_FILE}: {e}")
        return

    # Pobieramy mecze z ostatnich 5 dni z Flashscore
    all_scraped = []
    for offset in range(-5, 1):
        all_scraped.extend(fetch_flashscore_by_offset(offset))

    print(f"[INFO] Pobrano {len(all_scraped)} meczów z ostatnich dni z Flashscore.")

    updated_count = 0

    def update_match_array(match_list, section_name):
        nonlocal updated_count
        if not isinstance(match_list, list):
            return

        for item in match_list:
            if not isinstance(item, dict):
                continue

            # Sprawdzamy czy wynik P1 jest już wpisany
            p1_h = item.get("p1_home", item.get("p1H", None))
            if p1_h is not None and str(p1_h).strip() != "" and str(p1_h) != "0":
                continue

            h_raw = item.get("home", item.get("homeTeam", item.get("gospodarz", "")))
            a_raw = item.get("away", item.get("awayTeam", item.get("gosc", "")))

            if not h_raw or not a_raw:
                continue

            h_canon = get_canonical_name(h_raw)
            a_canon = get_canonical_name(a_raw)

            # Szukamy dopasowania w danych z Flashscore
            matched = False
            for m in all_scraped:
                m_h_canon = get_canonical_name(m["home"])
                m_a_canon = get_canonical_name(m["away"])

                if (h_canon in m_h_canon or m_h_canon in h_canon) and \
                   (a_canon in m_a_canon or m_a_canon in a_canon):
                    
                    item["p1_home"] = m["p1_home"]
                    item["p1_away"] = m["p1_away"]
                    item["over15_p1"] = (m["p1_home"] + m["p1_away"]) > 1
                    
                    print(f"[+ ZAKTUALIZOWANO] [{section_name}] {h_raw} vs {a_raw} -> P1: {m['p1_home']}:{m['p1_away']}")
                    updated_count += 1
                    matched = True
                    break

    # Aktualizacja wszystkich sekcji w pliku JSON
    if isinstance(data, dict):
        for key, val in data.items():
            if isinstance(val, list):
                update_match_array(val, section_name=key)
    elif isinstance(data, list):
        update_match_array(data, section_name="ROOT")

    # Zapis zmienionych danych
    if updated_count > 0:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"\n[SUKCES] Zaktualizowano {updated_count} spotkań w {DATA_FILE}.")
    else:
        print("\n[INFO] Brak nowych wyników do zaktualizowania.")

if __name__ == "__main__":
    run_update()
