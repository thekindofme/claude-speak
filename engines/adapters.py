"""Uniform TTS engine adapters for claude-speak.

Each adapter exposes:
    load() -> None            (warm the model; timed separately)
    synthesize(text, out_wav) -> dict with keys:
        latency_s   : wall time from call to full wav written
        first_chunk_s : time to first audio chunk if the engine streams, else == latency_s
    voices() -> list[str]
    name, default_voice
"""

from __future__ import annotations

import subprocess
import time
import wave
from pathlib import Path

import numpy as np
import soundfile as sf

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


class KokoroPyTorch:
    name = "kokoro-pytorch"
    default_voice = "af_heart"

    def __init__(self, voice: str | None = None):
        self.voice = voice or self.default_voice
        self.pipeline = None

    def load(self):
        from kokoro import KPipeline

        self.pipeline = KPipeline(lang_code=self.voice[0])
        # prime graph
        for _ in self.pipeline("Warm up.", voice=self.voice):
            pass

    def synthesize(self, text: str, out_wav: str, speed: float = 1.0) -> dict:
        t0 = time.perf_counter()
        first = None
        chunks = []
        for result in self.pipeline(text, voice=self.voice, speed=speed):
            audio = result.audio
            if audio is None:
                continue
            if first is None:
                first = time.perf_counter() - t0
            chunks.append(audio.detach().cpu().numpy())
        full = np.concatenate(chunks) if chunks else np.zeros(1, dtype=np.float32)
        sf.write(out_wav, full, 24000)
        total = time.perf_counter() - t0
        return {"latency_s": total, "first_chunk_s": first or total}

    def voices(self):
        # Kokoro ships 54 voices; canonical list kept short here.
        return [
            "af_heart", "af_bella", "af_nicole", "af_aoede", "af_kore", "af_sarah",
            "am_michael", "am_fenrir", "am_puck", "am_echo", "am_eric", "am_adam",
            "bf_emma", "bf_isabella", "bf_alice", "bm_george", "bm_fable", "bm_lewis",
        ]


class KokoroMLX:
    name = "kokoro-mlx"
    default_voice = "af_heart"
    model_id = "prince-canuma/Kokoro-82M"

    def __init__(self, voice: str | None = None):
        self.voice = voice or self.default_voice
        self.model = None

    def load(self):
        from mlx_audio.tts.utils import load_model

        self.model = load_model(self.model_id)
        for _ in self.model.generate(text="Warm up.", voice=self.voice):
            pass

    def synthesize(self, text: str, out_wav: str, speed: float = 1.0) -> dict:
        t0 = time.perf_counter()
        first = None
        chunks = []
        for seg in self.model.generate(text=text, voice=self.voice, speed=speed):
            if first is None:
                first = time.perf_counter() - t0
            chunks.append(np.asarray(seg.audio))
        full = np.concatenate(chunks) if chunks else np.zeros(1, dtype=np.float32)
        sf.write(out_wav, full, 24000)
        total = time.perf_counter() - t0
        return {"latency_s": total, "first_chunk_s": first or total}

    def voices(self):
        return KokoroPyTorch.voices(self)


class Piper:
    name = "piper"
    default_voice = "en_US-lessac-medium"

    def __init__(self, voice: str | None = None):
        self.voice_name = voice or self.default_voice
        self.voice = None

    def load(self):
        from piper import PiperVoice
        from piper.download_voices import download_voice

        vdir = Path.home() / ".local/share/piper-voices"
        vdir.mkdir(parents=True, exist_ok=True)
        onnx = vdir / f"{self.voice_name}.onnx"
        if not onnx.exists():
            download_voice(self.voice_name, vdir)
        self.voice = PiperVoice.load(str(onnx))
        # warm
        list(self.voice.synthesize("Warm up."))

    def synthesize(self, text: str, out_wav: str, speed: float = 1.0) -> dict:
        from piper import SynthesisConfig

        t0 = time.perf_counter()
        first = None
        cfg = SynthesisConfig(length_scale=1.0 / max(speed, 0.1))
        with wave.open(out_wav, "wb") as w:
            configured = False
            for chunk in self.voice.synthesize(text, cfg):
                if first is None:
                    first = time.perf_counter() - t0
                if not configured:
                    w.setnchannels(chunk.sample_channels)
                    w.setsampwidth(chunk.sample_width)
                    w.setframerate(chunk.sample_rate)
                    configured = True
                w.writeframes(chunk.audio_int16_bytes)
        total = time.perf_counter() - t0
        return {"latency_s": total, "first_chunk_s": first or total}

    def voices(self):
        return ["en_US-lessac-medium", "en_US-amy-medium", "en_US-ryan-high",
                "en_GB-alba-medium", "en_US-libritts_r-medium"]


class MacSay:
    name = "macos-say"
    default_voice = "Samantha"

    def __init__(self, voice: str | None = None):
        self.voice = voice or self.default_voice

    def load(self):
        pass  # OS service, nothing to warm

    def synthesize(self, text: str, out_wav: str, speed: float = 1.0) -> dict:
        t0 = time.perf_counter()
        aiff = out_wav.replace(".wav", ".aiff")
        rate = str(int(175 * speed))
        subprocess.run(["say", "-v", self.voice, "-r", rate, "-o", aiff, text], check=True)
        subprocess.run(
            ["afconvert", aiff, out_wav, "-d", "LEI16", "-f", "WAVE"], check=True
        )
        Path(aiff).unlink(missing_ok=True)
        total = time.perf_counter() - t0
        return {"latency_s": total, "first_chunk_s": total}

    def voices(self):
        out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True).stdout
        return [l.split()[0] for l in out.splitlines() if " en_" in l or " en-" in l]


class Null:
    """Silent engine: real timing shape, no audio. For tests and dry runs."""

    name = "null"
    default_voice = "silent"

    def __init__(self, voice: str | None = None):
        self.voice = voice or self.default_voice

    def load(self):
        pass

    def synthesize(self, text: str, out_wav: str, speed: float = 1.0) -> dict:
        t0 = time.perf_counter()
        with wave.open(out_wav, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(24000)
            w.writeframes(b"\x00\x00" * 240)  # 10ms of silence
        total = time.perf_counter() - t0
        return {"latency_s": total, "first_chunk_s": total}

    def voices(self):
        return ["silent"]


ENGINES = {
    "kokoro-pytorch": KokoroPyTorch,
    "kokoro-mlx": KokoroMLX,
    "piper": Piper,
    "macos-say": MacSay,
    "null": Null,
}
