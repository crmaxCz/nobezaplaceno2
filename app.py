"""
NOBE Statistiky – Monitorovací dashboard pro autoškolu
======================================================
Streamlit + Playwright (headless Chromium) + Pandas
"""

import asyncio
import base64
import re
import subprocess
import sys
import threading
import time
from datetime import date
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import streamlit as st
import httpx
from bs4 import BeautifulSoup

# ──────────────────────────────────────────────────────────────────────────────
# KONFIGURACE
# ──────────────────────────────────────────────────────────────────────────────

POBOCKY = {
    "Praha":          136,
    "Brno":           137,
    "Plzeň":          268,
    "Ostrava":        354,
    "Olomouc":        133,
    "Hradec Králové": 277,
    "Liberec":        326,
    "Pardubice":      387,
    "Nový Jičín":     151,
    "Frýdek-Místek":  321,
    "Havířov":        237,
    "Opava":          203,
    "Trutnov":        215,
    "Zlín":           400,
}

PRIORITY_POBOCKY = ("Praha", "Brno", "Ostrava", "Plzeň")

BASE_URL      = "https://nobe.moje-autoskola.cz"
LOGIN_URL     = f"{BASE_URL}/index.php"
LIST_PATH_TPL = (
    "/admin_prednasky.php"
    "?vytez_datum_od={datum}"
    "&vytez_datum_do={datum_do}"
    "&vytez_typ=545"
    "&vytez_lokalita={lokalita}"
    "&akce=prednasky_filtr"
)

CONCURRENCY       = 6
CACHE_TTL         = 1800  # 30 minut
BLOCKED_RESOURCES = {"image", "stylesheet", "font", "media"}


# ──────────────────────────────────────────────────────────────────────────────
# GIF LOADER
# ──────────────────────────────────────────────────────────────────────────────

@st.cache_resource
def _gif_base64() -> str:
    p = Path("assets/zebra.gif")
    if p.exists():
        return base64.b64encode(p.read_bytes()).decode()
    return ""


def _show_zebra(placeholder, text: str, pct: int | None = None) -> None:
    """Vykreslí loading screen se zebrou do předaného st.empty() placeholderu."""
    gif_b64 = _gif_base64()
    img_tag = (
        f'<img src="data:image/gif;base64,{gif_b64}" '
        f'style="width:100px;height:auto;display:block;margin:0 auto;">'
        if gif_b64 else
        '<div style="font-size:3rem;text-align:center">🦓</div>'
    )
    pct_html = (
        f'<p style="margin:4px 0 0;font-size:.85rem;opacity:.55;">{pct} %</p>'
        if pct is not None else ""
    )
    placeholder.markdown(f"""
    <div style="display:flex;flex-direction:column;align-items:center;
                justify-content:center;padding:80px 0 60px;">
        {img_tag}
        <p style="margin:18px 0 0;font-size:1rem;font-weight:500;
                  text-align:center;">{text}</p>
        {pct_html}
    </div>
    """, unsafe_allow_html=True)


# ──────────────────────────────────────────────────────────────────────────────
# PLAYWRIGHT – INSTALACE BINÁREK
# ──────────────────────────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
# PARSERY
# ──────────────────────────────────────────────────────────────────────────────

def _parse_castky(td_text: str) -> tuple[int, int]:
    """
    Vrátí (zaplaceno_czk, predepsano_czk).
    Formáty: '7 700,- Kč z 29 300,- Kč'  → (7700, 29300)
             ' z 6 950,- Kč'              → (0, 6950)
    """
    normalized = (
        td_text
        .replace("\xa0", " ").replace("\u202f", " ")
        .replace(",-", "").replace("Kč", "").replace("CZK", "")
        .strip()
    )
    parts = re.split(r"\s+z\s+", normalized, maxsplit=1)

    def to_int(s: str) -> int:
        digits = re.sub(r"\D", "", s.strip())
        return int(digits) if digits else 0

    if len(parts) == 2:
        return to_int(parts[0]), to_int(parts[1])
    return 0, to_int(parts[0])


def _parse_platba(td_text: str) -> bool:
    zap, _ = _parse_castky(td_text)
    return zap > 0


# ──────────────────────────────────────────────────────────────────────────────
# SCRAPER
# ──────────────────────────────────────────────────────────────────────────────

async def _login(client: httpx.AsyncClient, email: str, heslo: str) -> bool:
    """Přihlášení přes přímý POST požadavek na PHP backend."""
    try:
        resp = await client.post(
            LOGIN_URL,
            data={"log_email": email, "log_heslo": heslo, "akce": "login"},
            timeout=10.0
        )
        return "log_email" not in resp.text
    except Exception:
        return False


def _build_stredisko_redirect_url(datum: str, datum_do: str, lokalita: int) -> str:
    """Sestaví URL, která nastaví středisko 957 a redirectne na list page."""
    referer = LIST_PATH_TPL.format(datum=datum, datum_do=datum_do, lokalita=lokalita)
    return (
        f"{BASE_URL}/admin_nastav_stredisko.php"
        f"?form_data[session_stredisko]=957"
        f"&akce=nastav_stredisko"
        f"&form_data[referer]={quote(referer, safe='')}"
    )


def _extract_detail_urls(html: str) -> list[str]:
    """Extrahuje detail URL ze staženého HTML."""
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


def _parse_detail_html(html: str, url: str) -> dict:
    """Synchronní parsing detailu z raw HTML (vyhýbá se async DOM queries pro vyšší rychlost)."""
    termin_match = re.search(r"edit_id=(\d+)", url)
    termin_id = termin_match.group(1) if termin_match else "?"

    datum_str = None
    # Hledáme datum v hlavičkách (h1, h2) nebo titulku
    for tag in [r"<h1[^>]*>(.*?)</h1>", r"<h2[^>]*>(.*?)</h2>", r"<title[^>]*>(.*?)</title>"]:
        m = re.search(tag, html, re.IGNORECASE | re.DOTALL)
        if m:
            text = re.sub(r"<[^>]+>", "", m.group(1))
            dt = re.search(r"(\d{1,2}\.\d{1,2}\.\d{4})[^\d]{0,10}(\d{1,2}:\d{2})", text)
            if dt:
                datum_str = f"{dt.group(1)} {dt.group(2)}"
                break
            d = re.search(r"(\d{1,2}\.\d{1,2}\.\d{4})", text)
            if d:
                datum_str = d.group(1)
                break
    
    if not datum_str:
        # Fallback na regex z textového obsahu (prvních 5000 znaků)
        body_text = re.sub(r"<[^>]+>", "", html[:5000])
        dt = re.search(r"(\d{1,2}\.\d{1,2}\.\d{4})[^\d]{0,10}(\d{1,2}:\d{2})", body_text)
        if dt:
            datum_str = f"{dt.group(1)} {dt.group(2)}"
        else:
            d = re.search(r"(\d{1,2}\.\d{1,2}\.\d{4})", body_text)
            datum_str = d.group(1) if d else f"#{termin_id}"

    celkem = 0
    zaplaceno = 0
    zaplaceno_czk  = 0
    predepsano_czk = 0
    nedostavili = 0

    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", class_="table-striped")
    if table:
        parent = table.find("tbody") or table
        rows = parent.find_all("tr", recursive=False)
        for i, row in enumerate(rows):
            text = row.get_text(strip=True)
            if i == 0 or "∑" in text or not text:
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
        "Termín":        datum_str,
        "ID":            termin_id,
        "Žáků celkem":   celkem,
        "Nedostavili se": nedostavili,
        "Zaplaceno":     zaplaceno,
        "Nezaplaceno":   celkem - zaplaceno,
        "Zaplaceno_Kč":  zaplaceno_czk,
        "Předepsáno_Kč": predepsano_czk,
        "URL":           url,
    }


async def _scrape_detail(client: httpx.AsyncClient, url: str) -> dict | None:
    """Stáhne raw HTML přes API request a naparsuje ho."""
    try:
        response = await client.get(url, timeout=15.0)
        if response.status_code != 200:
            return None
        return _parse_detail_html(response.text, url)
    except Exception:
        return None


async def scrape_all(email: str, heslo: str, lokalita: int, datum: str, datum_do: str) -> pd.DataFrame:
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"}
    async with httpx.AsyncClient(follow_redirects=True, headers=headers) as client:
        if not await _login(client, email, heslo):
            return pd.DataFrame(columns=["_error_login"])

        # Nastaví středisko 957 a rovnou redirectne na list page
        redirect_url = _build_stredisko_redirect_url(datum, datum_do, lokalita)
        try:
            resp = await client.get(redirect_url, timeout=15.0)
        except Exception:
            return pd.DataFrame()

        # Ověříme, že redirect dovedl na list page; jinak fallback
        if "admin_prednasky" not in str(resp.url):
            fallback = f"{BASE_URL}{LIST_PATH_TPL.format(datum=datum, datum_do=datum_do, lokalita=lokalita)}"
            try:
                resp = await client.get(fallback, timeout=15.0)
            except Exception:
                return pd.DataFrame()

        detail_urls = _extract_detail_urls(resp.text)
        if not detail_urls:
            return pd.DataFrame()

        semaphore = asyncio.Semaphore(15)  # Můžeme zvýšit, protože neděláme rendering

        async def fetch_one(url: str) -> dict | None:
            async with semaphore:
                return await _scrape_detail(client, url)

        raw = await asyncio.gather(*[fetch_one(u) for u in detail_urls])
        results = [r for r in raw if r is not None]

    return pd.DataFrame(results) if results else pd.DataFrame()


def run_scraper(email: str, heslo: str, lokalita: int, datum: str, datum_do: str) -> pd.DataFrame:
    return asyncio.run(scrape_all(email, heslo, lokalita, datum, datum_do))


# ──────────────────────────────────────────────────────────────────────────────
# CACHE & PREFETCH
# ──────────────────────────────────────────────────────────────────────────────

@st.cache_resource
def _get_shared_cache() -> dict[tuple[int, str, str], tuple[float, pd.DataFrame]]:
    return {}


@st.cache_resource
def _get_shared_lock() -> threading.Lock:
    return threading.Lock()


def _cache_get(lid: int, datum: str, datum_do: str) -> pd.DataFrame | None:
    cache = _get_shared_cache()
    lock = _get_shared_lock()
    key = (lid, datum, datum_do)
    with lock:
        if key in cache:
            ts, df = cache[key]
            if time.time() - ts < CACHE_TTL:
                return df
            del cache[key]
    return None


def _cache_set(lid: int, datum: str, datum_do: str, df: pd.DataFrame) -> None:
    cache = _get_shared_cache()
    lock = _get_shared_lock()
    key = (lid, datum, datum_do)
    with lock:
        cache[key] = (time.time(), df)


def _cache_clear() -> None:
    cache = _get_shared_cache()
    lock = _get_shared_lock()
    with lock:
        cache.clear()


def get_data(lokalita: int, datum: str, datum_do: str) -> pd.DataFrame:
    """Vrátí data z cache nebo scrape on-demand."""
    cached = _cache_get(lokalita, datum, datum_do)
    if cached is not None:
        return cached
    email = st.secrets["moje_jmeno"]
    heslo = st.secrets["moje_heslo"]
    df = run_scraper(email, heslo, lokalita, datum, datum_do)
    if "_error_login" not in df.columns:
        _cache_set(lokalita, datum, datum_do, df)
    return df


async def _prefetch_batch_impl(
    email: str, heslo: str, lokalita_ids: list[int],
    cache_dict: dict, cache_lock: threading.Lock,
    datum: str, datum_do: str
) -> None:
    """Scrape více poboček v JEDNÉ session a uloží do cache."""
    semaphore = asyncio.Semaphore(15)
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"}

    async with httpx.AsyncClient(follow_redirects=True, headers=headers) as client:
        if not await _login(client, email, heslo):
            return

        # Středisko 957 – jednou pro celý batch
        try:
            await client.get(
                f"{BASE_URL}/admin_nastav_stredisko.php"
                f"?form_data[session_stredisko]=957&akce=nastav_stredisko",
                timeout=15.0
            )
        except Exception:
            pass

        for lid in lokalita_ids:
            with cache_lock:
                if (lid, datum, datum_do) in cache_dict:
                    continue

            list_url = f"{BASE_URL}{LIST_PATH_TPL.format(datum=datum, datum_do=datum_do, lokalita=lid)}"
            try:
                resp = await client.get(list_url, timeout=15.0)
            except Exception:
                with cache_lock:
                    cache_dict[(lid, datum, datum_do)] = (time.time(), pd.DataFrame())
                continue

            detail_urls = _extract_detail_urls(resp.text)
            if not detail_urls:
                with cache_lock:
                    cache_dict[(lid, datum, datum_do)] = (time.time(), pd.DataFrame())
                continue

            async def _fetch(url: str) -> dict | None:
                async with semaphore:
                    return await _scrape_detail(client, url)

            raw = await asyncio.gather(*[_fetch(u) for u in detail_urls])
            results = [r for r in raw if r is not None]
            df_res = pd.DataFrame(results) if results else pd.DataFrame()
            key = (lid, datum, datum_do)
            with cache_lock:
                cache_dict[key] = (time.time(), df_res)


def _start_prefetch(exclude_lid: int, datum: str, datum_do: str) -> None:
    """Spustí background prefetch pro priority pobočky (mimo aktuální)."""
    if st.session_state.get("filter_type", "default") != "default":
        return
    if st.session_state.get("_prefetch_started"):
        return
    st.session_state["_prefetch_started"] = True

    ids = [POBOCKY[n] for n in PRIORITY_POBOCKY if POBOCKY[n] != exclude_lid]
    if not ids:
        return

    email = st.secrets["moje_jmeno"]
    heslo = st.secrets["moje_heslo"]
    
    # Musíme vyzvednout cache objekty TADY v hlavním vlákně (Streamlit kontext)
    c_dict = _get_shared_cache()
    c_lock = _get_shared_lock()

    threading.Thread(
        target=lambda: asyncio.run(_prefetch_batch_impl(email, heslo, ids, c_dict, c_lock, datum, datum_do)),
        daemon=True,
    ).start()


# ──────────────────────────────────────────────────────────────────────────────
# TABULKA
# ──────────────────────────────────────────────────────────────────────────────

def render_table(df: pd.DataFrame) -> None:
    today = pd.Timestamp.today().normalize()
    rows = ""
    for _, r in df.iterrows():
        pct     = r["Zaplaceno"] / max(r["Žáků celkem"], 1) * 100
        bar_w   = f"{pct:.1f}%"
        zap_col = "#2ecc71" if r["Zaplaceno"] > 0 else "#aaa"
        nez_col = "#e74c3c" if r["Nezaplaceno"] > 0 else "#aaa"
        
        try:
            termin_dt = pd.to_datetime(str(r["Termín"]).split(" ")[0], format="%d.%m.%Y", dayfirst=True)
            is_past = pd.notna(termin_dt) and termin_dt.normalize() < today
        except:
            is_past = False
            
        ned = r.get("Nedostavili se", 0)
        celkem = max(r["Žáků celkem"], 1)
        
        if is_past:
            ned_pct = (ned / celkem) * 100
            ned_text = f"<b>{ned}</b> <span style='font-size:0.8em;opacity:0.7'>({ned_pct:.0f} %)</span>"
        else:
            ned_text = "-"

        rows += f"""
        <tr>
          <td><a href="{r['URL']}" target="_blank" style="
              color:inherit;font-weight:600;text-decoration:none;
              border-bottom:1px dashed #999;">{r['Termín']}</a></td>
          <td style="text-align:center">{r['Žáků celkem']}</td>
          <td style="text-align:center;color:{zap_col};font-weight:600">{r['Zaplaceno']}</td>
          <td style="text-align:center;color:{nez_col};font-weight:600">{r['Nezaplaceno']}</td>
          <td style="min-width:180px">
            <div style="display:flex;align-items:center;gap:8px;">
              <div style="flex:1;background:#e0e0e0;border-radius:6px;
                          height:10px;overflow:hidden;">
                <div style="width:{bar_w};background:#2ecc71;height:100%;
                            border-radius:6px;"></div>
              </div>
              <span style="font-size:.85rem;min-width:38px;
                           text-align:right">{pct:.0f}&nbsp;%</span>
            </div>
          </td>
          <td style="text-align:center">{ned_text}</td>
        </tr>"""

    html = f"""
    <style>
      .nobe-table {{ width:100%;border-collapse:collapse;font-size:.92rem; }}
      .nobe-table th {{
        padding:8px 12px;text-align:left;border-bottom:2px solid #555;
        font-size:.8rem;text-transform:uppercase;letter-spacing:.05em;opacity:.65;
      }}
      .nobe-table td {{ padding:9px 12px;border-bottom:1px solid rgba(128,128,128,.2); }}
      .nobe-table tr:last-child td {{ border-bottom:none; }}
      .nobe-table tr:hover td {{ background:rgba(128,128,128,.07); }}
    </style>
    <table class="nobe-table">
      <thead><tr>
        <th>Termín</th>
        <th style="text-align:center">Celkem</th>
        <th style="text-align:center">✅ Zaplaceno</th>
        <th style="text-align:center">❌ Nezaplaceno</th>
        <th>Uhrazeno</th>
        <th style="text-align:center">🚶 Nedostavili se</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table>"""
    st.markdown(html, unsafe_allow_html=True)


# ──────────────────────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────────────────────

def get_date_range(filter_type: str) -> tuple[str, str]:
    today = pd.Timestamp.today()
    if filter_type == "last_month":
        first_day_this_month = today.replace(day=1)
        last_day_last_month = first_day_this_month - pd.Timedelta(days=1)
        first_day_last_month = last_day_last_month.replace(day=1)
        return first_day_last_month.strftime('%d.%m.%Y'), last_day_last_month.strftime('%d.%m.%Y')
    elif filter_type == "last_3_months":
        first_day_this_month = today.replace(day=1)
        last_day_last_month = first_day_this_month - pd.Timedelta(days=1)
        first_day_3_months_ago = first_day_this_month - pd.DateOffset(months=3)
        return first_day_3_months_ago.strftime('%d.%m.%Y'), last_day_last_month.strftime('%d.%m.%Y')
    elif filter_type == "next_month":
        first_day_next_month = today.replace(day=1) + pd.DateOffset(months=1)
        last_day_next_month = (first_day_next_month + pd.DateOffset(months=1)) - pd.Timedelta(days=1)
        return first_day_next_month.strftime('%d.%m.%Y'), last_day_next_month.strftime('%d.%m.%Y')
    elif filter_type == "next_3_months":
        first_day_next_month = today.replace(day=1) + pd.DateOffset(months=1)
        last_day_3_months_ahead = (first_day_next_month + pd.DateOffset(months=3)) - pd.Timedelta(days=1)
        return first_day_next_month.strftime('%d.%m.%Y'), last_day_3_months_ahead.strftime('%d.%m.%Y')
    elif filter_type == "custom":
        return st.session_state.custom_start.strftime('%d.%m.%Y'), st.session_state.custom_end.strftime('%d.%m.%Y')
    else:
        # default
        datum = today.strftime("%d.%m.%Y")
        target = today + pd.DateOffset(months=3)
        target += pd.Timedelta(days=6 - target.weekday())
        return datum, target.strftime("%d.%m.%Y")


def main() -> None:
    st.set_page_config(page_title="NOBE Statistiky", page_icon="🚗", layout="wide")

    if "filter_type" not in st.session_state:
        st.session_state.filter_type = "default"

    try:
        _ = st.secrets["moje_jmeno"]
        _ = st.secrets["moje_heslo"]
    except KeyError:
        st.error(
            "🔑 Chybí přihlašovací údaje. "
            "Přidejte `moje_jmeno` a `moje_heslo` do **Settings → Secrets**."
        )
        st.code(
            'moje_jmeno = "vas@email.cz"\nmoje_heslo = "vase_heslo"',
            language="toml",
        )
        st.stop()

    # ── Boční panel ──
    with st.sidebar:
        st.title("🚗 NOBE Statistiky")
        st.markdown("---")
        st.markdown("**Pobočka**")
        pobocka_nazev = st.radio(
            label="pobocka",
            options=list(POBOCKY.keys()),
            label_visibility="collapsed",
        )
        lokalita_id = POBOCKY[pobocka_nazev]
        st.markdown("---")
        if st.button("🔄 Aktualizovat data", use_container_width=True, type="primary"):
            _cache_clear()
            st.session_state.pop("_prefetch_started", None)
            st.rerun()
        st.markdown("---")
        st.caption(f"Dnešní datum: **{date.today().strftime('%d. %m. %Y')}**")
        st.caption("Data se obnovují automaticky každých 30 min.")

    # ── Nadpis ──
    st.title(f"📊 Termíny – {pobocka_nazev}")
    
    # Inicializace vlastního rozsahu
    if "custom_start" not in st.session_state:
        st.session_state.custom_start = pd.Timestamp.today().date()
    if "custom_end" not in st.session_state:
        st.session_state.custom_end = pd.Timestamp.today().date() + pd.Timedelta(days=30)
        
    datum_str, do_str = get_date_range(st.session_state.filter_type)

    col1, col2 = st.columns([1.5, 3.5], vertical_alignment="center")

    with col1:
        st.markdown(f"**Od {datum_str} do {do_str}**")
        
    with col2:
        options = {
            "last_month": "Poslední měsíc",
            "last_3_months": "Poslední 3 měs.",
            "next_month": "Následující měsíc",
            "next_3_months": "Následující 3 měs.",
            "custom": "📅 Vlastní",
            "default": "Zrušit filtr"
        }
        
        default_val = st.session_state.filter_type if st.session_state.filter_type in options else "default"
        
        selection = st.segmented_control(
            "Rychlé filtry",
            options=list(options.keys()),
            format_func=lambda x: options[x],
            default=default_val,
            selection_mode="single",
            label_visibility="collapsed"
        )
        
        if selection and selection != st.session_state.filter_type:
            st.session_state.filter_type = selection
            st.rerun()
            
    # Zobrazit date picker, pokud je vybrán "Vlastní" filtr
    if st.session_state.filter_type == "custom":
        selected_dates = st.date_input(
            "Zvolte rozsah (od – do):",
            value=(st.session_state.custom_start, st.session_state.custom_end),
            format="DD.MM.YYYY"
        )
        if len(selected_dates) == 2:
            start_date, end_date = selected_dates
            if start_date != st.session_state.custom_start or end_date != st.session_state.custom_end:
                st.session_state.custom_start = start_date
                st.session_state.custom_end = end_date
                st.rerun()

    st.markdown("""
        <style>
        [data-testid="stMetricDelta"] svg { display: none; }
        </style>""", unsafe_allow_html=True)

    # ── Loading + content placeholders ──
    loading_slot = st.empty()
    content_slot = st.empty()

    # Okamžitě vymaž starý obsah předchozí pobočky
    content_slot.empty()

    # Zobraz zebru
    _show_zebra(loading_slot, "Stahuji data, načítám termíny a počítám peníze...")
    st.session_state["_progress_slot"] = loading_slot


    # ── Data ──
    df = get_data(lokalita_id, datum_str, do_str)

    # ── Vymaž loading screen ──
    loading_slot.empty()
    st.session_state.pop("_progress_slot", None)

    if "_error_login" in df.columns:
        content_slot.error("❌ Přihlášení selhalo – zkontrolujte přihlašovací údaje v st.secrets.")
        st.stop()

    if df.empty:
        content_slot.info("ℹ️ Žádná data k zobrazení. Zkuste aktualizovat nebo zvolte jinou pobočku.")
        st.stop()

    # ── Řazení ──
    def _sort_key(s):
        for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y"):
            try:
                return pd.to_datetime(s, format=fmt)
            except ValueError:
                pass
        return pd.Timestamp.max

    df = df.copy()
    df["_sort"] = df["Termín"].apply(_sort_key)
    df = df.sort_values("_sort").drop(columns="_sort").reset_index(drop=True)

    # ── Obsah ──
    with content_slot.container():
        zap_czk  = int(df["Zaplaceno_Kč"].sum())
        pred_czk = int(df["Předepsáno_Kč"].sum())
        zap_pct  = zap_czk / pred_czk * 100 if pred_czk else 0

        col1, col2, col3 = st.columns(3)
        col1.metric("📋 Počet termínů",  len(df))
        col2.metric("👥 Žáků celkem",    int(df["Žáků celkem"].sum()))
        col3.metric(
            "💳 Celkem zaplaceno",
            int(df["Zaplaceno"].sum()),
            delta=f"{int(df['Zaplaceno'].sum() / max(df['Žáků celkem'].sum(), 1) * 100)} % má alespoň něco uhrazeno",
        )
        col3.caption(
            f"{zap_czk:,} z {pred_czk:,} Kč — {zap_pct:.0f} %"
            .replace(",", "\u00a0")
        )
        st.markdown("---")
        render_table(df)

    # ── Background prefetch priority poboček ──
    _start_prefetch(exclude_lid=lokalita_id, datum=datum_str, datum_do=do_str)


if __name__ == "__main__":
    main()
