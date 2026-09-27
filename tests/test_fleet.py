"""Synthetic contract tests: no battery or network adapters."""
import copy
import importlib.util
from pathlib import Path
import pytest


def engine():
    assert importlib.util.find_spec('almanac.fleet') is not None, 'fleet engine not implemented'
    from almanac import fleet
    return fleet


def test_comparison_and_reproducible_receipt():
    f = engine()
    report = f.compare()
    assert report == f.compare()
    body = dict(report); receipt = body.pop('receipt_sha256')
    assert f.digest(body) == receipt
    assert set(report['strategies']) == {'baseline', 'constrained'}
    b, c = (report['strategies'][s] for s in ('baseline', 'constrained'))
    assert c['metrics']['shortfall_kwh'] < b['metrics']['shortfall_kwh']
    assert b['initial_state_sha256'] == c['initial_state_sha256']
    assert b['scenario_sha256'] == c['scenario_sha256']


def test_all_rows_physical_invariants_and_cumulative_accounting():
    f = engine(); report = f.compare()
    for run in report['strategies'].values():
        previous = sum(d['initial_energy_kwh'] for d in report['scenario']['devices'])
        for row in run['timeline']:
            assert row['lower_kw'] <= row['delivered_kw'] + 1e-7 <= row['upper_kw'] + 1e-7
            assert row['delivered_kw'] <= row['target_kw'] + 1e-7
            assert row['energy_remaining_kwh'] <= previous + 1e-7
            previous = row['energy_remaining_kwh']
            for d in row['ground_truth_devices']:
                assert d['reserve_kwh'] - 1e-7 <= d['energy_kwh'] <= d['capacity_kwh']
                assert 0 <= d['average_kw'] <= d['max_kw'] + 1e-7
        initial = sum(d['initial_energy_kwh'] for d in report['scenario']['devices'])
        assert initial - previous == pytest.approx(run['metrics']['delivered_kwh'], abs=1e-7)


def test_loss_is_unknown_not_instant_zero_then_rebalances():
    f = engine(); r = f.compare()['strategies']['constrained']['timeline']
    at = {x['time_s']: x for x in r}
    assert at[60]['state'] == 'UNCERTAIN'
    assert at[60]['uncertain_upper_kw'] > 0
    assert at[60]['delivered_kw'] > at[60]['lower_kw']
    assert at[90]['uncertain_upper_kw'] == 0
    assert 'REBALANCED' in at[90]['reasons']
    assert at[90]['delivered_kw'] == pytest.approx(400)


def test_capacity_all_offline_late_reconnect_recovery():
    f = engine(); at = {x['time_s']: x for x in f.compare()['strategies']['constrained']['timeline']}
    assert at[280]['state'] == 'DEGRADED'
    assert at[340]['state'] == 'HOLD'
    assert at[340]['delivered_kw'] == 0
    assert at[360]['fresh_devices'] == 0
    assert 'STALE_TELEMETRY' in at[360]['reasons']
    assert at[380]['fresh_devices'] == 100
    assert at[380]['delivered_kw'] > at[340]['delivered_kw']


def test_low_energy_reserve_unreachable_target():
    f = engine(); s = f.default_scenario()
    s['target_kw'] = 1000
    for d in s['devices']:
        d['initial_energy_kwh'] = d['reserve_kwh'] + .001
    report = f.compare(s)
    for run in report['strategies'].values():
        assert run['metrics']['shortfall_kwh'] > 0
        assert run['metrics']['delivered_kwh'] == pytest.approx(.1)
        assert run['timeline'][-1]['state'] in ('HOLD', 'DEGRADED')


@pytest.mark.parametrize('dt', [1, 2, 5, 10])
def test_step_convergence_exact_energy(dt):
    f = engine(); a = f.compare(step_s=dt); b = f.compare(step_s=10)
    for strategy in a['strategies']:
        assert a['strategies'][strategy] == b['strategies'][strategy]


def test_repeat_command_does_not_extend_expiry_or_spend_twice():
    f = engine(); d = f.default_scenario()['devices'][0]
    plant = f.Device(d, expiry_s=37)
    command = f.Command('a', 0, f.F(4), 40)
    assert plant.accept(command, 0) == 'ACCEPTED'
    assert plant.accept(command, 20) == 'IDEMPOTENT_NOOP'
    assert plant.expires_s == 37
    assert plant.integrate(0, 40) == f.F(4) * 37 / 3600
    assert plant.accept(command, 41) == 'IDEMPOTENT_NOOP'
    assert plant.integrate(40, 50) == 0
    with pytest.raises(ValueError):
        plant.accept(f.Command('a', 0, f.F(5), 40), 50)


def test_controller_cannot_see_future_or_hidden_plant():
    f = engine(); s = f.default_scenario(); changed = copy.deepcopy(s)
    changed['faults'].append({'start_s': 500, 'end_s': 550, 'kind': 'offline', 'devices': ['B099']})
    a, b = f.compare(s), f.compare(changed)
    for strategy in a['strategies']:
        assert a['strategies'][strategy]['timeline'][:50] == b['strategies'][strategy]['timeline'][:50]
    assert 'faults' not in f.Controller.__init__.__annotations__


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), -1, True, '400'])
def test_invalid_target_rejected(bad):
    f = engine(); s = f.default_scenario(); s['target_kw'] = bad
    with pytest.raises(ValueError): f.compare(s)


def test_challenge_one_replayed_physics_interval_must_not_spend_twice():
    f = engine(); plant = f.Device(f.default_scenario()['devices'][0], 37)
    plant.accept(f.Command('a', 0, f.F(4), 40), 0)
    plant.integrate(0, 10)
    energy = plant.energy
    with pytest.raises(ValueError): plant.integrate(0, 10)
    assert plant.energy == energy
    with pytest.raises(ValueError): plant.integrate(20, 10)
    with pytest.raises(ValueError): plant.integrate(20, 30)


def test_challenge_two_future_command_cannot_rewrite_unintegrated_past():
    f = engine(); plant = f.Device(f.default_scenario()['devices'][0], 37)
    plant.accept(f.Command('a', 0, f.F(4), 40), 0)
    with pytest.raises(ValueError):
        plant.accept(f.Command('b', 20, f.F(5), 60), 20)
    assert plant.integrate(0, 10) == f.F(4) * 10 / 3600
    with pytest.raises(ValueError):
        plant.accept(f.Command('b', 5, f.F(5), 45), 5)
    plant.accept(f.Command('b', 10, f.F(5), 50), 10)
    assert plant.integrate(10, 20) == f.F(5) * 10 / 3600


def test_delayed_expiry_and_stale_reconnect_do_not_create_credit():
    f = engine(); report = f.compare()
    for run in report['strategies'].values():
        at = {r['time_s']: r for r in run['timeline']}
        offline = at[80]['ground_truth_devices'][:10]
        assert any(d['average_kw'] > 0 for d in offline), 'some plants stop at the maximum assumed lease'
        assert any(d['average_kw'] == 0 for d in offline), 'other plants have stopped but controller cannot know'
        assert at[80]['uncertain_upper_kw'] > 0
        assert not any(a['time_s'] == 180 and a['device'] in {f'B{i:03d}' for i in range(10)} for a in run['actions'])
        assert all(p['sampled_s'] < 180 for p in at[180]['controller_visible'] if p['id'] in {f'B{i:03d}' for i in range(10)})


def test_empty_and_single_device_domains():
    f = engine(); s = f.default_scenario(); s['devices'] = []
    with pytest.raises(ValueError): f.compare(s)
    s = f.default_scenario(); s['devices'] = s['devices'][:1]; s['faults'] = []; s['target_kw'] = 4
    result = f.compare(s)
    baseline = result['strategies']['baseline']
    constrained = result['strategies']['constrained']
    # Policy identity differs; every physical and observational result must match.
    assert baseline['controller'] == {'id': 'baseline', 'version': '1'}
    assert constrained['controller'] == {'id': 'constrained', 'version': '1'}
    for key in baseline.keys() - {'controller', 'events'}:
        assert baseline[key] == constrained[key]
    assert [{k: v for k, v in e.items() if k != 'controller'} for e in baseline['events']] == [
        {k: v for k, v in e.items() if k != 'controller'} for e in constrained['events']]
    assert all(e['controller'] == r['controller'] for r in (baseline, constrained) for e in r['events'])


def test_independent_seeded_fault_matrix():
    import random
    f = engine(); rng = random.Random(842)
    for case in range(12):
        s = f.default_scenario(); s['seed'] = case; s['duration_s'] = 120
        s['devices'] = s['devices'][:rng.randint(1, 12)]
        s['target_kw'] = rng.randint(0, 80)
        for d in s['devices']: d['initial_energy_kwh'] = rng.choice([1, 1.001, 1.1, 2])
        ids = [d['id'] for d in s['devices']]
        s['faults'] = [{'start_s': 20, 'end_s': 60, 'kind': 'offline', 'devices': ids[:max(1,len(ids)//2)]},
                       {'start_s': 60, 'end_s': 80, 'kind': 'stale', 'devices': ids}]
        r = f.compare(s)
        for run in r['strategies'].values():
            assert run['metrics']['max_overshoot_kw'] == 0
            assert run['metrics']['reserve_violations'] == 0
            assert run['metrics']['delivered_kwh'] + run['metrics']['shortfall_kwh'] == pytest.approx(run['metrics']['requested_kwh'], abs=2e-9)


def test_adjustable_timeout_assumptions_remain_bounded():
    f = engine(); s = f.default_scenario()
    s['lease_policy'] = {'local_expiry_s': [20, 60], 'lease_bound_s': 60}
    report = f.compare(s)
    at = {r['time_s']:r for r in report['strategies']['constrained']['timeline']}
    assert at[100]['uncertain_upper_kw'] > 0
    assert at[110]['uncertain_upper_kw'] == 0
    assert report['assumptions']['controller_lease_bound_s'] == 60
    s['lease_policy']['lease_bound_s'] = 40
    with pytest.raises(ValueError): f.compare(s)


def test_ui_contract():
    root = Path(__file__).resolve().parents[1]
    page = root / 'demo/fleet.html'
    assert page.exists(), 'fleet comparison UI missing'
    content = page.read_text()
    for token in ['id="scrub"', 'id="export"', 'id="comparison"', 'id="audit"', 'id="run"', '<summary>Device and telemetry detail</summary>', '<summary>Inspect current control receipt</summary>', 'AI is not in the control loop']:
        assert token in content
