"""ERCOT 15-minute real-time settlement point prices (SPP) for load zones.

Source: ERCOT NP6-785-ER "Historical RTM Load Zone and Hub Prices", one xlsx
per year with one sheet per month, located via gridstatus.

Why we don't call ``gridstatus.Ercot().get_rtm_spp`` directly
-------------------------------------------------------------
Every load zone appears TWICE per 15-minute interval in the raw file, once per
``Settlement Point Type``:

* ``LZ``   -> RTSPP:   time-weighted average of the zone's (load-weighted) LMPs.
                        ERCOT uses it to settle *financial* transactions (DAM
                        energy and bilateral trades) at the zone.
* ``LZEW`` -> RTSPPEW: additionally weights each SCED run by state-estimated
                        zone Load (added by NPRR355). ERCOT uses it to settle
                        *physical* energy consumption (Adjusted Metered Load).

gridstatus (0.36) only tags energy-weighted rows when the column is named
``SettlementPointType``; this file names it ``Settlement Point Type``, so the
``_EW`` rename silently never happens, the type column is dropped, and callers
receive two indistinguishable ``LZ_HOUSTON`` rows per interval. Summing them
doubles any bill.

Choice: for customer bill modelling (metered household consumption) we keep
``LZEW`` because that is the price ERCOT applies to metered Load in the
Real-Time Energy Imbalance formula:
    RTEIAMT = (-1)*RTSPP*(DAM/trade energy) + (-1)*RTSPPEW*(SOG - AdjMeteredLoad)
(ERCOT "Settlement: Energy and PTP Obligations" training, 2024, slides 32-34, 44.)
``LZ`` is still available via ``kind="LZ"`` for hedge / DAM-vs-RT comparisons.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pandas as pd
import requests

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
TZ = "US/Central"
INTERVAL = pd.Timedelta(minutes=15)
BILLING_KIND = "LZEW"

RAW_COLS = [
    "Delivery Date",
    "Delivery Hour",
    "Delivery Interval",
    "Repeated Hour Flag",
    "Settlement Point Name",
    "Settlement Point Type",
    "Settlement Point Price",
]


def _raw_zip_path(year: int) -> Path:
    return DATA_DIR / "raw" / f"rtm_spp_{year}.zip"


def _parsed_path(year: int) -> Path:
    return DATA_DIR / f"rtm_spp_{year}.parquet"


def download_year(year: int) -> Path:
    """Download (once) the ERCOT yearly RTM load zone & hub SPP zip."""
    path = _raw_zip_path(year)
    if path.exists():
        return path
    import gridstatus
    from gridstatus.ercot import HISTORICAL_RTM_LOAD_ZONE_AND_HUB_PRICES_RTID

    doc = gridstatus.Ercot()._get_document(
        report_type_id=HISTORICAL_RTM_LOAD_ZONE_AND_HUB_PRICES_RTID,
        constructed_name_contains=f"{year}.zip",
    )
    resp = requests.get(doc.url, timeout=600)
    resp.raise_for_status()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(resp.content)
    return path


def parse_raw(frames: list[pd.DataFrame]) -> pd.DataFrame:
    """Normalise raw monthly sheets into one tidy frame, keeping the point type.

    * drops the non-data footer row ERCOT appends (no settlement point name)
    * Delivery Date arrives as a mix of str and datetime across sheets
    * builds a tz-aware ``interval_start``; the fall-back repeated hour is
      disambiguated with ``Repeated Hour Flag`` (N = first pass, still CDT).
    """
    df = pd.concat(frames, ignore_index=True)[RAW_COLS]
    df = df.dropna(subset=["Settlement Point Name", "Delivery Hour", "Delivery Interval"])
    date = pd.to_datetime(df["Delivery Date"].astype(str), format="mixed")
    local = (
        date
        + pd.to_timedelta(df["Delivery Hour"].astype(int) - 1, unit="h")
        + (df["Delivery Interval"].astype(int) - 1) * INTERVAL
    )
    is_dst = (df["Repeated Hour Flag"].astype(str).str.strip() == "N").to_numpy()
    start = local.dt.tz_localize(TZ, ambiguous=is_dst, nonexistent="raise")
    return pd.DataFrame(
        {
            "interval_start": start,
            "location": df["Settlement Point Name"].astype(str),
            "point_type": df["Settlement Point Type"].astype(str),
            "spp": df["Settlement Point Price"].astype(float),
        }
    ).reset_index(drop=True)


def load_year(year: int) -> pd.DataFrame:
    """All hubs and load zones for ``year``, both point types. Cached as parquet."""
    cached = _parsed_path(year)
    if cached.exists():
        return pd.read_parquet(cached)
    with zipfile.ZipFile(download_year(year)) as z:
        (name,) = [n for n in z.namelist() if n.endswith(".xlsx")]
        sheets = pd.read_excel(io.BytesIO(z.read(name)), sheet_name=None)
    df = parse_raw(list(sheets.values()))
    cached.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(cached)
    return df


def dedupe_load_zone(df: pd.DataFrame, kind: str = BILLING_KIND) -> pd.DataFrame:
    """Keep exactly one row per (location, interval) for load zones.

    Raises if the chosen point type itself still has duplicates, so a future
    format change fails loudly instead of doubling bills.
    """
    if kind not in {"LZ", "LZEW"}:
        raise ValueError(f"kind must be LZ or LZEW, got {kind!r}")
    out = df[df["location"].str.startswith("LZ_") & (df["point_type"] == kind)]
    dupes = out.duplicated(["location", "interval_start"])
    if dupes.any():
        raise ValueError(f"{int(dupes.sum())} duplicate {kind} rows after dedupe")
    return out.sort_values(["location", "interval_start"]).reset_index(drop=True)


def load_zone_prices(
    zone: str = "LZ_HOUSTON",
    years: tuple[int, ...] = (2021, 2024),
    kind: str = BILLING_KIND,
) -> pd.DataFrame:
    """15-minute RT SPP ($/MWh) for one load zone, one row per interval."""
    df = pd.concat([load_year(y) for y in years], ignore_index=True)
    out = dedupe_load_zone(df, kind=kind)
    return out[out["location"] == zone].reset_index(drop=True)
