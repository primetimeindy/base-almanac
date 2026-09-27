"""Experimental offline simulator of command and telemetry uncertainty.

This module is **additive and experimental**. It does not replace `almanac.fleet`, it does
not touch the frozen legacy receipt schemas, it has no live adapter, no device I/O and no
network call. The experimental `almanac.uncertainty_lab` surface runs it over a closed
registry of registered synthetic scenarios and is the only thing that exposes it to a CLI,
a loopback route or a page; nothing reaches this module from a caller except a registered
scenario, and nothing here dispatches. It exists to model one thing the
existing engine assumes away: a controller that can only see delivered telemetry packets
and delivered acknowledgements, and therefore cannot know what a device is currently doing.

Everything here is synthetic. Durations, capacities, reserves, targets, the fault schedule
and the loss schedule are invented inputs, not measurements, and none of the defaults claim
to be production values. Energy and power are exact `Fraction` arithmetic; floats appear
only when a caller asks for a presentation value.

Two axes are kept deliberately separate, because conflating them is the modelling error
this module exists to avoid:

* **Physical state** — what a device is really doing: `grid-connected`, `islanded`,
  `non-operational`. Only a grid-connected device delivers energy to the grid; islanded and
  non-operational devices deliver exactly zero. No house load is simulated.
* **Observation freshness** — what the controller can currently see about a device:
  `reporting`, `late`, `unreachable`. This is a property of the link and the clock, never of
  the device's physics. The two overlap freely: a non-operational device that is also
  silent is a normal case, not a special one.

Event order at each simulated second is fixed and total:

1. **Expiry** — any command whose absolute TTL has passed lapses, on the device and in the
   controller's knowledge. Absolute TTL runs on wall time and keeps running during an
   outage; it is never restarted by a reconnect.
2. **Faults** — scheduled physical-state and link changes apply.
3. **Telemetry** — every powered device samples into its own bounded buffer, then the link
   delivers whatever it can. A sample keeps the time it was measured, so a packet delivered
   after an outage reads as history, never as a current measurement.
4. **ACKs already due** — acknowledgements from earlier seconds land before the decision.
5. **Decision** — on a control tick the controller sees delivered packets and its own
   command/ACK history, and nothing else. The proposal is then gated, and an unsafe or
   malformed one is refused before any dispatch.
6. **ACKs generated now** — an acknowledgement with no delay lands in the same second its
   command was issued, before physics and before any metric is taken. Nothing is backdated:
   such an ACK is delivered at this timestamp, not earlier.
7. **Physics** — the step is integrated and energy is moved.

The controller is a bounded built-in conservative controller, not a generic policy adapter.
That is a deliberate scope limit: see `CONTROLLER_LIMITS`.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field, replace
from fractions import Fraction

SCHEMA = 'almanac.uncertainty.v1'
MODE = 'offline-experimental-simulation-only'

# Physical state: what the device is really doing.
GRID_CONNECTED = 'grid-connected'
ISLANDED = 'islanded'
NON_OPERATIONAL = 'non-operational'
PHYSICAL_STATES = (GRID_CONNECTED, ISLANDED, NON_OPERATIONAL)

# Observation freshness: what the controller can currently see.
REPORTING = 'reporting'
LATE = 'late'
UNREACHABLE = 'unreachable'

# Bounds. Small on purpose: this is a deterministic unit-test-scale simulator.
MAX_DURATION_S = 86_400
MAX_DEVICES = 64
MAX_PERIOD_S = 3_600
MAX_SEQUENCE = 1_000_000  # an upper bound on any command sequence a scenario may name
MAX_BUFFERED_SAMPLES = 16  # bounded offline sample buffer, per device
SECONDS_PER_HOUR = Fraction(3600)

ASSUMPTIONS = (
    'Every number in a scenario is a synthetic assumption: capacities, reserves, initial '
    'energy, target, fault times and loss schedules are invented inputs, not measurements.',
    'Only grid-connected devices deliver to the grid. Islanded and non-operational devices '
    'deliver exactly zero. No household or site load is simulated, so nothing here shows '
    'that any home stayed powered.',
    'Reserve is an energy floor inside the simulation, enforced by an explicit local clamp '
    'at the device. It is not evidence about any real site.',
    'Devices are perfect followers of the command they actually hold: a received setpoint '
    'is tracked exactly, subject to capacity and the reserve clamp. Ramp rates, efficiency '
    'and measurement error are not modelled.',
    'Command loss, ACK loss and telemetry loss come from an explicit deterministic '
    'schedule, or from a seeded RNG the controller never observes.',
)

CONTROLLER_LIMITS = (
    'The controller here is a bounded built-in conservative controller, not a generic '
    'policy adapter. A pluggable adapter for this knowledge state is not implemented.',
    'It is conservative by construction, not optimal: it respects the worst case over every '
    'command that might still be active, so it leaves capacity unused whenever it is '
    'uncertain, and it waits rather than act when it has no confirmed headroom.',
    'No claim is made that this controller is correct for any real fleet, or that its '
    'uncertainty bound is the tightest sound one.',
)


class ScenarioError(ValueError):
    """A scenario was rejected before any state was mutated."""


def _integer_seconds(name: str, value: object, *, minimum: int = 1, maximum: int = MAX_PERIOD_S) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ScenarioError(f'{name} must be an integer number of seconds, got {value!r}')
    if not minimum <= value <= maximum:
        raise ScenarioError(f'{name} must be in [{minimum}, {maximum}] seconds, got {value}')
    return value


def _finite(name: str, value: object, *, minimum: Fraction | None = None) -> Fraction:
    """Accept only exactly representable finite numerics; reject float NaN/inf outright."""
    if isinstance(value, bool) or isinstance(value, complex):
        raise ScenarioError(f'{name} must be a finite number, got {value!r}')
    if isinstance(value, float):
        if value != value or value in (float('inf'), float('-inf')):
            raise ScenarioError(f'{name} must be finite, got {value!r}')
        exact = Fraction(value).limit_denominator(1_000_000)
    elif isinstance(value, (int, Fraction)):
        exact = Fraction(value)
    elif isinstance(value, str):
        try:
            exact = Fraction(value)
        except (ValueError, ZeroDivisionError) as exc:
            raise ScenarioError(f'{name} is not a valid number: {value!r}') from exc
    else:
        raise ScenarioError(f'{name} must be a finite number, got {value!r}')
    if minimum is not None and exact < minimum:
        raise ScenarioError(f'{name} must be >= {minimum}, got {exact}')
    return exact


def _sequence_id(name: str, value: object) -> int:
    """A command sequence number: a plain positive integer, never a bool or a string."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ScenarioError(f'{name} must be an integer command sequence number, got {value!r}')
    if not 1 <= value <= MAX_SEQUENCE:
        raise ScenarioError(f'{name} must be in [1, {MAX_SEQUENCE}], got {value}')
    return value


def _sequence_set(name: str, value: object) -> frozenset[int]:
    """Validate a loss schedule. Duplicate ids are a restatement, not an error."""
    if value is None:
        return frozenset()
    if isinstance(value, (str, bytes, bytearray, dict)) or \
            not isinstance(value, (list, tuple, set, frozenset)):
        raise ScenarioError(f'{name} must be a list or set of command sequence numbers, '
                            f'got {value!r}')
    return frozenset(_sequence_id(f'{name} entry', item) for item in value)


def _ack_delays(value: object, duration_s: int) -> dict[int, int]:
    """Validate the ACK delay schedule: sequence id -> whole seconds within the run."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ScenarioError('ack_delay_s must be a mapping of command sequence number to '
                            f'a delay in seconds, got {value!r}')
    delays: dict[int, int] = {}
    for key, delay in value.items():
        sequence = _sequence_id('ack_delay_s key', key)
        # The bound is on the delay itself, not on the arrival time. An ACK for a command
        # issued at `issued_s` is due at `issued_s + delay`, which may fall beyond
        # `duration_s`; such an ACK is simply never delivered inside the run, so the command
        # stays unconfirmed until its own TTL. This caps the schedule, it promises nothing
        # about arrival.
        delays[sequence] = _integer_seconds(f'ack_delay_s[{sequence}]', delay, minimum=0,
                                            maximum=duration_s)
    return delays


@dataclass(frozen=True)
class Command:
    """One dispatch instruction with an absolute expiry and a total order."""
    command_id: str
    device_id: str
    setpoint_kw: Fraction
    issued_s: int
    expires_s: int
    sequence: int

    def expired_at(self, now_s: int) -> bool:
        """Absolute TTL. The boundary second itself is expired, not still valid."""
        return now_s >= self.expires_s


@dataclass(frozen=True)
class DeviceSpec:
    device_id: str
    capacity_kw: Fraction
    energy_kwh: Fraction
    reserve_kwh: Fraction


@dataclass(frozen=True)
class Scenario:
    """A validated synthetic scenario. Construct through `build_scenario`."""
    scenario_id: str
    duration_s: int
    control_period_s: int
    telemetry_period_s: int
    inactivity_timeout_s: int
    command_ttl_s: int
    target_kw: Fraction
    seed: int
    devices: tuple[DeviceSpec, ...]
    faults: tuple[dict, ...]
    drop_commands: frozenset[int]
    drop_acks: frozenset[int]
    ack_delay_s: dict[int, int]


TINY_FIXTURE = {
    'scenario_id': 'uncertainty-tiny-synthetic-v1',
    'duration_s': 120,
    'control_period_s': 20,
    'telemetry_period_s': 10,
    'inactivity_timeout_s': 30,
    'command_ttl_s': 60,
    'target_kw': 10,
    'seed': 1,
    'devices': [
        {'device_id': 'd1', 'capacity_kw': 8, 'energy_kwh': 5, 'reserve_kwh': 1},
        {'device_id': 'd2', 'capacity_kw': 8, 'energy_kwh': 5, 'reserve_kwh': 1},
    ],
    'faults': [],
}


def build_scenario(spec: dict | None = None) -> Scenario:
    """Validate a scenario fully before anything is simulated.

    The default is the tiny synthetic fixture above, which is a test fixture and not a
    claim about any production configuration.
    """
    raw = dict(TINY_FIXTURE if spec is None else spec)
    if not isinstance(raw.get('scenario_id', ''), str):
        raise ScenarioError('scenario_id must be a string')
    duration_s = _integer_seconds('duration_s', raw.get('duration_s'), maximum=MAX_DURATION_S)
    control_period_s = _integer_seconds('control_period_s', raw.get('control_period_s'))
    telemetry_period_s = _integer_seconds('telemetry_period_s', raw.get('telemetry_period_s'))
    inactivity_timeout_s = _integer_seconds('inactivity_timeout_s', raw.get('inactivity_timeout_s'))
    command_ttl_s = _integer_seconds('command_ttl_s', raw.get('command_ttl_s'))
    seed = raw.get('seed', 0)
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2 ** 32:
        raise ScenarioError(f'seed must be an integer in [0, 2**32), got {seed!r}')
    target_kw = _finite('target_kw', raw.get('target_kw'), minimum=Fraction(0))  # zero is permitted

    device_specs = raw.get('devices')
    if not isinstance(device_specs, (list, tuple)) or not device_specs:
        raise ScenarioError('devices must be a non-empty list')
    if len(device_specs) > MAX_DEVICES:
        raise ScenarioError(f'at most {MAX_DEVICES} devices, got {len(device_specs)}')
    devices: list[DeviceSpec] = []
    seen: set[str] = set()
    for entry in device_specs:
        if not isinstance(entry, dict):
            raise ScenarioError(f'each device must be a mapping, got {entry!r}')
        device_id = entry.get('device_id')
        if not isinstance(device_id, str) or not device_id:
            raise ScenarioError(f'device_id must be a non-empty string, got {device_id!r}')
        if device_id in seen:
            raise ScenarioError(f'duplicate device_id {device_id!r}')
        seen.add(device_id)
        capacity_kw = _finite(f'{device_id}.capacity_kw', entry.get('capacity_kw'), minimum=Fraction(0))
        energy_kwh = _finite(f'{device_id}.energy_kwh', entry.get('energy_kwh'), minimum=Fraction(0))
        reserve_kwh = _finite(f'{device_id}.reserve_kwh', entry.get('reserve_kwh'), minimum=Fraction(0))
        if energy_kwh < reserve_kwh:
            raise ScenarioError(f'{device_id}: initial energy {energy_kwh} kWh is below its '
                                f'reserve floor {reserve_kwh} kWh')
        devices.append(DeviceSpec(device_id, capacity_kw, energy_kwh, reserve_kwh))

    faults = []
    for fault in raw.get('faults', ()) or ():
        if not isinstance(fault, dict):
            raise ScenarioError(f'each fault must be a mapping, got {fault!r}')
        at_s = _integer_seconds('fault.at_s', fault.get('at_s'), minimum=0, maximum=duration_s)
        device_id = fault.get('device_id')
        if device_id not in seen:
            raise ScenarioError(f'fault names unknown device {device_id!r}')
        physical = fault.get('physical')
        if physical is not None and physical not in PHYSICAL_STATES:
            raise ScenarioError(f'unknown physical state {physical!r}')
        link = fault.get('link')
        if link not in (None, 'up', 'down'):
            raise ScenarioError(f"link must be 'up' or 'down', got {link!r}")
        if physical is None and link is None:
            raise ScenarioError('a fault must change physical state, link state or both')
        faults.append({'at_s': at_s, 'device_id': device_id, 'physical': physical, 'link': link})

    return Scenario(
        scenario_id=raw.get('scenario_id', 'unnamed-synthetic'), duration_s=duration_s,
        control_period_s=control_period_s, telemetry_period_s=telemetry_period_s,
        inactivity_timeout_s=inactivity_timeout_s, command_ttl_s=command_ttl_s,
        target_kw=target_kw, seed=seed, devices=tuple(devices),
        faults=tuple(sorted(faults, key=lambda f: (f['at_s'], f['device_id']))),
        drop_commands=_sequence_set('drop_commands', raw.get('drop_commands')),
        drop_acks=_sequence_set('drop_acks', raw.get('drop_acks')),
        ack_delay_s=_ack_delays(raw.get('ack_delay_s'), duration_s),
    )


@dataclass
class TruthDevice:
    """Simulator-side truth. The controller never reads this."""
    spec: DeviceSpec
    energy_kwh: Fraction
    physical: str = GRID_CONNECTED
    link_up: bool = True
    active: Command | None = None
    buffered_packets: list[dict] = field(default_factory=list)
    dropped_samples: int = 0

    @property
    def device_id(self) -> str:
        return self.spec.device_id

    def samples_now(self) -> bool:
        """A powered device samples even with no link. A non-operational one samples nothing.

        An islanded device is still powered and still measures itself, so its samples are
        buffered for later delivery rather than lost.
        """
        return self.physical != NON_OPERATIONAL

    def delivers_now(self) -> bool:
        """Delivery needs both a link and a device that is powered enough to use it."""
        return self.link_up and self.physical != NON_OPERATIONAL

    def buffer_sample(self, packet: dict) -> None:
        """Hold one sample. The buffer is bounded: the oldest sample is dropped first."""
        self.buffered_packets.append(packet)
        while len(self.buffered_packets) > MAX_BUFFERED_SAMPLES:
            self.buffered_packets.pop(0)
            self.dropped_samples += 1

    def setpoint_kw(self) -> Fraction:
        return self.active.setpoint_kw if self.active is not None else Fraction(0)

    def deliverable_kw(self, step_s: int) -> Fraction:
        """Power actually delivered to the grid over the next step, after the local clamp.

        Islanded and non-operational devices deliver exactly zero to the grid. The reserve
        clamp is local to the device: it never discharges below its reserve floor, whatever
        it was commanded to do.
        """
        if self.physical != GRID_CONNECTED:
            return Fraction(0)
        wanted = min(self.setpoint_kw(), self.spec.capacity_kw)
        if wanted <= 0:
            return Fraction(0)
        available_kwh = self.energy_kwh - self.spec.reserve_kwh
        if available_kwh <= 0:
            return Fraction(0)
        return min(wanted, available_kwh * SECONDS_PER_HOUR / step_s)


@dataclass
class DeviceKnowledge:
    """Everything the controller is allowed to know about one device."""
    device_id: str
    last_sample_s: int | None = None
    last_power_kw: Fraction = Fraction(0)
    last_energy_kwh: Fraction | None = None
    reported_physical: str | None = None
    confirmed: Command | None = None
    unconfirmed: list[Command] = field(default_factory=list)
    stale_backfill_samples: int = 0   # duplicates and out-of-order arrivals
    backfilled_samples: int = 0       # delivered later than they were sampled
    max_sample_age_s: int = 0         # the oldest sample this controller ever had to accept

    def freshness(self, now_s: int, telemetry_period_s: int, inactivity_timeout_s: int) -> str:
        if self.last_sample_s is None:
            return UNREACHABLE if now_s > inactivity_timeout_s else LATE
        age = now_s - self.last_sample_s
        if age > inactivity_timeout_s:
            return UNREACHABLE
        return REPORTING if age <= telemetry_period_s else LATE

    def drop_expired(self, now_s: int) -> None:
        if self.confirmed is not None and self.confirmed.expired_at(now_s):
            self.confirmed = None
        self.unconfirmed = [c for c in self.unconfirmed if not c.expired_at(now_s)]

    def possible_active(self, now_s: int) -> list[Command]:
        """Mutually exclusive histories of what this device might be doing right now.

        Each entry is one *alternative*, not a concurrent command: either the last
        confirmed command is still running (every later command was lost), or one of the
        unconfirmed commands was received. That is why the bound below is a maximum over
        this list and never a sum: a device runs at most one setpoint at a time.
        """
        options = [c for c in ([self.confirmed] if self.confirmed else []) + self.unconfirmed
                   if not c.expired_at(now_s)]
        return options

    def bound_kw(self, now_s: int) -> Fraction:
        """Worst-case power this device may currently be producing, as far as anyone knows.

        A command that was issued but not acknowledged may be running, and the command it
        replaced may equally still be running because the replacement was lost. Both are
        counted, and the maximum is taken. In particular an unconfirmed *stop* frees
        nothing: the higher old setpoint stays in the bound until an ACK confirms the stop.
        """
        options = self.possible_active(now_s)
        if not options:
            return Fraction(0)
        return max(c.setpoint_kw for c in options)

    def pending(self, now_s: int) -> bool:
        return bool([c for c in self.unconfirmed if not c.expired_at(now_s)])


def _ingest_packet(know: DeviceKnowledge, packet: dict, now_s: int) -> str:
    """Fold one *delivered* telemetry packet into the controller's knowledge.

    Returns what the packet was: a `current` reading, a `historical` one that was sampled
    before it could be delivered, or a `stale` one that is no newer than what is already
    known (a duplicate or an out-of-order arrival).

    The sample time is kept as it was measured. It is never moved forward to the delivery
    second, so a backfilled packet reads as old — which is what it is — and a device's
    physical state at some earlier second is never presented as its state now.
    """
    age_s = now_s - packet['sample_s']
    if age_s < 0:  # a sample from the future cannot be delivered; the caller is broken
        raise ScenarioError(f"sample at {packet['sample_s']} s delivered at {now_s} s")
    if know.last_sample_s is not None and packet['sample_s'] <= know.last_sample_s:
        know.stale_backfill_samples += 1  # history only, never current
        return 'stale'
    know.last_sample_s = packet['sample_s']
    know.last_power_kw = packet['power_kw']
    know.last_energy_kwh = packet['energy_kwh']
    know.reported_physical = packet['physical']
    know.max_sample_age_s = max(know.max_sample_age_s, age_s)
    if age_s > 0:
        know.backfilled_samples += 1
        return 'historical'
    return 'current'


def _apply_ack(know: DeviceKnowledge, command: Command, now_s: int) -> str:
    """Reconcile one delivered acknowledgement into the controller's knowledge.

    An ACK is proof that the device received *this* command. Because a device only ever
    accepts a command newer than the one it holds, every older command becomes impossible
    the moment this one is acknowledged — whether or not any newer command is outstanding.
    Leaving the older ones in the possible set is what used to keep an impossible high
    bound alive until its TTL after an acknowledged stop.

    Returns `superseded` when the ACK is older than what is already confirmed (it teaches
    nothing and must not resurrect anything), `expired` when the acknowledged command has
    itself already lapsed (the device dropped it, so nothing is confirmed), or `confirmed`.
    Commands newer than this one are always retained: an ACK can never discard them.
    """
    know.unconfirmed = [c for c in know.unconfirmed if c.sequence != command.sequence]
    if know.confirmed is not None and know.confirmed.sequence >= command.sequence:
        return 'superseded'
    # Older commands cannot still be running on a device that took this one.
    know.unconfirmed = [c for c in know.unconfirmed if c.sequence > command.sequence]
    if command.expired_at(now_s):
        know.confirmed = None  # it lapsed on the device too; it is not a current state
        return 'expired'
    know.confirmed = command
    return 'confirmed'


def _deliver_due_acks(pending_acks: list[dict], knowledge: dict[str, DeviceKnowledge],
                      now_s: int) -> None:
    """Deliver every acknowledgement due at or before `now_s`, in issue order."""
    due = sorted((e for e in pending_acks if e['deliver_s'] <= now_s),
                 key=lambda e: (e['deliver_s'], e['command'].sequence))
    for entry in due:
        pending_acks.remove(entry)
        command = entry['command']
        _apply_ack(knowledge[command.device_id], command, now_s)


def _conservative_decision(knowledge: dict[str, DeviceKnowledge], capacities: dict[str, Fraction],
                           freshness: dict[str, str], now_s: int,
                           target_kw: Fraction) -> dict[str, Fraction]:
    """Bounded built-in conservative controller over the uncertain knowledge state.

    It never proposes a set of commands whose worst-case total can exceed the target, where
    worst case means: for every device, the largest setpoint that might currently be active
    (see `DeviceKnowledge.bound_kw`). Devices are visited in a fixed order, so the decision
    is a pure function of the knowledge state. If there is no headroom it returns no
    commands at all: waiting is the correct conservative action, not commanding zero, and
    commanding zero would not reduce the bound anyway until it is acknowledged.
    """
    committed = sum((k.bound_kw(now_s) for k in knowledge.values()), Fraction(0))
    headroom = target_kw - committed
    proposals: dict[str, Fraction] = {}
    for device_id in sorted(knowledge):
        if headroom <= 0:
            break
        know = knowledge[device_id]
        bound = know.bound_kw(now_s)
        if freshness[device_id] == UNREACHABLE:  # unknown output: do not add load blindly
            continue
        room = min(headroom, capacities[device_id] - bound)
        if room <= 0:
            continue
        proposals[device_id] = bound + room
        headroom -= room
    return proposals


def _gate_decision(proposals: object, knowledge: dict[str, DeviceKnowledge],
                   capacities: dict[str, Fraction], now_s: int,
                   target_kw: Fraction) -> tuple[dict[str, Fraction], str | None]:
    """Judge a proposed decision **before** anything is dispatched. Fail closed.

    Either the whole decision is accepted, or nothing is issued and a diagnostic string
    explains why. A partially accepted decision is not offered: the proposal is one joint
    action, and half of it is not a safe version of it.
    """
    if not isinstance(proposals, dict):
        return {}, f'decision must be a mapping of device_id to setpoint, got {proposals!r}'
    worst_case = sum((k.bound_kw(now_s) for k in knowledge.values()), Fraction(0))
    accepted: dict[str, Fraction] = {}
    for device_id in sorted(proposals, key=str):
        if device_id not in knowledge:
            return {}, f'decision names unknown device {device_id!r}'
        try:
            setpoint_kw = _finite(f'decision.{device_id}', proposals[device_id],
                                  minimum=Fraction(0))
        except ScenarioError as exc:
            return {}, str(exc)
        if setpoint_kw > capacities[device_id]:
            return {}, (f'decision would command {device_id} to {setpoint_kw} kW, above its '
                        f'{capacities[device_id]} kW capacity')
        worst_case += max(Fraction(0), setpoint_kw - knowledge[device_id].bound_kw(now_s))
        accepted[device_id] = setpoint_kw
    if worst_case > target_kw:
        return {}, (f'decision worst case {worst_case} kW exceeds the {target_kw} kW target')
    return accepted, None


def simulate(scenario: Scenario | dict | None = None, *, decide=None) -> dict:
    """Run the experimental simulation and return a result with metrics and a trace.

    Nothing about the outcome is hardcoded: every metric below is integrated from the
    simulated trajectory, including the failure metrics.

    `decide` is an **internal, in-process test seam**: a callable with the signature of
    `_conservative_decision`, used only to drive an unsafe or naive proposal through the
    real gate. It is not a policy adapter — there is no HTTP, no dynamic import and no
    configuration path to it — and whatever it proposes is subject to the same gate as the
    built-in controller.
    """
    if not isinstance(scenario, Scenario):
        scenario = build_scenario(scenario)
    decide = _conservative_decision if decide is None else decide
    rng = random.Random(scenario.seed)  # never observed by the controller
    step_s = 1
    devices = {spec.device_id: TruthDevice(spec, spec.energy_kwh) for spec in scenario.devices}
    capacities = {spec.device_id: spec.capacity_kw for spec in scenario.devices}
    knowledge = {spec.device_id: DeviceKnowledge(spec.device_id) for spec in scenario.devices}
    faults_by_time: dict[int, list[dict]] = {}
    for fault in scenario.faults:
        faults_by_time.setdefault(fault['at_s'], []).append(fault)

    pending_acks: list[dict] = []  # {'deliver_s': int, 'command': Command}
    issued: list[Command] = []
    trace: list[dict] = []
    delivered_kwh = Fraction(0)
    reserve_violations = 0
    max_overshoot_kw = Fraction(0)
    gate_violations = 0
    gate_rejections: list[dict] = []
    sequence = 0
    ever_pending: set[str] = set()
    uncertain_device_seconds = 0

    for now_s in range(0, scenario.duration_s, step_s):
        # 1. Expiry, before any delivery or decision at this timestamp.
        for device in devices.values():
            if device.active is not None and device.active.expired_at(now_s):
                device.active = None
        for know in knowledge.values():
            know.drop_expired(now_s)

        # 2. Faults, before telemetry: a device that goes non-operational now does not
        #    report as healthy at this timestamp.
        for fault in faults_by_time.get(now_s, ()):
            device = devices[fault['device_id']]
            if fault['physical'] is not None:
                device.physical = fault['physical']
            if fault['link'] is not None:
                device.link_up = fault['link'] == 'up'

        # 3. Telemetry: sample into the device's own bounded buffer, then deliver whatever
        #    the link can carry. A powered device keeps sampling through an outage, so a
        #    reconnect delivers genuine history; history is never read as a current value.
        if now_s % scenario.telemetry_period_s == 0:
            for device in devices.values():
                if not device.samples_now():
                    continue  # a non-operational device measures nothing at all
                device.buffer_sample({'device_id': device.device_id, 'sample_s': now_s,
                                      'power_kw': device.deliverable_kw(step_s),
                                      'energy_kwh': device.energy_kwh,
                                      'physical': device.physical})
        for device in devices.values():
            if not device.delivers_now():
                continue  # nothing leaves the device; its buffer holds until it can
            for packet in device.buffered_packets:  # oldest first, so the newest one wins
                _ingest_packet(knowledge[device.device_id], packet, now_s)
            device.buffered_packets = []

        # 3b. ACKs already due. A delayed ACK confirms only the command it belongs to, and
        #     can never resurrect a command older than what the controller already knows.
        _deliver_due_acks(pending_acks, knowledge, now_s)

        freshness = {d: knowledge[d].freshness(now_s, scenario.telemetry_period_s,
                                               scenario.inactivity_timeout_s) for d in knowledge}

        # 4. Decision, from delivered packets and command/ACK history only.
        decision: dict[str, Fraction] = {}
        if now_s % scenario.control_period_s == 0:
            proposed = decide(knowledge, capacities, freshness, now_s, scenario.target_kw)
            decision, rejection = _gate_decision(proposed, knowledge, capacities, now_s,
                                                 scenario.target_kw)
            if rejection is not None:
                # Fail closed: the proposal is refused here, before any simulated dispatch,
                # and the reason is kept as a diagnostic instead of being silently counted.
                gate_violations += 1
                gate_rejections.append({
                    'time_s': now_s, 'reason': rejection,
                    'proposed': {str(k): str(v) for k, v in proposed.items()}
                    if isinstance(proposed, dict) else None})
            for device_id in sorted(decision):
                sequence += 1
                command = Command(command_id=f'c{sequence}', device_id=device_id,
                                  setpoint_kw=decision[device_id], issued_s=now_s,
                                  expires_s=now_s + scenario.command_ttl_s, sequence=sequence)
                issued.append(command)
                knowledge[device_id].unconfirmed.append(command)
                device = devices[device_id]
                lost = command.sequence in scenario.drop_commands or not device.link_up \
                    or device.physical == NON_OPERATIONAL
                if lost:
                    continue  # the previous command persists on the device until its own TTL
                if device.active is None or device.active.sequence < command.sequence:
                    device.active = command
                if command.sequence not in scenario.drop_acks:
                    delay = scenario.ack_delay_s.get(command.sequence, 0)
                    pending_acks.append({'deliver_s': now_s + delay, 'command': command})

            # 4b. An ACK with no delay arrives in the second its command was issued. It is
            #     delivered here, after the decision and before physics and metrics, so a
            #     fault-free run is not left artificially uncertain by phase order alone.
            #     Nothing is backdated: this ACK is delivered at `now_s`, not before it.
            _deliver_due_acks(pending_acks, knowledge, now_s)

        # 5. Physics: integrate the step.
        step_power_kw = Fraction(0)
        for device in devices.values():
            power_kw = device.deliverable_kw(step_s)
            energy = power_kw * Fraction(step_s) / SECONDS_PER_HOUR
            device.energy_kwh -= energy
            if device.energy_kwh < device.spec.reserve_kwh - Fraction(1, 10 ** 12):
                reserve_violations += 1
            delivered_kwh += energy
            step_power_kw += power_kw
        max_overshoot_kw = max(max_overshoot_kw, step_power_kw - scenario.target_kw)

        for device_id, know in knowledge.items():
            if know.pending(now_s):
                ever_pending.add(device_id)
                uncertain_device_seconds += step_s

        trace.append({
            'time_s': now_s,
            'observed': {  # exactly what the controller could see
                device_id: {'freshness': freshness[device_id],
                            'last_sample_s': knowledge[device_id].last_sample_s,
                            # How old the newest delivered sample is, and whether it was
                            # measured in this very second. A backfilled sample is history.
                            'sample_age_s': (None if knowledge[device_id].last_sample_s is None
                                             else now_s - knowledge[device_id].last_sample_s),
                            'reading_is_current':
                                knowledge[device_id].last_sample_s == now_s,
                            'reported_physical': knowledge[device_id].reported_physical,
                            'possible_active_commands': [c.command_id for c in
                                                         knowledge[device_id].possible_active(now_s)],
                            'worst_case_kw': str(knowledge[device_id].bound_kw(now_s)),
                            'pending_unconfirmed': knowledge[device_id].pending(now_s)}
                for device_id in sorted(knowledge)},
            'truth': {  # evaluator-only; no policy input reads this
                device_id: {'physical': devices[device_id].physical,
                            'link_up': devices[device_id].link_up,
                            'active_command': (devices[device_id].active.command_id
                                               if devices[device_id].active else None),
                            'setpoint_kw': str(devices[device_id].setpoint_kw()),
                            'energy_kwh': str(devices[device_id].energy_kwh)}
                for device_id in sorted(devices)},
            'decision': {device_id: str(value) for device_id, value in sorted(decision.items())},
            'grid_kw': str(step_power_kw),
        })

    requested_kwh = scenario.target_kw * Fraction(scenario.duration_s) / SECONDS_PER_HOUR
    final_worst_case = sum((k.bound_kw(scenario.duration_s) for k in knowledge.values()), Fraction(0))
    return {
        'schema': SCHEMA, 'mode': MODE, 'experimental': True,
        'scenario_id': scenario.scenario_id,
        'config': {'duration_s': scenario.duration_s, 'control_period_s': scenario.control_period_s,
                   'telemetry_period_s': scenario.telemetry_period_s,
                   'inactivity_timeout_s': scenario.inactivity_timeout_s,
                   'command_ttl_s': scenario.command_ttl_s, 'seed': scenario.seed,
                   'target_kw': str(scenario.target_kw), 'devices': len(scenario.devices)},
        'metrics': {
            'requested_kwh': str(requested_kwh),
            'delivered_grid_kwh': str(delivered_kwh),
            'shortfall_kwh': str(max(Fraction(0), requested_kwh - delivered_kwh)),
            'overshoot_kwh': str(max(Fraction(0), delivered_kwh - requested_kwh)),
            'max_overshoot_kw': str(max(Fraction(0), max_overshoot_kw)),
            'reserve_violations': reserve_violations,
            'reserve_preserved': reserve_violations == 0,
            'commands_issued': len(issued),
            'attempted_gate_violations': gate_violations,
            'final_worst_case_kw': str(final_worst_case),
            'stale_backfill_samples': sum(k.stale_backfill_samples for k in knowledge.values()),
            # Telemetry that survived an outage: delivered later than it was sampled, kept
            # as history at its original sample time.
            'backfilled_samples': sum(k.backfilled_samples for k in knowledge.values()),
            'max_sample_age_s': max((k.max_sample_age_s for k in knowledge.values()), default=0),
            'dropped_buffered_samples': sum(d.dropped_samples for d in devices.values()),
            # Devices that were unsure at any point in the run, and how long that lasted in
            # device-seconds. An end-of-run snapshot alone would read empty whenever every
            # outstanding command happened to expire before the last second.
            'devices_with_pending_uncertainty': sorted(ever_pending),
            'uncertain_device_seconds': uncertain_device_seconds,
            'devices_pending_at_end': sorted(
                d for d in knowledge if knowledge[d].pending(scenario.duration_s)),
        },
        'assumptions': list(ASSUMPTIONS),
        'controller_limits': list(CONTROLLER_LIMITS),
        # Every proposal the gate refused, with the reason. Empty for the built-in controller.
        'gate_rejections': gate_rejections,
        'trace': trace,
    }
