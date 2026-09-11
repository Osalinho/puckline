import requests

# Testowy adres feedu Flashscore dla meczów z ubiegłych dni
URL = "https://local-global.flashscore.ninja/3/x/feed/r_3_-2"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "x-fsign": "SW1hZ2luZSB3aXRob3V0IGEgZmF0ZWZ1bCBzaWduYXR1cmU=",
    "Referer": "https://www.flashscore.pl/hokej/"
}

print("--- TEST POŁĄCZENIA Z FLASHSCORE ---")
try:
    response = requests.get(URL, headers=HEADERS, timeout=10)
    print(f"Status HTTP: {response.status_code}")
    print(f"Rozmiar odpowiedzi: {len(response.text)} bajtów")
    
    print("\n--- PIERWSZE 500 ZNAKÓW ODPOWIEDZI ---")
    print(response.text[:500])
    
    # Analiza wyniku
    if response.status_code == 403:
        print("\n[DIAGNOZA] BŁĄD 403: IP zostało zablokowane przez zapaporę (Cloudflare/Akamai).")
    elif "Cloudflare" in response.text or "Just a moment" in response.text:
        print("\n[DIAGNOZA] BLOKADA CLOUDFLARE: Serwer wymaga weryfikacji JavaScript/CAPTCHA.")
    elif len(response.text) < 100:
        print("\n[DIAGNOZA] PUSTA ODPOWIEDŹ: Serwer zignorował zapytanie z tego adresu IP.")
    elif "AA÷" in response.text:
        print("\n[DIAGNOZA] SUKCES: Dane meczowe zostały prawidłowo pobrane!")
    else:
        print("\n[DIAGNOZA] NIEZNANY FORMAT: Zwrócono inną treść niż dane meczowe.")

except Exception as e:
        print(f"\n[DIAGNOZA] BŁĄD POŁĄCZENIA: {e}")
