"""Geographic selection is explicit, synthetic and independent of weather severity."""
import importlib.util
import json
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]

def engine():
    assert importlib.util.find_spec('almanac.geofleet'), 'geofleet extension missing'
    from almanac import geofleet
    return geofleet

def test_geometry_order_boundary_empty():
    g = engine()
    area = [-97, 29, -95, 31]
    points = [{'id':'edge', 'lon':-97, 'lat':29}, {'id':'inside','lon':-96,'lat':30}, {'id':'out','lon':-94,'lat':30}]
    assert g.select(points, area) == ['edge','inside']
    assert g.select([], area) == []
    assert g.select(points, [-100,32,-99,33]) == []
    for bad in [[29,-97,31,-95],[-95,29,-97,31],[True,29,-95,31],[float('nan'),29,-95,31]]:
        with pytest.raises(ValueError): g.select(points,bad)
    with pytest.raises(ValueError): g.select([{'id':'x','lon':30,'lat':-96}],area)

@pytest.mark.parametrize('bad', [None, {}, [], {'area':'bogus'}, {'area':True}, {'area':'core','extra':1}])
def test_malformed_request(bad):
    with pytest.raises(ValueError): engine().preview(bad)

def test_source_missing_and_tampered(tmp_path):
    g = engine()
    with pytest.raises((ValueError,FileNotFoundError)): g.load_source(tmp_path)
    for name in ['al022024_best_track.kmz','manifest.json']:
        (tmp_path/name).write_bytes((g.SOURCE_DIR/name).read_bytes())
    p=tmp_path/'al022024_best_track.kmz'; p.write_bytes(p.read_bytes()+b'bad')
    with pytest.raises(ValueError,match='digest'): g.load_source(tmp_path)

def test_source_real_geometry_time_and_receipt():
    g=engine(); s=g.load_source()
    assert s['metadata']['url'].startswith('https://www.nhc.noaa.gov/')
    assert len(s['track']) > 2
    assert all(p['time_utc'].startswith('2024-07-08') or p['time_utc'].startswith('2024-07-09') for p in s['track'])
    assert all(-98 < p['lon'] < -93 and 27 < p['lat'] < 34 for p in s['track'])
    a=g.experiment({'area':'core'}); b=g.experiment({'area':'core'})
    assert a==b
    body=dict(a); h=body.pop('receipt_sha256'); assert g.digest(body)==h
    strategies=a['simulation']['strategies']
    assert strategies['baseline']['initial_state_sha256']==strategies['constrained']['initial_state_sha256']
    assert strategies['baseline']['scenario_sha256']==strategies['constrained']['scenario_sha256']
    assert a['selection']['selected_ids']==a['simulation']['scenario']['faults'][0]['devices']
    wide=g.experiment({'area':'wide'}); empty=g.experiment({'area':'empty'})
    assert 0 < len(a['selection']['selected_ids']) < len(wide['selection']['selected_ids'])
    assert empty['selection']['selected_ids']==[]
    assert empty['simulation']['scenario']['faults']==[]
    assert wide['simulation']['strategies']['baseline']['metrics'] != strategies['baseline']['metrics']
    for r in [a,wide,empty]:
        for run in r['simulation']['strategies'].values():
            assert run['metrics']['reserve_violations']==0
            assert run['metrics']['max_overshoot_kw']==0
    at={r['time_s']:r for r in strategies['constrained']['timeline']}
    assert at[60]['uncertain_upper_kw']>0
    assert at[60]['delivered_kw']>at[60]['lower_kw']
    assert at[90]['uncertain_upper_kw']==0
    assert at[360]['fresh_devices']==100

def test_challenge_all_selected_and_exact_bounds():
    g=engine(); pts=g.positions()
    assert g.select(pts,[-97.1,29.5,-95.3,30.4])==[p['id'] for p in pts]
    r=g.experiment({'area':'all'})
    assert len(r['selection']['selected_ids'])==100
    for strategy in r['simulation']['strategies'].values():
        at={x['time_s']:x for x in strategy['timeline']}
        assert at[60]['delivered_kw']>0
        assert at[90]['delivered_kw']==0
        assert at[90]['state']=='HOLD'
        assert at[360]['fresh_devices']==100
        assert strategy['metrics']['reserve_violations']==0

def test_challenge_source_timestamp_tampering(tmp_path):
    g=engine()
    for name in ['al022024_best_track.kmz','manifest.json']:
        (tmp_path/name).write_bytes((g.SOURCE_DIR/name).read_bytes())
    path=tmp_path/'manifest.json'; meta=json.loads(path.read_text())
    meta['retrieved_end_utc']='2020-01-01T00:00:00+00:00'
    path.write_text(json.dumps(meta))
    with pytest.raises(ValueError,match='capture time'): g.load_source(tmp_path)

def test_challenge_no_stale_selection_during_fetch():
    text=(ROOT/'demo/geofleet.html').read_text()
    before_fetch=text.split('async function load(){',1)[1].split('await fetch',1)[0]
    assert "$('map').replaceChildren()" in before_fetch
    assert "$('selection').textContent='Selection pending'" in before_fetch

def test_challenge_mobile_receipt_hash_wraps():
    text=(ROOT/'demo/geofleet.html').read_text()
    assert '#export-status{overflow-wrap:anywhere}' in text

def test_challenge_late_preview_failure_is_ignored():
    text=(ROOT/'demo/geofleet.html').read_text()
    load=text.split('async function load(){',1)[1].split('function render()',1)[0]
    assert 'catch(e){if(v!==version)return;' in load

def test_ui_contract():
    p=ROOT/'demo/geofleet.html'
    assert p.exists(), 'geographic UI missing'
    text=p.read_text()
    for token in ['id="map"','id="area"','id="run"','id="reset"','id="export"','id="scrub"','not a footprint','Physical grid outage is not modeled','synthetic relative time']:
        assert token in text

def test_rerun_invalidates_export_before_fetch():
    text=(ROOT/'demo/geofleet.html').read_text()
    handler=text.split("$('run').addEventListener('click',async()=>{",1)[1]
    assert 'clearResult();' in handler.split('await fetch',1)[0]
    assert 'catch(e){if(v!==version)return;' in handler
    assert 'finally{if(v===version)' in handler

@pytest.mark.parametrize('metadata', [[], None, {'retrieved_start_utc':1}])
def test_malformed_source_metadata_is_explicit_rejection(tmp_path, metadata):
    (tmp_path/'manifest.json').write_text(json.dumps(metadata))
    with pytest.raises(ValueError,match='source metadata'):
        engine().load_source(tmp_path)
