import pandas as pd
import pytest

from almanac import ercot_prices as ep


def _raw(rows):
    return pd.DataFrame(rows, columns=ep.RAW_COLS)


def _two_type_frame():
    rows = []
    for interval in (1, 2):
        rows.append(["02/15/2021", 1, interval, "N", "LZ_HOUSTON", "LZ", 9000.0])
        rows.append(["02/15/2021", 1, interval, "N", "LZ_HOUSTON", "LZEW", 9010.0])
    return ep.parse_raw([_raw(rows)])


def test_raw_has_two_rows_per_interval_until_deduped():
    df = _two_type_frame()
    assert df.duplicated(["location", "interval_start"]).sum() == 2
    out = ep.dedupe_load_zone(df)
    assert not out.duplicated(["location", "interval_start"]).any()
    assert (out["spp"] == 9010.0).all()  # LZEW is the billing row


def test_dedupe_raises_if_chosen_type_still_duplicated():
    df = _two_type_frame()
    with pytest.raises(ValueError, match="duplicate"):
        ep.dedupe_load_zone(pd.concat([df, df]))


def test_fall_back_repeated_hour_is_disambiguated():
    rows = [
        ["11/07/2021", 2, 1, "N", "LZ_HOUSTON", "LZEW", 20.0],
        ["11/07/2021", 2, 1, "Y", "LZ_HOUSTON", "LZEW", 21.0],
    ]
    df = ep.parse_raw([_raw(rows)])
    assert df["interval_start"].nunique() == 2
    utc = df["interval_start"].dt.tz_convert("UTC").sort_values().tolist()
    assert utc[1] - utc[0] == pd.Timedelta(hours=1)


def test_footer_junk_row_dropped():
    rows = [
        ["01/01/2021", 1, 1, "N", "LZ_HOUSTON", "LZEW", 20.0],
        ["2022-01-01", None, None, 10, None, "08:03:41", None],
    ]
    assert len(ep.parse_raw([_raw(rows)])) == 1


def _cached(year):
    if not ep._parsed_path(year).exists():
        pytest.skip(f"no cached data for {year}; run load_year({year})")
    return ep.load_year(year)


@pytest.mark.network
@pytest.mark.parametrize("year,n", [(2021, 365 * 96), (2024, 366 * 96)])
@pytest.mark.parametrize("kind", ["LZ", "LZEW"])
def test_real_data_one_row_per_zone_interval(year, n, kind):
    df = _cached(year)
    lz = df[df["location"].str.startswith("LZ_")]
    # The trap: raw data has two rows per load-zone interval.
    assert lz.duplicated(["location", "interval_start"]).any()
    out = ep.dedupe_load_zone(df, kind=kind)
    assert not out.duplicated(["location", "interval_start"]).any()
    counts = out.groupby("location").size()
    assert len(counts) == 8 and (counts == n).all()
    for _, g in out.groupby("location"):
        steps = g["interval_start"].dt.tz_convert("UTC").diff().dropna()
        assert (steps == ep.INTERVAL).all()
