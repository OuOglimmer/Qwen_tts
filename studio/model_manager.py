from __future__ import annotations

import gc
import threading
from pathlib import Path
from typing import Any

import torch
from qwen_tts import Qwen3TTSModel
from qwen_tts.inference.qwen3_tts_model import VoiceClonePromptItem
from safetensors.torch import load_file, save_file

from .config import MODEL_PATHS


class ModelManager:
    """Own exactly one GPU model and serialize every inference call."""

    @staticmethod
    def _audio_is_usable(waveform: Any) -> bool:
        """Reject empty/near-silent decoder results before saving them."""
        try:
            audio = torch.as_tensor(waveform, dtype=torch.float32)
            if audio.numel() == 0 or not torch.isfinite(audio).all():
                return False
            peak = float(audio.abs().max())
            rms = float(audio.square().mean().sqrt())
            return peak >= 1e-3 and rms >= 1e-3
        except (TypeError, ValueError, RuntimeError):
            return False

    @staticmethod
    def _clone_max_new_tokens(text: str) -> int:
        """Choose a bounded codec budget for one utterance.

        Qwen3-TTS emits 12 codec tokens per second.  A fixed budget either
        truncates long lines (the former 1024-token/81.8-second cap) or lets
        short lines run for several minutes when the model does not emit EOS.
        This text-length estimate leaves headroom for pauses while keeping a
        runaway request bounded.
        """
        units = len("".join(str(text or "").split()))
        estimated = 96 + units * 6
        return max(128, min(2048, estimated))

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._model: Qwen3TTSModel | None = None
        self._kind: str | None = None

    @property
    def kind(self) -> str | None:
        return self._kind

    def load(self, kind: str) -> Qwen3TTSModel:
        if kind not in MODEL_PATHS:
            raise ValueError(f"Unknown model kind: {kind}")
        with self._lock:
            if self._model is not None and self._kind == kind:
                return self._model
            self.unload()
            path = MODEL_PATHS[kind]
            if not (path / "model.safetensors").exists():
                raise FileNotFoundError(f"Model is incomplete: {path}")
            self._model = Qwen3TTSModel.from_pretrained(
                str(path),
                device_map="cuda:0",
                dtype=torch.bfloat16,
                attn_implementation=None,
            )
            self._kind = kind
            return self._model

    def unload(self) -> None:
        with self._lock:
            if self._model is not None:
                model = self._model
                self._model = None
                self._kind = None
                del model
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

    def design(self, text: str, language: str, prompt: str, seed: int) -> tuple[Any, int]:
        with self._lock:
            model = self.load("voice_design")
            torch.manual_seed(seed)
            torch.cuda.manual_seed_all(seed)
            wavs, sample_rate = model.generate_voice_design(
                text=text,
                language=language,
                instruct=prompt,
                max_new_tokens=512,
            )
            return wavs[0], sample_rate

    def clone(
        self,
        text: str,
        language: str,
        *,
        ref_audio: str | None = None,
        ref_text: str | None = None,
        x_vector_only: bool = False,
        prompt_items: list[VoiceClonePromptItem] | None = None,
    ) -> tuple[Any, int]:
        with self._lock:
            model = self.load("base")
            # Qwen3-TTS uses a 12 Hz codec. 1024 tokens hard-caps every
            # sufficiently long utterance at roughly 81.8 seconds, which
            # made unrelated batch lines all end at the same duration.
            # Keep the model's documented default ceiling so generation can
            # finish naturally instead of being truncated by this wrapper.
            kwargs: dict[str, Any] = {
                "text": text,
                "language": language,
                "max_new_tokens": self._clone_max_new_tokens(text),
                # The Base model's streaming-style prompt layout can cause
                # severe speaking-rate drift for regular batch text. Batch
                # synthesis is a complete utterance, so use the stable
                # non-streaming layout explicitly.
                "non_streaming_mode": True,
            }
            if prompt_items is not None:
                kwargs["voice_clone_prompt"] = prompt_items
            else:
                kwargs.update(
                    ref_audio=ref_audio,
                    ref_text=ref_text,
                    x_vector_only_mode=x_vector_only,
                )
            last_waveform: Any = None
            sample_rate: int | None = None
            for _ in range(3):
                wavs, sample_rate = model.generate_voice_clone(**kwargs)
                last_waveform = wavs[0]
                if self._audio_is_usable(last_waveform):
                    return last_waveform, sample_rate
            raise RuntimeError("模型连续生成了近静音音频，请重试")

    def create_prompt(
        self,
        ref_audio: str,
        ref_text: str | None,
        x_vector_only: bool,
    ) -> list[VoiceClonePromptItem]:
        with self._lock:
            model = self.load("base")
            return model.create_voice_clone_prompt(
                ref_audio=ref_audio,
                ref_text=ref_text,
                x_vector_only_mode=x_vector_only,
            )

    @staticmethod
    def save_prompt(items: list[VoiceClonePromptItem], destination: Path) -> Path:
        if len(items) != 1:
            raise ValueError("Only one voice prompt can be archived at a time")
        item = items[0]
        tensors = {"ref_spk_embedding": item.ref_spk_embedding.detach().cpu().contiguous()}
        if item.ref_code is not None:
            tensors["ref_code"] = item.ref_code.detach().cpu().contiguous()
        metadata = {
            "ref_text": item.ref_text or "",
            "x_vector_only_mode": str(item.x_vector_only_mode).lower(),
            "icl_mode": str(item.icl_mode).lower(),
        }
        destination.parent.mkdir(parents=True, exist_ok=True)
        save_file(tensors, str(destination), metadata=metadata)
        return destination

    @staticmethod
    def load_prompt(source: Path, ref_text: str | None, x_vector_only: bool) -> list[VoiceClonePromptItem]:
        tensors = load_file(str(source), device="cpu")
        return [VoiceClonePromptItem(
            ref_code=tensors.get("ref_code"),
            ref_spk_embedding=tensors["ref_spk_embedding"],
            x_vector_only_mode=x_vector_only,
            icl_mode=not x_vector_only,
            ref_text=ref_text,
        )]

    def status(self) -> dict[str, Any]:
        allocated = torch.cuda.memory_allocated() / 2**30 if torch.cuda.is_available() else 0.0
        return {
            "model": self._kind or "未加载",
            "cuda": torch.cuda.is_available(),
            "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "不可用",
            "allocated_gib": round(allocated, 2),
        }
