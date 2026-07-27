"""Text -> speakable text.

Two entry points, for the two channels:

    lint()       the voice channel. Claude wrote this line *for the ear*, so the
                 job is mostly to catch slips (a stray path, a 60-word ramble)
                 and hand back notes so the next line is better.

    speechify()  the fallback channel. Raw markdown written for the screen,
                 mangled into something survivable. Always second-best — this is
                 what "reading a report at you" sounds like.

Stdlib only: hooks import this on the prompt path.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

# --------------------------------------------------------------- lexicon

# Dev vocabulary that neural TTS reliably fumbles. Users extend this via
# ~/.claude-speak/lexicon.json (same shape, merged over these).
DEFAULT_LEXICON = {
    "idempotent": "eye-dem-potent",
    "kubectl": "cube control",
    "nginx": "engine ex",
    "psql": "P S Q L",
    "postgres": "post-gress",
    "sqlite": "S Q lite",
    "jwt": "J W T",
    "yaml": "yammle",
    "sudo": "soo-doo",
    "async": "ay-sync",
    "regex": "reg-ex",
    "PR": "P R",
    "PRs": "P Rs",
    "CI": "C I",
    "CLI": "C L I",
    "API": "A P I",
    "UI": "U I",
    "TTS": "T T S",
    "npm": "N P M",
    "AWS": "A W S",
    "S3": "S three",
    "EC2": "E C two",
    "DDB": "dynamo",
    "DynamoDB": "dynamo D B",
    "OOM": "out of memory",
    "SFN": "step functions",
    "e2e": "end to end",
    "WIP": "work in progress",
    "TL;DR": "short version",
    "LGTM": "looks good to me",
}

_lexicon_cache: tuple[tuple, dict, re.Pattern | None] = ((), {}, None)


def _lexicon(home: Path | None = None) -> tuple[dict, re.Pattern | None]:
    """Merged lexicon + a compiled alternation, recompiled when the file changes."""
    global _lexicon_cache
    from . import state

    path = (home or state.home()) / "lexicon.json"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    key = (str(path), mtime)
    if _lexicon_cache[0] == key and _lexicon_cache[2] is not None:
        return _lexicon_cache[1], _lexicon_cache[2]

    lex = dict(DEFAULT_LEXICON)
    if mtime:
        try:
            user = json.loads(path.read_text())
            if isinstance(user, dict):
                lex.update({str(k): str(v) for k, v in user.items()})
        except Exception:
            pass
    # Longest-first so "DynamoDB" wins over "DDB"-style prefixes.
    keys = sorted((re.escape(k) for k in lex), key=len, reverse=True)
    pat = re.compile(r"(?<![\w-])(" + "|".join(keys) + r")(?![\w-])") if keys else None
    _lexicon_cache = (key, lex, pat)
    return lex, pat


def apply_lexicon(text: str) -> str:
    lex, pat = _lexicon()
    if not pat:
        return text
    lower = {k.lower(): v for k, v in lex.items()}

    def sub(m):
        w = m.group(1)
        return lex.get(w) or lower.get(w.lower(), w)

    return pat.sub(sub, text)


# --------------------------------------------------------------- patterns

CODE_FENCE = re.compile(r"```.*?```", re.S)
INLINE_CODE = re.compile(r"`([^`]*)`")
LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
URL = re.compile(r"https?://\S+")
TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$", re.M)
HEADER = re.compile(r"^#{1,6}\s+", re.M)
HEADER_LINE = re.compile(r"^#{1,6}\s+.*$", re.M)  # screen furniture, never spoken
BULLET = re.compile(r"^\s*[-*+]\s+", re.M)
NUMBULLET = re.compile(r"^\s*\d+[.)]\s+", re.M)
EMPHASIS = re.compile(r"(\*\*|__|\*|_|~~)")
SPEAK_BLOCK = re.compile(r"<speak>(.*?)</speak>", re.S | re.I)
# a/b/c.ts, ~/x/y, ./foo/bar — with an optional :line[:col] suffix
PATHLIKE = re.compile(r"(?:~|\.{1,2})?/?(?:[\w.-]+/){1,}[\w.-]+(?::\d+)*")
LINEREF = re.compile(r"\b([\w.-]+\.\w{1,5}):(\d+)(?::\d+)?\b")
SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")
SYMBOLS = [
    (re.compile(r"\s*(?:->|→|=>)\s*"), " then "),
    (re.compile(r"\s*&\s*"), " and "),
    (re.compile(r"(\d)\s*%"), r"\1 percent"),
    (re.compile(r"\s*\|\s*"), ", "),
    (re.compile(r"(?<=\w)/(?=\w)"), " or "),
    (re.compile(r"[#*_`~<>\[\]{}]"), ""),
    (re.compile(r"\s+([,.!?;:])"), r"\1"),
]


def _basename(match: re.Match) -> str:
    """Paths are unspeakable. Keep the leaf, drop the directories and line number."""
    leaf = match.group(0).split(":")[0].rstrip("/").split("/")[-1]
    return leaf.rsplit(".", 1)[0] if "." in leaf else leaf


def _tidy(text: str) -> str:
    text = LINEREF.sub(lambda m: f"{m.group(1).rsplit('.', 1)[0]}, line {m.group(2)}", text)
    text = LINK.sub(r"\1", text)
    text = URL.sub("a link", text)
    text = INLINE_CODE.sub(r"\1", text)
    text = HEADER.sub("", text)
    text = BULLET.sub("", text)
    text = NUMBULLET.sub("", text)
    text = EMPHASIS.sub("", text)
    text = PATHLIKE.sub(_basename, text)
    for pat, rep in SYMBOLS:
        text = pat.sub(rep, text)
    text = apply_lexicon(text)
    return re.sub(r"\s+", " ", text).strip()


def _truncate(text: str, max_chars: int, tail: str = "") -> str:
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    dot = cut.rfind(". ")
    cut = cut[: dot + 1] if dot > max_chars // 3 else cut.rsplit(" ", 1)[0]
    return (cut + tail).strip()


# --------------------------------------------------------------- voice channel

WORD_BUDGET = 28  # a spoken line longer than this stops sounding like speech


def lint(text: str, max_chars: int = 320) -> tuple[str, list[str]]:
    """Clean one spoken line and report what was wrong with it.

    The notes go back to Claude in the tool result — cheap, immediate coaching
    that keeps later lines in the same turn on-voice.
    """
    issues: list[str] = []
    raw = text or ""

    if CODE_FENCE.search(raw):
        issues.append("code block dropped — never speak code")
        raw = CODE_FENCE.sub(" ", raw)
    if TABLE_ROW.search(raw):
        issues.append("table dropped — tables are screen-only")
        raw = TABLE_ROW.sub(" ", raw)
    if "\n" in raw.strip():
        issues.append("multi-line — a spoken line is one or two sentences")
    if PATHLIKE.search(raw) or LINEREF.search(raw):
        issues.append("file path spoken as its bare name — say what it does instead")
    if BULLET.search(raw) or NUMBULLET.search(raw):
        issues.append("list read aloud — say the headline, leave the list on screen")
    if re.search(r"\b(above|below|as shown|see the table|on screen)\b", raw, re.I):
        issues.append("referred to the screen — the listener has no 'above'")

    clean = _tidy(raw)
    words = len(clean.split())
    if words > WORD_BUDGET:
        issues.append(f"{words} words — aim under {WORD_BUDGET}")
    if len(clean) > max_chars:
        clean = _truncate(clean, max_chars)
    return clean, issues


# ------------------------------------------------------------ fallback channel

def speechify(text: str, max_chars: int = 1200, lead_only: bool = False) -> str:
    """Best-effort narration of a reply that was written for the screen."""
    m = SPEAK_BLOCK.search(text or "")
    if m:
        text = m.group(1)
    else:
        text = SPEAK_BLOCK.sub("", text or "")
        text = CODE_FENCE.sub(" ", text)
        text = HEADER_LINE.sub("", text)
        if TABLE_ROW.search(text):
            text = TABLE_ROW.sub("", text)
    clean = _tidy(text)
    if not clean:
        return ""
    if lead_only:
        parts = [s for s in SENT_SPLIT.split(clean) if s.strip()]
        clean = " ".join(parts[:2])
        return _truncate(clean, 220)
    return _truncate(clean, max_chars, tail=" The rest is on screen.")


def sentences(text: str, chunk_chars: int = 220) -> list[str]:
    """Split into playback chunks so speech starts before synthesis finishes."""
    parts = [s.strip() for s in SENT_SPLIT.split(text) if s.strip()]
    out: list[str] = []
    buf = ""
    for s in parts:
        if buf and len(buf) + len(s) + 1 > chunk_chars:
            out.append(buf)
            buf = s
        else:
            buf = f"{buf} {s}".strip()
    if buf:
        out.append(buf)
    return out
