# Offline controller check (CLI)

`almanac` runs one known local control policy against one registered **synthetic**
scenario, entirely offline, and reports what the run actually produced. It reuses the
existing engine (`almanac.fleet`), the existing trusted local policy seam
(`almanac.controller_contract`) and the existing synthetic geofleet fixtures. It adds no
new path around the enforced power/reserve envelope.

## Install-free usage

The console script is declared in `pyproject.toml`, so `almanac ...` works after a
reinstall. In any checkout, the module form works with no reinstall at all:

```sh
PYTHONPATH=src python3 -m almanac scenarios
```

Both spellings run the same code. Nothing here reaches the network.

## Commands

```sh
# What can be run: the closed scenario and policy registry, exit codes, limits.
PYTHONPATH=src python3 -m almanac scenarios

# Print a deterministic JSON verdict to stdout, write nothing.
PYTHONPATH=src python3 -m almanac check --scenario core --policy conservative-share

# Same evaluation, plus a JSON and a self-contained HTML report under out/.
PYTHONPATH=src python3 -m almanac run --scenario core --policy naive-target-fill --out out --html
```

`--scenario` accepts `all`, `core`, `empty`, `west`, `wide`: the same five synthetic
areas the map demo offers. `--policy` accepts only the known fixtures
`conservative-share`, `naive-target-fill` and `hold-zero`. **The CLI never takes an
import path from the user.** Loading your own module is still done through the existing
seam, which is explicit about the trust it requires:

```sh
PYTHONPATH=src:. python3 -m almanac.controller_contract \
  --policy examples.priority_policy:Policy --area core > policy-receipt.json
```

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Pass: no rejected action, no reserve violation, no realized overshoot, and the shortfall did not exceed the fixed-share reference. |
| 1 | Defined performance regression: candidate delivered-energy shortfall **exceeds** the built-in `baseline` fixed-share shortfall on the identical scenario. |
| 2 | `unsafe`: a rejected action, invalid policy result, policy exception, or measured reserve violation/overshoot. Exceptions inside `compare_policy`, including reference-engine failures, also receive this classification; it fails closed but does not reliably identify policy fault. |
| 3 | Unknown or malformed arguments, including an unregistered scenario or policy name. |
| 4 | Operational error escaping evaluation or report publication, such as fixture setup or output I/O failure. A machine-readable `almanac.cli-error.v1` object goes to **stderr** with the stage, error type and message, and stdout stays empty. Engine failures caught inside `compare_policy` instead produce exit 2; exit 4 is not a guarantee covering every engine failure. |

Observed behaviour of the three fixtures on `--scenario core`:

- `conservative-share` spends only the dispatch budget: **exit 0**.
- `naive-target-fill` ignores the unknown-output allowance and asks for the whole
  target, so the existing action validator rejects it at the first control step where
  silent devices still hold an unexpired command: **exit 2**. On `--scenario empty`
  there is no link failure, nothing is ever unknown, and the same policy passes. The
  failure is scenario-dependent, and the CLI says only what the run showed.
- `hold-zero` is inside the envelope and delivers nothing: **exit 1**.

## Reports

`run` publishes one immutable **generation** per check:

```
out/check-<scenario>-<policy>/
  current.json                              # pointer: which generation to read
  generations/<content_sha256>/report.json
  generations/<content_sha256>/report.html  # only with --html
```

A generation directory is named after the report `content_sha256` and is never edited in
place; the paths `run` prints are the ones inside it, so a reader that follows them cannot
mix generations. `current.json` names the generation a reader should use by default and is
replaced atomically as the last step. `out/` is gitignored. Both artifacts carry the
engine, scenario, policy and config
identifiers, the scenario SHA-256, and a `content_sha256` that is the digest of the
report body with the hash field removed, so it is verifiable and not self-referential.
Reports contain no timestamps, so repeating an identical run produces identical bytes.

Every report, including a failure report, also carries a `code` source manifest: the
SHA-256 of the current on-disk source bytes of exactly seven modules — `almanac.fleet` (engine),
`almanac.controller_contract` (controller adapter), `almanac.policies`, `almanac.geofleet`
(scenario builder), `almanac.replay` (the canonicalisation and digest helpers that define
receipt and hash semantics), `almanac.inspector` (receipt inspector) and `almanac.cli`
(this evaluator) — keyed by import name, plus a `manifest_sha256` over that
mapping. This is a **disk snapshot taken during evaluation, not an attestation of executed
code**. A long-running process may still execute earlier imported code after a file changes;
this legacy manifest does not detect that mismatch. Restart the demo from the reviewed,
unchanged checkout before a release demonstration. This is **not** a supply-chain identity: the interpreter, the standard library,
installed packages, the OS and every other module in the repository are outside it, as the
report's own `excludes` field states. Editing any of the seven changes `manifest_sha256` and therefore
`content_sha256`, even when every metric is unchanged. No filesystem path, environment
variable or build metadata is recorded. Matching source snapshots alone do not prove
matching in-memory code; byte reproducibility assumes unchanged sources and identical runs. The frozen legacy receipt schemas are untouched: this metadata exists
only in the CLI report.

Publication is atomic per generation. Both payloads are rendered and the report identity
is re-verified before anything is created; the artifacts are staged in a private temporary
directory and the generation becomes visible through a single directory rename, so an
interrupted or failing run cannot publish a truncated file or a JSON/HTML pair from
different generations. `current.json` is only replaced after the generation is re-read and
confirmed byte-for-byte, so a partial publication is never reported as success, and
earlier complete generations are left untouched.

Two details are honest limits rather than guarantees. An existing generation is validated
by its bytes, not trusted by its name: a damaged or JSON-only directory is republished
rather than reused. That repair path moves the damaged copy aside and restores it if the
swap fails, which is two renames, so only the ordinary first publication of a generation
is crash-atomic.

The HTML report is standalone: inline CSS only, no script, no stylesheet, font, image or
tile request. Source URLs appear as escaped text, never as a fetched resource.

A pass reports `target requested`, `delivered`, `shortfall`, `realized overshoot`,
`reserve violations`, `configured_reserve_preserved`, controller setpoint changes and
accepted commands, plus both built-in reference shortfalls. A rejection reports the
validator stage, the error type and message, and sets `measured` to `null`: the aborted
run returned no receipt, so no delivery numbers exist to report. It does **not** mean the
plant was never touched (see the limits below).

## Browser surface (loopback only)

The same evaluator is reachable from the map demo's **"4. Offline controller checks"**
section, served by the existing loopback-only `almanac.geofleet_server`. Two read-only
`GET` routes were added; nothing else about that server changed.

| Route | Returns |
| --- | --- |
| `/api/v1/check/registry` | Exactly `almanac.cli.listing()`: the closed scenario and policy registry, the exit-code table, the regression rule and the limits. No query parameters are accepted. |
| `/api/v1/check/run?scenario=<key>&policy=<key>` | One `almanac.check-envelope.v1` object holding a single `cli.evaluate()` report, plus `report_json` and `report_html` — the exact bytes `almanac run --html` would publish for that report. |

The page fills both selectors from `/api/v1/check/registry`, so the browser offers exactly
what the CLI registers and nothing else. One run produces one envelope: the displayed
verdict, the provenance rows and **both** download buttons come from that single
evaluation, so the JSON you keep and the HTML you open describe the same check and carry
the same `content_sha256`. The page never re-runs the evaluator to build a file.

Both routes sit behind the existing strict loopback gate: the `Host` header must be
`127.0.0.1:<port>`, an `Origin`, if present, must match it, and one request per
connection. `/api/v1/check/run` accepts a bounded query (96 characters) that must parse to
exactly one `scenario` and one `policy`, each a **key of the closed registry**. An import
path, a filesystem path, a blank, a duplicate or an unknown parameter is a `400` whose body
is a fixed diagnostic that never echoes the rejected value back. There is no upload, no
dynamic import, no arbitrary path, no outbound request and no repository file served by
these routes. A run takes the same single `RUN_SLOT` as the map experiment, so a concurrent
request gets `503` rather than queueing work.

Verdicts arrive as themselves: `pass`, `regression` and a safety verdict are all `200` with
the report's own `exit_code`, and a rejection carries `measured: null` with its
`rejection` block, exactly as the CLI reports it. An error escaping evaluation produces
a `503` (or `500`) carrying the `almanac.cli-error.v1` object with
`verdict: "operational-error"`, no report, and the page says no verdict is claimed.
The exit-code classification limitation applies here too: an exception caught inside
`compare_policy`, including a reference-engine failure, instead arrives as an `unsafe` report.

This surface is covered by `tests/test_check_http.py`, which asserts the envelope equals a
direct `cli.evaluate()` call for a real example of each verdict, that `report_json` and
`report_html` are byte-identical to `canonical(report)` and `cli.render_html(report)`, and
that the hardening above actually holds over real HTTP.

## Limits

- Every scenario is synthetic. Device positions, capacities, reserve, the 400 kW target
  and the communication-loss schedule are invented. Only the bundled NHC Beryl track is
  observed data, and it is storm-track geometry, not an outage or wind footprint.
- One scenario and one seeded plant per check. This is not an empirical performance
  estimate, a controller ranking, or evidence about any real fleet.
- `configured_reserve_preserved` means the simulated plant held every device at or above
  its configured reserve energy. It is **not** evidence that any household, load or site
  stayed powered.
- A rejected action means the harness refused **that proposal** before dispatching it.
  That is an attempted overcommit caught by the validator, **not** a simulated physical
  overshoot, and it says nothing about what a real device would have done.
- A rejection is not proof that nothing ran. A candidate may complete several accepted
  control ticks, whose commands really are dispatched into the simulated plant, before it
  proposes something the validator refuses. The aborted run returns no receipt, so that
  partial trajectory is unavailable: `measured` is `null`, `realized_overshoot_kw` is
  `null`, `partial_trajectory` says so explicitly, and no delivered energy, overshoot or
  reserve outcome is claimed for the policy — or ruled out. `realized_overshoot_kw` is
  `0` only when a run completed and measured it.
- Missing telemetry is not zero output: a silent device may still be discharging under an
  unexpired command, which is exactly the allowance `naive-target-fill` ignores.
- Policies run in process as trusted local imports. There is no sandbox, timeout, memory
  bound or determinism guarantee for arbitrary Python.
- The performance verdict compares a single number against one reference strategy on one
  scenario. It is not a claim of optimality, and passing is not a safety certification.
- Experimental work beyond this milestone is not in the CLI. There is no hidden flag, no
  placeholder control and no stub data for it.
