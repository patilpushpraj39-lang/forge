from __future__ import annotations

import base64
import hashlib
import tempfile
import unittest
from pathlib import Path

from forge_publisher import (
    GitHubCatalog,
    GitHubResponse,
    PublisherConflictError,
)


def blob_sha(content: bytes) -> str:
    return hashlib.sha1(
        f"blob {len(content)}\0".encode("ascii") + content
    ).hexdigest()


class FakeTokens:
    def __init__(self) -> None:
        self.read_installations: list[int] = []

    def app_token(self) -> str:
        return "app-jwt"

    def read_token_for(self, installation_id: int) -> str:
        self.read_installations.append(installation_id)
        return f"read-{installation_id}"


class CatalogTransport:
    def __init__(self, *, truncated: bool = False, unsafe: bool = False) -> None:
        self.requests: list[tuple[str, str, str, dict | None]] = []
        self.contents = {
            "README.md": b"immutable source\n",
            "src/main.py": b"print('forge')\n",
        }
        self.truncated = truncated
        self.unsafe = unsafe

    def request(self, method, path, token, *, body=None, query=None):
        self.requests.append((method, path, token, query))
        if path == "/app/installations":
            return GitHubResponse(
                200,
                [
                    {
                        "id": 42,
                        "account": {"login": "octo-org", "type": "Organization"},
                        "repository_selection": "selected",
                        "suspended_at": None,
                    },
                    {
                        "id": 99,
                        "account": {"login": "paused", "type": "Organization"},
                        "repository_selection": "all",
                        "suspended_at": "2026-01-01T00:00:00Z",
                    },
                ],
                {},
            )
        if path == "/installation/repositories":
            return GitHubResponse(
                200,
                {
                    "repositories": [
                        {
                            "owner": {"login": "octo-org"},
                            "name": "fixture",
                            "full_name": "octo-org/fixture",
                            "default_branch": "main",
                            "private": True,
                            "archived": False,
                            "disabled": False,
                        },
                        {
                            "owner": {"login": "octo-org"},
                            "name": "archived",
                            "full_name": "octo-org/archived",
                            "default_branch": "main",
                            "private": False,
                            "archived": True,
                            "disabled": False,
                        },
                    ]
                },
                {},
            )
        if path.endswith("/git/ref/heads/main"):
            return GitHubResponse(200, {"object": {"sha": "a" * 40}}, {})
        if path.endswith(f"/git/commits/{'a' * 40}"):
            return GitHubResponse(200, {"tree": {"sha": "b" * 40}}, {})
        if path.endswith(f"/git/trees/{'b' * 40}"):
            entries = []
            for repository_path, content in self.contents.items():
                entries.append(
                    {
                        "path": repository_path,
                        "mode": "100755" if repository_path.endswith("main.py") else "100644",
                        "type": "blob",
                        "size": len(content),
                        "sha": blob_sha(content),
                    }
                )
            if self.unsafe:
                entries.append(
                    {
                        "path": "unsafe-link",
                        "mode": "120000",
                        "type": "blob",
                        "size": 6,
                        "sha": "c" * 40,
                    }
                )
            return GitHubResponse(
                200,
                {"sha": "b" * 40, "truncated": self.truncated, "tree": entries},
                {},
            )
        if "/git/blobs/" in path:
            sha = path.rsplit("/", 1)[-1]
            content = next(
                value for value in self.contents.values() if blob_sha(value) == sha
            )
            return GitHubResponse(
                200,
                {
                    "sha": sha,
                    "size": len(content),
                    "encoding": "base64",
                    "content": base64.b64encode(content).decode("ascii"),
                },
                {},
            )
        raise AssertionError(f"unexpected GitHub request: {method} {path}")


class GitHubCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.tokens = FakeTokens()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_catalog_lists_only_active_usable_targets(self) -> None:
        catalog = GitHubCatalog(self.tokens, CatalogTransport())

        installations = catalog.list_installations()
        repositories = catalog.list_repositories(42)

        self.assertEqual([item.installation_id for item in installations], [42])
        self.assertEqual([item.full_name for item in repositories], ["octo-org/fixture"])
        self.assertEqual(self.tokens.read_installations, [42])

    def test_materialization_is_bound_to_exact_commit_and_verified_blobs(self) -> None:
        transport = CatalogTransport()
        catalog = GitHubCatalog(self.tokens, transport)
        destination = self.root / "checkout"

        result = catalog.materialize_selected_repository(
            42, "octo-org", "fixture", "main", destination
        )

        self.assertEqual(result.base_sha, "a" * 40)
        self.assertEqual(result.tree_sha, "b" * 40)
        self.assertEqual(result.file_count, 2)
        self.assertEqual((destination / "README.md").read_bytes(), b"immutable source\n")
        self.assertEqual((destination / "src" / "main.py").read_bytes(), b"print('forge')\n")
        self.assertTrue(
            all(token == "read-42" for _, path, token, _ in transport.requests if path != "/app/installations")
        )

    def test_truncated_tree_and_unsupported_links_are_rejected(self) -> None:
        for transport in (CatalogTransport(truncated=True), CatalogTransport(unsafe=True)):
            destination = self.root / f"checkout-{id(transport)}"
            with self.assertRaises(PublisherConflictError):
                GitHubCatalog(self.tokens, transport).materialize_selected_repository(
                    42, "octo-org", "fixture", "main", destination
                )
            self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
