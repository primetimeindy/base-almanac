"""Acceptance tests for the experimental command/telemetry uncertainty simulator.

Small fixtures only, so the whole file stays fast and every expectation is checkable by
hand. These tests are about *knowledge*: what a controller may and may not conclude when
commands, acknowledgements and telemetry can each be lost independently.
"""
from fractions import Fraction

import pytest

from almanac.uncertainty import (GRID_CONNECTED, ISLANDED, NON_OPERATIONAL, REPORTING,
                                 UNREACHABLE, Command, DeviceKnowledge, ScenarioError,
                                 _apply_ack, _conservative_decision, _ingest_packet,
                                 build_scenario, simulate)

BASE = {
    'scenario_id': 'test-v1', 'duration_s': 120, 'control_period_s': 20,
    'telemetry_period_s': 10, 'inactivity_timeout_s': 30, 'command_ttl_s': 60,
    'target_kw': 10, 'seed': 1,
    'devices': [{'device_id': 'd1', 'capacity_kw': 8, 'energy_kwh': 5, 'reserve_kwh': 1},
                {'device_id': 'd2', 'capacity_kw': 8, 'energy_kwh': 5, 'reserve_kwh': 1}],
    'faults': [],
}


def scenario(**overrides):
    spec = dict(BASE)
    spec.update(overrides)
    return build_scenario(spec)


def at(result, time_s):
    return next(entry for entry in result['trace'] if entry['time_s'] == time_s)


def command(setpoint_kw, *, sequence, issued_s=0, ttl_s=60, device_id='d1'):
    return Command(command_id=f'c{sequence}', device_id=device_id,
                   setpoint_kw=Fraction(setpoint_kw), issued_s=issued_s,
                   expires_s=issued_s + ttl_s, sequence=sequence)


# --- clocks and expiry -------------------------------------------------------------

def test_control_telemetry_and_timeout_clocks_are_independent():
    result = simulate(scenario(control_period_s=20, telemetry_period_s=10,
                               inactivity_timeout_s=30, command_ttl_s=60))
    decided = [e['time_s'] for e in result['trace'] if e['decision']]
    assert decided and all(t % 20 == 0 for t in decided)
    # Telemetry runs on its own clock: a sample lands on every 10 s boundary.
    sampled = [e['time_s'] for e in result['trace']
               if e['observed']['d1']['last_sample_s'] == e['time_s']]
    assert sampled == [t for t in range(0, 120, 10)]


def test_absolute_ttl_expires_exactly_on_its_boundary_second():
    issued = command(5, sequence=1, issued_s=10, ttl_s=30)
    assert issued.expires_s == 40
    assert issued.expired_at(39) is False
    assert issued.expired_at(40) is True  # the boundary second is expired, not still valid
    assert issued.expired_at(41) is True


def test_a_duplicate_command_never_renews_the_ttl():
    original = command(5, sequence=1, issued_s=0, ttl_s=30)
    duplicate = command(5, sequence=1, issued_s=0, ttl_s=30)
    know = DeviceKnowledge('d1', unconfirmed=[original])
    know.unconfirmed.append(duplicate)
    assert {c.expires_s for c in know.unconfirmed} == {30}
    know.drop_expired(30)
    assert know.unconfirmed == []  # the redelivered copy expires with the original


# --- what the controller may conclude ----------------------------------------------

def test_a_pending_replacement_is_not_counted_twice_on_one_device():
    know = DeviceKnowledge('d1', confirmed=command(3, sequence=1), unconfirmed=[command(4, sequence=2)])
    # Either the old 3 kW or the new 4 kW is running, never both: the bound is the max.
    assert know.bound_kw(0) == Fraction(4)
    assert know.bound_kw(0) != Fraction(7)
    assert len(know.possible_active(0)) == 2


def test_an_unconfirmed_stop_cannot_free_capacity():
    know = DeviceKnowledge('d1', confirmed=command(5, sequence=1), unconfirmed=[command(0, sequence=2)])
    # The stop may have been lost, so the device may still be at 5 kW.
    assert know.bound_kw(0) == Fraction(5)
    assert know.pending(0) is True


def test_a_reporting_device_with_a_pending_command_is_still_uncertain():
    know = DeviceKnowledge('d1', last_sample_s=0, last_power_kw=Fraction(3),
                           confirmed=command(3, sequence=1), unconfirmed=[command(6, sequence=2)])
    assert know.freshness(0, 10, 30) == REPORTING
    # Telemetry is not an acknowledgement: a fresh sample does not resolve which command runs.
    assert know.bound_kw(0) == Fraction(6)
    assert know.pending(0) is True


def test_the_worst_case_bound_sums_across_devices_but_maximises_within_one():
    devices = {
        'd1': DeviceKnowledge('d1', confirmed=command(3, sequence=1),
                              unconfirmed=[command(4, sequence=3)]),
        'd2': DeviceKnowledge('d2', confirmed=command(2, sequence=2, device_id='d2'),
                              unconfirmed=[command(5, sequence=4, device_id='d2')]),
    }
    assert sum(k.bound_kw(0) for k in devices.values()) == Fraction(9)  # 4 + 5, not 3+4+2+5


def test_a_delayed_ack_cannot_override_knowledge_of_a_newer_command():
    """Driven through the real ACK handler, not by hand-editing the knowledge state."""
    know = DeviceKnowledge('d1', unconfirmed=[command(2, sequence=1), command(7, sequence=2)])
    # The ACK for the older command arrives after the newer one was already issued.
    assert _apply_ack(know, know.unconfirmed[0], 0) == 'confirmed'
    assert know.confirmed.sequence == 1
    assert [c.sequence for c in know.unconfirmed] == [2]  # the newer command is still out there
    assert know.bound_kw(0) == Fraction(7)  # the newer command still might be running
    assert know.pending(0) is True


def test_an_acknowledged_stop_prunes_the_superseded_command_from_the_bound():
    """An ACK proves the device took that command, so no older one can still be running.

    There is no newer command here, which is exactly the case that used to leave an
    impossible old positive bound standing until its TTL.
    """
    stop = command(0, sequence=2)
    know = DeviceKnowledge('d1', unconfirmed=[command(5, sequence=1), stop])
    assert know.bound_kw(0) == Fraction(5)  # before the ACK the stop frees nothing
    assert _apply_ack(know, stop, 0) == 'confirmed'
    assert know.confirmed.sequence == 2
    assert know.unconfirmed == []  # sequence 1 is superseded, not left behind
    assert know.bound_kw(0) == Fraction(0)  # the capacity is genuinely free now
    assert know.pending(0) is False


def test_an_acknowledged_command_supersedes_older_ones_but_not_newer_ones():
    know = DeviceKnowledge('d1', confirmed=command(9, sequence=1),
                           unconfirmed=[command(4, sequence=2), command(3, sequence=3)])
    middle = know.unconfirmed[0]
    assert _apply_ack(know, middle, 0) == 'confirmed'
    assert know.confirmed.sequence == 2  # the 9 kW command cannot still be running
    assert [c.sequence for c in know.unconfirmed] == [3]
    assert know.bound_kw(0) == Fraction(4)  # max(4, 3), never 9 and never a sum


def test_an_ack_of_an_older_command_never_resurrects_it_over_a_newer_confirmation():
    older, stop = command(7, sequence=1), command(0, sequence=2)
    know = DeviceKnowledge('d1', confirmed=stop)
    assert _apply_ack(know, older, 0) == 'superseded'
    assert know.confirmed is stop
    assert know.bound_kw(0) == Fraction(0)  # the late ACK brings back no capacity claim


def test_an_ack_that_arrives_after_its_own_ttl_confirms_nothing():
    know = DeviceKnowledge('d1', unconfirmed=[command(5, sequence=1, issued_s=0, ttl_s=10),
                                              command(3, sequence=2, issued_s=0, ttl_s=60)])
    assert _apply_ack(know, know.unconfirmed[0], 20) == 'expired'
    assert know.confirmed is None  # an expired command is not a current state
    assert [c.sequence for c in know.unconfirmed] == [2]
    assert know.bound_kw(20) == Fraction(3)


def test_an_expired_command_drops_out_of_the_possible_set():
    know = DeviceKnowledge('d1', confirmed=command(5, sequence=1, issued_s=0, ttl_s=30))
    assert know.bound_kw(29) == Fraction(5)
    know.drop_expired(30)
    assert know.bound_kw(30) == Fraction(0)
    assert know.possible_active(30) == []


# --- delivery failures in the running simulation -----------------------------------

def test_a_lost_command_leaves_the_previous_command_running_until_its_own_ttl():
    """A replacement is really issued and really lost.

    The built-in conservative controller has no headroom to reissue while a command of its
    own still stands, so the replacement is driven through the internal decision seam. It
    is judged by the same gate: `attempted_gate_violations` stays 0 below.
    """
    def reissue(knowledge, capacities, freshness, now_s, target_kw):
        return {0: {'d1': Fraction(8)}, 20: {'d1': Fraction(6)}}.get(now_s, {})

    result = simulate(scenario(command_ttl_s=100, target_kw=8, drop_commands=[2],
                               devices=[{'device_id': 'd1', 'capacity_kw': 8,
                                         'energy_kwh': 5, 'reserve_kwh': 1}]),
                      decide=reissue)
    assert result['metrics']['commands_issued'] == 2  # c1 at t=0 and c2 at t=20
    assert result['metrics']['attempted_gate_violations'] == 0
    assert at(result, 5)['truth']['d1']['active_command'] == 'c1'
    # The replacement issued at t=20 was dropped, so the device still holds c1 at 8 kW.
    assert at(result, 25)['truth']['d1']['active_command'] == 'c1'
    assert Fraction(at(result, 25)['truth']['d1']['setpoint_kw']) == Fraction(8)
    # The controller cannot tell which of the two is running, so it keeps both.
    assert at(result, 25)['observed']['d1']['possible_active_commands'] == ['c1', 'c2']
    assert Fraction(at(result, 25)['observed']['d1']['worst_case_kw']) == Fraction(8)


def test_an_immediate_ack_is_delivered_in_the_second_its_command_was_issued():
    """A fault-free one-second run must not end falsely pending on a phase-order artifact."""
    result = simulate(scenario(duration_s=1, control_period_s=1, telemetry_period_s=1))
    entry = at(result, 0)
    assert entry['decision']  # a command was issued at t=0
    assert entry['observed']['d1']['pending_unconfirmed'] is False
    assert result['metrics']['devices_pending_at_end'] == []
    assert result['metrics']['uncertain_device_seconds'] == 0


def test_a_one_second_ttl_is_still_confirmable_by_its_own_immediate_ack():
    result = simulate(scenario(duration_s=3, control_period_s=1, telemetry_period_s=1,
                               command_ttl_s=1))
    assert at(result, 0)['observed']['d1']['possible_active_commands'] == ['c1']
    # The ACK is not backdated: it lands at the second it was delivered, which is this one.
    assert result['metrics']['uncertain_device_seconds'] == 0


def test_a_delayed_ack_leaves_the_device_uncertain_until_the_second_it_lands():
    result = simulate(scenario(duration_s=30, ack_delay_s={1: 5}))
    assert at(result, 4)['observed']['d1']['pending_unconfirmed'] is True
    assert at(result, 5)['observed']['d1']['pending_unconfirmed'] is False
    assert at(result, 4)['observed']['d2']['pending_unconfirmed'] is False  # ACKed at once
    assert result['metrics']['uncertain_device_seconds'] == 5


def test_a_lost_ack_still_lets_the_command_execute_while_the_controller_stays_unsure():
    result = simulate(scenario(drop_acks=[1, 2]))
    running = at(result, 5)
    # Truth: the command was received and is producing.
    assert running['truth']['d1']['active_command'] == 'c1'
    assert Fraction(running['truth']['d1']['setpoint_kw']) > 0
    # Knowledge: never acknowledged, so it stays in the unconfirmed set.
    assert running['observed']['d1']['pending_unconfirmed'] is True
    assert 'd1' in result['metrics']['devices_with_pending_uncertainty']


def test_islanding_delivers_zero_to_the_grid_while_silence_retains_uncertainty():
    result = simulate(scenario(
        duration_s=100, command_ttl_s=90,
        faults=[{'at_s': 30, 'device_id': 'd1', 'physical': ISLANDED, 'link': 'down'}]))
    before, after = at(result, 25), at(result, 70)
    assert Fraction(before['truth']['d1']['setpoint_kw']) > 0
    assert after['truth']['d1']['physical'] == ISLANDED
    # No grid delivery from an islanded device, and no house load is invented for it.
    assert Fraction(at(result, 70)['grid_kw']) == Fraction(at(result, 70)['truth']['d2']['setpoint_kw'])
    # The controller went blind, and its worst case still carries the pre-outage command.
    assert after['observed']['d1']['freshness'] == UNREACHABLE
    assert Fraction(after['observed']['d1']['worst_case_kw']) > 0


def test_a_non_operational_and_silent_device_overlaps_both_failure_axes():
    result = simulate(scenario(
        duration_s=100, command_ttl_s=90,
        faults=[{'at_s': 20, 'device_id': 'd1', 'physical': NON_OPERATIONAL, 'link': 'down'}]))
    entry = at(result, 80)
    assert entry['truth']['d1']['physical'] == NON_OPERATIONAL
    assert entry['observed']['d1']['freshness'] == UNREACHABLE
    # Physical failure and observation failure are reported on separate axes, not merged.
    assert entry['observed']['d1']['reported_physical'] == GRID_CONNECTED  # last thing ever seen


def test_an_outage_buffers_samples_and_the_reconnect_delivers_genuine_history():
    """The reconnect is deliberately off the telemetry cadence, so nothing fresh coincides
    with it and every delivered packet is genuinely old."""
    result = simulate(scenario(
        duration_s=120, command_ttl_s=110,
        faults=[{'at_s': 20, 'device_id': 'd1', 'link': 'down'},
                {'at_s': 75, 'device_id': 'd1', 'link': 'up'}]))
    dark = at(result, 60)['observed']['d1']
    assert dark['freshness'] == UNREACHABLE
    assert dark['last_sample_s'] == 10  # nothing sampled during the outage was delivered yet
    reconnected = at(result, 75)['observed']['d1']
    assert reconnected['last_sample_s'] == 70  # the sample time, never the delivery time
    # Six samples were taken while the link was down (20, 30, 40, 50, 60, 70) and arrive now.
    assert result['metrics']['backfilled_samples'] == 6
    assert result['metrics']['max_sample_age_s'] == 55  # the 20 s sample delivered at 75 s
    assert reconnected['sample_age_s'] == 5
    assert reconnected['reading_is_current'] is False  # history, not a current measurement
    # A mere reconnect does not resolve which command was active during the outage.
    assert Fraction(reconnected['worst_case_kw']) > 0
    for entry in result['trace']:  # no future leakage anywhere in the run
        observed = entry['observed']['d1']
        if observed['last_sample_s'] is not None:
            assert observed['last_sample_s'] <= entry['time_s']
            assert observed['reading_is_current'] == (observed['last_sample_s'] == entry['time_s'])


def test_the_offline_sample_buffer_is_bounded_and_keeps_the_newest_samples():
    from almanac.uncertainty import MAX_BUFFERED_SAMPLES
    result = simulate(scenario(
        duration_s=200, telemetry_period_s=5, command_ttl_s=60,
        faults=[{'at_s': 10, 'device_id': 'd1', 'link': 'down'},
                {'at_s': 191, 'device_id': 'd1', 'link': 'up'}]))
    # 37 samples were taken at 10..190 s; the bounded buffer can only hold the newest ones.
    assert MAX_BUFFERED_SAMPLES == 16
    assert result['metrics']['dropped_buffered_samples'] == 37 - MAX_BUFFERED_SAMPLES
    assert result['metrics']['backfilled_samples'] == MAX_BUFFERED_SAMPLES
    assert at(result, 191)['observed']['d1']['last_sample_s'] == 190  # newest survives
    assert at(result, 191)['observed']['d1']['reading_is_current'] is False


def test_an_islanded_device_keeps_sampling_while_a_dead_one_does_not():
    islanded = simulate(scenario(
        duration_s=120, command_ttl_s=110,
        faults=[{'at_s': 20, 'device_id': 'd1', 'physical': ISLANDED, 'link': 'down'},
                {'at_s': 75, 'device_id': 'd1', 'link': 'up'}]))
    dead = simulate(scenario(
        duration_s=120, command_ttl_s=110,
        faults=[{'at_s': 20, 'device_id': 'd1', 'physical': NON_OPERATIONAL, 'link': 'down'},
                {'at_s': 75, 'device_id': 'd1', 'link': 'up'}]))
    assert islanded['metrics']['backfilled_samples'] == 6
    # An islanded device reports its real zero grid power as history, not as a current value.
    backfilled = at(islanded, 75)['observed']['d1']
    assert backfilled['reported_physical'] == ISLANDED and backfilled['sample_age_s'] == 5
    assert dead['metrics']['backfilled_samples'] == 0  # a dead device samples nothing at all
    assert at(dead, 75)['observed']['d1']['reported_physical'] == GRID_CONNECTED


def test_a_duplicate_or_out_of_order_sample_is_history_and_never_the_current_reading():
    def packet(sample_s, power_kw):
        return {'device_id': 'd1', 'sample_s': sample_s, 'power_kw': Fraction(power_kw),
                'energy_kwh': Fraction(5), 'physical': GRID_CONNECTED}

    know = DeviceKnowledge('d1')
    assert _ingest_packet(know, packet(30, 4), 30) == 'current'
    assert _ingest_packet(know, packet(30, 9), 30) == 'stale'  # exact duplicate
    assert _ingest_packet(know, packet(20, 9), 30) == 'stale'  # out of order
    assert know.last_power_kw == Fraction(4)  # the newest reading is not overwritten by old
    assert know.stale_backfill_samples == 2
    assert _ingest_packet(know, packet(40, 6), 55) == 'historical'
    assert know.last_sample_s == 40 and know.max_sample_age_s == 15


# --- physics, conservation and gates ------------------------------------------------

def test_energy_is_conserved_exactly_and_the_reserve_floor_holds():
    result = simulate(scenario(duration_s=600, command_ttl_s=60,
                               devices=[{'device_id': 'd1', 'capacity_kw': 8,
                                         'energy_kwh': Fraction(3, 2), 'reserve_kwh': 1}]))
    drained = Fraction(3, 2) - Fraction(at(result, 599)['truth']['d1']['energy_kwh'])
    delivered = Fraction(result['metrics']['delivered_grid_kwh'])
    # The trace snapshot is taken after this step was integrated, so the two match exactly.
    assert delivered == drained
    assert result['metrics']['reserve_violations'] == 0
    assert result['metrics']['reserve_preserved'] is True
    # It could only ever deliver the energy above its reserve floor.
    assert delivered <= Fraction(1, 2)


def test_energy_is_conserved_exactly_while_the_device_is_still_discharging():
    """The reserve-floor case above ends at zero power; this one ends mid-discharge, so a
    conservation error in the final step cannot hide behind a zero."""
    result = simulate(scenario(duration_s=60, command_ttl_s=60, target_kw=8,
                               devices=[{'device_id': 'd1', 'capacity_kw': 8,
                                         'energy_kwh': 5, 'reserve_kwh': 1}]))
    final = at(result, 59)
    assert Fraction(final['grid_kw']) == Fraction(8)  # still discharging at the last second
    drained = Fraction(5) - Fraction(final['truth']['d1']['energy_kwh'])
    assert Fraction(result['metrics']['delivered_grid_kwh']) == drained
    assert drained == Fraction(8) * Fraction(60) / Fraction(3600)  # 2/15 kWh, exactly
    assert result['metrics']['reserve_violations'] == 0


def test_a_device_at_its_reserve_floor_delivers_nothing():
    result = simulate(scenario(devices=[{'device_id': 'd1', 'capacity_kw': 8,
                                         'energy_kwh': 2, 'reserve_kwh': 2}]))
    assert Fraction(result['metrics']['delivered_grid_kwh']) == 0
    assert result['metrics']['reserve_violations'] == 0


def test_the_conservative_controller_never_attempts_a_gate_violation():
    for faults in ([], [{'at_s': 20, 'device_id': 'd1', 'link': 'down'}],
                   [{'at_s': 40, 'device_id': 'd2', 'physical': NON_OPERATIONAL}]):
        result = simulate(scenario(faults=faults, drop_acks=[1, 3]))
        assert result['metrics']['attempted_gate_violations'] == 0
        assert Fraction(result['metrics']['max_overshoot_kw']) == 0


def test_a_naive_proposal_that_ignores_uncertainty_is_rejected_by_the_same_bound():
    """The naive failure is exercised as a rejected proposal; the gate is not loosened."""
    from almanac.uncertainty import _gate_decision
    spec = build_scenario(dict(BASE, target_kw=10))
    knowledge = {
        'd1': DeviceKnowledge('d1', confirmed=command(5, sequence=1),
                              unconfirmed=[command(0, sequence=3)]),
        'd2': DeviceKnowledge('d2', confirmed=command(5, sequence=2, device_id='d2')),
    }
    conservative = sum(k.bound_kw(0) for k in knowledge.values())
    assert conservative == Fraction(10) == spec.target_kw  # per device the max: 5 + 5
    # A naive controller believes d1's unconfirmed stop, sees only 5 kW committed, and fills
    # the capacity it thinks is free by loading d2 to 8 kW. If the stop was lost, that is
    # 5 + 8 = 13 kW against a 10 kW target — and the same bound refuses it.
    capacities = {d.device_id: d.capacity_kw for d in spec.devices}
    accepted, rejection = _gate_decision({'d2': Fraction(8)}, knowledge, capacities, 0,
                                         spec.target_kw)
    assert accepted == {} and rejection is not None and '13' in rejection
    # The conservative controller proposes nothing here at all: it waits.
    assert _would_wait(knowledge, spec)


def _would_wait(knowledge, spec):
    capacities = {d.device_id: d.capacity_kw for d in spec.devices}
    freshness = {device_id: REPORTING for device_id in knowledge}
    return _conservative_decision(knowledge, capacities, freshness, 0, spec.target_kw) == {}


def test_the_gate_rejects_an_unsafe_proposal_before_anything_is_dispatched():
    """Counting a violation is not enough: an unsafe proposal must never reach a device."""
    def greedy(knowledge, capacities, freshness, now_s, target_kw):
        return {device_id: capacities[device_id] for device_id in sorted(knowledge)}

    result = simulate(scenario(), decide=greedy)  # 8 + 8 kW against a 10 kW target
    assert result['metrics']['attempted_gate_violations'] == 6  # one per control tick
    assert result['metrics']['commands_issued'] == 0  # fail closed: nothing was dispatched
    assert Fraction(result['metrics']['delivered_grid_kwh']) == 0
    assert Fraction(result['metrics']['max_overshoot_kw']) == 0
    reasons = [rejection['reason'] for rejection in result['gate_rejections']]
    assert len(reasons) == 6 and all('16' in reason for reason in reasons)  # the diagnostic
    assert result['gate_rejections'][0]['proposed'] == {'d1': '8', 'd2': '8'}


@pytest.mark.parametrize('proposal', [
    {'d1': Fraction(-1)},            # negative setpoint
    {'d1': Fraction(99)},            # beyond the device capacity
    {'nope': Fraction(1)},           # unknown device
    {'d1': float('nan')},            # not a finite number
    {'d1': float('inf')},
    {'d1': 'lots'},                  # not a number at all
    ['d1'],                          # not even a mapping
    'd1',
])
def test_the_gate_rejects_a_malformed_proposal_and_returns_a_diagnostic(proposal):
    result = simulate(scenario(), decide=lambda *args: proposal)
    assert result['metrics']['commands_issued'] == 0
    assert result['metrics']['attempted_gate_violations'] == 6
    assert all(rejection['reason'] for rejection in result['gate_rejections'])


def test_a_zero_target_is_permitted_and_commands_nothing():
    result = simulate(scenario(target_kw=0))
    assert Fraction(result['metrics']['delivered_grid_kwh']) == 0
    assert result['metrics']['commands_issued'] == 0
    assert all(not entry['decision'] for entry in result['trace'])


def test_metrics_are_integrated_rather_than_asserted():
    result = simulate(scenario())
    requested = Fraction(result['metrics']['requested_kwh'])
    delivered = Fraction(result['metrics']['delivered_grid_kwh'])
    shortfall = Fraction(result['metrics']['shortfall_kwh'])
    assert requested == Fraction(10) * Fraction(120) / Fraction(3600)
    assert shortfall == max(Fraction(0), requested - delivered)
    integrated = sum(Fraction(e['grid_kw']) for e in result['trace']) / Fraction(3600)
    assert delivered == integrated


# --- determinism, validation and the truth boundary ---------------------------------

def test_the_same_seed_and_config_reproduce_an_identical_result():
    assert simulate(scenario()) == simulate(scenario())


@pytest.mark.parametrize('bad', [
    {'devices': [{'device_id': 'd1', 'capacity_kw': 8, 'energy_kwh': 0, 'reserve_kwh': 1}]},
    {'devices': []},
    {'devices': [{'device_id': 'd1', 'capacity_kw': -1, 'energy_kwh': 5, 'reserve_kwh': 1}]},
    {'devices': [{'device_id': 'd1', 'capacity_kw': 8, 'energy_kwh': 5, 'reserve_kwh': 1},
                 {'device_id': 'd1', 'capacity_kw': 8, 'energy_kwh': 5, 'reserve_kwh': 1}]},
    {'target_kw': float('nan')},
    {'target_kw': float('inf')},
    {'target_kw': -1},
    {'control_period_s': 0},
    {'control_period_s': 1.5},
    {'control_period_s': True},
    {'duration_s': 10 ** 9},
    {'seed': -1},
    {'seed': 2 ** 32},
    {'command_ttl_s': 'soon'},
    {'faults': [{'at_s': 10, 'device_id': 'nope', 'link': 'down'}]},
    {'faults': [{'at_s': 10, 'device_id': 'd1', 'physical': 'on-fire'}]},
    {'faults': [{'at_s': 10, 'device_id': 'd1'}]},
    # Loss and delay schedules are inputs too, and were previously taken on trust.
    {'drop_commands': 'abc'},
    {'drop_commands': 3},
    {'drop_commands': {1: 2}},
    {'drop_commands': [0]},
    {'drop_commands': [-1]},
    {'drop_commands': [True]},
    {'drop_commands': [1.5]},
    {'drop_commands': ['1']},
    {'drop_acks': 'abc'},
    {'drop_acks': [0]},
    {'drop_acks': [float('nan')]},
    {'ack_delay_s': [1, 2]},
    {'ack_delay_s': 'now'},
    {'ack_delay_s': {'1': 2}},
    {'ack_delay_s': {0: 2}},
    {'ack_delay_s': {1: -1}},
    {'ack_delay_s': {1: 1.5}},
    {'ack_delay_s': {1: True}},
    {'ack_delay_s': {1: 'soon'}},
    {'ack_delay_s': {1: float('inf')}},
    {'ack_delay_s': {1: 121}},  # beyond the 120 s scenario duration
])
def test_malformed_scenarios_are_rejected_before_any_state_is_mutated(bad):
    with pytest.raises(ScenarioError):
        scenario(**bad)


def test_duplicate_loss_and_delay_entries_are_deduplicated_rather_than_rejected():
    """Duplicate sequence ids are a harmless restatement of the same instruction."""
    spec = scenario(drop_commands=[1, 1, 2], drop_acks=(3, 3))
    assert spec.drop_commands == frozenset({1, 2})
    assert spec.drop_acks == frozenset({3})
    assert scenario(ack_delay_s={1: 0, 2: 3}).ack_delay_s == {1: 0, 2: 3}
    assert scenario(ack_delay_s={1: 120}).ack_delay_s == {1: 120}  # the bound itself is fine


def test_the_observed_timeline_never_leaks_truth_or_the_future():
    result = simulate(scenario(
        duration_s=100, command_ttl_s=90,
        faults=[{'at_s': 20, 'device_id': 'd1', 'physical': ISLANDED, 'link': 'down'}]))
    for entry in result['trace']:
        observed = entry['observed']['d1']
        assert set(observed) == {'freshness', 'last_sample_s', 'sample_age_s',
                                 'reading_is_current', 'reported_physical',
                                 'possible_active_commands', 'worst_case_kw', 'pending_unconfirmed'}
        # No truth key, no fault schedule, no RNG and never a sample from the future.
        assert 'physical' not in observed and 'link_up' not in observed
        if observed['last_sample_s'] is not None:
            assert observed['last_sample_s'] <= entry['time_s']
    # After the fault the truth says islanded while the last observation still says otherwise.
    late = at(result, 60)
    assert late['truth']['d1']['physical'] == ISLANDED
    assert late['observed']['d1']['reported_physical'] == GRID_CONNECTED


def test_the_result_labels_itself_experimental_and_states_its_assumptions():
    result = simulate()
    assert result['experimental'] is True
    assert result['mode'] == 'offline-experimental-simulation-only'
    assert any('synthetic' in line for line in result['assumptions'])
    assert any('not a generic policy adapter' in line for line in result['controller_limits'])
