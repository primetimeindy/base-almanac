"""Post-run views of existing traces. Never a controller input."""

def annotate(rows: list[dict], actions: list[dict], device_ids: list[str], identity: dict) -> list[dict]:
    """Attach past-only reported state and emit observed row-state changes.

    Missing telemetry stays missing; command power is not reported power.
    Actual plant state remains exclusively in ground_truth_devices.
    """
    reports, acknowledgements, events = {}, {}, []
    by_time = {}
    for action in actions:
        by_time.setdefault(action['time_s'], []).append(action)
    previous_signature = None
    for row in rows:
        now = row['time_s']
        packets = {p['id']: p for p in row['controller_visible']}
        if any(p['sampled_s'] > now for p in packets.values()):
            raise ValueError('inspector cannot expose a future telemetry sample')
        reports.update(packets)
        for a in by_time.get(now, []):
            acknowledgements[a['device']] = dict(command_id=a['command_id'],
                issued_s=a['time_s'], power_kw=a['power_kw'], deadline_s=a['deadline_s'])
        devices = []
        for device_id in device_ids:
            packet = packets.get(device_id)
            report = reports.get(device_id)
            ack = acknowledgements.get(device_id)
            fresh = packet is not None and packet['accepted_as_fresh']
            state = 'FRESH_PACKET' if fresh else 'STALE_PACKET' if packet else 'NO_PACKET'
            if fresh:
                reason = 'ACKNOWLEDGED_ALLOCATION' if ack and ack['power_kw'] else 'NO_ALLOCATION'
            elif ack and now < ack['deadline_s']:
                reason = 'WAITING_FOR_LEASE_BOUND' if ack['power_kw'] else 'ACKNOWLEDGED_ZERO_COMMAND'
            else:
                reason = 'LEASE_BOUND_ELAPSED' if ack else 'NO_ACKNOWLEDGED_COMMAND'
            devices.append(dict(id=device_id, connectivity=state,
                sampled_s=report['sampled_s'] if report else None,
                report_age_s=now-report['sampled_s'] if report else None,
                reported_energy_kwh=report['energy_kwh'] if report else None,
                reported_power_kw=None, last_acknowledged_command=ack,
                reason_code=reason))
        row['inspector'] = devices
        # Emit only changes evidenced by this row, not a prescribed story arc.
        signature = (tuple(row['reasons']), row['uncertain_upper_kw'],
                     row['shortfall_kw'], tuple((d['connectivity'], d['reason_code']) for d in devices))
        if signature != previous_signature:
            events.append(dict(time_s=now, end_s=row['end_s'], controller=identity,
                reason_codes=row['reasons'], uncertain_upper_kw=row['uncertain_upper_kw'],
                target_kw=row['target_kw'], shortfall_kw=row['shortfall_kw'],
                acknowledged_commands=len(by_time.get(now, [])),
                lease_bound_elapsed_ids=[d['id'] for d in devices
                    if d['reason_code'] == 'LEASE_BOUND_ELAPSED']))
        previous_signature = signature
    return events
