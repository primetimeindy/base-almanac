"""Loopback HTTP surface for the offline controller check.

The browser must reach exactly the CLI's closed registry and its evaluator, with the
JSON and HTML downloads coming from one evaluation. Nothing here may accept an import
path, a filesystem path or serve a repository file.
"""
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from almanac import cli, geofleet, geofleet_server
from almanac.replay import canonical

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = '/api/v1/check/registry'
RUN = '/api/v1/check/run'

# One genuine example of every verdict the evaluator can reach on a real fixture.
VERDICTS = [('core', 'conservative-share', 'pass', 0),
            ('core', 'hold-zero', 'regression', 1),
            ('core', 'naive-target-fill', 'unsafe', 2)]


@pytest.fixture
def service():
    server = ThreadingHTTPServer(('127.0.0.1', 0), geofleet_server.Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    yield f'http://127.0.0.1:{server.server_port}'
    server.shutdown(); server.server_close(); worker.join()


def test_registry_is_the_cli_registry_and_takes_no_query(service):
    with urlopen(service + REGISTRY) as response:
        assert response.headers['Content-Type'] == 'application/json'
        body = response.read()
    assert body == canonical(cli.listing()).encode()
    listing = json.loads(body)
    assert listing['schema'] == 'almanac.cli.v1'
    assert [s['key'] for s in listing['scenarios']] == sorted(cli.SCENARIOS)
    assert [p['key'] for p in listing['policies']] == sorted(cli.POLICIES)
    assert listing['limits'] == cli.LIMITS
    with pytest.raises(HTTPError) as error:  # a listing has no parameters to bound
        urlopen(service + REGISTRY + '?scenario=core')
    assert error.value.code == 400


@pytest.mark.parametrize('scenario,policy,verdict,exit_code', VERDICTS)
def test_run_returns_one_evaluation_with_both_download_bodies(service, scenario, policy, verdict, exit_code):
    with urlopen(f'{service}{RUN}?scenario={scenario}&policy={policy}') as response:
        envelope = json.load(response)
    expected = cli.evaluate(scenario, policy)
    assert envelope['schema'] == 'almanac.check-envelope.v1'
    assert envelope['report'] == expected
    assert envelope['report']['verdict'] == verdict
    assert envelope['report']['exit_code'] == exit_code
    # The two downloads are byte-identical to what `almanac run --html` would publish
    # for this same report, and both describe the one evaluation above.
    assert envelope['report_json'] == canonical(expected) + '\n'
    assert envelope['report_html'] == cli.render_html(expected)
    assert json.loads(envelope['report_json']) == envelope['report']
    assert expected['content_sha256'] in envelope['report_html']
    stem = cli.report_stem(scenario, policy)
    assert envelope['downloads'] == {'json': stem + '.json', 'html': stem + '.html'}


def test_rejection_and_regression_carry_their_own_provenance(service):
    reports = {}
    for scenario, policy, verdict, _ in VERDICTS:
        with urlopen(f'{service}{RUN}?scenario={scenario}&policy={policy}') as response:
            reports[verdict] = json.load(response)['report']
    assert reports['unsafe']['measured'] is None
    assert reports['unsafe']['rejection']['attempted_overcommit_rejected'] is True
    assert reports['regression']['measured'] is not None
    assert any('regression' in f.lower() for f in reports['regression']['findings'])
    identities = set()
    for report in reports.values():
        assert report['scenario']['scenario_sha256']
        assert report['code']['manifest_sha256'] == cli.code_manifest()['manifest_sha256']
        identities.add(report['content_sha256'])
    assert len(identities) == len(reports)  # provenance distinguishes the three runs


@pytest.mark.parametrize('query', [
    '',                                              # strict parsing, no bare route
    '?scenario=core',                                # policy missing
    '?policy=hold-zero',                             # scenario missing
    '?scenario=core&policy=hold-zero&extra=1',       # unknown parameter
    '?scenario=core&scenario=wide&policy=hold-zero', # duplicate parameter
    '?scenario=&policy=hold-zero',                   # blank value
    '?scenario=core&policy=',                        # blank value
    '?scenario=bogus&policy=hold-zero',              # outside the closed registry
    '?scenario=core&policy=bogus',
    '?scenario=core&policy=almanac.policies:ConservativeShare',  # no import path
    '?scenario=core&policy=/etc/passwd',                         # no filesystem path
    '?scenario=../../pyproject.toml&policy=hold-zero',
    '?scenario=core&policy=hold-zero&' + 'x' * 120,              # bounded query
])
def test_only_closed_registry_keys_are_accepted(service, query):
    with pytest.raises(HTTPError) as error:
        urlopen(service + RUN + query)
    assert error.value.code in (400, 414)
    if error.value.code == 400:
        assert set(json.loads(error.value.read())) == {'error'}


def test_rejected_parameters_are_not_reflected_back(service):
    with pytest.raises(HTTPError) as error:
        urlopen(service + RUN + '?scenario=%3Cscript%3E&policy=hold-zero')
    assert error.value.code == 400
    assert 'script' not in json.loads(error.value.read())['error']


@pytest.mark.parametrize('query', [
    '?scenario=core&policy=hold-zero&REFLECT_ME',  # strict parsing names the bad field
    '?REFLECT_ME',
    '?REFLECT_ME=1&scenario=core&policy=hold-zero',
    '?scenario=core&policy=hold-zero&REFLECT_ME&also=2',
])
def test_malformed_query_never_reflects_the_rejected_field(service, query):
    """A field the strict parser refuses must not reach the response body.

    `parse_qs(strict_parsing=True)` raises `bad query field: '<value>'`, so sending that
    exception's text back would echo caller-controlled bytes into the error document.
    """
    with pytest.raises(HTTPError) as error:
        urlopen(service + RUN + query)
    assert error.value.code == 400
    body = error.value.read()
    assert b'REFLECT_ME' not in body
    assert set(json.loads(body)) == {'error'}


@pytest.mark.parametrize('headers', [{'Host': 'evil.example'}, {'Origin': 'http://evil.example'}])
@pytest.mark.parametrize('target', [REGISTRY, RUN + '?scenario=core&policy=hold-zero'])
def test_non_loopback_authority_is_refused(service, target, headers):
    with pytest.raises(HTTPError) as error:
        urlopen(Request(service + target, headers=headers))
    assert error.value.code == 403


@pytest.mark.parametrize('target', [
    '/api/v1/check', '/api/v1/check/', '/api/v1/check/run/report.json',
    '/api/v1/check/../../pyproject.toml', '/src/almanac/cli.py', '/out/current.json',
])
def test_no_repository_file_is_reachable(service, target):
    with pytest.raises(HTTPError) as error:
        urlopen(service + target)
    assert error.value.code in (400, 404)


def test_unavailable_evidence_is_an_operational_error_not_a_verdict(service, monkeypatch):
    def unavailable():
        raise FileNotFoundError('missing raw')
    monkeypatch.setattr(geofleet, 'load_source', unavailable)
    with pytest.raises(HTTPError) as error:
        urlopen(service + RUN + '?scenario=core&policy=conservative-share')
    assert error.value.code == 503
    body = json.loads(error.value.read())
    assert body['schema'] == cli.ERROR_SCHEMA
    assert body['verdict'] == 'operational-error'
    assert body['exit_code'] == cli.OPERATIONAL_EXIT
    assert body['error_type'] == 'FileNotFoundError'
    assert 'report' not in body and 'measured' not in body


def test_check_run_uses_the_shared_run_slot(service, monkeypatch):
    started, release = threading.Event(), threading.Event()
    original = geofleet_server.evaluate

    def blocking(scenario_key, policy_key):
        started.set()
        assert release.wait(5)
        return original(scenario_key, policy_key)
    monkeypatch.setattr(geofleet_server, 'evaluate', blocking)
    try:
        with ThreadPoolExecutor(2) as pool:
            first = pool.submit(urlopen, service + RUN + '?scenario=core&policy=hold-zero')
            assert started.wait(3)
            with pytest.raises(HTTPError) as error:
                urlopen(service + '/api/v1/geofleet/run?area=core')
            assert error.value.code == 503
            release.set()
            with first.result(timeout=10) as response:
                assert response.status == 200
    finally:
        release.set()


def test_browser_section_uses_these_routes_only():
    text = (ROOT / 'demo/geofleet.html').read_text()
    assert 'Offline controller checks' in text
    assert REGISTRY in text and RUN in text
    assert 'Test a controller change' not in text  # the honest-naming regression
    for token in ['id="check-verdict"', 'id="check-provenance"', 'id="check-json"', 'id="check-html"',
                  'role="alert"', 'AbortSignal.timeout(15000)']:
        assert token in text
    # The downloads reuse the one envelope; the page never re-runs to build a file.
    assert 'report_json' in text and 'report_html' in text
    assert text.count(RUN) == 1


def test_browser_section_invalidates_and_locks_on_selection_change():
    """A changed selection may not leave the previous verdict, provenance or downloads on screen.

    There is no real-browser harness in this repository, so the wiring is pinned textually:
    both selectors clear through one function that drops the envelope the downloads read and
    bumps a generation token, a run locks both selectors, and every outcome restores them.
    """
    text = (ROOT / 'demo/geofleet.html').read_text()
    assert "for(const id of ['check-scenario','check-policy'])$(id).addEventListener('change',()=>clearCheck(" in text
    clear = text.split('function clearCheck(status){')[1].split('\n')[0]
    for dropped in ['checkGeneration++', 'checkEnvelope=null', "$('check-result').hidden=true",
                    "$('check-verdict').removeAttribute('data-verdict')",
                    "$('check-findings').replaceChildren()", "$('check-provenance').replaceChildren()",
                    "$('check-download-status').textContent=''"]:
        assert dropped in clear, dropped
    lock = text.split('function lockCheck(busy){')[1].split('\n')[0]
    assert "['check-scenario','check-policy','check-run']" in lock and 'disabled=busy' in lock
    assert 'clearCheck(`Evaluating' in text and 'lockCheck(true)' in text
    assert 'finally{lockCheck(false)}' in text  # restored on success, failure and abort alike
    assert "$('check-run').disabled=true" not in text  # the button is no longer toggled by hand
    # A late answer, or one about another pair, is discarded instead of displayed.
    assert text.count('if(generation!==checkGeneration)return') == 2
    assert 'envelope.report.scenario.key!==scenario||envelope.report.policy.key!==policy' in text
    assert 'checkEnvelope=envelope;renderCheck()' in text


def test_report_carries_the_keys_the_page_guard_compares():
    """The stale-answer guard reads report.scenario.key and report.policy.key, so they exist."""
    for scenario, policy, _, _ in VERDICTS:
        report = cli.evaluate(scenario, policy)
        assert report['scenario']['key'] == scenario
        assert report['policy']['key'] == policy


def test_new_check_section_separates_with_colons_not_em_dashes():
    text = (ROOT / 'demo/geofleet.html').read_text()
    section = text.split('<section id="controller-check"')[1].split('</section>')[0]
    assert 'Offline controller checks' in section
    assert '—' not in section
    for label in ["(exit ${rep.exit_code}): ${rep.scenario.label}", '${p.id} ${p.version}: ${p.description}']:
        assert label in text
