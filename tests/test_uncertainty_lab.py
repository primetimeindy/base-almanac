"""Closed registry, evaluator, CLI and loopback surface for the uncertainty experiment.

The experimental surface is additive: it must never touch the legacy check registry, the
frozen receipts or the existing controller-check routes. What is pinned here is that the
browser reaches exactly the same bounded registry the separate CLI reaches, that one
completed run produces every displayed value and the exported bytes, and that a parameter
outside the registry is refused without being echoed back.
"""
import json
import os
import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from almanac import geofleet_server, uncertainty, uncertainty_lab as lab
from almanac.replay import canonical, digest

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = '/api/v1/uncertainty/registry'
RUN = '/api/v1/uncertainty/run'


@pytest.fixture
def service():
    server = ThreadingHTTPServer(('127.0.0.1', 0), geofleet_server.Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    yield f'http://127.0.0.1:{server.server_port}'
    server.shutdown(); server.server_close(); worker.join()


# ---------------------------------------------------------------- registry

def test_registry_is_closed_small_and_bounded():
    listing = lab.listing()
    assert listing['schema'] == 'almanac.uncertainty-registry.v1'
    assert listing['experimental'] is True
    assert listing['safety_verdict'] is None
    keys = [e['key'] for e in listing['experiments']]
    assert keys == sorted(lab.EXPERIMENTS)
    assert 2 <= len(keys) <= 8
    for entry in listing['experiments']:
        assert entry['synthetic'] is True
        assert entry['mechanism'] in lab.MECHANISMS
        scenario = uncertainty.build_scenario(lab.EXPERIMENTS[entry['key']]['spec'])
        assert scenario.duration_s <= lab.MAX_TIMELINE_S
        assert len(scenario.devices) <= lab.MAX_DEVICES


def test_registry_covers_ack_loss_ack_delay_and_reconnect_backfill():
    declared = {lab.EXPERIMENTS[key]['mechanism'] for key in lab.EXPERIMENTS}
    assert {'ack-loss', 'ack-delay', 'reconnect-backfill'} <= declared


@pytest.mark.parametrize('key', sorted(lab.EXPERIMENTS))
def test_each_experiment_really_exercises_the_mechanism_it_declares(key):
    """The registry may not claim a mechanism the run does not actually produce."""
    report = lab.evaluate(key)
    evidence = report['mechanism_evidence']
    assert evidence['claim'] == lab.EXPERIMENTS[key]['mechanism']
    assert evidence['satisfied'] is True, evidence
    for name in evidence['required']:
        assert name in evidence['observed']


def test_ack_loss_delay_and_backfill_show_their_own_distinct_evidence():
    loss = lab.evaluate('ack-loss-unconfirmed')
    delay = lab.evaluate('ack-delay-late-confirm')
    backfill = lab.evaluate('reconnect-backfill')
    overflow = lab.evaluate('backfill-buffer-overflow')
    clean = lab.evaluate('acknowledged-baseline')

    # ACK loss: the device holds a command the observer never gets confirmation of.
    assert loss['metrics']['uncertain_device_seconds'] > 0
    assert sum(d['unacknowledged_command_seconds'] for d in loss['observer'].values()) > 0
    assert sum(d['seconds_holding_command'] for d in loss['evaluator_truth'].values()) > 0
    # ACK delay: unconfirmed first, confirmed later in the same run.
    assert sum(d['unacknowledged_command_seconds'] for d in delay['observer'].values()) > 0
    assert sum(d['acknowledged_command_seconds'] for d in delay['observer'].values()) > 0
    # Reconnect backfill: history arrives keeping its original sample age.
    assert backfill['metrics']['backfilled_samples'] > 0
    assert backfill['metrics']['max_sample_age_s'] > 0
    assert max(d['max_sample_age_s'] for d in backfill['observer'].values()) > 0
    assert backfill['metrics']['dropped_buffered_samples'] == 0
    # A long outage genuinely loses buffered history.
    assert overflow['metrics']['dropped_buffered_samples'] > 0
    # The clean baseline is uncertainty-free, so the contrast is real.
    assert clean['metrics']['uncertain_device_seconds'] == 0
    assert clean['metrics']['backfilled_samples'] == 0


@pytest.mark.parametrize('key,expected', [
    ('acknowledged-baseline', {'uncertain_device_seconds': 0, 'backfilled_samples': 0,
                               'max_sample_age_s': 0, 'dropped_buffered_samples': 0}),
    ('ack-loss-unconfirmed', {'uncertain_device_seconds': 120, 'backfilled_samples': 0,
                              'max_sample_age_s': 0, 'dropped_buffered_samples': 0}),
    ('ack-delay-late-confirm', {'uncertain_device_seconds': 50, 'backfilled_samples': 0,
                                'max_sample_age_s': 0, 'dropped_buffered_samples': 0}),
    # The two outage scenarios report no pending uncertainty: the 60 s absolute TTL leaves no
    # headroom at the control ticks during the outage, so the controller waits instead of
    # issuing a command it could not confirm. Their evidence is the telemetry backlog.
    ('reconnect-backfill', {'uncertain_device_seconds': 0, 'backfilled_samples': 4,
                            'max_sample_age_s': 40, 'dropped_buffered_samples': 0}),
    ('backfill-buffer-overflow', {'uncertain_device_seconds': 0, 'backfilled_samples': 15,
                                  'max_sample_age_s': 75, 'dropped_buffered_samples': 5}),
])
def test_registered_runs_have_these_exact_counters(key, expected):
    """Pin what each registered scenario actually produces, so the registry cannot drift.

    These are integrated by the simulator, not asserted into it: a change to the event order,
    the buffer bound or a registered spec must update these numbers deliberately.
    """
    metrics = lab.evaluate(key)['metrics']
    assert {name: metrics[name] for name in expected} == expected


def test_report_separates_observer_knowledge_from_evaluator_truth():
    report = lab.evaluate('reconnect-backfill')
    assert report['schema'] == 'almanac.uncertainty-experiment.v1'
    assert report['mode'] == uncertainty.MODE
    assert report['experimental'] is True
    assert report['safety']['verdict'] is None
    assert set(report['observer']) == set(report['evaluator_truth'])
    for device in report['observer'].values():
        # Observer rows carry only what the controller could see.
        assert set(device) >= {'freshness_seconds', 'last_sample_s', 'sample_age_s',
                               'reading_is_current', 'reported_physical', 'max_sample_age_s',
                               'history_only_reading_seconds', 'acknowledged_command_seconds',
                               'unacknowledged_command_seconds', 'no_command_seconds',
                               'final_worst_case_kw'}
        assert 'energy_kwh' not in device and 'link_up' not in device
    for device in report['evaluator_truth'].values():
        assert set(device) >= {'physical', 'link_up', 'active_command', 'setpoint_kw',
                              'energy_kwh', 'capacity_kw', 'reserve_kwh',
                              'seconds_holding_command'}
    assert 'never a controller input' in report['separation_note']
    assert report['expiry']['command_ttl_s'] == report['config']['command_ttl_s']
    assert 'not evidence' in report['reserve']['meaning']
    assert report['reserve']['reserve_preserved'] is report['metrics']['reserve_preserved']


def test_timeline_is_bounded_and_comes_from_the_same_run():
    report = lab.evaluate('ack-delay-late-confirm')
    timeline = report['timeline']
    assert 0 < len(timeline) <= lab.MAX_TIMELINE_S
    assert len(timeline) == report['experiment']['timeline_rows']
    assert [row['time_s'] for row in timeline] == list(range(len(timeline)))
    row = timeline[0]
    assert set(row) == {'time_s', 'observed', 'truth', 'decision', 'grid_kw'}
    # Real values, not placeholders: the observer row carries its own sample age.
    for device_id, observed in timeline[-1]['observed'].items():
        assert observed['freshness'] in (uncertainty.REPORTING, uncertainty.LATE,
                                         uncertainty.UNREACHABLE)
        assert Fraction(observed['worst_case_kw']) >= 0
        assert report['observer'][device_id]['final_worst_case_kw'] == observed['worst_case_kw']


def test_identity_covers_the_new_evaluator_and_registry_sources():
    report = lab.evaluate('acknowledged-baseline')
    code = report['code']
    assert 'almanac.uncertainty' in code['modules'] and 'almanac.uncertainty_lab' in code['modules']
    assert set(code['modules']) == set(lab.SOURCE_MODULES)
    assert code['manifest_sha256'] == digest(code['modules'])
    assert 'supply-chain' in code['excludes']
    stored = report.pop('content_sha256')
    assert digest(report) == stored


def test_evaluate_is_deterministic_and_refuses_unknown_keys():
    assert canonical(lab.evaluate('ack-loss-unconfirmed')) == canonical(lab.evaluate('ack-loss-unconfirmed'))
    for bad in ['bogus', '', 'almanac.uncertainty:simulate', '../pyproject.toml', 'ACK-LOSS-UNCONFIRMED']:
        with pytest.raises(KeyError):
            lab.evaluate(bad)


def test_evaluator_reuses_simulate_and_never_reimplements_physics(monkeypatch):
    calls = []
    real = uncertainty.simulate

    def spy(scenario=None, **kwargs):
        calls.append(scenario)
        return real(scenario, **kwargs)
    monkeypatch.setattr(lab.uncertainty, 'simulate', spy)
    lab.evaluate('acknowledged-baseline')
    assert len(calls) == 1
    source = (ROOT / 'src/almanac/uncertainty_lab.py').read_text()
    assert 'deliverable_kw' not in source and 'SECONDS_PER_HOUR' not in source


# ---------------------------------------------------------------- CLI

def test_cli_lists_and_runs_the_same_bounded_registry(capsys):
    assert lab.main(['experiments']) == 0
    listing = json.loads(capsys.readouterr().out)
    assert listing == lab.listing()
    assert lab.main(['run', '--experiment', 'ack-loss-unconfirmed']) == 0
    report = json.loads(capsys.readouterr().out)
    assert report == lab.evaluate('ack-loss-unconfirmed')


@pytest.mark.parametrize('argv', [[], ['run'], ['run', '--experiment', 'bogus'],
                                  ['run', '--experiment', 'almanac.uncertainty:simulate'],
                                  ['check', '--scenario', 'core'], ['experiments', '--experiment', 'x']])
def test_cli_rejects_unknown_or_malformed_arguments(argv):
    assert lab.main(argv) == 3


def test_legacy_check_cli_registry_is_untouched():
    from almanac import cli
    assert sorted(cli.SCENARIOS) == ['all', 'core', 'empty', 'west', 'wide']
    assert sorted(cli.POLICIES) == ['conservative-share', 'hold-zero', 'naive-target-fill']
    assert cli.SOURCE_MODULES == ('almanac.cli', 'almanac.controller_contract', 'almanac.fleet',
                                  'almanac.geofleet', 'almanac.inspector', 'almanac.policies',
                                  'almanac.replay')


# ---------------------------------------------------------------- HTTP

def test_http_registry_is_the_cli_registry_and_takes_no_query(service):
    with urlopen(service + REGISTRY) as response:
        assert response.headers['Content-Type'] == 'application/json'
        body = response.read()
    assert body == canonical(lab.listing()).encode()
    with pytest.raises(HTTPError) as error:
        urlopen(service + REGISTRY + '?experiment=acknowledged-baseline')
    assert error.value.code == 400


@pytest.mark.parametrize('key', sorted(lab.EXPERIMENTS))
def test_http_run_returns_one_report_and_the_exported_bytes(service, key):
    with urlopen(f'{service}{RUN}?experiment={key}') as response:
        envelope = json.load(response)
    expected = lab.evaluate(key)
    assert envelope['schema'] == 'almanac.uncertainty-envelope.v1'
    assert envelope['experimental'] is True
    assert envelope['report'] == expected
    assert envelope['report']['experiment']['key'] == key
    assert envelope['report_json'] == canonical(expected) + '\n'
    assert json.loads(envelope['report_json']) == envelope['report']
    assert envelope['downloads'] == {'json': lab.experiment_stem(key) + '.json'}


def test_http_run_is_consistent_with_its_own_timeline_and_identity(service):
    with urlopen(f'{service}{RUN}?experiment=reconnect-backfill') as response:
        envelope = json.load(response)
    report = envelope['report']
    assert report['experiment']['timeline_rows'] == len(report['timeline'])
    assert report['code']['manifest_sha256'] == lab.code_manifest()['manifest_sha256']
    stored = dict(report)
    assert digest({k: v for k, v in stored.items() if k != 'content_sha256'}) == report['content_sha256']
    # Rollups are the run's own timeline, not a second evaluation.
    for device_id, observed in report['observer'].items():
        last = report['timeline'][-1]['observed'][device_id]
        assert observed['last_sample_s'] == last['last_sample_s']
        assert observed['sample_age_s'] == last['sample_age_s']
        assert observed['reading_is_current'] == last['reading_is_current']
        total = (observed['acknowledged_command_seconds'] + observed['unacknowledged_command_seconds']
                 + observed['no_command_seconds'])
        assert total == len(report['timeline'])
        assert sum(observed['freshness_seconds'].values()) == len(report['timeline'])


@pytest.mark.parametrize('query', [
    '',                                                   # no bare route
    '?experiment=',                                       # blank value
    '?experiment=bogus',                                  # outside the closed registry
    '?experiment=acknowledged-baseline&extra=1',          # unknown parameter
    '?experiment=acknowledged-baseline&experiment=ack-loss-unconfirmed',  # duplicate
    '?scenario=core',                                     # wrong parameter name
    '?experiment=almanac.uncertainty_lab:EXPERIMENTS',    # no import path
    '?experiment=/etc/passwd',                            # no filesystem path
    '?experiment=../../pyproject.toml',
    '?experiment=%7B%22duration_s%22%3A1%7D',             # no arbitrary scenario JSON
    '?experiment=acknowledged-baseline&' + 'x' * 120,     # bounded query
])
def test_http_only_closed_registry_keys_are_accepted(service, query):
    with pytest.raises(HTTPError) as error:
        urlopen(service + RUN + query)
    assert error.value.code in (400, 414)
    if error.value.code == 400:
        assert set(json.loads(error.value.read())) == {'error'}


@pytest.mark.parametrize('query', ['?experiment=%3Cscript%3E', '?experiment=REFLECT_ME',
                                   '?REFLECT_ME', '?experiment=a&REFLECT_ME',
                                   '?REFLECT_ME=1&experiment=acknowledged-baseline'])
def test_http_rejected_parameters_are_never_reflected(service, query):
    with pytest.raises(HTTPError) as error:
        urlopen(service + RUN + query)
    assert error.value.code == 400
    body = error.value.read()
    assert b'REFLECT_ME' not in body and b'script' not in body
    assert set(json.loads(body)) == {'error'}


@pytest.mark.parametrize('headers', [{'Host': 'evil.example'}, {'Origin': 'http://evil.example'}])
@pytest.mark.parametrize('target', [REGISTRY, RUN + '?experiment=acknowledged-baseline'])
def test_http_non_loopback_authority_is_refused(service, target, headers):
    with pytest.raises(HTTPError) as error:
        urlopen(Request(service + target, headers=headers))
    assert error.value.code == 403


@pytest.mark.parametrize('target', ['/api/v1/uncertainty', '/api/v1/uncertainty/',
                                    '/api/v1/uncertainty/run/report.json',
                                    '/src/almanac/uncertainty_lab.py'])
def test_http_no_repository_file_is_reachable(service, target):
    with pytest.raises(HTTPError) as error:
        urlopen(service + target)
    assert error.value.code in (400, 404)


def test_http_failure_is_an_operational_error_not_a_result(service, monkeypatch):
    def unavailable(*args, **kwargs):
        raise RuntimeError('simulator unavailable')
    monkeypatch.setattr(geofleet_server, 'uncertainty_evaluate', unavailable)
    with pytest.raises(HTTPError) as error:
        urlopen(service + RUN + '?experiment=acknowledged-baseline')
    assert error.value.code in (500, 503)
    body = json.loads(error.value.read())
    assert body['verdict'] == 'operational-error'
    assert 'report' not in body and 'timeline' not in body


def test_http_uncertainty_run_uses_the_shared_run_slot(service, monkeypatch):
    started, release = threading.Event(), threading.Event()
    original = geofleet_server.uncertainty_evaluate

    def blocking(key):
        started.set()
        assert release.wait(5)
        return original(key)
    monkeypatch.setattr(geofleet_server, 'uncertainty_evaluate', blocking)
    try:
        with ThreadPoolExecutor(2) as pool:
            first = pool.submit(urlopen, service + RUN + '?experiment=acknowledged-baseline')
            assert started.wait(3)
            with pytest.raises(HTTPError) as error:
                urlopen(service + '/api/v1/check/run?scenario=core&policy=hold-zero')
            assert error.value.code == 503
            release.set()
            with first.result(timeout=15) as response:
                assert response.status == 200
    finally:
        release.set()


def test_http_run_leaves_the_out_directory_byte_identical(service):
    """Named for what it checks: no file under `out/` appears, vanishes or changes.

    This is a directory comparison and nothing more. It says nothing about sockets, about
    writes anywhere else on the filesystem, or about what the request handler could do in
    principle: `test_evaluate_opens_no_socket_and_no_file_for_writing` covers containment.
    """
    def snapshot():
        return sorted((str(p.relative_to(ROOT)), p.stat().st_size if p.is_file() else None,
                       p.stat().st_mtime_ns) for p in (ROOT / 'out').rglob('*'))
    before = snapshot()
    with urlopen(service + RUN + '?experiment=ack-loss-unconfirmed') as response:
        assert response.status == 200
    assert snapshot() == before


def test_evaluate_opens_no_socket_and_no_file_for_writing(monkeypatch):
    """Genuine in-process guards, not a directory listing.

    The evaluator is allowed to read files, because the source manifest hashes its own
    module sources. Any write mode and any socket is an immediate failure.
    """
    import builtins
    import io
    import socket

    real_open = builtins.open

    def read_only_open(file, mode='r', *args, **kwargs):
        if set(mode) & set('wxa+'):
            raise AssertionError(f'opened {file!r} for writing, mode {mode!r}')
        return real_open(file, mode, *args, **kwargs)

    def no_socket(*args, **kwargs):
        raise AssertionError('opened a socket')

    monkeypatch.setattr(builtins, 'open', read_only_open)
    monkeypatch.setattr(io, 'open', read_only_open)
    monkeypatch.setattr(socket, 'socket', no_socket)
    monkeypatch.setattr(socket, 'create_connection', no_socket)
    assert lab.evaluate('reconnect-backfill')['schema'] == lab.SCHEMA
    assert lab.listing()['schema'] == lab.REGISTRY_SCHEMA


# ------------------------------------------------- backlog is a claim, not any clock advance

def test_a_healthy_link_skips_no_sample_time_and_backfills_nothing():
    """The once-per-period advance of a healthy link is not evidence of anything."""
    for key in ['acknowledged-baseline', 'ack-loss-unconfirmed', 'ack-delay-late-confirm']:
        report = lab.evaluate(key)
        assert report['metrics']['backfilled_samples'] == 0, key
        for device_id, observed in report['observer'].items():
            assert observed['skipped_sample_time_s'] == 0, (key, device_id)
        assert 'sample_time_jump_s' not in next(iter(report['observer'].values()))


def test_only_the_outage_device_skips_sample_time_and_only_outages_backfill():
    report = lab.evaluate('reconnect-backfill')
    assert report['metrics']['backfilled_samples'] > 0
    assert report['observer']['d1']['skipped_sample_time_s'] > 0
    assert report['observer']['d2']['skipped_sample_time_s'] == 0
    # Read off this run's own timeline, and only the excess over one telemetry period counts.
    period = report['config']['telemetry_period_s']
    skipped, previous = [0], None
    for row in report['timeline']:
        last = row['observed']['d1']['last_sample_s']
        if last is None:
            continue
        if previous is not None and last > previous:
            skipped.append(last - previous - period)
        previous = last
    assert max(skipped) == report['observer']['d1']['skipped_sample_time_s']
    overflow = lab.evaluate('backfill-buffer-overflow')
    assert overflow['observer']['d1']['skipped_sample_time_s'] > 0
    assert overflow['observer']['d2']['skipped_sample_time_s'] == 0


def test_browser_does_not_label_a_plain_clock_advance_as_backlog():
    text = (ROOT / 'demo/geofleet.html').read_text()
    assert '(backlog delivered as history)' not in text
    assert 'seen.skipped_sample_time_s' in text
    assert 'sample_time_jump_s' not in text


# ------------------------------------------------- source provenance fails closed on drift

DRIFT_PROBES = {
    'before': '''
import pathlib
from almanac import uncertainty_lab as lab
target = pathlib.Path(lab.uncertainty.__file__)
target.write_bytes(target.read_bytes() + b"\\n# injected after import, before the run\\n")
print("EXIT", lab.main(["run", "--experiment", "acknowledged-baseline"]))
''',
    'during': '''
import pathlib
from almanac import uncertainty, uncertainty_lab as lab
target = pathlib.Path(lab.uncertainty.__file__)
original = uncertainty.simulate
def drifting(scenario):
    result = original(scenario)
    target.write_bytes(target.read_bytes() + b"\\n# injected mid run\\n")
    return result
uncertainty.simulate = drifting
print("EXIT", lab.main(["run", "--experiment", "acknowledged-baseline"]))
''',
    'missing': '''
import pathlib
from almanac import uncertainty_lab as lab
pathlib.Path(lab.uncertainty.__file__).unlink()
print("EXIT", lab.main(["run", "--experiment", "acknowledged-baseline"]))
''',
    'clean': '''
from almanac import uncertainty_lab as lab
print("EXIT", lab.main(["run", "--experiment", "acknowledged-baseline"]))
''',
}


def _isolated_probe(tmp_path, name):
    """Run one probe against a throwaway copy of `src/`.

    The copy is the point: source drift is reproduced by editing files in `tmp_path`, never
    in this checkout, so a live server running from the real tree is never disturbed.
    """
    source = tmp_path / 'src'
    shutil.copytree(ROOT / 'src', source)
    script = tmp_path / 'probe.py'
    script.write_text(DRIFT_PROBES[name])
    env = dict(os.environ, PYTHONPATH=str(source), PYTHONDONTWRITEBYTECODE='1')
    return subprocess.run([sys.executable, str(script)], cwd=str(tmp_path), env=env,
                          capture_output=True, text=True, timeout=120)


def test_isolated_copy_still_produces_a_report_without_drift(tmp_path):
    """Control: the fixture itself is not what makes the drift probes fail."""
    done = _isolated_probe(tmp_path, 'clean')
    assert 'EXIT 0' in done.stdout, done.stderr
    assert lab.SCHEMA in done.stdout


@pytest.mark.parametrize('name', ['before', 'during', 'missing'])
def test_source_drift_is_rejected_and_no_report_is_produced(tmp_path, name):
    done = _isolated_probe(tmp_path, name)
    assert f'EXIT {lab.OPERATIONAL_EXIT}' in done.stdout, (done.stdout, done.stderr)
    # No result, no verdict, no manifest: the run is reported as an operational error only.
    assert lab.SCHEMA not in done.stdout
    assert 'manifest_sha256' not in done.stdout
    failure = json.loads(done.stderr.strip().splitlines()[-1])
    assert failure['schema'] == lab.ERROR_SCHEMA
    assert failure['verdict'] == 'operational-error'
    assert failure['error_type'] == 'SourceDriftError'


def test_code_manifest_claims_a_disk_snapshot_and_not_executed_bytes():
    code = lab.code_manifest()
    assert set(code['modules']) == set(lab.SOURCE_MODULES)
    assert code['manifest_sha256'] == digest(code['modules'])
    assert 'supply-chain' in code['excludes']
    # The narrowed claim is stated in the report, not only in a docstring.
    assert 'disk' in code['snapshot'] and 'imported' in code['snapshot']
    assert 'not proof of the bytes the interpreter executed' in code['not_an_attestation_of']
    preloaded = code['loaded_before_this_module']
    assert isinstance(preloaded, list) and set(preloaded) <= set(lab.SOURCE_MODULES)
    assert 'almanac.uncertainty_lab' not in preloaded
    assert 'no report is produced' in code['on_drift']


# ---------------------------------------------------------------- browser surface

def test_browser_has_a_separate_experimental_section_using_these_routes_only():
    text = (ROOT / 'demo/geofleet.html').read_text()
    section = text.split('<section id="uncertainty-lab"')[1].split('</section>')[0]
    assert 'Experimental' in section
    assert 'experimental' in section.lower()
    assert '—' not in section  # no em dashes in new copy
    assert REGISTRY in text and RUN in text
    assert text.count(RUN) == 1
    for token in ['id="unc-experiment"', 'id="unc-run"', 'id="unc-result"', 'id="unc-status"',
                  'id="unc-error"', 'id="unc-timeline"', 'id="unc-device"', 'id="unc-json"',
                  'role="alert"', 'AbortSignal.timeout(20000)']:
        assert token in text
    # The legacy check section and its routes are untouched.
    assert '<section id="controller-check"' in text
    assert '/api/v1/check/run' in text and '/api/v1/check/registry' in text


def test_browser_experimental_section_shows_the_three_axes_distinctly():
    text = (ROOT / 'demo/geofleet.html').read_text()
    for token in ['Command received', 'Acknowledged', 'Telemetry freshness',
                  'Observer knowledge', 'Evaluator truth', 'Original sample age',
                  'Command expiry', 'Reserve']:
        assert token in text, token
    assert 'no production safety verdict' in text
    assert 'synthetic' in text and 'uncalibrated' in text


def test_browser_experimental_section_invalidates_locks_and_guards_generations():
    text = (ROOT / 'demo/geofleet.html').read_text()
    assert "$('unc-experiment').addEventListener('change',()=>clearUnc(" in text
    clear = text.split('function clearUnc(status){')[1].split('\n')[0]
    for dropped in ['uncGeneration++', 'uncEnvelope=null', "$('unc-result').hidden=true",
                    "$('unc-observer').replaceChildren()", "$('unc-truth').replaceChildren()",
                    "$('unc-download-status').textContent=''", "$('unc-error').textContent=''"]:
        assert dropped in clear, dropped
    lock = text.split('function lockUnc(busy){')[1].split('\n')[0]
    assert "['unc-experiment','unc-run','unc-timeline','unc-device']" in lock and 'disabled=busy' in lock
    assert 'lockUnc(true)' in text and 'finally{lockUnc(false)}' in text
    assert text.count('if(generation!==uncGeneration)return') == 2
    assert 'envelope.report.experiment.key!==key' in text
    assert 'uncEnvelope.report_json' in text  # export is the same completed run
