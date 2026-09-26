"""Exercise real HTTP boundary against isolated loopback server, no shared state."""
import json
import threading
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer
from pathlib import Path
import pytest
from almanac.replay_server import make_handler
from almanac.replay import replay, canonical

@pytest.fixture
def server():
    bundle=json.loads((Path(__file__).resolve().parents[1]/'demo/data/uri.json').read_text())
    service=ThreadingHTTPServer(('127.0.0.1',0),make_handler(bundle))
    worker=threading.Thread(target=service.serve_forever,daemon=True); worker.start()
    yield f'http://127.0.0.1:{service.server_port}',bundle
    service.shutdown();service.server_close();worker.join()

def test_http_export_matches_engine(server):
    url,bundle=server
    commands=['missing','approve','restore','approve']
    req=urllib.request.Request(url+'/api/v1/replay',data=json.dumps({'commands':commands}).encode(),headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=5) as response:
        assert response.read().decode()==canonical(replay(bundle,commands))
    with urllib.request.urlopen(url+'/api/v1/replay',timeout=5) as response:
        assert json.load(response)['simulated_actions']==[]

@pytest.mark.parametrize('payload', [b'no json', b'[]', b'{"commands":["dispatch"]}',b'{"commands":[],"asset":{}}'])
def test_malformed_http_fails_closed(server,payload):
    url,_=server
    req=urllib.request.Request(url+'/api/v1/replay',data=payload)
    with pytest.raises(urllib.error.HTTPError) as error: urllib.request.urlopen(req,timeout=5)
    assert error.value.code==400

def test_cross_origin_and_path_rejected(server):
    url,_=server
    req=urllib.request.Request(url+'/api/v1/replay',data=b'{"commands":[]}',headers={'Origin':'https://untrusted.example'})
    with pytest.raises(urllib.error.HTTPError) as error: urllib.request.urlopen(req,timeout=5)
    assert error.value.code==403
    with pytest.raises(urllib.error.HTTPError) as error: urllib.request.urlopen(url+'/../pyproject.toml',timeout=5)
    assert error.value.code==404
