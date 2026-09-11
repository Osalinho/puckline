import json
import re
import datetime
import requests
from difflib import SequenceMatcher

DATA_FILE = "data.json"

# Daty do pobrania i uzupełnienia
TARGET_DATES = ["09.09", "10.09"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "x-fsign": "SW1hZ2luZSB3aXRob3V0IGEgZmF0ZWZ1bCBzaWduYXR1cmU=",
    "Referer": "https://www.flashscore.pl/hokej/"
}

def similar(a, b):
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, str(a).lower().strip(), str(b).lower().strip()).ratio()

def get_offset_for_date(date_str):
    """Oblicza ile dni wstecz od dzisiaj była dana data (np. '09.09')."""
    today = datetime.date.today()
    try:
        parts = date_str.strip().split(".")
        day = int(parts[0])
        month = int(parts[1])
        year = today.year
        
        target_dt = datetime.date(year, month, day)
        # Jeśli data wyszłaby w przyszłości o ponad miesiąc, korygujemy rok
        if target_dt > today + datetime.timedelta(days=30):
            target_dt = datetime.date(year - 1, month, day)
            
        return (target_dt - today).days
    except Exception as e:
        print(f"[!] Błąd wyliczania offsetu dla daty {date_str}: {e}")
        return None

def fetch_flashscore_by_offset(offset, date_str):
    url = f"https://local-global.flashscore.ninja/3/x/feed/r_3_{offset}"
    print(f"[FETCH] Pobieranie Flashscore dla daty {date_str} (offset: {offset})...")
    
    try:
        res = requests.get(url, headers=HEADERS, timeout=15)
        if res.status_code != 200 or not res.text:
            print(f"  [X] Błąd HTTP {res.status_code} lub pusta odpowiedź.")
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
            elif k == "PD": # Wynik 1. tercji
                scores = re.findall(r'\d+', v)
                if len(scores) >= 2:
                    current["p1_home"] = int(scores[0])
                    current["p1_away"] = int(scores[1])
                    
        if "home" in current and "p1_home" in current:
            matches.append(current)
            
        print(f"  [✓] Znaleziono {len(matches)} zakończonych meczów z wynikiem P1.")
        return matches
    except Exception as e:
        print(f"  [X] Błąd połączenia: {e}")
        return []

def run_update():
    # 1. Wczytanie pliku data.json
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"[CRITICAL] Błąd odczytu {DATA_FILE}: {e}")
        return

    # 2. Pobranie meczów z Flashscore dla wskazanych dat
    scraped_by_date = {}
    for d_str in TARGET_DATES:
        offset = get_offset_for_date(d_str)
        if offset is not None:
            scraped_by_date[d_str] = fetch_flashscore_by_offset(offset, d_str)

    updated_count = 0

    # 3. Przeszukiwanie i aktualizacja w data.json
    def process_match_list(match_list, section_name):
        nonlocal updated_count
        if not isinstance(match_list, list):
            return

        for item in match_list:
            if not isinstance(item, dict):
                continue

            item_date = str(item.get("date", item.get("data", item.get("dt", "")))).strip()
            
            # Sprawdzamy, czy mecz jest z daty 09.09 lub 10.09
            matched_date_key = None
            for d in TARGET_DATES:
                if d in item_date:
                    matched_date_key = d
                    break
            
            if not matched_date_key:
                continue

            # Pomijamy, jeśli wynik P1 już istnieje
            p1_h = item.get("p1_home", item.get("p1H", item.get("p1_gospodarz", None)))
            if p1_h is not None and str(p1_h).strip() != "":
                continue

            h_name = item.get("home", item.get("homeTeam", item.get("gospodarz", "")))
            a_name = item.get("away", item.get("awayTeam", item.get("gosc", "")))

            if not h_name or not a_name:
                continue

            # Szukamy dopasowania wśród wyników z Flashscore
            flash_matches = scraped_by_date.get(matched_date_key, [])
            for m in flash_matches:
                sim_h = similar(h_name, m["home"])
                sim_a = similar(a_name, m["away"])

                # Elastyczne dopasowywanie nazw drużyn
                if (sim_h > 0.50 or h_name.lower() in m["home"].lower() or m["home"].lower() in h_name.lower()) and \
                   (sim_a > 0.50 or a_name.lower() in m["away"].lower() or m["away"].lower() in a_name.lower()):
                    
                    item["p1_home"] = m["p1_home"]
                    item["p1_away"] = m["p1_away"]
                    item["over15_p1"] = (m["p1_home"] + m["p1_away"]) > 1
                    
                    print(f"[+ ZAKTUALIZOWANO] [{section_name}] {matched_date_key} | {h_name} vs {a_name} -> P1: {m['p1_home']}:{m['p1_away']}")
                    updated_count += 1
                    break

    # Przeszukanie wszystkich sekcji w JSON (Wyniki ALL, Terminarz, itp.)
    if isinstance(data, dict):
        for key, val in data.items():
            if isinstance(val, list):
                process_match_list(val, section_name=key)
    elif isinstance(data, list):
        process_match_list(data, section_name="ROOT_LIST")

    # 4. Zapis zmian
    if updated_count > 0:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"\n[SUKCES] Zaktualizowano pomyślnie {updated_count} meczów!")
    else:
        print("\n[INFO] Brak nowych meczów do aktualizacji dla dat 09.09 i 10.09.")

if __name__ == "__main__":
    run_update()
