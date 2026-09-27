# Public data and attribution

## Hurricane Beryl

Publisher: NOAA National Hurricane Center. Storm identifier: AL022024.

- Official source: https://www.nhc.noaa.gov/gis/best_track/al022024_best_track.kmz
- Included unchanged: `demo/data/beryl/al022024_best_track.kmz`.
- Acquisition metadata, byte count, SHA-256 and coverage: `demo/data/beryl/manifest.json`.
- Source link metadata: `demo/data/beryl/citations.json`.

The archive contains post-storm best-track estimates, not forecasts, observed outages or a wind footprint. The demo displays a limited time subset from the source. Battery coordinates and the selectable fault rectangles are separately generated fiction. NOAA/NHC attribution is not endorsement. The included archive and its embedded source notices are retained unchanged.

## Winter Storm Uri

Publisher: ERCOT. Report NP6-785-ER / 13061, Houston load-zone LZEW settlement prices.

- Catalog: https://www.ercot.com/misapp/servlets/IceDocListJsonWS?reportTypeId=13061
- Original archive: https://www.ercot.com/misdownload/servlets/mirDownload?doclookupId=814922832
- Included derived excerpt: `demo/data/uri.json`, containing source URLs, original archive/workbook identity, raw and parsed hashes, extraction timestamp, selected intervals and units.

The full-year raw ZIP and parsed parquet caches are not distributed here. Source metadata paths refer to the original extraction inputs, not bundled files. `src/almanac/ercot_prices.py` supplies the optional ingestion workflow. This demo uses retrospective settlement prices; interval-end availability is a simulation assumption because original publication latency is unknown. Prices are not outage observations or proof of a dispatch requirement.

The data remains attributable to its publishers and subject to their applicable terms; this repository does not relicense third-party material or claim endorsement. The project software is licensed under the [MIT License](../LICENSE); that license does not relicense third-party data.
