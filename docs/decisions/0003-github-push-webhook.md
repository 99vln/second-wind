# 0003. GitHub push webhook: which commits count, and how the endpoint fails

- Status: Accepted
- Date: 2026-11-11

## Context

v0.1 turns GitHub commits into XP (ADR 0002). Commits arrive as push webhooks on a public URL, so anyone can send requests to it, and GitHub's push payload contains far more than the user's own new work: other people's commits, bot commits, merge commits made with the web UI, and on a new remote, a repository's whole history.

## Decision

**Endpoint: `POST /api/webhooks/github`.** Checks run in this order, and each one stops the request:

| Check | Response |
| --- | --- |
| `GITHUB_WEBHOOK_SECRET` or `GITHUB_USERNAME` not set | 503, nothing processed (fail closed) |
| Body over 1 MB, read in chunks so an oversized body is never held whole | 413 |
| `X-Hub-Signature-256` missing or not the HMAC-SHA256 of the raw body | 401, no reason given |
| `X-GitHub-Event: ping` | 200 `{"ok": true}` |
| Any event but `push` | 202 `{"ignored": "<event>"}` |
| Body not JSON, or not a push payload | 400 |
| `push` | 200 with counts: received, counted, new, duplicates, too_old, XP and Reserve moved |

The signature is compared with `hmac.compare_digest` on the raw bytes, before any parsing. A missing username is treated like a missing secret: GitHub shows the failed delivery, and a redelivery works once the setting is fixed, instead of the commits being silently skipped.

**Which commits count.** A commit earns XP only if its `author.username` equals `GITHUB_USERNAME` (ignoring case) and its `committer.username` is not `web-flow`. Commits without a username (bots such as Dependabot, emails not linked to an account) earn nothing, and so do merge and squash commits made with GitHub's buttons, which repeat work already counted on the branch. Every branch counts; the same commit pushed to two branches is counted once, by its SHA. Each counted commit's raw object is stored as the event payload.

**When they count.** A push's commits are recorded oldest first, all with the time the push was received, so they count for that local day (ADR 0002). Commits more than 7 days old on arrival are ignored.

**Logging.** The `X-GitHub-Delivery` id and the counts are logged. The body, the signature and secrets never are.

**`GET /api/progress`** returns XP, level, today's Energy (with Second Wind), the Reserve balance and the 10 latest wins. It is read-only and has no authentication: v0.1 has one user and nothing to change.

## Consequences

- Rejected requests cost little: no database work happens before the signature is verified, and the body read stops at 1 MB.
- A push larger than 1 MB is refused whole. Normal pushes are far below that; a huge history push would mostly be too old to count anyway.
- `/api/progress` shows repository names and commit subjects to anyone who knows the URL. That includes private repositories that send webhooks. When there is more than one user, or private repositories need hiding, the route needs authentication.
- Commits authored with an email not linked to the GitHub account do not count. The fix is on GitHub's side (add the email to the account), not a looser filter.
