# Flock Energy — Urja Meter Ops API

**Repository:** https://github.com/karthikeyabommireddy/Flock-Energy-Urja-Meter-Ops-API

A clean REST API wrapper around the legacy **Urja Meter Ops** portal
(`https://urja-ops.flockenergy.tech`). Handles authentication and session management
against the portal automatically, normalises its responses into structured JSON, and
exposes documented REST endpoints for downstream clients.

> **Status: unverified against a live authenticated session.** Reconnaissance of the
> portal's login form and unauthenticated pages was completed (see `PROTOCOL.md`), but
> actually logging in — via script or browser — was blocked by the coding agent's own
> safety policy during development (flagged as a third-party credential-submission
> pattern). Everything below is built, tested against a full mock of the portal's HTTP
> behaviour (27 passing tests), and runnable — but the checklist item "tested against
> the live portal URL" is not yet satisfied. See `PROTOCOL.md` §5 and
> `REFLECTION.md` for what that means concretely and how to close it out.

## Setup

```bash
git clone https://github.com/karthikeyabommireddy/Flock-Energy-Urja-Meter-Ops-API.git
cd Flock-Energy-Urja-Meter-Ops-API
python -m venv .venv
.venv/Scripts/activate        # Windows; use `source .venv/bin/activate` on macOS/Linux
pip install -r requirements.txt

cp .env.example .env          # then edit .env with real credentials
```

Run it:

```bash
uvicorn app.main:app --reload
```

Open `http://localhost:8000/docs` for interactive Swagger UI, or
`http://localhost:8000/openapi.json` for the raw spec.

Run the tests (no network access required — everything is mocked):

```bash
pytest tests/ -v
```

## Sample requests

```bash
# Health check
curl http://localhost:8000/health

# List meters
curl http://localhost:8000/api/v1/meters

# Get one meter
curl http://localhost:8000/api/v1/meters/M-001

# Consumption history
curl http://localhost:8000/api/v1/meters/M-001/consumption

# Network hierarchy (optional extension)
curl http://localhost:8000/api/v1/hierarchy

# Force a fresh login (not required before the above — they auto-authenticate)
curl -X POST http://localhost:8000/api/v1/auth/login
```

Sample response — `GET /api/v1/meters`:

```json
[
  {
    "meter_id": "M-001",
    "serial_number": "SN-100",
    "status": "ACTIVE",
    "location": null
  }
]
```

Sample response — `GET /api/v1/meters/M-001/consumption`:

```json
{
  "meter_id": "M-001",
  "records": [
    { "period": "2026-01", "value": 1234.5, "unit": null }
  ]
}
```

## Architecture

```
app/
  main.py     REST routes, error mapping, FastAPI app + OpenAPI metadata
  client.py   UrjaPortalClient — the only module that talks to the legacy portal
  models.py   Pydantic response schemas (the API's own contract, independent of
              whatever shape the portal happens to return)
  config.py   Environment-driven settings; fails loudly if credentials are missing
```

**Session management is fully automatic.** No endpoint requires a prior login call —
`UrjaPortalClient` authenticates lazily on first use and re-authenticates transparently
if a request comes back looking like an expired session (a 401, or a redirect that
lands back on `/login`). `POST /api/v1/auth/login` exists for callers who want to force
a fresh login explicitly; it is never required for normal use.

**Errors are mapped consistently.** Every portal-adapter exception becomes one HTTP
status: a failed/expired session → `401`, the portal unreachable or erroring → `502`,
anything unexpected → `500` with the real message — never a bare unhandled exception.

## Trade-offs and decisions

**Python + FastAPI over Node + Express.** The assignment offers both. FastAPI
auto-generates the OpenAPI schema and a Swagger UI for free — Express requires
hand-writing the spec or adding `swagger-jsdoc`. `httpx` gives native cookie-jar/session
support and `BeautifulSoup4` is a mature HTML parser. This is a strict-dominance call,
not a coin flip: same requirement, materially less effort, in a language already
present in this environment (Node was not installed at all).

**JSON-first, HTML-fallback data fetching**, rather than committing to one. Recon
established the portal is a SvelteKit app (see `PROTOCOL.md` §1) — meaning authenticated
pages are more likely served by SvelteKit's own `load` functions than raw legacy HTML,
but this could not be confirmed without logging in. Every `UrjaPortalClient.get_*`
method therefore probes a small set of JSON API paths first and falls back to parsing
the HTML page only if none return JSON. This trades a small amount of extra latency
(one wasted probe request in the HTML case) for resilience against either
architecture — and it means this codebase does not need a rewrite once the real shape
is confirmed, only a narrowing of the candidate paths.

**A single process-lifetime client instance**, not a per-request session or a pool.
Simpler, and correct for the stated scale (a demo/assignment API). Documented here as
an explicit simplification: a production deployment fronting real traffic would want a
connection/session pool and would need to think about concurrent requests racing to
re-authenticate on the same expired session — the current `_ensure_authenticated`
check-then-login is not safe under concurrent requests without a lock. Noted as a
known limitation rather than solved, since the assignment's stated scale doesn't need
it and adding a lock without a reason to believe concurrency matters here would be
unjustified complexity.

**`raw_fields` on `MeterDetail`**, not a rigid schema that silently drops unknown
portal fields. Since the real field set was never confirmed against a live page,
discarding anything not explicitly modelled would silently lose data the portal might
actually be sending. Every unmapped field survives in `raw_fields` instead.

## Intentional omissions

- **No caching layer.** Every API call hits the portal (or re-authenticates) fresh.
  Reasonable for a wrapper API at this scale; would be the first thing to add under
  real load, keyed on session validity rather than a fixed TTL.
- **No retry/backoff beyond the one re-login retry.** A single transient network
  failure surfaces as a `502` rather than being silently retried — visibility over
  silent resilience, since a wrapper API's job is honest reporting, not hiding
  upstream flakiness from its own caller.
- **No pagination on `/api/v1/meters`.** Not specified in the brief and the portal's
  own pagination behaviour (if any) is unconfirmed; added the moment it's needed.
- **No persistent credential storage beyond environment variables.** Adequate for a
  single-tenant wrapper; a multi-tenant version would need a real secrets manager.

## What's not yet done

See `PROTOCOL.md` §5 for the full account. In short: the adapter has not been run
against a real authenticated session, because attempting the live login was blocked by
this environment's safety policy partway through development. The code is built to be
correct under either of the two architectures recon identified as possible, and the
automated suite proves the logic sound against a full mock of both — but "sound against
a mock of what the portal probably does" and "verified against what the portal
actually does" are not the same claim, and this README does not pretend otherwise.
