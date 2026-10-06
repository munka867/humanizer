# Data request: what to export and how

Goal: as many years as you can get of **1-minute (preferred) or 5-minute** bars for **MES** (and ES if
possible; NQ/MNQ later), covering **both regular (RTH 09:30-16:00 ET) and electronic (ETH, nearly 24h) hours**.
Put files in `data/raw/`. This sandbox cannot download market data, so nothing here has been verified by us.

## What to get

1. **Individual contracts, not one stitched "continuous" series** if you can: e.g. MESH5, MESM5, MESU5, MESZ5
   (quarterly: H=Mar, M=Jun, U=Sep, Z=Dec). We check/handle rolls ourselves (see `docs/DATA_QUALITY.md`).
   MES began trading in May 2019. ES is much older and tracks the same index, so ES history is a good way to get more years.
2. If you can only get a continuous series, say whether it is **unadjusted or back-adjusted** and how it rolls.
   Unadjusted is preferred; back-adjusted changes price levels.
3. Bar size 1 minute; if only 5 minute is available, that is fine (tell us). Always include volume.
4. Export **timestamps in UTC if the tool allows it**. Otherwise note the timezone shown (usually exchange time
   America/Chicago, or your local time). Do not convert by hand.
5. Note whether the time shown is when the bar **opens** or **closes**. If unsure, export one known bar
   (e.g. the 09:30 ET open) and tell us what it shows. We will not guess.

## Where (honest notes, check current terms yourself)

- **IBKR TWS** (paper or live account; market-data subscriptions for CME may be required, and Canadian
  accounts may need to subscribe separately): open a chart for the contract, set bar size 1 min, "Use RTH" OFF
  (to include ETH), pick the end date, and export via the chart / use the historical-data tools. IBKR limits how
  much 1-minute history a single request returns and how far back small bars go; expired contracts may have
  limited or no 1-minute history. We cannot verify current limits from here. Expect to fetch in chunks and
  stitch, or accept fewer years.
- **IBKR Client Portal / API**: historical data endpoints have similar pacing and size limits. Only retrieve
  data; no order placement is needed or wanted for this project.
- **TradingView**: CSV export of chart data exists on paid plans (availability, bars-per-export limits and the
  `time` format depend on your plan and may change; check the app). Prefer the individual-contract symbols
  (e.g. `CME_MINI:MES1!` is a continuous series; the quarterly symbol such as `MESH2025` is a single contract).
- **Other vendors / exports**: any CSV with time, open, high, low, close, volume works. Data purchased or
  downloaded must be allowed for your use; do not commit data you are not permitted to redistribute if the repo is public.

## File naming

`data/raw/<SYMBOL>_<bar>_<contract or continuous-type>.csv`, for example:
`data/raw/MES_1m_MESZ4.csv`, `data/raw/MES_1m_MESH5.csv`, `data/raw/ES_5m_continuous_unadjusted.csv`.
Do not rename columns or edit the files by hand. Never put the word SYNTHETIC in a real file name.

## Check what you got

```
pip install -r requirements.txt
python scripts/check_data.py data/raw/MES_1m_MESZ4.csv --freq 1min --tz America/New_York --label open --contract MESZ4
```
- `--tz` is only needed when timestamps have no UTC offset (then tell us the real zone; not needed for UTC or epoch values).
- `--label close` if the timestamp marks the bar close (then `--freq` is used to shift it back).
- `--sort` for newest-first files; `--instrument ES` etc.; `--out report.md` saves the report.
- It exits with an error if the file cannot be read unambiguously, for example local times with no
  timezone, or a local time that falls in the repeated hour when clocks go back. In that case re-export in UTC.
- Send us (or commit) the report next to the files. Warnings about gaps, holiday dates and rolls are normal to
  review; errors (duplicates, bad OHLC, wrong bar size) need a fresh export.
