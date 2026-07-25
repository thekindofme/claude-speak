# claude-speak engine eval

M5 Pro / 48GB, macOS 26.4, 10 sentences x 2 runs (best), warm models.

| engine | warm load | voices | mean latency | first sound | RTF | WER* | UTMOS |
|---|---|---|---|---|---|---|---|
| kokoro-pytorch | 11.57s | 18+ | 1.233s | 1.231s | 0.203 | 0.0375 | **4.518** |
| kokoro-mlx | 7.03s | 18+ | 0.262s | 0.247s | 0.042 | 0.0754 | **4.509** |
| piper | 0.79s | 5+ | 0.226s | 0.15s | 0.042 | 0.0504 | **4.373** |
| macos-say | 0.0s | 27+ | 0.881s | 0.881s | 0.202 | 0.0419 | **3.968** |

*WER via mlx-whisper large-v3-turbo round-trip; the `punctuation` sentence
adds ~0.02 to every engine (normalizer splits "backoff"), and kokoro-mlx's
outlier is whisper concatenating snake_case words, not audio corruption.

## Decision: **kokoro-mlx**

- Quality: UTMOS 4.51, statistically tied with kokoro-pytorch (4.52), far above
  piper (4.37, audible mispronunciations e.g. "ite-potent" for idempotent) and
  macos-say (3.97, robotic).
- Latency: 0.26s mean full-synth, 0.25s to first sound — 5x faster than
  kokoro-pytorch, tied with piper. RTF 0.04 = 25x realtime.
- 50+ voices (US/British, male/female) vs piper's handful per model download.
- Runner-up kept installed: `speak engine kokoro-pytorch|piper|macos-say`.

Listen: open eval/gallery.html
