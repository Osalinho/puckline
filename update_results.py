import json
import re
import datetime
import requests
from difflib import SequenceMatcher

DATA_FILE = "data.json"

# Nagłówki udające przeglądarkę
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7",
    "x-fsign": "SW1hZ2luZSB3aXRob3V0IGEgZmF0ZWZ1bCBzaWduYXR1cmU=",
    "Referer": "https://www.flashscore.pl/hokej/"
}

def similar(a, b):
    """Oblicza podobieństwo tekstu (0.0 - 1.0)"""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()

def fetch_flashscore_day(day_offset):
    """Pobiera mecze dla danego dnia z Flashscore"""
    url = f"https://local-global.flashscore.ninja/3/x/feed/r_3_{day_offset}"
    
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        print(f"[DEBUG] Zapytanie {url} -> Status: {response.status_code}")
        
        if response.status_code != 200:
            print(f"[!] Błąd HTTP {response.status_code} z Flashscore.")
            return []
            
        raw_text = response.text
        if not raw_text or len(raw_text) < 100:
            print(f"[!] Odpowiedź z Flashscore jest pusta lub zablokowana (długość: {len(raw_text)}).")
            return []
            
        return parse_flashscore_feed(raw_text)
    except Exception as e:
        print(f"[!] Wyjątek podczas pobierania danych: {e}")
        return []

def parse_flashscore_feed(raw_data):
    matches = []
    blocks = raw_data.split("~")
    current = {}

    for block in blocks:
        if not block.strip():
            continue
        parts = block.split("÷")
        if len(parts) < 2:
            continue
            
        key, val = parts[0], parts[1]

        if key == "AA":  # ID meczu
            if "home" in current and "p1_home" in current:
                matches.append(current)
            current = {"id": val}
        elif key == "AO":  # Gospodarz
            current["home"] = val
        elif key == "AP":  # Gość
            current["away"] = val
        elif key == "PD":  # Wynik 1. tercji (np. 1 - 0)
            scores = re.findall(r'\d+', val)
            if len(scores) >= 2:
                current["p1_home"] = int(scores[0])
                current["p1_away"] = int(scores[1])

    if "home" in current and "p1_home" in current:
        matches.append(current)

    return matches

def update_data():
    # 1. Wczytanie data.json
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"[CRITICAL] Nie można wczytać pliku {DATA_FILE}: {e}")
        return

    # Wykrycie właściwego klucza w data.json dla Wyników ALL
    target_key = None
    possible_keys = ["wyniki_all", "wynikiAll", "results", "matches", "PLAYNOW"]
    
    if isinstance(data, dict):
        for k in possible_keys:
            if k in data and isinstance(data[k], list):
                target_key = k
                break
        if not target_key:
            # Jeśli brak pasującego klucza, szukamy pierwszej listy w obiekcie JSON
            for k, v in data.items():
                if isinstance(v, list) and len(v) > 0:
                    target_key = k
                    break
        target_list = data.get(target_key, [])
    elif isinstance(data, list):
        target_list = data
    else:
        print("[CRITICAL] Nieznana struktura data.json")
        return

    print(f"[INFO] Znaleziono sekcję danych: '{target_key}' z {len(target_list)} wpisami.")

    # 2. Pobranie danych z Flashscore (Wczoraj: -1, Dzisiaj: 0)
    scraped = fetch_flashscore_day(-1) + fetch_flashscore_day(0)
    print(f"[INFO] Pobrano łącznie {len(scraped)} meczów z P1 z Flashscore.")

    if not scraped:
        print("[!] Brak pobranych meczów z Flashscore. Prawdopodobnie blokada IP lub brak rozegranych spotkań.")
        return

    # 3. Porównanie i aktualizacja
    updated = 0
    for item in target_list:
        # Szukamy tylko spotkań nieuzupełnionych
        h_name = item.get("home", item.get("homeTeam", item.get("gospodarz", "")))
        a_name = item.get("away", item.get("awayTeam", item.get("gosc", "")))

        if not h_name or not a_name:
            continue

        # Weryfikacja czy wynik P1 jest pusty
        p1_h = item.get("p1_home", item.get("p1H", None))
        if p1_h is not None and p1_h != "":
            continue # Mecz już ma wynik

        for m in scraped:
            # Wykorzystanie fuzzy matching (dopasowanie na poziomie min. 65%)
            sim_home = similar(h_name, m["home"])
            sim_away = similar(a_name, m["away"])

            if (sim_home > 0.65 or h_name.lower() in m["home"].lower()) and \
               (sim_away > 0.65 or a_name.lower() in m["away"].lower()):
                
                item["p1_home"] = m["p1_home"]
                item["p1_away"] = m["p1_away"]
                item["over15_p1"] = (m["p1_home"] + m["p1_away"]) > 1
                
                print(f"[+] ZAKTUALIZOWANO: {h_name} vs {a_name} -> P1: {m['p1_home']}:{m['p1_away']}")
                updated += 1
                break

    # 4. Zapis
    if updated > 0:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"[SUCCESS] Zaktualizowano {updated} meczów w {DATA_FILE}.")
    else:
        print("[INFO] Nie dopasowano żadnych nowych wyników do istniejących rekordów.")

if __name__ == "__main__":
    update_data()
