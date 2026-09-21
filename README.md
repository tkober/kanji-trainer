# Kanji Trainer

A WaniKani-style SRS with the one thing WaniKani does not offer: **"I know
this"**.

WaniKani has no way to mark an item as already known. After a long break that
leaves two options — dig out from thousands of overdue reviews, or reset and
re-earn material you already know at four hours a step. Sixty levels at a
3.5-day minimum is roughly seven months of *waiting* for content you have seen
before.

This trainer imports your WaniKani account, reads which items you already have
at Guru or above, and starts you there. Everything at or above the threshold
comes in as known and never enters the review queue. Everything below keeps its
stage *and its WaniKani due date*. Locked items become lessons.

After the import you are independent: your own SRS, your own database, no
subscription needed to keep going.

## What it does

- **Import** from WaniKani via a read-only API token, with an adjustable
  "counts as known" threshold.
- **Reviews** with WaniKani's stage ladder and intervals — familiar pacing,
  but configurable.
- **"I know this"** on any item, any time: during a review (`Alt+K`), in a
  lesson, or in bulk from the item list. Two strengths: *I know these* (retired
  outright) and *fairly sure* (one check-up in four months).
- **"Relearn from scratch"** and **"Hide"** as the counterparts, so nothing is a
  one-way door.
- **A second chance on mistakes.** An answer that would be wrong is held back
  once — `Enter` submits it anyway, `Esc` hands the field back. A typo otherwise
  costs exactly what not knowing the item costs. Answering a kanji with a real
  reading of the type that was not asked for is not counted at all.
- **Lessons** in WaniKani's teaching order, scoped to one level at a time, read
  as a batch and then quizzed as a batch. No dependency gate — jump ahead if you
  want.
- **A review forecast** — what is arriving per hour, and how deep the pile gets
  if you answer nothing.
- Romaji-to-kana input as you type, typo tolerance on meanings and none at all
  on readings.

## Running it

### Whole stack in Docker

```bash
docker compose up --build            # Postgres + backend + UI on http://localhost:8086
docker compose -f compose.sqlite.yaml up --build   # no Postgres; SQLite in a volume
```

### Development

```bash
cp backend/.env.example backend/.env    # point DB_URL somewhere
./dev.sh                                # backend on :8000, UI on :4200
```

`dev.sh` expects `backend/.env` to exist. For a quick start without Postgres,
set `DB_URL=sqlite:///./kanji_trainer.db` in it.

### Tests

```bash
cd backend
uv run pytest                  # against a throwaway Postgres (needs Docker)
TEST_DB=sqlite uv run pytest   # same suite, no Docker
```

## First run

1. Create a **read-only** personal access token at
   *wanikani.com → Settings → API Tokens*.
2. Paste it under **Settings → WaniKani access**, then press *Check* to confirm
   the account and that the subscription is active. Without an active
   subscription the import only sees levels 1–3.
3. Pick the threshold under **Import**. Guru I (stage 5) is the default and is
   what WaniKani itself treats as "this has landed".
4. Start the import. It walks ~9.000 subjects and takes a few minutes; the
   progress bar polls the run.

Do **not** reset your WaniKani account before importing — your SRS stages there
are exactly the data this uses to decide what you already know.

Not happy with the mapping after a few sessions? Change the threshold and
import again with *Re-map items you have already worked on* ticked.

Coming back after a break and you know roughly how far you got? **Items** →
filter by level → *I know these* is the fast way to hand yourself back the
levels you finished once, without relearning them.

## Deploying to unraid

Copy `deploy/kanji_trainer/` into the `compose-stacks-unraid` repository, then:

1. Fill in `env/kanji-trainer-backend/.env` from the `.env.example` beside it.
   Passwords go in **without** quotes.
2. Create the database and roles on `postgres-core`:

   ```bash
   docker exec -i postgres-core psql -U postgres < bootstrap/create_users_and_db.sql
   docker exec -i postgres-core psql -U postgres -d kanji_trainer < bootstrap/grant_privileges.sql
   ```

   (Substitute the `${...}` password placeholders first. `dbeaver/verify.sql`
   in this repo is what to run when the backend reports an authentication
   failure — Postgres reports a missing role and a wrong password identically.)
3. Start the stack. The UI is on port **8086**; the backend publishes nothing
   and is reached through the frontend's nginx.

The images are built by GitHub Actions on every push to `main` and published as
`ghcr.io/tkober/kanji-trainer-backend` and `-frontend`.

**On installing it as a phone app:** service workers need a secure context, so
`http://<host>:8086/` will not install as a PWA. In a browser it works fine.
Put TLS in front of the stack if you want it installed and offline-capable.

## A note on the content

This repository contains no WaniKani content — only the code that imports it.

The subjects an import fetches — meanings, readings, mnemonics — are WaniKani's
copyrighted content. Pulling them into your own trainer with your own active
subscription is a personal-use copy. Publishing or redistributing the resulting
database is not.

## Disclaimer

Unofficial and not affiliated with or endorsed by Tofugu / WaniKani. It talks to
their public API v2 with a read-only token you provide, and never writes back.

## License

[MIT](LICENSE) © Thorsten Kober
