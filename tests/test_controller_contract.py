"""Trusted local adapters receive observations, never scenario/plant handles."""
import importlib.util
import pytest
from almanac import fleet


def contract():
    assert importlib.util.find_spec('almanac.controller_contract'), 'local adapter contract missing'
    from almanac import controller_contract
    return controller_contract


class ZeroPolicy:
    policy_id = 'test-zero'
    policy_version = '1.0.0'
    def decide(self, observation):
        assert set(observation) == {'schema', 'time_s', 'control_period_s', 'lease_bound_s', 'target_kw', 'unknown_upper_kw', 'dispatch_budget_kw', 'devices'}
        assert all(set(d) == {'id', 'max_kw', 'reserve_kwh', 'sampled_s', 'energy_kwh', 'dispatch_cap_kw'} for d in observation['devices'])
        return {'schema': 'almanac.actions.v1', 'powers_kw': {d['id']: 0 for d in observation['devices']}}


def test_third_policy_is_deterministic_and_identified():
    c = contract()
    s = fleet.default_scenario()
    a = c.compare_policy(s, ZeroPolicy())
    b = c.compare_policy(s, ZeroPolicy())
    assert a == b
    assert a['policy'] == {'id': 'test-zero', 'version': '1.0.0', 'contract': 'almanac.observation.v1'}
    assert a['candidate']['metrics']['delivered_kwh'] == 0
    body = dict(a); checksum = body.pop('receipt_sha256')
    assert fleet.digest(body) == checksum
    assert a['reference']['strategies']['baseline'] == fleet.compare(s)['strategies']['baseline']


@pytest.mark.parametrize('bad', [None, [], {}, {'schema':'almanac.actions.v2','powers_kw':{}}, {'schema':'almanac.actions.v1','powers_kw':{'UNKNOWN':0}}, {'schema':'almanac.actions.v1','powers_kw':{},'extra':True}])
def test_invalid_envelopes_abort(bad):
    c = contract()
    class Bad(ZeroPolicy):
        def decide(self, obs): return bad
    with pytest.raises(ValueError): c.compare_policy(fleet.default_scenario(), Bad())


@pytest.mark.parametrize('bad', [True, '1', -1, float('nan'), float('inf'), 6, 1e300])
def test_invalid_power_aborts(bad):
    c = contract()
    class Bad(ZeroPolicy):
        def decide(self, obs):
            action = super().decide(obs)
            action['powers_kw'][obs['devices'][0]['id']] = bad
            return action
    with pytest.raises(ValueError): c.compare_policy(fleet.default_scenario(), Bad())


def test_target_energy_and_missing_fresh_command_abort():
    c = contract()
    for mode, message in [('target', 'target'), ('energy', 'energy'), ('missing', 'identifiers')]:
        class Bad(ZeroPolicy):
            def decide(self, obs):
                action = super().decide(obs)
                if mode == 'target': action['powers_kw'] = {d['id']: 4.5 for d in obs['devices']}
                if mode == 'energy': action['powers_kw']['B099'] = 5
                if mode == 'missing': action['powers_kw'].pop('B000')
                return action
        s = fleet.default_scenario()
        if mode == 'energy': s['devices'][-1]['initial_energy_kwh'] = 1.001
        with pytest.raises(ValueError, match=message): c.compare_policy(s, Bad())


def test_example_adapter_runs_full_fault_scenario():
    c = contract()
    from examples.priority_policy import Policy
    a = c.compare_policy(fleet.default_scenario(), Policy())
    assert a['policy']['id'] == 'priority-order'
    assert a['candidate']['metrics']['delivered_kwh'] > 0
    assert a['candidate']['metrics']['reserve_violations'] == 0
    assert a['candidate']['metrics']['max_overshoot_kw'] == 0
    assert a == c.compare_policy(fleet.default_scenario(), Policy())


def test_observation_mutation_does_not_weaken_validation():
    c = contract()
    class Bad(ZeroPolicy):
        def decide(self, obs):
            action = super().decide(obs)
            obs['target_kw'] = 1e12
            obs['devices'][0]['dispatch_cap_kw'] = 1e12
            action['powers_kw'][obs['devices'][0]['id']] = 100
            return action
    with pytest.raises(ValueError): c.compare_policy(fleet.default_scenario(), Bad())
