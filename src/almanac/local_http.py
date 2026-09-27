"""Small loopback HTTP limits for demo servers, not public hosting infrastructure."""
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class BoundedHTTPServer(ThreadingHTTPServer):
    """Reject excess connections instead of creating unbounded worker threads."""
    max_workers = 4  # Local demo budget, including slow clients.
    daemon_threads = True
    request_queue_size = 4

    def __init__(self, *args, **kwargs):
        self.slots = threading.BoundedSemaphore(self.max_workers)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            try:
                request.settimeout(0.2)
                request.sendall(b'HTTP/1.0 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
            except OSError:
                pass  # Client is gone; no request was dispatched.
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


class LocalHandler(BaseHTTPRequestHandler):
    """One request per connection with bounded framing and loopback authority."""
    timeout = 3  # Bound incomplete local request headers/bodies.

    def parse_request(self):
        if not super().parse_request():
            return False
        self.close_connection = True
        host = f'127.0.0.1:{self.server.server_port}'
        if self.headers.get_all('Host', []) != [host] or self.headers.get_all('Origin', []) not in ([], ['http://' + host]):
            self.send_error(403, 'local_origin_required')
            return False
        if len(self.path) > 256:
            self.send_error(414, 'request_target_too_large')
            return False
        if not self.path.startswith('/') or self.path.startswith('//') or '#' in self.path:
            self.send_error(400, 'origin_form_path_required')
            return False
        lengths = self.headers.get_all('Content-Length', [])
        if self.headers.get_all('Transfer-Encoding') or len(lengths) > 1 or (lengths and re.fullmatch(r'[0-9]{1,5}', lengths[0]) is None):
            self.send_error(400, 'ambiguous_body_framing')
            return False
        if lengths and (int(lengths[0]) > 16384 or (self.command != 'POST' and int(lengths[0]) != 0)):
            self.send_error(400, 'invalid_body_size')
            return False
        return True


def strict_json(raw: bytes):
    """Reject duplicate fields, nonfinite constants and excessive nesting."""
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate JSON field')
            result[key] = value
        return result
    def constant(_):
        raise ValueError('nonfinite JSON value')
    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except RecursionError as exc:
        raise ValueError('JSON nesting exceeds limit') from exc
