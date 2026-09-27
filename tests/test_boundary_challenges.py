"""Held-out probes of composed action and connection boundaries."""
import json
from fractions import Fraction as F
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import pytest
from almanac.controller_contract import compare_policy
from almanac.fleet import default_scenario
from almanac.local_http import BoundedHTTPServer
from almanac.geofleet_server import Handler
from examples.priority_policy import Policy

ROOT = Path(__file__).resolve().parents[1]


def test_unknown_output_budget_is_enforced_after_links_disappear():
    class Overcommit(Policy):
        def decide(self, observation):
            if F(observation['unknown_upper_kw']) > 0:
                # Ignore the withheld budget, while still respecting device caps.
                observation['dispatch_budget_kw'] = observation['target_kw']
            return super().decide(observation)
    with pytest.raises(ValueError, match='target minus unknown'):
        compare_policy(default_scenario(), Overcommit())


def test_stale_and_missing_devices_are_never_actionable():
    seen = []
    class Inspect(Policy):
        def decide(self, observation):
            ids = {d['id'] for d in observation['devices']}
            t = observation['time_s']
            assert all(d['sampled_s'] == t for d in observation['devices'])
            if 60 <= t < 200: assert not ids.intersection({f'B{i:03d}' for i in range(10)})
            if 300 <= t < 380: assert not ids
            seen.append(t)
            return super().decide(observation)
    compare_policy(default_scenario(), Inspect())
    assert seen == list(range(0, 600, 10))


def test_real_cli_import_receipt_and_failure_stdout():
    command = [sys.executable, '-m', 'almanac.controller_contract', '--policy', 'examples.priority_policy:Policy', '--area', 'empty']
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=15)
    assert completed.returncode == 0, completed.stderr
    receipt = json.loads(completed.stdout)
    assert receipt['policy']['id'] == 'priority-order'
    assert receipt['policy']['version'] == '1.0.0'
    assert receipt['candidate']['scenario_sha256'] == receipt['reference']['strategies']['baseline']['scenario_sha256']
    command[4] = 'examples.missing:Policy'
    failed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=15)
    assert failed.returncode != 0
    assert failed.stdout == ''
    assert 'No receipt generated' in failed.stderr


def test_actual_connection_capacity_and_recovery():
    service = BoundedHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=service.serve_forever, daemon=True); worker.start()
    held = []
    try:
        for _ in range(4):
            client = socket.create_connection(service.server_address, timeout=2)
            client.sendall(b'GET /health HTTP/1.1\r\n')
            held.append(client)
        deadline = time.monotonic() + 1
        while service.slots._value and time.monotonic() < deadline: time.sleep(.01)
        with socket.create_connection(service.server_address, timeout=2) as extra:
            assert extra.recv(1024).startswith(b'HTTP/1.0 503')
        for client in held: client.close()
        held.clear()
        deadline = time.monotonic() + 1
        while service.slots._value != 4 and time.monotonic() < deadline: time.sleep(.01)
        assert service.slots._value == 4
        from urllib.request import urlopen
        with urlopen(f'http://127.0.0.1:{service.server_port}/health', timeout=2) as response:
            assert response.status == 200
    finally:
        for client in held: client.close()
        service.shutdown();service.server_close();worker.join()
