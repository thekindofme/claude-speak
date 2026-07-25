"""Generate eval/REPORT.md and eval/gallery.html from eval/results.json."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
r = json.loads((ROOT / "eval/results.json").read_text())

# ---- REPORT.md
lines = [
    "# claude-speak engine eval\n",
    "M5 Pro / 48GB, macOS 26.4, 10 sentences x 2 runs (best), warm models.\n",
    "| engine | warm load | voices | mean latency | first sound | RTF | WER* | UTMOS |",
    "|---|---|---|---|---|---|---|---|",
]
for name, res in r.items():
    a = res["aggregate"]
    lines.append(
        f"| {name} | {res['warm_load_s']}s | {res['n_voices']}+ | "
        f"{a['mean_latency_s']}s | {a['mean_first_chunk_s']}s | {a['mean_rtf']} | "
        f"{a['mean_wer']} | **{a['mean_utmos']}** |"
    )
lines += [
    "",
    "*WER via mlx-whisper large-v3-turbo round-trip; the `punctuation` sentence",
    "adds ~0.02 to every engine (normalizer splits \"backoff\"), and kokoro-mlx's",
    "outlier is whisper concatenating snake_case words, not audio corruption.",
    "",
    "## Decision: **kokoro-mlx**",
    "",
    "- Quality: UTMOS 4.51, statistically tied with kokoro-pytorch (4.52), far above",
    "  piper (4.37, audible mispronunciations e.g. \"ite-potent\" for idempotent) and",
    "  macos-say (3.97, robotic).",
    "- Latency: 0.26s mean full-synth, 0.25s to first sound — 5x faster than",
    "  kokoro-pytorch, tied with piper. RTF 0.04 = 25x realtime.",
    "- 50+ voices (US/British, male/female) vs piper's handful per model download.",
    "- Runner-up kept installed: `speak engine kokoro-pytorch|piper|macos-say`.",
    "",
    "Listen: open eval/gallery.html",
]
(ROOT / "eval/REPORT.md").write_text("\n".join(lines) + "\n")

# ---- gallery.html
rows = []
sids = [row["id"] for row in next(iter(r.values()))["rows"]]
engines = list(r)
head = "".join(f"<th>{e}</th>" for e in engines)
for sid in sids:
    cells = []
    for e in engines:
        row = next(x for x in r[e]["rows"] if x["id"] == sid)
        rel = f"../samples/eval/{Path(row['wav']).name}"
        cells.append(
            f"<td><audio controls preload='none' src='{rel}'></audio>"
            f"<div class='m'>lat {row['latency_s']}s · wer {row['wer']} · mos {row['utmos']}</div></td>"
        )
    text = next(x for x in r[engines[0]]["rows"] if x["id"] == sid)["text"]
    rows.append(f"<tr><td class='s'><b>{sid}</b><div class='m'>{text}</div></td>{''.join(cells)}</tr>")

html = f"""<!doctype html><meta charset="utf-8"><title>claude-speak eval gallery</title>
<style>
body{{font:14px -apple-system,sans-serif;margin:2rem;background:#111;color:#eee}}
table{{border-collapse:collapse;width:100%}}
td,th{{border:1px solid #333;padding:8px;vertical-align:top;text-align:left}}
audio{{width:230px}} .m{{color:#999;font-size:11px;margin-top:4px}} .s{{max-width:260px}}
</style>
<h1>claude-speak eval gallery</h1>
<table><tr><th>sentence</th>{head}</tr>{''.join(rows)}</table>"""
(ROOT / "eval/gallery.html").write_text(html)
print("wrote eval/REPORT.md and eval/gallery.html")
