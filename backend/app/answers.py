"""Deciding whether an answer was right.

Two very different jobs behind one door. A *meaning* is English prose and has
to tolerate the learner's typing; a *reading* is kana and has to tolerate
nothing at all, because a wrong kana is a wrong reading. The asymmetry is
deliberate and is the single most important thing in this module: loosening
the reading check would quietly teach the wrong word.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable

# Katakana and hiragana occupy parallel blocks 0x60 apart, so folding one onto
# the other is arithmetic rather than a table. Applied to both sides of every
# comparison: WaniKani spells a katakana word's reading in katakana and a
# kanji's on'yomi in hiragana, and the learner should not have to know which.
_KATAKANA_START = 0x30A1
_KATAKANA_END = 0x30F6
_KANA_OFFSET = 0x60

_WHITESPACE = re.compile(r"\s+")
# Punctuation a learner might type around a meaning without meaning anything
# by it. The apostrophe stays: "child's" and "childs" are different answers
# only by accident, but stripping it would also merge readings typed as n'.
_MEANING_NOISE = re.compile(r"[.,!?;:\"()\[\]{}]")
_ASCII_LETTERS = re.compile(r"^[a-z' -]+$")


@dataclass(frozen=True)
class AnswerCheck:
    """The result of checking one answer."""

    correct: bool
    #: The accepted answer closest to what was typed, for the feedback line.
    expected: str
    #: True when the answer was right but not the *primary* reading/meaning --
    #: worth showing, never worth marking wrong.
    secondary: bool = False
    #: Set when an answer was rejected on purpose rather than merely missed:
    #: a blacklisted meaning, or the on'yomi where the kun'yomi was asked.
    hint: str | None = None
    #: "Not wrong, just not what was asked." A real reading of this kanji, of
    #: the type the question did not ask for. WaniKani re-asks rather than
    #: counting it wrong, and so does this: the learner knew the character.
    retry: bool = False
    #: Accepted, but not spelled the way the item spells it. Worth showing --
    #: silently forgiving a typo teaches the typo.
    typo: bool = False


# --- normalisation ---------------------------------------------------------


def normalise_kana(value: str) -> str:
    """Fold a reading to one comparable form.

    NFKC first, because a full-width Latin character or a half-width katakana
    pasted from elsewhere should not be a wrong answer. Then katakana to
    hiragana, then whitespace out.
    """
    folded = unicodedata.normalize("NFKC", value).strip()
    folded = _WHITESPACE.sub("", folded)
    return "".join(
        chr(ord(char) - _KANA_OFFSET)
        if _KATAKANA_START <= ord(char) <= _KATAKANA_END
        else char
        for char in folded
    )


def normalise_meaning(value: str) -> str:
    """Fold a meaning to one comparable form."""
    folded = unicodedata.normalize("NFKC", value).casefold().strip()
    folded = _MEANING_NOISE.sub(" ", folded)
    return _WHITESPACE.sub(" ", folded).strip()


def levenshtein(left: str, right: str) -> int:
    """Edit distance, two rows at a time.

    Written out rather than pulled in: it is fifteen lines, it runs on every
    meaning answer, and a dependency here would be the only one in the
    request path.
    """
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)

    previous = list(range(len(right) + 1))
    for i, left_char in enumerate(left, start=1):
        current = [i]
        for j, right_char in enumerate(right, start=1):
            current.append(
                min(
                    previous[j] + 1,  # deletion
                    current[j - 1] + 1,  # insertion
                    previous[j - 1] + (left_char != right_char),  # substitution
                )
            )
        previous = current
    return previous[-1]


def typo_allowance(expected: str, divisor: int = 7) -> int:
    """How far off a meaning may be and still count.

    Approximates WaniKani's rule: nothing under four characters gets any
    slack, and longer answers get roughly one edit per ``divisor``
    characters. Short words are excluded because at three characters almost
    every other word is one edit away.

    This does accept a near-miss that happens to be a real word -- "hire" for
    "fire". WaniKani has the same hole, and the guard against it is the
    blacklist in ``auxiliary_meanings`` rather than a tighter distance, which
    would cost far more correct answers than it saves wrong ones.
    """
    length = len(expected)
    if length < 4:
        return 0
    return 1 + max(0, (length - 4) // max(1, divisor))


# --- meanings --------------------------------------------------------------


def _accepted(entries: Iterable[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    return [entry for entry in entries if entry.get("accepted_answer", True) and entry.get(key)]


def check_meaning(
    given: str,
    meanings: Iterable[dict[str, Any]],
    auxiliary_meanings: Iterable[dict[str, Any]] = (),
    typo_divisor: int = 7,
) -> AnswerCheck:
    """Check an English meaning, allowing for typing.

    Order matters: the blacklist is consulted before the typo tolerance, or a
    deliberately excluded answer could be let in as a near-miss of an accepted
    one.
    """
    answer = normalise_meaning(given)
    accepted = _accepted(meanings, "meaning")
    primary = next(
        (entry["meaning"] for entry in accepted if entry.get("primary")),
        accepted[0]["meaning"] if accepted else "",
    )

    if not answer:
        return AnswerCheck(correct=False, expected=primary)

    auxiliary = list(auxiliary_meanings)
    blacklist = {
        normalise_meaning(entry["meaning"])
        for entry in auxiliary
        if entry.get("type") == "blacklist" and entry.get("meaning")
    }
    if answer in blacklist:
        return AnswerCheck(
            correct=False,
            expected=primary,
            hint="This meaning is explicitly excluded for this item.",
        )

    whitelist = [
        entry["meaning"]
        for entry in auxiliary
        if entry.get("type") == "whitelist" and entry.get("meaning")
    ]
    candidates = [entry["meaning"] for entry in accepted] + whitelist

    for candidate in candidates:
        if answer == normalise_meaning(candidate):
            secondary = normalise_meaning(candidate) != normalise_meaning(primary)
            # On a secondary answer `expected` names the *primary* one, because
            # that is the thing the learner has not said yet. Naming the answer
            # they just gave would read as "you meant what you wrote".
            return AnswerCheck(
                correct=True,
                expected=primary if secondary else candidate,
                secondary=secondary,
            )

    # Nothing matched exactly; allow for a typo against the closest candidate.
    best: tuple[int, str] | None = None
    for candidate in candidates:
        distance = levenshtein(answer, normalise_meaning(candidate))
        if best is None or distance < best[0]:
            best = (distance, candidate)

    if best is not None and best[0] <= typo_allowance(normalise_meaning(best[1]), typo_divisor):
        secondary = normalise_meaning(best[1]) != normalise_meaning(primary)
        return AnswerCheck(
            correct=True,
            # A misspelled secondary meaning gets the primary, since that is
            # the more useful of the two things to show; a misspelled primary
            # gets its own correct spelling.
            expected=primary if secondary else best[1],
            secondary=secondary,
            typo=True,
        )

    return AnswerCheck(correct=False, expected=primary)


# --- readings --------------------------------------------------------------


def check_reading(
    given: str,
    readings: Iterable[dict[str, Any]],
    object_type: str = "kanji",
) -> AnswerCheck:
    """Check a reading. Exact after normalisation -- no typo tolerance.

    A reading that is one kana off is a different reading, and accepting it
    would drill the wrong one. The only latitude is the folding in
    :func:`normalise_kana` and the romaji fallback, neither of which changes
    which reading was given.

    A reading WaniKani knows but does not accept for this item (typically the
    on'yomi of a kanji taught by its kun'yomi) is still wrong, but says so:
    "richtig gelesen, andere Lesung gefragt" is a different mistake from not
    knowing the word, and conflating them wastes the learner's attention.
    """
    entries = list(readings)
    answer = normalise_kana(romaji_to_hiragana(given) if _is_romaji(given) else given)

    accepted = _accepted(entries, "reading")
    primary = next(
        (entry["reading"] for entry in accepted if entry.get("primary")),
        accepted[0]["reading"] if accepted else "",
    )

    if not answer:
        return AnswerCheck(correct=False, expected=primary)

    for entry in accepted:
        if answer == normalise_kana(entry["reading"]):
            secondary = not entry.get("primary", False)
            return AnswerCheck(
                correct=True,
                expected=primary if secondary else entry["reading"],
                secondary=secondary,
            )

    for entry in entries:
        if entry.get("reading") and answer == normalise_kana(entry["reading"]):
            kind = entry.get("type") or "diese Lesung"
            # Not counted. The learner produced a real reading of this
            # character, so they knew it -- they answered a question that was
            # not the one asked. WaniKani re-asks here rather than marking it
            # wrong, and charging for it would punish knowing *more*.
            return AnswerCheck(
                correct=False,
                expected="",
                retry=True,
                hint=f"That is the {kind} reading — a different one was asked for.",
            )

    return AnswerCheck(correct=False, expected=primary)


def _is_romaji(value: str) -> bool:
    """Whether to run the romaji fallback over an answer."""
    return bool(_ASCII_LETTERS.match(value.strip().casefold()))


# Longest-first so that "kyo" wins over "ki", and "tch" over "ch".
_ROMAJI: dict[str, str] = {
    "kya": "きゃ", "kyu": "きゅ", "kyo": "きょ", "gya": "ぎゃ", "gyu": "ぎゅ", "gyo": "ぎょ",
    "sha": "しゃ", "shu": "しゅ", "sho": "しょ", "shi": "し",
    "ja": "じゃ", "ju": "じゅ", "jo": "じょ", "ji": "じ",
    "cha": "ちゃ", "chu": "ちゅ", "cho": "ちょ", "chi": "ち", "tsu": "つ",
    "nya": "にゃ", "nyu": "にゅ", "nyo": "にょ",
    "hya": "ひゃ", "hyu": "ひゅ", "hyo": "ひょ", "bya": "びゃ", "byu": "びゅ", "byo": "びょ",
    "pya": "ぴゃ", "pyu": "ぴゅ", "pyo": "ぴょ",
    "mya": "みゃ", "myu": "みゅ", "myo": "みょ",
    "rya": "りゃ", "ryu": "りゅ", "ryo": "りょ",
    "ka": "か", "ki": "き", "ku": "く", "ke": "け", "ko": "こ",
    "ga": "が", "gi": "ぎ", "gu": "ぐ", "ge": "げ", "go": "ご",
    "sa": "さ", "su": "す", "se": "せ", "so": "そ",
    "za": "ざ", "zu": "ず", "ze": "ぜ", "zo": "ぞ",
    "ta": "た", "te": "て", "to": "と", "tu": "つ",
    "da": "だ", "di": "ぢ", "du": "づ", "de": "で", "do": "ど",
    "na": "な", "ni": "に", "nu": "ぬ", "ne": "ね", "no": "の",
    "ha": "は", "hi": "ひ", "fu": "ふ", "hu": "ふ", "he": "へ", "ho": "ほ",
    "ba": "ば", "bi": "び", "bu": "ぶ", "be": "べ", "bo": "ぼ",
    "pa": "ぱ", "pi": "ぴ", "pu": "ぷ", "pe": "ぺ", "po": "ぽ",
    "ma": "ま", "mi": "み", "mu": "む", "me": "め", "mo": "も",
    "ya": "や", "yu": "ゆ", "yo": "よ",
    "ra": "ら", "ri": "り", "ru": "る", "re": "れ", "ro": "ろ",
    "wa": "わ", "wo": "を", "wi": "ゐ", "we": "ゑ",
    "a": "あ", "i": "い", "u": "う", "e": "え", "o": "お",
    "n": "ん",
}
_ROMAJI_KEYS = sorted(_ROMAJI, key=len, reverse=True)
_VOWELS = "aiueo"


def romaji_to_hiragana(value: str) -> str:
    """Convert romaji to hiragana. A fallback, not the main path.

    The browser converts as the learner types, because seeing かん appear
    while typing "kan" is half the feedback. This exists so the API is usable
    without it -- a curl call, a test, a future import of someone else's deck
    -- and so an answer that arrives as romaji is not simply wrong.
    """
    text = unicodedata.normalize("NFKC", value).strip().casefold()
    out: list[str] = []
    index = 0

    while index < len(text):
        char = text[index]

        # Doubled consonant -> small tsu, except for "nn" which is just ん.
        if (
            char not in _VOWELS
            and char != "n"
            and index + 1 < len(text)
            and text[index + 1] == char
        ):
            out.append("っ")
            index += 1
            continue

        # "n" before a consonant (or at the end) is ん on its own; before a
        # vowel it belongs to the next syllable, which is what n' disambiguates.
        if char == "n":
            following = text[index + 1] if index + 1 < len(text) else ""
            if following == "'":
                out.append("ん")
                index += 2
                continue
            if following not in _VOWELS and following != "y":
                out.append("ん")
                index += 1
                continue

        for key in _ROMAJI_KEYS:
            if text.startswith(key, index):
                out.append(_ROMAJI[key])
                index += len(key)
                break
        else:
            # Not romaji we know -- pass it through so the caller compares it
            # and reports a wrong answer, rather than silently dropping it.
            out.append(char)
            index += 1

    return "".join(out)
