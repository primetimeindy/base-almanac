# Almanac: offline geographic fleet test bench

Choose a synthetic communication-loss region and compare fixed-share control with constrained redistribution under identical starting conditions. Historical Hurricane Beryl track data supplies map context, not a weather-to-outage prediction. All battery locations, specifications, targets and faults are fictitious.

This is an independent hackathon prototype, not affiliated with or endorsed by Base Power, NOAA or ERCOT. It does not connect to Base systems, represent Base's controller, dispatch hardware or demonstrate field safety. AI is not in the control loop.

## Setup and run

Python 3.11 or later:

```sh
git clone https://github.com/primetimeindy/base-almanac.git
cd base-almanac
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m pytest tests -q
PYTHONPATH=src python -m almanac.geofleet_server --port 8767
```

Open **http://127.0.0.1:8767/geofleet**. After dependency installation, the demo uses bundled data and works offline without API keys, map tiles or model calls. The geofleet engine/server itself uses Python's standard library. The declared dependencies also support the separate historical ERCOT ingestion tools and their tests.

1. Choose Houston-side, wider southeast Texas, west, empty or all-device stress.
2. Run the paired experiment. Selected links disappear at simulated t=60 seconds; local commands may persist until their assumed expiry. Inspect t=90 seconds and recovery at t=360 seconds.
3. Compare unmet energy and setpoint changes. The latter is a complexity proxy, not a CPU benchmark.
4. Export the displayed result, including selection, assumptions, source hashes and evaluator-only ground truth. A receipt detects changes; it is not a signed attestation.

The amber rectangle is an operator-selected stress area, not an observed storm footprint. Historical timestamps do not drive the simulation clock. Empty and all-device cases expose the limits of redistribution.

## Other included demos

```sh
PYTHONPATH=src python -m almanac.replay_server --port 8766
```

- http://127.0.0.1:8766/fleet: non-geographic fleet fault experiment.
- http://127.0.0.1:8766/: separate Winter Storm Uri settlement-price replay, not an as-published feed or evidence of outage need.

Both servers bind to loopback. These are local demonstration servers, not production services. See [the experiment contract](docs/geofleet-demo.md) and [source attribution](docs/sources.md).

## Tests and boundaries

`python -m pytest tests -q` exercises deterministic control, reserve/energy constraints, command expiry, telemetry freshness, geospatial selection, source tampering, HTTP validation and exports. Four annual ERCOT cache tests skip when the optional full-year caches are absent. Those large caches are intentionally not bundled; the compact Uri replay and official Beryl source are included.

Results are individual synthetic scenarios, not statistical performance estimates, empirical savings or evidence of superiority over a real controller. No household loads, grid constraints, charging, efficiency losses, noisy telemetry, lost acknowledgements or unbounded local stop delays are modeled. There is no arbitrary user-controller adapter or production authentication. No public site deployment is included.

This repository is a sanitized source snapshot with independent publication history. Private planning material, local diagnostics and generated receipts are not included. No project license has been selected; public visibility alone does not grant a software license. Third-party data retains its source terms and attribution.
