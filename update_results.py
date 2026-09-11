import json
import re
import datetime
import requests

DATA_FILE = "data.json"

# Nagłówki wymagane przez Flashscore do autoryzacji zapytań API
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "x-fsign": "SW1hZ2luZSB3aXRob3V0IGEgZmF0ZWZ1bCBzaWduYXR1cmU=",
    "Referer": "https://www.flashscore.pl/hokej/"
}

def fetch_flashscore_hockey_results(day_offset=-1):
    """
    Pobiera wyniki meczów hokejowych z danego dnia z feedu Flashscore.
    day_offset: -1 dla wczoraj, 0 dla dzisiaj.
    """
    url = f"https://local-global.flashscore.ninja/3/x/feed/r_3_{day_offset}"
    
    try:
        response = requests.get(url, headers=HEADERS, timeout=10)
        if response.status_status_code != 200:
            print(f"[!] Błąd pobierania feedu Flashscore (status: {response.status_code})")
            return []
        
        raw_data = response.text
        return parse_flashscore_raw_data(raw_data)
    except Exception as e:
        print(f"[!] Błąd podczas połączenia z Flashscore: {e}")
        return []

def parse_flashscore_raw_data(raw_data):
    """
    Parsuje specyficzny format tekstowy klucz-wartość zwracany przez Flashscore.
    """
    matches = []
    blocks = raw_data.split("~")
    
    current_match = {}
    
    for block in blocks:
        if not block.strip():
            continue
            
        parts = block.split("÷")
        if len(parts) < 2:
            continue
            
        key, value = parts[0], parts[1]
        
        # AA = ID meczu (nowy blok meczu)
        if key == "AA":
            if current_match and "home" in current_match and "p1_home" in current_match:
                matches.append(current_match)
            current_match = {"id": value}
            
        elif key == "AO":  # Gospodarz
            current_match["home"] = value
        elif key == "AP":  # Gość
            current_match["away"] = value
        elif key == "PD":  # Wynik 1. tercji (np. "1 - 1" lub "2:0")
            scores = re.findall(r'\d+', value)
            if len(scores) >= 2:
                current_match["p1_home"] = int(scores[0])
                current_match["p1_away"] = int(scores[1])
        elif key == "AG":  # Końcowy wynik gospodarza
            current_match["full_home"] = value
        elif key == "AH":  # Końcowy wynik gościa
            current_match["full_away"] = value
        elif key == "AD":  # Data meczu w timestampie
            try:
                dt = datetime.datetime.fromtimestamp(int(value))
                current_match["date"] = dt.strftime("%Y-%m-%d")
            except:
                pass

    if current_match and "home" in current_match and "p1_home" in current_match:
        matches.append(current_match)
        
    return matches

def update_data_json():
    # 1. Wczytaj istniejące dane
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"[!] Nie znaleziono pliku {DATA_FILE}")
        return

    # Pobieramy mecze z wczoraj i dzisiaj
    scraped_matches = fetch_flashscore_hockey_results(day_offset=-1) + fetch_flashscore_hockey_results(day_offset=0)
    print(f"[i] Znaleziono {len(scraped_matches)} zakończonych/trwających spotkań na Flashscore.")

    updated_count = 0

    # 2. Mapowanie i aktualizacja sekcji Wyniki ALL
    # Przeszukujemy strukturę data.json (zakładając klucz 'wyniki_all' lub 'results')
    target_list = data.get("wyniki_all", data.get("results", []))

    for item in target_list:
        # Aktualizujemy tylko te mecze, które nie mają jeszcze wpisanego wyniku P1
        if item.get("p1_home") is None or item.get("p1_away") is None or item.get("p1_home") == "":
            for m in scraped_matches:
                # Proste dopasowanie po nazwach drużyn
                if (m["home"].lower() in item.get("home", "").lower() or item.get("home", "").lower() in m["home"].lower()) and \
                   (m["away"].lower() in item.get("away", "").lower() or item.get("away", "").lower() in m["away"].lower()):
                    
                    item["p1_home"] = m["p1_home"]
                    item["p1_away"] = m["p1_away"]
                    item["over15_p1"] = (m["p1_home"] + m["p1_away"]) > 1
                    updated_count += 1
                    print(f"[+] Zaktualizowano mecz: {item.get('home')} vs {item.get('away')} -> P1: {m['p1_home']}:{m['p1_away']}")
                    break

    # 3. Zapisz zaktualizowany data.json
    if updated_count > 0:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"[✓] pomyślnie zaktualizowano {updated_count} rekordów w {DATA_FILE}")
    else:
        print("[i] Brak nowych wyników do zaktualizowania.")

if __name__ == "__main__":
    update_data_json()
