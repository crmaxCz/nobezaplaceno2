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
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

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

@st.cache_resource
def _install_chromium() -> str | None:
    """Spustí se jednou za lifetime deploymentu. Vrací chybový text nebo None."""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            p.chromium.launch(headless=True).close()
        return None
    except Exception:
        result = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            capture_output=True, text=True
        )
        return result.stderr[-500:] if result.returncode != 0 else None


def ensure_playwright_browsers() -> None:
    err = _install_chromium()
    if err:
        st.error(
            f"❌ Instalace Chromia selhala.\n\n**stderr:** `{err}`\n\n"
            "Ujistěte se, že `packages.txt` obsahuje systémové závislosti."
        )
        st.stop()


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

async def _block_resource(route, request):
    if request.resource_type in BLOCKED_RESOURCES:
        await route.abort()
    else:
        await route.continue_()


async def _login(page, email: str, heslo: str) -> bool:
    try:
        await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60_000)
        await page.fill('input[name="log_email"]', email)
        await page.fill('input[name="log_heslo"]', heslo)
        await page.click('button[type="submit"], input[type="submit"]')
        await page.wait_for_load_state("domcontentloaded", timeout=60_000)
        return "log_email" not in (await page.content())
    except PlaywrightTimeout:
        return False


def _build_stredisko_redirect_url(datum: str, lokalita: int) -> str:
    """Sestaví URL, která nastaví středisko 957 a redirectne na list page."""
    referer = LIST_PATH_TPL.format(datum=datum, lokalita=lokalita)
    return (
        f"{BASE_URL}/admin_nastav_stredisko.php"
        f"?form_data[session_stredisko]=957"
        f"&akce=nastav_stredisko"
        f"&form_data[referer]={quote(referer, safe='')}"
    )


async def _extract_detail_urls(page) -> list[str]:
    """Extrahuje detail URL z aktuálně načtené stránky (bez navigace)."""
    links = await page.query_selector_all('a[href*="admin_prednaska.php?edit_id="]')
    seen, result = set(), []
    for link in links:
        href = await link.get_attribute("href")
        if href and href not in seen:
            full = href if href.startswith("http") else f"{BASE_URL}/{href.lstrip('/')}"
            seen.add(href)
            result.append(full)
    return result


async def _scrape_detail(page, url: str) -> dict | None:
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    except PlaywrightTimeout:
        return None

    termin_match = re.search(r"edit_id=(\d+)", url)
    termin_id = termin_match.group(1) if termin_match else "?"

    datum_str = None
    for selector in ["h1", "h2", ".card-title", "title", ".page-header"]:
        el = await page.query_selector(selector)
        if not el:
            continue
        text = await el.inner_text()
        dt = re.search(r"(\d{1,2}\.\d{1,2}\.\d{4})[^\d]{0,10}(\d{1,2}:\d{2})", text)
        if dt:
            datum_str = f"{dt.group(1)} {dt.group(2)}"
            break
        d = re.search(r"(\d{1,2}\.\d{1,2}\.\d{4})", text)
        if d:
            datum_str = d.group(1)
            break
    if not datum_str:
        datum_str = f"#{termin_id}"

    celkem = 0
    zaplaceno = 0
    zaplaceno_czk  = 0
    predepsano_czk = 0

    tbody = await page.query_selector(".table-striped tbody")
    if tbody:
        rows = await tbody.query_selector_all("tr")
        for i, row in enumerate(rows):
            text = (await row.inner_text()).strip()
            if i == 0 or "∑" in text or not text:
                continue
            celkem += 1
            tds = await row.query_selector_all("td")
            if len(tds) >= 5:
                platba_text = (await tds[4].inner_text()).strip()
                zap_czk, pred_czk = _parse_castky(platba_text)
                if zap_czk > 0:
                    zaplaceno += 1
                zaplaceno_czk  += zap_czk
                predepsano_czk += pred_czk

    return {
        "Termín":        datum_str,
        "ID":            termin_id,
        "Žáků celkem":   celkem,
        "Zaplaceno":     zaplaceno,
        "Nezaplaceno":   celkem - zaplaceno,
        "Zaplaceno_Kč":  zaplaceno_czk,
        "Předepsáno_Kč": predepsano_czk,
        "URL":           url,
    }


async def scrape_all(email: str, heslo: str, lokalita: int) -> pd.DataFrame:
    datum = date.today().strftime("%d.%m.%Y")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )
        page = await ctx.new_page()
        await page.route("**/*", _block_resource)

        if not await _login(page, email, heslo):
            await browser.close()
            return pd.DataFrame(columns=["_error_login"])

        # Nastaví středisko 957 a rovnou redirectne na list page (1 navigace místo 2)
        redirect_url = _build_stredisko_redirect_url(datum, lokalita)
        try:
            await page.goto(redirect_url, wait_until="domcontentloaded", timeout=60_000)
        except PlaywrightTimeout:
            await browser.close()
            return pd.DataFrame()

        # Ověříme, že redirect dovedl na list page; jinak fallback
        if "admin_prednasky" not in page.url:
            fallback = f"{BASE_URL}{LIST_PATH_TPL.format(datum=datum, lokalita=lokalita)}"
            try:
                await page.goto(fallback, wait_until="domcontentloaded", timeout=60_000)
            except PlaywrightTimeout:
                await browser.close()
                return pd.DataFrame()

        detail_urls = await _extract_detail_urls(page)
        if not detail_urls:
            await browser.close()
            return pd.DataFrame()

        # Page pool – předvytvoříme stránky a rotujeme přes asyncio.Queue
        page_pool: asyncio.Queue = asyncio.Queue()
        for _ in range(CONCURRENCY):
            p = await ctx.new_page()
            await p.route("**/*", _block_resource)
            await page_pool.put(p)

        async def fetch_one(url: str) -> dict | None:
            p = await page_pool.get()
            try:
                return await _scrape_detail(p, url)
            finally:
                await page_pool.put(p)

        raw = await asyncio.gather(*[fetch_one(u) for u in detail_urls])
        results = [r for r in raw if r is not None]

        await browser.close()

    return pd.DataFrame(results) if results else pd.DataFrame()


def run_scraper(email: str, heslo: str, lokalita: int) -> pd.DataFrame:
    return asyncio.run(scrape_all(email, heslo, lokalita))


# ──────────────────────────────────────────────────────────────────────────────
# CACHE & PREFETCH
# ──────────────────────────────────────────────────────────────────────────────

_cache_lock: threading.Lock = threading.Lock()
_branch_cache: dict[int, tuple[float, pd.DataFrame]] = {}


def _cache_get(lid: int) -> pd.DataFrame | None:
    with _cache_lock:
        if lid in _branch_cache:
            ts, df = _branch_cache[lid]
            if time.time() - ts < CACHE_TTL:
                return df
            del _branch_cache[lid]
    return None


def _cache_set(lid: int, df: pd.DataFrame) -> None:
    with _cache_lock:
        _branch_cache[lid] = (time.time(), df)


def _cache_clear() -> None:
    with _cache_lock:
        _branch_cache.clear()


def get_data(lokalita: int) -> pd.DataFrame:
    """Vrátí data z cache nebo scrape on-demand."""
    cached = _cache_get(lokalita)
    if cached is not None:
        return cached
    email = st.secrets["moje_jmeno"]
    heslo = st.secrets["moje_heslo"]
    df = run_scraper(email, heslo, lokalita)
    if "_error_login" not in df.columns:
        _cache_set(lokalita, df)
    return df


async def _prefetch_batch_impl(
    email: str, heslo: str, lokalita_ids: list[int],
) -> None:
    """Scrape více poboček v JEDNÉ browser session a uloží do cache."""
    datum = date.today().strftime("%d.%m.%Y")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )
        page = await ctx.new_page()
        await page.route("**/*", _block_resource)

        if not await _login(page, email, heslo):
            await browser.close()
            return

        # Středisko 957 – jednou pro celý batch
        try:
            await page.goto(
                f"{BASE_URL}/admin_nastav_stredisko.php"
                f"?form_data[session_stredisko]=957&akce=nastav_stredisko",
                wait_until="domcontentloaded", timeout=60_000,
            )
        except PlaywrightTimeout:
            pass

        # Page pool sdílený mezi pobočkami
        page_pool: asyncio.Queue = asyncio.Queue()
        for _ in range(CONCURRENCY):
            p = await ctx.new_page()
            await p.route("**/*", _block_resource)
            await page_pool.put(p)

        for lid in lokalita_ids:
            if _cache_get(lid) is not None:
                continue

            list_url = f"{BASE_URL}{LIST_PATH_TPL.format(datum=datum, lokalita=lid)}"
            try:
                await page.goto(list_url, wait_until="domcontentloaded", timeout=60_000)
            except PlaywrightTimeout:
                _cache_set(lid, pd.DataFrame())
                continue

            detail_urls = await _extract_detail_urls(page)
            if not detail_urls:
                _cache_set(lid, pd.DataFrame())
                continue

            async def _fetch(url: str) -> dict | None:
                p = await page_pool.get()
                try:
                    return await _scrape_detail(p, url)
                finally:
                    await page_pool.put(p)

            raw = await asyncio.gather(*[_fetch(u) for u in detail_urls])
            results = [r for r in raw if r is not None]
            _cache_set(lid, pd.DataFrame(results) if results else pd.DataFrame())

        await browser.close()


def _start_prefetch(exclude_lid: int) -> None:
    """Spustí background prefetch pro priority pobočky (mimo aktuální)."""
    if st.session_state.get("_prefetch_started"):
        return
    st.session_state["_prefetch_started"] = True

    ids = [POBOCKY[n] for n in PRIORITY_POBOCKY if POBOCKY[n] != exclude_lid]
    if not ids:
        return

    email = st.secrets["moje_jmeno"]
    heslo = st.secrets["moje_heslo"]
    threading.Thread(
        target=lambda: asyncio.run(_prefetch_batch_impl(email, heslo, ids)),
        daemon=True,
    ).start()


# ──────────────────────────────────────────────────────────────────────────────
# TABULKA
# ──────────────────────────────────────────────────────────────────────────────

def render_table(df: pd.DataFrame) -> None:
    rows = ""
    for _, r in df.iterrows():
        pct     = r["Zaplaceno"] / max(r["Žáků celkem"], 1) * 100
        bar_w   = f"{pct:.1f}%"
        zap_col = "#2ecc71" if r["Zaplaceno"] > 0 else "#aaa"
        nez_col = "#e74c3c" if r["Nezaplaceno"] > 0 else "#aaa"
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
      </tr></thead>
      <tbody>{rows}</tbody>
    </table>"""
    st.markdown(html, unsafe_allow_html=True)


# ──────────────────────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    st.set_page_config(page_title="NOBE Statistiky", page_icon="🚗", layout="wide")

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
    st.markdown(f"Zobrazeny budoucí termíny od **{date.today().strftime('%d. %m. %Y')}**")

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

    # ── Playwright (tiše, cached) ──
    ensure_playwright_browsers()

    # ── Data ──
    df = get_data(lokalita_id)

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
    _start_prefetch(exclude_lid=lokalita_id)


if __name__ == "__main__":
    main()
