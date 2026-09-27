"""Bounded geographic communication-loss experiments, no physical integrations."""
import io
import json
import math
import zipfile
import hashlib
from copy import deepcopy
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
from xml.etree import ElementTree as ET
from almanac.fleet import compare, default_scenario
from almanac.replay import digest, canonical

ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIR = ROOT / 'demo/data/beryl'
SOURCE_URL = 'https://www.nhc.noaa.gov/gis/best_track/al022024_best_track.kmz'
# Operator stress regions, not hazard-derived footprints. lon/lat inclusive bounds.
AREAS = {
    'all': {'label': 'All-device stress control', 'bounds': [-97.1, 29.5, -95.3, 30.4]},
    'core': {'label': 'Houston-side cluster', 'bounds': [-96.1, 29.4, -95.4, 30.2]},
    'wide': {'label': 'Wider southeast Texas cluster', 'bounds': [-96.5, 29.4, -94.9, 30.8]},
    'west': {'label': 'Move stress west', 'bounds': [-97.1, 29.4, -96.4, 30.2]},
    'empty': {'label': 'Empty selection control', 'bounds': [-98, 31, -97.5, 31.5]},
}

def coordinate(value, low, high):
    """Return exact decimal fraction of a bounded native JSON number."""
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError('invalid lon/lat coordinate')
    return Fraction(str(value))

def select(points: list, bounds: list) -> list[str]:
    """Select inclusive rectangle in [west,south,east,north] order, exact arithmetic."""
    if type(bounds) is not list or len(bounds) != 4:
        raise ValueError('rectangle schema')
    w,s,e,n = [coordinate(v, -180 if i%2==0 else -90, 180 if i%2==0 else 90) for i,v in enumerate(bounds)]
    if not w < e or not s < n: raise ValueError('rectangle order')
    if type(points) is not list or len(points)>100: raise ValueError('bounded points required')
    result=[]; seen=set()
    for p in points:
        if type(p) is not dict or set(p)!={'id','lon','lat'} or type(p['id']) is not str or not p['id'] or p['id'] in seen:
            raise ValueError('point identity/schema')
        seen.add(p['id'])
        x,y=coordinate(p['lon'],-180,180),coordinate(p['lat'],-90,90)
        if w<=x<=e and s<=y<=n: result.append(p['id'])
    return sorted(result)

def parse_track(raw: bytes) -> dict:
    """Read only official point coordinates and UTC timestamps from bounded KMZ."""
    if not 0 < len(raw) <= 2_000_000: raise ValueError('source size')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        info=archive.getinfo('al022024.kml')
        if info.file_size > 2_000_000: raise ValueError('KML size')
        xml=archive.read(info)
    tree=ET.fromstring(xml)
    ns={'k':'http://earth.google.com/kml/2.2'}
    points=[]
    for p in tree.findall('.//k:Placemark',ns):
        c=p.find('k:Point/k:coordinates',ns)
        if c is None: continue
        parts=c.text.strip().split(',')
        lon,lat=float(parts[0]),float(parts[1])
        coordinate(lon,-180,180); coordinate(lat,-90,90)
        dt=datetime.strptime(p.findtext('k:atcfdtg',namespaces=ns), '%Y%m%d%H').replace(tzinfo=timezone.utc)
        points.append({'lon':lon,'lat':lat,'time_utc':dt.isoformat().replace('+00:00','Z')})
    if len(points)<2 or any(a['time_utc']>=b['time_utc'] for a,b in zip(points,points[1:])):
        raise ValueError('empty or nonmonotonic track')
    subset=[p for p in points if '2024-07-08T00:00:00Z'<=p['time_utc']<='2024-07-09T00:00:00Z']
    if len(subset)<2: raise ValueError('Texas time window missing')
    return {'track':subset, 'raw_point_count':len(points), 'raw_time_bounds_utc':[points[0]['time_utc'],points[-1]['time_utc']], 'display_time_bounds_utc':[subset[0]['time_utc'],subset[-1]['time_utc']]}

def load_source(directory: Path = SOURCE_DIR) -> dict:
    """Fail closed if cached evidence is missing, altered or invalid. Never fetch."""
    meta=json.loads((directory/'manifest.json').read_text())
    required={'retrieved_start_utc','retrieved_end_utc','sha256','url','bytes',
              'raw_point_count','raw_time_bounds_utc','display_time_bounds_utc'}
    if type(meta) is not dict or not required <= set(meta) or any(
            type(meta[k]) is not str for k in ('retrieved_start_utc','retrieved_end_utc','sha256','url')):
        raise ValueError('invalid source metadata schema')
    start=datetime.fromisoformat(meta['retrieved_start_utc'])
    end=datetime.fromisoformat(meta['retrieved_end_utc'])
    if start.tzinfo is None or end.tzinfo is None or not datetime(2025,1,23,tzinfo=timezone.utc)<=start<=end<=datetime.now(timezone.utc):
        raise ValueError('invalid source capture time')
    raw=(directory/'al022024_best_track.kmz').read_bytes()
    if hashlib.sha256(raw).hexdigest()!=meta['sha256']: raise ValueError('source digest mismatch')
    if meta['url']!=SOURCE_URL or meta['bytes']!=len(raw): raise ValueError('source identity')
    parsed=parse_track(raw)
    for key in ('raw_point_count','raw_time_bounds_utc','display_time_bounds_utc'):
        if meta[key]!=parsed[key]: raise ValueError('source coverage mismatch')
    return dict(parsed,metadata=meta)

def positions() -> list[dict]:
    """Fictitious abstract inland lattice, never customer or actual deployment sites."""
    return [{'id':f'B{i:03d}', 'lon':float(Fraction(-971,10)+Fraction(i%10,5)),
             'lat':float(Fraction(295,10)+Fraction(i//10,10))} for i in range(100)]

def preview(payload: dict) -> dict:
    """Validate one allowlisted preset and return exact selection before simulation."""
    if type(payload) is not dict or set(payload)!={'area'} or type(payload['area']) is not str or payload['area'] not in AREAS:
        raise ValueError('choose one allowlisted area')
    source=load_source(); pts=positions(); bounds=list(AREAS[payload['area']]['bounds'])
    # Detach every preset from the module global: a caller mutating one result must not
    # corrupt AREAS, another result or a prior experiment receipt.
    return {'source':source, 'positions':pts, 'areas':deepcopy(AREAS), 'area':payload['area'],
            'bounds':bounds, 'selected_ids':select(pts,bounds),
            'geometry_rule':'inclusive axis-aligned lon/lat rectangle; exact decimal fractions; no distance buffer or failure probabilities',
            'position_semantics':'fictitious abstract inland points, not addresses or Base deployments'}

def scenario_for(payload: dict) -> tuple[dict, dict]:
    """Validate the selection and build its synthetic scenario, before any simulation.

    Returns the preview selection and the scenario. Callers that only need the
    scenario must go through here rather than rebuilding the fault schedule.
    """
    selection=preview(payload); ids=selection['selected_ids']
    s=default_scenario(); s['id']='geofleet-beryl-'+payload['area']+'-v1'
    s['faults']=([{'start_s':60,'end_s':360,'kind':'offline','devices':ids}] if ids else [])
    return selection, s

def experiment(payload: dict) -> dict:
    """Run both illustrative controllers with identical initial state and geographic faults."""
    selection, s = scenario_for(payload)
    report={'schema':'almanac.geofleet.v1','selection':selection,
            'causal_contract':{'observed':'NHC post-storm best track, not wind or outage footprint',
                              'intervention':'Operator disconnects all selected simulated communication links at t=60s, reconnects at t=360s',
                              'time':'Historical track is static context; simulation uses synthetic relative seconds, not Beryl timestamps',
                              'not_modeled':['physical grid outage','wind damage','failure probability','Uri/Beryl price alignment','actual Base controller','HTTP-uploaded controller code'],
                              'need':'Assume engineering lacks an equivalent tool; not independently verified'},
            'simulation':compare(s)}
    no_fault_s = json.loads(canonical(s))
    no_fault_s['faults'] = []
    no_fault = compare(no_fault_s)
    def difference(simulation):
        runs = simulation['strategies']
        return (Fraction(runs['baseline']['metrics']['exact_shortfall_kwh']) -
                Fraction(runs['constrained']['metrics']['exact_shortfall_kwh']))
    fault_delta, control_delta = difference(report['simulation']), difference(no_fault)
    context = {'no_fault': no_fault,
               'interpretation': 'Full-run policy differences include reserve redistribution. The difference of differences is not an isolated fault-recovery benefit; fault and reserve effects interact. One synthetic scenario, not empirical performance.'}
    for name, value in [('fault_run_difference_kwh', fault_delta),
                        ('no_fault_difference_kwh', control_delta),
                        ('difference_of_differences_kwh', fault_delta - control_delta)]:
        context[name] = round(float(value), 9)
        context['exact_' + name] = str(value)
    report['comparison_context'] = context
    report['receipt_sha256']=digest(report)
    return report
