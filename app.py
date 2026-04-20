"""
NOBE Statistiky – Monitorovací dashboard pro autoškolu
======================================================
Streamlit + Playwright (headless Chromium) + Pandas
"""

import asyncio
import re
import subprocess
import sys
from datetime import date

import pandas as pd
import plotly.graph_objects as go
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

BASE_URL   = "https://nobe.moje-autoskola.cz"
LOGIN_URL  = f"{BASE_URL}/index.php"
LIST_URL   = (
    f"{BASE_URL}/admin_prednasky.php"
    "?vytez_datum_od={{datum}}"
    "&vytez_typ=545"
    "&vytez_lokalita={{lokalita}}"
    "&akce=prednasky_filtr"
)

BLOCKED_RESOURCES = {"image", "stylesheet", "font", "media"}


# ──────────────────────────────────────────────────────────────────────────────
# PLAYWRIGHT – INSTALACE BINÁREK (cloud kompatibilita)
# ──────────────────────────────────────────────────────────────────────────────

def ensure_playwright_browsers() -> None:
    """
    Instaluje pouze Chromium binárku (bez systémových závislostí).
    Systémové balíčky (libnss3 atd.) musí být v packages.txt – nelze je
    instalovat za běhu bez root oprávnění na Streamlit Cloudu.
    """
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            browser.close()
    except Exception:
        st.toast("🔧 Instaluji Chromium binárku…", icon="⏳")
        result = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            st.error(
                f"❌ Instalace Chromia selhala.\n\n"
                f"**stderr:** `{result.stderr[-500:]}`\n\n"
                "Ujistěte se, že `packages.txt` obsahuje systémové závislosti "
                "a je součástí repozitáře."
            )
            st.stop()


# ──────────────────────────────────────────────────────────────────────────────
# SCRAPER (async Playwright)
# ──────────────────────────────────────────────────────────────────────────────

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

async def _set_session_context(page, lokalita_id: int) -> None:
    """Explicitně přepne session na konkrétní pobočku dle struktury portalu."""
    session_url = (
        f"{BASE_URL}/admin_nastav_stredisko.php"
        f"?form_data[session_stredisko]={lokalita_id}"
        f"&akce=nastav_stredisko"
        f"&form_data[referer]=%2Fadmin_prednasky.php"
    )
    print(f"[DEBUG] 🔄 Přepínám session na pobočku {lokalita_id}...")
    await page.goto(session_url, wait_until="load", timeout=30_000)
    await page.wait_for_timeout(1000)
    
    header = await page.query_selector("li.dropdown.navbar_stredisko")
    if header:
        classes = await header.get_attribute("class")
        print(f"[DEBUG] ✅ Session nastavena. Header třída: {classes}")

async def _get_detail_urls(page, datum: str, lokalita: int) -> list[str]:
    # 1️⃣ NEJPRVE přepni session (řeší ignorování URL filtru)
    await _set_session_context(page, lokalita)

    # 2️⃣ Teď až načti filtrovanou URL
    url = LIST_URL.replace("{{datum}}", datum).replace("{{lokalita}}", str(lokalita))
    print(f"[DEBUG] 🔍 Navigace na: {url}")
    
    try:
        await page.goto(url, wait_until="load", timeout=90_000)
    except PlaywrightTimeout:
        try:
            await page.goto(url, wait_until="load", timeout=60_000)
        except PlaywrightTimeout:
            return []

    # 3️⃣ Počkej na vyrenderování tabulky + dojetí AJAX filtru
    await page.wait_for_selector("#tab-terminy tbody tr", timeout=30_000)
    try:
        await page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightTimeout:
        pass
    await page.wait_for_timeout(1500)

    # 4️⃣ Extrahuj odkazy
    links = await page.query_selector_all('#tab-terminy tbody a[href*="admin_prednaska.php?edit_id="]')
    seen, result = set(), []
    for link in links:
        href = await link.get_attribute("href")
        if href and href not in seen:
            full = href if href.startswith("http") else f"{BASE_URL}/{href.lstrip('/')}"
            seen.add(href)
            result.append(full)
    
    print(f"[DEBUG] 📥 Extrahováno odkazů: {len(result)}")
    return result

async def _scrape_detail(page, url: str) -> dict | None:
    # ... (původní funkce zůstává beze změny, jen ji sem zkopíruj, pokud ji nahrazuješ celou sekci)
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
    """Hlavní async funkce – přihlásí se, projde termíny, vrátí DataFrame."""
    datum = date.today().strftime("%d.%m.%Y")   # CRM vyžaduje formát DD.MM.YYYY
    results = []

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

        logged_in = await _login(page, email, heslo)
        if not logged_in:
            await browser.close()
            st.error("❌ Přihlášení selhalo – zkontrolujte přihlašovací údaje v st.secrets.")
            return pd.DataFrame()

        detail_urls = await _get_detail_urls(page, datum, lokalita)
        if not detail_urls:
            await browser.close()
            st.warning("⚠️ Nenalezeny žádné termíny pro zvolené datum a pobočku.")
            return pd.DataFrame()

        progress = st.progress(0, text="Načítám termíny…")
        for i, url in enumerate(detail_urls):
            progress.progress((i + 1) / len(detail_urls), text=f"Termín {i+1}/{len(detail_urls)}")
            data = await _scrape_detail(page, url)
            if data:
                results.append(data)

        progress.empty()
        await browser.close()

    return pd.DataFrame(results) if results else pd.DataFrame()


def run_scraper(email: str, heslo: str, lokalita: int) -> pd.DataFrame:
    """Synchronní wrapper – spustí async scraper v novém event loopu."""
    return asyncio.run(scrape_all(email, heslo, lokalita))


# ──────────────────────────────────────────────────────────────────────────────
# CACHE – data se uchovají 30 minut nebo do manuálního promazání
# ──────────────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=1800, show_spinner=False)
def cached_data(email: str, heslo: str, lokalita: int) -> pd.DataFrame:
    return run_scraper(email, heslo, lokalita)


# ──────────────────────────────────────────────────────────────────────────────
# UI
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
              border-bottom:1px dashed #999;">
            {r['Termín']}
          </a></td>
          <td style="text-align:center">{r['Žáků celkem']}</td>
          <td style="text-align:center;color:{zap_col};font-weight:600">{r['Zaplaceno']}</td>
          <td style="text-align:center;color:{nez_col};font-weight:600">{r['Nezaplaceno']}</td>
          <td style="min-width:180px">
            <div style="display:flex;align-items:center;gap:8px;">
              <div style="flex:1;background:#e0e0e0;border-radius:6px;height:10px;overflow:hidden;">
                <div style="width:{bar_w};background:#2ecc71;height:100%;border-radius:6px;
                            transition:width .3s;"></div>
              </div>
              <span style="font-size:.85rem;min-width:38px;text-align:right">{pct:.0f}&nbsp;%</span>
            </div>
          </td>
        </tr>"""

    html = f"""
    <style>
      .nobe-table {{ width:100%;border-collapse:collapse;font-size:.92rem; }}
      .nobe-table th {{
        padding:8px 12px;text-align:left;
        border-bottom:2px solid #555;
        font-size:.8rem;text-transform:uppercase;
        letter-spacing:.05em;opacity:.65;
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


def _DELETED_render_chart(df: pd.DataFrame) -> None:
    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="Zaplaceno ✅",
        x=df["Termín"],
        y=df["Zaplaceno"],
        marker_color="#2ecc71",
        text=df["Zaplaceno"],
        textposition="inside",
    ))
    fig.add_trace(go.Bar(
        name="Nezaplaceno ❌",
        x=df["Termín"],
        y=df["Nezaplaceno"],
        marker_color="#e74c3c",
        text=df["Nezaplaceno"],
        textposition="inside",
    ))
    max_stack = (df["Zaplaceno"] + df["Nezaplaceno"]).max() if not df.empty else 1
    y_max = max(max_stack * 1.45, 10)   # 45% rezerva nad nejvyšším sloupcem

    fig.update_layout(
        barmode="stack",
        title="Obsazenost a stav plateb po termínech",
        xaxis_title="Termín",
        yaxis=dict(
            title="Počet žáků",
            range=[0, y_max],
            dtick=1,              # vždy celá čísla na ose
        ),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(size=13),
        height=480,
        uniformtext=dict(mode="show", minsize=11),  # popisky vždy čitelné
    )
    st.plotly_chart(fig, use_container_width=True)


def main() -> None:
    st.set_page_config(
        page_title="NOBE Statistiky",
        page_icon="🚗",
        layout="wide",
    )

    # ── Ověření secrets ──
    try:
        email = st.secrets["moje_jmeno"]
        heslo = st.secrets["moje_heslo"]
    except KeyError:
        st.error(
            "🔑 Chybí přihlašovací údaje. "
            "Přidejte `moje_jmeno` a `moje_heslo` do **Settings → Secrets**."
        )
        st.code(
            '[moje_jmeno]\nmoje_jmeno = "vas@email.cz"\n\n[moje_heslo]\nmoje_heslo = "vase_heslo"',
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
            st.cache_data.clear()
            st.rerun()

        st.markdown("---")
        st.caption(f"Dnešní datum: **{date.today().strftime('%d. %m. %Y')}**")
        st.caption("Data se obnovují automaticky každých 30 min.")

    # ── Nadpis ──
    st.title(f"📊 Termíny – {pobocka_nazev}")
    st.markdown(f"Zobrazeny budoucí termíny od **{date.today().strftime('%d. %m. %Y')}**")

    # ── Instalace binárek (první spuštění) ──
    with st.spinner("Kontroluji Playwright…"):
        ensure_playwright_browsers()

    # ── Načtení dat ──
    with st.spinner(f"Načítám data pro pobočku **{pobocka_nazev}**…"):
        df = cached_data(email, heslo, lokalita_id)

    if df.empty:
        st.info("ℹ️ Žádná data k zobrazení. Zkuste aktualizovat nebo zvolte jinou pobočku.")
        st.stop()

    # Seřadit od nejbližšího termínu (vlevo) do vzdálenějších (vpravo)
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

    # ── Metriky ──
    st.markdown("""
        <style>
        [data-testid="stMetricDelta"] svg { display: none; }
        </style>""", unsafe_allow_html=True)

    col1, col2, col3 = st.columns(3)
    col1.metric("📋 Počet termínů",  len(df))
    col2.metric("👥 Žáků celkem",    int(df["Žáků celkem"].sum()))

    zap_czk  = int(df["Zaplaceno_Kč"].sum())
    pred_czk = int(df["Předepsáno_Kč"].sum())
    zap_pct  = zap_czk / pred_czk * 100 if pred_czk else 0
    col3.metric(
        "💳 Celkem zaplaceno",
        int(df["Zaplaceno"].sum()),
        delta=f"{int(df['Zaplaceno'].sum() / max(df['Žáků celkem'].sum(), 1) * 100)} %",
    )
    col3.caption(
        f"{zap_czk:,} z {pred_czk:,} Kč — {zap_pct:.0f} % má alespoň něco uhrazeno"
        .replace(",", "\u00a0")
    )

    st.markdown("---")

    render_table(df)


if __name__ == "__main__":
    main()
