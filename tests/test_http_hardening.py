"""Real HTTP failure probes. No external network or policy imports."""
import importlib.util
import json
from pathlib import Path
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.request import urlopen
from http.server import ThreadingHTTPServer
import pytest
from almanac import geofleet_server, replay_server

ROOT = Path(__file__).resolve().parents[1]

@pytest.fixture(params=['geo', 'replay'])
def service(request):
    handler = geofleet_server.Handler if request.param == 'geo' else replay_server.make_handler(json.loads((ROOT/'demo/data/uri.json').read_text()))
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    t = threading.Thread(target=server.serve_forever, daemon=True); t.start()
    yield server, request.param
    server.shutdown(); server.server_close(); t.join()


def exchange(server, target='/', headers='', body=b'', method='GET'):
    with socket.create_connection(server.server_address, timeout=4) as conn:
        conn.settimeout(4)
        conn.sendall(f'{method} {target} HTTP/1.1\r\nHost: 127.0.0.1:{server.server_port}\r\nConnection: close\r\n{headers}\r\n'.encode()+body)
        result = b''
        while True:
            chunk = conn.recv(65536)
            if not chunk: break
            result += chunk
        return result


@pytest.mark.parametrize('headers', ['Host: evil.example\r\n', 'Origin: http://evil.example\r\n', 'Transfer-Encoding: chunked\r\n'])
def test_ambiguous_authority_and_framing_rejected(service, headers):
    server, _ = service
    response = exchange(server, '/health', headers)
    assert response.startswith((b'HTTP/1.0 400', b'HTTP/1.0 403'))


def test_invalid_url_and_oversize_query_do_not_disconnect(service):
    server, _ = service
    for target in ['http://[/', '/api/v1/geofleet/preview?area='+('x'*1024)]:
        response = exchange(server, target)
        assert response.startswith((b'HTTP/1.0 400', b'HTTP/1.0 414'))


@pytest.mark.parametrize('body,headers', [
    (b'{"commands":[],"commands":[]}', ''),
    (b'['*1500+b']'*1500, ''),
    (b'{"commands":NaN}', ''),
    (b'{"commands":[]}', 'Content-Length: 15\r\n'),
])
def test_replay_invalid_json_and_duplicate_length_no_receipt(service, body, headers):
    server, kind = service
    if kind != 'replay': return
    response = exchange(server, '/api/v1/replay', headers+f'Content-Length: {len(body)}\r\n', body, 'POST')
    assert response.startswith(b'HTTP/1.0 400')
    assert b'receipt_sha256' not in response


def test_geofleet_busy_request_rejected_not_queued(monkeypatch):
    started, release = threading.Event(), threading.Event()
    original = geofleet_server.experiment
    def blocking(payload):
        started.set()
        assert release.wait(5)
        return original(payload)
    monkeypatch.setattr(geofleet_server, 'experiment', blocking)
    server = ThreadingHTTPServer(('127.0.0.1', 0), geofleet_server.Handler)
    t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
    try:
        with ThreadPoolExecutor(2) as pool:
            first = pool.submit(exchange, server, '/api/v1/geofleet/run?area=core')
            assert started.wait(2)
            second = pool.submit(exchange, server, '/api/v1/geofleet/run?area=wide')
            try:
                assert second.result(timeout=2).startswith(b'HTTP/1.0 503')
            finally: release.set()
            assert first.result(timeout=5).startswith(b'HTTP/1.0 200')
    finally:
        release.set();server.shutdown();server.server_close();t.join()


def test_production_server_bounds_workers():
    assert importlib.util.find_spec('almanac.local_http'), 'bounded server missing'
    from almanac.local_http import BoundedHTTPServer
    assert BoundedHTTPServer.max_workers == 4
    assert geofleet_server.Server is BoundedHTTPServer
    assert replay_server.Server is BoundedHTTPServer
