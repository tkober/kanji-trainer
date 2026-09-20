# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is for

A kanji trainer built around the one thing WaniKani will not do: let the
learner say **"das kann ich"**.

WaniKani's SRS has no override. After a long break, the only ways back in are
to dig out from thousands of overdue reviews or to reset and re-earn material
you already know at four hours a step — sixty levels at a 3.5-day minimum is
seven months of waiting for content, not of learning it. This app imports the
WaniKani account, reads `assignments.srs_stage` to find out what the learner
already knows, and starts from there. Everything else exists to support that.

When a change would make declaring an item known slower, rarer or less
trustworthy, it is probably the wrong change.

## Language convention

Conversation with the user happens in **German**. Everything in the repository —
identifiers, comments, commit messages, documentation — is **English**.

The exception is user-facing UI copy, which is German because the learner is a
German speaker: Angular templates, the German lead-in on error messages, and
the feedback hints in `backend/app/answers.py`. Backend `HTTPException` details
stay English and the frontend prefixes them with a German sentence
(`rethrow()` in `frontend/src/app/core/api.ts`).

`ImportRun.message` is the one German string stored in the database. It is
rendered verbatim on the import screen, so it is UI copy rather than an error
message — `importer.py` wraps the English `WaniKaniError` in a German sentence
before writing it.

SRS stage names (`Apprentice I`, `Guru`, `Burned`) stay English in the UI too.
They are the vocabulary the learner already thinks in; translating them would
make their own history unreadable to them.

## Working in this repository

Every change goes on a feature branch and reaches `main` through a pull
request. Do not merge locally and push `main` — a branch that is already an
ancestor of `main` cannot be turned into a PR afterwards, and the review never
happens.

```bash
git checkout -b feature/<topic>
# ... commit ...
git push -u origin feature/<topic>
# then open a PR against main and leave the merge to the repository owner
```

## Commands

```bash
docker compose up --build   # whole stack incl. Postgres on :8086
docker compose -f compose.sqlite.yaml up --build   # same, but SQLite in a volume
./dev.sh                    # dev servers; frontend proxies /api to :8000

cd backend
uv sync                     # install
uv run uvicorn app.main:app --reload --port 8000
uv run pytest               # needs Docker: testcontainers starts a Postgres
TEST_DB=sqlite uv run pytest   # same suite against a temp SQLite file, no Docker
uv run pytest tests/test_srs.py::test_falling_out_of_guru_counts_a_lapse

cd frontend
npm start                   # ng serve on :4200
npx ng build                # AOT + template type-check
```

The frontend has no spec files, so `ng test` finds nothing. `ng build` is the
correctness gate: it type-checks templates, so run it after touching any
component. The backend suite is the real safety net and `srs.py` is where it
matters most.

## Persistence

Postgres on the shared `postgres-core` instance, using the same two-role split
as the other stacks: an **owner** role runs DDL in `init_db()` at startup, an
**app** role serves every request. The app role's access comes from server-side
`ALTER DEFAULT PRIVILEGES` (bootstrap SQL in `deploy/kanji_trainer/bootstrap/`),
so no GRANT is issued from code.

`migrate_schema()` is the hook for column additions — `create_all` only creates
missing *tables*, so anything else has to go there, idempotent and append-only
via `ADDED_COLUMNS`.

`RuntimeConfig` (`runtime_config.py`) is what the request paths take, not raw
settings: environment defaults with the `app_settings` row layered on top. A
NULL column means "not set here", so clearing a field in the Settings UI falls
back to the environment rather than blanking it. It is loaded per request — the
table has one row, and a stale WaniKani token or interval table after a
settings change would be worse than the lookup.

The WaniKani token never leaves the backend: `/api/settings` returns only
whether one is set, a four-character hint, and whether it came from the
environment (which the UI needs in order to explain why an env token cannot be
cleared from the browser).

Tests run against a throwaway Postgres via testcontainers, reproducing the
owner/app split, so a stray DDL statement in a request path fails there rather
than at deploy time. HTTP tests use `httpx.ASGITransport` rather than
`TestClient`: the latter runs the app on its own event loop in a worker thread,
which the shared SQLAlchemy engine cannot be used from.

### SQLite, for a machine with no Postgres

A `sqlite://` `DB_URL` runs the same schema out of a local file
(`compose.sqlite.yaml` is that stack). It is a second backend, not a second
code path: everything that differs is collected in `db.py`, and nothing above
that module knows which one is in use. What differs:

- **The roles collapse.** `_role_url()` returns the same file for both, because
  the owner/app split is a Postgres privilege boundary and SQLite has nothing
  to enforce it with. The Postgres test run still exercises the split for real.
- **`JSONColumn`** is `JSON` with a `JSONB` variant for Postgres.
- **`UtcDateTime`** exists because SQLite has no timestamp type:
  `DateTime(timezone=True)` reads a *naive* datetime back, FastAPI serialises
  it without a zone, and the browser reads it as local time — a review due at
  18:00 UTC would look due at 18:00 local. The type attaches UTC on the way out.
- **Boolean `server_default`s must be `false()`, not `"false"`.** SQLAlchemy
  quotes a string default as a literal, so SQLite stores the *text* `'false'`
  and reads it back as `True`.
- **`migrate_schema()` reflects instead of `ADD COLUMN IF NOT EXISTS`**, which
  SQLite does not have. Add to `ADDED_COLUMNS`, not to the SQL.
- **`_upsert()`** picks the dialect's `insert`; both offer `on_conflict_*` with
  the same arguments but neither accepts the other's construct.
- **Three PRAGMAs on every connection** (`_configure_sqlite_connection`):
  `foreign_keys` (off by default, and the `ON DELETE` clauses are silently
  ignored without it), `journal_mode=WAL` (an import writes for minutes) and
  `busy_timeout`.
- **`_wait_for_database()` does not wait.** A file is openable now or never, so
  it fails immediately with the path named instead of retrying for a minute.

## Architecture

```
WaniKani ──import──> subjects + progress ──> lessons ──> reviews
                          ▲                                │
                          └──────── the overrides ─────────┘
```

**`srs.py` is the core and has no dependencies.** No database, no clock — `now`
is passed in and the progress row is mutated in place. That is what lets
`test_srs.py` exercise every rule against a plain object, and it is worth
protecting: this module decides how much work the learner is asked to do.

**`answers.py` is deliberately asymmetric.** Meanings forgive typing
(Levenshtein against every accepted and whitelisted meaning, allowance scaled
by length); readings forgive nothing beyond kana folding and the romaji
fallback. A reading one kana off is a *different reading*, and accepting it
would drill the wrong word while reporting success.

**`importer.py`** runs in the background (`asyncio.create_task`) and reports
through the `import_runs` row, which the UI polls. A synchronous import of
~9.000 subjects would hit nginx's read timeout long before it finished.

**Frontend state lives in component signals**; the app is zoneless, so anything
the UI must react to has to be a signal. There is no shared store — each screen
fetches what it needs, and the only cross-screen state is the due badge in
`app.ts`, which polls once a minute.

## Invariants worth preserving

**A queue item never carries its answers.** `SubjectSummary` and
`SubjectDetail` in `models.py` differ by exactly that, and `serialize.py` has
one function for each. `GET /api/reviews` returns summaries; the detail comes
back only in the *answer response*. An SRS the learner can read ahead in is not
an SRS.

**`state` and `srs_stage` are both stored, and are not redundant.** Stage 9 is
reachable two ways — eight correct reviews, or one press of "das kann ich" —
and `ItemState.KNOWN` is what tells them apart. `GET /api/stats` counts
`burned_count` only for `state == learning`, so the dashboard stays honest
rather than flattering. Do not collapse these.

**The pending flags live in the database, not the session.**
`progress.pending_meaning` / `pending_reading` are NULL while no review is in
flight and hold the outstanding questions once one is. Closing the tab after
answering the meaning of a kanji must not hand back a free pass on the reading.
They are initialised on the first *answer*, not when the queue is served — a
GET that writes is surprising, and an item merely looked at should be untouched.

**A wrong answer does not demote immediately.** It is counted in
`session_incorrect` and the question stays outstanding, so the learner is asked
again in the same session and the item moves exactly once, when it is fully
answered. That is what makes the "every two mistakes costs one step" rule mean
anything: missing both halves of a kanji is one demotion, not two.

**The penalty doubles at Guru.** `penalty_factor()` — an item the learner had
genuinely secured and then lost needs more re-exposure than one that never got
there. `lapses` counts only the crossings that actually leave Guru.

**`mark_known` works on any item in any state, including one never learned.**
Skipping the lesson entirely is exactly what someone returning after a break
needs for the first thirty levels. Adding a state check here would break the
feature the app exists for.

**The import does not reset local progress unless asked.** `remap_existing` is
off by default, so a second import picks up new WaniKani content without
undoing reviews done since the first one. `_apply_assignments` collects the
already-touched subject ids up front for exactly this.

**Items below the threshold keep WaniKani's own due date.** `available_at` is
used where present, so an item due in two days is not pulled forward just
because the import ran today.

## The import, in detail

Three outcomes per item, decided by `classify()`:

| WaniKani `srs_stage` | becomes | scheduled |
|---|---|---|
| ≥ threshold (default 5, Guru I) | `KNOWN` at `known_srs_stage` | no |
| 1 … threshold−1 | `LEARNING` at the same stage | yes, at WaniKani's `available_at` |
| 0 or no assignment | `NEW` (a lesson) | no |

The threshold is a setting because the right value is not knowable in advance:
it is the answer to "how much do I trust my Guru items after eight months
away", and the honest way to find it is to import, do a few reviews, and import
again with `remap_existing`.

Subjects are upserted on `wanikani_id`, which is **not** the primary key — the
trainer is meant to outlive the subscription that seeded it, and hand-added
items have no WaniKani id. `_resolve_components()` rewrites
`component_subject_ids` from WaniKani ids to local ones in a second pass,
because a kanji can list a radical that had not been inserted yet.

A lapsed subscription does not fail the import; it quietly returns three
levels' worth of subjects, which looks like a bug much later.
`_subscription_warning()` names it on the screen that reports the result.

## Deployment

Two GHCR images, built by GitHub Actions on push to `main`. Only the frontend
publishes a port (8086); its nginx serves the SPA and reverse-proxies `/api` to
the backend over the internal network, which is why no CORS is involved and the
backend port stays unpublished.

Ports already taken on the target host: 3141, 5432, 5678, 5900, 8000, 8001,
8080–8085, 8765, 18789. Hence 8086.

Database bootstrap is manual, like the other projects: `dbeaver/` holds the SQL
to create the roles and database, run by hand against postgres-core with the
`${...}` password placeholders substituted. `dbeaver/verify.sql` exists because
the failure mode is silent — Postgres answers a missing role with the same
`28P01` it uses for a wrong password, so "authentication failed" does not tell
you which of the two happened.

The stack directory (`deploy/kanji_trainer/`) is meant to be copied into the
`compose-stacks-unraid` repo. `compose.yaml` at the repo root is the local
mirror of it, down to creating both Postgres roles via `dev/initdb`.

**Installing it as a PWA needs TLS.** Service workers are `[SecureContext]`, so
over plain HTTP to a LAN address (`http://<host>:8086/`) one cannot register.
The app works normally in a browser there; "installed on the phone, offline"
needs a reverse proxy with a certificate in front. Nothing in the code assumes
either way.

## Known gaps

- No offline support and no service worker yet — see the TLS note above.
- No lesson gating: nothing waits for its radicals to reach Guru first. That is
  deliberate (the learner may jump ahead) but means the lesson order is only a
  suggestion.
- The review queue is served in due order and shuffled nowhere, so a long
  backlog is always worked oldest-first.
- `_apply_assignments` issues one UPDATE per assignment. At ~9.000 rows that is
  slow but bounded, and it only runs during an import.
- No authentication. Single user, LAN only — the same assumption the other
  stacks make.
