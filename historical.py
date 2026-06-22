"""
historical.py - Loading and matching historical data from 2025
=============================================================
Used from app.py for YoY (year-over-year) comparison.
Column names in the live DataFrame use Czech strings (Termin, Pobocka, etc.)
We resolve them dynamically to survive any future encoding issues.
"""

from pathlib import Path

import pandas as pd
import streamlit as st

CSV_PATH = Path(__file__).parent / "data_2025.csv"

# ── Column name resolution helpers ────────────────────────────────────────────
# We locate Czech-named columns by a short ASCII prefix/substring so this
# module stays encoding-safe even if the source file is re-encoded.

def _col(df: pd.DataFrame, *candidates: str) -> str | None:
    """Return the first column name whose lower-case form contains any candidate."""
    lower = {c.lower(): c for c in df.columns}
    for cand in candidates:
        for lk, orig in lower.items():
            if cand in lk:
                return orig
    return None


# ── Data loading ──────────────────────────────────────────────────────────────

@st.cache_data(show_spinner=False)
def load_historical_data() -> pd.DataFrame:
    """
    Load data_2025.csv once and cache it.
    Returns an empty DataFrame if the file does not exist.
    """
    if not CSV_PATH.exists():
        return pd.DataFrame()

    df = pd.read_csv(CSV_PATH, dtype=str)

    int_cols = [
        "iso_week", "day_of_week",
        "zaci_celkem", "zaplaceno", "nezaplaceno",
        "zaplaceno_czk", "predepsano_czk", "nedostavili",
    ]
    for col in int_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)

    return df


# ── Matching ──────────────────────────────────────────────────────────────────

def _parse_termin_dt(termin_str: str) -> pd.Timestamp | None:
    """Parse 'DD.MM.YYYY HH:MM' or 'DD.MM.YYYY' into a Timestamp."""
    try:
        return pd.to_datetime(str(termin_str).split(" ")[0], format="%d.%m.%Y")
    except Exception:
        return None


def find_yoy_match(
    df_hist: pd.DataFrame,
    termin_str: str,
    pobocka: str,
) -> dict | None:
    """
    For a current term (date string + branch name) find the matching record
    in the historical CSV using key (iso_week, day_of_week, pobocka).

    Falls back to closest day_of_week within the same iso_week + pobocka
    when no exact day match exists (handles year-to-year schedule shifts).

    Returns a dict with historical values, or None if no match exists.
    """
    if df_hist.empty:
        return None

    dt = _parse_termin_dt(termin_str)
    if dt is None:
        return None

    iso        = dt.isocalendar()
    iso_week   = int(iso[1])
    day_of_wk  = int(iso[2])

    # ── 1. Exact match: same iso_week + day_of_week + pobocka ─────────────
    mask_exact = (
        (df_hist["iso_week"]    == iso_week) &
        (df_hist["day_of_week"] == day_of_wk) &
        (df_hist["pobocka"]     == pobocka)
    )
    matches = df_hist[mask_exact]

    # ── 2. Fallback: same iso_week + pobocka, pick closest day_of_week ────
    if matches.empty:
        mask_week = (
            (df_hist["iso_week"] == iso_week) &
            (df_hist["pobocka"]  == pobocka)
        )
        matches_week = df_hist[mask_week]
        if not matches_week.empty:
            matches_week = matches_week.copy()
            matches_week["_day_diff"] = (matches_week["day_of_week"] - day_of_wk).abs()
            matches = matches_week.sort_values("_day_diff").head(1)

    if matches.empty:
        return None

    row = matches.iloc[0]
    return {
        "hist_date":           row.get("date", ""),
        "hist_zaci":           int(row.get("zaci_celkem", 0)),
        "hist_zaplaceno":      int(row.get("zaplaceno", 0)),
        "hist_nezaplaceno":    int(row.get("nezaplaceno", 0)),
        "hist_zaplaceno_czk":  int(row.get("zaplaceno_czk", 0)),
        "hist_predepsano_czk": int(row.get("predepsano_czk", 0)),
        "hist_nedostavili":    int(row.get("nedostavili", 0)),
    }


# ── Extend DataFrame with YoY columns ────────────────────────────────────────

def build_yoy_columns(df: pd.DataFrame, df_hist: pd.DataFrame) -> pd.DataFrame:
    """
    Add historical value columns and deltas to df.
    When df_hist is empty or no match exists for a row, values are NaN/None.

    Added columns (ASCII names to avoid encoding issues):
      yoy_date            - date from 2025
      yoy_zaci            - total students 2025
      yoy_zaplaceno       - paid students 2025
      yoy_zap_pct         - % paid 2025
      yoy_zaplaceno_czk   - CZK paid 2025
      yoy_predepsano_czk  - CZK prescribed 2025
      yoy_nedostavili     - no-shows 2025

      delta_zaci          - diff students (current - 2025)
      delta_zap_pct       - diff % paid
      delta_zaplaceno_czk - diff CZK paid
    """
    if df_hist.empty:
        return df

    df = df.copy()

    # Initialise output columns
    yoy_col_defaults: dict = {
        "yoy_date":            None,
        "yoy_zaci":            pd.NA,
        "yoy_zaplaceno":       pd.NA,
        "yoy_zap_pct":         pd.NA,
        "yoy_zaplaceno_czk":   pd.NA,
        "yoy_predepsano_czk":  pd.NA,
        "yoy_nedostavili":     pd.NA,
        "delta_zaci":          pd.NA,
        "delta_zap_pct":       pd.NA,
        "delta_zaplaceno_czk": pd.NA,
    }
    for col, default in yoy_col_defaults.items():
        df[col] = default

    # Resolve Czech column names dynamically
    col_termin   = _col(df, "term")          # "Termin" / "Termín"
    col_pobocka  = _col(df, "pobo")          # "Pobocka" / "Pobočka"
    col_zaci     = _col(df, "celkem")        # "Žáků celkem"
    col_zapl     = _col(df, "zaplaceno_k")   # "Zaplaceno_Kč"

    has_city_col = col_pobocka is not None and col_pobocka in df.columns

    for idx, row in df.iterrows():
        # Branch name
        if has_city_col:
            pobocka = str(row.get(col_pobocka, "") or "")
        else:
            pobocka = ""

        # Date string for this term
        termin_str = str(row[col_termin]) if col_termin else ""
        if not termin_str or termin_str == "nan":
            continue

        match = find_yoy_match(df_hist, termin_str, pobocka)
        if match is None:
            continue

        h_zaci     = match["hist_zaci"]
        h_zap      = match["hist_zaplaceno"]
        h_zap_czk  = match["hist_zaplaceno_czk"]
        h_pred_czk = match["hist_predepsano_czk"]

        # Current values
        cur_zaci    = int(row.get(col_zaci, 0) or 0) if col_zaci else 0
        cur_zap     = int(row.get("Zaplaceno", 0) or 0)
        cur_zap_czk = int(row.get(col_zapl, 0) or 0)  if col_zapl else 0

        h_zap_pct   = h_zap   / max(h_zaci, 1) * 100
        cur_zap_pct = cur_zap / max(cur_zaci, 1) * 100

        df.at[idx, "yoy_date"]            = match["hist_date"]
        df.at[idx, "yoy_zaci"]            = h_zaci
        df.at[idx, "yoy_zaplaceno"]       = h_zap
        df.at[idx, "yoy_zap_pct"]         = round(h_zap_pct, 1)
        df.at[idx, "yoy_zaplaceno_czk"]   = h_zap_czk
        df.at[idx, "yoy_predepsano_czk"]  = h_pred_czk
        df.at[idx, "yoy_nedostavili"]     = match["hist_nedostavili"]
        df.at[idx, "delta_zaci"]          = cur_zaci - h_zaci
        df.at[idx, "delta_zap_pct"]       = round(cur_zap_pct - h_zap_pct, 1)
        df.at[idx, "delta_zaplaceno_czk"] = cur_zap_czk - h_zap_czk

    return df


# ── Aggregate YoY summary ─────────────────────────────────────────────────────

def yoy_summary(df: pd.DataFrame) -> dict:
    """
    Returns aggregate YoY values from df (which must already contain yoy_/delta_ cols).
    Used for st.metric() deltas.
    """
    def safe_sum(col: str) -> int | None:
        if col not in df.columns:
            return None
        vals = pd.to_numeric(df[col], errors="coerce").dropna()
        return int(vals.sum()) if not vals.empty else None

    # Resolve Czech column names
    col_zaci    = _col(df, "celkem")
    col_zapl    = _col(df, "zaplaceno_k")

    hist_zaci       = safe_sum("yoy_zaci")
    hist_zaplaceno  = safe_sum("yoy_zaplaceno")
    hist_zap_czk    = safe_sum("yoy_zaplaceno_czk")
    hist_pred_czk   = safe_sum("yoy_predepsano_czk")
    delta_zaci      = safe_sum("delta_zaci")
    delta_zap_czk   = safe_sum("delta_zaplaceno_czk")

    cur_zaci    = int(df[col_zaci].sum())    if col_zaci and col_zaci in df.columns else 0
    cur_zap     = int(df["Zaplaceno"].sum()) if "Zaplaceno" in df.columns else 0
    cur_zap_czk = int(df[col_zapl].sum())   if col_zapl and col_zapl in df.columns else 0

    paired = int(df["yoy_zaci"].notna().sum()) if "yoy_zaci" in df.columns else 0

    return {
        "paired":         paired,
        "hist_zaci":      hist_zaci,
        "hist_zaplaceno": hist_zaplaceno,
        "hist_zap_czk":   hist_zap_czk,
        "hist_pred_czk":  hist_pred_czk,
        "delta_zaci":     delta_zaci,
        "delta_zap_czk":  delta_zap_czk,
        "cur_zaci":       cur_zaci,
        "cur_zap":        cur_zap,
        "cur_zap_czk":    cur_zap_czk,
    }
