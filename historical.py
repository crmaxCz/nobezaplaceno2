"""
historical.py ΓÇô Na─ìten├¡ a p├írov├ín├¡ historick├╜ch dat roku 2025
=============================================================
Pou┼╛├¡v├íno z app.py pro YoY (year-over-year) porovn├ín├¡.
"""

from pathlib import Path

import pandas as pd
import streamlit as st

CSV_PATH = Path(__file__).parent / "data_2025.csv"

# ΓöÇΓöÇ Na─ìten├¡ dat ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ

@st.cache_data(show_spinner=False)
def load_historical_data() -> pd.DataFrame:
    """
    Na─ìte data_2025.csv jednou a ulo┼╛├¡ do cache.
    Vr├ít├¡ pr├ízdn├╜ DataFrame pokud soubor neexistuje.
    """
    if not CSV_PATH.exists():
        return pd.DataFrame()

    df = pd.read_csv(CSV_PATH, dtype=str)

    # P┼Öev├⌐st numerick├⌐ sloupce
    int_cols = [
        "iso_week", "day_of_week",
        "zaci_celkem", "zaplaceno", "nezaplaceno",
        "zaplaceno_czk", "predepsano_czk", "nedostavili",
    ]
    for col in int_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)

    return df


# ΓöÇΓöÇ P├írov├ín├¡ ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ

def _parse_termin_dt(termin_str: str) -> pd.Timestamp | None:
    """Naparsuje string 'DD.MM.YYYY HH:MM' nebo 'DD.MM.YYYY' na Timestamp."""
    for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y"):
        try:
            return pd.to_datetime(str(termin_str).split(" ")[0], format="%d.%m.%Y")
        except Exception:
            pass
    return None


def find_yoy_match(
    df_hist: pd.DataFrame,
    termin_str: str,
    pobocka: str,
) -> dict | None:
    """
    Pro aktu├íln├¡ term├¡n (datum string + n├ízev pobo─ìky) najde odpov├¡daj├¡c├¡
    z├íznam v historick├⌐m CSV dle kl├¡─ìe (iso_week, day_of_week, pobocka).

    Vr├ít├¡ slovn├¡k s historick├╜mi hodnotami nebo None pokud p├ír neexistuje.
    """
    if df_hist.empty:
        return None

    dt = _parse_termin_dt(termin_str)
    if dt is None:
        return None

    iso        = dt.isocalendar()
    iso_week   = int(iso[1])
    day_of_wk  = int(iso[2])

    # Hled├íme shodu: stejn├╜ ISO t├╜den + stejn├╜ den v t├╜dnu + stejn├í pobo─ìka
    mask = (
        (df_hist["iso_week"]    == iso_week) &
        (df_hist["day_of_week"] == day_of_wk) &
        (df_hist["pobocka"]     == pobocka)
    )
    matches = df_hist[mask]

    if matches.empty:
        return None

    # Pokud je v├¡ce shod (nepravd─¢podobn├⌐), vr├ít├¡me prvn├¡
    row = matches.iloc[0]
    return {
        "hist_date":          row.get("date", ""),
        "hist_zaci":          int(row.get("zaci_celkem", 0)),
        "hist_zaplaceno":     int(row.get("zaplaceno", 0)),
        "hist_nezaplaceno":   int(row.get("nezaplaceno", 0)),
        "hist_zaplaceno_czk": int(row.get("zaplaceno_czk", 0)),
        "hist_predepsano_czk":int(row.get("predepsano_czk", 0)),
        "hist_nedostavili":   int(row.get("nedostavili", 0)),
    }


# ΓöÇΓöÇ Roz┼í├¡┼Öen├¡ cel├⌐ho DataFrame o YoY sloupce ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ

def build_yoy_columns(df: pd.DataFrame, df_hist: pd.DataFrame) -> pd.DataFrame:
    """
    P┼Öid├í do df sloupce s historick├╜mi hodnotami a deltami.
    Pokud df_hist je pr├ízdn├╜ nebo pro ┼Ö├ídek neexistuje p├ír, hodnoty jsou NaN.

    P┼Öidan├⌐ sloupce:
      yoy_date            ΓÇô datum z roku 2025
      yoy_zaci            ΓÇô ┼╛├ík┼» celkem 2025
      yoy_zaplaceno       ΓÇô zaplaceno 2025
      yoy_zap_pct         ΓÇô % zaplaceno 2025
      yoy_zaplaceno_czk   ΓÇô K─ì zaplaceno 2025
      yoy_predepsano_czk  ΓÇô K─ì p┼Öedeps├íno 2025
      yoy_nedostavili     ΓÇô nedostavili 2025

      ╬ö_zaci              ΓÇô rozd├¡l ┼╛├ík┼» (aktu├íln├¡ - 2025)
      ╬ö_zap_pct           ΓÇô rozd├¡l % zaplacen├¡
      ╬ö_zaplaceno_czk     ΓÇô rozd├¡l K─ì zaplaceno
    """
    if df_hist.empty:
        return df

    df = df.copy()

    yoy_cols = {
        "yoy_date":           None,
        "yoy_zaci":           pd.NA,
        "yoy_zaplaceno":      pd.NA,
        "yoy_zap_pct":        pd.NA,
        "yoy_zaplaceno_czk":  pd.NA,
        "yoy_predepsano_czk": pd.NA,
        "yoy_nedostavili":    pd.NA,
        "╬ö_zaci":             pd.NA,
        "╬ö_zap_pct":          pd.NA,
        "╬ö_zaplaceno_czk":    pd.NA,
    }
    for col, default in yoy_cols.items():
        df[col] = default

    # Ur─ì├¡me n├ízev pobo─ìky pro ka┼╛d├╜ ┼Ö├ídek
    # ΓÇô v single-branch m├│du ho dostaneme jako parametr (p┼Öid├ív├íme extern├íln─¢)
    # ΓÇô v all-branches m├│du je ve sloupci "Pobo─ìka"
    has_city_col = "Pobo─ìka" in df.columns

    for idx, row in df.iterrows():
        pobocka = row.get("Pobo─ìka", "") if has_city_col else ""
        # pobocka m┼»┼╛e b├╜t "" pokud single-branch ΓÇô caller mus├¡ doplnit
        match = find_yoy_match(df_hist, str(row["Term├¡n"]), pobocka)
        if match is None:
            continue

        h_zaci  = match["hist_zaci"]
        h_zap   = match["hist_zaplaceno"]
        h_zap_czk = match["hist_zaplaceno_czk"]
        h_pred_czk = match["hist_predepsano_czk"]

        cur_zaci = int(row.get("┼╜├ík┼» celkem", 0))
        cur_zap  = int(row.get("Zaplaceno", 0))
        cur_zap_czk = int(row.get("Zaplaceno_K─ì", 0))

        h_zap_pct   = h_zap   / max(h_zaci, 1) * 100
        cur_zap_pct = cur_zap / max(cur_zaci, 1) * 100

        df.at[idx, "yoy_date"]           = match["hist_date"]
        df.at[idx, "yoy_zaci"]           = h_zaci
        df.at[idx, "yoy_zaplaceno"]      = h_zap
        df.at[idx, "yoy_zap_pct"]        = round(h_zap_pct, 1)
        df.at[idx, "yoy_zaplaceno_czk"]  = h_zap_czk
        df.at[idx, "yoy_predepsano_czk"] = h_pred_czk
        df.at[idx, "yoy_nedostavili"]    = match["hist_nedostavili"]
        df.at[idx, "╬ö_zaci"]             = cur_zaci - h_zaci
        df.at[idx, "╬ö_zap_pct"]          = round(cur_zap_pct - h_zap_pct, 1)
        df.at[idx, "╬ö_zaplaceno_czk"]    = cur_zap_czk - h_zap_czk

    return df


# ΓöÇΓöÇ Souhrnn├⌐ YoY statistiky ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ

def yoy_summary(df: pd.DataFrame) -> dict:
    """
    Vr├ít├¡ souhrnn├⌐ YoY hodnoty z df, kter├╜ ji┼╛ obsahuje yoy_ a ╬ö_ sloupce.
    Slou┼╛├¡ pro st.metric() delty.
    """
    def safe_sum(col: str) -> int | None:
        if col not in df.columns:
            return None
        vals = pd.to_numeric(df[col], errors="coerce").dropna()
        return int(vals.sum()) if not vals.empty else None

    hist_zaci        = safe_sum("yoy_zaci")
    hist_zaplaceno   = safe_sum("yoy_zaplaceno")
    hist_zap_czk     = safe_sum("yoy_zaplaceno_czk")
    hist_pred_czk    = safe_sum("yoy_predepsano_czk")
    delta_zaci       = safe_sum("╬ö_zaci")
    delta_zap_czk    = safe_sum("╬ö_zaplaceno_czk")

    cur_zaci  = int(df["┼╜├ík┼» celkem"].sum()) if "┼╜├ík┼» celkem" in df.columns else 0
    cur_zap   = int(df["Zaplaceno"].sum()) if "Zaplaceno" in df.columns else 0
    cur_zap_czk = int(df["Zaplaceno_K─ì"].sum()) if "Zaplaceno_K─ì" in df.columns else 0

    paired = int(df["yoy_zaci"].notna().sum()) if "yoy_zaci" in df.columns else 0

    return {
        "paired":          paired,          # po─ìet term├¡n┼» s p├í┼Öem v 2025
        "hist_zaci":       hist_zaci,
        "hist_zaplaceno":  hist_zaplaceno,
        "hist_zap_czk":    hist_zap_czk,
        "hist_pred_czk":   hist_pred_czk,
        "delta_zaci":      delta_zaci,
        "delta_zap_czk":   delta_zap_czk,
        "cur_zaci":        cur_zaci,
        "cur_zap":         cur_zap,
        "cur_zap_czk":     cur_zap_czk,
    }
