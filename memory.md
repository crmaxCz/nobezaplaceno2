# memory.md – NOBE Statistiky (nobezaplaceno2)

## YoY Porovnání s rokem 2025 (2026-06-03)

### Co bylo hotovo
- Přidána funkce porovnání aktuálních termínů s historickými daty roku 2025.
- Jednorázový scraper `scrape_2025.py` stáhl 651 termínů ze všech 14 poboček za celý rok 2025 a uložil je do `data_2025.csv`.
- Nový modul `historical.py` načítá CSV jednou při startu (`@st.cache_data`) a páruje termíny dle klíče `(iso_week, day_of_week, pobočka)`.
- `app.py` rozšířen o:
  - Toggle „📊 Srovnání s rokem 2025" v sidebaru (zobrazí se jen pokud existuje `data_2025.csv`)
  - Delta žáků vs. 2025 v metrice „Žáků celkem"
  - Souhrnný řádek pod metrikami s Kč deltou
  - Sloupec „📅 vs. 2025" v tabulce termínů (žáci Δ, % plateb Δ, Kč Δ, barevně)

### Odchylky od původního zadání
- Žádné zásadní odchylky. Všechny pobočky, celý rok 2025 jak bylo požadováno.

### Rozhodnutí
- **Párování**: ISO týden + den v týdnu + pobočka. Alternativa (přesné datum -365 dní) by nefungovala kvůli různým kalendářním distribucím poboček v různých letech.
- **Uložení**: CSV přímo v repozitáři (ne S3/DB) – jednoduché, Streamlit Cloud ho vidí ihned.
- **Credentials v scraper skriptu**: odstraněn `getpass` (nefunguje v PowerShellu), nahrazeno argparse (`-e`/`-p`), fallback na `input()`.

### Známé limitace / TODO
- Pokud pro daný (iso_week, weekday, pobočka) existuje více záznamů v 2025 (edge case), bere se první. Pravděpodobnost: velmi nízká.
- `data_2025.csv` je statický snapshot – nebude reflektovat případné opravy historických dat v systému.
- Scraper je třeba znovu spustit pro rok 2026 (konec roku nebo kdykoliv jindy pro refresh).
