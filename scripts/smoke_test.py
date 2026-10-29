"""Sequential GPU smoke test for both local Qwen3-TTS models."""

from __future__ import annotations

import gc
import time
from pathlib import Path

import soundfile as sf
import torch
from qwen_tts import Qwen3TTSModel
from studio.config import MODEL_PATHS


ROOT = Path(__file__).resolve().parents[1]
DESIGN_MODEL = MODEL_PATHS["voice_design"]
CLONE_MODEL = MODEL_PATHS["base"]
DESIGN_OUTPUT = ROOT / "outputs" / "voicedesign" / "smoke_design.wav"
CLONE_OUTPUT = ROOT / "outputs" / "voiceclone" / "smoke_clone.wav"
REFERENCE_TEXT = "你好，这是声线创建模型的部署测试。"


def load_model(path: Path) -> Qwen3TTSModel:
    started = time.perf_counter()
    model = Qwen3TTSModel.from_pretrained(
        str(path),
        device_map="cuda:0",
        dtype=torch.bfloat16,
        attn_implementation=None,
    )
    print(f"Loaded {path.name} in {time.perf_counter() - started:.1f}s")
    print(f"GPU allocated: {torch.cuda.memory_allocated() / 2**30:.2f} GiB")
    return model


def clear_gpu_cache() -> None:
    gc.collect()
    torch.cuda.empty_cache()
    print(f"GPU allocated after unload: {torch.cuda.memory_allocated() / 2**30:.2f} GiB")


def main() -> None:
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable")
    DESIGN_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    CLONE_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    torch.cuda.reset_peak_memory_stats()

    design = load_model(DESIGN_MODEL)
    wavs, sample_rate = design.generate_voice_design(
        text=REFERENCE_TEXT,
        language="Chinese",
        instruct="清晰、自然、温和的成年女性普通话声音，语速适中。",
        max_new_tokens=128,
    )
    sf.write(DESIGN_OUTPUT, wavs[0], sample_rate)
    print(f"Voice design OK: {DESIGN_OUTPUT} ({sample_rate} Hz)")
    print(f"Design peak GPU: {torch.cuda.max_memory_allocated() / 2**30:.2f} GiB")
    del design
    clear_gpu_cache()

    torch.cuda.reset_peak_memory_stats()
    clone = load_model(CLONE_MODEL)
    wavs, sample_rate = clone.generate_voice_clone(
        text="你好，这是声线克隆模型的部署测试。",
        language="Chinese",
        ref_audio=str(DESIGN_OUTPUT),
        ref_text=REFERENCE_TEXT,
        max_new_tokens=128,
    )
    sf.write(CLONE_OUTPUT, wavs[0], sample_rate)
    print(f"Voice clone OK: {CLONE_OUTPUT} ({sample_rate} Hz)")
    print(f"Clone peak GPU: {torch.cuda.max_memory_allocated() / 2**30:.2f} GiB")
    del clone
    clear_gpu_cache()


if __name__ == "__main__":
    main()
