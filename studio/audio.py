from __future__ import annotations

import math
import re
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import imageio_ffmpeg
import soundfile as sf


WINDOWS_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# CON/PRN/NUL/COM1 这些名字在 Windows 上永远打不开（带扩展名也一样）。
WINDOWS_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def ffmpeg_executable() -> str:
    return imageio_ffmpeg.get_ffmpeg_exe()


def safe_filename(value: str, fallback: str = "audio") -> str:
    cleaned = WINDOWS_INVALID.sub("_", value).strip(" ._")
    cleaned = (cleaned or fallback)[:80]
    # 保留名要按"主名"判断（CON.txt 同样非法），加下划线后即可正常落盘。
    if cleaned.split(".")[0].strip().lower() in WINDOWS_RESERVED:
        cleaned = f"_{cleaned}"
    return cleaned


def run_ffmpeg(arguments: list[str]) -> None:
    flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    completed = subprocess.run(
        [ffmpeg_executable(), "-hide_banner", "-loglevel", "error", "-y", *arguments],
        capture_output=True,
        text=True,
        creationflags=flags,
    )
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip() or "FFmpeg conversion failed")


def normalize_to_wav(source: str | Path, destination: str | Path) -> Path:
    source_path = Path(source)
    destination_path = Path(destination)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg([
        "-i", str(source_path),
        "-vn", "-ac", "1", "-ar", "24000",
        "-c:a", "pcm_s16le", str(destination_path),
    ])
    return destination_path


def wav_to_mp3(source: str | Path, destination: str | Path, bitrate: str = "128k") -> Path:
    destination_path = Path(destination)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg([
        "-i", str(source),
        "-vn", "-ac", "1", "-ar", "24000",
        "-c:a", "libmp3lame", "-b:a", bitrate,
        str(destination_path),
    ])
    return destination_path


def write_wav(destination: str | Path, waveform, sample_rate: int) -> Path:
    destination_path = Path(destination)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(destination_path, waveform, sample_rate, subtype="PCM_16")
    return destination_path


def copy_original(source: str | Path, destination_dir: str | Path) -> Path:
    source_path = Path(source)
    target = Path(destination_dir) / f"original{source_path.suffix.lower()}"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, target)
    return target


def audio_summary(path: str | Path) -> str:
    info = sf.info(path)
    return f"{info.duration:.2f}s · {info.samplerate}Hz · {info.channels}ch"


def wav_duration_ms(path: str | Path) -> int | None:
    """音频时长（毫秒）。文件缺失/不可读时返回 None —— 血缘字段宁缺勿造。"""
    try:
        return int(round(sf.info(str(path)).duration * 1000))
    except (OSError, RuntimeError, ValueError):
        return None


# ---------------------------------------------------------------- 语速 / 时间伸缩
#
# 语速不是模型参数：Qwen3-TTS 只有 voice_design 与 base clone 两条推理入口，
# 参数表里都没有 speed，clone 的韵律还直接继承参考音频。所以"台词快一点"
# 只能做在成品导出这一层：ffmpeg atempo 是 WSOLA 时间伸缩，压时长不改基频
# （实测 1.0~1.4 倍基频中位数一位不动），代价是句间停顿一起被压缩。
#
# 区间取 [0.5, 2.0]：atempo 单实例的合法闭区间就是这个范围。界面只露到 1.5，
# 再快短句会有吞字的金属感；上限放宽到 2.0 是留给脚本/接口调用方的余地。

SPEED_MIN = 0.5
SPEED_MAX = 2.0
# 流式吐音频的块大小：64KB 正好对齐 mp3 帧边界之外的任意切点均可，播放器能接。
_STREAM_CHUNK = 65536


def check_speed(speed: float) -> float:
    """校验并归一化语速。

    越界不静默夹紧：用户写了 2 倍却悄悄按 1.5 跑，导出的音频和预期不符却没有任何
    提示，比直接报错难查得多。
    """
    try:
        value = float(speed)
    except (TypeError, ValueError):
        raise ValueError("语速必须是数字") from None
    if not math.isfinite(value):
        raise ValueError("语速必须是数字")
    if not SPEED_MIN <= value <= SPEED_MAX:
        raise ValueError(f"语速需要在 {SPEED_MIN}~{SPEED_MAX} 倍之间")
    return value


def tempo_filter(speed: float) -> str:
    """atempo 滤镜表达式。

    单实例合法区间是 [0.5, 2.0]；超出范围按倍数串联（本功能到不了，留作兜底，
    免得以后放宽上限时这里悄悄变成"无效参数"）。
    """
    remaining = check_speed(speed)
    parts: list[str] = []
    while remaining > 2.0:
        parts.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5:
        parts.append("atempo=0.5")
        remaining /= 0.5
    parts.append(f"atempo={remaining:.6f}")
    return ",".join(parts)


def tempo_stream(source: str | Path, speed: float) -> Iterator[bytes]:
    """把变速后的音频边转边吐，不落任何临时文件。

    浏览器导出（export-plan + 本机写盘）就是用这条流逐条取音频的。实测单条
    5 秒音频 66ms / 65KB，逐条串行远快于浏览器写盘本身。
    """
    source_path = Path(source)
    command = [
        ffmpeg_executable(), "-hide_banner", "-loglevel", "error",
        "-i", str(source_path), "-vn",
        "-filter:a", tempo_filter(speed),
        "-ac", "1", "-ar", "24000",
        "-c:a", "libmp3lame", "-b:a", "128k",
        "-f", "mp3", "pipe:1",
    ]
    flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=flags,
    )
    produced = 0
    try:
        stdout = process.stdout
        if stdout is None:  # PIPE 已指定，纯属防御
            raise RuntimeError("无法读取 FFmpeg 输出")
        while True:
            chunk = stdout.read(_STREAM_CHUNK)
            if not chunk:
                break
            produced += len(chunk)
            yield chunk
    finally:
        # 调用方中途放弃（关页面、取消导出）时必须收掉子进程，
        # 否则每取消一次就在进程表里留一个 ffmpeg。
        if process.poll() is None:
            process.kill()
        stderr_text = ""
        if process.stderr is not None:
            stderr_text = process.stderr.read().decode("utf-8", "ignore").strip()
            process.stderr.close()
        if process.stdout is not None:
            process.stdout.close()
        code = process.wait()
        # 一个字节都没吐出来才算真失败；已经流出去一半时改状态码也来不及了，
        # 那属于"响应被截断"，由前端 fetch 自行报错。
        if produced == 0 and code not in (0, -9):
            raise RuntimeError(stderr_text or "FFmpeg 变速失败")


def tempo_to_file(source: str | Path, destination: str | Path, speed: float) -> Path:
    """变速并直接落盘。

    调用方必须保证 destination != source：这一步会写目标路径，把导出目录选成
    成品存放目录就会当场覆盖母本。``run_ffmpeg`` 带 ``-y``，不拦自己。
    """
    source_path = Path(source)
    destination_path = Path(destination)
    if source_path.resolve() == destination_path.resolve():
        raise ValueError("变速导出不能覆盖源文件，请换一个导出目录")
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg([
        "-i", str(source_path), "-vn",
        "-filter:a", tempo_filter(speed),
        "-ac", "1", "-ar", "24000",
        "-c:a", "libmp3lame", "-b:a", "128k",
        str(destination_path),
    ])
    return destination_path


