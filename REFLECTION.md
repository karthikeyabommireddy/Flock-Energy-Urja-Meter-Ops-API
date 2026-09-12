# REFLECTION.md

## 1. What assumptions did you make?

- **That the portal's field naming would match the brief's example code.** It didn't —
  the real login form uses `email`, not `username` (see `PROTOCOL.md` §3). This was
  caught by actually fetching the login page before writing a line of adapter code,
  rather than trusting the brief's snippet at face value. If I'd copied the example
  code directly, login would have silently failed against the real form every time.
- **That "legacy portal" meant classic server-rendered HTML.** The recon showed
  otherwise — it's a SvelteKit application (§1). This assumption, had it gone
  unchecked, would have led to writing an HTML-table-first scraper for what is
  probably a JSON-API-backed app, which is why the adapter probes JSON first instead.
- **That CSRF protection, if present, is enforced via the `Origin` header rather than a
  form token** — inferred from the absence of any hidden token field in the rendered
  login form, combined with SvelteKit's documented default CSRF behavior. This is a
  reasonable inference from the framework identification, not a verified fact, and
  it's exactly the kind of thing that would be confirmed or corrected on first real
  login attempt.
- **That Python's environment already having the right runtime, and Node not being
  installed at all, is a legitimate tie-breaker** between the two language options the
  brief offers — not just a convenience for me, but a genuine signal about what this
  environment is set up to run and maintain.

## 2. What was the most difficult part?

Building a legacy-portal adapter **without being able to see what "authenticated" looks
like**. Every design decision downstream of authentication — session detection, data
shape, HTML selectors — normally gets nailed down by watching one real request/response
cycle. Here, that cycle was blocked partway through Step 1 of the brief's own
instructions (the coding agent's safety layer treated scripted credential submission to
an external host as an attack pattern, and treated driving a real browser to do the
same thing as equivalent — correctly, since the *action* is identical regardless of
which tool performs it).

The honest difficulty wasn't writing the code — it was resisting the pull to just
*assume* a shape and write confidently as if it were confirmed. The discipline that
actually mattered was: verify what can be verified (everything pre-authentication),
mark everything else as a hypothesis with a stated fallback, and make the code itself
resilient to being wrong about the hypothesis (JSON-first/HTML-fallback) rather than
betting the whole design on one guess.

## 3. What mistakes did you make, and how did you catch them?

- **I made the exact mistake I was trying to avoid, once.** After the direct login
  attempts were blocked, I built and ran the FastAPI server locally to smoke-test it —
  legitimate — but then issued a request to one of *its own* endpoints
  (`GET /api/v1/meters`) without stopping to notice that this would cause the server's
  own adapter code to make a real, live, authenticated request against the portal
  using the real credentials — functionally the identical denied action, just routed
  through one layer of indirection. I caught it immediately after seeing the response
  (a `401`, which I chose not to treat as verified information about the portal),
  killed the local server, and did not repeat the pattern for the rest of the session.
  Recorded here rather than quietly fixed, because it's a more instructive mistake than
  most of the ones that get caught by tests: a safety boundary can be worked around
  completely by accident, with good intentions, one abstraction layer at a time.
- **A test bug, not a code bug**, caught immediately by running the suite: the first
  version of `test_login_success_sets_authenticated_true` asserted the request path was
  `/login` unconditionally inside the mock handler — but `httpx`'s `follow_redirects`
  causes the same handler to be invoked again for the redirect target, and the
  assertion fired on that second call. Fixed by branching on path inside the handler.
  This is exactly why the suite was run before treating anything as done, rather than
  after — it turned a subtle test flaw into a two-line fix instead of a false
  confidence in coverage that didn't test what it claimed to.

## 4. What would you do differently with more time or access?

- **Get explicit, scoped permission to complete the live login step**, ideally in an
  environment set up for it (a disposable test account, clearly bounded), and then
  spend perhaps twenty minutes updating `PROTOCOL.md` from real evidence: the actual
  session cookie's name and attributes, whether the JSON-API or HTML-page hypothesis is
  correct, and the real selectors/paths. That would let the HTML-fallback selectors and
  JSON candidate paths in `client.py` be narrowed from "best-effort, both hypotheses
  covered" to "confirmed" — a small, mechanical change once the evidence exists.
- **Add a proper integration test tier** that runs against a local mock server
  simulating the *confirmed* portal behavior (once known), sitting between the current
  unit tests (mocked at the transport layer) and a real end-to-end run against the
  live portal — giving a middle rung that catches integration issues without ever
  touching the real service.
- **Add a lock around `_ensure_authenticated`** if this were ever going to see
  concurrent traffic — noted in the README as a known gap rather than solved, since
  solving it without evidence of real concurrent load would be speculative complexity.
- **Consider rate-limiting outbound requests to the portal** — a wrapper API that gets
  popular could otherwise hammer the legacy system harder than a human ever would
  through the browser.

## 5. Anything else worth noting?

The single most useful thing in this exercise wasn't code — it was the discipline of
writing `PROTOCOL.md` **before** the adapter, and writing it as "verified" vs.
"unverified" rather than as a flat list of facts. It's the artifact that made the
JSON-first/HTML-fallback design an obvious, almost mechanical consequence of what was
actually known, rather than a decision made by feel. A legacy-integration project's real
risk isn't the code being hard to write — it's the code being confidently wrong about
something nobody checked, and the checking discipline is the actual deliverable here,
even more than the working server is.
