import importlib.util
import threading
import json
from urllib.request import urlopen, Request
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer
import pytest

def test_local_routes_export_and_rejection():
    assert importlib.util.find_spec('almanac.geofleet_server'), 'geofleet server missing'
    from almanac.geofleet_server import Handler
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    t=threading.Thread(target=server.serve_forever,daemon=True); t.start()
    url=f'http://127.0.0.1:{server.server_port}'
    try:
        assert b'id="map"' in urlopen(url+'/geofleet').read()
        preview=json.load(urlopen(url+'/api/v1/geofleet/preview?area=core'))
        assert preview['selected_ids']
        a=urlopen(url+'/api/v1/geofleet/run?area=core').read()
        b=urlopen(url+'/api/v1/geofleet/export?area=core').read()
        assert a==b
        for path in ['?area=bogus','?area=core&area=wide','?area=core&foo=1','?area=core&foo=','?area=core&area=','']:
            with pytest.raises(HTTPError) as e: urlopen(url+'/api/v1/geofleet/run'+path)
            assert e.value.code==400
        with pytest.raises(HTTPError) as e: urlopen(Request(url+'/health',headers={'Host':'evil.example'}))
        assert e.value.code==403
    finally:
        server.shutdown(); server.server_close(); t.join()

@pytest.mark.parametrize('path', ['/api/v1/geofleet/preview','/api/v1/geofleet/run','/api/v1/geofleet/export'])
@pytest.mark.parametrize('query', [
    '?area=core&REFLECT_ME',  # strict parsing names the bad field
    '?REFLECT_ME',
    '?REFLECT_ME=1&area=core',
    '?area=core&REFLECT_ME&also=2',
])
def test_legacy_routes_never_reflect_the_rejected_query_field(path, query):
    """The geofleet routes must reject a malformed query the way the check route does.

    `parse_qs(strict_parsing=True)` raises `bad query field: '<value>'`, so returning that
    exception's text echoes caller-controlled bytes into the error document.
    """
    from almanac.geofleet_server import Handler
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    t=threading.Thread(target=server.serve_forever,daemon=True); t.start()
    try:
        with pytest.raises(HTTPError) as error:
            urlopen(f'http://127.0.0.1:{server.server_port}{path}{query}')
        assert error.value.code==400
        body=error.value.read()
        assert b'REFLECT_ME' not in body
        assert set(json.loads(body))=={'error'}
    finally:
        server.shutdown(); server.server_close(); t.join()

@pytest.mark.parametrize('failure,status', [(FileNotFoundError('missing raw'),503), (ValueError('source digest mismatch'),400)])
def test_missing_or_altered_source_never_returns_simulation(monkeypatch, failure, status):
    from almanac import geofleet
    from almanac.geofleet_server import Handler
    def unavailable():
        raise failure
    monkeypatch.setattr(geofleet, 'load_source', unavailable)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    t=threading.Thread(target=server.serve_forever,daemon=True); t.start()
    try:
        for action in ('preview','run','export'):
            with pytest.raises(HTTPError) as error:
                urlopen(f'http://127.0.0.1:{server.server_port}/api/v1/geofleet/{action}?area=core')
            assert error.value.code==status
            body=json.loads(error.value.read())
            assert set(body)=={'error'}
    finally:
        server.shutdown(); server.server_close(); t.join()
