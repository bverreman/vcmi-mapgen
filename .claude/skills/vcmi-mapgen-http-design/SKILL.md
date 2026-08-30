---
name: vcmi-mapgen-http-design
description: "The stack-neutral contract for designing an HTTP API — operate on resources, never verbs; no request body in GET or DELETE; how to model actions that are not CRUD (a cache refresh, a send, a retry) as sub-resources instead of RPC verbs; batch requests as an explicit collection operation; ordering, pagination and searching as query-string contracts on collections and sub-collections; status codes that say what happened rather than 200-with-an-error-body. OpenAPI-first where the repo carries a contract file: the spec is edited before the handler. Load when adding or changing any HTTP endpoint; hexagonal-architecture governs where the handler sits (an adapter), this governs the shape of the surface it exposes."
metadata:
  generated_by: farrier
  source: library/skills/architecture/http-design/SKILL.md
  resolve: "farrier source .claude/skills/vcmi-mapgen-http-design/SKILL.md"
  do_not_edit: "generated — run the `resolve` command below for this machine's editable source path, edit that, then `make agent-install` to regenerate"
  tags: [standards]
---

# The HTTP Surface Contract

This is the stack-neutral definition of a well-shaped HTTP API. It states the *contract*; the
mechanics — router, framework, serializer, validation library — are stack-specific and live in
the matching stack skill. The [`../vcmi-mapgen-hexagonal-architecture/SKILL.md`](../vcmi-mapgen-hexagonal-architecture/SKILL.md)
skill governs where the handler sits (in the adapter ring, translating the wire into domain
calls); this skill governs the shape of the wire itself.

The point is that the surface is **predictable**: a consumer who has seen three of your
endpoints can guess the fourth. Every rule below trades a moment of design-time convenience for
that predictability, and every violation is a special case a client must be told about.

## 1. Operate on resources, not verbs

The URL names a **thing**; the method supplies the verb. Nouns, plural, hierarchical:

```text
GET    /projects                    # list the collection
POST   /projects                    # create in the collection
GET    /projects/{id}               # read one
PATCH  /projects/{id}               # partial update
PUT    /projects/{id}               # full replacement (only if you truly support it)
DELETE /projects/{id}               # remove
GET    /projects/{id}/members       # a sub-collection, same grammar one level down
```

**Trigger.** A verb in the path — `/createProject`, `/getUsers`, `/project/delete` — or a
`method` / `action` field in the body selecting what the endpoint does. Both are RPC wearing an
HTTP costume: they defeat caching, method semantics, and every generic client.

**Counter-case.** Some operations genuinely are not CRUD on a resource. That is §2, and it has
a shape — it is not permission to fall back to verbs.

## 2. Actions that are not CRUD are modelled, not bolted on

A cache refresh, a resend, a cancel, a deploy — the operation is real, and `PATCH` on some
status field is often a lie about what happens. Model it one of two ways, in order of
preference:

- **As a state you PATCH, when the action is a state transition.** Cancelling an order *is*
  `PATCH /orders/{id}` with `{"status": "cancelled"}` — the action vocabulary stays in the
  resource where a GET can see it.
- **As a sub-resource you POST to, when the action is an event with its own lifecycle.**
  `POST /caches/{id}/refreshes`, `POST /messages/{id}/sends`, `POST /builds/{id}/retries`.
  The POST creates a *record of the action* — which gives you an id to poll for a slow one
  (`GET /caches/{id}/refreshes/{rid}` → `202` semantics done honestly) and a history for free.

**Trigger.** `POST /caches/{id}/refresh` (bare imperative verb as a leaf), or an endpoint whose
body is `{"action": "..."}`.

The second form is the general one: any "do something to this resource" becomes "create an
occurrence of that something under it". If the occurrence could never plausibly be listed or
polled, the action is probably a state transition — use the first form.

## 3. The method carries the semantics — and its constraints

- **GET and DELETE take no request body.** Intermediaries may drop it, servers may ignore it,
  and no spec defends it. Everything a GET needs is in the path and the query string. A "search
  is too big for a URL" case is §5's escape, not a body on GET.
- **GET is safe and cacheable** — it changes nothing. A GET that mutates (marks-as-read,
  increments a counter) will be triggered by prefetchers and crawlers, and the bug reports will
  say "it happened by itself".
- **PUT and DELETE are idempotent.** Retrying them is always safe; design them so that is true
  (a second DELETE returns `404` or `204`, never an error that makes the caller believe the
  first one failed).
- **POST is neither**, which is exactly why creation and action-occurrences (§2) live there —
  and why a retryable client needs an idempotency key on POSTs that must not double-execute.
- **PATCH is a partial update** with explicit semantics: absent field = unchanged, `null` =
  clear. Say which convention the API uses once, globally, and never vary it per endpoint.

## 4. Batch operations are an explicit collection operation

When a client needs to act on many resources in one round trip, the batch is **its own
declared shape**, not a loop the client is told to write and not a comma-hack in the path:

```text
POST /projects/batch-get        { "ids": [...] }          # bulk read, body allowed: it's POST
POST /projects/{id}/members/batch   { "add": [...], "remove": [...] }
```

- The batch endpoint **names its atomicity**: all-or-nothing, or per-item results. Per-item is
  the honest default — return `207`-style per-item statuses in the body so one bad item does
  not make the caller guess which of the other 99 landed.
- A batch is a POST even when it only reads (batch-get): the id list belongs in a body, and
  GET may not carry one (§3).

**Trigger.** `GET /projects/1,2,3`, or a client-side for-loop in the consumer's code that
exists because the API offered nothing better.

## 5. Collections answer ordering, pagination and search in the query string

Every collection and sub-collection endpoint owes the same three contracts, spelled the same
way across the whole API:

- **Ordering**: one parameter, one grammar — e.g. `?sort=created_at` ascending,
  `?sort=-created_at` descending, comma-separated for tie-breaks. A collection without a
  declared default order has an *undefined* order, and it will change under load or reindexing;
  declare the default.
- **Pagination**: pick cursor (`?cursor=`, opaque) or offset (`?limit=&offset=`) **once** for
  the API and state it. Cursor survives inserts and deletes mid-walk; offset does not — prefer
  cursor for anything that grows. The response carries the next cursor / total explicitly.
- **Searching / filtering**: field filters as named parameters (`?status=open&owner=…`), free
  text as one parameter (`?q=`). When a query outgrows a URL, the escape is a **search
  sub-resource** — `POST /projects/searches` with the criteria as a body (§2's occurrence
  pattern) — never a body on GET.

Sub-collections inherit all of this: `GET /projects/{id}/members?sort=-joined_at&limit=50` must
work the way `/projects` does. A consumer should never have to learn per-endpoint dialects.

## 6. Status codes say what happened; the error body says why

- `200` result in body · `201` created (echo the resource, include its URL) · `202` accepted,
  work continues (§2 gives the poll target) · `204` done, nothing to say.
- `400` malformed request · `401` who are you · `403` you may not · `404` no such resource ·
  `409` conflicts with current state · `422` well-formed but invalid.
- **Never `200` with an error in the body.** Every generic client, cache, and monitor reads
  the status code first; a 200-wrapped failure is invisible to all of them.
- One **uniform error shape** across the API — a machine-readable code, a human message, and
  the offending field where there is one. A consumer writes its error handling once.

## 7. OpenAPI-first where the repo carries a contract

If the repo has an OpenAPI/spec file, **the spec is edited before the handler** — the contract
is designed as a contract, reviewed as one, and the implementation follows it. Generated
clients, validation middleware and docs all hang off the spec; a handler that drifts from it is
a defect even while every test passes.

**Trigger.** A diff that changes a route, parameter, or response shape without touching the
spec file sitting beside it.

Where there is no spec file, this rule is dormant — but the rest of this contract is exactly
what makes retrofitting one cheap.

## Review checklist

Before approving an endpoint — yours or someone else's:

- [ ] The path names a resource; the method is the only verb.
- [ ] No GET or DELETE carries a request body, and no GET mutates.
- [ ] Non-CRUD actions are a state transition or an occurrence sub-resource, not a bare verb.
- [ ] Batch operations declare their atomicity and report per-item outcomes.
- [ ] The collection answers `sort` / pagination / filtering in the API's one shared grammar.
- [ ] Failures use the status code, and the error body is the API's uniform shape.
- [ ] The spec file (where one exists) changed in the same diff as the handler.

## Smells

| Symptom | What it usually means |
| --- | --- |
| A verb in the path (`/getUsers`, `/project/delete`) | RPC-over-HTTP; resource modelling skipped (§1) |
| `{"action": "..."}` dispatch in a request body | One endpoint hiding an API (§1, §2) |
| A request body documented on a GET | Search outgrew the URL and took the wrong escape (§3, §5) |
| Clients loop over single-item calls | The batch operation was never designed (§4) |
| Each list endpoint paginates differently | No collection grammar; consumers learn N dialects (§5) |
| Monitors green while users see failures | `200` with an error body (§6) |
| Spec file untouched while routes changed | Handler-first drift; the contract is fiction (§7) |
