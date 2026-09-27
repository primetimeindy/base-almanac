"""Loopback-only read-only geofleet HTTP service. No policy imports over HTTP."""
import argparse
import threading

RUN_SLOT = threading.Lock()
from almanac.local_http import LocalHandler, BoundedHTTPServer as Server
from urllib.parse import urlsplit, parse_qs
from almanac.geofleet import ROOT, preview, experiment, load_source
from almanac.replay import canonical
from almanac.cli import (ERROR_SCHEMA, OPERATIONAL_EXIT, POLICIES, SCENARIOS,
                         evaluate, listing, render_html, report_stem)
# Experimental uncertainty surface: a separate closed registry, separate routes, separate
# schemas. It shares this server's loopback guards and the single RUN_SLOT, nothing else.
from almanac.uncertainty_lab import EXPERIMENTS, experiment_stem
from almanac.uncertainty_lab import evaluate as uncertainty_evaluate
from almanac.uncertainty_lab import listing as uncertainty_listing

CHECK_ENVELOPE = 'almanac.check-envelope.v1'
CHECK_REGISTRY_PATH = '/api/v1/check/registry'
CHECK_RUN_PATH = '/api/v1/check/run'
UNCERTAINTY_ENVELOPE = 'almanac.uncertainty-envelope.v1'
UNCERTAINTY_REGISTRY_PATH = '/api/v1/uncertainty/registry'
UNCERTAINTY_RUN_PATH = '/api/v1/uncertainty/run'

class Handler(LocalHandler):
    def send(self,status,body,kind='application/json',attachment=False):
        self.send_response(status)
        self.send_header('Content-Type',kind)
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'")
        if attachment: self.send_header('Content-Disposition','attachment; filename="almanac-geofleet.json"')
        self.end_headers(); self.wfile.write(body)

    def check_envelope(self,scenario_key,policy_key):
        """One evaluation, and the exact bytes of both downloads it produces.

        `report_json` and `report_html` are what `almanac run --html` would publish for this
        report, so the browser's two downloads and the state it displays are the same check.
        """
        report=evaluate(scenario_key,policy_key)
        stem=report_stem(scenario_key,policy_key)
        return {'schema':CHECK_ENVELOPE,'report':report,
                'report_json':canonical(report)+'\n','report_html':render_html(report),
                'downloads':{'json':stem+'.json','html':stem+'.html'},
                'note':'report, report_json and report_html all come from the single evaluation '
                       'recorded here; the downloads are those exact bytes, never a second run'}

    def check_run(self,query):
        """Run one registered fixture against one bundled policy. Registry keys only."""
        try:
            if len(query)>96: raise ValueError('query too large')
            try:
                q=parse_qs(query,strict_parsing=True,keep_blank_values=True)
            except ValueError: # its message quotes the offending field; never echo caller bytes
                raise ValueError('malformed query') from None
            if set(q)!={'scenario','policy'} or any(len(v)!=1 for v in q.values()):
                raise ValueError('exactly one scenario and one policy required')
            scenario,policy=q['scenario'][0],q['policy'][0]
            # Membership in the closed registry, nothing else: no import path, no filesystem
            # path, no uploaded code. A rejected value is never echoed back to the caller.
            if scenario not in SCENARIOS: raise ValueError('unknown scenario key')
            if policy not in POLICIES: raise ValueError('unknown policy key')
        except ValueError as exc:
            return self.send(400,canonical({'error':str(exc)}).encode())
        if not RUN_SLOT.acquire(blocking=False):
            return self.send(503,b'{"error":"busy; retry after current request"}')
        try:
            envelope=self.check_envelope(scenario,policy)
        except Exception as exc: # the check never completed: report that, never a verdict
            return self.send(503 if isinstance(exc,(OSError,KeyError)) else 500,canonical({
                'schema':ERROR_SCHEMA,'verdict':'operational-error','stage':'offline check over HTTP',
                'error_type':type(exc).__name__,'message':str(exc),'exit_code':OPERATIONAL_EXIT,
                'note':'The check did not complete. This is a fixture, engine or rendering failure, '
                       'not a pass, not a performance regression and not an observation about the '
                       'simulated plant.'}).encode())
        finally:
            RUN_SLOT.release()
        self.send(200,canonical(envelope).encode())

    def uncertainty_run(self,query):
        """Run one registered experiment. Registry keys only, and no file is written.

        The report, the values the page displays and the exported bytes all come from this
        one completed run. A rejected key is never echoed back into the response.
        """
        try:
            if len(query)>64: raise ValueError('query too large')
            try:
                q=parse_qs(query,strict_parsing=True,keep_blank_values=True)
            except ValueError: # its message quotes the offending field; never echo caller bytes
                raise ValueError('malformed query') from None
            if set(q)!={'experiment'} or len(q['experiment'])!=1:
                raise ValueError('exactly one experiment required')
            # Membership in the closed registry, nothing else: no scenario JSON, no import
            # path, no filesystem path. The rejected value stays out of the response.
            if q['experiment'][0] not in EXPERIMENTS: raise ValueError('unknown experiment key')
            key=q['experiment'][0]
        except ValueError as exc:
            return self.send(400,canonical({'error':str(exc)}).encode())
        if not RUN_SLOT.acquire(blocking=False):
            return self.send(503,b'{"error":"busy; retry after current request"}')
        try:
            report=uncertainty_evaluate(key)
            envelope={'schema':UNCERTAINTY_ENVELOPE,'experimental':True,'safety_verdict':None,
                      'report':report,'report_json':canonical(report)+'\n',
                      'downloads':{'json':experiment_stem(key)+'.json'},
                      'note':'report and report_json come from the single completed run '
                             'recorded here; the export is those exact bytes, never a second run'}
        except Exception as exc: # the run never completed: report that, never a result
            return self.send(503 if isinstance(exc,(OSError,KeyError)) else 500,canonical({
                'schema':ERROR_SCHEMA,'verdict':'operational-error',
                'stage':'experimental uncertainty run over HTTP','error_type':type(exc).__name__,
                'message':str(exc),'exit_code':OPERATIONAL_EXIT,
                'note':'The experiment did not complete. This is a simulator or evaluator '
                       'failure, not a result and not a safety verdict.'}).encode())
        finally:
            RUN_SLOT.release()
        self.send(200,canonical(envelope).encode())

    def do_GET(self):
        host=f'127.0.0.1:{self.server.server_port}'
        if self.headers.get('Host')!=host or (self.headers.get('Origin') and self.headers['Origin']!='http://'+host):
            return self.send(403,b'{"error":"local_origin_required"}')
        u=urlsplit(self.path)
        if u.path in ('/','/geofleet') and not u.query:
            return self.send(200,(ROOT/'demo/geofleet.html').read_bytes(),'text/html; charset=utf-8')
        assets = {f'/assets/{name}.svg' for name in ('almanac-mark-dark', 'almanac-mark-light', 'almanac-wordmark-dark', 'almanac-wordmark-light', 'favicon')}
        if u.path in assets and not u.query:
            return self.send(200,(ROOT/'demo'/u.path.lstrip('/')).read_bytes(),'image/svg+xml')
        if u.path=='/api/v1/contract' and not u.query:
            from almanac.controller_contract import integration_contract
            return self.send(200,canonical(integration_contract()).encode())
        if u.path=='/health': return self.send(200,b'{"status":"ok","mode":"simulation-only"}')
        if u.path==CHECK_REGISTRY_PATH:
            if u.query: return self.send(400,b'{"error":"listing accepts no query parameters"}')
            return self.send(200,canonical(listing()).encode())
        if u.path==CHECK_RUN_PATH: return self.check_run(u.query)
        if u.path==UNCERTAINTY_REGISTRY_PATH:
            if u.query: return self.send(400,b'{"error":"listing accepts no query parameters"}')
            return self.send(200,canonical(uncertainty_listing()).encode())
        if u.path==UNCERTAINTY_RUN_PATH: return self.uncertainty_run(u.query)
        if u.path not in ('/api/v1/geofleet/preview','/api/v1/geofleet/run','/api/v1/geofleet/export'):
            return self.send(404,b'{"error":"not_found"}')
        try:
            if len(u.query)>64: raise ValueError('query too large')
            try:
                q=parse_qs(u.query,strict_parsing=True,keep_blank_values=True)
            except ValueError: # its message quotes the offending field; never echo caller bytes
                raise ValueError('malformed query') from None
            if set(q)!={'area'} or len(q['area'])!=1: raise ValueError('one area required')
            fn=preview if u.path.endswith('/preview') else experiment
            if not RUN_SLOT.acquire(blocking=False):
                return self.send(503,b'{"error":"busy; retry after current request"}')
            try:
                result=fn({'area':q['area'][0]})
            finally:
                RUN_SLOT.release()
        except ValueError as exc:
            return self.send(400,canonical({'error':str(exc)}).encode())
        except (OSError,KeyError) as exc:
            return self.send(503,b'{"error":"cached official evidence unavailable; no simulation generated"}')
        self.send(200,canonical(result).encode(),attachment=u.path.endswith('/export'))

def main():
    p=argparse.ArgumentParser(); p.add_argument('--port',type=int,default=8767); args=p.parse_args()
    load_source() # Missing evidence prevents startup, never synthesizes source data.
    server=Server(('127.0.0.1',args.port),Handler)
    print(f'Geofleet simulation only: http://127.0.0.1:{args.port}/geofleet',flush=True)
    server.serve_forever()

if __name__=='__main__': main()
