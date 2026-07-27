"""Should this line actually be spoken, right now, in this room?

Kept as pure functions so the rules that decide whether the machine talks are
readable and testable without an audio device.
"""

from __future__ import annotations

from dataclasses import dataclass, field

KINDS = ("open", "progress", "milestone", "question", "done", "alert")

# Lower number = spoken sooner. A question the user is blocking on must not sit
# behind three lines of progress chatter.
PRIORITY = {
    "alert": 0,
    "question": 0,
    "done": 1,
    "milestone": 1,
    "open": 1,
    "progress": 2,
}
URGENT = {"alert", "question"}
REPEAT_WINDOW_S = 90.0


MAX_QUEUE = 4  # deeper than this and speech is lagging reality; stop taking chatter

# How much room to leave between two non-urgent lines. This is what `style`
# actually means — the brief tells the model how much to say, and the floor
# here decides how much gets through. An explicit `min_gap_s` overrides it.
STYLE_GAP_S = {"terse": 15.0, "companion": 6.0, "narrate": 2.5}


def min_gap(eff: dict) -> float:
    explicit = eff.get("min_gap_s")
    if explicit is not None:
        return float(explicit)
    return STYLE_GAP_S.get(eff.get("style", "companion"), 6.0)


@dataclass
class Runtime:
    """Everything the decision depends on that isn't config."""
    now: float
    active_session: str | None = None
    last_utterance_at: float = 0.0
    last_text: str = ""
    last_text_at: float = 0.0
    queued_progress: bool = False
    queue_depth: int = 0
    presence_block: str | None = None   # "screen-locked", "call-app:zoom.us", …
    seen_ids: set = field(default_factory=set)


@dataclass
class Decision:
    accept: bool
    reason: str
    priority: int = 2
    coalesce: bool = False   # replace the queued progress line instead of appending


def decide(kind: str, text: str, session_id: str, eff: dict, rt: Runtime,
           request_id: str = "", source: str = "tool") -> Decision:
    kind = kind if kind in PRIORITY else "progress"
    prio = PRIORITY[kind]

    if request_id and request_id in rt.seen_ids:
        return Decision(False, "duplicate-request", prio)
    if not text or not text.strip():
        return Decision(False, "empty", prio)

    # `speak say ...` from the user's own terminal is a direct instruction, not
    # Claude talking: it bypasses gating entirely.
    if source == "cli":
        return Decision(True, "manual", prio)

    if not eff.get("enabled"):
        return Decision(False, eff.get("source") == "muted" and "muted" or "voice-off", prio)

    # Never talk over a meeting, a locked screen, or a muted Mac — not even for
    # an alert. The screen still has everything.
    if rt.presence_block:
        return Decision(False, rt.presence_block, prio)

    if session_id and rt.active_session and session_id != rt.active_session:
        return Decision(False, "inactive-session", prio)

    if (text.strip() == rt.last_text.strip()
            and rt.now - rt.last_text_at < REPEAT_WINDOW_S):
        return Decision(False, "repeat", prio)

    if kind not in URGENT:
        if rt.queue_depth >= MAX_QUEUE:
            return Decision(False, "queue-full", prio)
        gap = min_gap(eff)
        if kind == "progress" and rt.queued_progress:
            # Progress goes stale in the queue. Freshest wins; the old one dies.
            return Decision(True, "coalesced", prio, coalesce=True)
        if rt.now - rt.last_utterance_at < gap:
            return Decision(False, "rate-limited", prio)

    return Decision(True, "ok", prio)
