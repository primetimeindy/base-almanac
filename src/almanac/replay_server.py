"""Local-only, stateless replay server. No physical or commercial integrations."""
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from almanac.replay import replay, canonical, validate_bundle
from almanac.fleet import compare
ROOT=Path(__file__).resolve().parents[2]

def make_handler(bundle):
    validate_bundle(bundle)
    # Compute once per local server, never accept external control/scenario input.
    fleet_receipt = canonical(compare()).encode()
    class Handler(BaseHTTPRequestHandler):
        def send(self,status,body,kind='application/json',attachment=None):
            self.send_response(status)
            if attachment: self.send_header('Content-Disposition', f'attachment; filename="{attachment}"')
            self.send_header('Content-Type',kind); self.send_header('Content-Length',str(len(body))); self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff'); self.end_headers(); self.wfile.write(body)
        def do_GET(self):
            if self.path=='/': self.send(200,(ROOT/'demo/index.html').read_bytes(),'text/html; charset=utf-8')
            elif self.path=='/fleet': self.send(200,(ROOT/'demo/fleet.html').read_bytes(),'text/html; charset=utf-8')
            elif self.path=='/api/v1/fleet': self.send(200,fleet_receipt)
            elif self.path=='/api/v1/fleet/export': self.send(200,fleet_receipt,attachment='almanac-fleet-comparison.json')
            elif self.path=='/health': self.send(200,b'{"status":"ok","mode":"simulation-only"}')
            elif self.path=='/api/v1/replay': self.send(200,canonical(replay(bundle,[])).encode())
            else: self.send(404,b'{"error":"not_found"}')
        def do_POST(self):
            if self.path!='/api/v1/replay': return self.send(404,b'{"error":"not_found"}')
            origin=self.headers.get('Origin')
            if origin and origin!=f'http://{self.headers.get("Host")}': return self.send(403,b'{"error":"cross_origin"}')
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=16384: raise ValueError('invalid body size')
                payload=json.loads(self.rfile.read(size))
                if not isinstance(payload,dict) or set(payload)!={'commands'}: raise ValueError('commands required')
                result=replay(bundle,payload['commands'])
            except (ValueError,TypeError) as exc: return self.send(400,canonical({'error':str(exc)}).encode())
            self.send(200,canonical(result).encode())
    return Handler

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--port',type=int,default=8765); args=parser.parse_args()
    bundle=json.loads((ROOT/'demo/data/uri.json').read_text())
    server=ThreadingHTTPServer(('127.0.0.1',args.port),make_handler(bundle))
    server.timeout=10
    print(f'Almanac simulation only: http://127.0.0.1:{args.port}',flush=True)
    server.serve_forever()
if __name__=='__main__': main()
