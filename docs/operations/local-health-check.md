# Check whether local Forge is running

Open a new terminal in the Forge repository. Keep the website and backend
server terminals open, then run:

```powershell
.\.venv\Scripts\python.exe scripts/check-local.py
```

When both services respond, the first two lines are:

```text
OK - Forge website is responding.
OK - Forge backend is responding.
```

Open `http://localhost:3000/demo` to use the free offline workflow. A successful
health check confirms serving only: it does not verify sign-in, reviewer access,
run execution, GitHub configuration, sandbox isolation, or deployment readiness.

## If a check says CHECK

- Website: inspect the terminal running Next.js on port 3000.
- Backend: inspect the terminal running the API on port 8000.
- Connection refused: the service may be stopped or using a different port.
- Port already in use: do not start a second server or kill an unknown process.
  Inspect the existing server terminal first.
- Unexpected page or response: another application or an unhealthy service may
  be using that port. The command does not count any HTTP 200 as Forge success.

The command starts no server, installs nothing, and does not modify settings or
read environment files, passwords, API keys or session tokens. It sends only two
unauthenticated GET requests to the local website and health endpoint. System
HTTP proxies are disabled for these requests, redirects are not followed, response
reads are size-bounded, and each request has a three-second socket timeout.
Socket timeout is not a strict total wall-clock deadline. Response bodies and
raw network errors are never printed. It makes no model or GitHub requests.

## Automation and alternate ports

```powershell
.\.venv\Scripts\python.exe scripts/check-local.py --json
.\.venv\Scripts\python.exe scripts/check-local.py --web-port 3001 --api-port 8001
```

Exit code 0 means both expected services responded; 1 means at least one failed.
Invalid arguments return 2. Only port numbers can be changed; arbitrary remote
URLs are not accepted. Alternate ports do not change the running servers or Clerk
configuration. Use `localhost` consistently for the website's host and authorized
origin when configuring Clerk.

## Local versus public availability

These local servers still depend on your PC being awake and their processes
running. Turning off just the monitor does not stop them; shutting down or sleeping
the PC does. `localhost` on another user's computer refers to their own computer,
not yours. A separately verified hosted deployment is required for independent
public availability. This command does not deploy Forge or expose your PC.
