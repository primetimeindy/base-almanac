"""Behavioural tests for the offline controller-check CLI.

Every exit code the CLI documents is exercised here through the real entry point.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

import pytest

from almanac.cli import (CURRENT_NAME, OPERATIONAL_EXIT, POLICIES, SCENARIOS, evaluate, main,
                         read_reports, source_digests, write_reports)
from almanac.policies import ConservativeShare, NaiveTargetFill
from almanac.replay import digest

ROOT = Path(__file__).resolve().parents[1]


def run_cli(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def check_report(capsys, scenario, policy):
    code, out, _ = run_cli(capsys, 'check', '--scenario', scenario, '--policy', policy)
    return code, json.loads(out)


def test_scenarios_lists_only_registry_entries_and_labels_them_synthetic(capsys):
    code, out, _ = run_cli(capsys, 'scenarios')
    listing = json.loads(out)
    assert code == 0
    assert [s['key'] for s in listing['scenarios']] == sorted(SCENARIOS)
    assert all(s['synthetic'] is True for s in listing['scenarios'])
    assert [p['key'] for p in listing['policies']] == sorted(POLICIES)


def test_conservative_policy_passes_with_assertions_that_support_it(capsys):
    code, report = check_report(capsys, 'core', 'conservative-share')
    assert (code, report['verdict'], report['exit_code']) == (0, 'pass', 0)
    measured = report['measured']
    assert measured['realized_overshoot_kw'] == 0
    assert measured['reserve_violations'] == 0
    assert measured['configured_reserve_preserved'] is True
    assert measured['attempted_overcommit_rejected'] is False
    assert measured['delivered_kwh'] > 0
    assert measured['controller_setpoint_changes'] >= 0


def test_naive_policy_is_caught_as_unsafe_without_claiming_physical_overshoot(capsys):
    code, report = check_report(capsys, 'core', 'naive-target-fill')
    assert (code, report['verdict'], report['exit_code']) == (2, 'unsafe', 2)
    assert report['measured'] is None
    rejection = report['rejection']
    assert rejection['attempted_overcommit_rejected'] is True
    assert rejection['realized_overshoot_kw'] is None
    assert 'budget' in rejection['message']
    # The note must not claim an absence of dispatch or overshoot it cannot observe.
    assert 'not dispatched' in rejection['note']
    assert 'no receipt was returned' in rejection['note']
    assert 'none is ruled out' in rejection['note']
    assert rejection['partial_trajectory'] == 'unavailable: the aborted run returned no receipt'


def test_zero_output_policy_is_a_defined_performance_regression(capsys):
    code, report = check_report(capsys, 'core', 'hold-zero')
    assert (code, report['verdict'], report['exit_code']) == (1, 'regression', 1)
    measured = report['measured']
    assert measured['shortfall_kwh'] > measured['reference_baseline_shortfall_kwh']
    assert measured['realized_overshoot_kw'] == 0
    assert 'regression' in ' '.join(report['findings']).lower()


@pytest.mark.parametrize('argv', [
    ('check', '--scenario', 'not-an-area', '--policy', 'conservative-share'),
    ('check', '--scenario', 'core', '--policy', 'examples.evil:Policy'),
    ('check', '--scenario', 'core'),
    ('check',),
    ('inspect', '--scenario', 'core'),
    (),
    ('scenarios', '--scenario', 'core'),
])
def test_unknown_or_malformed_arguments_exit_three(capsys, argv):
    code, _, _ = run_cli(capsys, *argv)
    assert code == 3


def test_unexpected_exception_fails_closed_as_unsafe(capsys, monkeypatch):
    class Bomb:
        policy_id = 'bomb'
        policy_version = '1.0.0'

        def decide(self, observation):
            raise RuntimeError('unexpected policy failure')

    monkeypatch.setitem(POLICIES, 'bomb', {'factory': Bomb, 'source': 'test:Bomb',
                                           'description': 'raises an unexpected error'})
    code, report = check_report(capsys, 'core', 'bomb')
    assert (code, report['verdict']) == (2, 'unsafe')
    assert report['measured'] is None


def test_report_identifies_engine_scenario_policy_config_and_verifiable_hash(capsys):
    _, report = check_report(capsys, 'west', 'conservative-share')
    assert report['engine']['receipt_schema'] == 'almanac.fleet.v1'
    assert report['scenario']['id'] == 'geofleet-beryl-west-v1'
    assert len(report['scenario']['scenario_sha256']) == 64
    assert report['policy']['id'] == 'conservative-share'
    assert report['config']['physics_step_s'] == 10
    stored = report.pop('content_sha256')
    assert digest(report) == stored


def test_repeated_runs_write_identical_report_bytes(tmp_path, capsys):
    first = tmp_path / 'a'
    second = tmp_path / 'b'
    for out in (first, second):
        code, _, _ = run_cli(capsys, 'run', '--scenario', 'empty', '--policy',
                             'conservative-share', '--out', str(out), '--html')
        assert code == 0
    paths = {out: read_reports(out, 'empty', 'conservative-share') for out in (first, second)}
    assert [p.name for p in paths[first]] == ['report.html', 'report.json']
    for left, right in zip(paths[first], paths[second]):
        assert left.name == right.name
        assert left.read_bytes() == right.read_bytes()
    # The same generation directory name on both sides, so identical runs are interchangeable.
    assert [p.parent.name for p in paths[first]] == [p.parent.name for p in paths[second]]


def test_html_report_is_self_contained_and_escapes_text(tmp_path, capsys):
    code, _, _ = run_cli(capsys, 'run', '--scenario', 'core', '--policy', 'naive-target-fill',
                         '--out', str(tmp_path), '--html')
    assert code == 2
    published = read_reports(tmp_path, 'core', 'naive-target-fill')
    html = next(p for p in published if p.name.endswith('.html')).read_text()
    assert '<script' not in html.lower()
    assert not re.search(r'(src|href)\s*=\s*["\']?(?:https?:)?//', html, re.I)
    # The embedded report is escaped text, not live markup.
    assert '&quot;schema&quot;' in html
    assert '"schema"' not in html
    assert 'almanac.check.v1' in html
    assert 'not a simulated physical overshoot' in html
    assert 'that partial trajectory' in html
    # Source URLs may be named as text, never fetched as a resource.
    assert 'nhc.noaa.gov' in html


class LateReject:
    """Behave conservatively for the first tick, then overcommit on a later one.

    Built from the shipped policies so the accepted first tick really is dispatched by
    the engine before the harness rejects the second proposal.
    """
    policy_id = 'late-reject'
    policy_version = '1.0.0'

    def __init__(self):
        self.calls = 0
        self.safe = ConservativeShare()
        self.greedy = NaiveTargetFill()

    def decide(self, observation):
        self.calls += 1
        return (self.greedy if self.calls > 1 else self.safe).decide(observation)


def test_late_rejection_does_not_claim_the_plant_was_untouched(capsys, monkeypatch):
    """A rejection on a later tick cannot assert that nothing was ever dispatched."""
    seen = []

    class Recording(LateReject):
        def decide(self, observation):
            seen.append(observation['time_s'])
            return super().decide(observation)

    monkeypatch.setitem(POLICIES, 'late-reject', {'factory': Recording, 'source': 'test:LateReject',
                                                  'description': 'overcommits after one accepted tick'})
    code, report = check_report(capsys, 'core', 'late-reject')
    # The rejection really is late: the engine dispatched at least one accepted tick first.
    assert len(seen) > 1 and seen[0] < seen[-1]
    assert (code, report['verdict'], report['exit_code']) == (2, 'unsafe', 2)
    assert report['measured'] is None
    rejection = report['rejection']
    assert rejection['attempted_overcommit_rejected'] is True
    note = rejection['note']
    assert 'No commands reached the simulated plant' not in note
    assert 'no physical overshoot was simulated' not in note
    # It must say the rejected proposal was not dispatched, and that earlier ticks are unknown.
    assert 'not dispatched' in note or 'never dispatched' in note
    assert 'earlier' in note.lower()
    assert rejection['partial_trajectory'] == 'unavailable: the aborted run returned no receipt'
    assert rejection['realized_overshoot_kw'] is None
    # No fabricated partial metrics.
    assert not any(key.endswith('_kwh') for key in rejection)


def test_report_records_exact_source_bytes_of_the_code_that_produced_it(capsys):
    _, report = check_report(capsys, 'core', 'conservative-share')
    modules = report['code']['modules']
    expected = {
        'almanac.cli': ROOT / 'src/almanac/cli.py',
        'almanac.controller_contract': ROOT / 'src/almanac/controller_contract.py',
        'almanac.fleet': ROOT / 'src/almanac/fleet.py',
        'almanac.geofleet': ROOT / 'src/almanac/geofleet.py',
        'almanac.inspector': ROOT / 'src/almanac/inspector.py',
        'almanac.policies': ROOT / 'src/almanac/policies.py',
        'almanac.replay': ROOT / 'src/almanac/replay.py',
    }
    assert set(modules) == set(expected)
    for name, path in expected.items():
        assert modules[name] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert report['code']['manifest_sha256'] == digest(modules)
    # No absolute machine path leaks into the report.
    assert str(ROOT) not in json.dumps(report)


def test_failure_reports_also_carry_the_source_manifest(capsys):
    _, report = check_report(capsys, 'core', 'naive-target-fill')
    assert report['measured'] is None
    assert len(report['code']['manifest_sha256']) == 64
    assert len(report['code']['modules']['almanac.fleet']) == 64


def test_source_manifest_changes_when_source_bytes_change(tmp_path):
    path = tmp_path / 'fake_module.py'
    path.write_bytes(b'x = 1\n')
    before = source_digests({'fake': str(path)})
    path.write_bytes(b'x = 2\n')
    after = source_digests({'fake': str(path)})
    assert before != after
    assert before['fake'] == hashlib.sha256(b'x = 1\n').hexdigest()
    assert digest(before) != digest(after)


def test_malformed_registered_fixture_is_an_operational_error_not_a_verdict(capsys, monkeypatch):
    def broken(_request):
        raise ValueError('scenario fixture is malformed')

    monkeypatch.setattr('almanac.cli.scenario_for', broken)
    code, out, err = run_cli(capsys, 'check', '--scenario', 'core', '--policy', 'conservative-share')
    assert code == OPERATIONAL_EXIT
    assert code not in (0, 1)
    assert out == ''
    failure = json.loads(err)
    assert failure['verdict'] == 'operational-error'
    assert failure['error_type'] == 'ValueError'
    assert 'malformed' in failure['message']
    assert failure['exit_code'] == OPERATIONAL_EXIT


def test_unwriteable_output_destination_is_an_operational_error(tmp_path, capsys):
    blocked = tmp_path / 'blocked'
    blocked.mkdir()
    blocked.chmod(0o500)
    try:
        code, out, err = run_cli(capsys, 'run', '--scenario', 'empty', '--policy',
                                 'conservative-share', '--out', str(blocked / 'reports'))
    finally:
        blocked.chmod(0o700)
    assert code == OPERATIONAL_EXIT
    failure = json.loads(err)
    assert failure['verdict'] == 'operational-error'
    assert failure['error_type'] in ('PermissionError', 'OSError')


def test_existing_file_as_output_directory_is_an_operational_error(tmp_path, capsys):
    occupied = tmp_path / 'not-a-dir'
    occupied.write_text('already here\n')
    code, out, err = run_cli(capsys, 'run', '--scenario', 'empty', '--policy',
                             'conservative-share', '--out', str(occupied))
    assert code == OPERATIONAL_EXIT
    assert json.loads(err)['verdict'] == 'operational-error'
    assert occupied.read_text() == 'already here\n'


def test_write_reports_refuses_a_report_whose_identity_does_not_match(tmp_path):
    report = evaluate('empty', 'conservative-share')
    report['verdict'] = 'pass-but-tampered'
    with pytest.raises(ValueError, match='identity'):
        write_reports(report, tmp_path, False)
    assert list(tmp_path.iterdir()) == []


def test_failed_html_render_preserves_the_previous_complete_generation(tmp_path, monkeypatch, capsys):
    code, _, _ = run_cli(capsys, 'run', '--scenario', 'empty', '--policy', 'conservative-share',
                         '--out', str(tmp_path), '--html')
    assert code == 0
    published = read_reports(tmp_path, 'empty', 'conservative-share')
    good = [path.read_bytes() for path in published]

    def boom(_report):
        raise RuntimeError('render interrupted')

    monkeypatch.setattr('almanac.cli.render_html', boom)
    code, out, err = run_cli(capsys, 'run', '--scenario', 'empty', '--policy', 'conservative-share',
                             '--out', str(tmp_path), '--html')
    assert code == OPERATIONAL_EXIT
    assert json.loads(err)['verdict'] == 'operational-error'
    # The previous complete generation survives and no truncated artifact is left behind.
    assert read_reports(tmp_path, 'empty', 'conservative-share') == published
    assert [path.read_bytes() for path in published] == good
    assert [p.name for p in sorted(published[0].parent.iterdir())] == ['report.html', 'report.json']
    assert not list((tmp_path / 'check-empty-conservative-share').glob('.*'))


def _regenerate(report: dict, findings: list[str]) -> dict:
    """A second generation of the same check: same stem, different content hash."""
    fresh = dict(report)
    fresh['findings'] = findings
    fresh['content_sha256'] = digest({k: v for k, v in fresh.items() if k != 'content_sha256'})
    return fresh


def _embedded_hash(path: Path) -> str:
    """The content hash the artifact itself carries, JSON or HTML."""
    body = path.read_text()
    if path.name.endswith('.html'):
        body = (re.search(r'<pre>(.*)</pre>', body, re.S).group(1)
                .replace('&quot;', '"').replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&'))
    return json.loads(body)['content_sha256']


def _current_generation(directory: Path, scenario: str, policy: str) -> set[str]:
    """Hashes carried by the artifacts a reader actually gets by following the pointer.

    More than one hash here is the mixed-generation defect. Older generations may still
    exist on disk by design, so this deliberately only follows the current pointer.
    """
    return {_embedded_hash(path) for path in read_reports(directory, scenario, policy)}


def test_failure_on_the_second_rename_never_publishes_a_mixed_generation(tmp_path, monkeypatch):
    """The bug: JSON and HTML were replaced separately, so a failure between them mixed them."""
    old = evaluate('empty', 'conservative-share')
    old_paths = write_reports(old, tmp_path, True)
    assert _current_generation(tmp_path, 'empty', 'conservative-share') == {old['content_sha256']}
    new = _regenerate(old, ['a different second generation'])
    assert new['content_sha256'] != old['content_sha256']

    real_replace = os.replace
    calls = []

    def flaky(src, dst):
        calls.append(dst)
        if len(calls) > 1:
            raise RuntimeError('rename interrupted after the first artifact')
        return real_replace(src, dst)

    monkeypatch.setattr('almanac.cli.os.replace', flaky)
    with pytest.raises(RuntimeError, match='rename interrupted'):
        write_reports(new, tmp_path, True)
    # Exactly one generation is readable, and a partial publish never becomes the current one.
    assert _current_generation(tmp_path, 'empty', 'conservative-share') == {old['content_sha256']}
    assert read_reports(tmp_path, 'empty', 'conservative-share') == old_paths


def test_reader_paths_resolve_one_generation_and_repeat_publication_is_stable(tmp_path):
    report = evaluate('empty', 'conservative-share')
    first = write_reports(report, tmp_path, True)
    again = write_reports(report, tmp_path, True)
    assert first == again
    bodies = [path.read_bytes() for path in first]
    assert all(str(report['content_sha256']) in str(path) for path in first)
    # Republishing an identical generation rewrites nothing and keeps the same bytes.
    assert bodies == [path.read_bytes() for path in again]
    assert _current_generation(tmp_path, 'empty', 'conservative-share') == {report['content_sha256']}


def test_json_only_generation_is_completed_when_html_is_requested_later(tmp_path):
    report = evaluate('empty', 'conservative-share')
    json_only = write_reports(report, tmp_path, False)
    assert len(json_only) == 1
    with_html = write_reports(report, tmp_path, True)
    assert len(with_html) == 2
    assert _current_generation(tmp_path, 'empty', 'conservative-share') == {report['content_sha256']}


def test_a_damaged_generation_is_republished_rather_than_trusted_by_name(tmp_path):
    report = evaluate('empty', 'conservative-share')
    published = write_reports(report, tmp_path, True)
    damaged = next(p for p in published if p.name.endswith('.json'))
    damaged.write_text('{"truncated": true}\n')
    republished = write_reports(report, tmp_path, True)
    assert republished == published
    assert json.loads(damaged.read_text())['content_sha256'] == report['content_sha256']


def test_previous_generation_survives_a_new_publication(tmp_path):
    old = evaluate('empty', 'conservative-share')
    old_paths = write_reports(old, tmp_path, True)
    new = _regenerate(old, ['second generation kept separately'])
    new_paths = write_reports(new, tmp_path, True)
    assert set(old_paths).isdisjoint(new_paths)
    assert json.loads(old_paths[1].read_text())['content_sha256'] == old['content_sha256']
    assert read_reports(tmp_path, 'empty', 'conservative-share') == new_paths


def test_listing_documents_the_operational_error_exit_code(capsys):
    _, out, _ = run_cli(capsys, 'scenarios')
    codes = json.loads(out)['exit_codes']
    assert codes[str(OPERATIONAL_EXIT)].startswith('operational error')


def test_module_fallback_entry_point_runs_offline(tmp_path):
    proc = subprocess.run([sys.executable, '-m', 'almanac', 'scenarios'], cwd=ROOT,
                          capture_output=True, text=True, env={'PYTHONPATH': 'src', 'PATH': '/usr/bin:/bin'})
    assert proc.returncode == 0
    assert json.loads(proc.stdout)['schema'] == 'almanac.cli.v1'
