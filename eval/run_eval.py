"""claude-speak engine eval.

Per engine x sentence:
  latency_s      full synth wall time (best of RUNS)
  first_chunk_s  time to first audio chunk (best of RUNS) — proxy for time-to-first-sound
  audio_s        duration of generated audio
  rtf            latency_s / audio_s (lower = faster than realtime)
  wer            round-trip word error rate via mlx-whisper large-v3-turbo
  utmos          UTMOS22 predicted MOS (1-5, higher better)

Usage: .venv/bin/python eval/run_eval.py [engine ...]
Writes eval/results.json and per-sample wavs to samples/eval/.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engines"))
sys.path.insert(0, str(ROOT / "eval"))

from adapters import ENGINES  # noqa: E402
from corpus import CORPUS  # noqa: E402

RUNS = 2
WHISPER = "mlx-community/whisper-large-v3-turbo"
OUT = ROOT / "samples" / "eval"
OUT.mkdir(parents=True, exist_ok=True)


def transcribe(wav: str) -> str:
    import mlx_whisper

    return mlx_whisper.transcribe(wav, path_or_hf_repo=WHISPER)["text"]


def wer(ref: str, hyp: str) -> float:
    import jiwer
    from whisper_normalizer.english import EnglishTextNormalizer

    n = EnglishTextNormalizer()
    r, h = n(ref), n(hyp)
    if not r:
        return 0.0
    return jiwer.wer(r, h)


_UTMOS = None


def utmos_score(wav: str) -> float:
    global _UTMOS
    import librosa
    import torch

    if _UTMOS is None:
        _UTMOS = torch.hub.load(
            "tarepan/SpeechMOS:v1.2.0", "utmos22_strong", trust_repo=True
        )
    audio, _ = librosa.load(wav, sr=16000, mono=True)
    with torch.no_grad():
        return float(_UTMOS(torch.from_numpy(audio).unsqueeze(0), 16000).item())


def main():
    only = set(sys.argv[1:])
    results = {}
    for name, cls in ENGINES.items():
        if only and name not in only:
            continue
        print(f"=== {name} ===", flush=True)
        eng = cls()
        t0 = time.perf_counter()
        eng.load()
        warm_load_s = time.perf_counter() - t0
        rows = []
        for sid, text in CORPUS:
            wav = str(OUT / f"{name}__{sid}.wav")
            best = None
            for _ in range(RUNS):
                m = eng.synthesize(text, wav)
                if best is None or m["latency_s"] < best["latency_s"]:
                    best = m
            info = sf.info(wav)
            audio_s = info.frames / info.samplerate
            rows.append({
                "id": sid, "text": text, "wav": wav,
                "latency_s": round(best["latency_s"], 3),
                "first_chunk_s": round(best["first_chunk_s"], 3),
                "audio_s": round(audio_s, 2),
                "rtf": round(best["latency_s"] / max(audio_s, 0.01), 3),
            })
            print(f"  {sid}: {best['latency_s']:.2f}s / {audio_s:.1f}s audio", flush=True)
        results[name] = {
            "warm_load_s": round(warm_load_s, 2),
            "n_voices": len(eng.voices()),
            "rows": rows,
        }
        del eng

    # Quality passes after all synth (whisper/utmos load once)
    print("=== transcribing (WER) ===", flush=True)
    for name, res in results.items():
        for row in res["rows"]:
            hyp = transcribe(row["wav"])
            row["wer"] = round(wer(row["text"], hyp), 3)
            row["transcript"] = hyp.strip()
        print(f"  {name} done", flush=True)

    print("=== scoring (UTMOS) ===", flush=True)
    for name, res in results.items():
        for row in res["rows"]:
            row["utmos"] = round(utmos_score(row["wav"]), 3)
        print(f"  {name} done", flush=True)

    # Aggregates
    for name, res in results.items():
        rows = res["rows"]
        agg = {
            "mean_latency_s": round(sum(r["latency_s"] for r in rows) / len(rows), 3),
            "mean_first_chunk_s": round(sum(r["first_chunk_s"] for r in rows) / len(rows), 3),
            "mean_rtf": round(sum(r["rtf"] for r in rows) / len(rows), 3),
            "mean_wer": round(sum(r["wer"] for r in rows) / len(rows), 4),
            "mean_utmos": round(sum(r["utmos"] for r in rows) / len(rows), 3),
        }
        res["aggregate"] = agg
        print(name, json.dumps(agg))

    out = ROOT / "eval" / "results.json"
    out.write_text(json.dumps(results, indent=2))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
