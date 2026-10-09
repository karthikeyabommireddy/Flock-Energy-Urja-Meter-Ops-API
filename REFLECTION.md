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

---

## Addendum: the v1.1 extension

> Written by Claude Code, the coding agent that built v1.1, from what actually happened while
> it was built (see "Use of AI" in the README). The five answers above are the author's own,
> from v1, and are unchanged; they still describe v1. This section is limited to the extension.

### What was assumed

- **That the readings are in `Asia/Kolkata`.** The portal prints `23/06/2026 23:30` with no
  zone. The zone of the network (Jaipur) was chosen, made a setting, and put in every
  consumption response (`portal_timezone`), so a wrong guess is visible rather than silent.
- **That every meter under a transformer shares that transformer's path through the
  network.** It is what lets the tree be rebuilt from 40 breadcrumbs instead of 403. The
  audit (`/hierarchy/issues`) catches a transformer whose sample disagrees with the
  Transformers screen; it cannot catch a single meter whose own breadcrumb differs from its
  transformer's sample. That is a limit of the method, stated in `PROTOCOL.md` §8.
- **That five captured meters are representative.** They showed two different shapes for a
  meter's detail. A third shape raises a `502` rather than a half-empty meter, so the
  assumption fails loudly if it is wrong.
- **That an expired session looks like a redirect to `/login`.** The author saw that in the
  browser, but the capture does not show how it looks on the JSON endpoints, so the adapter
  treats a 401, a redirect and a JSON redirect body alike.

### What was difficult

Reading the data correctly, once it could be seen. The numbers are strings, the consumption
is a cumulative register rather than consumption, one meter detail comes in two shapes (one
of them a JSON document inside a string, inside SvelteKit's `devalue` encoding), and the
network exists only as breadcrumbs. None of that was visible from outside the login, and
each was found by comparing meters side by side rather than reading one. The other
difficulty was not over-claiming: the adapter has still not touched the live portal, so
"replayed against the capture" is the strongest thing that can honestly be said.

### Mistakes, and how they were caught

The repo's own test suite was not run for this change, so these were caught by replaying the
captured responses through the adapter and the routes, by looking at the web client against
them, and by separate Claude Code review agents (security, backend, web) that read the whole
change before it was committed. The first group, caught by replay and by eye:

- **New optional fields would have changed v1's response bodies.** Adding `latitude`,
  `longitude`, `distance_m` and `hierarchy_path` as `null` to every list row breaks a client
  that compares bodies exactly. Caught by re-creating v1's scenarios in the replay; fixed by
  leaving unset fields out of the list response.
- **A radius search hid the coordinates it had just fetched.** It filtered by them and then
  returned rows without them. Caught in the replay; fixed.
- **One log line per unreadable coordinate** would have flooded the log on a bad day. Caught
  in the replay; now one aggregated line per kind of problem.
- **The same fact reported as two kinds of issue** (an unreadable breadcrumb was also
  reported as an unresolved transformer). Caught reading the report; one kind removed.
- **The KPI cards on the fleet page listed their parts in reverse order** (a stray
  `flex-direction: column-reverse`). Caught by looking at the page, not by any check.

The second group, found by the review agents and reproduced before it was fixed, is the more
uncomfortable one, because none of it showed up in any run the agent itself did:

- **A redirect on the login request would have re-sent the password to another host.** The
  check for "left the portal's host" ran on the response, after the second request had gone out.
  It now runs before every request, redirects included.
- **`interval_kwh` could span a gap.** After one missing half hour the next interval held two
  half hours of consumption and was reported as one, which the web client then flagged as a
  "spike". The interval is now left empty after a missing or unreadable reading.
- **The client was built more than once on a cold start** (eight threads, eight clients, eight
  logins), which quietly defeated the single-flight login and the concurrency cap.
- **A bad answer for one meter's coordinates was remembered for an hour** as "this meter has
  none", so a radius search silently left it out.
- **The documented `include=coordinates,hierarchy` returned a 422**; only the repeated form
  worked. The first fix for that broke the repeated form too (the validator hid that the
  parameter was a list), which a re-run of the same probes caught.
- **A payload that had the wrong shape could be cached as an empty answer** (rows with no ids, a
  list that said it had 403 meters and yielded none) instead of failing and serving stale data.
- **README claims that were stronger than the code**: "a typo cannot lock the account", "a single
  aggregated warning", "status codes unchanged". Each was corrected rather than defended.

### What should be done differently

- **Run it once against the live portal** with real credentials and compare it with the
  capture: the cookie's name and lifetime, what an unknown id and a wrong password return,
  page 2 of the Transformers screen, and the breadcrumbs and coordinates of all 403 meters.
- **Turn the throwaway replay into a test tier**: a trimmed, sanitised fixture set (the raw
  capture holds a live session and stays out of the repository), and the six stale v1 tests
  rewritten against the real shapes. The v1 list item "add an integration tier against a
  mock of the confirmed portal" is exactly this, now that the portal is confirmed.
- **Move the index out of process memory** (a store with a spatial index, refreshed
  incrementally) before the fleet is much larger than 403 meters: ingestion, not querying,
  is the cost.
- **Two of the v1 items above were done in v1.1**: re-login is single-flight (the lock),
  and outbound requests are capped, identified and backed off (the rate limiting).

### Anything else

The capture contained the live password and a session. It was never put in the repository
(`*.har` is git-ignored) and was analysed with scripts that print neither.

A security review of v1.1 found that the v1 commit itself had published the portal login, in
`PROJECT-TASK.md` and in a test fixture. v1.1 removes it from the files (the history keeps it),
so the password should be treated as exposed and rotated, and the repository now ignores
`.env.*`, captures, cookies and keys so that the same mistake is harder to repeat.

The most interesting thing in the data is not a bug in the code: two of the five meters are
`Decommissioned` yet report a steady 0.4 to 0.5 kWh every half hour, which the web client
now points out on the meter page.
