# Geographic controller fault bench

The map compares two example controllers, fixed equal shares and constrained redistribution, under the same synthetic starting state and faults. Neither is Base's actual controller. There is no claim of an unmet internal need or adoption.

## Experiment contract

A fictitious 100-device lattice is selected by inclusive longitude/latitude rectangles. Selected links are offline from relative t=60 to t=360 seconds in a 600-second experiment. No weather-to-outage causal model, wind footprint, severity estimate or probability is used. Beryl best-track coordinates are historical context only. Uri prices belong to a separate replay.

Each device has an assumed 5 kW maximum, 3 kWh capacity and 1 kWh reserve. Ninety start at 2 kWh and ten at 1.03 kWh. The target is a constant synthetic 400 kW, with 10-second control cadence. Hidden local expiry is seeded at 30 or 40 seconds; the controller knows only the guaranteed 40-second bound. These are simulation assumptions, not hardware specifications or measured guarantees.

The baseline renews fixed shares when telemetry is fresh, capped by power and usable energy. The constrained policy subtracts the worst-case output of unseen devices with unexpired commands, then distributes the remaining budget across fresh devices. Unknown devices receive no new commands. Fresh commands are assumed instantly acknowledged; local expiry cannot be renewed by duplicates. The controller cannot read the future fault schedule, hidden plant energy or per-device hidden expiry. Receipts include evaluator ground truth separately from controller inputs.

Reserve and energy invariants are conditional on the model: noiseless fresh telemetry, instantaneous acknowledged commands, guaranteed local expiry, discharge-only operation and perfect efficiency. Real devices violating those assumptions invalidate the modeled bounds.

## Run and API

From the repository root, with the environment described in the README:

```sh
PYTHONPATH=src python -m almanac.geofleet_server --port 8767
python -m pytest tests -q
```

Open http://127.0.0.1:8767/geofleet. Read-only GET routes under `/api/v1/geofleet/` are `preview?area=core`, `run?area=core` and `export?area=core`. Supported areas are `core`, `wide`, `west`, `empty`, `all`. Malformed or ambiguous queries are rejected. Source bytes are hash-checked and reparsed on every preview/run/export; missing or changed evidence prevents simulation. There is no hardware dispatch or controller-upload API.

Compare unmet energy with command renewals and setpoint changes, not just a success label. No fault does not remove reserve limitations, and redistribution cannot restore delivery while every link is absent. Receipts are deterministic change-detection artifacts, not third-party attestations or protection against someone rewriting both source and metadata.

Not covered: actual fleet locations or policies, outages, storm damage, real controller performance, arbitrary controller adapters, noisy/delayed telemetry, lost acknowledgements, household load, grid constraints, charging/losses, production deployment or field certification.
