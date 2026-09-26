"""Offline fleet experiment. No I/O, hardware adapters, model calls or dispatch.

Plant state is deliberately unavailable to Controller. Exact rational arithmetic
owns energy and bounds; floats are presentation-only. See docs/fleet-demo.md.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from fractions import Fraction as F
from almanac.replay import canonical, digest
import json

# All constants below are simulation assumptions, NOT Base operating policy.
CONTROL_S = 10
LEASE_BOUND_S = 40


def number(value: object) -> F:
    """Parse a finite nonnegative native JSON number; reject bool/coercion."""
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError('finite nonnegative numeric value required')
    return F(str(value))


def display(value: F) -> float:
    """Round only at the presentation boundary, to nine decimal places."""
    return round(float(value), 9)


def default_scenario() -> dict:
    """Return a detached synthetic fault scenario, never an inferred grid target."""
    ids = [f'B{i:03d}' for i in range(100)]
    return {
        'id': 'lossy-link-100-v1', 'seed': 7, 'duration_s': 600, 'target_kw': 400,
        'lease_policy': {'local_expiry_s': [30, 40], 'lease_bound_s': LEASE_BOUND_S},
        'devices': [{'id': name, 'max_kw': 5, 'capacity_kwh': 3,
                     'initial_energy_kwh': 1.03 if i >= 90 else 2,
                     'reserve_kwh': 1} for i, name in enumerate(ids)],
        'faults': [
            {'start_s': 60, 'end_s': 180, 'kind': 'offline', 'devices': ids[:10]},
            {'start_s': 180, 'end_s': 200, 'kind': 'stale', 'devices': ids[:10]},
            {'start_s': 240, 'end_s': 360, 'kind': 'offline', 'devices': ids[:80]},
            {'start_s': 300, 'end_s': 360, 'kind': 'offline', 'devices': ids},
            {'start_s': 360, 'end_s': 380, 'kind': 'stale', 'devices': ids},
            {'start_s': 420, 'end_s': 460, 'kind': 'stale', 'devices': ids[10:20]},
        ]}


def validate(s: dict) -> None:
    """Validate the bounded experiment domain before constructing any state."""
    if type(s) is not dict or set(s) != {'id', 'seed', 'duration_s', 'target_kw', 'devices', 'faults', 'lease_policy'}:
        raise ValueError('scenario schema')
    if not isinstance(s['id'], str) or not s['id']:
        raise ValueError('scenario id')
    if type(s['seed']) is not int or not 0 <= s['seed'] <= 2**32:
        raise ValueError('seed')
    duration = s['duration_s']
    if type(duration) is not int or not 10 <= duration <= 3600 or duration % CONTROL_S:
        raise ValueError('duration must be 10..3600 seconds on control boundaries')
    number(s['target_kw'])
    policy = s['lease_policy']
    if type(policy) is not dict or set(policy) != {'local_expiry_s', 'lease_bound_s'}:
        raise ValueError('lease policy schema')
    bound = policy['lease_bound_s']
    times = policy['local_expiry_s']
    if type(bound) is not int or not CONTROL_S <= bound <= 600:
        raise ValueError('lease bound must be 10..600 seconds')
    if type(times) is not list or not 1 <= len(times) <= 100 or any(type(t) is not int or not CONTROL_S <= t <= bound for t in times):
        raise ValueError('local expiry must fit the declared bound and control horizon')
    devices = s['devices']
    if type(devices) is not list or not 1 <= len(devices) <= 100:
        raise ValueError('1..100 devices required')
    ids = []
    for d in devices:
        if type(d) is not dict or set(d) != {'id', 'max_kw', 'capacity_kwh', 'initial_energy_kwh', 'reserve_kwh'}:
            raise ValueError('device schema')
        if not isinstance(d['id'], str) or not d['id'] or d['id'] in ids:
            raise ValueError('unique device id required')
        ids.append(d['id'])
        p, c, e, r = (number(d[k]) for k in ('max_kw', 'capacity_kwh', 'initial_energy_kwh', 'reserve_kwh'))
        if not p > 0 or not 0 <= r <= e <= c or not c > 0:
            raise ValueError('device limits')
    if type(s['faults']) is not list or len(s['faults']) > 100:
        raise ValueError('bounded fault list required')
    for fault in s['faults']:
        if type(fault) is not dict or set(fault) != {'start_s', 'end_s', 'kind', 'devices'}:
            raise ValueError('fault schema')
        a, b = fault['start_s'], fault['end_s']
        if any(type(t) is not int or t % CONTROL_S for t in (a, b)) or not 0 <= a < b <= duration:
            raise ValueError('faults must align to control boundaries')
        if fault['kind'] not in ('offline', 'stale') or type(fault['devices']) is not list:
            raise ValueError('fault kind/devices')
        if not fault['devices'] or any(x not in ids for x in fault['devices']) or len(set(fault['devices'])) != len(fault['devices']):
            raise ValueError('fault device identity')


@dataclass(frozen=True)
class Command:
    id: str
    issued_s: int
    power_kw: F
    deadline_s: int


@dataclass(frozen=True)
class Telemetry:
    id: str
    sampled_s: int
    energy_kwh: F


class Device:
    """Ground-truth plant: local power/reserve clamp and absolute lease expiry."""
    def __init__(self, spec: dict, expiry_s: int, lease_bound_s: int = LEASE_BOUND_S):
        self.spec = dict(spec)
        self.energy = number(spec['initial_energy_kwh'])
        self.reserve = number(spec['reserve_kwh'])
        self.maximum = number(spec['max_kw'])
        if type(expiry_s) is not int or not CONTROL_S <= expiry_s <= lease_bound_s:
            raise ValueError('expiry outside declared local bound')
        self.lease_bound_s = lease_bound_s
        self.ttl_s = expiry_s
        self.expires_s = 0
        self.power = F(0)
        self.seen = {}
        self.last_issued_s = -1
        self.integrated_until_s = 0

    def accept(self, command: Command, now_s: int) -> str:
        """Accept an acknowledged in-memory command; duplicates never renew TTL."""
        if command.id in self.seen:
            if self.seen[command.id] != command:
                raise ValueError('idempotency conflict')
            return 'IDEMPOTENT_NOOP'
        if now_s != self.integrated_until_s or command.issued_s != now_s or now_s <= self.last_issued_s or command.deadline_s != now_s + self.lease_bound_s:
            raise ValueError('late/out-of-order command')
        if not 0 <= command.power_kw <= self.maximum:
            raise ValueError('power cap')
        self.seen[command.id] = command
        self.last_issued_s = now_s
        self.power = command.power_kw
        self.expires_s = min(command.deadline_s, now_s + self.ttl_s)
        return 'ACCEPTED'

    def integrate(self, start_s: int, end_s: int) -> F:
        """Advance a disjoint time interval exactly; return delivered kWh."""
        if any(type(t) is not int for t in (start_s, end_s)) or start_s != self.integrated_until_s or end_s <= start_s:
            raise ValueError('physics intervals must be positive, contiguous and non-replayed')
        active_s = max(0, min(end_s, self.expires_s) - start_s)
        used = min(self.energy - self.reserve, self.power * active_s / 3600)
        self.energy -= used
        self.integrated_until_s = end_s
        if not self.reserve <= self.energy <= number(self.spec['capacity_kwh']):
            raise AssertionError('reserve/capacity invariant')
        return used


class Controller:
    """Telemetry-only policy. No plant, fault schedule, RNG or future input."""
    def __init__(self, specs: list[dict], target_kw: F, strategy: str, lease_bound_s: int = LEASE_BOUND_S):
        self.specs = {d['id']: {k: number(d[k]) for k in ('max_kw', 'reserve_kwh')} for d in specs}
        self.lease_bound_s = lease_bound_s
        self.target = target_kw
        self.strategy = strategy
        self.last = {}
        self.fixed = {d['id']: min(number(d['max_kw']), target_kw / len(specs)) for d in specs}

    def decide(self, now_s: int, telemetry: list[Telemetry]) -> dict:
        """Produce commands and conservative bounds using current packets only.

        A packet is actionable only when sampled at this control instant. Unknown
        devices retain [0,last acknowledged power] until their absolute deadline.
        """
        fresh = {p.id: p for p in telemetry if p.sampled_s == now_s}
        uncertain = {i: c.power_kw for i, c in self.last.items()
                     if i not in fresh and now_s < c.deadline_s}
        upper_unknown = sum(uncertain.values(), F(0))
        caps = {i: min(self.specs[i]['max_kw'], max(F(0), p.energy_kwh - self.specs[i]['reserve_kwh']) * 3600 / CONTROL_S)
                for i, p in fresh.items()}
        budget = max(F(0), self.target - upper_unknown)
        if self.strategy == 'baseline':
            powers = {i: min(cap, self.fixed[i]) for i, cap in caps.items()}
        else:
            # Equal-share water filling is constrained, not globally optimal.
            powers = {i: F(0) for i in caps}
            remaining = dict(caps)
            while remaining and budget > 0:
                share = budget / len(remaining)
                saturated = [i for i, cap in remaining.items() if cap <= share]
                if not saturated:
                    for i in remaining:
                        powers[i] = share
                    break
                for i in saturated:
                    powers[i] = remaining.pop(i)
                    budget -= powers[i]
        commands = {i: Command(f'{now_s}:{i}', now_s, power, now_s + self.lease_bound_s) for i, power in powers.items()}
        known = sum(powers.values(), F(0))
        upper = known + upper_unknown
        # Constant target, fixed baseline shares and bounded leases make this exact.
        if upper > self.target:
            raise AssertionError('aggregate upper bound exceeds target')
        self.last.update(commands)  # Simulation transport acknowledges these only.
        reasons = []
        if any(p.sampled_s != now_s for p in telemetry): reasons.append('STALE_TELEMETRY')
        if len(fresh) < len(self.specs): reasons.append('TELEMETRY_UNAVAILABLE')
        if upper_unknown: reasons.append('UNKNOWN_OUTPUT_UNTIL_LEASE_BOUND')
        if any(caps[i] < self.specs[i]['max_kw'] for i in caps): reasons.append('BACKUP_RESERVE_LIMIT')
        if known < self.target: reasons.append('TARGET_SHORTFALL_POSSIBLE')
        if not fresh: reasons.append('NO_FRESH_DEVICES')
        if self.strategy == 'constrained' and any(powers[i] > self.fixed[i] for i in powers): reasons.append('REBALANCED')
        if not reasons: reasons.append('TARGET_TRACKED')
        state = 'UNCERTAIN' if upper_unknown else 'HOLD' if not fresh else 'DEGRADED' if known < self.target else 'NORMAL'
        return {'commands': commands, 'lower': known, 'upper': upper, 'uncertain': upper_unknown,
                'fresh': fresh, 'caps': caps, 'state': state, 'reasons': reasons}


def run(s: dict, strategy: str, step_s: int) -> dict:
    """Execute one strategy from fresh initial state on the same seeded plant."""
    rng = random.Random(s['seed'])
    policy = s['lease_policy']
    plants = {d['id']: Device(d, rng.choice(policy['local_expiry_s']), policy['lease_bound_s']) for d in s['devices']}
    controller = Controller(s['devices'], number(s['target_kw']), strategy, policy['lease_bound_s'])
    cached = {i: Telemetry(i, -CONTROL_S, p.energy) for i, p in plants.items()}
    rows, actions = [], []
    delivered, shortfall = F(0), F(0)
    initial = sum((p.energy for p in plants.values()), F(0))
    counts = {'accepted_commands': 0, 'setpoint_changes': 0, 'uncertain_seconds': 0,
              'hold_seconds': 0, 'degraded_seconds': 0}
    old_power = {i: F(0) for i in plants}
    for t in range(0, s['duration_s'], CONTROL_S):
        offline, stale = set(), set()
        for fault in s['faults']:
            if fault['start_s'] <= t < fault['end_s']:
                (offline if fault['kind'] == 'offline' else stale).update(fault['devices'])
        packets = []
        for i, p in plants.items():
            if i in offline: continue
            if i not in stale: cached[i] = Telemetry(i, t, p.energy)
            packets.append(cached[i])
        decision = controller.decide(t, packets)
        for i, command in decision['commands'].items():
            outcome = plants[i].accept(command, t)
            counts['accepted_commands'] += 1
            if old_power[i] != command.power_kw: counts['setpoint_changes'] += 1
            old_power[i] = command.power_kw
            actions.append({'time_s': t, 'device': i, 'command_id': command.id,
                            'power_kw': display(command.power_kw), 'deadline_s': command.deadline_s,
                            'outcome': outcome, 'reason_codes': decision['reasons']})
        used_by_device = {i: F(0) for i in plants}
        for start in range(t, t + CONTROL_S, step_s):
            for i, plant in plants.items():
                used_by_device[i] += plant.integrate(start, start + step_s)
        used = sum(used_by_device.values(), F(0))
        actual = used * 3600 / CONTROL_S
        if not decision['lower'] <= actual <= decision['upper'] <= controller.target:
            raise AssertionError('delivery outside controller bounds or target')
        delivered += used
        shortfall += (controller.target - actual) * CONTROL_S / 3600
        energy = sum((p.energy for p in plants.values()), F(0))
        if initial - energy != delivered: raise AssertionError('energy accounting')
        state = decision['state']
        count_key = state.lower() + '_seconds'
        if count_key in counts: counts[count_key] += CONTROL_S
        rows.append({
            'time_s': t, 'end_s': t + CONTROL_S, 'state': state, 'reasons': decision['reasons'],
            'target_kw': display(controller.target), 'lower_kw': display(decision['lower']),
            'upper_kw': display(decision['upper']), 'uncertain_upper_kw': display(decision['uncertain']),
            'delivered_kw': display(actual), 'shortfall_kw': display(controller.target - actual),
            'fresh_devices': len(decision['fresh']), 'offline_devices': len(offline),
            'reserve_limited_devices': sum(cap < controller.specs[i]['max_kw'] for i, cap in decision['caps'].items()),
            'energy_remaining_kwh': display(energy), 'cumulative_delivered_kwh': display(delivered),
            'controller_visible': [{'id': p.id, 'sampled_s': p.sampled_s, 'energy_kwh': display(p.energy_kwh),
                                    'accepted_as_fresh': p.id in decision['fresh']} for p in packets],
            'ground_truth_devices': [{'id': i, 'energy_kwh': display(p.energy),
                                      'soc_fraction': display(p.energy / number(p.spec['capacity_kwh'])),
                                      'capacity_kwh': p.spec['capacity_kwh'], 'reserve_kwh': display(p.reserve),
                                      'max_kw': display(p.maximum), 'average_kw': display(used_by_device[i] * 3600 / CONTROL_S),
                                      'local_expiry_s': p.expires_s} for i, p in plants.items()]})
    return {'initial_state_sha256': digest(s['devices']), 'scenario_sha256': digest(s),
            'metrics': dict(counts, delivered_kwh=display(delivered), shortfall_kwh=display(shortfall),
                            requested_kwh=display(controller.target * s['duration_s'] / 3600),
                            exact_delivered_kwh=str(delivered), exact_shortfall_kwh=str(shortfall),
                            max_overshoot_kw=0, reserve_violations=0,
                            sample='one synthetic scenario, not empirical performance'),
            'timeline': rows, 'actions': actions}


def compare(scenario: dict | None = None, step_s: int = 10) -> dict:
    """Return deterministic paired experiment and hash, no external effects.

    step_s subdivides physics only, not the 10-second controller cadence.
    Faults and target are synthetic and never computed from historical prices.
    """
    s = default_scenario() if scenario is None else scenario
    validate(s)
    if type(step_s) is not int or step_s not in (1, 2, 5, 10):
        raise ValueError('physics step must be 1, 2, 5 or 10 seconds')
    s = json.loads(canonical(s))
    result = {
        'schema': 'almanac.fleet.v1', 'mode': 'offline-simulation-only',
        'scenario': s, 'physics_step_s': step_s,
        'assumptions': {
            'control_period_s': CONTROL_S, 'local_expiry_s': s['lease_policy']['local_expiry_s'], 'controller_lease_bound_s': s['lease_policy']['lease_bound_s'],
            'local_expiry': 'absolute deadline, never renewed by duplicate command; guaranteed bound is assumed, not measured',
            'transport': 'instant acknowledged command on fresh connected link; no queued commands; faults at control boundaries',
            'telemetry': 'exact noiseless energy when fresh, stale cached packets rejected, offline output unknown until lease bound',
            'physics': 'discharge-only, 100% efficiency, no ramp, no charge, no household load or network constraints',
            'reserve': 'hard local floor; capacities and reserve are synthetic, not Base limits',
            'baseline': 'fixed equal initial shares renewed only for fresh devices, reserve-clamped; no reallocation',
            'constrained': 'equal-share water filling of fresh capacity after subtracting unknown devices worst-case output',
            'target': 'constant synthetic target; decreasing targets are outside this experiment contract',
            'bounds': '10-second interval average kW; lower is acknowledged model prediction, NOT a measured aggregate',
            'complexity': 'compare command renewals and setpoint changes, not CPU cost or adoption value',
        },
        'provenance': {
            'interpretation': 'Synthetic redistribution experiment with assumed local command expiry, not a validated internal gap or adoption.',
            'historical_context': 'Uri ERCOT Houston LZEW settlement replay remains separate. Not Beryl, outage evidence, or proof of discharge need.',
            'ai_role': 'Hermes development/orchestration only; no AI in control loop',
            'not_claimed': 'No real dispatch, private Base policy, empirical savings, optimality, field safety certification or adoption'},
        'strategies': {name: run(s, name, step_s) for name in ('baseline', 'constrained')},
    }
    result['receipt_sha256'] = digest(result)
    return result


if __name__ == '__main__':
    print(canonical(compare()))
