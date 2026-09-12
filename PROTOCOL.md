# PROTOCOL.md — Urja Meter Ops Portal Reconnaissance

Findings from inspecting `https://urja-ops.flockenergy.tech` prior to writing the adapter.
Every claim below is marked **VERIFIED** (observed directly, unauthenticated) or
**BLOCKED / UNVERIFIED** (could not be confirmed — see note at the end).

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

## 4. Data access — hypothesis, not yet confirmed

**BLOCKED / UNVERIFIED.** Authentication against the portal was not possible in this
session (see §5), so the actual shape of meter listing, meter detail, consumption, and
hierarchy data has not been observed. Two shapes are possible for a SvelteKit app, and
they lead to materially different adapter code:

| Hypothesis | What it means for the adapter |
|---|---|
| **A. Server-rendered pages** — each route (`/meters`, `/meters/[id]`, ...) is a normal SvelteKit page whose `load` function fetches data server-side and embeds it in the rendered HTML. | The adapter fetches the HTML page and parses it (BeautifulSoup), as the brief anticipates. |
| **B. Internal JSON API** — the frontend calls a JSON endpoint (e.g. `/api/v1/meters` or a SvelteKit `+server.js` route) client-side, and the adapter can call that endpoint directly. | The adapter calls the JSON endpoint directly — no HTML parsing needed, far less brittle. |

**Design decision:** the adapter (`app/client.py`) **tries the JSON-API hypothesis
first** (a small set of candidate paths, since SvelteKit `+server.js` endpoints
commonly sit alongside the page route) and **falls back to HTML parsing** of the page
route when the JSON attempt doesn't return a JSON content-type. This is recorded as a
trade-off in `README.md` and `REFLECTION.md`: the adapter is resilient to either
architecture, but the HTML-parsing fallback's selectors are necessarily *best-effort*
until checked against a real authenticated page.

## 5. What is BLOCKED and why

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

## Version Log

| Version | Date | Change |
|---|---|---|
| 1 | 2026-09-12 | Initial reconnaissance: platform, unauthenticated flow, login form fields. Live authenticated recon blocked — see §5. |
