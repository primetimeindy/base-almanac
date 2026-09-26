"""Loopback-only bounded, read-only geofleet HTTP service. No controller adapter."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs
from almanac.geofleet import ROOT, preview, experiment, load_source
from almanac.replay import canonical

class Handler(BaseHTTPRequestHandler):
    def send(self,status,body,kind='application/json',attachment=False):
        self.send_response(status)
        self.send_header('Content-Type',kind)
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'")
        if attachment: self.send_header('Content-Disposition','attachment; filename="almanac-geofleet.json"')
        self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        host=f'127.0.0.1:{self.server.server_port}'
        if self.headers.get('Host')!=host or (self.headers.get('Origin') and self.headers['Origin']!='http://'+host):
            return self.send(403,b'{"error":"local_origin_required"}')
        u=urlsplit(self.path)
        if u.path in ('/','/geofleet') and not u.query:
            return self.send(200,(ROOT/'demo/geofleet.html').read_bytes(),'text/html; charset=utf-8')
        if u.path=='/health': return self.send(200,b'{"status":"ok","mode":"simulation-only"}')
        if u.path not in ('/api/v1/geofleet/preview','/api/v1/geofleet/run','/api/v1/geofleet/export'):
            return self.send(404,b'{"error":"not_found"}')
        try:
            if len(u.query)>64: raise ValueError('query too large')
            q=parse_qs(u.query,strict_parsing=True,keep_blank_values=True)
            if set(q)!={'area'} or len(q['area'])!=1: raise ValueError('one area required')
            fn=preview if u.path.endswith('/preview') else experiment
            result=fn({'area':q['area'][0]})
        except ValueError as exc:
            return self.send(400,canonical({'error':str(exc)}).encode())
        except (OSError,KeyError) as exc:
            return self.send(503,b'{"error":"cached official evidence unavailable; no simulation generated"}')
        self.send(200,canonical(result).encode(),attachment=u.path.endswith('/export'))

def main():
    p=argparse.ArgumentParser(); p.add_argument('--port',type=int,default=8767); args=p.parse_args()
    load_source() # Missing evidence prevents startup, never synthesizes source data.
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    print(f'Geofleet simulation only: http://127.0.0.1:{args.port}/geofleet',flush=True)
    server.serve_forever()

if __name__=='__main__': main()
