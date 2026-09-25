from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from typing import Any

from .protocol import ArtifactNotFoundError, ArtifactRef


class S3ArtifactStore:
    """Content-addressed artifact storage for S3-compatible object stores."""

    def __init__(
        self,
        bucket: str,
        *,
        prefix: str = "forge/artifacts",
        endpoint_url: str | None = None,
        region_name: str | None = None,
        client: Any | None = None,
    ) -> None:
        if not bucket:
            raise ValueError("artifact bucket is required")
        normalized_prefix = prefix.strip("/")
        if not normalized_prefix or ".." in normalized_prefix.split("/"):
            raise ValueError("artifact prefix is invalid")
        if client is None:
            try:
                import boto3
            except ImportError as error:
                raise RuntimeError(
                    "install forge-sandbox-controller[s3] for S3 artifacts"
                ) from error
            client = boto3.client(
                "s3",
                endpoint_url=endpoint_url,
                region_name=region_name,
            )
        self.bucket = bucket
        self.prefix = normalized_prefix
        self.client = client

    def put_bytes(self, content: bytes, media_type: str) -> ArtifactRef:
        digest = hashlib.sha256(content).hexdigest()
        reference = ArtifactRef(digest, len(content), media_type)
        self._put(reference, content)
        return reference

    def put_file(self, source: Path, media_type: str) -> ArtifactRef:
        digest = hashlib.sha256()
        size = 0
        with source.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
                size += len(block)
        reference = ArtifactRef(digest.hexdigest(), size, media_type)
        with source.open("rb") as stream:
            self._put(reference, stream)
        return reference

    def read_bytes(self, reference: ArtifactRef, max_bytes: int) -> bytes:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        if reference.size_bytes > max_bytes:
            raise ValueError("artifact exceeds read limit")
        try:
            response = self.client.get_object(
                Bucket=self.bucket,
                Key=self._key(reference.sha256),
            )
        except Exception as error:
            raise ArtifactNotFoundError(reference.sha256) from error
        body = response["Body"]
        try:
            content = body.read(reference.size_bytes + 1)
        finally:
            close = getattr(body, "close", None)
            if close is not None:
                close()
        if len(content) != reference.size_bytes:
            raise ArtifactNotFoundError("artifact size does not match reference")
        if hashlib.sha256(content).hexdigest() != reference.sha256:
            raise ArtifactNotFoundError("artifact checksum does not match reference")
        metadata = response.get("Metadata", {})
        if metadata.get("forge-sha256", reference.sha256) != reference.sha256:
            raise ArtifactNotFoundError("artifact metadata checksum mismatch")
        return content

    def lifecycle_configuration(self, expiration_days: int = 30) -> dict[str, Any]:
        if expiration_days <= 0:
            raise ValueError("expiration_days must be positive")
        return {
            "Rules": [
                {
                    "ID": "forge-standard-artifact-expiration",
                    "Status": "Enabled",
                    "Filter": {"Prefix": f"{self.prefix}/sha256/"},
                    "Expiration": {"Days": expiration_days},
                    "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 1},
                }
            ]
        }

    def _put(self, reference: ArtifactRef, body: Any) -> None:
        checksum = base64.b64encode(
            bytes.fromhex(reference.sha256)
        ).decode("ascii")
        self.client.put_object(
            Bucket=self.bucket,
            Key=self._key(reference.sha256),
            Body=body,
            ContentLength=reference.size_bytes,
            ContentType=reference.media_type,
            ChecksumSHA256=checksum,
            Metadata={
                "forge-sha256": reference.sha256,
                "forge-size": str(reference.size_bytes),
            },
            ServerSideEncryption="AES256",
            Tagging="forge-artifact=true&retention=standard",
        )

    def _key(self, checksum: str) -> str:
        if len(checksum) != 64 or any(
            character not in "0123456789abcdef" for character in checksum
        ):
            raise ValueError("invalid SHA-256 checksum")
        return f"{self.prefix}/sha256/{checksum[:2]}/{checksum}"
