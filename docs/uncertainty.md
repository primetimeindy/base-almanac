# Experimental: command and telemetry uncertainty

`almanac.uncertainty` is an **additive, experimental, offline** simulator. It does not
replace `almanac.fleet`, it changes no legacy receipt and it has no live adapter. It is a
foundation for later risk work, not a finished probabilistic controller.

The separate experimental surface `almanac.uncertainty_lab` runs it over a closed registry of
registered synthetic scenarios, and is the only thing that exposes it to a command line, a
loopback route or a page. That surface is described at the end of this document, and it is
**not** the legacy offline controller check.

It exists to model the one thing the main engine assumes away: a controller that can only
see **delivered** telemetry packets and **delivered** acknowledgements, and therefore does
not know what a device is currently doing.

## Two axes, never merged

| Physical state (what the device really does) | Observation freshness (what the controller can see) |
| --- | --- |
| `grid-connected` — delivers to the grid | `reporting` — last sample within one telemetry period |
| `islanded` — delivers exactly zero to the grid | `late` — older than that, within the inactivity timeout |
| `non-operational` — delivers exactly zero | `unreachable` — older than the inactivity timeout |

The axes overlap freely. A non-operational device that is also silent is an ordinary case,
not a special one, and the trace reports the two separately: `observed` holds only what the
controller could see, `truth` is evaluator-only.

## Event order at each simulated second

1. **Expiry** — commands past their absolute TTL lapse, on the device and in the
   controller's knowledge, before any delivery or decision at this timestamp.
2. **Faults** — scheduled physical-state and link changes apply, before telemetry, so a
   device that fails now does not report as healthy at this timestamp.
3. **Telemetry** — every powered device samples into its own **bounded offline buffer**, then
   the link delivers whatever it can carry.
4. **ACKs already due** — acknowledgements from earlier seconds land before the decision.
5. **Decision** — the controller reads delivered packets and its own command/ACK history, and
   the proposal is **gated before any dispatch**.
6. **ACKs generated now** — an ACK with no delay lands in the same second its command was
   issued, before physics and before metrics. A fault-free run is therefore not left
   artificially uncertain by phase order, and nothing is backdated: the ACK is delivered at
   this timestamp, not earlier. A one-second TTL is still confirmable by its own ACK.
7. **Physics** — the step is integrated with exact `Fraction` energy.

## Command lifecycle

Every command carries an id, an issue time, an **absolute** expiry and a monotonically
increasing sequence number.

- The absolute TTL runs on wall time. It keeps running through an outage and a reconnect
  never restarts it. The boundary second itself is expired, not still valid.
- A redelivered duplicate never renews the TTL.
- An older, out-of-order or expired command can never supersede a newer one.
- An **acknowledged** command retires every *older* command from the possible set, because a
  device only accepts a command newer than the one it holds. So an acknowledged stop really
  does free capacity, while an unacknowledged one frees nothing. Commands *newer* than the
  acknowledged one are always kept. An ACK older than what is already confirmed teaches
  nothing and resurrects nothing; an ACK that arrives after its own command expired confirms
  nothing, because the device dropped that command too.
- **Command receipt and ACK delivery are separate.** A dropped command means the previous
  command keeps running until its own TTL. A dropped ACK means a received command may well
  be executing while the controller cannot assume it was confirmed.
- An omitted device keeps its prior command until that command expires.
- A delayed ACK confirms only its own command and cannot override knowledge of a newer one.

## What the controller may conclude

Per device the controller keeps a bounded set of **mutually exclusive** histories: either
the last confirmed command is still running because every later one was lost, or one of the
unconfirmed commands was received. A device runs at most one setpoint at a time, so the
per-device worst case is the **maximum** over that set, never the sum of old and new. Those
per-device maxima are then summed across devices.

Consequences the tests pin down:

- A pending replacement is not counted twice on one device.
- An unconfirmed **stop** frees nothing: the higher old setpoint stays in the bound until an
  ACK confirms the stop.
- A device reporting fresh telemetry while a command is pending is still uncertain.
  Telemetry is not an acknowledgement.
- A reconnect delivers **real backlog**. A powered device — grid-connected or islanded —
  keeps sampling while its link is down, into a buffer bounded at 16 samples per device that
  drops its oldest entry when full. Each sample keeps the time it was measured, so what
  arrives after an outage is labelled history: the trace carries `sample_age_s` and
  `reading_is_current`, and the metrics carry `backfilled_samples`, `max_sample_age_s`,
  `dropped_buffered_samples` and `stale_backfill_samples` (duplicates and out-of-order
  arrivals). A backfilled sample is never promoted to a current measurement and no sample is
  ever dated in the future. A non-operational device measures nothing at all.
- Pending command history is reconciled only by an actual acknowledgement, never because the
  link came back.

## The gate refuses, it does not merely count

Every proposed decision — from the built-in controller or from the internal test seam — is
judged before anything is dispatched, and the whole proposal is refused if its worst case
exceeds the target, if it names an unknown device, or if a setpoint is negative, non-finite
or above the device's capacity. A refused decision issues **nothing** (fail closed, not
partially applied) and its reason is kept in `gate_rejections` alongside the
`attempted_gate_violations` count. The `decide=` parameter of `simulate` is an internal,
in-process test seam for driving a naive or unsafe proposal into that gate: there is no HTTP
path, no dynamic import and no configuration path to it, and it is not the deferred policy
adapter. Counterfactual or experimental policies belong to later frontier work, not here.

## Scenario inputs are validated, including the failure schedules

`drop_commands`, `drop_acks` and `ack_delay_s` are inputs like any other and are validated
before anything is simulated: the containers must be lists/sets (a mapping for
`ack_delay_s`), every sequence id must be a plain integer in `[1, 1_000_000]` — not a bool,
float, string or NaN — and every delay must be a whole number of seconds in
`[0, duration_s]`. Duplicate sequence ids are accepted deliberately: repeating the same
instruction is a restatement, not a new one.
- If everything is uncertain, the controller **waits**. Commanding zero would not reduce the
  bound until it is acknowledged.

## Physics and reserve

Only grid-connected devices deliver to the grid; islanded and non-operational devices
deliver exactly zero. **No household or site load is simulated.** Reserve is an energy floor
enforced by an explicit clamp local to the device: it never discharges below that floor
whatever it was commanded to do. A zero target is permitted; an initial energy below the
reserve floor is rejected.

## Limits, stated plainly

- Everything is a synthetic assumption: capacities, reserves, initial energy, target, fault
  times and loss schedules are invented inputs, not measurements.
- Reserve preservation here is an energy floor inside a simulation. **Nothing here shows
  that any home stayed powered.**
- Devices are perfect followers of the command they hold, subject to capacity and the
  reserve clamp. Ramp rates, efficiency and measurement error are not modelled.
- The controller is a **bounded built-in conservative controller, not a generic policy
  adapter**; a pluggable adapter over this knowledge state is not implemented. It is
  conservative by construction, not optimal, and its bound is not claimed to be the tightest
  sound one.
- No stochastic calibration is attempted. The seeded RNG is never observed by the controller.
- The offline sample buffer is bounded at 16 samples per device and drops the oldest first, so
  a long outage genuinely loses history. That bound is an invented simulation limit, not a
  measured device capability.
- Still **not** built here: a simulator adapter for the real engine, a risk frontier, and any
  integration with the controller-check evaluator or its receipts.

## The experimental surface around it: `almanac.uncertainty_lab`

`almanac.uncertainty_lab` is a **separate, additive, experimental** surface over the simulator
above. It adds a closed registry, a reporting layer, one loopback route pair and one page
section. It does not change `almanac.uncertainty`, and it is **not** the legacy offline
controller check.

| | Legacy controller check | Experimental uncertainty surface |
| --- | --- | --- |
| Module | `almanac.cli` with `almanac.fleet` | `almanac.uncertainty_lab` with `almanac.uncertainty` |
| Report schema | `almanac.check.v1` | `almanac.uncertainty-experiment.v1` |
| Registry | 5 geofleet scenarios, 3 bundled policies | 5 synthetic uncertainty experiments, built-in controller only |
| Verdict | `pass` / `regression` / `unsafe`, with exit codes | none: `safety.verdict` is always `null` |
| Routes | `/api/v1/check/registry`, `/api/v1/check/run` | `/api/v1/uncertainty/registry`, `/api/v1/uncertainty/run` |
| Page section | 4. Offline controller checks | 5. Experimental: command and telemetry uncertainty |

Nothing is shared between the two except the loopback server, its `Host`/`Origin` and framing
guards, and the single `RUN_SLOT` that keeps one simulation running at a time. The legacy
registry, its source manifest, its receipts and its verdicts are untouched.

### The closed registry

Five small bounded deterministic scenarios, all synthetic, each at most 180 simulated seconds
over two devices:

| Key | Declared mechanism | What it exercises |
| --- | --- | --- |
| `acknowledged-baseline` | `none` | every ACK lands in the second its command was issued, so the run is uncertainty free. The contrast case. |
| `ack-loss-unconfirmed` | `ack-loss` | the first two ACKs are dropped: the devices are running commands the controller cannot confirm, so it holds the worst case and waits for the absolute TTL. |
| `ack-delay-late-confirm` | `ack-delay` | the first two ACKs arrive 25 s late, so one run holds an unconfirmed stretch and a confirmed one. |
| `reconnect-backfill` | `reconnect-backfill` | one link is down for 40 s while the device keeps sampling; the reconnect delivers that backlog at its original sample times. |
| `backfill-buffer-overflow` | `buffer-overflow` | a 100 s outage outlasts the 16 sample buffer, so recovered history is genuinely incomplete. |

A registry entry cannot lie about what it exercises. Every report carries
`mechanism_evidence`, which lists the counters the declared mechanism requires, the values
this run produced, and whether they support the claim; a test asserts `satisfied` for every
registered key.

**Keys are the only accepted input.** There is no scenario JSON, no import path, no filesystem
path and no module reference, on the command line or over HTTP.

### What a report separates

- **Command received** (evaluator truth: the simulated device holds it) versus **acknowledged**
  (the controller's only confirmation) versus **telemetry freshness** (`reporting`, `late`,
  `unreachable`: a property of the link and the clock). The per-device `observer` rows carry
  the first two as `acknowledged_command_seconds` / `unacknowledged_command_seconds` and the
  third as a `freshness_seconds` histogram.
- **Observer knowledge versus evaluator truth**, as two separate objects. `observer` holds only
  delivered telemetry and delivered acknowledgements and has no energy or link field at all;
  `evaluator_truth` carries physical state, link, held command, setpoint and energy, and is
  never a controller input.
- **Original sample age on backfill**: `max_sample_age_s` (peak age of the newest reading the
  controller held), `skipped_sample_time_s` (how much sample time the newest reading skipped
  over *beyond one telemetry period*, so the once-per-period advance of a healthy link does
  not count: it is zero in the loss-free baseline, and it is a statement about the
  controller's own view rather than a claim about why those readings were missing) and the
  run level `backfilled_samples`, which is where genuine late delivery is counted,
  `stale_backfill_samples` and `dropped_buffered_samples`.
- **Expiry**: the absolute `command_ttl_s`, the devices still unconfirmed at the end, and
  `observer_truth_divergence_device_seconds`, the device seconds in which the controller's
  possible set and the device's held command disagreed.
- **Reserve**: violations and whether the floor held, with the meaning stated inline: an energy
  floor inside the simulation, not evidence that any household or site stayed powered.

`timeline` is the simulator's own trace, one row per simulated second, bounded by
`MAX_TIMELINE_S` (240) and by the registry itself. The page's per second inspection reads that
array, so every displayed value comes from the one completed run.

### Identity

`code.manifest_sha256` covers the exact source bytes of four modules only:
`almanac.uncertainty` (which produces every number), `almanac.uncertainty_lab` (this registry
and evaluator), `almanac.replay` (the canonicalisation and digest helpers that define the
report identity) and `almanac.cli` (the source hashing helpers reused here). It is **not a
supply-chain identity**: the interpreter, the standard library, installed packages, the
operating system and every other module in this repository are outside it.

It is also **not an attestation of executed code**. The digests are a snapshot of those four
files *on disk*, taken when `almanac.uncertainty_lab` was imported into the process, re-read
and confirmed byte identical before and after every run. Python executes compiled code
objects held in memory, and any of those modules that was already imported before the lab
module loaded (reported as `code.loaded_before_this_module`) ran from whatever its file held
at some earlier moment. Proving the executed bytes is outside this surface, so the claim is
narrowed rather than dressed up. What the manifest does give is **fail-closed drift
detection**: a manifested source file that changes or disappears after the import snapshot
raises `SourceDriftError`, and the run exits `4` as an operational error with no report, no
manifest and no verdict. That matters for the long-running loopback server, which keeps
executing the code it imported at start-up. Alongside it,
`experiment.scenario_sha256` hashes the registered scenario spec and `content_sha256` hashes
the whole report body, so the exported file names the code, the scenario and itself.

### Command line

```
python -m almanac.uncertainty_lab experiments
python -m almanac.uncertainty_lab run --experiment reconnect-backfill
```

This is a separate CLI with its own subcommands, deliberately not a subcommand of `almanac`:
adding one there would change `almanac.cli`'s source bytes and therefore the legacy check's
source manifest and every published check report identity. Exit codes are 0 on success, 3 on
unknown or malformed arguments and 4 on an operational error, and there is no verdict exit
code because this surface never issues a verdict. Both subcommands print canonical JSON to
stdout and write nothing to disk. Browser and CLI use the same registry; with the same
source snapshot and experiment, results and source manifests match. Report bytes need not
match across processes: `code.loaded_before_this_module` records import history and may
differ, which also changes `content_sha256`. Each HTTP download contains the exact bytes
of its own report, not a claim of byte equality with a separate CLI run.

### Loopback surface

`GET /api/v1/uncertainty/registry` returns the registry and takes no query parameters.
`GET /api/v1/uncertainty/run?experiment=<key>` returns one envelope holding the report and the
exact bytes the page exports. The route inherits the server's loopback `Host`/`Origin` gate,
its one request per connection framing limits and its 256 byte request target bound, and adds
a 64 character query bound, strict query parsing, exactly one `experiment` parameter,
membership in the closed registry, and error documents that never echo a rejected value. It
takes the shared `RUN_SLOT` without blocking and answers `503` when the slot is busy, so one
simulation runs at a time across both surfaces. It opens no socket and writes no file, and a
failure is reported as an operational error rather than as a result.

### Not claimed

No safety verdict, no certification, no ranking, no failure rate, no probability and no risk
frontier. The model is synthetic and uncalibrated, every input is invented, and the page says
so where a reader sees the numbers.

## Running a small example

```python
from almanac.uncertainty import simulate

result = simulate()                       # the tiny synthetic default fixture
print(result['metrics']['delivered_grid_kwh'], result['metrics']['uncertain_device_seconds'])

result = simulate({**dict(duration_s=100, control_period_s=20, telemetry_period_s=10,
                          inactivity_timeout_s=30, command_ttl_s=90, target_kw=10, seed=1,
                          devices=[{'device_id': 'd1', 'capacity_kw': 8,
                                    'energy_kwh': 5, 'reserve_kwh': 1}]),
                  'faults': [{'at_s': 30, 'device_id': 'd1',
                              'physical': 'islanded', 'link': 'down'}]})
print(result['metrics']['attempted_gate_violations'])   # 0
```

Metrics are integrated from the trajectory, never hardcoded: delivered grid kWh, shortfall
and overshoot, reserve status, pending uncertainty in device-seconds, backfilled, stale and
dropped sample counts with the oldest sample age, and attempted gate violations with their
reasons. The fault-free default fixture now reports `uncertain_device_seconds` 0, because its
undelayed ACKs land in the second their commands were issued.
