# claude-speak

Claude Code speaks its final replies out loud. Local, fast, high quality.

```
you type/dictate ──> Claude Code ──> Stop hook ──> speakd daemon ──> 🔊
                                      (extracts       (kokoro-mlx warm,
                                       final reply)     sentence-streamed)
```

## What you get

- **Spoken replies**: every final reply in your interactive session is read
  aloud (~0.25s to first sound after the reply lands).
- **Interrupts**: typing a new prompt cuts speech off instantly. `speak stop` too.
- **Session-aware**: only the session you last typed in speaks. Headless /
  background / automation sessions never talk.
- **Voice picker**: 50+ Kokoro voices, live-switchable. `speak voice am_michael`.
- **Modes**: `full` (whole reply, speechified), `summary` (Claude writes a
  spoken TL;DR in `<speak>` tags), `auto` (summary if present, else full).

## Install / use

```sh
./install.sh        # symlinks CLI + skill, registers hooks, writes default config
speak on            # enable + start daemon
speak say hello     # test
speak off           # disable
```

In Claude Code: the `/speak` skill knows every command ("switch to a British
male voice", "talk slower", "summary mode").

## Engine choice — evals, not vibes

Four engines were adapted behind one interface and benchmarked on a 10-sentence
corpus (latency, RTF, whisper round-trip WER, UTMOS naturalness). Full numbers:
`eval/REPORT.md`, listenable A/B: `eval/gallery.html`.

**Winner: kokoro-mlx** (Kokoro-82M on Apple MLX) — quality tied with the
PyTorch build at 5x less latency, 25x realtime, 50+ voices. Runners-up stay
installed and switchable: `kokoro-pytorch`, `piper`, `macos-say`.

Reproduce: `.venv/bin/python eval/run_eval.py && .venv/bin/python eval/make_report.py`

## Layout

| path | what |
|---|---|
| `daemon/speakd.py` | warm-engine HTTP daemon (127.0.0.1:7865), queue, interrupt, speechify |
| `engines/adapters.py` | uniform adapters for the four engines |
| `hooks/claude_speak_hook.py` | Stop hook (speak final reply) + UserPromptSubmit (interrupt, mark active session, inject summary-mode instructions) |
| `bin/speak` | control CLI |
| `skill/SKILL.md` | `/speak` skill for Claude Code |
| `eval/` | corpus, runner, results, report, gallery |
| `tests/` | pytest suite (speechify, transcript extraction, gating) |

Config `~/.claude-speak/config.json` · logs `~/.claude-speak/speakd.log`,
`hook.log` · uninstall: remove the two hook entries from
`~/.claude/settings.json`, `rm ~/.local/bin/speak ~/.claude/skills/speak`.

## Design notes

- **speechify**: code fences → "code block omitted", tables → one mention,
  paths → basename, markdown stripped, >2200 chars → truncated at sentence
  boundary + "the rest is on screen".
- **Robustness**: hook always exits 0; daemon down → hook parks payload, spawns
  daemon, detached deliverer retries with an idempotent `request_id` (no double
  speech); single-instance port guard; `afplay` per sentence chunk so first
  sound doesn't wait for full synth.
