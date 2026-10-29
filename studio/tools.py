"""Standalone tools component: one-off auditions and file conversion.

Stateless utilities that never touch project data: quick VoiceDesign /
VoiceClone auditions land under the shared temp directory, and format
conversion only transforms uploaded files.  Depends solely on
:class:`ModelManager`, :mod:`studio.audio` and the database id generator.
"""

from __future__ import annotations

import json
import random
from datetime import datetime
from pathlib import Path
from typing import Any

from .audio import normalize_to_wav, safe_filename, wav_to_mp3, write_wav
from .config import TEMP_DIR
from .database import Database, utc_now
from .model_manager import ModelManager


class ToolsService:
    def __init__(self, database: Database, models: ModelManager) -> None:
        self.db = database
        self.models = models
        self._random = random.SystemRandom()

    def convert_files(self, files: list[str], target: str) -> list[str]:
        if not files:
            raise ValueError("请至少选择一个文件")
        conversion_id = self.db.new_id()
        output_dir = TEMP_DIR / "conversions" / conversion_id
        output_dir.mkdir(parents=True, exist_ok=True)
        outputs: list[str] = []
        for source in files:
            source_path = Path(source)
            if target == "MP3 (128kbps)":
                wav_source = source_path
                temporary_wav = None
                if source_path.suffix.lower() != ".wav":
                    temporary_wav = normalize_to_wav(source_path, output_dir / f"{source_path.stem}.normalized.wav")
                    wav_source = temporary_wav
                output = wav_to_mp3(wav_source, output_dir / f"{safe_filename(source_path.stem)}.mp3")
                if temporary_wav:
                    temporary_wav.unlink(missing_ok=True)
            else:
                output = normalize_to_wav(source_path, output_dir / f"{safe_filename(source_path.stem)}.wav")
            outputs.append(str(output))
        return outputs

    def design_sample(
        self, text: str, language: str, prompt: str, design_id: str | None = None
    ) -> str:
        """One-off VoiceDesign audition without creating project data.

        产物固定落在 ``standalone/voicedesign/<design_id>.{wav,mp3}``：调用方
        （``POST /api/tools/design``）回传 ``design_id`` 给前端，前端点「入库」时
        再拿它换回**这一段**音频去固化。用固定 id 命名而非时间戳，是为了让
        「入库」能定位到用户刚试听的那一段 —— 重跑一次会得到另一个 seed 的
        批次，试听的和入库的就不是同一把声音了。

        中间 wav **保留**：固化时要拿它原样做 ``reference.wav`` 并提取 prompt，
        若只剩 mp3 就只能先解码回 wav，等于白白过一次有损压缩。
        """
        text = str(text or "").strip()
        prompt = str(prompt or "").strip()
        language = str(language or "Chinese")
        if not text or not prompt:
            raise ValueError("文本和声线提示词均为必填项")
        seed = self._random.randint(1, 2_147_483_647)
        waveform, sample_rate = self.models.design(text, language, prompt, seed)
        output_dir = TEMP_DIR / "standalone" / "voicedesign"
        output_dir.mkdir(parents=True, exist_ok=True)
        stem = str(design_id or self.db.new_id())
        wav_path = write_wav(output_dir / f"{stem}.wav", waveform, sample_rate)
        mp3_path = wav_to_mp3(wav_path, output_dir / f"{stem}.mp3")
        # 血缘边车：入库时把它写进声线 metadata.json，回答"这把声是哪个 seed 试出来的"。
        # 存成独立 json 而不是塞进 mp3 tag，是因为试听件会被覆盖/删除，边车跟着设计 id 走。
        (output_dir / f"{stem}.json").write_text(
            json.dumps(
                {
                    "design_id": stem,
                    "model": "voice_design",
                    "seed": seed,
                    "language": language,
                    "prompt": prompt,
                    "text": text,
                    "created_at": utc_now(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return str(mp3_path)

    def clone_sample(
        self,
        source_audio: str,
        ref_text: str,
        x_vector_only: bool,
        text: str,
        language: str,
    ) -> str:
        """One-off VoiceClone audition without creating project data."""
        text = str(text or "").strip()
        if not source_audio:
            raise ValueError("请上传参考音频")
        if not text:
            raise ValueError("请填写需要生成的文本")
        ref_text = str(ref_text or "").strip()
        if not x_vector_only and not ref_text:
            raise ValueError("高质量克隆模式必须填写参考音频对应文字")
        reference_wav = normalize_to_wav(
            source_audio,
            TEMP_DIR / "standalone" / f"ref_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.wav",
        )
        try:
            prompt_items = self.models.create_prompt(
                str(reference_wav), ref_text or None, bool(x_vector_only)
            )
            waveform, sample_rate = self.models.clone(text, str(language or "Chinese"), prompt_items=prompt_items)
        finally:
            reference_wav.unlink(missing_ok=True)
        output_dir = TEMP_DIR / "standalone" / "voiceclone"
        output_dir.mkdir(parents=True, exist_ok=True)
        stem = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        wav_path = write_wav(output_dir / f"{stem}.wav", waveform, sample_rate)
        mp3_path = wav_to_mp3(wav_path, output_dir / f"{stem}.mp3")
        wav_path.unlink(missing_ok=True)
        return str(mp3_path)
