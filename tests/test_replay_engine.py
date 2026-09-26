"""Safety contract first, synthetic unit fixtures explicitly separate from history."""
import copy
import hashlib
import json
from pathlib import Path
import pytest
from almanac.replay import replay, canonical, validate_bundle

ROOT = Path(__file__).resolve().parents[1]

@pytest.fixture
def bundle():
    return json.loads((ROOT / 'demo/data/uri.json').read_text())

def test_real_bundle(bundle):
    validate_bundle(bundle)
    assert len(bundle['observed']) == 32
    assert bundle['observed'][0]['location'] == 'LZ_HOUSTON'
    assert bundle['observed'][0]['point_type'] == 'LZEW'

def test_determinism_reset_export(bundle):
    a = replay(bundle, [])
    assert canonical(a) == canonical(replay(bundle, []))
    assert replay(bundle, ['next', 'missing', 'reset']) == a
    receipt = a.pop('receipt_sha256')
    assert hashlib.sha256(canonical(a).encode()).hexdigest() == receipt

@pytest.mark.parametrize('failure,reason', [('missing','MISSING_FEED'), ('stale','STALE_FEED'), ('unavailable','RESOURCE_UNAVAILABLE')])
def test_failure_recovery_requires_new_approval(bundle, failure, reason):
    a = replay(bundle, [failure, 'approve'])
    assert a['decision']['status'] == 'HOLD'
    assert reason in a['decision']['reasons']
    assert a['simulated_actions'] == []
    restored = replay(bundle, [failure, 'approve', 'restore'])
    assert restored['decision']['status'] == 'READY'
    assert restored['simulated_actions'] == []
    assert len(replay(bundle, [failure, 'approve', 'restore', 'approve'])['simulated_actions']) == 1

def test_idempotency_and_invalidation(bundle):
    assert len(replay(bundle, ['approve', 'approve'])['simulated_actions']) == 1
    assert replay(bundle, ['approve','missing'])['decision']['status'] == 'HOLD'
    assert replay(bundle, ['approve','missing','restore'])['decision']['status'] == 'READY'
    assert len(replay(bundle, ['approve','missing','restore','approve'])['simulated_actions']) == 1

@pytest.mark.parametrize('mutation', ['empty','duplicate','naive','nan','unit','location','type','reversed','future','gap'])
def test_reject_invalid_evidence(bundle, mutation):
    x=copy.deepcopy(bundle); rows=x['observed']
    if mutation=='empty': x['observed']=[]
    elif mutation=='duplicate': rows[1]=rows[0].copy()
    elif mutation=='naive': rows[0]['interval_start']='2021-02-14T16:00:00'
    elif mutation=='nan': rows[0]['spp_usd_per_mwh']='NaN'
    elif mutation=='unit': rows[0]['unit']='USD/kWh'
    elif mutation=='location': rows[0]['location']='LZ_NORTH'
    elif mutation=='type': rows[0]['point_type']='LZ'
    elif mutation=='reversed': x['observed']=list(reversed(rows))
    elif mutation=='future': rows[0]['available_at']='2099-01-01T00:00:00+00:00'
    elif mutation=='gap': del rows[1]
    with pytest.raises(ValueError): replay(x, [])

def test_reject_bad_commands(bundle):
    for commands in (['dispatch'], 'next', [None], ['next']*513):
        with pytest.raises(ValueError): replay(bundle, commands)

def test_no_future_evidence_and_boundary(bundle):
    a=replay(bundle, [])
    assert a['decision']['age_seconds']==0
    b=replay(bundle, ['tick'])
    assert b['decision']['age_seconds']==900
    assert b['decision']['status']=='READY'
    c=replay(bundle, ['tick','tick'])
    assert c['decision']['status']=='HOLD'
    assert len(replay(bundle,['next']*100)['visible_observed'])==32

def test_reject_boolean_price(bundle):
    bundle['observed'][0]['spp_usd_per_mwh'] = True
    with pytest.raises(ValueError): replay(bundle, [])

def test_result_is_detached_from_evidence(bundle):
    original=copy.deepcopy(bundle)
    result=replay(bundle, [])
    result['observed']['spp_usd_per_mwh']='0.00'
    result['source']['publisher']='Altered'
    assert bundle==original

def test_restore_never_rewinds_or_invents_fresh_evidence(bundle):
    stale=replay(bundle,['stale'])
    recovered=replay(bundle,['stale','restore'])
    assert recovered['replay_at_utc']==stale['replay_at_utc']
    assert recovered['observed']['interval_start']!=stale['observed']['interval_start']
    exhausted=replay(bundle,['next']*32+['stale','restore','approve'])
    assert exhausted['decision']['status']=='HOLD'
    assert exhausted['simulated_actions']==[]

def test_audit_hash_chain(bundle):
    previous=None
    for row in replay(bundle,['missing','approve','restore','approve'])['audit']:
        row=copy.deepcopy(row); checksum=row.pop('hash')
        assert row['previous_hash']==previous
        assert checksum==hashlib.sha256(canonical(row).encode()).hexdigest()
        previous=checksum

def test_capacity_and_low_stress_hold(bundle,monkeypatch):
    from almanac import replay as module
    monkeypatch.setitem(module.ASSET,'capacity_kw',9)
    assert 'INSUFFICIENT_CAPACITY' in replay(bundle,[])['decision']['reasons']
    monkeypatch.setitem(module.ASSET,'capacity_kw',12)
    monkeypatch.setitem(module.ASSET,'energy_kwh','2.49')
    assert replay(bundle,['approve'])['simulated_actions']==[]
    monkeypatch.setitem(module.ASSET,'energy_kwh','5.00')
    bundle['observed'][0]['spp_usd_per_mwh']='999.99'
    assert replay(bundle,['approve'])['decision']['status']=='MONITOR'
    assert replay(bundle,['approve'])['simulated_actions']==[]

def test_exhaustive_short_failure_sequences(bundle):
    import itertools
    for sequence in itertools.product(['missing','stale','unavailable','restore','approve'],repeat=3):
        r=replay(bundle,list(sequence))
        for event in r['audit']:
            if event['outcome']=='SIMULATION_RECORDED':
                assert event['command']=='approve'
                assert event['decision']['status']=='READY'
                assert not event['decision']['reasons']
        assert len({a['id'] for a in r['simulated_actions']})==len(r['simulated_actions'])

def test_ui_contract():
    text=(ROOT/'demo/index.html').read_text()
    for hook in ('id="decision"','id="approve"','id="download"','id="missing"','id="stale"','id="unavailable"','id="restore"','id="audit"'):
        assert hook in text
