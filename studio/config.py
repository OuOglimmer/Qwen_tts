from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_local_env() -> None:
    """Load simple KEY=VALUE overrides without requiring another dependency."""
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


_load_local_env()


def _resolve_data_dir() -> Path:
    """Resolve storage without hiding an existing repository-local database."""
    configured = os.getenv("QWEN_TTS_DATA_DIR", "").strip()
    if configured:
        path = Path(os.path.expandvars(configured)).expanduser()
        return path if path.is_absolute() else (ROOT / path)

    legacy = ROOT / "data"
    if (legacy / "app.db").exists():
        return legacy

    local_app_data = os.getenv("LOCALAPPDATA", "").strip()
    if local_app_data:
        return Path(local_app_data) / "QwenTTSVoiceoverStudio"
    return legacy


DATA_DIR = _resolve_data_dir()
VOICE_DIR = DATA_DIR / "voices"
PROJECT_DIR = DATA_DIR / "projects"
TEMP_DIR = DATA_DIR / "temp"
DB_PATH = DATA_DIR / "app.db"
GPU_LOCK_PATH = DATA_DIR / "gpu.lock"

MODEL_ROOT = Path(os.getenv("QWEN_TTS_MODEL_ROOT", str(ROOT / "models"))).expanduser()
MODEL_PATHS = {
    "voice_design": Path(
        os.getenv("QWEN_TTS_GENERATION_MODEL_PATH", str(MODEL_ROOT / "voice_design_1.7b"))
    ).expanduser(),
    "base": Path(
        os.getenv("QWEN_TTS_CLONE_MODEL_PATH", str(MODEL_ROOT / "voice_clone_1.7b"))
    ).expanduser(),
}

LANGUAGES = [
    "Chinese",
    "English",
    "Japanese",
    "Korean",
    "German",
    "French",
    "Russian",
    "Portuguese",
    "Spanish",
    "Italian",
    "Auto",
]

for directory in (DATA_DIR, VOICE_DIR, PROJECT_DIR, TEMP_DIR):
    directory.mkdir(parents=True, exist_ok=True)
