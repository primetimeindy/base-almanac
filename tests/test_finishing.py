"""Regressions for honest result interpretation and read-only local HTTP."""
import json
from fractions import Fraction as F
from pathlib import Path
import threading
from urllib.request import urlopen, Request
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer
import pytest
from almanac import geofleet
from almanac.fleet import compare

ROOT = Path(__file__).resolve().parents[1]

@pytest.mark.parametrize('area', ['core', 'empty', 'all'])
def test_no_fault_counterfactual_is_recomputed_not_attributed(area):
    report = geofleet.experiment({'area': area})
    assert 'comparison_context' in report
    context = report['comparison_context']
    s = json.loads(json.dumps(report['simulation']['scenario']))
    s['faults'] = []
    expected = compare(s)
    assert context['no_fault'] == expected
    def delta(run):
        rows = run['strategies']
        return F(rows['baseline']['metrics']['exact_shortfall_kwh']) - F(rows['constrained']['metrics']['exact_shortfall_kwh'])
    assert F(context['exact_no_fault_difference_kwh']) == delta(expected)
    assert F(context['exact_fault_run_difference_kwh']) == delta(report['simulation'])
    assert F(context['exact_difference_of_differences_kwh']) == delta(report['simulation']) - delta(expected)
    if area == 'empty': assert context['difference_of_differences_kwh'] == 0
    assert 'not an isolated' in context['interpretation']


def test_plain_ui_visible_assumptions_and_no_fixed_port_link():
    text = (ROOT/'demo/geofleet.html').read_text()
    assert 'Compare two example battery policies' in text
    assert 'Test a controller change' not in text
    assert '8766' not in text
    visible = text.split('<details>')[0]
    for token in ['Guaranteed local stops', 'instant acknowledgements', 'noiseless', 'local plant', 'id="decomposition"']:
        assert token in visible
    assert 'Historical hazard geometry' not in text
    assert 'class="diagram-scroll"' in text
    assert '#map{min-width:720px}' in text
    assert '#chart{min-width:1000px}' in text
    assert 'AbortSignal.timeout(15000)' in text
    assert "$('run').textContent='Running...'" in text


def test_read_only_contract_endpoint_is_used_by_frontend():
    from almanac.geofleet_server import Handler
    service = ThreadingHTTPServer(('127.0.0.1',0), Handler)
    worker = threading.Thread(target=service.serve_forever, daemon=True); worker.start()
    try:
        with urlopen(f'http://127.0.0.1:{service.server_port}/api/v1/contract') as r:
            data = json.load(r)
        assert data['schema'] == 'almanac.integration.v1'
        assert data['http_accepts_policy_code'] is False
        assert data['observation_schema'] == 'almanac.observation.v1'
        assert '/api/v1/contract' in (ROOT/'demo/geofleet.html').read_text()
        for name in ('almanac-mark-dark.svg', 'almanac-mark-light.svg', 'almanac-wordmark-dark.svg', 'almanac-wordmark-light.svg', 'favicon.svg'):
            with urlopen(f'http://127.0.0.1:{service.server_port}/assets/{name}') as asset:
                assert asset.headers['Content-Type'] == 'image/svg+xml'
                assert asset.read() == (ROOT/'demo/assets'/name).read_bytes()
        assert 'almanac-wordmark-dark.svg' in (ROOT/'demo/geofleet.html').read_text()
        assert 'almanac-wordmark-light.svg' in (ROOT/'README.md').read_text()
    finally:
        service.shutdown(); service.server_close(); worker.join()
