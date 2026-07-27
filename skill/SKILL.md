---
name: speak
description: Turn Claude Code's voice on or off and control how it sounds — claude-speak. Use when the user says "voice on/off", "speak to me", "talk to me", "stop talking", "be quiet", "mute yourself", "read this out", "change your voice", "speak faster/slower", or asks whether voice is on. Also the reference for HOW to use the voice channel while it is on.
---

# speak — Claude Code's voice

Voice is **off until asked for**, and it is **per session**: turning it on in this
session does not make every other Claude Code tab start talking.

## Controlling it

Run these with Bash; report the result in one line.

| The user says | Run |
|---|---|
| "voice on", "talk to me" | `speak on` |
| "voice on everywhere / by default" | `speak on --global` |
| "voice off", "stop talking" | `speak off` |
| "quiet for a bit", "mute for 20 minutes" | `speak mute 20m` → `speak unmute` |
| "stop" mid-sentence | `speak stop` |
| "is voice on?" | `speak` |
| "use a male / British voice" | `speak voice am_michael` · `speak voices` |
| "let me hear that voice first" | `speak preview am_michael` |
| "don't talk during meetings / at night" | `speak quiet apps zoom.us` · `speak quiet hours 22:00-08:00` |
| "how's the voice doing?" | `speak report` |
| "slower", "faster" | `speak speed 0.9` … `speak speed 1.3` |
| "quieter" | `speak volume 0.6` |
| "talk less" / "talk me through it" | `speak style terse` / `speak style narrate` — also sets the spacing between lines (15s / 6s / 2.5s) |
| "say something when you're done even if you forget" | `speak fallback lead` (default) |
| "say this out loud" | `speak say <text>` |
| something is wrong | `speak doctor`, `speak tail`, `speak log`, `speak restart` |

Voices: `af_heart` (warm US female, default), `af_bella`, `am_michael` (calm US
male), `am_fenrir`, `bf_emma`, `bm_george` (British).

After `speak on`, say your first line out loud with the `speak` tool — that
confirmation *is* the demo.

## Using the voice channel

While voice is on you have two channels, and they carry different things.

- **Screen** — your normal reply. Unchanged: dense, technical, complete. Paths,
  diffs, tables, `file:line`. Not read aloud.
- **Voice** — the `speak` tool. What you'd say out loud to someone at the next
  desk while the detail lands on their screen.

Never say your reply. Never write a "spoken summary" into the reply either — the
`speak` tool is the only way to talk.

**How to say it:** one or two sentences, under 25 words, first person, present
tense, contractions. No markdown, no code, no paths, no IDs. Name things by what
they do — "the auth middleware", not a file path. Never say "above" or "on
screen"; a listener has no *above*. Lead with the answer.

**When to say it** — `kind` picks the moment:

- `open` — long work is starting; what you're about to do.
- `progress` — mid-flight update. Rate-limited; a newer one replaces an older one.
- `milestone` — something notable landed, or the problem changed shape.
- `question` — you need a decision. Say it immediately; it jumps the queue. This
  is the highest-value moment for voice: they may not be looking at the screen.
- `done` — the closing line: outcome and what's next. Only this kind closes the turn: open by voice and you owe a close, or the fallback speaks one for you.
- `alert` — something is wrong and they should know now.

**How much:** one to three calls in a typical turn — `speak style narrate` buys room for a real walkthrough, `terse` spaces lines far apart. A quick factual answer is one
(the close). A trivial one is none. Silence beats chatter.

The tool tells you when a line was suppressed and why (`rate-limited`, `repeat`,
`voice-off`, `screen-locked`, `call-app:zoom.us`) and coaches on the line itself
("31 words — aim under 28"). Take the note; don't retry the same line. A
presence reason means the room said no — stop calling `speak` for that turn.

If the `speak` tool isn't available in this session, voice mode falls back to a
`<speak>…</speak>` block at the end of the reply; the injected context says which
mode you're in. Don't use both.

## How it fits together

```
SessionStart ─ voice on? warm the engine, brief the model
UserPromptSubmit ─ barge-in (you started typing), mark this the live session
speak tool ─────────────► speakd ──► gating, rate limit, priority ──► speakers
Notification ─ "I need your go-ahead" spoken the moment permission is asked
Stop ─ only if the model never spoke: one fallback line
```

Daemon `~/code/claude-speak/daemon/speakd.py` on 127.0.0.1:7865. State in
`~/.claude-speak/` — `config.json`, `sessions/`, `lexicon.json` (your own
pronunciations), `utterances.jsonl` (everything said and suppressed).
