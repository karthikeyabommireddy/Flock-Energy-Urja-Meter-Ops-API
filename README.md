# Flock Energy — Urja Meter Ops API

**Repository:** https://github.com/karthikeyabommireddy/Flock-Energy-Urja-Meter-Ops-API

A clean, read-only REST API over the legacy **Urja Meter Ops** portal
(`https://urja-ops.flockenergy.tech`), and a small web client that uses it. The API logs in
to the portal by itself, keeps the session alive, turns what the portal sends into typed
JSON and serves it from a cache that survives a portal outage. Downstream clients never
talk to the portal.

> **Status: checked against a capture of the portal, not against the portal itself.**
>
> - **The protocol is now observed, not guessed.** I logged in with DevTools open and saved
>   a HAR (55 requests) and two screenshots; `PROTOCOL.md` (v2) is built from them. v1 had
>   to guess the portal's endpoints and HTML. v1.1 makes only the requests the browser was
>   seen making.
> - **This code has still never called the live portal.** The coding agent's safety policy
>   blocked that during development, and the HAR holds a live session, so it is kept out of
>   this repository. What was run: lint, type checks, the OpenAPI export, clean-room installs
>   of both dependency sets (`pip install -r requirements.txt`, `npm ci`), the web client's
>   type check and production build, and an ad hoc replay of the captured responses through
>   the adapter, the routes and the web client. That is a check against the capture, not
>   against the portal.
> - **The pytest suite was not run for this change.** It holds 27 tests written for v1; this
>   change was scoped as development only; and six of the tests pin v1's guessed JSON paths
>   and HTML selectors, so they are stale by design (listed under
>   [What's not yet done](#whats-not-yet-done)). No tests were written for v1.1, and the only
>   edit to the existing ones is that the portal login v1 had put in a fixture was replaced
>   with a synthetic one.

## What changed in v1.1

v1.1 extends v1 in place. It is additive: every v1 route keeps its path and field names, and
what does change in what v1 returned is listed under [Deliberate changes to v1](#deliberate-changes-to-v1).

### Grounded in the real portal

| v1 guessed | v1.1 (observed; `PROTOCOL.md` §6 to §8) |
|---|---|
| Probe a list of candidate JSON paths, then scrape HTML with BeautifulSoup. | Only the requests the UI makes: `GET /portal/meters/search?q=&page=N` (20 meters a page, so the whole fleet is 21 requests: the bulk path), `GET /meters/<id>/__data.json` (nameplate and breadcrumb), `GET /portal/meters/<id>/geo`, `GET /portal/meters/<id>/energy`, `GET /portal/dts?page=N`. No HTML is parsed any more and `beautifulsoup4` is gone from `requirements.txt`. |
| Log in with a classic form post. | The login is a SvelteKit form action that answers JSON. The adapter sends what the UI sends and reads the verdict from the JSON, falling back to v1's "did we land back on `/login`" check. |
| Take consumption values at face value. | The portal sends cumulative register readings as strings, with day-first timestamps and no time zone. They are parsed, converted to UTC (the zone is an assumption, set by `URJA_PORTAL_TIMEZONE`, and every response names it) and `interval_kwh`, the consumption in each half hour, is derived. It is left empty after a missing or unreadable reading, so an interval never silently spans a gap. The register itself stays as `value`. |
| One guessed shape for a meter's detail. | The portal sends two (a list of name/value parameters, and a JSON document inside a string). Both are normalised; a third raises `502 upstream_unparseable` instead of returning a half-empty meter. |
| A guessed hierarchy endpoint. | The portal has none: the network exists only as a breadcrumb on each meter. The tree is rebuilt by merging one breadcrumb per transformer (about 40 requests, not 403) and audited, see below. |

### Hardening

| v1 said | v1.1 |
|---|---|
| "the current `_ensure_authenticated` check-then-login is not safe under concurrent requests without a lock" | Re-login is single-flight: a lock plus a session-generation counter, so N threads hitting an expired session cause exactly one login (`app/client.py`). An expired session is a 401, a redirect to `/login`, or the JSON redirect the form action sends. The client itself is built once, under a lock, however many requests arrive at start-up. |
| "No retry/backoff beyond the one re-login retry" | Timeouts, connection errors and 429/5xx are retried a bounded number of times with jittered back-off, honouring `Retry-After`. What survives surfaces as `503` (`504` for a timeout) with `Retry-After`. The one `502` in the capture was retried by the UI and then succeeded; the adapter retries a `502` too. |
| "Consider rate-limiting outbound requests to the portal" (`REFLECTION.md`) | At most `URJA_MAX_CONCURRENCY` (default 4) requests in flight and an identifying `User-Agent`. After the portal rejects the credentials, logins are refused for 30 s, then 60 s, doubling to a 15 minute ceiling, so a typo in `.env` is retried ever more rarely instead of hammering the account. `POST /api/v1/auth/login` reuses a session less than 30 s old rather than posting the password again. |
| "No caching layer" | `app/cache.py`: TTL, one load per key however many callers wait, serve-stale when the portal is down (`X-Cache: HIT \| MISS \| STALE`), least-recently-used eviction. A load that failed is not retried for 15 s, so during an outage callers get the stale answer, or the same error, at once instead of each waiting out its own attempt. |
| "No pagination on `/api/v1/meters`" | `limit` / `offset` plus filters and search, with `X-Total-Count`. Without `limit` the response is the full list, as in v1. |
| not covered | `GET /health/ready`; request ids (`X-Request-ID`); RFC 9457 `application/problem+json` errors that keep the v1 `detail` message and add a stable `code`; no exception text leaked on a `500`, and socket or TLS details of a failed portal call go to the log, not to the caller; meter ids validated and URL-encoded before they reach a portal URL; every request, redirect hops included, is checked against the portal's origin *before* it is sent, so a redirect cannot carry the password elsewhere; the password is a `SecretStr`, the portal URL must be `https` (plain `http` only for localhost), and the HTTP libraries never log below WARNING (at DEBUG they would print the session cookie); one bad value in a reading becomes `null` with a warning instead of a failed response, while a payload that is structurally wrong (no readable timestamps, rows without ids, a list that says it has meters but yields none) is refused rather than cached as an empty answer. |

### New surface

| Route | What it gives |
|---|---|
| `GET /api/v1/meters` | v1's list, now filterable by `status`, `make`, `phase`, `dt`, network branch (`zone`, `circle`, `division`, `subdivision`, `substation`, `feeder`) and a radius (`lat`, `lon`, `radius_m`, nearest first, with `distance_m`); searchable (`q` matches meter id or serial number); pageable (`limit` ≤ 500, `offset`); `include=coordinates,hierarchy` (comma separated or repeated) adds fields. |
| `GET /api/v1/meters/{id}` | Nameplate, installation type, transformer, coordinates and the breadcrumb (`hierarchy`: level, name, code). |
| `GET /api/v1/meters/{id}/consumption` | Readings with `value` (cumulative kWh), `kvah`, `voltage_r`, `interval_kwh`; `interval_minutes`, `portal_timezone`. `from` and `to` cut the portal's fixed window locally. |
| `GET /api/v1/hierarchy` | The tree, zone down to transformer, with meter counts and capacity; `root` and `depth` take a subtree. |
| `GET /api/v1/hierarchy/issues` | What does not add up in the network data (below). |
| `GET /api/v1/transformers` | The Transformers screen: feeder, capacity, and how many meters point at each. |
| `GET /api/v1/fleet/summary` | Counts by status, make, phase and transformer. |
| `GET /health/ready` | Can we hold a session with the portal? Probed at most every 15 s however often it is called. |

### Deliberate changes to v1

Everything above only adds. These three are the exceptions, and change what a v1 client could see:

- Where the portal gives no value, `serial_number` and `status` are `null` rather than the
  placeholder strings `"N/A"` / `"UNKNOWN"` (v1's own README listed this as a wart to fix once
  real data was known).
- A portal that is down answers `503` (`504` for a timeout), both with `Retry-After`, where v1
  answered `502` for every failure. Other upstream failures, such as an answer in a shape this
  code does not recognise, stay `502`. A client that read `502` as "the portal is down" should
  treat all three alike.
- Errors are `application/problem+json`. The v1 `detail` message is still in them; the media
  type and the extra fields (`code`, `request_id`) are new.

**The hierarchy is audited, not trusted.** Merging breadcrumbs is also the check on them.
`/hierarchy/issues` reports a node found under two parents, a code spelled two ways, a
feeder that disagrees with the Transformers screen, a transformer with no meters, a meter
that points at an unlisted transformer, and a breadcrumb that could not be read. Nothing is
dropped silently. The limit of the method: it assumes every meter under a transformer shares
that transformer's path (`PROTOCOL.md` §8, item 5).

## Setup

```bash
git clone https://github.com/karthikeyabommireddy/Flock-Energy-Urja-Meter-Ops-API.git
cd Flock-Energy-Urja-Meter-Ops-API
python -m venv .venv
.venv/Scripts/activate        # Windows; use `source .venv/bin/activate` on macOS/Linux
pip install -r requirements.txt

cp .env.example .env          # then fill in your portal credentials (blank in the example on purpose)
```

Every setting is an environment variable prefixed `URJA_`; `.env.example` lists them all.

Run it:

```bash
uvicorn app.main:app --reload
```

Open `http://localhost:8000/docs` for interactive Swagger UI, or
`http://localhost:8000/openapi.json` for the raw spec. After changing a route or a model,
regenerate the committed spec with `python scripts/export_openapi.py`.

The tests (no network access required — everything is mocked) were written for v1 and have
not been re-run since; see the status note above before relying on them:

```bash
pytest tests/ -v
```

### Web client

`web/` is a read-only client for the API: React 18, TypeScript (strict), Vite, TanStack
Query, React Router, Recharts, and Leaflet with OpenStreetMap tiles.

```bash
uvicorn app.main:app --reload      # the API, on :8000
cd web
npm ci                             # exactly what package-lock.json says
npm run dev                        # http://localhost:5173
```

The API allows that origin for `GET` requests (`URJA_CORS_ALLOW_ORIGINS`). If the API is
elsewhere, copy `web/.env.example` to `web/.env.local` and set `VITE_API_BASE_URL`.
`npm run build` type-checks and builds to `web/dist`.

| Screen | What it is for |
|---|---|
| Fleet | Counts by status, make and phase; the transformers with the largest share of faulty meters; a summary of the data-quality report. |
| Meters | Search and filter (URL-synced, so a filtered view can be shared), paged. |
| Meter | Nameplate, breadcrumb, map, half-hourly consumption with voltage, and a "worth a look" list: a decommissioned meter that still reports consumption, a faulty meter, voltage outside 207 to 253 V, gaps, spikes above three times the median, idle stretches, a register that falls. |
| Map | Every meter that has usable coordinates, marked by status in colour, size (faulty is larger) and outline (decommissioned is dashed). |
| Network | The hierarchy as a tree, with its data-quality issues. |

State is never carried by colour alone (badges say it in words), controls are labelled and
keyboard-reachable, light and dark schemes follow the system, and a response flagged
`X-Cache: STALE` shows a banner saying the data may be a few minutes old. Pages that draw
charts or maps are loaded on demand.

## Sample requests

```bash
# Liveness, and readiness (can we hold a session with the portal?)
curl http://localhost:8000/health
curl http://localhost:8000/health/ready

# Page through the fleet; X-Total-Count says how many matched, X-Cache where the data came from
curl -i "http://localhost:8000/api/v1/meters?limit=2"

# Filter: faulty L&T meters on one transformer (an & inside a value must be sent as %26)
curl "http://localhost:8000/api/v1/meters?status=faulty&make=L%26T&dt=DT-007"

# Search meter ids and serial numbers
curl "http://localhost:8000/api/v1/meters?q=SE33"

# One branch of the network, with each meter's path from the zone down
curl "http://localhost:8000/api/v1/meters?feeder=F-005&include=hierarchy"

# Meters within 500 m of a point, nearest first (lat, lon and radius_m go together)
curl "http://localhost:8000/api/v1/meters?lat=26.9124&lon=75.7873&radius_m=500"

# One meter, and its readings (from / to cut the portal's fixed window)
curl http://localhost:8000/api/v1/meters/J100009
curl "http://localhost:8000/api/v1/meters/J100009/consumption?from=2026-06-25&to=2026-06-26"

# The network, and what is wrong with it
curl "http://localhost:8000/api/v1/hierarchy?root=C-05&depth=2"
curl http://localhost:8000/api/v1/hierarchy/issues

# Transformers and fleet counts
curl http://localhost:8000/api/v1/transformers
curl http://localhost:8000/api/v1/fleet/summary

# Force a fresh login (not required before the above — they log in themselves)
curl -X POST http://localhost:8000/api/v1/auth/login
```

The samples below are the portal's captured data run through the API (`X-Total-Count: 403`
on the first).

`GET /api/v1/meters?limit=2`:

```json
[
  {
    "meter_id": "J100000",
    "serial_number": "SE33962",
    "status": "DECOMMISSIONED",
    "location": null,
    "make": "HPL",
    "phase_type": "single",
    "dt_code": "DT-001"
  },
  {
    "meter_id": "J100001",
    "serial_number": "GE84132",
    "status": "INSTALLED",
    "location": null,
    "make": "L&T",
    "phase_type": "single",
    "dt_code": "DT-002"
  }
]
```

`GET /api/v1/meters/J100009/consumption` (two of 337 readings):

```json
{
  "meter_id": "J100009",
  "records": [
    { "period": "2026-06-23T18:00:00Z", "value": 38630.74, "unit": "kWh", "kvah": 41721.2, "voltage_r": 239.0, "interval_kwh": null },
    { "period": "2026-06-23T18:30:00Z", "value": 38631.26, "unit": "kWh", "kvah": 41721.76, "voltage_r": 234.0, "interval_kwh": 0.52 }
  ],
  "interval_minutes": 30,
  "portal_timezone": "Asia/Kolkata"
}
```

`GET /api/v1/meters/J999999` (`application/problem+json`):

```json
{
  "type": "about:blank",
  "title": "Not Found",
  "status": 404,
  "code": "not_found",
  "detail": "meter 'J999999' does not exist",
  "request_id": "7c711c61fddc4ce2875016dfa7d3b019"
}
```

## Architecture

```
app/
  main.py       REST routes, error mapping, FastAPI app + OpenAPI metadata
  service.py    Use-cases over the adapter: caching, filtering, paging, the tiered local index
  cache.py      Thread-safe TTL cache: single-flight, serve-stale, size-bounded
  client.py     UrjaPortalClient — the only module that talks to the portal: login, retries, paging
  parsers.py    Decodes SvelteKit page data (devalue) and the portal's JSON into plain dicts
  normalize.py  Text, number and timestamp clean-up (day-first local time to UTC)
  hierarchy.py  Builds the network tree from breadcrumbs and audits it
  geo.py        Haversine distance and bounding box
  errors.py     Parse and not-found errors shared by the adapter and the routes
  models.py     Pydantic response schemas (the API's own contract, independent of
                whatever shape the portal happens to return)
  config.py     Environment-driven settings; fails loudly if credentials are missing
scripts/
  export_openapi.py   Regenerates openapi.json from the app
web/                  The web client (above)
```

**Session management is fully automatic.** No endpoint requires a prior login call —
`UrjaPortalClient` authenticates lazily on first use and re-authenticates transparently
if a request comes back looking like an expired session (a 401, a redirect that lands
back on `/login`, or the JSON redirect the portal's form action sends). `POST
/api/v1/auth/login` exists for callers who want to force a fresh login explicitly; it is
never required for normal use. When several requests notice the expiry at once, a lock
and a session-generation counter make the first one log in and the rest simply retry
with the new cookie.

**Errors are mapped consistently.** Every portal-adapter exception becomes one HTTP
status: a failed/expired session → `401`, a portal that is unreachable or kept failing
after the retries → `503` (with `Retry-After`), a timeout → `504`, a portal answer in a
shape this code does not recognise → `502`, an unknown meter or node → `404`, a bad
parameter → `422`, and anything unexpected → `500` with a generic message (the traceback
goes to the server log, not to the caller) — never a bare unhandled exception. A `404` from
the portal on an endpoint that has to exist (a list) is a `502`, since it means the portal
changed, not that something is missing, and that way a cached answer can still be served. Bodies are
RFC 9457 `application/problem+json`: `{type, title, status, code, detail, request_id}`,
where `detail` is the message v1 clients already read and `code` is stable
(`upstream_auth_failed`, `upstream_unavailable`, `upstream_timeout`, `upstream_error`,
`upstream_unparseable`, `invalid_request`, `not_found`, `internal_error`).

### Local index and scale

The "local index" is the meter list held in memory, enriched in tiers so that a simple
query never pays for an expensive one. A tier is built only when a request needs it, kept
for its TTL, and shared by concurrent requests (one load per key).

| Tier | Serves | Built from | First-time cost |
|---|---|---|---|
| 1. Attributes | `status`, `make`, `phase`, `dt`, `q`, paging, `/fleet/summary`, `/transformers` | The meter list | 21 requests (20 meters a page) |
| 2. Network position | `zone` … `feeder` filters, `include=hierarchy`, `/hierarchy` | One breadcrumb per transformer | About 40 more requests |
| 3. Coordinates | `lat` / `lon` / `radius_m`, `include=coordinates` | `/geo`, one request per meter | One per meter a request needs: 403 for the whole fleet, kept for an hour. An answer that cannot be read is left out and asked for again next time, not remembered as "no coordinates" |

**The bulk path** is the portal's paged search: 20 meters and the total per request, so the
fleet is 21 requests rather than 403 detail pages. A meter's own page is fetched only for
what the list lacks (installation type, breadcrumb, readings). A radius search narrows by a
bounding box first, then by haversine distance, and returns the nearest first.

**At 403 meters all of this is small, and ingestion, not querying, is the bottleneck.**
The portal's per-meter calls (`/geo`, `/energy`, detail) cost one request each, so tier 3 is
the expensive one and the first radius search over the whole fleet makes 403 of them, four
at a time. A fleet of 100 000 would need about 5 000 requests for the list and 100 000 for
coordinates. Nothing here would survive that unchanged: the next step is a background,
incremental refresh into a real store (SQLite, or Postgres with a spatial index), with the
API reading from the store instead of from process memory.

### Caching and freshness

| Data | TTL (setting) |
|---|---|
| Meter list, meter detail, hierarchy, transformers | 300 s (`URJA_CACHE_TTL_SECONDS`) |
| Consumption | 60 s (`URJA_CACHE_TTL_CONSUMPTION_SECONDS`) |
| Coordinates | 3 600 s (`URJA_CACHE_TTL_GEO_SECONDS`) |

Every data response says where it came from in `X-Cache`: `HIT` (fresh cache), `MISS`
(loaded from the portal) or `STALE`. When the portal is down, rejects our credentials or
answers in a shape we cannot read, the last good answer (coordinates included) is served as
`STALE` for up to `URJA_CACHE_MAX_STALE_SECONDS` (default 1 h) past its TTL; after that, or
if nothing was ever cached, the error is returned. A load that has just failed is not tried
again for 15 s (fixed), so an outage costs the portal one attempt per window, not one per
request. Only one refresh of a kind runs at a time: a request that arrives meanwhile waits for
it (coordinate loads queue behind one another), and there is no per-request wait limit.
Invalidation is TTL-only on purpose: there is no "force refresh" a caller could use to hammer
the portal. The consumption window in the
capture is fixed (23 to 30 June 2026) and takes no date range, so these TTLs could be much
longer without ever serving a wrong answer.

## Trade-offs and decisions

**Python + FastAPI over Node + Express.** The assignment offers both. FastAPI
auto-generates the OpenAPI schema and a Swagger UI for free — Express requires
hand-writing the spec or adding `swagger-jsdoc`. `httpx` gives native cookie-jar/session
support. This is a strict-dominance call, not a coin flip: same requirement, materially
less effort, in a language already present in this environment (Node was not installed at
all when v1 was written).

**Observed endpoints only, no fallbacks.** v1 probed candidate JSON paths and fell back to
scraping HTML because it could not see behind the login. With the capture those guesses
were deleted rather than kept as a safety net: the brief asks that the adapter follow only
what the UI itself requests, and a fallback that silently returns something else hides a
change in the portal. The cost is that a portal change now fails loudly (`502
upstream_unparseable`, or a `STALE` answer if there is something cached) instead of being
guessed around.

**A single process-lifetime client instance**, not a per-request session or a pool — now
safe to share. FastAPI runs these sync routes in a thread pool, so v1.1 keeps the one
instance and makes the sharing correct instead of replacing it: re-login is single-flight
(a lock plus a session-generation counter), outbound concurrency is capped by a
semaphore, and `httpx.Client` is itself thread-safe. A pool of sessions would only matter
with several portal accounts.

**Sync code kept, not rewritten as async.** The v1 adapter, routes and tests are
synchronous. Moving to `async` would touch every function and every test for no gain at
this scale: the work is I/O-bound and the thread pool already overlaps it. If concurrency
ever outgrows the pool, async is the next step and the `UrjaPortalClient` seam keeps it
contained.

**Keep the register, derive the interval.** `kwh` is a cumulative register, so `value`
keeps it as the portal states it and `interval_kwh` is added beside it. Where a register
falls (a reset, not negative consumption) or a reading is missing, the interval is `null`
and the series shows a gap; nothing is interpolated.

**The time zone is an assumption, and the response says so.** The portal's timestamps carry
no zone. They are read as `Asia/Kolkata` (the network is in Jaipur), converted to UTC, and
every consumption response carries `portal_timezone`, so a wrong guess is visible and one
setting away from fixed. (A zone with daylight saving time would lose the repeated hour once a
year: both readings look the same and the second is dropped as a duplicate. The default has
none.)

**Serve stale rather than fail.** A cached answer up to `URJA_CACHE_MAX_STALE_SECONDS` past
its TTL is served with `X-Cache: STALE` instead of an error, so the response says what it
is. The cost is that the cache is per process.

**An unknown meter id is a `404` without a portal request.** What the portal answers for
an unknown id was never observed, so the service checks the id against the cached meter
list first. The price: a meter added within one cache TTL answers `404` until the list
refreshes.

**Additive, with three named exceptions.** v1 consumers keep working: same routes and
fields, with new behaviour arriving as parameters, headers and new routes. List responses
stay a plain JSON array, and the new optional fields (`latitude`, `longitude`,
`distance_m`, `hierarchy_path`) are absent unless asked for. The exceptions are the ones
under [Deliberate changes to v1](#deliberate-changes-to-v1): `null` instead of `"N/A"`,
`503` / `504` instead of `502` for an unavailable portal, and problem+json error bodies.

**`raw_fields` on `MeterDetail`**, not a rigid schema that silently drops unknown portal
fields. Only five meters' details were captured, so any other parameter a meter lists
survives in `raw_fields` instead of being lost. The flip side: if the portal ever adds
personal data to a meter's parameters, `raw_fields` would pass it on, so allow-list it before
this API is exposed beyond a trusted network.

**A thin web client over the API only.** It never talks to the portal, shows what the API
says (including `STALE`), and does its own analysis only on data the API already returns
(the "worth a look" rules). Vite and React for a fast, conventional toolchain; Recharts and
Leaflet rather than hand-drawn charts and maps; TypeScript strict so the API types are
checked end to end.

## Intentional omissions

- **No full circuit breaker, no metrics.** A failed load is remembered for 15 s, which stops a
  flood of requests each retrying a dead portal, but beyond that every attempt makes its
  bounded retries, and there is no `/metrics`.
- **No authentication on this API.** It is a local, read-only exercise; a production
  version would add API keys or OAuth, per-client rate limiting, a CORS allow-list and TLS
  termination. Until then keep it on loopback (what `uvicorn app.main:app` does by default):
  it serves everything the service account can see, including every meter's location and
  half-hourly consumption, and without a `Host` allow-list a page on another site could reach a
  loopback API through DNS rebinding.
- **The cache and the local index are per process and in memory.** Several instances would
  each hold their own and each fill their own (tier 3 included); a shared cache (Redis) or
  a store is the next step, as described under Local index and scale.
- **No persistent credential storage beyond environment variables.** Adequate for a
  single-tenant wrapper; a multi-tenant version would need a real secrets manager.
- **The web client has no sign-in and nothing it can change** (the API is read-only), and
  its map loads tiles from openstreetmap.org, a third-party request from the browser.

## What's not yet done

See `PROTOCOL.md` §5 and §10 for the full account. In short: the adapter has not been run
against the live portal, because attempting that was blocked by this environment's safety
policy during development; what it was checked against is a capture of one logged-in
browser session. "Sound against a capture of what the portal did once" and "verified
against what the portal does" are not the same claim, and this README does not pretend
otherwise.

Concretely, still open:

- **Live verification.** Run the adapter once against the portal with real credentials,
  and compare.
- **Cases the capture does not show** (`PROTOCOL.md` §10): what the portal answers for an
  unknown meter id, for a wrong password and for a page past the end of a list; the session
  cookie's name and lifetime; page 2 of the Transformers screen; and whether the breadcrumbs
  and coordinates of all 403 meters are as clean as the five that were captured.
- **The time zone** of the readings (`Asia/Kolkata` is an assumption).
- **No automated tests cover v1.1**, and six of v1's tests are stale by design because they
  pin guessed shapes the adapter no longer uses. All are in `tests/test_client.py`:
  `test_get_meters_uses_json_api_when_available`,
  `test_get_meters_falls_back_to_html_table_when_no_json`,
  `test_get_meter_details_html_fallback_uses_brief_example_selectors`,
  `test_get_meter_details_reports_unknown_when_elements_missing`,
  `test_get_meter_consumption_html_fallback_normalizes_values` and
  `test_auto_reauth_retries_once_after_session_expiry`. They were left as they were rather
  than rewritten to pass.
- **A persistent, incrementally refreshed index** for a fleet far larger than 403 meters.
- **Recharts 2** is pinned in `web/`; npm marks that line as no longer active (version 3 is the
  maintained one). Moving over is a small migration that was not worth the risk to a
  working client at the end of this change.

## Use of AI

I used a coding agent throughout. Who did what:

- **v1** was built with a coding agent, as `PROTOCOL.md` §5 describes.
- **The v1.1 extension**, everything listed under "What changed in v1.1" and the whole of
  `web/`, was written by **Claude Code** (Anthropic), working in this repository from the
  assignment brief, my v1 code and the portal capture I supplied. Before it was committed,
  separate Claude Code review agents (security, Python backend, web client with accessibility)
  read the whole change and their findings were fixed. Those reviews are AI output too, not a
  substitute for a human one.
- **The portal reconnaissance was mine.** I logged in and browsed the portal in my own browser
  with DevTools open (all 21 pages of the meter list, a search, five meters, the Transformers
  screen, a session expiry) and saved the HAR and two screenshots. The agent did not contact
  the portal (its safety policy blocked that); it analysed my files offline and wrote the
  findings up as `PROTOCOL.md` v2.
- **Scope and decisions.** I chose to extend my own v1 in place, in Python, as development only
  with no tests written or run for this change, and I approved the plan before any code was
  written. After the review I asked the agent to settle the remaining decisions as it judged
  best for a submission an assessor would accept; those were its calls, and the commit messages
  say what they were.
- **`REFLECTION.md`**: the five answers are my own, from v1, and are unchanged. The addendum at
  the end was drafted by Claude Code from what happened while v1.1 was built, and says so.
