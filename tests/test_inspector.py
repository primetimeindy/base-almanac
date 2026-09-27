from pathlib import Path
from almanac.fleet import default_scenario, run
from almanac.inspector import annotate
import pytest


def test_ui_readable_fields_and_energy_fill():
    html = Path('demo/geofleet.html').read_text()
    for hook in ('<dl id="device-state"', 'Last report', 'Last reported energy',
                 'Reported power', 'Command expiry', 'data-energy-fill',
                 'Connected / fresh', 'Connected / stale', 'Disconnected / output uncertain',
                 'Disconnected / expiry passed'):
        assert hook in html


def test_map_has_battery_detail_scale_for_nonoverlapping_glyphs():
    html = Path('demo/geofleet.html').read_text()
    assert 'id="map-view"' in html
    assert 'Battery detail' in html
    assert "$('map-view').value='batteries'" in html


def test_ui_clear_removes_stale_map_and_export():
    html = Path('demo/geofleet.html').read_text()
    clear = html.split('function clearResult(){')[1].split('async function load')[0]
    assert "$('map').replaceChildren()" in clear
    assert "$('export').disabled=true" in clear


def test_expiry_is_its_own_event_even_with_zero_command():
    s = default_scenario()
    s['target_kw'] = 0
    i = s['devices'][0]['id']
    s['faults'] = [dict(kind='offline', start_s=10, end_s=60, devices=[i])]
    result = run(s, 'constrained', 10)
    assert any(e['time_s'] == 40 and i in e['lease_bound_elapsed_ids']
               for e in result['events'])


def test_zero_command_is_not_unknown_output():
    s = default_scenario()
    s['target_kw'] = 0
    i = s['devices'][0]['id']
    s['faults'] = [dict(kind='offline', start_s=10, end_s=60, devices=[i])]
    d = run(s, 'constrained', 10)['timeline'][1]['inspector'][0]
    assert d['reason_code'] == 'ACKNOWLEDGED_ZERO_COMMAND'


def test_annotation_rejects_future_packet():
    rows = [dict(time_s=0, end_s=10, controller_visible=[dict(
        id='a', sampled_s=10, accepted_as_fresh=False, energy_kwh=2)],
        reasons=[], uncertain_upper_kw=0, shortfall_kw=0, target_kw=0)]
    with pytest.raises(ValueError, match='future'):
        annotate(rows, [], ['a'], {'id': 'test', 'version': '1'})


def test_no_packet_does_not_invent_a_reading_or_command():
    s = default_scenario()
    i = s['devices'][0]['id']
    s['faults'] = [dict(kind='offline', start_s=0, end_s=50, devices=[i])]
    result = run(s, 'constrained', 10)
    d = result['timeline'][0]['inspector'][0]
    assert d['reported_energy_kwh'] is None
    assert d['sampled_s'] is None
    assert d['reported_power_kw'] is None
    assert d['last_acknowledged_command'] is None
    assert d['reason_code'] == 'NO_ACKNOWLEDGED_COMMAND'



def sample():
    s = default_scenario()
    i = s['devices'][0]['id']
    s['faults'] = [dict(kind='offline', start_s=10, end_s=50, devices=[i]),
                   dict(kind='stale', start_s=50, end_s=70, devices=[i])]
    return s, i, run(s, 'constrained', 10)


def test_inspector_age_expiry_and_reconnect():
    s, i, result = sample()
    def at(t):
        return next(d for d in result['timeline'][t // 10]['inspector'] if d['id'] == i)
    assert at(10)['report_age_s'] == 10
    assert at(30)['reason_code'] == 'WAITING_FOR_LEASE_BOUND'
    assert at(40)['reason_code'] == 'LEASE_BOUND_ELAPSED'
    assert at(50)['connectivity'] == 'STALE_PACKET'
    assert at(50)['sampled_s'] == 0
    assert at(70)['connectivity'] == 'FRESH_PACKET'
    assert at(70)['report_age_s'] == 0
    assert at(70)['last_acknowledged_command']['issued_s'] == 70
    assert at(10)['reported_power_kw'] is None
    assert result['controller'] == {'id': 'constrained', 'version': '1'}
    assert result == run(s, 'constrained', 10)


def test_events_reference_actual_rows_and_actions():
    _, _, result = sample()
    for event in result['events']:
        row = result['timeline'][event['time_s'] // 10]
        assert event['reason_codes'] == row['reasons']
        assert event['shortfall_kw'] == row['shortfall_kw']
        assert event['controller'] == result['controller']
    for row in result['timeline']:
        for d in row['inspector']:
            assert d['sampled_s'] is None or d['sampled_s'] <= row['time_s']
            ack = d['last_acknowledged_command']
            if ack:
                assert any(a['command_id'] == ack['command_id'] and a['device'] == d['id']
                           for a in result['actions'])
            assert 'ground_truth' not in d


def test_inspector_ui_uses_receipt():
    html = Path('demo/geofleet.html').read_text()
    assert 'Device &amp; event inspector' in html
    assert 'row.inspector.find' in html
    assert 'inspect-device' in html
    assert "e.key==='Enter'" in html
