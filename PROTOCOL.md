# PROTOCOL.md — Urja Meter Ops Portal Reconnaissance

How `https://urja-ops.flockenergy.tech` actually works, as observed. There are two rounds of evidence:

- **v1 (2026-09-12):** unauthenticated requests only (the login page and its redirects). Logging in, by
  script or by driving a browser, was blocked by the coding agent's own safety policy (§5).
- **v2 (2026-10-09):** a Chrome DevTools **HAR** of one logged-in browsing session (55 requests: login, the
  21-page meter list, search, five meter pages, the Transformers screen) and two screenshots, supplied by the
  author. The HAR contains the login password and the session, so it is **not** in this repository. It was
  analysed offline with scripts that redact both.

Every claim is marked **VERIFIED** (seen in the HAR, or in v1's requests), **ASSUMED** (a decision taken without
evidence, with the reason) or **UNOBSERVED** (not seen at all; collected in §10).

## Summary (read this first)

| | |
|---|---|
| Rendering | A SvelteKit app with a JSON API behind it. No HTML needs scraping anywhere. |
| Auth | Form action: `POST /login` (`email`, `password`) sets a session cookie. An expired session redirects to `/login`. |
| Data available | 403 meters (`J100000` to `J100402`). Per meter: a nameplate, a network breadcrumb, coordinates and 7 days of half-hourly readings. 40 distribution transformers (the Transformers screen). |
| Best data path | `GET /portal/meters/search?q=&page=N`: 20 meters a page plus `total`, so the whole fleet is 21 requests. |
| Top surprises | (1) consumption is a **cumulative register** sent as **strings**; (2) a meter's detail comes in **two different shapes**; (3) the network hierarchy exists **only as breadcrumbs** inside each meter's detail; (4) 77 of the 403 serial numbers contain an `&`; (5) the login field is `email`, not `username` (v1). |

## 1. Platform

**VERIFIED.** The portal is a [SvelteKit](https://kit.svelte.dev) application, not a
classic server-rendered legacy stack as the brief's phrasing ("legacy portal") might
suggest.

- Response to `GET /login` carries the header `X-Sveltekit-Page: true`.
- Static assets are served from `./_app/immutable/...` — SvelteKit's standard build
  output layout (`entry/start.<hash>.js`, `entry/app.<hash>.js`, `chunks/*.js`).
- The page is genuinely server-rendered (the HTML contains the full login form, not an
  empty `<div id="app">` shell) and then hydrates client-side via
  `kit.start(app, element, {...})`.
- `Alt-Svc: h3=":443"` is advertised — HTTP/3 capable edge (likely a modern reverse
  proxy / CDN in front of the app).

**Consequence for the adapter:** this is not a legacy app with brittle server-rendered
HTML tables everywhere. It is far more likely that authenticated pages are populated by
SvelteKit's own `load` functions, which may fetch from an internal JSON API. See §4 for
how the adapter is built to handle either case.

*(v2: the "far more likely" guess was right, see §4.)*

## 2. Unauthenticated request flow

**VERIFIED.**

```
GET / HTTP/1.1
→ 302 Found
  Location: /login

GET /login HTTP/1.1
→ 200 OK
  Content-Type: text/html
  X-Sveltekit-Page: true
```

The root path redirects unauthenticated visitors to `/login`. No cookie is set on this
initial pass (no `Set-Cookie` header on either response), so there is no pre-auth CSRF
or anti-forgery cookie issued before the login form is even loaded.

## 3. Login form

**VERIFIED** (from the rendered HTML of `GET /login`):

```html
<form method="POST" class="space-y-4">
  <input name="email" type="email" required autocomplete="username" />
  <input name="password" type="password" required autocomplete="current-password" />
  <button type="submit">Sign in</button>
</form>
```

Key findings:

- **No `action` attribute on the form.** It posts back to the same URL, `/login`. This
  is SvelteKit's [Form Actions](https://kit.svelte.dev/docs/form-actions) convention —
  a progressive-enhancement pattern where the form works even with JavaScript disabled.
- **The field is named `email`, not `username`.** This directly contradicts the
  assignment brief's own example code (`app/client.py`), which posts
  `{"username": ..., "password": ...}`. Anomaly recorded — the adapter uses `email`.
- **No CSRF token field and no other hidden inputs** are present in the rendered
  markup. If the portal enforces CSRF protection, it is most likely via a `SameSite`
  cookie policy or an `Origin`/`Referer` check on the server, rather than a token
  embedded in the form — SvelteKit's built-in CSRF protection (enabled by default)
  works exactly this way: it checks the `Origin` header against the request host for
  non-GET requests whose `content-type` is one a plain `<form>` can send. **This means
  the adapter must send the login POST as `application/x-www-form-urlencoded` (a
  browser-form content-type), not as JSON** — a JSON body would very likely be
  rejected by SvelteKit's default CSRF check.

### 3.1 Login as the UI does it (v2)

**VERIFIED** in the HAR. The UI does not submit the form natively. It posts it with `fetch` and asks SvelteKit
for a JSON answer:

| | Request | Response |
|---|---|---|
| 1 | `POST /login`, `application/x-www-form-urlencoded`, fields `email` and `password`. Headers: `Accept: application/json`, `x-sveltekit-action: true`, `Origin: https://urja-ops.flockenergy.tech` | `200 application/json`: `{"type":"redirect","status":303,"location":"/meters"}` |
| 2 | `GET /meters/__data.json?x-sveltekit-invalidated=010` | `200`: the signed-in user's name and e-mail, in SvelteKit's data format (§6) |

So success is stated by the portal (`"type":"redirect"` to somewhere other than `/login`) and need not be inferred
from where a followed redirect lands. The adapter sends the same two headers and reads that verdict; with a
plain HTML response it falls back to v1's inference (landed off `/login` and holds a cookie).

**Not recorded:** the session cookie's name, flags and lifetime. DevTools leaves cookies out of a HAR export unless
"include sensitive data" is ticked, and the response headers carry no `Set-Cookie`. The adapter does not need them:
`httpx`'s cookie jar replays whatever the portal sets. **UNOBSERVED:** what a *failed* login answers (§10).

## 4. Data access

**VERIFIED (v2).** v1 could not tell whether authenticated data is server-rendered HTML (A) or served by an
internal JSON API (B). It is **B**, in two forms: custom JSON endpoints under `/portal/`, and SvelteKit's own
page-data endpoints (`<route>/__data.json`). Nothing in the capture needed an HTML page parsed. v1's table of
hypotheses is kept for the record:

| Hypothesis (v1) | Verdict (v2) |
|---|---|
| **A. Server-rendered pages**, parsed with BeautifulSoup. | Not needed. The pages render client-side from JSON. |
| **B. Internal JSON API**, called directly. | **Correct.** The endpoints are in §6. |

v1's design (probe a few candidate JSON paths, fall back to scraping HTML) was removed in v1.1: the adapter now
calls only what the UI itself calls. Guessing paths against a live system is also against the brief's rules of
engagement.

## 5. What is BLOCKED and why

**Resolved (v2).** The gap described below was closed by the author's HAR (§6 to §9). The account is kept as
written, because it still explains why v1 shipped unverified.

Reconnaissance was unauthenticated only. Attempting to actually **log in** — via a
scripted `curl` POST with the provided credentials, and separately via a real browser
driven through the chrome-devtools MCP tools — was denied both times by the coding
agent's own auto-mode safety classifier, which flags scripted credential submission and
automated interaction with an external host as a "Third-Party Attack" pattern
regardless of which tool is used to do it. This is a session-level policy decision, not
a network or credentials problem, and it does not distinguish "this happens to be a
training assignment" from a real attack pattern at the point the action is requested —
nor should it be expected to from the request shape alone.

**What this means concretely:** the login flow's actual POST response, the session
cookie's name and attributes, and every authenticated page's real structure are
**unverified**. The adapter and API below are built defensively — a candidate-path
probe with a fallback, clear error surfacing, nothing hard-coded that can't be swapped
in one place — but they have **not been run against a live authenticated session**, and
the summary checklist item "Tested endpoint functionality against the live portal URL"
is **not yet satisfied**. Closing this out needs one of:

1. The user grants the permission interactively and asks the agent to continue the live
   recon (attempt login, capture the real response, inspect one authenticated page), or
2. The user (or someone with a browser) performs Step 1 manually — logs in, opens
   DevTools → Network tab, and shares the login POST's response headers/body and one
   authenticated page's HTML/JSON — and this document plus the adapter's
   selectors/endpoints get updated from real evidence, or
3. The adapter ships as-is, clearly marked in `README.md` as unverified against a live
   session, for the user to smoke-test with real credentials in an environment where
   that action is permitted.

*(v2: option 2 happened. The adapter is still **not** run against the live portal: it has been replayed against the
captured responses only, see §11.)*

## 6. Endpoint inventory (v2)

**VERIFIED**, all in the HAR. Every request is a `GET` except the login. All were same-origin `fetch` calls with
`Accept: */*`; none needed any header beyond the session cookie.

| Purpose | Request | Response | Median latency |
|---|---|---|---|
| Meter list (the bulk path) | `GET /portal/meters/search?q=&page=N` | `{"data":[…20 rows…],"total":403,"page":N,"pageSize":20}` | 37 ms |
| Meter nameplate and breadcrumb | `GET /meters/<id>/__data.json?x-sveltekit-invalidated=001` | SvelteKit page data (below) | 40 ms |
| Coordinates | `GET /portal/meters/<id>/geo` | `{"data":{"latitude":"26.82…","longitude":"75.90…"}}` | 81 ms |
| Consumption | `GET /portal/meters/<id>/energy` | `{"data":[…337 readings…]}`, 27 KB | 100 ms |
| Transformers screen | `GET /portal/dts?page=N` | `{"data":[…20 rows…],"total":40,"page":N,"pageSize":20}` | 92 ms |
| Session probe | `GET /meters/__data.json?x-sveltekit-invalidated=010` | the layout node: 158 bytes | 36 ms |

**Paging.** `page` is 1-based and the response repeats `page`, `pageSize` (always 20) and `total`. The meter list
has 21 pages: 20 rows each and 3 on the last (`403 = 20 × 20 + 3`). `total` was the same on every page.
`q` is a substring match: `J` returned a full page, `J1000` a full page, `J100001` exactly one row, `J!` an
empty `data` array with `total: 0`. No page-size parameter was ever sent, so none is used. Page 2 of the
Transformers screen was not captured.

**SvelteKit page data.** `__data.json` answers `{"type":"data","nodes":[…]}` with one node per layout and page;
nodes the client did not ask for are `{"type":"skip"}`. A data node's `data` is not nested JSON but SvelteKit's
`devalue` encoding: **one flat array whose objects and arrays hold indices into that same array**, so a repeated
value is stored once. `app/parsers.py` decodes it (`unflatten`). `x-sveltekit-invalidated=001` means "re-run only
the page node", `010` "only the middle (layout) node", which is what the UI sends and what the adapter sends.

The captured payloads use only plain objects, arrays, strings and the constant `-1` (undefined). The decoder also
handles devalue's tagged types (`Set`, `Map`, `Date`, ...) and its other negative constants, numbered as devalue 4.3
and later (SvelteKit 2) number them: `-2` a hole, `-3` NaN, `-4` and `-5` the infinities, `-6` negative zero. Older
devalue had no hole and numbered NaN as `-2`: **ASSUMED** not to matter, since none of those occur in the capture.
Input that is malformed, larger than 100 000 values or nested more than 100 deep is refused as a `PortalParseError`
(a `502`), never expanded.

## 7. Data model as discovered (v2)

| Entity | Portal field | Example raw value | API field (type) |
|---|---|---|---|
| Meter (list row) | `meterId` | `"J100001"` | `meter_id` (string) |
| | `serialNo` | `"GE84132"`, `"L&84997"` | `serial_number` (string) |
| | `make` | `"L&T"` | `make` (string) |
| | `phaseType` | `"single"` / `"three"` | `phase_type` (string) |
| | `installStatus` | `"Installed"` | `status` (string, upper-cased: `INSTALLED`) |
| | `dtCode` | `"DT-002"` | `dt_code` (string) |
| Meter (detail) | parameters `Meter ID`, `Serial No`, `Make`, `Phase Type`, `Installation Status`, `Installation Type` | `"CT Operated"`, `"Whole Current"` | the same fields, plus `installation_type` |
| Breadcrumb | `Zone`, `Circle`, `Division`, `Subdivision`, `Sub Station`, `Feeder`, `DT` | `"Jaipur Zone 2 (Z-02)"` | `hierarchy[]`: `{level, name, code}` |
| Coordinates | `latitude`, `longitude` | `"26.822136543835608"` (a string) | `latitude`, `longitude` (float or `null`) |
| Reading | `timestamp` | `"23/06/2026 23:30"` | `period` (RFC 3339 UTC) |
| | `kwh` | `"42594.05"` | `value` (float, unit `kWh`) |
| | `kvah` | `"46001.58"` | `kvah` (float) |
| | `voltR` | `"231"` | `voltage_r` (float, volts) |
| | derived | | `interval_kwh` (float or `null`) |
| Transformer | `code`, `name`, `feederCode`, `capacityKva` | `"DT-001"`, `"Malviya Nagar DT 1"`, `"F-001"`, `100` | `code`, `name`, `feeder_code`, `capacity_kva` |

The portal shows no installation date and no place name for a meter. `installed_date` is therefore always `null`,
and `location` carries the name of the transformer the meter hangs off (the closest thing to a place).

**Fleet, from the 21 list pages.** 403 meters, ids `J100000` to `J100402` with no gaps. Status: `Installed` 238,
`Faulty` 90, `Decommissioned` 75. Make: HPL 90, Genus 88, Secure 85, Allied 76, L&T 64. Phase: single 277, three 126.
40 transformers (`DT-001` to `DT-040`) with 10 to 13 meters each. No nulls, no blanks, no stray whitespace in any list row.

**The network.** The portal has no hierarchy endpoint. The tree is Zone > Circle > Division > Subdivision >
Substation > Feeder > DT > Meter, and every meter's detail carries its own path as `"Name (CODE)"` strings. The
five breadcrumbs captured:

| Meter | Zone | Circle | Division | Subdivision | Substation | Feeder | DT |
|---|---|---|---|---|---|---|---|
| J100000 | Z-01 | C-01 | D-01 | SD-01 | SS-01 | F-001 | DT-001 |
| J100001 | Z-02 | C-02 | D-02 | SD-02 | SS-02 | F-002 | DT-002 |
| J100004 | Z-02 | C-05 | D-05 | SD-05 | SS-05 | F-005 | DT-005 |
| J100008 | Z-03 | C-03 | D-09 | SD-09 | SS-09 | F-009 | DT-009 |
| J100009 | Z-01 | C-04 | D-10 | SD-10 | SS-10 | F-010 | DT-010 |

Division to DT all carry the transformer's number; zones and circles repeat. Whether one circle ever appears under
two zones can only be told by reading all 40 breadcrumbs, which the adapter does and reports (§8, item 5).

## 8. Quirks and surprises (v2)

Each with its evidence and what the service does about it.

1. **Numbers are strings.** `"kwh":"42594.05"`, `"voltR":"231"`, and the coordinates. All 337 × 5 energy values
   in the capture were strings. They are parsed to floats; one unparseable value becomes `null` with a warning
   instead of failing the response.
2. **Timestamps are day-first and carry no zone.** `23/06/2026 23:30` (days run 23 to 30, so DD/MM is certain).
   They are read in `URJA_PORTAL_TIMEZONE` and returned as RFC 3339 UTC. **ASSUMED:** `Asia/Kolkata`, because the
   network is in Jaipur and nothing in the portal says otherwise (§10).
3. **`kwh` and `kvah` are cumulative registers, not consumption.** J100001: 42594.05 → 42732.85 over 337 readings,
   never decreasing, about 0.41 to 0.42 per half hour. The API keeps the register as `value` and derives
   `interval_kwh`: the difference to the reading one step earlier (within one and a half of the series' usual
   spacing). It is `null` for the first reading, after a missing or unreadable reading (the difference would span
   several intervals) and wherever a register falls, which would be a reset rather than negative consumption.
4. **Meter detail has two shapes.** J100001, J100008 and J100000 send `{"data":[{"parameterName":"Make",
   "parameterValue":"L&T"}, …]}`. J100009 and J100004 send `{"classData":"<JSON text>"}`, a JSON document
   *inside a string* whose keys are PascalCase (`{"installed_meter":{"MeterId":…,"SerialNo":…,"Make":…}}`).
   Both are normalised; an unknown third shape raises `PortalParseError` (a 502) instead of returning a
   half-empty meter.
5. **Hierarchy only as breadcrumbs.** Rebuilt by merging the breadcrumbs of one sample meter per transformer
   (40 requests rather than 403). Merging is also the audit: a node found under two parents, a code spelled two
   ways, a feeder that disagrees with the Transformers screen, a transformer with no meters and a meter pointing at
   an unlisted transformer are all reported by `GET /api/v1/hierarchy/issues` and never dropped silently. The
   limit of the method: it assumes every meter under a transformer shares that transformer's path.
6. **77 of 403 serial numbers contain an `&`** (`L&84997`), and one make is `L&T`. Unescaped in a query string
   an `&` splits the parameter, so query values always go through the HTTP client's `params`, which
   percent-encodes them. Serial prefixes do not follow the make (every make appears with every prefix), so the
   make cannot be inferred from a serial.
7. **Decommissioned meters still report consumption.** J100000 and J100009 are `Decommissioned`, yet their
   registers climb 0.42 and 0.52 kWh every half hour, as an installed meter's do. Either the status is stale or the
   readings are. The web client flags it on the meter page. **UNOBSERVED** whether the fleet-wide pattern is the
   same: only five meters were captured.
8. **The consumption window is fixed and old.** All five series run from 23/06/2026 23:30 to 30/06/2026 23:30,
   about three months before the capture (2026-10-09). The portal takes no date range, so `from` and `to` on our
   endpoint cut that window locally. The data does not move, so the cache TTLs could be far longer than their
   defaults without ever serving a wrong answer.
9. **Zone and circle numbering is not monotonic** (§7): DT-009 sits in Z-03 / C-03 but DT-005 in Z-02 / C-05.
   Not an error on its own; it is why the merge checks for circles under two zones.

## 9. Failure modes observed (v2)

- **Session expiry** (the author's observation: deleting the session and reloading a data page): *redirect to
  `/login`*. How that looks on the JSON endpoints, as a 3xx, a 401 or a JSON `{"type":"redirect"}` body, was not
  captured, so the adapter treats all three, and a final URL of `/login`, as "logged out", logs in again once
  (however many requests noticed) and retries.
- **`502 Bad Gateway` once**, on `GET /meters/J100001/__data.json?…`: 11 bytes of plain text, no `Content-Type`.
  The UI's retry, about 50 ms later, got `200`. The adapter retries 429 and 5xx up to `URJA_MAX_RETRIES` times
  with jittered back-off and surfaces what survives as `503`.
- **An empty result is not an error:** `search?q=J!` answers `200` with `"data":[]` and `"total":0`.
- **Latency is low and flat** (§6), so a cold fleet load (21 list requests) is well under a second at the default
  concurrency of 4.

## 10. Assumptions and open questions (v2)

| # | Open | Decision, and why |
|---|---|---|
| 1 | Time zone of the readings | **ASSUMED** `Asia/Kolkata`, configurable (`URJA_PORTAL_TIMEZONE`). The responses name the zone they were converted from, so a wrong guess is visible. |
| 2 | Session cookie name, flags, idle lifetime | **UNOBSERVED** (the HAR omits cookies). Not needed: the cookie jar replays it, and expiry is detected rather than predicted. |
| 3 | What the portal answers for an unknown meter id | **UNOBSERVED.** The service never asks: an id absent from the cached meter list is a `404` without an upstream request. The cost is a `404` for a meter added within one cache TTL. |
| 4 | What a failed login answers | **UNOBSERVED.** Anything other than `{"type":"redirect"}` away from `/login` counts as rejected, and further logins are refused for 30 s, doubling with each consecutive rejection up to 15 minutes, so a typo in the credentials is retried ever more rarely. |
| 5 | Page 2 of the Transformers screen, and a page beyond the end of any list | **UNOBSERVED.** Page 1 gives `total: 40`; the adapter reads `ceil(total / pageSize)` pages and reconciles the row count against `total`. |
| 6 | Whether `q` also matches make or transformer | **UNOBSERVED.** Not relied on: the API filters the cached list itself. |
| 7 | Breadcrumb and coordinate quality across all 403 meters | Only five meters were captured. Coordinates outside −90..90 / −180..180, and 0,0, become `null`; the breadcrumb merge reports what disagrees. |
| 8 | Rate limiting | **UNOBSERVED.** Concurrency is capped at 4, requests carry an identifying `User-Agent`, and the portal is only ever read. |

## 11. How this was figured out (v2)

- The author logged in with DevTools open (Network, Preserve log), paged the meter list to its end, searched,
  opened five meters and the Transformers screen, and saved a HAR plus two screenshots.
- The HAR was read with scripts, not by eye: an inventory (method, path, status, size), then the bodies, then
  data-quality scans of all 21 list pages and the five consumption series. The scripts print no password and no
  cookie, and are not part of this repository.
- Dead ends: the login response is JSON, not the redirect v1 expected from a form post. The detail's two shapes were
  only found by comparing five meters side by side. The HAR holds no `Set-Cookie`, so the cookie facts stay open.
- The adapter and routes were replayed once against the captured responses (login, paging, the retried `502`,
  both detail shapes, the JSON login redirect, filtering, `404`/`422`). That checks the code against the capture,
  **not** against the live portal, which has still not been called by this code.

## Version Log

| Version | Date | Change |
|---|---|---|
| 1 | 2026-09-12 | Initial reconnaissance: platform, unauthenticated flow, login form fields. Live authenticated recon blocked — see §5. |
| 2 | 2026-10-09 | Author-supplied HAR and screenshots: login protocol, the JSON endpoints, data model, quirks, failure modes, open questions (§3.1, §4, §6 to §11). §1 to §5 kept, with pointers to what resolved them. |
| 2.1 | 2026-10-10 | Review fixes, no new evidence: `interval_kwh` no longer spans a missing reading (§8, item 3); the decoder's constants and limits stated (§6); the login back-off now grows with each rejection (§10, item 4). |
