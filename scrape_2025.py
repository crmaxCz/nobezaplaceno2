"""
scrape_2025.py ΓÇô Jednor├ízov├╜ scraper dat za rok 2025
=====================================================
Spus┼Ñ lok├íln─¢:  python scrape_2025.py
V├╜stup:         data_2025.csv  (pot├⌐ commitni do gitu)

Stahuje data za ka┼╛d├╜ m─¢s├¡c roku 2025 pro ka┼╛dou pobo─ìku zvl├í┼í┼Ñ,
aby list-page m─¢la manageable po─ìet ┼Ö├ídk┼» a city detekce fungovala spolehliv─¢.
Odhadovan├╜ ─ìas: 5ΓÇô20 minut podle rychlosti p┼Öipojen├¡.
"""

import asyncio
import argparse
import csv
import os
import re
import sys
from datetime import date
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

# ΓöÇΓöÇ Konfigurace ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ

POBOCKY: dict[str, int] = {
    "Praha":          136,
    "Brno":           137,
    "Plze┼ê":          268,
    "Ostrava":        354,
    "Olomouc":        133,
    "Hradec Kr├ílov├⌐": 277,
    "Liberec":        326,
    "Pardubice":      387,
    "Nov├╜ Ji─ì├¡n":     151,
    "Fr├╜dek-M├¡stek":  321,
    "Hav├¡┼Öov":        237,
    "Opava":          203,
    "Trutnov":        215,
    "Zl├¡n":           400,
}

BASE_URL   = "https://nobe.moje-autoskola.cz"
LOGIN_URL  = f"{BASE_URL}/index.php"
LIST_TPL   = (
    "/admin_prednasky.php"
    "?vytez_datum_od={datum}"
    "&vytez_datum_do={datum_do}"
    "&vytez_typ=545"
    "&vytez_lokalita={lokalita}"
    "&akce=prednasky_filtr"
)

OUTPUT_CSV = Path(__file__).parent / "data_2025.csv"

CSV_FIELDS = [
    "date", "time", "pobocka",
    "iso_week", "day_of_week",
    "zaci_celkem", "zaplaceno", "nezaplaceno",
    "zaplaceno_czk", "predepsano_czk",
    "nedostavili", "termin_id", "url",
]

SEMAPHORE_DETAIL = 12   # paraleln├¡ detail requesty
YEAR = 2025


def _load_credentials(args: argparse.Namespace) -> tuple[str, str]:
    """
    Na─ìte email + heslo v tomto po┼Öad├¡ priorit:
    1. CLI argumenty (--email / --password)
    2. Prom─¢nn├⌐ prost┼Öed├¡ NOBE_EMAIL / NOBE_HESLO
    3. Streamlit secrets.toml (.streamlit/secrets.toml)
    4. Interaktivn├¡ input() ΓÇô bez getpass, aby fungovalo i v PowerShellu
    """
    email = args.email or os.environ.get('NOBE_EMAIL', '')
    heslo = args.password or os.environ.get('NOBE_HESLO', '')

    # Zkus├¡me p┼Öe─ì├¡st ze Streamlit secrets.toml
    if not email or not heslo:
        secrets_path = Path(__file__).parent / '.streamlit' / 'secrets.toml'
        if secrets_path.exists():
            try:
                content = secrets_path.read_text(encoding='utf-8')
                for line in content.splitlines():
                    line = line.strip()
                    if line.startswith('moje_jmeno') and '=' in line and not email:
                        email = line.split('=', 1)[1].strip().strip('"').strip("'")
                    if line.startswith('moje_heslo') and '=' in line and not heslo:
                        heslo = line.split('=', 1)[1].strip().strip('"').strip("'")
                if email and heslo:
                    print('\u2139∩╕Å  P┼Öihla┼íovac├¡ ├║daje na─ìteny z .streamlit/secrets.toml')
            except Exception as e:
                print(f'  ΓÜá Nepoda┼Öilo se p┼Öe─ì├¡st secrets.toml: {e}')

    if not email:
        email = input('NOBE email: ').strip()
    if not heslo:
        print('NOBE heslo (bude zobrazeno): ', end='', flush=True)
        heslo = input().strip()

    return email, heslo


# ΓöÇΓöÇ Parsovac├¡ helpery (kopie z app.py) ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ

def _parse_castky(td_text: str) -> tuple[int, int]:
    normalized = (
        td_text
        .replace("\xa0", " ").replace("\u202f", " ")
        .replace(",-", "").replace("K─ì", "").replace("CZK", "")
        .strip()
    )
    parts = re.split(r"\s+z\s+", normalized, maxsplit=1)

    def to_int(s: str) -> int:
        digits = re.sub(r"\D", "", s.strip())
        return int(digits) if digits else 0

    if len(parts) == 2:
        return to_int(parts[0]), to_int(parts[1])
    return 0, to_int(parts[0])


def _parse_detail_html(html: str, url: str, pobocka: str) -> dict | None:
    termin_match = re.search(r"edit_id=(\d+)", url)
    termin_id = termin_match.group(1) if termin_match else "?"

    datum_str = None
    time_str = ""

    for tag in [r"<h1[^>]*>(.*?)</h1>", r"<h2[^>]*>(.*?)</h2>", r"<title[^>]*>(.*?)</title>"]:
        m = re.search(tag, html, re.IGNORECASE | re.DOTALL)
        if m:
            text = re.sub(r"<[^>]+>", "", m.group(1))
            dt = re.search(r"(\d{1,2}\.\d{1,2}\.\d{4})[^\d]{0,10}(\d{1,2}:\d{2})", text)
            if dt:
                datum_str = dt.group(1)
                time_str = dt.group(2)
                break
            d = re.search(r"(\d{1,2}\.\d{1,2}\.\d{4})", text)
            if d:
                datum_str = d.group(1)
                break

    if not datum_str:
        body_text = re.sub(r"<[^>]+>", "", html[:5000])
        dt = re.search(r"(\d{1,2}\.\d{1,2}\.\d{4})[^\d]{0,10}(\d{1,2}:\d{2})", body_text)
        if dt:
            datum_str = dt.group(1)
            time_str = dt.group(2)
        else:
            d = re.search(r"(\d{1,2}\.\d{1,2}\.\d{4})", body_text)
            datum_str = d.group(1) if d else None

    if not datum_str:
        return None

    # Parsujeme datum pro ISO week
    try:
        parts = datum_str.split(".")
        d_val = int(parts[0])
        m_val = int(parts[1])
        y_val = int(parts[2])
        dt_date = date(y_val, m_val, d_val)
    except Exception:
        return None

    # Filtrujeme ΓÇô chceme jen rok 2025
    if dt_date.year != YEAR:
        return None

    iso = dt_date.isocalendar()
    iso_week    = iso[1]        # 1ΓÇô53
    day_of_week = iso[2]        # 1=Pond─¢l├¡ ΓÇª 7=Ned─¢le

    celkem = 0
    zaplaceno = 0
    zaplaceno_czk = 0
    predepsano_czk = 0
    nedostavili = 0

    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", class_="table-striped")
    if table:
        parent = table.find("tbody") or table
        rows = parent.find_all("tr", recursive=False)
        for i, row in enumerate(rows):
            text = row.get_text(strip=True)
            if i == 0 or "Γêæ" in text or not text:
                continue
            if "text-strike" in row.get("class", []):
                nedostavili += 1
            tds = row.find_all(["td", "th"], recursive=False)
            if len(tds) >= 5:
                celkem += 1
                platba_text = tds[4].get_text(separator=" ", strip=True)
                zap_czk, pred_czk = _parse_castky(platba_text)
                if zap_czk > 0:
                    zaplaceno += 1
                zaplaceno_czk  += zap_czk
                predepsano_czk += pred_czk

    return {
        "date":           datum_str,
        "time":           time_str,
        "pobocka":        pobocka,
        "iso_week":       iso_week,
        "day_of_week":    day_of_week,
        "zaci_celkem":    celkem,
        "zaplaceno":      zaplaceno,
        "nezaplaceno":    celkem - zaplaceno,
        "zaplaceno_czk":  zaplaceno_czk,
        "predepsano_czk": predepsano_czk,
        "nedostavili":    nedostavili,
        "termin_id":      termin_id,
        "url":            url,
    }


def _extract_detail_urls(html: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    links = soup.select('a[href*="admin_prednaska.php?edit_id="]')
    seen, result = set(), []
    for link in links:
        href = link.get("href")
        if href and href not in seen:
            full = href if href.startswith("http") else f"{BASE_URL}/{href.lstrip('/')}"
            seen.add(href)
            result.append(full)
    return result


# ΓöÇΓöÇ Async scraping ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ

async def _login(client: httpx.AsyncClient, email: str, heslo: str) -> bool:
    try:
        resp = await client.post(
            LOGIN_URL,
            data={"log_email": email, "log_heslo": heslo, "akce": "login"},
            timeout=15.0
        )
        return "log_email" not in resp.text
    except Exception:
        return False


async def _scrape_month(
    client: httpx.AsyncClient,
    pobocka_name: str,
    pobocka_id: int,
    year: int,
    month: int,
    semaphore: asyncio.Semaphore,
) -> list[dict]:
    """St├íhne data jedn├⌐ pobo─ìky za jeden m─¢s├¡c."""
    from calendar import monthrange
    first_day = date(year, month, 1)
    last_day  = date(year, month, monthrange(year, month)[1])
    datum    = first_day.strftime("%d.%m.%Y")
    datum_do = last_day.strftime("%d.%m.%Y")

    list_url = f"{BASE_URL}{LIST_TPL.format(datum=datum, datum_do=datum_do, lokalita=pobocka_id)}"
    try:
        resp = await client.get(list_url, timeout=20.0)
    except Exception as e:
        print(f"  ΓÜá list page chyba {pobocka_name} {year}/{month:02d}: {e}")
        return []

    detail_urls = _extract_detail_urls(resp.text)
    if not detail_urls:
        return []

    async def fetch_one(url: str) -> dict | None:
        async with semaphore:
            try:
                r = await client.get(url, timeout=20.0)
                if r.status_code != 200:
                    return None
                return _parse_detail_html(r.text, url, pobocka_name)
            except Exception:
                return None

    raw = await asyncio.gather(*[fetch_one(u) for u in detail_urls])
    return [r for r in raw if r is not None]


async def scrape_all_2025(email: str, heslo: str) -> list[dict]:
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    semaphore = asyncio.Semaphore(SEMAPHORE_DETAIL)
    all_rows: list[dict] = []

    async with httpx.AsyncClient(follow_redirects=True, headers=headers) as client:
        print("≡ƒöæ P┼Öihla┼íov├ín├¡...")
        if not await _login(client, email, heslo):
            print("Γ¥î P┼Öihl├í┼íen├¡ selhalo ΓÇô zkontroluj NOBE_EMAIL / NOBE_HESLO")
            sys.exit(1)

        # Nastavit st┼Öedisko 957
        try:
            await client.get(
                f"{BASE_URL}/admin_nastav_stredisko.php"
                f"?form_data[session_stredisko]=957&akce=nastav_stredisko",
                timeout=15.0,
            )
        except Exception:
            pass

        total = len(POBOCKY) * 12
        done  = 0
        for pobocka_name, pobocka_id in POBOCKY.items():
            for month in range(1, 13):
                done += 1
                print(f"[{done}/{total}] {pobocka_name} ΓÇô {YEAR}/{month:02d} ...", end=" ", flush=True)
                rows = await _scrape_month(client, pobocka_name, pobocka_id, YEAR, month, semaphore)
                print(f"{len(rows)} term├¡n┼»")
                all_rows.extend(rows)

    return all_rows


# ΓöÇΓöÇ Hlavn├¡ vstupn├¡ bod ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ

def main() -> None:
    parser = argparse.ArgumentParser(
        description='Jednor├ízov├╜ scraper NOBE dat za rok 2025'
    )
    parser.add_argument('--email', '-e', default='', help='P┼Öihla┼íovac├¡ email (nebo nastav NOBE_EMAIL)')
    parser.add_argument('--password', '-p', default='', help='Heslo (nebo nastav NOBE_HESLO)')
    args = parser.parse_args()

    email, heslo = _load_credentials(args)

    print(f'\n≡ƒÜÇ Spou┼ít├¡m scrape roku {YEAR} ΓÇô {len(POBOCKY)} pobo─ìek ├ù 12 m─¢s├¡c┼»')
    print(f'≡ƒôä V├╜stup: {OUTPUT_CSV}\n')

    rows = asyncio.run(scrape_all_2025(email, heslo))

    if not rows:
        print('ΓÜá ┼╜├ídn├í data nebyla sta┼╛ena.')
        sys.exit(1)

    # Deduplikace dle termin_id (pro p┼Ö├¡pad p┼Öekryv┼» na p┼Öelomu m─¢s├¡ce)
    seen_ids: set[str] = set()
    unique_rows = []
    for r in rows:
        tid = r.get('termin_id', '')
        if tid not in seen_ids:
            seen_ids.add(tid)
            unique_rows.append(r)

    with open(OUTPUT_CSV, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(unique_rows)

    print(f'\nΓ£à Hotovo! Ulo┼╛eno {len(unique_rows)} term├¡n┼» do {OUTPUT_CSV}')
    print('≡ƒæë Commitni data_2025.csv do gitu a pushni na GitHub.')


if __name__ == "__main__":
    main()
