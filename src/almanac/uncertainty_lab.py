"""Experimental evaluator over a closed registry of uncertainty scenarios.

This is an **additive experimental surface**. It does not touch `almanac.fleet`, the frozen
legacy receipts, the `almanac` controller-check registry or any existing controller check.
It runs `almanac.uncertainty.simulate` unchanged: no physics, no controller and no gate is
reimplemented here. What this module adds is a small closed registry of bounded synthetic
scenarios, plus a reporting layer that separates three things the legacy check conflates:

* **Command received** by the simulated device, which only the evaluator can see.
* **Command acknowledged** to the controller, which is the only confirmation the controller
  has, and which a lost or delayed acknowledgement withholds while the device runs anyway.
* **Telemetry freshness**, a property of the link and the clock, never of the device physics.

Every scenario is synthetic and the model is uncalibrated. Nothing here is a production
safety verdict: `safety.verdict` is always `None`, and no report ranks, certifies or clears
any controller, policy or device.

The registry is closed by construction. Keys are the only accepted input: no scenario JSON,
no import path, no filesystem path and no module reference reaches this module from a
caller, over HTTP or on the command line.
"""
from __future__ import annotations

import argparse
import sys

# Source bytes that decide what an experiment report says: the simulator that produces every
# number, this registry and evaluator, the canonicalisation and digest helpers that define
# the report identity, and the module providing the source-hashing helpers reused here.
SOURCE_MODULES = ('almanac.cli', 'almanac.replay', 'almanac.uncertainty',
                  'almanac.uncertainty_lab')

# Which of those modules the interpreter had already executed before this module's own
# imports ran. For those the snapshot below is not even an import-time reading of what was
# executed: they were executed from whatever their files held at some earlier moment this
# module cannot observe. This module's own entry is always present in `sys.modules` while it
# is still initialising, so it is excluded rather than reported as pre-existing.
_LOADED_BEFORE = tuple(name for name in SOURCE_MODULES
                       if name != __name__ and name in sys.modules)

from almanac import uncertainty  # noqa: E402  (after the pre-existing-import probe above)
from almanac.cli import Parser, source_digests, source_paths  # noqa: E402
from almanac.replay import canonical, digest  # noqa: E402
from almanac.uncertainty import LATE, REPORTING, UNREACHABLE  # noqa: E402

SCHEMA = 'almanac.uncertainty-experiment.v1'
REGISTRY_SCHEMA = 'almanac.uncertainty-registry.v1'
ERROR_SCHEMA = 'almanac.uncertainty-error.v1'

# Registry bounds, small on purpose: every run is a deterministic unit-test-scale simulation
# that must finish inside one loopback request without a timeout or a progress indicator.
MAX_TIMELINE_S = 240   # an experiment may not be longer than this many simulated seconds
MAX_DEVICES = 4        # registry limit, well below the simulator's own device bound
OPERATIONAL_EXIT = 4

# What a registered scenario claims to exercise. A claim is checked against the run: see
# `_mechanism_evidence`, which reports the observed counters and whether they back the claim.
MECHANISMS = ('none', 'ack-loss', 'ack-delay', 'reconnect-backfill', 'buffer-overflow')

# Evidence each declared mechanism must actually produce, by name, in the observed counters.
REQUIRED_EVIDENCE = {
    'none': ('uncertain_device_seconds', 'backfilled_samples'),
    'ack-loss': ('unacknowledged_command_seconds', 'uncertain_device_seconds',
                 'devices_with_pending_uncertainty'),
    'ack-delay': ('unacknowledged_command_seconds', 'acknowledged_command_seconds'),
    'reconnect-backfill': ('backfilled_samples', 'max_sample_age_s'),
    'buffer-overflow': ('dropped_buffered_samples', 'backfilled_samples', 'max_sample_age_s'),
}

_TWO_DEVICES = [{'device_id': 'd1', 'capacity_kw': 8, 'energy_kwh': 5, 'reserve_kwh': 1},
                {'device_id': 'd2', 'capacity_kw': 8, 'energy_kwh': 5, 'reserve_kwh': 1}]

# Closed registry. Every value is an invented input: capacities, reserves, initial energy,
# target, periods, fault times and the loss schedules are assumptions, not measurements.
EXPERIMENTS = {
    'acknowledged-baseline': {
        'label': 'Acknowledged baseline, no loss',
        'description': 'Every command is received and acknowledged in the second it was '
                       'issued, so the controller is never uncertain. The contrast case.',
        'mechanism': 'none',
        'spec': {'scenario_id': 'uncertainty-lab-acknowledged-baseline-v1', 'duration_s': 120,
                 'control_period_s': 20, 'telemetry_period_s': 10, 'inactivity_timeout_s': 30,
                 'command_ttl_s': 60, 'target_kw': 10, 'seed': 1, 'devices': _TWO_DEVICES,
                 'faults': []},
    },
    'ack-loss-unconfirmed': {
        'label': 'Acknowledgement loss, command still running',
        'description': 'The first two acknowledgements are dropped. Both devices received '
                       'and are running their commands, and the controller cannot know it, '
                       'so it holds the worst case and waits until the absolute TTL lapses.',
        'mechanism': 'ack-loss',
        'spec': {'scenario_id': 'uncertainty-lab-ack-loss-v1', 'duration_s': 120,
                 'control_period_s': 20, 'telemetry_period_s': 10, 'inactivity_timeout_s': 30,
                 'command_ttl_s': 60, 'target_kw': 10, 'seed': 1, 'devices': _TWO_DEVICES,
                 'faults': [], 'drop_acks': [1, 2]},
    },
    'ack-delay-late-confirm': {
        'label': 'Acknowledgement delay, late confirmation',
        'description': 'The first two acknowledgements arrive 25 seconds late. The same run '
                       'shows an unconfirmed stretch first and a confirmed one afterwards.',
        'mechanism': 'ack-delay',
        'spec': {'scenario_id': 'uncertainty-lab-ack-delay-v1', 'duration_s': 120,
                 'control_period_s': 20, 'telemetry_period_s': 10, 'inactivity_timeout_s': 30,
                 'command_ttl_s': 60, 'target_kw': 10, 'seed': 1, 'devices': _TWO_DEVICES,
                 'faults': [], 'ack_delay_s': {1: 25, 2: 25}},
    },
    'reconnect-backfill': {
        'label': 'Outage then reconnect, telemetry arrives as history',
        'description': 'One device loses its link for 40 seconds while it keeps sampling. '
                       'The reconnect delivers that backlog at its original sample times, '
                       'so it reads as history and never as a current measurement.',
        'mechanism': 'reconnect-backfill',
        'spec': {'scenario_id': 'uncertainty-lab-reconnect-backfill-v1', 'duration_s': 120,
                 'control_period_s': 20, 'telemetry_period_s': 10, 'inactivity_timeout_s': 30,
                 'command_ttl_s': 60, 'target_kw': 10, 'seed': 1, 'devices': _TWO_DEVICES,
                 'faults': [{'at_s': 30, 'device_id': 'd1', 'link': 'down'},
                            {'at_s': 70, 'device_id': 'd1', 'link': 'up'}]},
    },
    'backfill-buffer-overflow': {
        'label': 'Long outage, buffered history genuinely lost',
        'description': 'The outage outlasts the bounded sample buffer, so the oldest samples '
                       'are dropped before the reconnect. Recovered history is incomplete.',
        'mechanism': 'buffer-overflow',
        'spec': {'scenario_id': 'uncertainty-lab-buffer-overflow-v1', 'duration_s': 180,
                 'control_period_s': 20, 'telemetry_period_s': 5, 'inactivity_timeout_s': 30,
                 'command_ttl_s': 60, 'target_kw': 10, 'seed': 1, 'devices': _TWO_DEVICES,
                 'faults': [{'at_s': 20, 'device_id': 'd1', 'link': 'down'},
                            {'at_s': 120, 'device_id': 'd1', 'link': 'up'}]},
    },
}

LIMITS = (
    'Every experiment is synthetic and the model is uncalibrated: capacities, reserves, '
    'initial energy, target, periods, fault times, acknowledgement loss and delay schedules '
    'are invented inputs, not measurements of any device or fleet.',
    'No report here is a safety verdict, a certification or a ranking. The registered '
    'scenarios show how one bounded conservative controller behaves under invented '
    'communication failures, and nothing more.',
    'Reserve preservation is an energy floor inside the simulation, enforced by a local '
    'clamp at the simulated device. It is not evidence that any household, load or site '
    'stayed powered.',
    'Command expiry is an absolute TTL on simulated wall time. It keeps running through an '
    'outage and a reconnect never restarts it, so a lapsed command is gone rather than '
    'resumed. That is a modelling choice, not a measured device behaviour.',
    'Observer rows hold only delivered telemetry and delivered acknowledgements. Evaluator '
    'truth rows exist for reading the run and are never a controller input.',
    'This surface is separate from the offline controller check. It shares no registry, no '
    'receipt schema and no verdict with it, and it does not change it.',
    'No probabilistic claim, risk frontier or failure-rate estimate is produced. The seeded '
    'RNG inside the simulator is never observed by the controller.',
)


def _spec(key: str) -> dict:
    """Return the registered scenario spec for `key`, or raise `KeyError`.

    Membership in the closed registry is the only accepted input. A key that is not
    registered never reaches the simulator and is never echoed back by a caller of this
    module.
    """
    if not isinstance(key, str) or key not in EXPERIMENTS:
        raise KeyError('unknown experiment key')
    return EXPERIMENTS[key]['spec']


def experiment_stem(key: str) -> str:
    """Download name stem for one experiment. Registry keys only."""
    _spec(key)
    return f'uncertainty-{key}'


class SourceDriftError(RuntimeError):
    """A manifested source file changed or disappeared after this module was imported.

    Raised instead of returning a manifest. A long-running process keeps executing the code
    objects it compiled at import time, so once the files on disk move away from the import
    snapshot there is nothing honest left to say about which bytes produced a report.
    """


def _read_sources() -> dict[str, str]:
    """Digest the manifested module sources as they are on disk right now.

    A source file that has gone missing or become unreadable is drift too: it is reported as
    drift rather than allowed to surface as a bare filesystem error, so that every caller has
    one failure to handle and none of them can fall through to a report.
    """
    try:
        return source_digests(source_paths(SOURCE_MODULES))
    except OSError as exc:
        raise SourceDriftError(f'a manifested source file is unreadable: {exc}') from exc
    except ValueError as exc:
        raise SourceDriftError(f'a manifested module has no source file: {exc}') from exc


# The disk bytes of the manifested modules at the moment this module finished importing.
# Every later reading is compared against this, and a difference fails the run closed.
_IMPORT_SNAPSHOT = _read_sources()


def verify_sources() -> dict[str, str]:
    """Re-read the manifested sources and confirm they still match the import snapshot.

    Returns the snapshot on success and raises `SourceDriftError` otherwise. Callers run this
    before a simulation and again when the manifest is built, so a file edited while an
    experiment is running is caught as well as one edited beforehand.
    """
    current = _read_sources()
    drifted = sorted(name for name in _IMPORT_SNAPSHOT
                     if current.get(name) != _IMPORT_SNAPSHOT[name])
    if drifted:
        raise SourceDriftError(
            'source files changed since almanac.uncertainty_lab was imported, so this '
            'process is no longer running the code on disk: ' + ', '.join(drifted))
    return dict(_IMPORT_SNAPSHOT)


def code_manifest() -> dict:
    """Name the source bytes this report was produced from, and say what that does not prove.

    The digests are a *disk snapshot* taken when this module was imported, re-read and
    confirmed unchanged here. They are deliberately not called an attestation of the bytes the
    interpreter executed: Python runs compiled code objects held in memory, and any module
    already imported before this one was executed from whatever its file held at that earlier
    moment. Proving executed bytes is outside this surface, so the claim is narrowed instead
    of dressed up, and detectable drift fails the run rather than being papered over.
    """
    modules = verify_sources()
    return {'modules': modules, 'manifest_sha256': digest(modules),
            'snapshot': 'source bytes read from disk when almanac.uncertainty_lab was '
                        'imported into this process, re-read and confirmed byte identical '
                        'before and after this run',
            'covers': 'exact source bytes of these four modules only: the experimental '
                      'uncertainty simulator, this registry and evaluator, the '
                      'canonicalisation and digest helpers that define the report identity, '
                      'and the module providing the source-hashing helpers reused here',
            'excludes': 'not a supply-chain identity: the Python interpreter, the standard '
                        'library, installed packages, the operating system and every other '
                        'module in this repository are outside this manifest',
            'not_an_attestation_of': 'the executed code: this is a disk snapshot and not '
                                     'proof of the bytes the interpreter executed, which are '
                                     'compiled code objects held in memory that this manifest '
                                     'cannot read',
            'loaded_before_this_module': list(_LOADED_BEFORE),
            'loaded_before_note': 'those modules were already imported when this module '
                                  'loaded, so even the import snapshot postdates their '
                                  'execution and cannot describe the bytes they ran',
            'on_drift': 'a manifested source file that changes or disappears after the import '
                        'snapshot fails the run: no report is produced and no manifest is '
                        'returned',
            'note': 'import names only; no filesystem path, environment or build metadata '
                    'is recorded'}


def listing() -> dict:
    """Describe the closed registry without running any simulation."""
    experiments = []
    for key in sorted(EXPERIMENTS):
        entry = EXPERIMENTS[key]
        scenario = uncertainty.build_scenario(entry['spec'])
        experiments.append({
            'key': key, 'label': entry['label'], 'description': entry['description'],
            'mechanism': entry['mechanism'], 'synthetic': True,
            'scenario_id': scenario.scenario_id, 'duration_s': scenario.duration_s,
            'devices': len(scenario.devices), 'control_period_s': scenario.control_period_s,
            'telemetry_period_s': scenario.telemetry_period_s,
            'command_ttl_s': scenario.command_ttl_s,
            'scenario_sha256': digest(entry['spec']),
            'required_evidence': list(REQUIRED_EVIDENCE[entry['mechanism']]),
        })
    return {
        'schema': REGISTRY_SCHEMA, 'mode': uncertainty.MODE, 'experimental': True,
        'safety_verdict': None,
        'simulator': {'module': 'almanac.uncertainty', 'schema': uncertainty.SCHEMA,
                      'reused_unchanged': True,
                      'note': 'this surface runs that simulator directly; no physics, '
                              'controller or gate is reimplemented'},
        'bounds': {'max_timeline_s': MAX_TIMELINE_S, 'max_devices': MAX_DEVICES,
                   'max_buffered_samples': uncertainty.MAX_BUFFERED_SAMPLES},
        'mechanisms': list(MECHANISMS),
        'experiments': experiments,
        'cli': 'python -m almanac.uncertainty_lab run --experiment <key>',
        'accepts': 'registered experiment keys only: no scenario JSON, no import path, no '
                   'filesystem path and no uploaded code, here or over HTTP',
        'assumptions': list(uncertainty.ASSUMPTIONS),
        'controller_limits': list(uncertainty.CONTROLLER_LIMITS),
        'limits': list(LIMITS),
    }


def _observer_rollup(trace: list[dict], device_id: str, telemetry_period_s: int) -> dict:
    """Summarise one device **as the controller saw it**, from this run's own timeline.

    Every value comes from the delivered-telemetry and acknowledgement view in the trace.
    Nothing physical is read here, which is why there is no energy or link field: those are
    evaluator truth and live in `_truth_rollup`.

    A second counts as acknowledged only when the device has some command the controller
    still considers possible and none of them is outstanding, which is exactly the state in
    which an acknowledgement has arrived and no later command is unconfirmed.
    """
    freshness = {REPORTING: 0, LATE: 0, UNREACHABLE: 0}
    acknowledged = unacknowledged = no_command = history_only = 0
    max_age_s = 0
    skipped_sample_time_s = 0
    previous_last: int | None = None
    for row in trace:
        seen = row['observed'][device_id]
        freshness[seen['freshness']] += 1
        if seen['pending_unconfirmed']:
            unacknowledged += 1
        elif seen['possible_active_commands']:
            acknowledged += 1
        else:
            no_command += 1
        age_s = seen['sample_age_s']
        if age_s is not None:
            max_age_s = max(max_age_s, age_s)
            if age_s > 0:
                history_only += 1
        last = seen['last_sample_s']
        if last is not None:
            # A jump in the newest sample time is not evidence of anything on its own: in the
            # loss-free baseline it happens once per telemetry period, by construction. What
            # the controller can genuinely tell is how much sample time the newest reading
            # skipped *beyond* one period, meaning readings it never held as current. That is
            # zero whenever delivery keeps up, and it stays a statement about the controller's
            # own view: it does not claim to know whether those readings were backfilled
            # later, dropped, or never taken. Late delivery itself is counted by the
            # simulator as `metrics.backfilled_samples`.
            if previous_last is not None and last > previous_last:
                skipped_sample_time_s = max(skipped_sample_time_s,
                                            last - previous_last - telemetry_period_s)
            previous_last = last
    final = trace[-1]['observed'][device_id]
    return {
        'freshness_seconds': freshness,
        'last_sample_s': final['last_sample_s'],
        'sample_age_s': final['sample_age_s'],
        'reading_is_current': final['reading_is_current'],
        'reported_physical': final['reported_physical'],
        'max_sample_age_s': max_age_s,
        'skipped_sample_time_s': max(0, skipped_sample_time_s),
        'history_only_reading_seconds': history_only,
        'acknowledged_command_seconds': acknowledged,
        'unacknowledged_command_seconds': unacknowledged,
        'no_command_seconds': no_command,
        'final_possible_active_commands': list(final['possible_active_commands']),
        'final_worst_case_kw': final['worst_case_kw'],
    }


def _truth_rollup(trace: list[dict], device_id: str, spec: dict) -> dict:
    """Summarise one device as the **evaluator** saw it. Never a controller input."""
    holding = sum(1 for row in trace if row['truth'][device_id]['active_command'] is not None)
    final = trace[-1]['truth'][device_id]
    return {
        'physical': final['physical'], 'link_up': final['link_up'],
        'active_command': final['active_command'], 'setpoint_kw': final['setpoint_kw'],
        'energy_kwh': final['energy_kwh'],
        'capacity_kw': str(spec['capacity_kw']), 'reserve_kwh': str(spec['reserve_kwh']),
        'initial_energy_kwh': str(spec['energy_kwh']),
        'seconds_holding_command': holding,
    }


def _divergence_seconds(trace: list[dict]) -> int:
    """Device-seconds where the device held a command the controller no longer considered possible.

    That is the visible footprint of expiry and loss: the controller's possible set is empty
    while the simulated device is still running something, or the reverse.
    """
    total = 0
    for row in trace:
        for device_id, seen in row['observed'].items():
            holds = row['truth'][device_id]['active_command'] is not None
            if holds != bool(seen['possible_active_commands']):
                total += 1
    return total


def _mechanism_evidence(claim: str, metrics: dict, observer: dict) -> dict:
    """Check the registry's own claim against what the run actually produced."""
    observed = {
        'uncertain_device_seconds': metrics['uncertain_device_seconds'],
        'devices_with_pending_uncertainty': list(metrics['devices_with_pending_uncertainty']),
        'unacknowledged_command_seconds': sum(d['unacknowledged_command_seconds']
                                              for d in observer.values()),
        'acknowledged_command_seconds': sum(d['acknowledged_command_seconds']
                                            for d in observer.values()),
        'backfilled_samples': metrics['backfilled_samples'],
        'max_sample_age_s': metrics['max_sample_age_s'],
        'stale_backfill_samples': metrics['stale_backfill_samples'],
        'dropped_buffered_samples': metrics['dropped_buffered_samples'],
    }
    required = list(REQUIRED_EVIDENCE[claim])
    if claim == 'none':
        satisfied = all(not observed[name] for name in required)
    else:
        satisfied = all(bool(observed[name]) for name in required)
    return {
        'claim': claim, 'required': required, 'observed': observed, 'satisfied': satisfied,
        'note': 'the registry claim is checked against this run; a claim the run does not '
                'produce is reported as unsatisfied rather than assumed',
    }


def evaluate(key: str) -> dict:
    """Run one registered experiment through `uncertainty.simulate` and report it.

    Every number below is integrated by that simulator or counted from the one trace it
    returned. There is no second run, no cached value and no hardcoded outcome, and no
    safety verdict is produced.
    """
    spec = _spec(key)
    entry = EXPERIMENTS[key]
    # Fail closed before anything is simulated, and again in `code_manifest` afterwards, so
    # a source file edited before or during the run is rejected instead of being reported as
    # the provenance of a report it did not produce.
    verify_sources()
    scenario = uncertainty.build_scenario(spec)
    if scenario.duration_s > MAX_TIMELINE_S or len(scenario.devices) > MAX_DEVICES:
        raise ValueError(f'registered experiment {key} exceeds the registry bounds')
    result = uncertainty.simulate(scenario)
    trace = result['trace']
    device_specs = {device['device_id']: device for device in spec['devices']}
    observer = {device_id: _observer_rollup(trace, device_id, scenario.telemetry_period_s)
                for device_id in sorted(device_specs)}
    truth = {device_id: _truth_rollup(trace, device_id, device_specs[device_id])
             for device_id in sorted(device_specs)}
    report = {
        'schema': SCHEMA, 'mode': result['mode'], 'experimental': True,
        'safety': {'verdict': None,
                   'note': 'no production safety verdict is claimed, and none can be read '
                           'out of this report: the model is synthetic and uncalibrated'},
        'experiment': {
            'key': key, 'label': entry['label'], 'description': entry['description'],
            'mechanism': entry['mechanism'], 'synthetic': True,
            'scenario_id': result['scenario_id'], 'scenario_sha256': digest(spec),
            'duration_s': scenario.duration_s, 'devices': len(scenario.devices),
            'timeline_rows': len(trace),
        },
        'simulator': {'module': 'almanac.uncertainty', 'schema': result['schema'],
                      'reused_unchanged': True},
        'config': result['config'],
        'metrics': result['metrics'],
        'observer': observer,
        'evaluator_truth': truth,
        'separation_note': 'observer rows hold only delivered telemetry and delivered '
                           'acknowledgements, which is everything the controller could see; '
                           'evaluator_truth is never a controller input and exists only to '
                           'read the run',
        'mechanism_evidence': _mechanism_evidence(entry['mechanism'], result['metrics'], observer),
        'expiry': {
            'command_ttl_s': result['config']['command_ttl_s'],
            'absolute': True,
            'note': 'the TTL is absolute simulated wall time: it keeps running through an '
                    'outage and a reconnect never restarts it, and the boundary second is '
                    'already expired',
            'observer_truth_divergence_device_seconds': _divergence_seconds(trace),
            'devices_pending_at_end': list(result['metrics']['devices_pending_at_end']),
        },
        'reserve': {
            'reserve_violations': result['metrics']['reserve_violations'],
            'reserve_preserved': result['metrics']['reserve_preserved'],
            'meaning': 'an energy floor inside the simulation, held by a local clamp at the '
                       'simulated device; it is not evidence that any household, load or '
                       'site stayed powered',
        },
        'gate_rejections': result['gate_rejections'],
        'timeline': trace,
        'assumptions': result['assumptions'],
        'controller_limits': result['controller_limits'],
        'limits': list(LIMITS),
        'code': code_manifest(),
    }
    report['content_sha256'] = digest(report)
    return report


def build_parser() -> Parser:
    """Separate experimental CLI. It shares no subcommand with the controller check."""
    parser = Parser(prog='almanac-uncertainty',
                    description='Experimental offline uncertainty experiments on a closed '
                                'registry of synthetic scenarios. No safety verdict.')
    subparsers = parser.add_subparsers(dest='command', required=True)
    subparsers.add_parser('experiments', help='list the registered experiments and limits')
    run = subparsers.add_parser('run', help='print one deterministic experiment report')
    run.add_argument('--experiment', required=True, choices=sorted(EXPERIMENTS))
    return parser


def main(argv: list[str] | None = None) -> int:
    """Print canonical JSON. Exit 0 on success, 3 on bad arguments, 4 on an operational error.

    There is no verdict exit code here, because this surface never issues a verdict. Nothing
    is written to disk: the report goes to stdout so a caller decides where it lands.
    """
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 0 if not exc.code else 3
    try:
        payload = listing() if args.command == 'experiments' else evaluate(args.experiment)
    except Exception as exc:  # the run did not complete: say that, never imply a result
        import sys
        sys.stderr.write(canonical({
            'schema': ERROR_SCHEMA, 'verdict': 'operational-error',
            'stage': 'experimental uncertainty run', 'error_type': type(exc).__name__,
            'message': str(exc), 'exit_code': OPERATIONAL_EXIT,
            'note': 'the experiment did not complete; no result and no safety verdict is '
                    'claimed'}) + '\n')
        return OPERATIONAL_EXIT
    print(canonical(payload))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
