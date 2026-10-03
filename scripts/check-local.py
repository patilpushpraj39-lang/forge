"""Read-only, credential-free checks for the two local Forge services."""
from __future__ import annotations

import argparse
import http.client
import json
import urllib.error
import urllib.request
from typing import Any

MAX_RESPONSE_BYTES = 65_536


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def port_number(value: str) -> int:
    try:
        port = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("Use a port number from 1 to 65535") from None
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("Use a port number from 1 to 65535")
    return port


def probe_service(name: str, port: int, timeout: float = 3) -> dict[str, Any]:
    if name not in {"website", "backend"} or not 1 <= port <= 65535:
        raise ValueError("Unsupported local service or port")
    url = (f"http://localhost:{port}/" if name == "website"
           else f"http://127.0.0.1:{port}/health")
    result: dict[str, Any] = {"service": name, "url": url, "reachable": False}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirects())
    request = urllib.request.Request(url, method="GET")
    try:
        with opener.open(request, timeout=timeout) as response:
            status = response.status
            body = response.read(MAX_RESPONSE_BYTES + 1)
        result["http_status"] = status
        if len(body) > MAX_RESPONSE_BYTES:
            result["message"] = "Response was too large to verify safely."
        elif status != 200:
            result["message"] = "The service did not return a successful response."
        elif name == "website":
            result["reachable"] = b"Forge Run Console" in body
            result["message"] = ("Forge website is responding." if result["reachable"]
                                 else "This port did not return the expected Forge page.")
        else:
            try:
                payload = json.loads(body)
            except (ValueError, UnicodeError):
                payload = None
            result["reachable"] = isinstance(payload, dict) and payload.get("status") == "ok"
            result["message"] = ("Forge backend is responding." if result["reachable"]
                                 else "The backend did not return the expected health response.")
    except urllib.error.HTTPError as error:
        result["http_status"] = error.code
        result["message"] = ("The service redirected; redirects are not followed."
                             if 300 <= error.code < 400
                             else "The service returned an error. Check its server terminal.")
        error.close()
    except (OSError, ValueError, urllib.error.URLError, http.client.HTTPException):
        result["message"] = "Cannot reach this service. Check that its server terminal is running."
    return result


def check_local(web_port: int = 3000, api_port: int = 8000) -> dict[str, Any]:
    checks = [probe_service("website", web_port), probe_service("backend", api_port)]
    return {
        "services_reachable": all(check["reachable"] for check in checks),
        "checks": checks,
        "scope": "Local serving only; sign-in, run execution and deployment readiness are not verified.",
        "model_calls_made": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--web-port", type=port_number, default=3000)
    parser.add_argument("--api-port", type=port_number, default=8000)
    parser.add_argument("--json", action="store_true", help="Print safe machine-readable results")
    args = parser.parse_args(argv)
    report = check_local(args.web_port, args.api_port)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        for check in report["checks"]:
            print(f"{'OK' if check['reachable'] else 'CHECK'} - {check['message']}")
        print(report["scope"])
        print("No API keys are read and no paid AI calls are made.")
        if report["services_reachable"]:
            print(f"Open http://localhost:{args.web_port}/demo for the free offline demo.")
        else:
            print("Keep existing server terminals open; do not start duplicate servers on the same ports.")
    return 0 if report["services_reachable"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
