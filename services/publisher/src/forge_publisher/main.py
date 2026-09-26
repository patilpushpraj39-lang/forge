from __future__ import annotations

import argparse
import os
import socket
import time
from pathlib import Path

from forge_agent_core import create_run_store
from forge_sandbox_controller import create_sandbox_controller

from .github import (
    GitHubAppInstallationTokenProvider,
    GitHubPullRequestPublisher,
    UrllibGitHubTransport,
)
from .service import run_once


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--database",
        default=os.environ.get("FORGE_DATABASE_PATH", ".state/forge.db"),
    )
    parser.add_argument(
        "--database-url", default=os.environ.get("FORGE_DATABASE_URL")
    )
    parser.add_argument(
        "--migrations",
        default=os.environ.get("FORGE_MIGRATIONS_PATH", "db/migrations"),
    )
    parser.add_argument(
        "--github-client-id", default=os.environ.get("FORGE_GITHUB_APP_CLIENT_ID")
    )
    parser.add_argument(
        "--github-private-key",
        default=os.environ.get("FORGE_GITHUB_APP_PRIVATE_KEY_PATH"),
    )
    parser.add_argument("--publisher-id")
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if not args.github_client_id or not args.github_private_key:
        parser.error(
            "publisher requires FORGE_GITHUB_APP_CLIENT_ID and "
            "FORGE_GITHUB_APP_PRIVATE_KEY_PATH"
        )
    if args.poll_seconds <= 0 or args.poll_seconds > 60:
        parser.error("--poll-seconds must be greater than 0 and at most 60")
    private_key_path = Path(args.github_private_key).resolve()
    if not private_key_path.is_file():
        parser.error("GitHub App private key file does not exist")

    store = create_run_store(
        database_url=args.database_url,
        sqlite_path=args.database,
        migrations_path=args.migrations,
    )
    controller = create_sandbox_controller()
    transport = UrllibGitHubTransport()
    tokens = GitHubAppInstallationTokenProvider(
        args.github_client_id,
        private_key_path.read_text(encoding="utf-8"),
        transport,
    )
    github = GitHubPullRequestPublisher(tokens, transport)
    publisher_id = args.publisher_id or f"{socket.gethostname()}:{os.getpid()}"

    def advance() -> bool:
        return run_once(
            store,
            controller,
            github,
            publisher_id=publisher_id,
        )

    try:
        if args.once:
            return 0 if advance() else 2
        while True:
            if not advance():
                time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        return 0
    finally:
        close = getattr(store, "close", None)
        if callable(close):
            close()


if __name__ == "__main__":
    raise SystemExit(main())
