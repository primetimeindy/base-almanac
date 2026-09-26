"""Live loopback route tests, paired export is the actual engine report."""
import json
import urllib.request
from test_replay_http import server
from almanac.fleet import compare
from almanac.replay import canonical


def test_fleet_route_and_export(server):
    url, _ = server
    with urllib.request.urlopen(url + '/fleet', timeout=10) as response:
        assert b'id="comparison"' in response.read()
    with urllib.request.urlopen(url + '/api/v1/fleet', timeout=10) as response:
        assert response.read().decode() == canonical(compare())
    with urllib.request.urlopen(url + '/api/v1/fleet/export', timeout=10) as response:
        assert 'attachment' in response.headers['Content-Disposition']
        assert json.load(response) == compare()
    with urllib.request.urlopen(url, timeout=10) as response:
        assert b'href="/fleet"' in response.read()
