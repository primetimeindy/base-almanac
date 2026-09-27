"""Versioned in-process seam for trusted local policy code, not a sandbox.

No import path is accepted over HTTP. The observation contains only current fresh
telemetry and conservative bounds. Invalid actions abort without a receipt.
"""
from __future__ import annotations

import argparse
import importlib
import json
import re
import sys
from fractions import Fraction as F
from typing import Protocol
from almanac.fleet import CONTROL_S, canonical, compare, digest, number, run

OBSERVATION = 'almanac.observation.v1'
ACTIONS = 'almanac.actions.v1'
# Decimal floor avoids advertising capacity above the exact rational limit.
SCALE = 10**9


class OvercommitRejected(ValueError):
    """A proposed action exceeded a device limit or the aggregate dispatch budget.

    A ValueError subclass so existing callers keep their behaviour; the distinct type
    lets a reporting caller say *why* the run aborted without matching on messages.
    """


class Policy(Protocol):
    """Implement a deterministic decision function; instantiate once per run."""
    policy_id: str
    policy_version: str

    def decide(self, observation: dict) -> dict:
        """Return the exact action envelope, or raise to abort the run."""
        ...


def floor_number(value: F) -> float:
    """Convert a nonnegative bound to a conservative nine-place JSON number."""
    return (value * SCALE).__floor__() / SCALE


def decide_checked(policy: Policy, now_s: int, specs: dict, fresh: dict,
                   caps: dict, target: F, unknown: F, lease_bound_s: int) -> dict:
    """Call with detached observations; validate all actions before acceptance.

    Require a command for every fresh device, including zero. Omitting one must
    not leave its old command active outside the aggregate budget.
    """
    budget = max(F(0), target - unknown)
    observation = {
        'schema': OBSERVATION, 'time_s': now_s, 'control_period_s': CONTROL_S,
        'lease_bound_s': lease_bound_s, 'target_kw': floor_number(target),
        'unknown_upper_kw': str(unknown), 'dispatch_budget_kw': floor_number(budget),
        'devices': [
            {'id': i, 'max_kw': floor_number(specs[i]['max_kw']),
             'reserve_kwh': str(specs[i]['reserve_kwh']),
             'sampled_s': fresh[i].sampled_s, 'energy_kwh': str(fresh[i].energy_kwh),
             'dispatch_cap_kw': floor_number(caps[i])}
            for i in sorted(fresh)
        ],
    }
    # JSON detachment: plugin mutation cannot edit the controller's exact bounds.
    action = policy.decide(json.loads(canonical(observation)))
    if type(action) is not dict or set(action) != {'schema', 'powers_kw'} or action['schema'] != ACTIONS:
        raise ValueError('action envelope must be almanac.actions.v1')
    powers = action['powers_kw']
    if type(powers) is not dict or any(type(i) is not str for i in powers) or set(powers) != set(fresh):
        raise ValueError('actions require exactly the fresh device identifiers')
    validated = {i: number(power) for i, power in powers.items()}
    if any(power > caps[i] for i, power in validated.items()):
        raise OvercommitRejected('action exceeds device power or available energy limit')
    if sum(validated.values(), F(0)) > budget:
        raise OvercommitRejected('actions exceed target minus unknown output budget')
    return validated


def compare_policy(scenario: dict, policy: Policy) -> dict:
    """Evaluate trusted policy against both built-ins; raise on invalid output.

    This is not process isolation, a timeout boundary, or arbitrary-code safety.
    Caller is responsible for trusting the module and deterministic behavior.
    """
    identity = {}
    for key, attr in (('id', 'policy_id'), ('version', 'policy_version')):
        value = getattr(policy, attr, None)
        if type(value) is not str or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}', value) is None:
            raise ValueError('bounded policy id/version required')
        identity[key] = value
    if not callable(getattr(policy, 'decide', None)):
        raise ValueError('policy decide method required')
    reference = compare(scenario)
    candidate = run(reference['scenario'], 'external', 10, controller_policy=policy)
    result = {'schema': 'almanac.policy-comparison.v1', 'mode': 'offline-simulation-only',
              'policy': dict(identity, contract=OBSERVATION),
              'reference': reference, 'candidate': candidate,
              'boundary': 'Trusted local code, not sandboxed; no production controller or device integration.'}
    result['receipt_sha256'] = digest(result)
    return result


def integration_contract() -> dict:
    """Describe the actual local seam, consumed by the read-only web UI."""
    return {'schema': 'almanac.integration.v1', 'observation_schema': OBSERVATION,
            'action_schema': ACTIONS, 'http_accepts_policy_code': False,
            'runtime': 'Trusted local Python import, in process, not sandboxed.',
            'command': 'PYTHONPATH=src:. python -m almanac.controller_contract --policy examples.priority_policy:Policy --area core',
            'boundary': 'Implement decide(observation), return powers for every fresh device. Invalid actions abort. No future fault schedule or plant ground truth is passed.',
            'production': 'No physical devices, controller credentials, transport adapter or production safety guarantee.'}


def main() -> None:
    """Import only an explicitly trusted local module and write receipt to stdout."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--policy', required=True, help='trusted import module:zero_argument_factory')
    parser.add_argument('--area', choices=('core', 'wide', 'west', 'empty', 'all'), default='core')
    args = parser.parse_args()
    if re.fullmatch(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*', args.policy) is None:
        parser.error('use module:factory')
    from almanac.geofleet import experiment
    # Validate bundled source before loading any user-selected module.
    scenario = experiment({'area': args.area})['simulation']['scenario']
    module, factory = args.policy.split(':')
    try:
        policy = getattr(importlib.import_module(module), factory)()
        receipt = compare_policy(scenario, policy)
    except (ValueError, TypeError, ImportError, AttributeError) as exc:
        parser.exit(2, f'No receipt generated: {exc}\n')
    sys.stdout.write(canonical(receipt) + '\n')


if __name__ == '__main__':
    main()
