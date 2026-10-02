from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

# Bind an OS-selected loopback port in the server process and retain the socket.
# This avoids stealing port 8000 or racing another service for a "free" port.
SERVER_BOOT = """
import socket
import sys
import tempfile
from pathlib import Path
import uvicorn
from forge_api.main import app

tempfile.tempdir = sys.argv[2]
listener = socket.socket()
listener.bind(('127.0.0.1', 0))
Path(sys.argv[1]).write_text(str(listener.getsockname()[1]), encoding='utf-8')
server = uvicorn.Server(uvicorn.Config(
    app, host='127.0.0.1', log_level='warning', access_log=False,
))

@app.post('/_test/stop/' + sys.argv[3])
async def stop_test_server():
    server.should_exit = True
    return {'stopping': True}

server.run(sockets=[listener])
"""


class OfflineDemoHttpTests(unittest.TestCase):
    """Exercise real HTTP serialization and persistence across API restarts."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="forge-http-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.server: subprocess.Popen[bytes] | None = None
        self.base_url: str | None = None
        self.addCleanup(self._stop_server)
        self.environment = {
            key: value
            for key, value in os.environ.items()
            if not key.upper().startswith((
                "FORGE_", "OPENAI_", "CLERK_", "AWS_", "ANTHROPIC_",
            ))
        }
        source_paths = [
            str(path)
            for directory in (ROOT / "packages", ROOT / "services")
            for path in directory.glob("*/src")
        ]
        self.environment.update({
            "PYTHONPATH": os.pathsep.join(source_paths),
            "PYTHONDONTWRITEBYTECODE": "1",
            "FORGE_DATABASE_PATH": str(self.root / "forge.db"),
            "FORGE_ARTIFACT_BACKEND": "local",
            "FORGE_ARTIFACT_PATH": str(self.root / "artifacts"),
            "FORGE_SANDBOX_BACKEND": "local",
            "FORGE_AUTH_MODE": "disabled",
            "FORGE_OFFLINE_DEMO_FIXTURE_PATH": str(
                ROOT / "evals" / "public-tasks" / "status-normalizer"
            ),
        })
        # Never use a system proxy even if the workstation has one configured.
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self._start_server()

    def _start_server(self) -> None:
        port_file = self.root / "server-port"
        port_file.unlink(missing_ok=True)
        self.base_url = None
        self.shutdown_token = uuid.uuid4().hex
        self.server = subprocess.Popen(
            [
                sys.executable, "-c", SERVER_BOOT, str(port_file),
                str(self.root), self.shutdown_token,
            ],
            cwd=ROOT,
            env=self.environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self.server.poll() is not None:
                self.fail("isolated demo API exited before becoming healthy")
            if port_file.is_file():
                try:
                    self.base_url = "http://127.0.0.1:" + port_file.read_text(
                        encoding="utf-8"
                    ).strip()
                    status, response = self._request("/health", timeout=1)
                    if status == 200 and response == {"status": "ok"}:
                        return
                except (OSError, ValueError):
                    pass
            time.sleep(0.05)
        self.fail("isolated demo API did not become healthy within 15 seconds")

    def _stop_server(self) -> None:
        if self.server is not None:
            # Graceful shutdown closes the actual server, not just a Windows
            # virtualenv launcher. This test-only route never exists in Forge.
            if self.server.poll() is None and self.base_url is not None:
                try:
                    self._request(
                        "/_test/stop/" + self.shutdown_token,
                        method="POST", timeout=2,
                    )
                except OSError:
                    pass
            try:
                self.server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                if os.name == "nt":
                    # Only this still-owned subprocess and its children.
                    subprocess.run(
                        ["taskkill", "/PID", str(self.server.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        timeout=5, check=False,
                    )
                else:
                    self.server.kill()
                self.server.wait(timeout=5)
            self.server = None

    def _request(
        self, path: str, *, method: str = "GET",
        payload: dict[str, object] | None = None, timeout: float = 30,
    ) -> tuple[int, object]:
        assert self.base_url is not None
        request = urllib.request.Request(
            self.base_url + path,
            data=None if payload is None else json.dumps(payload).encode("utf-8"),
            method=method,
            headers={"content-type": "application/json"},
        )
        try:
            response = self.opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.code, json.loads(response.read())

    def _create_demo(self) -> dict[str, object]:
        status, created = self._request("/demo/runs", method="POST")
        self.assertEqual(status, 201, created)
        self.assertEqual(created["run"]["state"], "AWAITING_APPROVAL")
        self.assertEqual(created["review"]["verdict"], "passed")
        self.assertEqual(len(created["review"]["checks"]), 4)
        self.assertTrue(all(
            check["status"] == "passed" for check in created["review"]["checks"]
        ))
        self.assertEqual(created["safety"], {
            "provider": "offline", "model": "forge-deterministic-demo-v1",
            "model_calls": 0, "network_requests": 0, "cost_microusd": 0,
            "github_writes": 0,
        })
        return created

    @staticmethod
    def _decision(created: dict[str, object], decision: str) -> dict[str, object]:
        return {
            "decision": decision,
            "patch_hash": created["review"]["patch_hash"],
            "evaluation_verdict_hash": created["review"]["verdict_hash"],
            "decision_key": "http-demo-review-" + decision,
        }

    def test_approve_and_reject_survive_process_restart_without_publication(self) -> None:
        for choice in ("approved", "rejected"):
            with self.subTest(decision=choice):
                created = self._create_demo()
                path = "/demo/runs/" + created["run"]["run_id"]
                payload = self._decision(created, choice)
                status, decided = self._request(
                    path + "/decision", method="POST", payload=payload,
                )
                self.assertEqual(status, 200, decided)
                self.assertEqual(decided["decision"]["decision"], choice)
                self.assertFalse(decided["decision"]["authorizes_github_write"])

                self._stop_server()
                self._start_server()
                status, restored = self._request(path)
                self.assertEqual(status, 200, restored)
                self.assertEqual(restored["decision"], decided["decision"])
                self.assertEqual(restored["review"], created["review"])
                self.assertEqual(restored["run"]["state"], "AWAITING_APPROVAL")
                status, replay = self._request(
                    path + "/decision", method="POST", payload=payload,
                )
                self.assertEqual(status, 200, replay)
                self.assertEqual(replay["decision"], decided["decision"])
                opposite = dict(
                    payload, decision="rejected" if choice == "approved" else "approved",
                )
                status, _ = self._request(
                    path + "/decision", method="POST", payload=opposite,
                )
                self.assertEqual(status, 409)
                run_id = created["run"]["run_id"]
                status, _ = self._request(
                    f"/runs/{run_id}/publish", method="POST", payload={
                        "approval_id": decided["decision"]["event_id"],
                        "patch_hash": payload["patch_hash"],
                        "title": "Must not publish", "body": "",
                        "idempotency_key": "http-demo-never-publish",
                    },
                )
                self.assertEqual(status, 503)  # Disabled auth cannot authorize publication.
                _, unchanged = self._request(path)
                event_types = [event["event_type"] for event in unchanged["events"]]
                self.assertEqual(event_types.count("offline_demo_review_decided"), 1)
                self.assertNotIn("approval_granted", event_types)
                self.assertNotIn("publication_requested", event_types)

    def test_http_rejects_invalid_and_stale_review_evidence_without_writing(self) -> None:
        created = self._create_demo()
        path = "/demo/runs/" + created["run"]["run_id"]
        payload = self._decision(created, "approved")
        for field, value, expected in (
            ("patch_hash", "f" * 64, 409),
            ("evaluation_verdict_hash", "e" * 64, 409),
            ("decision", "publish", 422),
            ("decision_key", "short", 422),
            ("actor_id", "forged:reviewer", 422),
        ):
            with self.subTest(field=field):
                status, _ = self._request(
                    path + "/decision", method="POST",
                    payload=dict(payload, **{field: value}),
                )
                self.assertEqual(status, expected)
        _, unchanged = self._request(path)
        self.assertIsNone(unchanged["decision"])
        self.assertEqual(unchanged["events"], created["events"])
        status, _ = self._request("/demo/runs/nonexistent")
        self.assertEqual(status, 404)

    def test_tampered_or_missing_persisted_patch_fails_closed_after_restart(self) -> None:
        created = self._create_demo()
        path = "/demo/runs/" + created["run"]["run_id"]
        checksum = created["review"]["patch_hash"]
        artifact = self.root / "artifacts" / "sha256" / checksum[:2] / checksum
        content = artifact.read_bytes()
        self._stop_server()
        artifact.write_bytes(b"x" * len(content))  # Same size, incorrect content digest.
        self._start_server()
        status, response = self._request(path)
        self.assertEqual(status, 409, response)
        self.assertEqual(response["detail"], "patch evidence failed integrity validation")
        status, _ = self._request(
            path + "/decision", method="POST",
            payload=self._decision(created, "approved"),
        )
        self.assertEqual(status, 409)
        artifact.unlink()
        status, _ = self._request(path)
        self.assertEqual(status, 409)
        artifact.write_bytes(content)
        status, restored = self._request(path)
        self.assertEqual(status, 200, restored)
        self.assertIsNone(restored["decision"])
        self.assertEqual(restored["events"], created["events"])


if __name__ == "__main__":
    unittest.main()
