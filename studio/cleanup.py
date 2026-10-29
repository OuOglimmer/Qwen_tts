"""一次性清理：回收 temp/ 与 quick/ 里过期不再用的临时音频。

这些目录从来没有回收策略，跑久了会无限长大：

- ``temp/standalone/voiceclone/``  克隆试听件（用完即弃）
- ``temp/standalone/voicedesign/`` 设计试听件（入库后音频已复制进 voices，临时件可删）
- ``temp/conversions/``            格式转换产物（下载后即可删）
- ``temp/uploads/``                上传的原始音频临时副本（入库/转换后即可删）
- ``<项目>/outputs/quick/``        按声线快速试听件（一次性试听）

清理策略按**文件修改时间**收，默认保留 7 天（``QWEN_TTS_TEMP_MAX_AGE_DAYS``
可覆写）。只动上面列死的目录，且不递归越界；正在生成的文件修改时间刚刷新，
自然不会被误删。清理在后台线程跑，不阻塞启动。
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

from .config import PROJECT_DIR, TEMP_DIR

LOGGER = logging.getLogger("qwentts.cleanup")

# 按修改时间收走这些目录里过期文件/子目录。全都在数据目录内，只删「临时/试听」
# 语义的产物，绝不碰 voices/、outputs 正片、manifest 等正式资产。
_TEMP_ROOTS = (
    TEMP_DIR / "standalone" / "voiceclone",
    TEMP_DIR / "standalone" / "voicedesign",
    TEMP_DIR / "conversions",
    TEMP_DIR / "uploads",
)

_QUICK_GLOB = "outputs/quick"


def _default_max_age_days() -> int:
    raw = os.getenv("QWEN_TTS_TEMP_MAX_AGE_DAYS", "7").strip()
    try:
        days = int(raw)
    except ValueError:
        return 7
    return max(1, days)


def _remove_stale(root: Path, max_age_seconds: float, now: float) -> int:
    """递归删除 ``root`` 下修改时间超过阈值的文件，并回收变空的目录。

    按**文件** mtime 判断（目录 mtime 只随增删子项变化、不随文件内容变化，
    只盯目录会漏掉「目录旧、但里面文件刚改过」或反之的两种情况）。删完文件后
    自底向上清空目录，返回删除的文件/目录条目数。
    """
    if not root.is_dir():
        return 0
    removed = 0
    stale_files: list[Path] = []
    for entry in root.rglob("*"):
        if not entry.is_file():
            continue
        try:
            if now - entry.stat().st_mtime > max_age_seconds:
                stale_files.append(entry)
        except OSError:
            continue
    for path in stale_files:
        try:
            path.unlink(missing_ok=True)
            removed += 1
        except OSError:
            continue
    # 自底向上清空目录（叶子在前），只清空的目录、不碰还有内容的。
    for entry in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if not entry.is_dir():
            continue
        try:
            entry.rmdir()
            removed += 1
        except OSError:
            # 目录非空或已被占用则跳过，无害。
            continue
    return removed


def cleanup_stale_temp_files(max_age_days: int | None = None) -> dict[str, int]:
    """回收过期临时文件，返回各根目录删除的条目数。"""
    days = max_age_days if max_age_days is not None else _default_max_age_days()
    max_age_seconds = float(days) * 86400.0
    now = time.time()
    result: dict[str, int] = {}
    for root in _TEMP_ROOTS:
        result[str(root)] = _remove_stale(root, max_age_seconds, now)
    # 每个项目的 quick/ 试听目录（项目可能很多，逐个扫，不递归越界）。
    if PROJECT_DIR.is_dir():
        for project_dir in PROJECT_DIR.iterdir():
            if not project_dir.is_dir():
                continue
            quick_root = project_dir / _QUICK_GLOB
            result[str(quick_root)] = _remove_stale(quick_root, max_age_seconds, now)
    total = sum(result.values())
    if total:
        LOGGER.info("清理了 %d 个过期临时条目（阈值 %d 天）", total, days)
    return result


def start_background_cleanup(max_age_days: int | None = None) -> None:
    """启动时在后台线程跑一次清理；失败只记日志，绝不影响启动。"""
    def _run() -> None:
        try:
            cleanup_stale_temp_files(max_age_days)
        except Exception:  # noqa: BLE001 —— 清理失败不能拖垮应用
            LOGGER.exception("后台临时文件清理失败")

    thread = threading.Thread(target=_run, name="temp-cleanup", daemon=True)
    thread.start()
