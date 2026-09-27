"""Known local policy fixtures for the milestone-1 controller check.

These are illustrative synthetic controllers, not Base policies and not candidates
for real dispatch. They exist so the check CLI has a fixed, inspectable set of
inputs: one policy that overcommits, one conservative baseline, and one degenerate
zero-output control that exercises the performance-regression gate.

Each implements the same trusted local seam as examples/priority_policy.py: it sees
one observation of current fresh telemetry and conservative bounds, and nothing
about the fault schedule, the plant's ground truth or the future.
"""
from __future__ import annotations

from fractions import Fraction

MICRO = 10**6


def _exact(value: object) -> Fraction:
    """Read an observation number without inheriting binary float error."""
    return Fraction(str(value))


def _floored(value: Fraction) -> float:
    """Floor to microwatts so the JSON number never rounds above the exact bound."""
    return float(Fraction((value * MICRO).__floor__(), MICRO))


def _observation_devices(observation: dict) -> list[dict]:
    if observation['schema'] != 'almanac.observation.v1':
        raise ValueError('unsupported observation contract')
    return observation['devices']


class NaiveTargetFill:
    """Fills toward the raw target and ignores the unknown-output allowance.

    The mistake is deliberate and generic: it treats `target_kw` as the amount it may
    hand out, so it double-counts the output that silent devices may still be
    producing under an unexpired command. The harness's action validator rejects the
    offending proposal before it is dispatched; commands this policy proposed on any
    earlier accepted tick were already dispatched, and the aborted run returns no
    trajectory, so no realized-overshoot figure exists either way.
    """
    policy_id = 'naive-target-fill'
    policy_version = '1.0.0'

    def decide(self, observation: dict) -> dict:
        devices = _observation_devices(observation)
        remaining = _exact(observation['target_kw'])
        powers = {}
        for device in devices:
            power = min(remaining, _exact(device['dispatch_cap_kw']))
            powers[device['id']] = _floored(power)
            remaining -= _exact(powers[device['id']])
        return {'schema': 'almanac.actions.v1', 'powers_kw': powers}


class ConservativeShare:
    """Spends only the dispatch budget, split equally and clamped per device.

    One pass, no refilling of the slack left by reserve-limited devices, so the sum
    stays at or below the budget the harness allows. Conservative here means it never
    asks for the unknown-output allowance, not that it is optimal or safe in reality.
    """
    policy_id = 'conservative-share'
    policy_version = '1.0.0'

    def decide(self, observation: dict) -> dict:
        devices = _observation_devices(observation)
        budget = _exact(observation['dispatch_budget_kw'])
        share = budget / len(devices) if devices else Fraction(0)
        powers = {d['id']: _floored(min(share, _exact(d['dispatch_cap_kw']))) for d in devices}
        return {'schema': 'almanac.actions.v1', 'powers_kw': powers}


class HoldZero:
    """Commands zero everywhere: never unsafe, never useful.

    Kept as a fixture because a policy can be perfectly within the envelope and still
    be a performance regression against the built-in fixed-share reference.
    """
    policy_id = 'hold-zero'
    policy_version = '1.0.0'

    def decide(self, observation: dict) -> dict:
        devices = _observation_devices(observation)
        return {'schema': 'almanac.actions.v1', 'powers_kw': {d['id']: 0 for d in devices}}
