---
name: speak
description: Control claude-speak — spoken voice replies from Claude Code via the local speakd TTS daemon. Use when the user says "speak", "voice on/off", "talk to me", "read your replies aloud", "stop talking", "change voice", "speak faster/slower", or wants voice-reply settings changed. Also defines how to write replies when voice summary mode is active.
---

# speak — voice replies for Claude Code

Everything is driven by the `speak` CLI at `~/code/claude-speak/bin/speak`
(also on PATH as `speak`). Run commands with Bash; report the result briefly.

| Intent | Command |
|---|---|
| turn voice replies on / off | `speak on` / `speak off` |
| stop current speech | `speak stop` |
| status (engine, voice, queue) | `speak status` |
| list voices | `speak voices` |
| change voice | `speak voice af_heart` (a*=US, b*=British; f/m in 2nd letter) |
| speaking rate | `speak speed 1.1` (0.8–1.3) |
| what gets spoken | `speak mode full` (whole reply) / `speak mode summary` (you write a `<speak>` TL;DR) / `speak mode auto` (default) |
| switch TTS engine | `speak engine kokoro-mlx|kokoro-pytorch|piper|macos-say` |
| test | `speak say hello there` |
| troubleshoot | `speak doctor`, `speak log`, `speak restart` |

Good voices: `af_heart` (warm US female, default), `af_bella`, `am_michael`
(calm US male), `am_fenrir`, `bf_emma`, `bm_george` (British).

## Writing for the ear

When voice replies are ON (check: hook injects `<voice-mode>` context, or user
said so), your final reply is read aloud by the daemon after speechification
(code blocks, tables, paths stripped). Help it:

- In **summary mode**, end the final reply with `<speak>one to three short
  plain-English sentences</speak>` — no markdown, no code, no paths, no IDs.
  Written for listening, not reading. The rest of the reply stays on screen.
- In **full/auto mode**, prefer prose over dense tables when the reply is short.

## Architecture (for debugging)

- Daemon: `~/code/claude-speak/daemon/speakd.py`, HTTP on 127.0.0.1:7865,
  config `~/.claude-speak/config.json`, log `~/.claude-speak/speakd.log`.
- Stop hook speaks the last assistant message; UserPromptSubmit hook interrupts
  playback and injects summary-mode instructions.
- Engine choice was decided by evals in `~/code/claude-speak/eval/results.json`.
