import json
import re
import unicodedata
from playwright.sync_api import sync_playwright

DATA_FILE = "data.json"

ALIASES = {
    "hifk": "ifk helsinki",
    "ässät": "assat",
    "herlev eagles": "herlev",
    "esbjerg energy": "esbjerg"
}

def normalize(text):
    if not text:
        return ""
    text = unicodedata.normalize('NFKD', str(text)).encode('ASCII', 'ignore').decode('utf-8')
    cleaned = text.lower().strip()
    return ALIASES.get(cleaned, cleaned)

def scrape_flashscore():
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            locale="pl-PL"
        )
        page = context.new_page()

        print("[INFO] Łączenie z Flashscore przez Chromium...")
        page.goto("https://www.flashscore.pl/hokej/", wait_until="domcontentloaded", timeout=60000)
        
        try:
            page.click("#onetrust-accept-btn-handler", timeout=3000)
        except:
            pass

        page.wait_for_selector(".sportName.hockey", timeout=15000)
        
        match_elements = page.query_selector_all(".event__match")
        print(f"[INFO] Wykryto {len(match_elements)} obiektów meczowych w przeglądarce.")

        for el in match_elements:
            try:
                home_el = el.query_selector(".event__homeParticipant")
                away_el = el.query_selector(".event__awayParticipant")
                part_el = el.query_selector(".event__part--1")

                if home_el and away_el and part_el:
                    home = home_el.text_content().strip()
                    away = away_el.text_content().strip()
                    score_text = part_el.text_content().strip()

                    scores = re.findall(r'\d+', score_text)
                    if len(scores) >= 2:
                        results.append({
                            "home": home,
                            "away": away,
                            "p1_home": int(scores[0]),
                            "p1_away": int(scores[1])
                        })
            except Exception:
                continue

        browser.close()
    return results

def main():
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"[ERROR] Nie można odczytać pliku {DATA_FILE}: {e}")
        return

    scraped = scrape_flashscore()
    print(f"[INFO] Pomyślnie zmapowano {len(scraped)} meczów z P1.")

    if not scraped:
        print("[WARN] Brak pobranych danych.")
        return

    updated = 0
    
    def process_items(items):
        nonlocal updated
        if not isinstance(items, list):
            return

        for item in items:
            if not isinstance(item, dict):
                continue

            p1_h = item.get("p1_home", item.get("p1H", None))
            if p1_h is not None and str(p1_h).strip() not in ["", "null"]:
                continue

            h_db = normalize(item.get("home", item.get("gospodarz", "")))
            a_db = normalize(item.get("away", item.get("gosc", "")))

            if not h_db or not a_db:
                continue

            for m in scraped:
                h_scr = normalize(m["home"])
                a_scr = normalize(m["away"])

                if (h_db in h_scr or h_scr in h_db) and (a_db in a_scr or a_scr in a_db):
                    item["p1_home"] = m["p1_home"]
                    item["p1_away"] = m["p1_away"]
                    item["over15_p1"] = (m["p1_home"] + m["p1_away"]) > 1
                    print(f"[+ ZAKTUALIZOWANO] {item.get('home')} vs {item.get('away')} -> P1: {m['p1_home']}:{m['p1_away']}")
                    updated += 1
                    break

    if isinstance(data, dict):
        for k, v in data.items():
            process_items(v)
    elif isinstance(data, list):
        process_items(data)

    if updated > 0:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"[SUKCES] Zaktualizowano {updated} rekordów w {DATA_FILE}.")
    else:
        print("[INFO] Brak pasujących meczów do aktualizacji.")

if __name__ == "__main__":
    main()
