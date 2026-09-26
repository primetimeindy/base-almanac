"""Deterministic retrospective evidence-to-proposal reducer. No external actions."""
from __future__ import annotations
import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

# Demonstration policy only, not Base operating limits or a dispatch strategy.
POLICY = {'stress_usd_per_mwh':'1000.00', 'max_age_seconds':900, 'requested_kw':10, 'duration_minutes':15}
ASSET = {'id':'SIM-RESERVE-01','provenance':'simulated','capacity_kw':12,'energy_kwh':'5.00','location':'LZ_HOUSTON','description':'Invented reserve resource, not a Base battery or customer'}
COMMANDS = {'next','tick','missing','stale','unavailable','restore','approve','reset'}

def canonical(value: object) -> str:
    """Return stable JSON, rejecting non-finite numeric JSON values."""
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)

def timestamp(value: str) -> datetime:
    """Parse an explicitly zoned ISO timestamp or fail closed."""
    if not isinstance(value,str): raise ValueError('timestamp must be a string')
    try: result=datetime.fromisoformat(value)
    except ValueError as exc: raise ValueError('malformed timestamp') from exc
    if result.tzinfo is None or result.utcoffset() is None: raise ValueError('timezone required')
    return result.astimezone(timezone.utc)

def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()

def validate_bundle(bundle: dict) -> None:
    """Validate one contiguous observed zone/type slice; never silently dedupe."""
    try:
        if bundle['schema']!='almanac.history.v1': raise ValueError('schema')
        rows=bundle['observed']
        if not isinstance(rows,list) or not rows: raise ValueError('empty evidence')
        previous=None
        for row in rows:
            start=timestamp(row['interval_start']); end=timestamp(row['interval_end'])
            if end-start!=timedelta(minutes=15) or timestamp(row['available_at'])!=end: raise ValueError('interval or availability')
            if previous is not None and start-previous!=timedelta(minutes=15): raise ValueError('duplicate, unsorted or gap')
            previous=start
            if (row['unit'],row['location'],row['point_type'],row['provenance'])!=('USD/MWh','LZ_HOUSTON','LZEW','observed'): raise ValueError('evidence semantics')
            price=row['spp_usd_per_mwh']
            if not isinstance(price,str) or not Decimal(price).is_finite(): raise ValueError('finite decimal price string required')
        source=bundle['source']
        timestamp(source['captured_at_utc'])
        if source['rows']!=len(rows): raise ValueError('count mismatch')
        if timestamp(source['selection_start'])!=timestamp(rows[0]['interval_start']) or timestamp(source['selection_end_exclusive'])!=timestamp(rows[-1]['interval_end']): raise ValueError('span mismatch')
    except (KeyError,TypeError,InvalidOperation) as exc:
        raise ValueError('malformed evidence bundle') from exc

def replay(bundle: dict, commands: list[str]) -> dict:
    """Recompute from immutable input and bounded commands; reset clears session.

    Approval creates only an in-memory simulated receipt. Recovery never approves.
    Repeat approval for one interval is idempotent even after failure/recovery.
    """
    validate_bundle(bundle)
    if not isinstance(commands,list) or len(commands)>512 or any(not isinstance(c,str) or c not in COMMANDS for c in commands): raise ValueError('invalid command history')
    # Reset is an explicit new replay, including audit and idempotency scope.
    if 'reset' in commands: commands=commands[len(commands)-commands[::-1].index('reset'):]
    rows=bundle['observed']; index=0; clock=timestamp(rows[0]['available_at'])
    mode='healthy'; available=True; actions=[]; audit=[]; normalized=[]
    def decision():
        row=rows[index]
        age=int((clock-timestamp(row['available_at'])).total_seconds())
        reasons=[]
        if mode=='missing': reasons.append('MISSING_FEED')
        if age<0: reasons.append('FUTURE_EVIDENCE')
        if age>POLICY['max_age_seconds']: reasons.append('STALE_FEED')
        if not available: reasons.append('RESOURCE_UNAVAILABLE')
        if ASSET['capacity_kw']<POLICY['requested_kw'] or Decimal(ASSET['energy_kwh'])<Decimal(POLICY['requested_kw'])*Decimal(POLICY['duration_minutes'])/60: reasons.append('INSUFFICIENT_CAPACITY')
        stress=Decimal(row['spp_usd_per_mwh'])>=Decimal(POLICY['stress_usd_per_mwh'])
        status='HOLD' if reasons else ('READY' if stress else 'MONITOR')
        return {'status':status,'reasons':reasons,'age_seconds':age,'stress':stress if mode!='missing' else None,'provenance':'derived','approval_required':True,'proposed_action':'Simulate a 10 kW reserve allocation for 15 minutes','requested_kw':POLICY['requested_kw'],'requested_kwh':'2.50','external_side_effects':False}
    for command in commands:
        normalized.append(command); outcome='RECOMPUTED'
        if command=='next':
            index=min(index+1,len(rows)-1); clock=max(clock,timestamp(rows[index]['available_at']))
        elif command=='tick': clock+=timedelta(minutes=15)
        elif command=='missing': mode='missing'
        elif command=='stale': clock+=timedelta(minutes=30)
        elif command=='unavailable': available=False
        elif command=='restore':
            mode='healthy'; available=True
            # No rewind: find latest evidence actually available by replay time.
            candidates=[i for i,r in enumerate(rows) if timestamp(r['available_at'])<=clock]
            index=max(candidates)
        current=decision()
        if command=='approve':
            key=digest({'bundle':digest(bundle),'interval':rows[index]['interval_start'],'asset':ASSET['id']})
            if current['status']!='READY': outcome='APPROVAL_REJECTED'
            elif any(a['id']==key for a in actions): outcome='IDEMPOTENT_NOOP'
            else:
                actions.append({'id':key,'provenance':'simulated','interval_start':rows[index]['interval_start'],'replay_at_utc':clock.isoformat(),'requested_kw':10,'duration_minutes':15,'external_side_effects':False})
                outcome='SIMULATION_RECORDED'
        audit.append({'sequence':len(audit)+1,'command':command,'outcome':outcome,'replay_at_utc':clock.isoformat(),'decision':current,'evidence_interval':rows[index]['interval_start'],'previous_hash':audit[-1]['hash'] if audit else None})
        audit[-1]['hash']=digest(audit[-1])
    result={'schema':'almanac.replay.v1','event':bundle['event'],'source':bundle['source'],'source_bundle_sha256':digest(bundle),'replay_at_utc':clock.isoformat(),'policy':POLICY.copy(),'simulated_asset':dict(ASSET,available=available),'feed_mode':mode,'observed':None if mode=='missing' else rows[index],'visible_observed':[r for r in rows if timestamp(r['available_at'])<=clock], 'decision':decision(),'commands':normalized,'audit':audit,'simulated_actions':actions,'truth':{'observed':'ERCOT retrospective LZEW settlement prices only','simulated':'Resource, faults, feed availability timing and reserve allocation','derived':'Threshold stress flag, freshness/capacity gates and receipt','not_claimed':'No outage measurement, forecast, savings, private Base data, real dispatch or as-published backtest'}}
    result['receipt_sha256']=digest(result)
    # Detach public receipt from immutable source and policy references.
    return json.loads(canonical(result))