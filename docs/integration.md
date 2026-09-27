# Local policy integration

This seam is for an engineer who already has a policy function to test against the toy plant. It does not connect to Base systems. The browser continues to compare only the two built-in examples.

## Run the third example

From the checkout root after the README setup:

```sh
PYTHONPATH=src:. python -m almanac.controller_contract \
  --policy examples.priority_policy:Policy --area core > policy-receipt.json
python -c "import json; r=json.load(open('policy-receipt.json')); print(r['policy']); print(r['candidate']['metrics'])"
python -m pytest tests/test_controller_contract.py -q
```

`examples/priority_policy.py` is an actual third policy, not a stub. It fills devices in stable ID order up to the remaining budget. Both built-in references are run unchanged. The candidate uses the same scenario, seed, initial energy and simulated plant. This example is not claimed to be optimal.

## Implement the protocol

Provide a zero-argument class or factory in a trusted local module. It returns an object with `policy_id`, `policy_version`, and `decide(observation)`. IDs and versions contain 1 to 64 ASCII letters, digits, dots, underscores or hyphens, starting with a letter or digit. Instantiate a fresh object for each run. Make decisions deterministic; do not use clocks, unseeded randomness, network calls or global mutable state.

The simulator calls `decide` once every ten synthetic seconds. It passes a detached JSON-compatible dictionary:

| Observation field | Meaning |
| --- | --- |
| `schema` | Exactly `almanac.observation.v1` |
| `time_s`, `control_period_s` | Current relative second and decision interval |
| `lease_bound_s` | Assumed maximum local persistence after a command |
| `target_kw` | Constant requested fleet power, decimal number |
| `unknown_upper_kw` | Exact rational string, conservative output from unavailable devices |
| `dispatch_budget_kw` | Remaining fleet budget, decimal floored to nine places |
| `devices` | Fresh devices only, sorted by ID, at most 100 |

Each device contains exactly `id`, `max_kw`, `reserve_kwh`, `sampled_s`, `energy_kwh`, and `dispatch_cap_kw`. Energy and reserve are exact rational strings, parseable with `fractions.Fraction`. Power cap and maximum are decimal numbers floored to nine places. The cap accounts for available energy above reserve over the next control interval. This rounding prevents the advertised cap from exceeding the internal exact limit.

No future fault schedule, future telemetry, hidden expiry, evaluator state, plant handle or scenario object is passed. When every link is unavailable, `devices` is empty. Stale readings are excluded, not silently made fresh.

Return exactly:

```json
{
  "schema": "almanac.actions.v1",
  "powers_kw": {"B000": 0, "B001": 2.5}
}
```

This tiny illustration assumes only those two IDs are fresh. A real response must include **every fresh ID and no others**, with zero for an intentional stop. With no fresh devices, return an empty `powers_kw` dictionary. Values must be native finite nonnegative Python `int` or `float`, not booleans, numeric strings or custom numeric objects. Floor rather than round when allocating a decimal budget.

Before any command in that decision is accepted, the wrapper checks the envelope, IDs, numeric types, finiteness, nonnegativity, per-device power/energy caps and the aggregate target minus unknown-output budget. Unknown fields, missing commands and invalid values abort. No success receipt is generated on an invalid action or an exception. Previously simulated intervals remain internal and are not sent to hardware.

## Call from Python

```python
from almanac.fleet import default_scenario
from almanac.controller_contract import compare_policy
from examples.priority_policy import Policy

report = compare_policy(default_scenario(), Policy())
print(report['policy'])
```

The protocol comparison receipt has schema `almanac.policy-comparison.v1`. It contains the candidate, the two built-in reference runs, the exact scenario and a policy ID/version. It is separate from the map receipt: it does not embed the map's historical track or no-fault comparison. A declared version is not a source-code signature. Save the code revision alongside the receipt when comparing your own changes.

## Trust boundary

Python imports execute code with your process permissions. Import only code you reviewed and trust, from your own local filesystem. This wrapper is not an execution sandbox and does not enforce timeouts, network isolation or arbitrary-policy determinism. A malicious module can introspect Python or ignore this protocol; it is outside the threat model. There is no HTTP upload/import endpoint and no controller credentials are read or required.

Production work would need independently verified telemetry, transport acknowledgements, stop guarantees, device limits and an isolated execution boundary. None is supplied by this prototype. Action validation proves only that the returned action fits this simulated contract, not that a real fleet is safe.
