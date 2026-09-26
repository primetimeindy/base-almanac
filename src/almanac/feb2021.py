"""Reproduce the Feb 2021 (Winter Storm Uri) Houston figures cited in the brief.

Run: .venv/bin/python -m almanac.feb2021
Assumption (label it everywhere): flat usage, i.e. monthly kWh spread evenly
over every 15-minute interval. Energy-only wholesale cost; no TDU, fees, taxes.
"""

from __future__ import annotations

import pandas as pd

from almanac import ercot_prices as ep

ZONE = "LZ_HOUSTON"


def flat_usage_cost(prices: pd.DataFrame, kwh: float) -> float:
    """$ cost of ``kwh`` spread evenly over the intervals in ``prices`` ($/MWh)."""
    mwh_per_interval = kwh / 1000 / len(prices)
    return float((prices["spp"] * mwh_per_interval).sum())


def month(df: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    t = df["interval_start"]
    return df[(t >= pd.Timestamp(start, tz=ep.TZ)) & (t < pd.Timestamp(end, tz=ep.TZ))]


def disagreement(df: pd.DataFrame) -> dict[str, int]:
    lz = df[df["location"].str.startswith("LZ_")]
    w = lz.pivot_table(index=["location", "interval_start"], columns="point_type", values="spp")
    d = (w["LZEW"] - w["LZ"]).abs()
    h = d.xs(ZONE, level="location")
    return {
        "zone_intervals_all_zones": len(d),
        "differ_all_zones": int((d > 0).sum()),
        "differ_ge_1c_all_zones": int((d >= 0.01).sum()),
        "houston_intervals": len(h),
        "houston_differ": int((h > 0).sum()),
        "houston_max_abs_diff": float(h.max()),
    }


def main() -> None:
    for year in (2021, 2024):
        print(year, disagreement(ep.load_year(year)))

    raw = month(ep.load_year(2021), "2021-02-01", "2021-03-01")
    raw = raw[raw["location"] == ZONE]
    ew = raw[raw["point_type"] == "LZEW"]
    lz = raw[raw["point_type"] == "LZ"]
    n = len(ew)
    per_interval_mwh = 2000 / 1000 / n
    print(f"\nFeb 2021 {ZONE}: {n} intervals per point type ({n / 96:.0f} days)")
    print(f"  LZEW mean ${ew['spp'].mean():,.2f}/MWh, LZ mean ${lz['spp'].mean():,.2f}/MWh")
    print(f"  2,000 kWh flat @ LZEW: ${flat_usage_cost(ew, 2000):,.2f}")
    print(f"  2,000 kWh flat @ LZ:   ${flat_usage_cost(lz, 2000):,.2f}")
    naive = float((raw["spp"] * per_interval_mwh).sum())  # both rows kept
    print(f"  naive (duplicates kept, same kWh/interval): ${naive:,.2f}")
    print(f"  implied rate for $158 / 2,000 kWh: {158 / 2000 * 100:.2f} c/kWh")

    storm = month(ew, "2021-02-15", "2021-02-19").copy()
    storm["hour"] = storm["interval_start"].dt.floor("h")
    hourly = storm.groupby("hour")["spp"].mean()
    print(f"\nFeb 15-18 LZEW: {len(storm)} intervals, {len(hourly)} hours")
    print(f"  intervals >= $500: {(storm['spp'] >= 500).sum()}  (> $500: {(storm['spp'] > 500).sum()})")
    print(f"  hours with mean > $500: {(hourly > 500).sum()}")
    print(f"  min interval ${storm['spp'].min():,.2f}, min hourly mean ${hourly.min():,.2f}")
    print("  below $500:")
    print(storm[storm["spp"] <= 500][["interval_start", "spp"]].to_string(index=False))
    for day, g in storm.groupby(storm["interval_start"].dt.date):
        print(f"  {day}: min ${g['spp'].min():,.2f} max ${g['spp'].max():,.2f} mean ${g['spp'].mean():,.2f}")
    ew_uri = month(ew, "2021-02-14", "2021-02-20")
    print(f"\nFeb 14-19 share of Feb LZEW cost: {ew_uri['spp'].sum() / ew['spp'].sum():.1%}")


if __name__ == "__main__":
    main()
