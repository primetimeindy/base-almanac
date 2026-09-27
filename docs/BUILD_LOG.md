# Build log

What this document is: a timeline of the work that is verifiable **from this public
repository alone**. Every commit line below was read from this repository's git history.
Nothing here is reconstructed from private planning material, private history or any
other repository, and no earlier development event is claimed.

Timestamps are UTC. Local commit times were recorded in US Central (UTC-5).

## Event window

**User-attested context (not git-verified here):** the work was done during the Base
Power x AITX hackathon, September 25 to 27, 2026. This public repository's earliest
commit is dated September 26, 2026, so the September 25 start date is the author's
attestation about the event, **not** a claim of a September 25 commit in this history.

## Commit timeline (this repository)

| UTC timestamp | Commit | Change |
| --- | --- | --- |
| 2026-09-26 11:42:56 | `e744632` | *Publish sanitized Almanac hackathon source snapshot.* Initial public commit: 28 files, +2113 lines. Simulation engine (`fleet`, `geofleet`, `replay`, `ercot_prices`, `feb2021`), the two loopback servers, the three demo pages, the cached NOAA Beryl evidence, and the first seven test modules. |
| 2026-09-26 17:12:41 | `85d65a4` | *Add checked local policy integration and honest demo comparisons.* 26 files, +1886 / -58. Trusted local policy protocol (`controller_contract`, `examples/priority_policy.py`, `docs/integration.md`), shared hardened HTTP base (`local_http`), the no-fault comparison and difference-of-differences framing, brand assets, `requirements.lock`, and the GitHub Actions test workflow. |
| 2026-09-26 17:41:50 | `c3a10b7` | *Finish trace-backed battery inspector and state visualization.* 5 files, +248 / -12. `inspector` module, per-device state rendering in `demo/geofleet.html`, and `tests/test_inspector.py`. |
| 2026-09-26 18:00:42 | `a1b9a0c` | *Detach preview results from module-global AREAS.* 1 file, +6 / -3. The pre-T0 defect fix described below. |
| 2026-09-26 18:00:59 | `a8a11b5` | *Regressions for shared mutable geofleet preview state.* 1 file, +64. `tests/test_geofleet_isolation.py`, written before the fix and run against `c3a10b7`. |
| 2026-09-26 18:01:08 | `7701887` | *Add license and documented public build timeline.* 5 files, +119 / -1. `LICENSE`, the first version of this build log, README license and reproducibility text, `.gitignore` additions, and a workflow comment. |
| 2026-09-27 01:34:45 | `bcb181a` | *ALM-T0: clarify verification evidence.* 1 file, +20 / -11. Build-log truthfulness corrections only; no code or test change. |
| 2026-09-27 01:36:15 | `11edff3` | *ALM-T2: freeze legacy receipt bytes with exact hashes.* 1 file, +65. `tests/test_legacy_receipts.py`. |
| 2026-09-27 01:44:03 | `1968399` | *ALM-T3: offline controller-check CLI with deterministic reports.* 7 files, +548 / -4. `almanac.cli`, `almanac.__main__`, `almanac.policies`, `tests/test_cli.py`, `OvercommitRejected`, `geofleet.scenario_for`, and the console-script declaration. |
| 2026-09-27 01:44:08 | `68345d9` | *ALM-T6: document the controller check with runnable examples.* 2 files, +114. `docs/controller-check.md` and a README section. |

The commit that added these four rows is not listed in its own table.

## 2026-09-26: pre-T0 defect fix (shared mutable preview state)

Found and fixed before the licensing/documentation work, under test-first discipline.

**Defect.** `geofleet.preview()` returned the module-global `AREAS` dict itself as
`result['areas']`, and the nested list object `AREAS[area]['bounds']` as
`result['bounds']`, with no copy at any level. Because `preview()` both validates the
requested area against `AREAS` and selects devices using that same nested bounds list,
one caller mutating a returned result could:

1. rewrite the `areas`/`bounds` of every other result in the process;
2. rewrite the module-global allowlist and rectangle, changing `selected_ids` and the
   derived fault device list for all later `preview`/`experiment` calls;
3. invalidate an already-issued receipt: `experiment()` embeds the preview dict as
   `report['selection']` before stamping `receipt_sha256`, so a later unrelated preview
   mutation altered the earlier report in place and its stored hash no longer matched
   `digest(body)`.

`positions()` and `load_source()` already build fresh objects per call, so `AREAS` was
the only aliasing leak.

**Process.** Three regression tests were written first, in
`tests/test_geofleet_isolation.py`, and run against `c3a10b7`. All three failed. Each
stopped at an early assertion, so what the red run actually demonstrated was: two
previews returning the same `areas` object, a module-global `AREAS` mutated through a
returned result, and a prior report's embedded `selection` content mutated by a separate
preview. The red run did not reach the later assertions in those tests. The subsequent
green run additionally exercises them: that a later `preview` still returns the original
bounds and `selected_ids`, that a mutated area key is still rejected as not allowlisted,
and that the earlier report's `receipt_sha256` still matches `digest(body)`. The fixture
snapshots and restores `AREAS` in place so a failing assertion cannot leak mutated
global state into other tests.

**Fix.** `preview()` now returns `deepcopy(AREAS)` and a copy of the selected bounds,
and selects against the copy. Serialized output, receipt hashes and HTTP responses are
unchanged; the loopback servers only serialize results, so no live HTTP behaviour
changed.

**Result.** Whole suite went from 135 passed / 4 skipped to 138 passed / 4 skipped.

## 2026-09-26: ALM-T0, license and public build timeline

- Added `LICENSE`: MIT, copyright 2026 Easton Evans. This licenses the **software** in
  this repository. Bundled third-party data keeps its own source terms and attribution
  (see [sources.md](sources.md)).
- Added this build log and linked it from the README.
- `.gitignore`: retained the existing `.env` rule, which was already present, and added
  `*.token` and `out/`. All existing rules were kept.
- Added an explicit reproducibility limitation to the README: the lock hash-pins
  installed runtime and test dependencies, but the editable install resolves its
  setuptools build backend in an isolated build environment outside that lock, so the
  build backend and environment are not fully hash-locked.

## Verification

All verification below is **local and offline**. No dependency was downloaded and no
remote CI run is claimed or referenced.

- Environment: the repository's `.venv`, CPython 3.12.13, pytest 8.3.5.
- Command: `python -m pytest tests -q`
- Baseline at `c3a10b7`, before any change: **135 passed, 4 skipped**.
- After the defect fix and its regressions: **138 passed, 4 skipped**.
- After all ALM-T0 edits: **138 passed, 4 skipped**.
- `git diff --check`: clean.

**Not verified:** CPython 3.11. The workflow matrix covers 3.11 and 3.12, but the suite
was not run under 3.11 in this task. The checkout's `.venv` is 3.12, and the system
`python3.11` that was checked does not have `pytest` importable. No other 3.11
environment was examined.

**Not verified:** the ERCOT real-data checks. The 4 skips are the `network`-marked
`tests/test_ercot_prices.py` cases for 2021 and 2024, which skip because no cached
ERCOT dataset is present in this checkout.

## 2026-09-27: milestone 1, offline controller check

Time-boxed work on branch `fix/local-policy-integration`, starting from `7701887`.
Started 2026-09-27 01:33:21 UTC, finished inside a 30-minute cap. Nothing was pushed.

**Baseline before any change:** `.venv/bin/python -m pytest tests -q` gave
**138 passed, 4 skipped** in 48.5 s, on CPython 3.12.

### ALM-T2, frozen legacy receipts

`tests/test_legacy_receipts.py` pins the canonical byte length, the SHA-256 of those
bytes and the carried `receipt_sha256` for `fleet.compare` (default synthetic scenario,
physics steps 10 and 5) and for `geofleet.experiment` on all five published areas, plus
two structural locks: repeated `compare` calls are byte-identical, and the stored
receipt hash equals the digest of the report body with the hash field removed. The
constants were read off the current code, so the first run was green by construction.
The red evidence is a drift check instead: with `fleet.display()` rounding changed from
9 to 8 decimal places, the file reported **7 failed, 4 passed**; after reverting,
**11 passed**. No existing module, test or gate was touched.

### ALM-T3, the CLI

`tests/test_cli.py` was written first and failed to collect:
`ModuleNotFoundError: No module named 'almanac.cli'` (**1 error**). After implementing
`almanac.cli`, `almanac.__main__` and `almanac.policies`, that file reported
**16 passed**. Each documented exit code has a test that drives the real entry point,
including a monkeypatched policy that raises `RuntimeError` to show the CLI fails closed
to exit 2 rather than degrading to a pass.

Two existing modules changed, both additively. `controller_contract` gained
`OvercommitRejected`, a `ValueError` subclass raised by the two existing envelope checks
with their messages unchanged, so existing callers and their tests are unaffected.
`geofleet` gained `scenario_for`, which is the scenario construction `experiment` already
performed; `experiment` now calls it. The ALM-T2 byte freeze is what shows that
extraction changed no output.

**Executed commands and exit codes** (`PYTHONPATH=src python3 -m almanac ...`):

| Command | Exit | Report `content_sha256` |
| --- | --- | --- |
| `scenarios` | 0 | not applicable |
| `run --scenario core --policy conservative-share --out out --html` | 0 | `a48649a4b9fb21792e4e26aa62d974320be150298ee70370d568f01bd56dedaa` |
| `run --scenario core --policy naive-target-fill --out out --html` | 2 | `f073e4b6f828147d795e28504d37c25c804d4f7c7681fb585e45eae424af490a` |
| `run --scenario core --policy hold-zero --out out --html` | 1 | `13ffdd85607aeba9ee298accb09a929ebd18464162b9cb3e23bb1b26f232bfbe` |
| `run --scenario empty --policy naive-target-fill --out out --html` | 0 | `b8a44203e15491559f24e90d8dfc343b7e8d8d92cd377a9b8b49bcf186593e23` |
| `check --scenario core --policy mystery-policy` | 3 | none written |
| `simulate --everything` | 3 | none written |

Reports are written under the gitignored `out/`, so those artifacts are not part of this
history. Each report carries engine, scenario, policy and config identifiers, the
scenario hash, and a `content_sha256` over the body with the hash field removed. They
contain no timestamps, and a test asserts that two separate runs write byte-identical
JSON and HTML.

**What the naive fixture actually showed.** On `core`, `naive-target-fill` ignores the
unknown-output allowance, and the existing action validator rejected its commands before
dispatch: `actions exceed target minus unknown output budget`. The report records an
attempted overcommit with `realized_overshoot_kw: null` and `measured: null`. **The
refused proposal was not dispatched. It is not a simulated physical overshoot. The
aborted run returned no receipt, so no delivered energy, overshoot or reserve outcome is
claimed for that policy — and none is ruled out** (see the ALM-T6 correction below).
On the fault-free `empty` area nothing is ever unknown, the same policy stays inside the
budget, and it passes with exit 0. That is reported as observed rather than presented as
a general result.

`conservative-share` on `core` passed with delivered 54.838888889 kWh against a
66.666666667 kWh request, shortfall 11.827777778 kWh, zero reserve violations, zero
realized overshoot, and 236 setpoint changes. Its shortfall is below the fixed-share
baseline reference (16.177777778 kWh) and above the redistribution reference
(8.484567901 kWh). Those are single-scenario simulation numbers, not a performance
estimate. Reserve preservation here is an energy floor inside the simulation and is not
evidence that any household stayed powered.

### Verification

- `.venv/bin/python -m pytest tests -q`: **165 passed, 4 skipped** in 58.2 s, run twice
  with the same result. Baseline was 138 passed / 4 skipped, so 27 tests were added and
  none were removed, weakened or skipped.
- `git diff --check`: clean.

**Not verified:** the installed `almanac` console script. `[project.scripts]` is declared
but taking effect needs a reinstall, which was out of scope here, so only
`python -m almanac` was executed. **Not verified:** CPython 3.11, unchanged from the
ALM-T0 note above. **Not verified:** anything about real hardware, real fleets or real
dispatch; this milestone is simulation only.

### ALM-T6 correction, parent review of milestone 1

A review of the milestone-1 work above found five defects. All five were reported by the
reviewer, not discovered by the tests, which is itself the finding: the suite asserted the
untruthful wording instead of catching it. Each fix landed with a test written first and
observed failing.

1. **The rejection note claimed more than the run could show.** It said *"No commands
   reached the simulated plant, no physical overshoot was simulated"*. That is false in
   general: `almanac.fleet.run` dispatches accepted commands tick by tick, so a candidate
   can complete several accepted control ticks before proposing something the validator
   refuses. Because the aborted run returns no receipt, the partial trajectory is
   unavailable — absence of a measured overshoot is not evidence that none occurred. The
   note now says only that the refused proposal was not dispatched, that earlier ticks may
   already have executed, that the partial trajectory is unavailable, and that no outcome
   is claimed *or ruled out*. A new `partial_trajectory` field says the same thing in a
   machine-readable form. No partial metric is fabricated to fill the gap, and the
   envelope gate is unchanged. The same correction was applied to the `NaiveTargetFill`
   docstring, the CLI `LIMITS`, `docs/controller-check.md` and the naive-fixture paragraph
   above. `tests/test_cli.py::test_late_rejection_does_not_claim_the_plant_was_untouched`
   builds a policy that is conservative on its first tick and greedy afterwards, asserts
   from the recorded observation times that the rejection really was late, and asserts the
   report does not claim an untouched plant.
2. **Report identity did not cover the code.** The `engine` block held a name and
   constants and the `policy` block a class string and version, so an edit to the engine,
   the adapter, a policy or the scenario builder could change behaviour without changing
   any identifier. Every report — including a failure report, because `_identity` runs
   before the simulation — now carries a `code` manifest: SHA-256 of the exact source
   bytes of `almanac.fleet`, `almanac.controller_contract`, `almanac.policies`,
   `almanac.geofleet` and `almanac.cli`, keyed by import name, plus a `manifest_sha256`
   over that mapping, which feeds `content_sha256`. Keys are import names only: no
   absolute path, environment variable or build metadata is recorded, so two checkouts of
   the same commit still produce identical bytes (a test asserts the repository path does
   not appear anywhere in the report). `config` additionally records the scenario's
   `local_expiry_s` and `lease_bound_s` alongside the physics step and control period. The
   frozen legacy receipt schemas were deliberately left alone: this metadata exists only
   in the CLI report, and `tests/test_legacy_receipts.py` still passes unchanged. The
   mutation-sensitivity test hashes a throwaway file in `tmp_path`, so it proves the
   manifest tracks byte changes without touching installed sources.
3. **Operational errors escaped the declared exit semantics.** A malformed registered
   fixture raised out of `evaluate` before the safety `try`, so it propagated out of
   `main` as an uncaught traceback and the process exited **1** — the code reserved for a
   defined performance regression. Output failures were unhandled too. There is now a
   documented exit **4**, `operational error`, which writes an `almanac.cli-error.v1`
   object to stderr with the stage, error type and message, and leaves stdout empty. Exit
   2 keeps its meaning: the check ran and the policy failed it. An I/O failure is no
   longer dressed up as a physical safety observation. `except Exception` is used
   deliberately, so `KeyboardInterrupt` and `SystemExit` still propagate, and the
   diagnostics are reported rather than swallowed. Three tests cover a malformed fixture,
   an unwriteable destination and a plain file given as the output directory.
4. **Reports were written in place.** A failure partway through could leave a truncated
   JSON file, or a JSON and HTML pair from different generations. `write_reports` now
   re-verifies the report identity, renders both payloads, and only then renames each into
   place through a same-directory temporary file with `os.replace`. A render failure
   therefore publishes nothing and leaves the previous complete generation byte-identical,
   which a test asserts by breaking `render_html` after a good run and checking both old
   files and the absence of stray temporaries. Passing a tampered report raises instead of
   writing.

**Not fixed, reported as a blocker.** Finding 5 asked for the bare `almanac` console
script to be executed. It was **not executed**, for two independent local reasons: the
project `.venv` has **no `pip`** (`.venv/bin/python -m pip --version` →
`No module named pip`), and the `uv pip install -e . --no-build-isolation --no-deps
--offline` attempt was **denied by this session's tool-permission mode**, so no install
was performed. No package was fetched and the environment was not modified. `setuptools`
is present in the venv but at **84.0.0**, not the `setuptools==78.1.1` that
`[build-system]` pins, so nothing here would have been a hash-locked build backend
either. The console script therefore remains **unverified**; only `python -m almanac` is
exercised, by `tests/test_cli.py::test_module_fallback_entry_point_runs_offline` in a real
subprocess.

### Verification, ALM-T6 correction

Before the fix, with the new tests in place and no source change:
`ImportError: cannot import name 'OPERATIONAL_EXIT' from 'almanac.cli'` — collection of
`tests/test_cli.py` failed outright. With the imports satisfied but the behaviour
unchanged, `test_late_rejection_does_not_claim_the_plant_was_untouched` then failed on
`assert False is True` for `attempted_overcommit_rejected`.

- `.venv/bin/python -m pytest -q` **before** any change: **165 passed, 4 skipped** in
  58.45 s, matching the figure recorded above.
- `.venv/bin/python -m pytest -q` **after**: **175 passed, 4 skipped** in 62.87 s. Ten
  tests added, none removed or skipped. Two existing assertions were repointed rather than
  deleted: they pinned the exact untruthful sentence this correction removes, so they now
  assert the corrected wording and the new `partial_trajectory` field instead.
- `git diff --check`: clean.
- Exit codes observed through the real entry point, `almanac.cli.main`: `scenarios` **0**,
  `check --scenario core --policy conservative-share` **0**,
  `check --scenario core --policy naive-target-fill` **2**,
  `check --scenario core --policy hold-zero` **1**,
  `run ... --out <dir> --html` **0**, `run ... --out <an existing file>` **4** with
  `{"error_type":"FileExistsError", ...}` on stderr, and an unregistered scenario **3**.
- Source manifest for this commit's tree, `check --scenario core --policy
  naive-target-fill`: `almanac.cli`
  `c1e753c39bb89a7f8845f9e9f9984abc3a4b3438f087400fd5d8896871ba289b`,
  `almanac.controller_contract`
  `83466e4f48da7b75a2eaf754501a6b10f9f5a521ec162cf522fa4588a18e43bd`, `almanac.fleet`
  `e3ed6cf54716ce9f84c3794302e920a8cbdfa022db81b1fc5ddacb8fd66f5659`, `almanac.geofleet`
  `f25357d810e9f2086f856e86799003f70a959bc072d5d12ab23f851b840dd5cd`, `almanac.policies`
  `7739909bc76546af6fcd3e8556ddf74bf32a10c42d38dd7fc114373333563f6f`; `manifest_sha256`
  `02a8a107d3cc8a1db07d97f05e6d90aa9565837eac5003715a0555579d55a33c`. Those bytes are
  pre-commit and the `almanac.cli` entry will change with any later edit to that file,
  which is the point of the manifest.

**Not verified:** the installed console script, as detailed above. **Not verified:** no UI
or demo page was changed or re-checked in this pass. **Not verified:** the correction was
bounded to 20 minutes of wall-clock work, so findings beyond these five were not searched
for.

## ALM-T6 correction 2: atomic report generations (2026-09-27)

The previous correction still replaced the JSON and the HTML **separately**. Two
`os.replace` calls means a failure on the second one publishes a new JSON beside an old
HTML: a mixed generation, with both files individually well-formed and self-consistent, so
nothing downstream can detect it.

**RED first, against the unchanged implementation.** A test wrote a complete generation,
built a second generation with the same stem and a genuinely different `content_sha256`,
then made the second `os.replace` raise. Observed failure:

```
E       AssertionError: mixed generations published
E       assert {'0eaf095f30c...dc2b2952bd89'} == {'1386488e3de...dc2b2952bd89'}
E         Extra items in the left set:
E         '0eaf095f30c376556be2fc6c02bd03dfd63935763214b8f390ebd3397e44ae1e'
```

The extra hash is the new JSON; the expected one is the old HTML still on disk. The
assertion reads each artifact's own embedded `content_sha256`, so it is independent of
where the files live and survived the layout change.

**Fix: one immutable generation per content hash, published by one directory rename.**
`out/check-<scenario>-<policy>/generations/<content_sha256>/{report.json,report.html}`,
with `current.json` as an atomically replaced pointer. Every artifact is rendered and the
report identity re-verified before anything is created, all artifacts are staged in a
private temporary directory, and the generation becomes visible through a single
`os.replace` of that directory. The generation is then re-read and confirmed
byte-for-byte before the pointer is moved, so a partial publication raises instead of
being reported as success. `write_reports` returns the in-generation paths and
`read_reports` resolves the pointer, so neither a writer nor a reader can straddle two
generations. Earlier complete generations are never touched.

Cases covered by tests: repeat publication of an identical generation is a no-op that
returns the same paths and bytes; a JSON-only generation is completed when `--html` is
requested later; a damaged generation is validated by its bytes and republished rather
than trusted by name; a previous generation survives a new publication and stays readable.

**Honest limits.** Republishing over a damaged directory of the same name moves the
damaged copy aside and restores it if the swap fails — two renames, so only the ordinary
first publication of a generation is crash-atomic. Concurrency is addressed only to the
extent that content-hash naming makes two writers of the same generation agree; no lock is
taken, and two writers publishing *different* generations concurrently race on
`current.json`, with the loser's generation still complete and readable on disk.

**Source manifest widened.** `almanac.replay` (the `canonical`/`digest` helpers that define
receipt and hash semantics) and `almanac.inspector` were missing, so a change to either
could leave the report identity unchanged. The manifest now covers exactly seven modules
and the report carries an `excludes` field stating plainly that this is not a
supply-chain identity: interpreter, standard library, installed packages, OS and all other
repository modules are outside it.

**Verification.** `.venv/bin/python -m pytest -q`: **180 passed, 4 skipped** in 65.55 s,
against the parent's 175 passed and the same 4 pre-existing ERCOT-cache skips. Five tests
net added, none removed or skipped. `git diff --check`: clean. The `content_sha256` values
recorded in the table earlier in this log are **stale from this commit onward**: widening
the source manifest changes `manifest_sha256` and therefore every report hash, by design.
They were not re-recorded in this pass.

**Path contract changed**, so the tests and docs that pinned the flat `out/check-*.json`
layout were repointed to the generation layout rather than deleted; every safety assertion
they made (identical bytes across runs, self-contained HTML, no stray temporaries, previous
generation intact, tampered report refused) is still asserted.

## ALM-T2 (milestone 2): command and telemetry uncertainty foundation (2026-09-27)

New module `src/almanac/uncertainty.py` with `tests/test_uncertainty.py` and
`docs/uncertainty.md`. **Additive and experimental.** `almanac.fleet` is untouched, no legacy
receipt changed, the frozen receipt-bytes suite is unchanged, and nothing here is reachable
from any server, page or CLI subcommand. It labels itself
`offline-experimental-simulation-only` in its own output and is a foundation for later risk
work, **not** a finished probabilistic controller.

**What it models.** A controller that sees only delivered telemetry packets and delivered
acknowledgements. Physical state (`grid-connected`, `islanded`, `non-operational`) is kept on
a separate axis from observation freshness (`reporting`, `late`, `unreachable`), and the trace
separates `observed` from evaluator-only `truth`. Independent integer-second control period,
telemetry cadence, inactivity timeout and absolute command TTL. Exact `Fraction` power and
energy; strict finite-numeric and schema validation before any state is mutated. Event order
at each second is fixed and total: expiry, then faults, then telemetry, then decision, then
physics.

**The core knowledge rule.** Per device the controller keeps mutually exclusive histories —
either the last confirmed command still runs because every later one was lost, or one of the
unconfirmed commands was received — so the per-device worst case is the **maximum** over that
set, never the sum of old and new. Those maxima are then summed across devices. An unconfirmed
stop therefore frees no capacity, and fresh telemetry from a device with a pending command
does not resolve which command is running.

**Verification.** `.venv/bin/python -m pytest tests/test_uncertainty.py -q`: **40 passed in
0.06 s**. `.venv/bin/python -m pytest -q` (whole suite): **220 passed, 4 skipped** in 65.54 s,
against 180 passed at the previous commit and the same 4 pre-existing ERCOT-cache skips. Forty
tests added, none removed or skipped. `git diff --check`: clean. The two examples printed in
`docs/uncertainty.md` were executed: the default fixture delivers `1/3` kWh with
`uncertain_device_seconds` 4 and 0 attempted gate violations; the islanded single-device
example delivers `1/15` kWh with shortfall `19/90` and 0 attempted gate violations.

Covered by test: independent clocks; exact TTL boundary second; duplicate never renewing TTL;
expired command leaving the possible set; pending replacement not double-counted; unconfirmed
stop not freeing capacity; reporting-with-pending still uncertain; per-device max summed across
devices; delayed ACK not overriding a newer command; command loss preserving the old output;
ACK loss with real execution; islanding delivering zero to the grid while silence retains
uncertainty; non-operational plus offline overlapping; backfill after reconnect never promoted
to current; exact conservation and the reserve floor; a device at its floor delivering nothing;
zero target; malformed input rejection (17 parametrised cases); determinism for the same seed
and config; metrics integrated rather than asserted; the observed timeline leaking neither
truth nor the future; the naive proposal rejected by the same bound.

**Scope limit, stated rather than glossed.** The controller is a **bounded built-in
conservative controller, not the generic policy adapter** the brief allowed to be deferred; a
pluggable adapter over this knowledge state is **not implemented**. It is conservative by
construction, not optimal, and its bound is not claimed to be the tightest sound one. No
user-facing claim is made that homes stay powered; reserve is an energy floor inside the
simulation.

**Uncovered in this pass:** no CLI subcommand, report or HTML surface for the simulator, and
no integration with the controller-check evaluator or its source manifest; duplicate-command
redelivery and delayed-ACK ordering are asserted at the knowledge level rather than driven
through long multi-tick scenarios in `simulate`; concurrent publication of *different* report
generations still races on `current.json` (see the correction above); no ramp rate, efficiency,
measurement error or stochastic calibration; no risk frontier and no UI integration. This
milestone was time-capped at 30 minutes of wall-clock work.

### Correction: the ALM-T2 whole-suite figure excluded an uncommitted file (2026-09-26)

The **220 passed, 4 skipped** figure recorded above was produced by a run that did not collect
everything in `tests/`. The actual checkout also held an untracked
`tests/test_red_mixed_tmp.py`, so `.venv/bin/python -m pytest tests -q` on that checkout
reported **1 failed, 220 passed, 4 skipped**. The pass/skip counts above are accurate; the
claim that they covered the whole suite as it stood on disk was not.

The failing file was the scratch RED reproduction written while chasing the mixed-generation
publish defect, before the fix settled on retaining older generations on disk. Its
`_generations()` helper walked every artifact under the output directory, so it counted
retained prior generations as a mixed publish and failed against correct behaviour. The
committed regression for the same defect,
`tests/test_cli.py::test_failure_on_the_second_rename_never_publishes_a_mixed_generation`,
follows the current pointer through `read_reports` instead and passes; it was left untouched,
along with every other committed test.

The obsolete file was **archived, not deleted** — renamed byte-for-byte (1712 bytes, SHA-256
`49d093f2083ed33f7d8202ee33dcabf9843bf9427e9f766c2dfba90555531e13`) to
a local diagnostic archive outside the repository and any collected test path. After the
move, `.venv/bin/python -m pytest tests -q` over the entire tests
directory: **220 passed, 4 skipped in 65.79 s**, with the same 4 pre-existing ERCOT-cache
skips. No source or test code was changed in this correction.

### ALM-T2 correction: acknowledgement and backfill lifecycle (2026-09-26)

An independent read-only review of `uncertainty.py` at `35df338` reported six defects. Each
was **reproduced first** against the committed behaviour, then fixed. Starting HEAD `838b0d5`,
clean tree. The full RED transcript was retained in a local diagnostic archive outside
the repository.

So that the new acceptance could fail behaviourally rather than on missing names, the ACK,
telemetry and decision paths were first **extracted verbatim** out of `simulate` into
`_apply_ack`, `_ingest_packet`, `_deliver_due_acks` and a `decide=` parameter. That extraction
changed no behaviour: `tests/test_uncertainty.py` stayed at **40 passed**. The new tests then
failed **42** against it, with every committed test still passing in its corrected form.

**1. An acknowledged stop left an impossible bound standing.** ACK reconciliation only pruned
superseded unconfirmed commands when a *newer* command also existed, so the ACK of a stop
after a lost positive ACK left the old positive setpoint in the worst case until its TTL.
RED: `assert know.unconfirmed == []` → `[Command(..., setpoint_kw=Fraction(5, 1), sequence=1)]`.
`_apply_ack` now retires every command *older* than the acknowledged one unconditionally —
a device only accepts a command newer than the one it holds — always keeps newer ones, reports
`superseded` for an ACK older than what is already confirmed (resurrecting nothing) and
`expired` for an ACK whose command has lapsed (confirming nothing).

**2. Zero-delay ACKs were implicitly delayed by a second.** ACKs were drained only before the
decision, so an ACK generated during the decision with delay 0 waited for the next second: a
fault-free one-second run ended falsely pending, and a one-second TTL expired before its own
ACK. RED: `assert entry['observed']['d1']['pending_unconfirmed'] is False` → `True is False`.
The event order is now explicit at seven phases, with ACKs delivered twice — those already
due before the decision, and those generated at this timestamp after the dispatch and before
physics and metrics. Nothing is backdated. A visible consequence: the default fixture's
`uncertain_device_seconds` is now **0**, not the 4 recorded in the milestone entry above,
which was that phase artefact rather than real uncertainty.

**3. Telemetry backfill was impossible.** Packets were only ever appended while linked and
operational and drained in the same second, so an outage discarded everything and the
reconnect test was vacuous — it coincided with a fresh sample. RED, with the reconnect moved
off the telemetry cadence: `assert reconnected['last_sample_s'] == 70` → `10 == 70`. Powered
devices — grid-connected or islanded — now sample into a per-device buffer bounded at
`MAX_BUFFERED_SAMPLES = 16` that drops its oldest entry when full; a non-operational device
samples nothing. Samples keep their measured time, so backfill reads as history:
`sample_age_s` and `reading_is_current` in the trace, `backfilled_samples`,
`max_sample_age_s`, `dropped_buffered_samples` and `stale_backfill_samples` in the metrics.
Tested: a six-sample backlog delivered at a 55 s maximum age, the 16-sample bound with 21
samples dropped from a 37-sample outage, duplicate and out-of-order arrivals, islanded
sampling versus a dead device, and no sample ever dated in the future.

**4. Weak test oracles replaced, and the gate now refuses.** Lines 471-479 only *counted* a
worst-case violation and dispatched the commands anyway. RED through the internal decision
seam: `assert result['metrics']['commands_issued'] == 0` → `12 == 0`. `_gate_decision` now
judges every proposal before dispatch and fails closed on the whole proposal — worst case
over target, unknown device, negative, non-finite or over-capacity setpoint, or a proposal
that is not a mapping — returning a diagnostic kept in `gate_rejections`. Oracles fixed
rather than deleted: the lost-replacement fixture now really issues a replacement (the
built-in controller has no headroom to reissue, so the replacement comes through the seam and
is still gated); the delayed-ACK test runs through the real handler instead of hand-editing
knowledge; the naive proposal's arithmetic is corrected (a naive controller reloads the
*other* device, 5 + 8 against a 10 kW target — old + new on one device was never the bound);
and the energy oracle no longer subtracts the last step, which was only correct because the
trace snapshot is post-integration and the run happened to end at the reserve floor. A second
conservation test now ends mid-discharge at 8 kW so that a final-step error cannot hide
behind a zero. The `decide=` seam is internal and in-process: no HTTP, no dynamic import, no
configuration path, and every proposal through it meets the same gate.

**5. Failure schedules were unvalidated.** `drop_commands`, `drop_acks` and `ack_delay_s` were
taken on trust (`frozenset('abc')` silently became a set of characters). 22 new parametrised
cases were RED as `DID NOT RAISE ScenarioError`. Containers must now be lists/sets, or a
mapping for `ack_delay_s`; sequence ids must be plain integers in `[1, 1_000_000]`, rejecting
bools, floats, strings and NaN; delays must be whole seconds in `[0, duration_s]`. Duplicate
ids remain accepted deliberately, and that is now asserted.

**6. The properties above are pinned by tests**, not by prose: exact energy conservation while
still discharging, sample truth versus observed state, an acknowledged stop freeing capacity
while a lost stop frees nothing, the per-device maximum never a sum, a zero target, malformed
rejection (39 parametrised cases) and byte-identical repeated runs.

`tests/test_uncertainty.py`: **40 → 82 passed**, no test deleted or skipped. Whole directory,
`.venv/bin/python -m pytest tests -q`: **262 passed, 4 skipped in 65.41 s**, the same four
pre-existing ERCOT-cache skips, with no untracked files under `tests/`. `git diff --check`
clean. Both examples in `docs/uncertainty.md` were re-executed: the default fixture delivers
`1/3` kWh with `uncertain_device_seconds` 0 and 0 attempted gate violations; the islanded
single-device example delivers `1/15` kWh with shortfall `19/90`, 0 attempted gate violations
and an empty `gate_rejections`.

**Still not done, and not claimed:** no simulator adapter for the real engine, no risk
frontier, no counterfactual experiments, no CLI, report or UI surface, and no integration with
the controller-check evaluator — this pass was correctness only. The legacy engine, its gates
and its receipt schemas were not touched. The offline buffer bound, like every other number
here, is an invented simulation limit. This correction was time-capped at 30 minutes of
wall-clock work.

## Demo integration: the controller check reaches the browser (2026-09-27)

**The gap.** `almanac.cli` had a real evaluator, a closed registry and two report formats, and
none of it was reachable from the demo page. The browser compared only the two built-in map
strategies; a reader who wanted a verdict had to leave the page for a shell. **Additive:** the
legacy map, its receipt schemas, the gates, the export path and the frozen receipt-bytes suite
are untouched, and no existing route, test or report format changed.

**Tests first, RED then GREEN.** New `tests/test_check_http.py`, 32 cases, written and run
against the unmodified server: **22 failed, 10 passed** — the 10 that passed were the
negative probes (`403` on a foreign `Host`/`Origin`, `404` on repository paths) that the
existing hardening already satisfied. Everything asserting the new behaviour was red,
including `test_browser_section_uses_these_routes_only`, which stayed red until the page
itself was wired.

**Two read-only `GET` routes** on the existing loopback `almanac.geofleet_server`:
`/api/v1/check/registry` returns exactly `cli.listing()`, and
`/api/v1/check/run?scenario=<key>&policy=<key>` returns one `almanac.check-envelope.v1`
object built from a single `cli.evaluate()` call. The envelope carries the report plus
`report_json` (`canonical(report) + '\n'`) and `report_html` (`cli.render_html(report)`) — the
exact bytes `almanac run --html` would publish. The tests pin that equality rather than
describing it: the envelope's report is compared against a direct `cli.evaluate()` call, and
both download bodies against `canonical` and `render_html` of that same report. **One
evaluation, one `content_sha256`, both files.** The page never re-runs the evaluator to
produce a download.

**Genuine states, no placeholders.** Three real fixtures give three real verdicts, asserted
end-to-end over HTTP: `core`/`conservative-share` → `pass`, exit 0; `core`/`hold-zero` →
`regression`, exit 1; `core`/`naive-target-fill` → a safety verdict, exit 2, with
`measured: null`, `attempted_overcommit_rejected: true` and the rejection block the CLI
produces. The three reports carry three distinct `content_sha256` values and a
`manifest_sha256` equal to `cli.code_manifest()`. The fourth state is an operational error,
driven by making the hash-checked NHC evidence unavailable: the response is `503` carrying
`almanac.cli-error.v1` with `verdict: "operational-error"` and `exit_code: 4`, **no report and
no verdict**, and the page says so.

**Hardening kept, not widened.** The routes sit behind the existing strict `Host`/`Origin`
loopback gate and one-request-per-connection framing; `403` on a foreign authority is
asserted on both routes. The run query is bounded at 96 characters, parsed with
`strict_parsing`, and must be exactly one `scenario` and one `policy` that are **keys of the
closed registry**. Thirteen parametrised cases cover a missing, blank, duplicate, unknown or
oversize parameter, an import path (`almanac.policies:ConservativeShare`), an absolute path
(`/etc/passwd`) and a traversal (`../../pyproject.toml`) — all `400`, and a separate test
asserts the rejected value is **not echoed back** into the response body. Six targets confirm
no repository file is reachable. Checks take the same single `RUN_SLOT` as the map
experiment, so a concurrent map run gets `503` rather than queued work — asserted with a
blocking evaluator, not assumed.

**The page.** A new `#controller-check` section, *"4. Offline controller checks"*, in the
existing dark-green vocabulary: existing `.results`, `.summary`, `.fine`, `.controls` and
`.table-wrap` classes, the existing 44px control sizing and focus ring, `role="alert"` for
errors and `role="status"` for progress, `AbortSignal.timeout(15000)` like every other fetch
on the page, and `th[scope=row]` provenance rows. No redesign and no new visual language.
Both selectors are **filled from the registry route**, so the page offers exactly what the CLI
registers. The provenance table shows the scenario SHA-256, the source manifest SHA-256, the
report `content_sha256`, the policy source and its trust statement, and either the delivered/
shortfall pair against the fixed-share baseline or — on a rejection — the stage and the
explicit statement that the aborted run returned no receipt.

**Verification.** `.venv/bin/python -m pytest tests -q`: **294 passed, 4 skipped in 84.57 s**,
against a pre-change baseline of **262 passed, 4 skipped**. The four skips are the same
pre-existing optional full-year ERCOT cache tests. No test was deleted, skipped or weakened.
All tests are offline; nothing in this pass makes an outbound request.

**Not done, and not claimed.** The experimental `almanac.uncertainty` surface is still
unreachable from any page, server or CLI subcommand — it did not fit inside this pass's
30-minute cap and no stub, flag or placeholder for it was added. No risk frontier, no
counterfactual experiments. The browser still executes no uploaded code, and there is still
no HTTP import or upload seam: a third policy goes through the documented local CLI/import
boundary. A verdict here remains a statement about one synthetic scenario and one seeded
simulated plant, not evidence about any real fleet.

## Demo integration correction: stale check results and a reflected query field (2026-09-27)

Two independent defects found by review of the commit above, `7128f25`. Both were real in
that commit, both are fixed here, and nothing else in this pass changed behaviour.

**Defect 1, the page showed a verdict for inputs that were no longer selected.** In
`7128f25` the two `#controller-check` selectors stayed enabled during a request and had no
`change` handler. So a reader could complete a check on `core`/`hold-zero`, then pick a
different scenario or policy, and the section still displayed the previous verdict, the
previous provenance table with its `content_sha256`, and two download buttons still wired to
the previous envelope — while the selectors read as the new pair. Downloading then produced a
file that was honest about itself but did not describe what the page appeared to be showing.
The selectors were also changeable mid-request, and the response handler had no way to tell a
late answer from the current one.

**The fix.** One `clearCheck(status)` function is the only path that resets the section: it
bumps a `checkGeneration` token, drops `checkEnvelope` (the object both downloads read),
hides `#check-result`, and empties the verdict, its `data-verdict` attribute, the findings
list, the provenance rows and the download status line. Both selectors call it on `change`,
and a run calls it before fetching. `lockCheck(busy)` disables *both selectors and the
button* for the duration of a run and restores all three in a `finally`, so success, an HTTP
failure, an operational error and a 15-second abort all end with usable controls. Two guards
reject an answer that is not about the current selection: the generation token captured at
request time is re-checked before anything is displayed and again in the `catch`, and the
envelope's own `report.scenario.key` / `report.policy.key` must equal the captured inputs or
nothing is shown and no verdict is claimed. Real HTTP and operational errors still surface
with their existing messages, and the legacy map, its inspector and its selectors are
untouched.

**Coverage, and its honest limit.** This repository has **no real-browser harness** — no
Playwright, Selenium or headless-DOM runner — so nothing here executes this page's JavaScript
and there is no test that actually clicks a selector. The new
`test_browser_section_invalidates_and_locks_on_selection_change` pins the wiring *textually*:
both selectors route through `clearCheck`, that function drops the envelope and bumps the
token, `lockCheck` covers all three controls, the restore is in a `finally`, the hand-rolled
`$('check-run').disabled=true` is gone, the generation guard appears on both the success and
the failure path, and the key comparison is present. Verified to fail against `7128f25`'s
page: `git show HEAD:demo/geofleet.html` contains no `clearCheck` change wiring, no
generation guard, and the forbidden manual `disabled=true`. A companion test,
`test_report_carries_the_keys_the_page_guard_compares`, runs the real evaluator on all three
fixtures so the guard's `report.scenario.key` / `report.policy.key` cannot silently stop
existing. A textual test proves the code is wired, **not** that the browser behaves; a real
end-to-end click test is still missing and is listed below.

**Defect 2, a rejected query field was reflected into the error body.** `check_run` parsed
with `parse_qs(..., strict_parsing=True)` inside a `try` whose `except ValueError` returned
`{'error': str(exc)}`. CPython's strict parser raises `bad query field: '<value>'`, quoting
caller bytes, so `GET /api/v1/check/run?scenario=core&policy=hold-zero&REFLECT_ME` answered
`400 {"error":"bad query field: 'REFLECT_ME'"}`. The existing non-reflection test only
covered a *rejected registry key*, which is raised by the module's own message, so it passed
throughout and did not catch this. The parse now has its own `except ValueError` that raises
`ValueError('malformed query') from None`; every other message in that block is a literal the
module owns.

**Tests first, RED then GREEN.** Four parametrised cases added to
`tests/test_check_http.py` asserting `400` and that `REFLECT_ME` does not appear anywhere in
the response bytes. Against the unmodified server: **3 failed, 1 passed** — the one that
passed, `?REFLECT_ME=1&scenario=core&policy=hold-zero`, parses cleanly and is rejected later
by the module's own "exactly one scenario and one policy" message, which is exactly why the
old test suite missed the defect. All four pass after the change. The bound at 96 characters,
the `strict_parsing` rejection itself, the closed-registry membership checks, the
`Host`/`Origin` gate and the shared `RUN_SLOT` are unchanged and still asserted.

**Also in this pass.** The new controller-check section's user-facing em-dash separators are
now colons: the verdict line, the policy option labels, and the one prose dash in the section's
fine print. Scope is that section only; no other page text or document was reformatted.

**Verification.** `.venv/bin/python -m pytest tests -q`: **301 passed, 4 skipped in 88.67 s**,
against this correction's pre-change baseline of **294 passed, 4 skipped**. The seven new
cases are the four reflection probes and three page/report-shape tests. The four skips are
the same pre-existing optional full-year ERCOT cache tests. No test was deleted, skipped or
weakened; all offline.

**Remaining gaps after this pass.** (1) No real-browser test exists, so the invalidation and
locking behaviour is pinned textually only. (2) The **legacy** `/api/v1/geofleet/*` handler
has the identical reflection pattern — its `parse_qs(u.query, strict_parsing=True)` shares an
`except ValueError` that returns `str(exc)`, so a malformed field is still echoed there. It
was outside this correction's two findings and was deliberately left alone rather than changed
without a reported finding and its own red test. (3) No uncertainty integration was attempted
in this pass, by instruction.

## Legacy geofleet query reflection, the last instance of the same pattern (2026-09-26)

One finding, carried over as gap (2) of the pass above and reported again by review at
`src/almanac/geofleet_server.py` ~93/102. Only that reflection is fixed here; no UI work and
no uncertainty work were attempted, by instruction.

**The defect.** The legacy `/api/v1/geofleet/preview`, `/run` and `/export` handler parsed
with `parse_qs(u.query, strict_parsing=True, keep_blank_values=True)` inside a `try` whose
`except ValueError` returned `{'error': str(exc)}`. CPython's strict parser raises
`bad query field: '<value>'`, quoting caller bytes, so
`GET /api/v1/geofleet/run?area=core&REFLECT_ME` answered
`400 {"error":"bad query field: 'REFLECT_ME'"}` — the identical shape fixed in `check_run`
one commit earlier, on the older route that the previous pass deliberately left alone.

**Tests first, RED then GREEN.** Twelve parametrised cases added to
`tests/test_geofleet_http.py` (four queries across all three legacy routes), asserting `400`,
that `REFLECT_ME` appears nowhere in the response bytes, and that the body is still exactly
`{'error': ...}`. Against the unmodified handler: **9 failed, 3 passed**. The three that
passed are `?REFLECT_ME=1&area=core`, which parses cleanly and is refused later by the
module's own `one area required` message — the same reason the earlier suite missed the
`check_run` instance.

**The fix.** The parse now has its own `except ValueError` raising
`ValueError('malformed query') from None`, matching `check_run` exactly. Every other message
reachable from that block is a literal owned by this module or by `almanac.geofleet`
(`query too large`, `one area required`, `choose one allowlisted area`, the source-integrity
messages). Unchanged and still asserted: the 64-character query bound, the `strict_parsing`
rejection itself, the area allowlist check inside `preview`/`experiment`, the `Host`/`Origin`
loopback gate, the shared `RUN_SLOT` non-blocking acquire with its `503`, the byte-identical
`run`/`export` receipts with `export`'s attachment header, and the `503`/`400` split when
cached official evidence is missing or altered.

**Audit of the remaining `str(exc)` sites, so this pattern is not left elsewhere.**
`geofleet_server.check_run` was fixed in the previous commit. `replay_server`'s `do_POST`
returns `str(exc)` too, but every message reachable there is a literal from `strict_json` or
`replay` (`invalid body size`, `commands required`, `invalid command history`, `malformed
timestamp`, `malformed evidence bundle`); no parser that quotes caller bytes feeds it. The
`cli.py` sites report engine and fixture failures, not HTTP query input. No other
`strict_parsing` call exists in `src/`.

**Verification.** `.venv/bin/python -m pytest tests -q`: **313 passed, 4 skipped in 94.69 s**,
against this pass's pre-change baseline of **301 passed, 4 skipped**. The twelve new cases are
the only difference. The four skips are the same pre-existing optional full-year ERCOT cache
tests. No test was deleted, skipped or weakened; all offline.

**Remaining gaps after this pass.** (1) This repository still has no real-browser harness, so
the check section's locking and invalidation remain pinned by textual assertions inside the
suite; they were confirmed by hand outside it, along with both downloads actually being
produced, but nothing in `tests/` executes the page's JavaScript. (2) No UI expansion was
attempted here. (3) No uncertainty integration was attempted here.

## Bounded experimental surface for the uncertainty model (2026-09-26)

Gap (3) of the pass above. The experimental simulator `almanac.uncertainty` existed with no
registry, no CLI, no route and no page section: `docs/uncertainty.md` listed exactly that as
not built. This pass adds one bounded surface around it and changes the simulator's physics,
controller and gate not at all.

**Scope kept narrow, by instruction.** `almanac.fleet`, `almanac.controller_contract`,
`almanac.policies`, the frozen legacy receipts and the whole offline controller check
(registry, evaluator, routes, page section 4) are untouched. The only edit to an existing
source file is the new route pair in `geofleet_server.py`, and one docstring correction in
`uncertainty.py` that no longer claims no server or page exposes it.

**New sources.** `src/almanac/uncertainty_lab.py` (closed registry, evaluator, separate CLI)
and `tests/test_uncertainty_lab.py`. The evaluator calls `uncertainty.simulate` once per run
and derives every rollup from that one trace; a test monkeypatches `simulate` to prove it is
called exactly once, and asserts the new module's source contains neither `deliverable_kw` nor
`SECONDS_PER_HOUR`, so no physics is reimplemented.

**The registry.** Five synthetic scenarios, each at most 180 simulated seconds over two
devices: `acknowledged-baseline` (uncertainty free contrast), `ack-loss-unconfirmed` (first two
ACKs dropped), `ack-delay-late-confirm` (first two ACKs 25 s late), `reconnect-backfill` (40 s
outage, backlog delivered at its original sample times) and `backfill-buffer-overflow` (100 s
outage that outlasts the 16 sample buffer). Keys are the only accepted input: no scenario JSON,
no import path, no filesystem path, on the CLI or over HTTP. A registry entry cannot lie about
what it exercises, because every report carries `mechanism_evidence` (required counters,
observed values, `satisfied`) and a parametrised test asserts `satisfied` for every key. The
exact counters each scenario produces are pinned by a second test, so the registry cannot
drift: `ack-loss-unconfirmed` 120 uncertain device seconds, `ack-delay-late-confirm` 50,
`reconnect-backfill` 4 backfilled samples with an oldest delivered sample age of 40 s and none
dropped, `backfill-buffer-overflow` 15 backfilled with 5 genuinely dropped and an oldest age of
75 s, and the baseline zero of all four. Those pinned numbers were read off the runs, not
predicted: the first attempt at that test claimed non-zero pending uncertainty for both outage
scenarios and failed, because the 60 s absolute TTL leaves the controller no headroom at the
control ticks during an outage, so it waits rather than issue a command it could not confirm.
The expectations were corrected to what the simulator actually integrates, and the reason is
recorded beside them.

**What the report separates.** Command received (evaluator truth) versus acknowledged
(`acknowledged_command_seconds` / `unacknowledged_command_seconds`) versus telemetry freshness
(a `freshness_seconds` histogram); `observer` versus `evaluator_truth` as two objects, with the
observer rows carrying no energy and no link field at all; original sample age on backfill
(`max_sample_age_s`, `sample_time_jump_s`, plus the run level backfilled, stale and dropped
counts); absolute command expiry with `observer_truth_divergence_device_seconds`; and the
reserve floor with its meaning stated inline. `safety.verdict` is always `null`: no verdict, no
ranking, no failure rate, no probability, no risk frontier.

**Identity, scoped.** `code.manifest_sha256` covers the exact source bytes of four modules
only: `almanac.uncertainty`, `almanac.uncertainty_lab`, `almanac.replay` and `almanac.cli` (the
source hashing helpers reused here). The report states in `excludes` that this is not a
supply-chain identity. `experiment.scenario_sha256` hashes the registered spec and
`content_sha256` the report body.

**Separate CLI, deliberately not an `almanac` subcommand.**
`python -m almanac.uncertainty_lab experiments` and `... run --experiment <key>`, exit 0 / 3 /
4 with no verdict exit code, canonical JSON on stdout, nothing written to disk. It is not added
to the `almanac` parser because that would change `almanac.cli`'s source bytes and therefore
the legacy check's source manifest and the identity of every already published check report.

**Loopback route pair.** `/api/v1/uncertainty/registry` (no query parameters) and
`/api/v1/uncertainty/run?experiment=<key>`, inheriting the existing `Host`/`Origin` gate, one
request per connection framing and the 256 byte target bound, and adding a 64 character query
bound, strict parsing, exactly one `experiment`, closed registry membership, fixed
non-reflecting error documents, and the shared `RUN_SLOT` acquired without blocking. A test
confirms a `/api/v1/check/run` request gets `503` while an uncertainty run holds the slot, and
that the route writes no file into `out/`.

**Page section 5, separate from section 4.** Same visual language as the existing check
section: existing CSS variables, `.check-fields`, `.table-wrap`, `.controls`, `.fine`,
`role="alert"` and `role="status"` regions, labelled selects, a range input for per second
inspection, 44 px control targets and no new colour. It shows the three axes as three headed
panels, observer and evaluator rows as two separate lists, provenance including both hashes,
and one JSON export taken from the same envelope the display reads. Changing the experiment
clears the summary, both inspection lists, the timeline readout and the export through a single
`clearUnc`, bumping a generation token; a run locks the experiment select, the button, the
timeline and the device select and restores them in `finally`; a late answer or one naming
another experiment is discarded. No em dash appears in the new copy.

**TDD, RED then GREEN.** `tests/test_uncertainty_lab.py` was written first: against the
unmodified tree it failed at collection (`ImportError: cannot import name 'uncertainty_lab'`),
which is the honest RED for a new module and a new route. After the registry and evaluator: 21
passed for the non-HTTP subset. After the route: 34 HTTP cases passed, including 11 invalid
parameter cases (blank, duplicate, unknown key, wrong parameter name, import path, filesystem
path, percent encoded scenario JSON, oversized query) and 5 reflection cases asserting
`REFLECT_ME` and `script` appear nowhere in the response bytes. After the page: 3 browser
wiring cases passed.

**Verification.** `.venv/bin/python -m pytest tests -q`: **376 passed, 4 skipped in 111.77 s**,
against this pass's pre-change baseline of **313 passed, 4 skipped**. The 63 new cases are the
only difference; no existing test was changed, deleted, skipped or weakened. The four skips are
the same pre-existing optional full-year ERCOT cache tests. All offline.

**Remaining gaps after this pass.** (1) Still no real-browser harness, so section 5's
invalidation, locking, generation guard and export wiring are pinned by textual assertions in
the suite exactly as section 4's are; nothing in `tests/` executes the page's JavaScript, and
this pass did not drive the page in a browser. (2) The new CLI was exercised through the test
suite only (`lab.main` for both subcommands, the six malformed argument cases and the printed
JSON compared against `listing()` and `evaluate()`); the sandbox for this pass permitted only
the pytest command, so no separate shell invocation of
`python -m almanac.uncertainty_lab` was run. (3) The registry deliberately covers ACK loss, ACK
delay, reconnect backfill and buffer overflow only: command loss with a surviving older
command, islanded devices and multi device fault interleavings are reachable in the simulator
but not registered here. (4) The built-in conservative controller is still the only controller
on this surface; the `decide=` seam stays an in-process test seam with no CLI or HTTP path. (5)
No calibration, no probabilistic claim and no integration with the legacy check evaluator or
its receipts was attempted, by instruction.

## Correction pass on the uncertainty experiment surface

Three overstated claims from the entry above, corrected in place. The physics, the controller,
the gate, the legacy CLI, the legacy check registry, the frozen receipts and the legacy routes
are untouched: `src/almanac/cli.py`, `src/almanac/geofleet_server.py` and `src/almanac/
uncertainty.py` are byte identical after this pass, and exactly one existing test changed.

**1. A clock advance was being labelled as backlog.** `_observer_rollup` reported
`sample_time_jump_s`, the largest increase in the newest delivered sample time, and the page
printed it as `N (backlog delivered as history)`. Reproduced before fixing: the loss-free
`acknowledged-baseline` run reports `sample_time_jump_s: 10` for both devices with
`backfilled_samples: 0`, so healthy once-per-period telemetry was being presented as delayed
delivery. The first attempted fix, counting only jumps where the newly delivered newest sample
was itself old, went to zero on every registered scenario: when a reconnect flushes a buffer,
the newest sample in the batch was measured in the delivery second, so the observer cannot see
the backlog in `last_sample_s` at all. That is the honest finding, and the field now reflects
it. `skipped_sample_time_s` reports how much sample time the newest reading skipped **beyond
one telemetry period** and nothing more: it is a statement about the controller's own view and
claims no cause, because an observer genuinely cannot tell a backfilled reading from one that
was never taken. It is `0` for both devices in all three no-loss scenarios and positive only
for the device that lost its link (`40` on `reconnect-backfill`, `100` on
`backfill-buffer-overflow`, `0` for the healthy peer in both). Genuine late delivery is left
where it is actually measured, the simulator's own `metrics.backfilled_samples`.

**2. The source manifest described disk bytes as the code that ran.** `code_manifest()` hashed
the module files at report time, so the long-running loopback server, which keeps executing
whatever it compiled at start-up, would have stamped a report with bytes it never ran.
Reproduced in an isolated copy of `src/` under pytest's `tmp_path` and a subprocess, never in
this checkout, so no live server was disturbed: appending a comment to `almanac/uncertainty.py`
after import changed `manifest_sha256` and `evaluate()` still returned a complete report
pinned to the new bytes. **Robust proof of the executed bytes is outside this pass's scope, so
the claim is narrowed rather than dressed up.** The manifest is now explicitly a *disk
snapshot* taken when `almanac.uncertainty_lab` was imported, and it says so in the report:
`snapshot`, `not_an_attestation_of` ("this is a disk snapshot and not proof of the bytes the
interpreter executed"), and `loaded_before_this_module`, which names the manifested modules
the interpreter had already executed before this module loaded and for which even the import
snapshot postdates execution. What is real is the fail-closed part: `verify_sources()` re-reads
the files and raises `SourceDriftError` on any difference from that snapshot, once before the
simulation runs and again when the manifest is built, and a missing or unreadable source is
treated as drift rather than surfacing as a bare `FileNotFoundError`. On drift the CLI exits
`4` with the operational-error envelope on stderr and prints no report, no manifest and no
verdict.

**3. A test named for containment only listed a directory.**
`test_http_writes_no_file_and_opens_no_socket` compared `sorted(p.name ...)` in `out/` before
and after a request. It could not have observed a socket, a write anywhere else, or a file
replaced in place. It is renamed `test_http_run_leaves_the_out_directory_byte_identical`,
strengthened to a recursive comparison of relative path, size and mtime, and its docstring
states what it does not cover. The containment claim is now carried by a real guard:
`test_evaluate_opens_no_socket_and_no_file_for_writing` patches `builtins.open` and `io.open`
to reject any write mode and `socket.socket` / `socket.create_connection` to raise, then runs
`evaluate()` and `listing()` under those guards. Reads stay allowed, because the manifest
hashes its own module sources.

**TDD, RED then GREEN.** The nine new cases were written first and run against the unmodified
tree: **7 failed, 3 passed**. `KeyError` on the new observer field for both jump tests,
`'(backlog delivered as history)' not in text` for the page, `KeyError: 'snapshot'` for the
manifest claim, and all three drift cases failing because the probe subprocess exited `0` with
a full report (the `missing` case surfaced `FileNotFoundError` rather than a drift rejection).
The three that passed are the renamed directory test, the new in-process containment guard and
the isolated-fixture control, which is there so the drift cases cannot pass merely because the
temporary copy is broken. After the fix: **72 passed** in that module.

**Verification.** `.venv/bin/python -m pytest tests -q`: **385 passed, 4 skipped in 112.32 s**,
against this pass's pre-change baseline of **376 passed, 4 skipped**. The nine added cases are
the only difference. One existing test was renamed and strengthened, as described above; no
other existing test was changed, deleted, skipped or weakened, and no assertion was relaxed.
The four skips are the same pre-existing optional full-year ERCOT cache tests. All offline.

**Remaining gaps after this pass.** (1) The manifest still proves nothing about executed bytes;
it is a disk snapshot plus drift detection, and it is documented as exactly that in
`docs/uncertainty.md` and in the report body. A real loaded-byte attestation would have to
reach the interpreter's own code objects and was not attempted. (2) `loaded_before_this_module`
records which manifested modules were already imported when the lab module loaded, but it
cannot say what their files held at that earlier moment; for those modules drift detection is
relative to the lab's import, not to their own. (3) Drift is checked before and after the
simulation, not continuously during it; the `during` test injects drift inside the simulate
call, which the post-run check catches, but a file edited and restored between the two checks
would pass. (4) The containment guard covers the evaluator in process. The HTTP handler itself
necessarily uses a socket, so no test claims otherwise. (5) The remaining gaps from the entry
above are unchanged: no real-browser harness, no shell invocation of the CLI outside pytest,
four mechanisms only, one controller, and no calibration.
