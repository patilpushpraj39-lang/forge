from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from .models import GitHubChange, PullRequestResult


class PublisherError(RuntimeError):
    code = "publisher_error"
    permanent = True


class PublisherTransientError(PublisherError):
    code = "github_transient_error"
    permanent = False


class PublisherPermissionError(PublisherError):
    code = "github_permission_denied"


class PublisherStaleBaseError(PublisherError):
    code = "stale_base_commit"


class PublisherConflictError(PublisherError):
    code = "github_publication_conflict"


@dataclass(frozen=True)
class GitHubResponse:
    status: int
    data: Any
    headers: Mapping[str, str]


class InstallationTokenProvider(Protocol):
    def token_for(self, installation_id: int) -> str: ...


class GitHubTransport(Protocol):
    def request(
        self,
        method: str,
        path: str,
        token: str,
        *,
        body: dict[str, Any] | None = None,
        query: dict[str, str] | None = None,
    ) -> GitHubResponse: ...


class StaticInstallationTokenProvider:
    """Development adapter; production supplies short-lived App tokens."""

    def __init__(self, token: str) -> None:
        if not token:
            raise ValueError("GitHub token is required")
        self.token = token

    def token_for(self, installation_id: int) -> str:
        if installation_id <= 0:
            raise ValueError("installation_id must be positive")
        return self.token


class GitHubAppInstallationTokenProvider:
    """Mints and caches least-privilege GitHub App installation tokens."""

    def __init__(
        self,
        client_id: str,
        private_key_pem: str,
        transport: GitHubTransport | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
        signer: Callable[[bytes, bytes], bytes] | None = None,
    ) -> None:
        if not client_id or not private_key_pem:
            raise ValueError("GitHub App client ID and private key are required")
        self.client_id = client_id
        self.private_key = private_key_pem.encode("utf-8")
        self.transport = transport or UrllibGitHubTransport()
        self.clock = clock or (lambda: datetime.now(UTC))
        self.signer = signer or _rsa_sha256_sign
        self.cache: dict[tuple[int, str], tuple[str, datetime]] = {}

    def token_for(self, installation_id: int) -> str:
        return self._token_for_permissions(
            installation_id,
            {
                "contents": "write",
                "pull_requests": "write",
                "metadata": "read",
            },
            "write",
        )

    def read_token_for(self, installation_id: int) -> str:
        return self._token_for_permissions(
            installation_id,
            {"contents": "read", "metadata": "read"},
            "read",
        )

    def app_token(self) -> str:
        return self._app_jwt(self.clock())

    def _token_for_permissions(
        self,
        installation_id: int,
        permissions: dict[str, str],
        cache_scope: str,
    ) -> str:
        if installation_id <= 0:
            raise ValueError("installation_id must be positive")
        now = self.clock()
        cache_key = (installation_id, cache_scope)
        cached = self.cache.get(cache_key)
        if cached is not None and cached[1] - now > timedelta(minutes=5):
            return cached[0]
        jwt = self._app_jwt(now)
        response = self.transport.request(
            "POST",
            f"/app/installations/{installation_id}/access_tokens",
            jwt,
            body={"permissions": permissions},
        )
        if response.status != 201:
            message = _error_message(response.data)
            if response.status >= 500 or response.status == 429:
                raise PublisherTransientError(message)
            raise PublisherPermissionError(message)
        if not isinstance(response.data, dict):
            raise PublisherTransientError("installation token response is invalid")
        token = response.data.get("token")
        expires_text = response.data.get("expires_at")
        if not isinstance(token, str) or not token or not isinstance(
            expires_text, str
        ):
            raise PublisherTransientError(
                "installation token response is incomplete"
            )
        try:
            expires_at = datetime.fromisoformat(expires_text.replace("Z", "+00:00"))
        except ValueError as error:
            raise PublisherTransientError(
                "installation token expiry is invalid"
            ) from error
        self.cache[cache_key] = (token, expires_at)
        return token

    def _app_jwt(self, now: datetime) -> str:
        issued_at = int(now.timestamp()) - 60
        expires_at = int(now.timestamp()) + 9 * 60
        header = _base64url(b'{"alg":"RS256","typ":"JWT"}')
        payload = _base64url(
            json.dumps(
                {"iat": issued_at, "exp": expires_at, "iss": self.client_id},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        signing_input = f"{header}.{payload}".encode("ascii")
        signature = _base64url(self.signer(self.private_key, signing_input))
        return f"{header}.{payload}.{signature}"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class UrllibGitHubTransport:
    def __init__(
        self,
        base_url: str = "https://api.github.com",
        api_version: str = "2026-03-10",
        timeout_seconds: float = 15,
        max_response_bytes: int = 10_000_000,
    ) -> None:
        parsed = urllib.parse.urlparse(base_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("GitHub API base URL must use HTTPS")
        self.base_url = base_url.rstrip("/")
        self.api_version = api_version
        self.timeout_seconds = timeout_seconds
        if max_response_bytes <= 0 or max_response_bytes > 20_000_000:
            raise ValueError("GitHub response limit is invalid")
        self.max_response_bytes = max_response_bytes
        self.opener = urllib.request.build_opener(_NoRedirect())

    def request(
        self,
        method: str,
        path: str,
        token: str,
        *,
        body: dict[str, Any] | None = None,
        query: dict[str, str] | None = None,
    ) -> GitHubResponse:
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("GitHub API path must be relative")
        url = self.base_url + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        payload = None
        if body is not None:
            payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=payload,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": self.api_version,
                "User-Agent": "forge-publisher/0.1",
                "Content-Type": "application/json",
            },
        )
        try:
            response = self.opener.open(request, timeout=self.timeout_seconds)
        except urllib.error.HTTPError as error:
            response = error
        except (OSError, TimeoutError) as error:
            raise PublisherTransientError(str(error)) from error
        with response:
            content = response.read(self.max_response_bytes + 1)
            if len(content) > self.max_response_bytes:
                raise PublisherTransientError("GitHub response exceeded limit")
            try:
                data = json.loads(content) if content else None
            except json.JSONDecodeError as error:
                raise PublisherTransientError("GitHub returned invalid JSON") from error
            return GitHubResponse(
                int(response.status), data, dict(response.headers.items())
            )


class GitHubPullRequestPublisher:
    def __init__(
        self,
        tokens: InstallationTokenProvider,
        transport: GitHubTransport | None = None,
    ) -> None:
        self.tokens = tokens
        self.transport = transport or UrllibGitHubTransport()

    def prepare_branch(
        self,
        publication: dict[str, Any],
        changes: tuple[GitHubChange, ...],
        heartbeat: Callable[[], None],
    ) -> str:
        token = self.tokens.token_for(int(publication["installation_id"]))
        owner = str(publication["repository_owner"])
        repo = str(publication["repository_name"])
        base_ref = str(publication["base_ref"])
        base_sha = str(publication["base_sha"])
        self._require_write_access(owner, repo, token)
        self._require_base(owner, repo, base_ref, base_sha, token)
        heartbeat()
        commit = self._json(
            "GET", f"/repos/{_q(owner)}/{_q(repo)}/git/commits/{_q(base_sha)}", token
        )
        tree_sha = _nested_string(commit, "tree", "sha")
        entries: list[dict[str, Any]] = []
        for change in changes:
            heartbeat()
            if change.content is None:
                entries.append(
                    {"path": change.path, "mode": "100644", "type": "blob", "sha": None}
                )
                continue
            blob = self._json(
                "POST",
                f"/repos/{_q(owner)}/{_q(repo)}/git/blobs",
                token,
                body={
                    "content": base64.b64encode(change.content).decode("ascii"),
                    "encoding": "base64",
                },
                expected=(201,),
            )
            entries.append(
                {
                    "path": change.path,
                    "mode": "100755" if change.executable else "100644",
                    "type": "blob",
                    "sha": _string(blob, "sha"),
                }
            )
        tree = self._json(
            "POST",
            f"/repos/{_q(owner)}/{_q(repo)}/git/trees",
            token,
            body={"base_tree": tree_sha, "tree": entries},
            expected=(201,),
        )
        timestamp = str(publication["created_at"])
        commit_message = (
            f"Forge: {publication['pull_request_title']}\n\n"
            f"Forge-Publication-ID: {publication['publication_id']}\n"
            f"Forge-Patch-SHA256: {publication['patch_hash']}"
        )
        created = self._json(
            "POST",
            f"/repos/{_q(owner)}/{_q(repo)}/git/commits",
            token,
            body={
                "message": commit_message,
                "tree": _string(tree, "sha"),
                "parents": [base_sha],
                "author": {
                    "name": "Forge",
                    "email": "forge@users.noreply.github.com",
                    "date": timestamp,
                },
                "committer": {
                    "name": "Forge",
                    "email": "forge@users.noreply.github.com",
                    "date": timestamp,
                },
            },
            expected=(201,),
        )
        head_sha = _string(created, "sha")
        branch_name = str(publication["branch_name"])
        branch = self._request(
            "GET",
            f"/repos/{_q(owner)}/{_q(repo)}/git/ref/heads/{_q(branch_name)}",
            token,
            expected=(200, 404),
        )
        if branch.status == 404:
            self._json(
                "POST",
                f"/repos/{_q(owner)}/{_q(repo)}/git/refs",
                token,
                body={"ref": f"refs/heads/{branch_name}", "sha": head_sha},
                expected=(201,),
            )
        elif _nested_string(branch.data, "object", "sha") != head_sha:
            raise PublisherConflictError(
                "deterministic Forge branch points to a different commit"
            )
        return head_sha

    def ensure_pull_request(
        self,
        publication: dict[str, Any],
        head_sha: str,
        heartbeat: Callable[[], None],
    ) -> PullRequestResult:
        token = self.tokens.token_for(int(publication["installation_id"]))
        owner = str(publication["repository_owner"])
        repo = str(publication["repository_name"])
        base_ref = str(publication["base_ref"])
        base_sha = str(publication["base_sha"])
        self._require_write_access(owner, repo, token)
        self._require_base(owner, repo, base_ref, base_sha, token)
        heartbeat()
        branch_name = str(publication["branch_name"])
        existing = self._json(
            "GET",
            f"/repos/{_q(owner)}/{_q(repo)}/pulls",
            token,
            query={"state": "all", "head": f"{owner}:{branch_name}"},
        )
        if not isinstance(existing, list):
            raise PublisherTransientError("GitHub pull list has invalid shape")
        for item in existing:
            if _nested_string(item, "head", "sha") == head_sha:
                return _pull_result(item, head_sha)
        if existing:
            raise PublisherConflictError(
                "Forge branch already has a pull request for another commit"
            )
        created = self._json(
            "POST",
            f"/repos/{_q(owner)}/{_q(repo)}/pulls",
            token,
            body={
                "title": publication["pull_request_title"],
                "body": publication["pull_request_body"],
                "head": branch_name,
                "base": base_ref,
            },
            expected=(201,),
        )
        return _pull_result(created, head_sha)

    def _require_write_access(self, owner: str, repo: str, token: str) -> None:
        repository = self._json(
            "GET", f"/repos/{_q(owner)}/{_q(repo)}", token
        )
        permissions = repository.get("permissions")
        if (
            not isinstance(permissions, dict)
            or permissions.get("push") is not True
            or repository.get("archived") is True
            or repository.get("disabled") is True
        ):
            raise PublisherPermissionError(
                "GitHub installation lacks repository write permission"
            )

    def _require_base(
        self, owner: str, repo: str, base_ref: str, base_sha: str, token: str
    ) -> None:
        reference = self._json(
            "GET",
            f"/repos/{_q(owner)}/{_q(repo)}/git/ref/heads/{_q(base_ref)}",
            token,
        )
        if _nested_string(reference, "object", "sha") != base_sha:
            raise PublisherStaleBaseError(
                "repository base branch changed after snapshot approval"
            )

    def _json(
        self,
        method: str,
        path: str,
        token: str,
        *,
        body: dict[str, Any] | None = None,
        query: dict[str, str] | None = None,
        expected: tuple[int, ...] = (200,),
    ) -> Any:
        return self._request(
            method, path, token, body=body, query=query, expected=expected
        ).data

    def _request(
        self,
        method: str,
        path: str,
        token: str,
        *,
        body: dict[str, Any] | None = None,
        query: dict[str, str] | None = None,
        expected: tuple[int, ...],
    ) -> GitHubResponse:
        response = self.transport.request(
            method, path, token, body=body, query=query
        )
        if response.status in expected:
            return response
        message = _error_message(response.data)
        if response.status == 403 and (
            response.headers.get("Retry-After")
            or response.headers.get("X-RateLimit-Remaining") == "0"
        ):
            raise PublisherTransientError(message)
        if response.status == 401 or response.status == 403:
            raise PublisherPermissionError(message)
        if response.status == 429 or response.status >= 500:
            raise PublisherTransientError(message)
        raise PublisherConflictError(
            f"GitHub API returned {response.status}: {message}"
        )


def _q(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def _string(value: Any, key: str) -> str:
    if not isinstance(value, dict) or not isinstance(value.get(key), str):
        raise PublisherTransientError(f"GitHub response is missing {key}")
    return value[key]


def _nested_string(value: Any, outer: str, inner: str) -> str:
    if not isinstance(value, dict) or not isinstance(value.get(outer), dict):
        raise PublisherTransientError(f"GitHub response is missing {outer}.{inner}")
    return _string(value[outer], inner)


def _pull_result(value: Any, expected_head: str) -> PullRequestResult:
    if not isinstance(value, dict):
        raise PublisherTransientError("GitHub pull request has invalid shape")
    number = value.get("number")
    url = value.get("html_url")
    actual_head = _nested_string(value, "head", "sha")
    if not isinstance(number, int) or not isinstance(url, str):
        raise PublisherTransientError("GitHub pull request evidence is incomplete")
    if actual_head != expected_head:
        raise PublisherConflictError("pull request head does not match Forge commit")
    return PullRequestResult(number, url, actual_head)


def _error_message(value: Any) -> str:
    if isinstance(value, dict) and isinstance(value.get("message"), str):
        return value["message"][:512]
    return "unexpected GitHub response"


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _rsa_sha256_sign(private_key_pem: bytes, payload: bytes) -> bytes:
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
    except ImportError as error:
        raise RuntimeError(
            "GitHub App authentication requires the cryptography dependency"
        ) from error
    private_key = serialization.load_pem_private_key(
        private_key_pem, password=None
    )
    return private_key.sign(payload, padding.PKCS1v15(), hashes.SHA256())
