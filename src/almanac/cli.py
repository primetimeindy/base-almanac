"""Offline controller-check CLI over a closed registry of synthetic fixtures.

Standard library only, no network, no dynamic user-supplied import path and no eval.
`scenarios` lists what can be run, `check` prints a deterministic JSON verdict, and
`run` additionally writes the JSON and a self-contained HTML report.

Exit codes: 0 pass, 1 defined performance regression, 2 safety violation (a rejected
overcommit or an invalid policy result: the check ran and the policy failed it),
3 unknown or malformed arguments, 4 operational error. An operational error is a tool,
fixture or I/O failure: the check never completed, so it is reported as its own machine
readable failure rather than dressed up as a pass, a regression or a physics observation.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import importlib
import inspect
import json
import os
import shutil
import sys
import tempfile
from fractions import Fraction
from pathlib import Path

from almanac.controller_contract import OBSERVATION, OvercommitRejected, compare_policy
from almanac.fleet import CONTROL_S, LEASE_BOUND_S
from almanac.geofleet import AREAS, SOURCE_URL, scenario_for
from almanac.policies import ConservativeShare, HoldZero, NaiveTargetFill
from almanac.replay import canonical, digest

SCHEMA = 'almanac.check.v1'
LISTING_SCHEMA = 'almanac.cli.v1'
ERROR_SCHEMA = 'almanac.cli-error.v1'
POINTER_SCHEMA = 'almanac.report-pointer.v1'
PHYSICS_STEP_S = 10
OPERATIONAL_EXIT = 4

# Published report layout: out/<stem>/generations/<content sha256>/{report.json,report.html}
# with out/<stem>/current.json naming the generation a reader should use.
GENERATIONS = 'generations'
CURRENT_NAME = 'current.json'
REPORT_JSON = 'report.json'
REPORT_HTML = 'report.html'

# Source bytes that decide what a report says: simulation engine, controller adapter,
# bundled policies, scenario builder, the replay canonicalisation and digest helpers that
# define receipt and hash semantics, the receipt inspector, and this evaluator. A change
# to any of them must change the report identity even when every metric comes out the same.
SOURCE_MODULES = ('almanac.cli', 'almanac.controller_contract', 'almanac.fleet',
                  'almanac.geofleet', 'almanac.inspector', 'almanac.policies', 'almanac.replay')

ENGINE = {
    'name': 'almanac', 'engine_module': 'almanac.fleet', 'receipt_schema': 'almanac.fleet.v1',
    'control_period_s': CONTROL_S, 'lease_bound_s': LEASE_BOUND_S,
    'arithmetic': 'exact rational energy accounting; floats only at the presentation boundary',
    'runtime': 'Python standard library only, offline, no device or network I/O',
}

# Closed registry. Scenario geometry, devices, target and faults all come from the
# existing synthetic geofleet fixtures; nothing here is measured or Base-derived.
SCENARIOS = {
    key: {'area': key, 'label': AREAS[key]['label']}
    for key in ('all', 'core', 'empty', 'west', 'wide')
}

# Known policies only. The CLI never accepts a module path from the user; the existing
# `python -m almanac.controller_contract --policy module:factory` seam still does.
POLICIES = {
    'conservative-share': {'factory': ConservativeShare, 'source': 'almanac.policies:ConservativeShare',
                           'description': 'equal share of the dispatch budget, clamped per device'},
    'naive-target-fill': {'factory': NaiveTargetFill, 'source': 'almanac.policies:NaiveTargetFill',
                          'description': 'fills toward the raw target and ignores unknown output'},
    'hold-zero': {'factory': HoldZero, 'source': 'almanac.policies:HoldZero',
                  'description': 'commands zero everywhere; safe and useless'},
}

LIMITS = [
    'Every scenario is synthetic: device positions, capacities, reserve, target and the '
    'communication-loss schedule are invented. Only the bundled NHC Beryl track is observed data.',
    'One scenario and one seeded plant per check. This is not an empirical performance estimate, '
    'a ranking of controllers or evidence about any real fleet.',
    'Reserve preservation here is an energy floor inside the simulation. It does not show that any '
    'household, load or site stayed powered.',
    'A rejected action means the harness refused that proposal before dispatching it. It is not a '
    'simulated physical overshoot, and it says nothing about what a real device would have done. '
    'Earlier control ticks of the same run may already have dispatched accepted commands, and an '
    'aborted run returns no trajectory, so no complete measured outcome is available either way.',
    'Policies run in process as trusted local imports. There is no sandbox, timeout or memory bound.',
    'The performance verdict compares one number, delivered-energy shortfall, against the built-in '
    'fixed-share reference on the same scenario. It is not a claim of optimality.',
]

REGRESSION_RULE = ('regression when the candidate delivered-energy shortfall exceeds the built-in '
                   'fixed-share baseline shortfall on the identical scenario')


class Parser(argparse.ArgumentParser):
    """Argument errors are exit code 3, not argparse's default 2."""

    def error(self, message: str) -> None:
        self.exit(3, f'{self.prog}: error: {message}\n')


def build_parser() -> Parser:
    parser = Parser(prog='almanac', description='Offline controller checks on synthetic fixtures.')
    subparsers = parser.add_subparsers(dest='command', required=True)
    subparsers.add_parser('scenarios', help='list the registered scenarios and policies')
    for name, help_text in (('check', 'print a deterministic JSON verdict'),
                            ('run', 'write JSON (and optional HTML) reports')):
        sub = subparsers.add_parser(name, help=help_text)
        sub.add_argument('--scenario', required=True, choices=sorted(SCENARIOS))
        sub.add_argument('--policy', required=True, choices=sorted(POLICIES))
        if name == 'run':
            sub.add_argument('--out', default='out', help='output directory, default out/')
            sub.add_argument('--html', action='store_true', help='also write the HTML report')
    return parser


def listing() -> dict:
    """Describe the closed registry without running any simulation."""
    return {
        'schema': LISTING_SCHEMA, 'engine': ENGINE,
        'scenarios': [{'key': key, 'label': SCENARIOS[key]['label'], 'synthetic': True,
                       'observed_context': SOURCE_URL,
                       'fixture': 'almanac.geofleet.scenario_for'} for key in sorted(SCENARIOS)],
        'policies': [{'key': key, 'id': POLICIES[key]['factory'].policy_id,
                      'version': POLICIES[key]['factory'].policy_version,
                      'source': POLICIES[key]['source'],
                      'description': POLICIES[key]['description']} for key in sorted(POLICIES)],
        'exit_codes': {'0': 'pass', '1': 'defined performance regression',
                       '2': 'safety violation: rejected overcommit or invalid policy result',
                       '3': 'unknown or malformed arguments',
                       str(OPERATIONAL_EXIT): 'operational error: the check did not complete '
                                              '(fixture, engine or output failure), no verdict claimed'},
        'regression_rule': REGRESSION_RULE,
        'limits': LIMITS,
    }


def source_digests(modules: dict[str, str]) -> dict[str, str]:
    """SHA256 the exact source bytes of each named module file.

    Keyed by import name, never by path: an absolute path would leak the machine layout
    into the report and stop two checkouts producing identical bytes.
    """
    return {name: hashlib.sha256(Path(path).read_bytes()).hexdigest()
            for name, path in sorted(modules.items())}


def source_paths(names: tuple[str, ...] = SOURCE_MODULES) -> dict[str, str]:
    """Locate the loaded source file of each module that shapes a report."""
    located = {}
    for name in names:
        path = inspect.getsourcefile(importlib.import_module(name))
        if path is None:
            raise ValueError(f'no source file available for {name}')
        located[name] = path
    return located


def code_manifest() -> dict:
    """Pin the report to the code that produced it, metrics aside."""
    modules = source_digests(source_paths())
    return {'modules': modules, 'manifest_sha256': digest(modules),
            'covers': 'exact source bytes of these seven modules only: the simulation engine, the '
                      'controller adapter, the bundled policies, the scenario builder, the replay '
                      'canonicalisation and digest helpers, the receipt inspector, and this report '
                      'evaluator',
            'excludes': 'not a supply-chain identity: the Python interpreter, the standard library, '
                        'installed packages, the operating system and any other module in this '
                        'repository are outside this manifest',
            'note': 'import names only; no filesystem path, environment or build metadata is recorded'}


def _identity(report: dict, scenario_key: str, policy_key: str, scenario: dict) -> dict:
    factory = POLICIES[policy_key]['factory']
    report.update({
        'schema': SCHEMA, 'mode': 'offline-simulation-only', 'engine': ENGINE,
        'scenario': {'key': scenario_key, 'id': scenario['id'], 'label': SCENARIOS[scenario_key]['label'],
                     'synthetic': True, 'devices': len(scenario['devices']),
                     'duration_s': scenario['duration_s'], 'target_kw': scenario['target_kw'],
                     'faulted_devices': sum(len(f['devices']) for f in scenario['faults']),
                     'scenario_sha256': digest(scenario)},
        'policy': {'key': policy_key, 'id': getattr(factory, 'policy_id', None),
                   'version': getattr(factory, 'policy_version', None),
                   'source': POLICIES[policy_key]['source'], 'contract': OBSERVATION,
                   'trust': 'trusted local in-process import, not a sandbox'},
        'config': {'physics_step_s': PHYSICS_STEP_S, 'control_period_s': CONTROL_S,
                   'lease_bound_s': LEASE_BOUND_S, 'local_expiry_s': scenario['lease_policy']['local_expiry_s'],
                   'scenario_lease_bound_s': scenario['lease_policy']['lease_bound_s'],
                   'observation_contract': OBSERVATION,
                   'reference_strategies': ['baseline', 'constrained'],
                   'regression_rule': REGRESSION_RULE},
        'code': code_manifest(),
    })
    return report


def _measured(receipt: dict) -> tuple[dict, list[str], str]:
    """Report only facts the run actually produced, and classify them."""
    candidate = receipt['candidate']['metrics']
    references = {name: receipt['reference']['strategies'][name]['metrics']
                  for name in ('baseline', 'constrained')}
    shortfall = Fraction(candidate['exact_shortfall_kwh'])
    baseline_shortfall = Fraction(references['baseline']['exact_shortfall_kwh'])
    measured = {
        'requested_kwh': candidate['requested_kwh'],
        'delivered_kwh': candidate['delivered_kwh'],
        'shortfall_kwh': candidate['shortfall_kwh'],
        'exact_delivered_kwh': candidate['exact_delivered_kwh'],
        'exact_shortfall_kwh': candidate['exact_shortfall_kwh'],
        'realized_overshoot_kw': candidate['max_overshoot_kw'],
        'attempted_overcommit_rejected': False,
        'reserve_violations': candidate['reserve_violations'],
        'configured_reserve_preserved': candidate['reserve_violations'] == 0,
        'reserve_meaning': 'the simulated plant held every device at or above its configured reserve energy; '
                           'this is not evidence that any household stayed powered',
        'controller_setpoint_changes': candidate['setpoint_changes'],
        'accepted_commands': candidate['accepted_commands'],
        'reference_baseline_shortfall_kwh': references['baseline']['shortfall_kwh'],
        'reference_constrained_shortfall_kwh': references['constrained']['shortfall_kwh'],
        'candidate_receipt_sha256': receipt['receipt_sha256'],
        'sample': candidate['sample'],
    }
    findings = []
    verdict = 'pass'
    if candidate['reserve_violations'] or candidate['max_overshoot_kw']:
        verdict = 'unsafe'
        findings.append('The run reported a reserve violation or realized overshoot.')
    elif shortfall > baseline_shortfall:
        verdict = 'regression'
        findings.append('Performance regression: candidate shortfall '
                        f"{candidate['shortfall_kwh']} kWh exceeds the fixed-share baseline "
                        f"{references['baseline']['shortfall_kwh']} kWh on the same scenario.")
    else:
        findings.append('Every command stayed inside the enforced envelope, the configured reserve floor '
                        'held, and the shortfall did not exceed the fixed-share baseline.')
    return measured, findings, verdict


def evaluate(scenario_key: str, policy_key: str) -> dict:
    """Run one known policy on one registered fixture and return the report.

    Anything unexpected is a safety verdict: this never degrades to a pass.
    """
    report: dict = {}
    _, scenario = scenario_for({'area': SCENARIOS[scenario_key]['area']})
    _identity(report, scenario_key, policy_key, scenario)
    try:
        receipt = compare_policy(scenario, POLICIES[policy_key]['factory']())
    except Exception as exc:  # fail closed: an unexpected failure is never a pass
        overcommit = isinstance(exc, OvercommitRejected)
        report['verdict'] = 'unsafe'
        report['measured'] = None
        report['rejection'] = {
            'stage': 'action validation' if isinstance(exc, ValueError) else 'policy execution',
            'error_type': type(exc).__name__,
            'message': str(exc),
            'attempted_overcommit_rejected': overcommit,
            'realized_overshoot_kw': None,
            'partial_trajectory': 'unavailable: the aborted run returned no receipt',
            'note': ('The harness rejected the proposed commands, so that proposal was not dispatched. '
                     if overcommit else 'The run aborted with an error and returned no receipt. ')
                    + 'The candidate may have completed earlier control ticks whose accepted commands did '
                      'reach the simulated plant; because no receipt was returned, that partial trajectory '
                      'is unavailable. No delivered energy, overshoot or reserve outcome is claimed for '
                      'this policy, and none is ruled out.',
        }
        report['findings'] = ['No receipt was generated: ' + str(exc)]
    else:
        measured, findings, verdict = _measured(receipt)
        report['verdict'] = verdict
        report['measured'] = measured
        report['rejection'] = None
        report['findings'] = findings
    report['exit_code'] = {'pass': 0, 'regression': 1, 'unsafe': 2}[report['verdict']]
    report['limits'] = LIMITS
    report['content_sha256'] = digest(report)
    return report


def render_html(report: dict) -> str:
    """Render a standalone report. No external resource is referenced or fetched."""
    def text(value: object) -> str:
        return html.escape(str(value), quote=True)

    def rows(mapping: dict) -> str:
        return '\n'.join(f'<tr><th>{text(k)}</th><td>{text(v)}</td></tr>' for k, v in mapping.items())

    detail = report['measured'] if report['measured'] is not None else report['rejection']
    lines = [
        '<!DOCTYPE html>', '<html lang="en">', '<head>', '<meta charset="utf-8">',
        '<title>' + text(f"Almanac check: {report['scenario']['key']} / {report['policy']['key']}") + '</title>',
        '<style>body{font-family:system-ui,sans-serif;margin:2rem;max-width:60rem;color:#111}'
        'table{border-collapse:collapse;margin:1rem 0;width:100%}'
        'th,td{border:1px solid #ccc;padding:.35rem .5rem;text-align:left;vertical-align:top;font-size:.9rem}'
        'th{width:22rem;background:#f5f5f5}pre{background:#f5f5f5;padding:1rem;overflow-x:auto;font-size:.8rem}</style>',
        '</head>', '<body>',
        '<h1>Almanac offline controller check</h1>',
        '<p>Synthetic scenario. Offline simulation only. No dispatch, no device, no network request.</p>',
        '<h2>Verdict: ' + text(report['verdict']) + ' (exit ' + text(report['exit_code']) + ')</h2>',
        '<ul>' + ''.join('<li>' + text(f) + '</li>' for f in report['findings']) + '</ul>',
        '<h2>Identifiers</h2>', '<table>',
        rows({'engine': report['engine']['engine_module'], 'receipt schema': report['engine']['receipt_schema'],
              'scenario': report['scenario']['id'], 'scenario sha256': report['scenario']['scenario_sha256'],
              'policy': f"{report['policy']['id']} {report['policy']['version']}",
              'policy source': report['policy']['source'],
              'physics step (s)': report['config']['physics_step_s'],
              'control period (s)': report['config']['control_period_s'],
              'controller lease bound (s)': report['config']['lease_bound_s'],
              'source manifest sha256': report['code']['manifest_sha256'],
              'content sha256': report['content_sha256']}),
        '</table>',
        '<h2>Source manifest</h2>',
        '<p>SHA256 of the exact source bytes that produced this report.</p>',
        '<table>', rows(report['code']['modules']),
        '</table>',
        '<h2>' + ('Measured' if report['measured'] is not None else 'Rejection') + '</h2>',
        '<table>', rows(detail), '</table>',
        '<h2>Limits</h2>', '<ul>' + ''.join('<li>' + text(limit) + '</li>' for limit in report['limits']) + '</ul>',
        '<h2>Observed context</h2>',
        '<p>Storm-track geometry only, from the bundled archive of ' + text(SOURCE_URL)
        + '. Named as a source, never requested by this page.</p>',
        '<h2>Report JSON</h2>',
        '<pre>' + text(json.dumps(report, sort_keys=True, indent=2)) + '</pre>',
        '</body>', '</html>', '',
    ]
    return '\n'.join(lines)


def _write_file(target: Path, text: str) -> None:
    """Write one artifact durably. Used inside a staging directory, never over a reader."""
    with open(target, 'w', encoding='utf-8', newline='\n') as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())


def _write_atomic(target: Path, text: str) -> None:
    """Replace target in one step so no reader ever sees a half-written file."""
    handle, temporary = tempfile.mkstemp(dir=str(target.parent), prefix=f'.{target.name}.', suffix='.tmp')
    try:
        with os.fdopen(handle, 'w', encoding='utf-8', newline='\n') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def report_stem(scenario_key: str, policy_key: str) -> str:
    """Directory name for one scenario/policy check."""
    return f'check-{scenario_key}-{policy_key}'


def _artifacts(report: dict, want_html: bool) -> dict[str, str]:
    """Render every byte of a generation up front, before anything is published."""
    artifacts = {REPORT_JSON: canonical(report) + '\n'}
    if want_html:
        artifacts[REPORT_HTML] = render_html(report)
    return artifacts


def _generation_intact(generation: Path, artifacts: dict[str, str]) -> bool:
    """Validate an existing generation by its bytes, never by trusting its name.

    A directory named after a content hash is only usable if it really holds every
    required artifact with exactly the expected bytes; a truncated or half-published
    generation is treated as absent and republished.
    """
    for name, text in artifacts.items():
        try:
            if (generation / name).read_bytes() != text.encode('utf-8'):
                return False
        except OSError:
            return False
    return True


def _publish_generation(generation: Path, artifacts: dict[str, str]) -> None:
    """Stage a whole generation, then publish it with one directory rename.

    Every artifact is written into a private staging directory first, so the single
    rename is what makes the generation visible: there is no window in which a reader
    can see a JSON from one generation beside an HTML from another.

    Republishing over a damaged directory of the same name moves the damaged copy aside
    and restores it if the swap fails. That repair path is two renames, not one, so it is
    not crash-atomic; the ordinary first publication of a generation is.
    """
    parent = generation.parent
    parent.mkdir(parents=True, exist_ok=True)
    staged: Path | None = Path(tempfile.mkdtemp(dir=str(parent), prefix=f'.{generation.name}.staging.'))
    retired: Path | None = None
    try:
        for name, text in sorted(artifacts.items()):
            _write_file(staged / name, text)
        if generation.exists():
            retired = parent / f'.{generation.name}.retired.{os.getpid()}'
            shutil.rmtree(retired, ignore_errors=True)
            os.replace(generation, retired)
        os.replace(staged, generation)
        staged = None
    finally:
        if staged is not None:
            shutil.rmtree(staged, ignore_errors=True)
        if retired is not None:
            if generation.exists():
                shutil.rmtree(retired, ignore_errors=True)
            else:  # the swap failed: put the previous copy back rather than lose it
                os.replace(retired, generation)


def _pointer(report: dict, names: list[str]) -> str:
    return canonical({
        'schema': POINTER_SCHEMA, 'content_sha256': report['content_sha256'],
        'generation': f"{GENERATIONS}/{report['content_sha256']}",
        'files': sorted(names), 'verdict': report['verdict'],
        'note': 'every file listed here belongs to the one generation named above',
    }) + '\n'


def write_reports(report: dict, directory: Path, want_html: bool) -> list[Path]:
    """Publish one generation of a report; identical runs produce identical bytes.

    A generation is an immutable directory named after the report content hash, holding
    the JSON and (when requested) the HTML. Both are rendered and the report identity is
    re-verified before anything is created, the generation is published by a single
    directory rename, and only then is the `current.json` pointer replaced atomically.
    Earlier complete generations are never touched, and a failure anywhere leaves the
    previous pointer in place rather than reporting a partial publication as success.

    Returns the paths inside the published generation, so a reader that uses them cannot
    mix generations.
    """
    stored = report.get('content_sha256')
    if stored != digest({key: value for key, value in report.items() if key != 'content_sha256'}):
        raise ValueError('report identity mismatch: refusing to write an unverified report')
    artifacts = _artifacts(report, want_html)
    base = directory / report_stem(report['scenario']['key'], report['policy']['key'])
    generation = base / GENERATIONS / stored
    if not _generation_intact(generation, artifacts):
        _publish_generation(generation, artifacts)
    if not _generation_intact(generation, artifacts):  # never claim a partial publication
        raise OSError(f'partial publication: {generation} does not hold the expected report bytes')
    _write_atomic(base / CURRENT_NAME, _pointer(report, list(artifacts)))
    return [generation / name for name in sorted(artifacts)]


def read_reports(directory: Path, scenario_key: str, policy_key: str) -> list[Path]:
    """Resolve the current published artifacts of one check, all from one generation."""
    base = directory / report_stem(scenario_key, policy_key)
    pointer = json.loads((base / CURRENT_NAME).read_text(encoding='utf-8'))
    paths = [base / pointer['generation'] / name for name in pointer['files']]
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise OSError(f"current.json points at missing artifacts: {', '.join(missing)}")
    return paths


def _operational_error(stage: str, exc: Exception) -> int:
    """Emit a machine-readable failure on stderr; never a verdict about physics."""
    sys.stderr.write(canonical({
        'schema': ERROR_SCHEMA, 'verdict': 'operational-error', 'stage': stage,
        'error_type': type(exc).__name__, 'message': str(exc), 'exit_code': OPERATIONAL_EXIT,
        'note': 'The check did not complete. This is a tool, fixture or I/O failure, not a pass, '
                'a performance regression and not an observation about the simulated plant.',
    }) + '\n')
    return OPERATIONAL_EXIT


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 0 if not exc.code else 3
    if args.command == 'scenarios':
        print(canonical(listing()))
        return 0
    try:
        report = evaluate(args.scenario, args.policy)
    except Exception as exc:  # fixture or engine failure: not a verdict about the policy
        return _operational_error('scenario preparation', exc)
    if args.command == 'check':
        print(canonical(report))
        return report['exit_code']
    try:
        written = write_reports(report, Path(args.out), args.html)
    except Exception as exc:  # KeyboardInterrupt and SystemExit deliberately propagate
        return _operational_error('report output', exc)
    print(canonical({'schema': LISTING_SCHEMA, 'verdict': report['verdict'],
                     'exit_code': report['exit_code'], 'content_sha256': report['content_sha256'],
                     'reports': [str(path) for path in written]}))
    return report['exit_code']


if __name__ == '__main__':
    raise SystemExit(main())
