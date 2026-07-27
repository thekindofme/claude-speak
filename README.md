# claude-speak

Claude Code talks to you. Not a screen reader bolted onto a terminal — a second
output channel the model drives on purpose, while the screen keeps the detail.

```
                    ┌─ screen ── the dense technical reply. paths, diffs, tables.
you ── Claude Code ─┤
                    └─ voice ─── the one line you'd say out loud. spoken, mid-turn.
                                      │
                         speak tool ──┴──► speakd ──► gating · rate limit · priority ──► 🔊
```

The difference is the point. v1 read your replies aloud and it felt like being
read a report. v2 gives the model a voice it chooses when to use: *"starting on
the auth bug, give me a minute"* … *"tests are green"* … *"postgres or sqlite?"*
— while the full answer, with the file paths and the table, lands on screen
unread.

## Off by default, on per session

```sh
speak on           # this session only — other tabs stay silent
speak on --global  # every interactive session
speak off          # or: "stop talking" to Claude, which runs it for you
speak mute 20m     # quiet, then it comes back on its own
```

`speak on` inside a live session takes effect on your very next message: the
hooks brief the model on the voice channel and warm the TTS engine before the
first line is needed. Nothing to restart.

Voice never leaks: only the session you last typed in speaks, headless and
background sessions never do, and typing cuts off speech mid-word.

## What it does that a TTS wrapper doesn't

- **Two channels, different payloads.** The model is briefed to write for the ear
  on one and for the eye on the other. Neither is a summary of the other.
- **Moments, not narration.** `open` / `progress` / `milestone` / `question` /
  `done` / `alert`. Questions jump the queue — you may not be looking at the
  screen, and that's exactly when being told matters.
- **It won't chatter.** Rate-limited between lines; a newer progress line
  replaces a stale one still waiting; the same sentence is never said twice; a
  line that waited out a cold start is dropped rather than said late.
- **It knows when not to talk.** Locked screen, screensaver, muted Mac, a
  meeting app running, or your quiet hours — everything stays silent, alerts
  included. `speak quiet` shows the current verdict.
- **It coaches itself.** The tool answers with what was wrong — "31 words, aim
  under 28", "file path spoken as its bare name" — so later lines in the same
  turn come out cleaner.
- **Permission prompts are spoken.** "I need your go-ahead on something" the
  moment Claude blocks, instead of a silent terminal.
- **Fallback, not silence.** A turn that opens by voice owes a closing line; if one
  never comes, the fallback speaks it (`speak fallback off|lead|full`).
- **Style is pacing, not prose.** `terse` · `companion` · `narrate` set both how the
  model is briefed and how far apart lines are allowed to land (15s / 6s / 2.5s).
  `speak min-gap 4` pins it; `speak min-gap auto` hands it back to the style.
- **Your pronunciations.** `~/.claude-speak/lexicon.json` — ships with the dev
  vocabulary neural TTS fumbles (`idempotent`, `kubectl`, `nginx`, `psql`).
- **A ledger, and a scorecard.** `speak tail` shows everything said *and*
  everything held back, with the reason. `speak report` scores the voice itself:
  median words per line, how many ran over budget, call-to-speech lag, which
  writing habits keep getting flagged.
- **It degrades instead of breaking.** In a session that started before install
  the `speak` tool doesn't exist, so the brief switches to `<speak>` blocks
  rather than promising a tool that isn't there.

## Install

```sh
./install.sh     # CLI, /speak skill, 5 hooks, MCP voice channel. Voice stays OFF.
speak say hi     # hear it
speak doctor     # verify every moving part
./uninstall.sh   # clean removal (--purge also drops ~/.claude-speak)
```

Restart Claude Code once after install so it picks up the MCP tool.

## Engine choice — evals, not vibes

Four engines behind one interface, benchmarked on a 10-sentence corpus (latency,
RTF, whisper round-trip WER, UTMOS naturalness). Numbers in `eval/REPORT.md`,
listenable A/B in `eval/gallery.html`.

**Winner: kokoro-mlx** (Kokoro-82M on Apple MLX) — quality tied with the PyTorch
build at 5x less latency, 25x realtime, 50+ voices. Runners-up stay installed and
switchable: `kokoro-pytorch`, `piper`, `macos-say`.

Reproduce: `.venv/bin/python eval/run_eval.py && .venv/bin/python eval/make_report.py`

## Layout

| path | what |
|---|---|
| `mcp/server.py` | the voice channel: one `speak` tool, stdlib JSON-RPC, instant start |
| `daemon/speakd.py` | warm engine, priority queue, barge-in, ledger — 127.0.0.1:7865 |
| `core/state.py` | global config + per-session overrides; the one answer to "is voice on" |
| `core/policy.py` | when the machine is allowed to talk (pure, tested) |
| `core/presence.py` | when the *room* says don't: lock, mute, meetings, quiet hours |
| `core/voice.py` | lint (voice channel) and speechify (fallback), lexicon |
| `hooks/claude_speak_hook.py` | session-start · prompt · notification · stop · end |
| `voice/brief.md` | the brief that makes the model write for the ear |
| `bin/speak` | control CLI |
| `skill/SKILL.md` | `/speak` skill |
| `eval/`, `tests/` | engine benchmark; pytest suite |

State lives in `~/.claude-speak/`: `config.json`, `sessions/`, `surfaces/`,
`lexicon.json`, `utterances.jsonl`, `speakd.log`, `hook.log`.

## Design notes

- **Session identity.** Claude Code gives child processes no session id, so the
  hooks leave a breadcrumb from terminal surface → session id. That's what lets
  `speak on`, typed inside a session, mean *this* session.
- **Nothing blocks a turn.** Hooks exit 0 always; the client gives up in under a
  second; a cold daemon parks the line and a detached deliverer retries with an
  idempotent `request_id`, so a timed-out response can't double-speak.
- **Barge-in is a generation counter.** Typing bumps it, the queue drains, and
  `afplay` is terminated mid-word.
- **Playback starts before synthesis finishes** — utterances are chunked on
  sentence boundaries.
- **No Focus/DND detection, on purpose.** macOS 26 removed every supported read
  path for Focus state, and guessing permissively is exactly the failure this
  gate exists to prevent. Quiet hours and the meeting-app list cover the same
  ground honestly. See the note at the top of `core/presence.py`.
- **The silent `null` engine** makes the whole daemon testable — real server,
  real queue, real worker, no audio (`tests/test_daemon.py`).
