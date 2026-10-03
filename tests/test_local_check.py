from __future__ import annotations

import contextlib
import http.client
import importlib.util
import io
import json
import threading
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

SPEC = importlib.util.spec_from_file_location(
    "forge_check_local", Path(__file__).resolve().parents[1] / "scripts" / "check-local.py"
)
assert SPEC and SPEC.loader
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)


class LocalCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.requests: list[tuple[str, str | None]] = []
        self.responses = {
            "/": (200, b"<title>Forge Run Console</title>", {}),
            "/health": (200, b'{"status":"ok"}', {}),
        }
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                fixture.requests.append((self.path, self.headers.get("Authorization")))
                status, body, headers = fixture.responses[self.path]
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.addCleanup(self.server.server_close)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join, 2)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_port

    def test_only_two_read_only_loopback_requests_without_credentials(self):
        with mock.patch.dict("os.environ", {
            "OPENAI_API_KEY": "fixture-paid-secret", "CLERK_SECRET_KEY": "fixture-auth-secret",
            "HTTP_PROXY": "http://invalid-proxy.example:9999",
            "http_proxy": "http://invalid-proxy.example:9999",
        }):
            report = checker.check_local(self.port, self.port)
        self.assertTrue(report["services_reachable"])
        self.assertFalse(report["model_calls_made"])
        self.assertEqual(self.requests, [("/", None), ("/health", None)])
        self.assertNotIn("fixture-paid-secret", json.dumps(report))
        self.assertNotIn("fixture-auth-secret", json.dumps(report))

    def test_wrong_page_and_malformed_health_are_not_success(self):
        for body in (b"null", b"[]", b"not json", b'{"status":"failed"}'):
            self.responses["/health"] = (200, body, {})
            self.assertFalse(checker.probe_service("backend", self.port)["reachable"])
        self.responses["/"] = (200, b"Another application", {})
        self.assertFalse(checker.probe_service("website", self.port)["reachable"])

    def test_redirect_is_not_followed_even_to_another_local_path(self):
        self.responses["/health"] = (302, b"private-provider-detail", {
            "Location": f"http://localhost:{self.port}/",
        })
        result = checker.probe_service("backend", self.port)
        self.assertFalse(result["reachable"])
        self.assertEqual(result["http_status"], 302)
        self.assertEqual(self.requests, [("/health", None)])
        self.assertNotIn("private-provider-detail", json.dumps(result))

    def test_http_errors_and_oversized_responses_do_not_echo_content(self):
        for status, body in ((503, b"private-provider-detail"),
                             (200, b"x" * (checker.MAX_RESPONSE_BYTES + 1))):
            self.responses["/health"] = (status, body, {})
            result = checker.probe_service("backend", self.port)
            self.assertFalse(result["reachable"])
            self.assertNotIn("private-provider-detail", json.dumps(result))

    def test_network_failure_is_bounded_and_sanitized(self):
        opener = mock.Mock()
        for error in (urllib.error.URLError("private-network-detail"),
                      http.client.IncompleteRead(b"private-network-detail")):
            opener.open.side_effect = error
            with mock.patch.object(checker.urllib.request, "build_opener", return_value=opener):
                result = checker.probe_service("backend", self.port)
            self.assertFalse(result["reachable"])
            self.assertNotIn("private-network-detail", json.dumps(result))
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 3)

    def test_cli_json_and_plain_text_have_meaningful_exit_codes(self):
        for json_mode in (False, True):
            output = io.StringIO()
            args = ["--web-port", str(self.port), "--api-port", str(self.port)]
            if json_mode:
                args.append("--json")
            with contextlib.redirect_stdout(output):
                self.assertEqual(checker.main(args), 0)
            if json_mode:
                self.assertTrue(json.loads(output.getvalue())["services_reachable"])
            else:
                self.assertIn("No API keys are read", output.getvalue())
        self.responses["/health"] = (503, b"private-provider-detail", {})
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(checker.main(["--web-port", str(self.port), "--api-port", str(self.port)]), 1)

    def test_invalid_ports_and_unsupported_services_stop_before_network(self):
        for value in ("0", "65536", "not-a-port"):
            with self.assertRaises(Exception):
                checker.port_number(value)
        with mock.patch.object(checker.urllib.request, "build_opener") as build_opener:
            with self.assertRaises(ValueError):
                checker.probe_service("external", self.port)
            with self.assertRaises(ValueError):
                checker.probe_service("backend", 0)
            build_opener.assert_not_called()


if __name__ == "__main__":
    unittest.main()
