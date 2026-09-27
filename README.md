<picture>
  <source media="(prefers-color-scheme: dark)" srcset="demo/assets/almanac-wordmark-dark.svg">
  <img src="demo/assets/almanac-wordmark-light.svg" alt="Almanac" width="240" height="52">
</picture>

# Almanac

**Compare battery-control policies when some batteries stop answering.**

Almanac is a local simulation for engineers exploring a simple question: can the remaining batteries supply missing power without spending their backup reserve? Choose an assumed link failure on a map, compare two example policies, inspect their limits, and download a reproducible JSON receipt.

The map uses the historical Hurricane Beryl storm track. Battery locations, specifications, target power and link failures are synthetic. This is not an outage prediction or Base's controller. It is an independent hackathon prototype, not affiliated with or endorsed by Base Power, NOAA or ERCOT. AI assisted development; no AI runs in the control loop.

## Quickstart

Use Python 3.11 or 3.12 and a current Chrome, Firefox or Safari browser. Run these commands from the repository root:

```sh
git clone https://github.com/primetimeindy/base-almanac.git
cd base-almanac
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip install --no-deps -e .
python -m pytest tests -q
python -m almanac.geofleet_server --port 8767
```

Open **http://127.0.0.1:8767/geofleet**. Stop the server with Ctrl-C.

No API keys, accounts, environment variables or secrets are required. There is no `.env` setup or sample credential file. After installation, the map demo runs offline with bundled data, no map tiles or model calls. The engine and local server use only Python's standard library. To try just the demo without installing the optional data-analysis dependencies:

```sh
PYTHONPATH=src python3 -m almanac.geofleet_server --port 8767
```

Use the editable source installation above, not a standalone wheel: the server reads bundled assets relative to this checkout. The dependency lock includes exact versions and hashes, excludes releases after September 25, 2026, and is not a security certification. Refresh it deliberately rather than silently upgrading dependencies.

Reproducibility limitation: the lock hash-pins the installed runtime and test dependencies, but `pip install --no-deps -e .` resolves its setuptools build backend in an isolated build environment outside that lock. The build backend and build environment are therefore **not** hash-locked, so this is hash-locked dependencies, not a fully reproducible installation.

## Try the core loop

1. **Map:** choose Houston-side cluster. The amber rectangle selects fictitious batteries, not places known to have lost power.
2. **Assumed failure:** their communication links disappear at simulated second 60 and return at second 360.
3. **Example policies:** compare fixed shares with redistribution. Fixed shares does not move unused capacity between devices; redistribution does.
4. **Constraints:** inspect the time slider. Silent batteries may still discharge until their assumed stop time, so their possible output remains in the power budget. The local plant protects reserve for both policies.
5. **Receipt:** download the displayed JSON, including source hashes, inputs, actions, results and the no-fault comparison.

Then try **Empty selection** and **All-device stress**. Empty selection shows that policies can differ even without a link failure: ten batteries start near reserve. All-device stress shows that redistribution cannot help while every device is unavailable and all outstanding commands have expired.

The full-run energy difference is **not all fault-recovery benefit**. The UI also shows a no-fault run with identical starting conditions and a difference of differences. Reserve use and faults interact, so that subtraction is not an isolated causal attribution. Each result is one synthetic scenario, not an empirical performance estimate. Setpoint changes are a complexity proxy, not a CPU benchmark.

## Try your own policy locally

A small versioned Python protocol accepts fresh observations and returns proposed powers. The included third policy fills devices in ID order rather than sharing equally:

```sh
PYTHONPATH=src:. python -m almanac.controller_contract \
  --policy examples.priority_policy:Policy --area core > policy-receipt.json
```

The receipt identifies `priority-order`, version `1.0.0`, and includes both built-in references and the candidate result. See [the integration guide](docs/integration.md) for the observation/action contract and tests.

**Only import code you trust.** This is an in-process local seam, not a sandbox or a physical-device adapter. HTTP never accepts a module path or uploaded code. Invalid returned actions abort the run without generating a success receipt. This does not guarantee the safety, determinism or termination of arbitrary Python code.

## Check a policy from the command line

An offline check runs one known policy against one synthetic scenario and prints a
deterministic JSON verdict. No reinstall is needed for the module form:

```sh
PYTHONPATH=src python3 -m almanac scenarios
PYTHONPATH=src python3 -m almanac check --scenario core --policy conservative-share
PYTHONPATH=src python3 -m almanac run --scenario core --policy naive-target-fill --out out --html
```

Exit codes are 0 pass, 1 defined performance regression, 2 safety verdict (a rejected action, an invalid policy result, or a policy that raised), 3 unknown or malformed arguments, 4 operational error. Errors escaping evaluation or report publication produce exit 4 with a machine-readable `almanac.cli-error.v1` object on stderr and empty stdout. However, exceptions inside `compare_policy`, including reference-engine failures, are currently caught as `unsafe` (exit 2), not reliably distinguished from policy failures; this fails closed but does not establish policy fault. The included `naive-target-fill` fixture ignores the output that silent devices may still be producing, so the existing action validator rejects it before dispatch: that is an attempted overcommit caught by the harness, not a simulated physical overshoot. `run` also writes a self-contained HTML report that makes no external requests. See [the controller-check guide](docs/controller-check.md), including its Limits section.

## Tech stack and implemented architecture

- Frontend: plain HTML, CSS, JavaScript and SVG. No frontend build step or framework.
- Backend: Python standard-library HTTP server bound to `127.0.0.1`.
- Simulation: rational energy accounting, deterministic seeded local expiry, ten-second control cadence.
- Tests: pytest. Optional ERCOT ingestion uses gridstatus, pandas, NumPy, PyArrow and openpyxl.
- No database, cloud service, authentication, billing, hardware connection or model endpoint.

```text
Browser: select area, run, inspect, export
    | read-only JSON requests
    v
Local HTTP server --> verified bundled NHC track + synthetic positions
    |
    v
Scenario --> fixed shares / redistribution --> simulated plant --> receipt
                  ^ current telemetry only          |
                  +---------------------------------+

Trusted local CLI import --> observation v1 --> candidate policy
                              action v1 <--       |
                              validated powers --> same simulated plant
```

The evaluator knows the fault schedule and plant state. Controllers receive neither the future schedule nor hidden plant truth. Exported receipts contain evaluator-only information for inspection, not as controller input.

## API and reproducibility

Read-only local endpoints used by the frontend:

| Route | Returns |
| --- | --- |
| `GET /api/v1/geofleet/preview?area=core` | Verified source, synthetic positions and selected IDs |
| `GET /api/v1/geofleet/run?area=core` | Paired experiment and no-fault comparison |
| `GET /api/v1/geofleet/export?area=core` | Same deterministic report as an attachment |
| `GET /api/v1/contract` | Implemented integration contract and CLI command |
| `GET /api/v1/check/registry` | The CLI's closed scenario/policy registry, exit codes and limits |
| `GET /api/v1/check/run?scenario=core&policy=hold-zero` | One offline controller check: the report plus the exact JSON and HTML bytes of its two downloads |
| `GET /health` | Process health, not a source-validation certificate |

Areas: `core`, `wide`, `west`, `empty`, `all`. The check route takes registry **keys** only — never an import path, a filesystem path or uploaded code — and a rejected value is not echoed back; see [the controller-check guide](docs/controller-check.md). No scenario upload or arbitrary/user-supplied policy execution over HTTP; bundled policies are evaluated through the closed registry. Missing or altered cached source blocks preview/run/export. The browser clears old results when selection or execution changes and exports exactly the displayed response. A receipt hash detects changes, not forgery by someone rewriting the report and hash.

Tests cover deterministic policies, observation boundaries, rejected actions, reserves, energy accounting, expiry, telemetry freshness, selection, source tampering, HTTP framing, concurrency and exports. Four optional full-year ERCOT cache tests skip without the separate large caches. A GitHub Actions workflow runs the local suite on pushes and pull requests; its remote status must be checked after publication.

## Data and assumptions

- **Observed context:** the unchanged official NHC Beryl best-track archive, metadata and SHA-256 are in `demo/data/beryl/`. Display window: July 8 through July 9, 2024, UTC. This is storm-track geometry, not a wind or outage footprint.
- **Synthetic experiment:** 100 generated points, 5 kW maximum per battery, 3 kWh capacity, 1 kWh reserve. Ninety start at 2 kWh, ten at 1.03 kWh. The requested target is 400 kW for 600 seconds. None are Base specifications.
- **Separate historical replay:** `demo/data/uri.json` is a retrospective ERCOT Houston settlement-price excerpt. It is not contemporaneous Beryl evidence. The full-year caches are not bundled.

See [source attribution](docs/sources.md) and [the experiment contract](docs/geofleet-demo.md).

## Known limitations and next steps

In the legacy map/check engine, guaranteed local expiry, instant acknowledgements and exact noiseless fresh telemetry are assumptions, not measured capabilities. The simulated local plant enforces reserve. There are no efficiency losses, household loads, charging, ramp limits or grid constraints. No empirical savings, production superiority, customer adoption or field-safety claim is made.

Both HTTP services are local demonstration tools, not public production services. They limit concurrent workers and reject ambiguous inputs, but provide no authentication or operational availability guarantee. Do not expose them publicly. Trusted plugins are not isolated or timed out. Python 3.13 and noneditable wheel distribution are not part of the verified quickstart.

The separate, implemented [experimental uncertainty surface](docs/uncertainty.md) appears in section 5 of the same page. It models synthetic lost/delayed acknowledgements, telemetry backfill and bounded-buffer overflow, with no safety verdict, hardware adapter or integration into legacy check receipts.

Next steps, not implemented: validate assumptions with engineers, add recorded noisy/delayed telemetry, test a broader scenario set, then design a separate production transport boundary if warranted. No real controller credentials are needed for this prototype.

The older non-geographic fleet and separate Uri price replay remain available:

```sh
python -m almanac.replay_server --port 8766
```

Open `/fleet` or `/` on that server. They are separate examples, not additional stages of the map workflow.

## Submission materials

[Submission description and recording checklist](docs/submission.md) · [Live narration script](docs/demo-script.md)

Built during the Base Power x AITX hackathon, Sept 25 to 27, 2026. See [docs/BUILD_LOG.md](docs/BUILD_LOG.md). The software in this repository is licensed under the [MIT License](LICENSE). Third-party data retains its own source terms and attribution.
