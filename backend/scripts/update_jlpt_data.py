"""Refresh the JLPT snapshot in app/data/jlpt/ from its upstream sources.

The snapshot is checked in on purpose: the lists change rarely, and the app
should not depend on a third-party site at import time. To update, bump the
pinned commits below, run

    uv run python scripts/update_jlpt_data.py

from backend/, review the diff and update the commits in
app/data/jlpt/README.md.

Both sources go back to Jonathan Waller's JLPT lists (tanos.co.uk, CC BY).
The files are reduced to what the trainer needs: kanji-data also carries
WaniKani's meanings and readings, which this repository must not ship.
"""

import csv
import io
import json
import urllib.request
from pathlib import Path

VOCAB_REPO = "jamsinclair/open-anki-jlpt-decks"
VOCAB_COMMIT = "1ad66734417aca9dbcca6b2d5ee440cb13ab3ba0"
KANJI_REPO = "davidluzgouveia/kanji-data"
KANJI_COMMIT = "00fd7079c3890f430759536f91aa5e854ec0ca4f"

# Easiest first, the order the upstream decks use.
LEVELS = (5, 4, 3, 2, 1)

OUT_DIR = Path(__file__).resolve().parent.parent / "app" / "data" / "jlpt"


def fetch(repo: str, commit: str, path: str) -> str:
    url = f"https://raw.githubusercontent.com/{repo}/{commit}/{path}"
    with urllib.request.urlopen(url, timeout=30) as response:
        return response.read().decode("utf-8")


def write_csv(path: Path, header: list[str], rows: list[list[object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def vocab_rows() -> list[list[object]]:
    # Expression and reading are copied verbatim, including the upstream
    # notation for variants ("足; 脚"), suru verbs ("べんきょう (する)") and
    # affixes ("～ごろ"). Normalising them is the consumer's job. A word listed
    # at two levels keeps both rows.
    rows: list[list[object]] = []
    for level in LEVELS:
        text = fetch(VOCAB_REPO, VOCAB_COMMIT, f"src/n{level}.csv")
        for entry in csv.DictReader(io.StringIO(text)):
            rows.append([level, entry["expression"].strip(), entry["reading"].strip()])
    return rows


def kanji_rows() -> list[list[object]]:
    data = json.loads(fetch(KANJI_REPO, KANJI_COMMIT, "kanji.json"))
    rows = [
        [level, kanji]
        for kanji, info in data.items()
        if (level := info.get("jlpt_new")) is not None
    ]
    # Stable order: easiest level first, then the upstream order.
    rows.sort(key=lambda row: LEVELS.index(row[0]))
    return rows


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    vocab = vocab_rows()
    kanji = kanji_rows()
    write_csv(OUT_DIR / "vocab.csv", ["jlpt", "expression", "reading"], vocab)
    write_csv(OUT_DIR / "kanji.csv", ["jlpt", "kanji"], kanji)
    for name, rows in (("vocab", vocab), ("kanji", kanji)):
        counts = ", ".join(
            f"N{level}: {sum(1 for row in rows if row[0] == level)}" for level in LEVELS
        )
        print(f"{name}: {len(rows)} rows ({counts})")


if __name__ == "__main__":
    main()
