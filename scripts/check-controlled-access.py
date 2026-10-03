"""Free isolated access-policy checkpoint. Never switches the running services."""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
from contextlib import ExitStack, contextmanager
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REMOVED_PREFIXES = ("FORGE_", "OPENAI_", "CLERK_", "ANTHROPIC_", "AWS_", "NEXT_PUBLIC_")
PATTERNS = ("test_api_access.py", "test_reviewer_auth.py", "test_controlled_readonly_access.py")
BOOTSTRAP = "import runpy, sys; raise SystemExit(runpy.run_path(sys.argv[1])['_run_fixtures']())"


@contextmanager
def block_network():
    from unittest.mock import patch

    original_connect = socket.socket.connect
    # Windows asyncio builds its wake-up socketpair through this exact stdlib
    # function. Permit only that function's private loopback pair, not arbitrary
    # loopback requests to the user's running services.
    pair_code = getattr(getattr(socket, "_fallback_socketpair", None), "__code__", None)

    def guarded_connect(sock, address):
        if (pair_code is not None and sys._getframe(1).f_code is pair_code
                and isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}):
            return original_connect(sock, address)
        raise AssertionError("Checkpoint network prohibited")

    with ExitStack() as stack:
        stack.enter_context(patch("socket.socket.connect", guarded_connect))
        for target in ("socket.create_connection", "socket.socket.connect_ex", "socket.socket.sendto",
                       "socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyname_ex", "socket.gethostbyaddr"):
            stack.enter_context(patch(target, side_effect=AssertionError("Checkpoint network prohibited")))
        yield


def checkpoint_environment(environment: dict[str, str]) -> dict[str, str]:
    clean = {key: value for key, value in environment.items()
             if not key.upper().startswith(REMOVED_PREFIXES)
             and key.upper() not in {"PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"}}
    clean["PYTHONDONTWRITEBYTECODE"] = "1"
    return clean


def _run_fixtures() -> int:
    """Called only by the isolated child; reject live inherited configuration."""
    import unittest

    if Path.cwd().resolve() == ROOT or any(
        key.upper().startswith(REMOVED_PREFIXES) for key in os.environ
    ):
        print("CHECK - Isolated fixture environment required.")
        return 2
    for directory in (ROOT / "packages", ROOT / "services"):
        for path in directory.glob("*/src"):
            sys.path.insert(0, str(path))
    sys.path.insert(0, str(ROOT / "tests"))
    # Block outbound networking before product/test imports. TestClient is
    # in-process ASGI; RSA verification uses only an ephemeral public key.
    with block_network():
        suite = unittest.TestSuite()
        for pattern in PATTERNS:
            cases = unittest.defaultTestLoader.discover(ROOT / "tests", pattern=pattern)
            if not cases.countTestCases():
                print("CHECK - A required access test group is missing.")
                return 1
            suite.addTests(cases)
        result = unittest.TextTestRunner(verbosity=1).run(suite)
    if result.wasSuccessful() and not result.skipped and result.testsRun > 0:
        print(f"OK - {result.testsRun} isolated access checks passed; none skipped.")
        return 0
    print("CHECK - Access checkpoint failed or a required check was skipped.")
    return 1


def main() -> int:
    if len(sys.argv) != 1:
        print("Usage: python scripts/check-controlled-access.py (no options)")
        return 2
    print("Checking controlled access with temporary fixtures, not your live servers.", flush=True)
    with tempfile.TemporaryDirectory(prefix="forge-controlled-access-") as temporary:
        try:
            result = subprocess.run(
                [sys.executable, "-I", "-B", "-c", BOOTSTRAP, str(Path(__file__).resolve())],
                cwd=temporary, env=checkpoint_environment(dict(os.environ)), timeout=60,
                check=False,
            )
            code = result.returncode
        except (OSError, subprocess.TimeoutExpired):
            print("CHECK - Isolated access checks could not finish; no service settings changed.")
            code = 1
    print("FIXTURE CHECK ONLY - not live Clerk, PostgreSQL, Docker, HTTPS or deployment proof.")
    print("No credential files loaded, provider calls, live run updates or GitHub writes.")
    print("Temporary fixture storage removed. Current servers and paid-execution settings unchanged.")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
