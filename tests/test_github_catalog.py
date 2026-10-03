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
    PublisherTransientError,
)
from forge_publisher.catalog import _decode_blob


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
    def __init__(
        self, *, truncated: bool = False, unsafe: bool = False,
        blob_line_ending: str | None = None,
    ) -> None:
        self.requests: list[tuple[str, str, str, dict | None]] = []
        self.contents = {
            "README.md": b"immutable source\n",
            "src/main.py": b"print('forge')\n",
        }
        self.truncated = truncated
        self.unsafe = unsafe
        self.blob_line_ending = blob_line_ending

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
            encoded = base64.b64encode(content).decode("ascii")
            if self.blob_line_ending is not None:
                encoded = self.blob_line_ending.join(
                    encoded[index:index + 60] for index in range(0, len(encoded), 60)
                ) + self.blob_line_ending
            return GitHubResponse(
                200,
                {
                    "sha": sha,
                    "size": len(content),
                    "encoding": "base64",
                    "content": encoded,
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

    def test_materialization_accepts_github_wrapping_and_trailing_newline(self) -> None:
        # A 60-byte blob produces the same two wrapped lines seen in the pilot.
        for index, line_ending in enumerate(("\n", "\r\n")):
            with self.subTest(line_ending=repr(line_ending)):
                transport = CatalogTransport(blob_line_ending=line_ending)
                transport.contents["README.md"] = b"source" * 10
                destination = self.root / f"wrapped-{index}"
                result = GitHubCatalog(self.tokens, transport).materialize_selected_repository(
                    42, "octo-org", "fixture", "main", destination
                )
                self.assertEqual(result.file_count, 2)
                self.assertEqual((destination / "README.md").read_bytes(), b"source" * 10)


class BlobDecodingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.content = b"verified source\n"
        self.sha = blob_sha(self.content)
        self.blob = {
            "encoding": "base64",
            "content": base64.b64encode(self.content).decode("ascii"),
            "sha": self.sha,
            "size": len(self.content),
        }

    def decode(self, blob: dict, *, sha: str | None = None) -> bytes:
        return _decode_blob(
            blob, self.sha if sha is None else sha, len(self.content),
        )

    def test_accepts_only_ascii_formatting_whitespace(self) -> None:
        encoded = self.blob["content"]
        wrapped = " \t\r\n" + encoded[:4] + " \t\r\n" + encoded[4:] + "\n"
        self.assertEqual(self.decode({**self.blob, "content": wrapped}), self.content)

    def test_malformed_base64_and_non_ascii_whitespace_are_rejected(self) -> None:
        encoded = self.blob["content"]
        for invalid in (
            encoded + "!", encoded[:-1], "%%%%", encoded + "\u00a0",
            encoded + "\u2003", encoded + "\v", encoded + "\f",
        ):
            with self.subTest(content=repr(invalid)):
                with self.assertRaisesRegex(PublisherTransientError, "base64 is invalid"):
                    self.decode({**self.blob, "content": invalid})

    def test_blob_metadata_must_still_match_tree(self) -> None:
        for change in (
            {"sha": "0" * 40}, {"size": len(self.content) + 1}, {"content": None},
        ):
            with self.subTest(change=change):
                with self.assertRaisesRegex(PublisherTransientError, "evidence does not match tree"):
                    self.decode({**self.blob, **change})
        with self.assertRaisesRegex(PublisherTransientError, "invalid encoding"):
            self.decode({**self.blob, "encoding": "utf-8"})

    def test_decoded_size_mismatch_is_still_rejected(self) -> None:
        changed = {
            **self.blob,
            "content": "\n" + base64.b64encode(self.content + b"x").decode("ascii") + "\n",
        }
        with self.assertRaisesRegex(PublisherTransientError, "size does not match tree"):
            self.decode(changed)

    def test_decoded_hash_mismatch_is_still_rejected(self) -> None:
        changed = {
            **self.blob,
            "content": "\n" + base64.b64encode(b"x" * len(self.content)).decode("ascii") + "\n",
        }
        with self.assertRaisesRegex(PublisherTransientError, "hash validation failed"):
            self.decode(changed)

    def test_sha256_git_objects_remain_supported(self) -> None:
        sha = hashlib.sha256(
            f"blob {len(self.content)}\0".encode("ascii") + self.content
        ).hexdigest()
        wrapped = {**self.blob, "sha": sha, "content": self.blob["content"] + "\n"}
        self.assertEqual(self.decode(wrapped, sha=sha), self.content)


if __name__ == "__main__":
    unittest.main()
