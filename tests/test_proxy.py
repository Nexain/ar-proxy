import http.client
import http.server
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import unittest

import verify_models

ROOT = Path(__file__).resolve().parents[1]
PROFILES = json.loads((ROOT / "trae-models.json").read_text())


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class FakeAgentRouter(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def send_json(self, status, data):
        raw = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        self.wfile.write(raw)

    def do_POST(self):
        if self.headers.get("Transfer-Encoding") == "chunked":
            chunks = []
            while True:
                size = int(self.rfile.readline().strip(), 16)
                if not size:
                    self.rfile.readline()
                    break
                chunks.append(self.rfile.read(size))
                self.rfile.read(2)
            raw = b"".join(chunks)
        else:
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if self.headers.get("X-Test-Mode") == "html":
            raw = b"<html>WAF challenge</html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            self.wfile.write(raw)
            return
        if self.headers.get("X-Test-Mode") == "quota":
            self.send_json(429, {"error": {"message": "quota exhausted"}})
            return
        if self.path == "/echo":
            self.send_json(200, {"body": raw.decode()})
            return
        body = json.loads(raw)
        model = body["model"]
        native = model.startswith("claude-")
        route = "/v1/messages" if native else "/v1/chat/completions"
        if self.path != route:
            self.send_json(400, {"error": {"message": "Wrong model protocol"}})
            return
        key = self.headers.get("x-api-key") if native else self.headers.get("Authorization")
        if key != ("dummy-test-key" if native else "Bearer dummy-test-key"):
            self.send_json(401, {"error": {"message": "Wrong authentication"}})
            return
        token_field = "max_completion_tokens" if model.startswith("gpt-") else "max_tokens"
        if token_field not in body or (native and not self.headers.get("anthropic-version")):
            self.send_json(400, {"error": {"message": "Wrong model parameters"}})
            return
        if not body.get("stream"):
            self.send_json(200, {"content": [{"type": "text", "text": "OK"}]} if native else
                           {"choices": [{"message": {"content": "OK"}}]})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        if self.headers.get("X-Test-Mode") == "stream-error":
            self.wfile.write(b'event: error\r\ndata: {"type":"error","error":{"message":"model unavailable"}}\r\n\r\n')
            self.wfile.flush()
            return
        payload = ({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "你好"}}
                   if native else {"choices": [{"delta": {"content": "你好"}}]})
        frame = (("event: content_block_delta\r\n" if native else "") +
                 "data: " + json.dumps(payload, ensure_ascii=False) + "\r\n\r\n").encode()
        split = frame.index("你".encode()) + 1
        self.wfile.write(frame[:split])
        self.wfile.flush()
        time.sleep(0.02)
        self.wfile.write(frame[split:])
        self.wfile.flush()
        time.sleep(0.2)
        done = ('event: message_stop\r\ndata: {"type":"message_stop"}' if native else 'data: [DONE]')
        self.wfile.write(b'data: null\r\n\r\ndata: []\r\n\r\n' + done.encode() + b'\r\n\r\n')
        self.wfile.flush()


class ProxyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.upstream = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeAgentRouter)
        threading.Thread(target=cls.upstream.serve_forever, daemon=True).start()
        cls.processes = []
        cls.proxies = []
        cls.addClassCleanup(cls.cleanup)
        for script in ("proxy.js", "proxy.py", "ar_proxy.py"):
            port = free_port()
            env = dict(os.environ, AR_PROXY_PORT=str(port),
                       AR_UPSTREAM=f"http://127.0.0.1:{cls.upstream.server_port}",
                       AR_LOG="0", AR_LOG_FILE="", AR_VERBOSE="0", PYTHONDONTWRITEBYTECODE="1")
            process = subprocess.Popen(["node" if script.endswith(".js") else sys.executable, str(ROOT / script)],
                                       env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            cls.processes.append(process)
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError(f"{script} exited before listening")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.05):
                        break
                except OSError:
                    time.sleep(0.02)
            else:
                raise RuntimeError(f"{script} never listened")
            cls.proxies.append((script, port))

    @classmethod
    def cleanup(cls):
        for process in cls.processes:
            process.terminate()
            process.wait(timeout=5)
        cls.upstream.shutdown()
        cls.upstream.server_close()

    def test_all_model_profiles_and_protocols(self):
        for script, port in self.proxies:
            for profile in PROFILES:
                for stream in (False, True):
                    with self.subTest(proxy=script, model=profile["model"], stream=stream):
                        ok, detail = verify_models.probe(profile, f"http://127.0.0.1:{port}", "dummy-test-key", stream, timeout=2)
                        self.assertTrue(ok, detail)

    def test_stream_is_incremental_and_unicode_is_preserved(self):
        for script, port in self.proxies:
            with self.subTest(proxy=script):
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                self.addCleanup(conn.close)
                start = time.monotonic()
                conn.request("POST", "/v1/chat/completions", json.dumps(verify_models.request_body("deepseek-v4-flash", True, 256)),
                             {"Authorization": "Bearer dummy-test-key", "Content-Type": "application/json"})
                response = conn.getresponse()
                first = response.readline()
                self.assertLess(time.monotonic() - start, 0.2)
                self.assertIn("你好", first.decode())
                rest = response.read()
                self.assertIn(b"[DONE]", rest)
                self.assertNotIn(b"data: null", rest)
                self.assertNotIn(b"data: []", rest)

    def test_json_ends_and_chunked_requests_keep_body(self):
        for script, port in self.proxies:
            with self.subTest(proxy=script):
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                self.addCleanup(conn.close)
                conn.request("POST", "/echo", iter([b'{"x":', b'1}']), encode_chunked=True)
                self.assertEqual(json.loads(conn.getresponse().read()), {"body": '{"x":1}'})

    def test_html_is_actionable_and_quota_error_is_preserved(self):
        for script, port in self.proxies:
            for mode, status in (("html", 502), ("quota", 429)):
                with self.subTest(proxy=script, mode=mode):
                    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                    self.addCleanup(conn.close)
                    conn.request("POST", "/v1/chat/completions", b"{}", {"X-Test-Mode": mode})
                    response = conn.getresponse()
                    self.assertEqual(response.status, status)
                    data = json.loads(response.read())
                    if mode == "html":
                        self.assertEqual(data["error"]["type"], "upstream_html_response")
                    else:
                        self.assertEqual(data["error"]["message"], "quota exhausted")

    def test_wrong_protocol_remains_a_failure(self):
        profile = dict(PROFILES[1], apiFormat="Anthropic Messages", requestUrl="http://localhost/v1/messages")
        for script, port in self.proxies:
            with self.subTest(proxy=script):
                ok, detail = verify_models.probe(profile, f"http://127.0.0.1:{port}", "dummy-test-key", False, timeout=2)
                self.assertFalse(ok)
                self.assertIn("Wrong model protocol", detail)

    def test_native_stream_errors_are_not_discarded(self):
        for script, port in self.proxies:
            with self.subTest(proxy=script):
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                self.addCleanup(conn.close)
                conn.request("POST", "/v1/messages", json.dumps(verify_models.request_body("claude-opus-5", True, 256)),
                             {"x-api-key": "dummy-test-key", "anthropic-version": "2023-06-01", "X-Test-Mode": "stream-error"})
                response = conn.getresponse()
                raw = response.read()
                self.assertIn(b"event: error", raw)
                self.assertIn(b"model unavailable", raw)


if __name__ == "__main__":
    unittest.main()
