from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path

from forge_sandbox_controller import (
    ArtifactNotFoundError,
    S3ArtifactStore,
)


ROOT = Path(__file__).resolve().parents[1]


class _FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], dict[str, object]] = {}
        self.last_put: dict[str, object] | None = None

    def put_object(self, **request):
        body = request["Body"]
        content = body if isinstance(body, bytes) else body.read()
        stored = {**request, "Body": content}
        self.objects[(request["Bucket"], request["Key"])] = stored
        self.last_put = stored
        return {"ETag": "fixture"}

    def get_object(self, *, Bucket: str, Key: str):
        stored = self.objects[(Bucket, Key)]
        return {
            "Body": io.BytesIO(stored["Body"]),
            "ContentLength": len(stored["Body"]),
            "Metadata": dict(stored["Metadata"]),
        }


class S3ArtifactStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = _FakeS3Client()
        self.store = S3ArtifactStore(
            "forge-test",
            prefix="tenant-a/artifacts",
            client=self.client,
        )

    def test_bytes_are_content_addressed_encrypted_and_verified(self) -> None:
        reference = self.store.put_bytes(b"forge-artifact", "text/plain")
        content = self.store.read_bytes(reference, max_bytes=100)
        assert self.client.last_put is not None

        self.assertEqual(content, b"forge-artifact")
        self.assertTrue(
            str(self.client.last_put["Key"]).endswith(reference.sha256)
        )
        self.assertEqual(
            self.client.last_put["ServerSideEncryption"], "AES256"
        )
        self.assertEqual(
            self.client.last_put["Metadata"]["forge-sha256"],
            reference.sha256,
        )
        self.assertIn("forge-artifact=true", self.client.last_put["Tagging"])

    def test_file_upload_read_limit_and_tamper_detection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / "snapshot.tar"
            source.write_bytes(b"snapshot-content")
            reference = self.store.put_file(
                source, "application/vnd.forge.snapshot+tar"
            )

        with self.assertRaises(ValueError):
            self.store.read_bytes(reference, max_bytes=1)

        key = ("forge-test", self.client.last_put["Key"])
        self.client.objects[key]["Body"] = b"tampered-content"
        with self.assertRaises(ArtifactNotFoundError):
            self.store.read_bytes(reference, max_bytes=100)

    def test_lifecycle_configuration_scopes_standard_artifacts(self) -> None:
        configuration = self.store.lifecycle_configuration(
            expiration_days=30
        )
        rule = configuration["Rules"][0]

        self.assertEqual(rule["Status"], "Enabled")
        self.assertEqual(
            rule["Filter"]["Prefix"],
            "tenant-a/artifacts/sha256/",
        )
        self.assertEqual(rule["Expiration"]["Days"], 30)
        self.assertEqual(
            rule["AbortIncompleteMultipartUpload"]["DaysAfterInitiation"],
            1,
        )

    def test_lifecycle_configuration_matches_retention_policy(self) -> None:
        policy = json.loads(
            (
                ROOT
                / "infra"
                / "policies"
                / "artifact-retention-v1.json"
            ).read_text(encoding="utf-8")
        )
        standard = policy["standard"]
        configuration = self.store.lifecycle_configuration(
            expiration_days=standard["expiration_days"]
        )
        rule = configuration["Rules"][0]

        self.assertEqual(
            rule["Expiration"]["Days"], standard["expiration_days"]
        )
        self.assertEqual(
            rule["AbortIncompleteMultipartUpload"][
                "DaysAfterInitiation"
            ],
            standard["abort_incomplete_multipart_upload_days"],
        )


if __name__ == "__main__":
    unittest.main()
