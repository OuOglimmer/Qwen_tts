from __future__ import annotations

import argparse
import logging
import os
import re
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote

import uvicorn
from fastapi import Body, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from .audio import check_speed, safe_filename, tempo_stream
from .config import DATA_DIR, PROJECT_DIR, TEMP_DIR
from .database import Database
from .takes import voice_versions
from .model_manager import ModelManager
from .service import StudioService


database = Database()
service = StudioService(database, ModelManager())
app = FastAPI(title="Qwen TTS Studio API", version="0.2.0")

# file_url 会被列表接口逐行调用，数据目录的最终路径固定，启动时解析一次。
_DATA_DIR_RESOLVED = DATA_DIR.resolve()


def _cors_origins() -> list[str]:
    configured = [origin.strip() for origin in os.getenv("QWEN_TTS_CORS_ORIGINS", "").split(",") if origin.strip()]
    if configured:
        return configured
    # 局域网开放访问：同事通过 http://<本机IP>:3000 打开页面时 Origin 不固定，
    # 默认放开所有来源；需要收紧时用 QWEN_TTS_CORS_ORIGINS 指定白名单。
    return ["*"]


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_methods=["*"],
    allow_headers=["*"],
)

ALLOWED_UPLOAD_SUFFIXES = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac", ".wma"}
MAX_UPLOAD_BYTES = 80 * 2**20
# 上传按块落盘：整段 read() 会把 80MB 一次性读进内存，多文件接口还会叠加。
UPLOAD_CHUNK_BYTES = 1 * 2**20
LOGGER = logging.getLogger("qwentts.api")
MEDIA_TYPES = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".wma": "audio/x-ms-wma",
}
# 交付型写盘（往后端所在的那台电脑写文件、弹目录选择框、打开那个文件夹）默认关闭：
# 部署成局域网服务后，这些动作只会发生在后端那台电脑上，对调用者既无意义又是攻击面。
# 单机自用时在 .env 里设 QWEN_TTS_ALLOW_HOST_FS=1 开放。
HOST_FS_FLAG = "QWEN_TTS_ALLOW_HOST_FS"
DENY_HOST_FS = "该操作会写入后端所在的那台电脑，仅在单机自用模式（QWEN_TTS_ALLOW_HOST_FS=1）下开放"


def host_fs_allowed() -> bool:
    return os.getenv(HOST_FS_FLAG, "").strip().lower() in {"1", "true", "yes", "on"}


def require_host_fs() -> None:
    if not host_fs_allowed():
        raise HTTPException(status_code=403, detail=DENY_HOST_FS)


# ---------------------------------------------------------------- shared token 门
#
# 写接口与文件下载零鉴权，局域网部署时等于把「删项目 / 改数据 / 拿音频」全部裸
# 暴露给任何能连到 7871 端口的人。这里加一道共享 token：``QWEN_TTS_AUTH_TOKEN``
# 为空时不鉴权（单机自用零影响）；设了之后，写方法与音频下载必须带同一把 token。
#
# 音频走 query ``?token=`` 而不是 header，因为 ``<audio src>`` / ``<img>`` 这类
# 媒体标签带不了自定义头；浏览器直开音频文件也是裸 GET。写请求走 header 更干净，
# 但为了脚本 / 跨端调用方便，两者都接受。OPTIONS（CORS 预检）与健康检查放行。

AUTH_TOKEN = os.getenv("QWEN_TTS_AUTH_TOKEN", "").strip()

# 只读接口（列表/状态/进度）不强制 token：它们拿不到数据之外的东西，风险低，
# 也保证前端首次加载页面不因缺 token 而 401。
_PUBLIC_GET_PREFIXES = ("/api/health", "/api/system/status")


def _request_token(request: Request) -> str | None:
    """从三种来源里取 token：query > Bearer header > X-Auth-Token header。"""
    query_token = request.query_params.get("token")
    if query_token:
        return query_token
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("x-auth-token")


def _token_matches(request: Request) -> bool:
    return AUTH_TOKEN and _request_token(request) == AUTH_TOKEN


@app.middleware("http")
async def shared_token_gate(request: Request, call_next: Any) -> Any:
    path = request.url.path
    if AUTH_TOKEN and path.startswith("/api/") and request.method != "OPTIONS":
        if request.method == "GET" and path.startswith(_PUBLIC_GET_PREFIXES):
            return await call_next(request)
        is_write = request.method in {"POST", "PUT", "PATCH", "DELETE"}
        is_file_download = request.method == "GET" and ("/files/" in path)
        if (is_write or is_file_download) and not _token_matches(request):
            return JSONResponse(
                status_code=401,
                content={"detail": "需要共享 token：写请求带 X-Auth-Token 头，音频下载在 URL 后加 ?token=…"},
            )
    return await call_next(request)


@app.exception_handler(sqlite3.Error)
async def handle_sqlite_error(request: Request, exc: sqlite3.Error) -> JSONResponse:
    """把数据库层的失败翻译成有意义的响应，而不是裸 500。

    各路由的 ``except (ValueError, RuntimeError)`` 从来不含 ``sqlite3.IntegrityError``：
    外键 RESTRICT 一拦就变成 Internal Server Error，用户只看到一句「请求失败」。
    这里统一兜底，并把原始信息写进日志（响应里不外泄 SQL 细节）。
    """
    LOGGER.error("数据库操作失败：%s %s", request.method, request.url.path, exc_info=exc)
    if isinstance(exc, sqlite3.IntegrityError):
        return JSONResponse(
            status_code=409,
            content={"detail": "操作被数据库拒绝：名称重复，或有其他记录仍在引用它"},
        )
    return JSONResponse(status_code=500, content={"detail": "数据库操作失败，请查看后端日志"})


class BatchLines(BaseModel):
    voice_id: str
    language: str = "Chinese"
    text: str = Field(min_length=1)


class ProjectPayload(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class ProjectRenamePayload(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class LastStepPayload(BaseModel):
    step: int = Field(ge=1, le=4)


class AttachVoicePayload(BaseModel):
    voice_id: str = Field(min_length=1)
    display_name: str = ""


class VoiceSlotsPayload(BaseModel):
    count: int = Field(default=1, ge=1, le=50)
    prefix: str = "声线"
    names_text: str = ""
    language: str = "Chinese"


class AttachLibraryPayload(BaseModel):
    """从全局声线库直接挂成项目里的已固化槽位。"""
    voice_ids: list[str] = Field(min_length=1, max_length=50)


class CandidatePayload(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    prompt: str = Field(min_length=1)
    audition_text: str = Field(min_length=1)
    language: str = "Chinese"
    count: int = Field(default=4, ge=1, le=8)
    request_id: str | None = None


class RequestDraftPayload(BaseModel):
    project_id: str
    name: str = Field(min_length=1, max_length=80)
    prompt: str = ""
    audition_text: str = ""
    language: str = "Chinese"


class ShortlistPayload(BaseModel):
    shortlisted: bool


class PositionsPayload(BaseModel):
    positions: list[int] = Field(min_length=1)


class BatchEditItem(BaseModel):
    position: int = Field(gt=0)
    text: str = Field(min_length=1)
    language: str = "Chinese"


class BatchEditsPayload(BaseModel):
    items: list[BatchEditItem] = Field(min_length=1)


class RegeneratePayload(BaseModel):
    text: str = Field(min_length=1)
    language: str = "Chinese"


class BatchSelectPayload(BaseModel):
    selected: bool


class UseVersionPayload(BaseModel):
    """换用历史版本：version 是 output_versions 里的版本号，不是 outputs.version。"""
    version: int = Field(ge=1)


class GenerateWithVoicePayload(BaseModel):
    text: str = Field(min_length=1)
    language: str = "Chinese"


class ExportPayload(BaseModel):
    destination: str = Field(min_length=1)
    excerpt_length: int = Field(default=8, ge=5, le=10)
    # 导出副本的播放倍率；源文件不受影响（1.0 = 原速，与改造前完全一致）。
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    # 逐条倍率（order_key → 倍率），覆盖 speed；没列到的行按 speed 走。
    speeds: dict[str, float] | None = None


def _parse_speeds(raw: str | None) -> dict[str, float]:
    """把 ``order_key|倍率`` 的逗号分隔串解析成字典。

    GET 通道（export-plan / archive.zip 都是浏览器直接开链接）没有请求体，
    逐条倍率只能走查询串。``order_key`` 形如 ``batch:<uuid>``，本身含冒号，
    所以分隔符用 ``|`` 而不是 ``:``。解析不动的片段直接跳过 —— 前端只发自己
    刚设过的值，出现脏片段说明链接被手工改坏了，按原速走比整批失败好。
    """
    if not raw:
        return {}
    parsed: dict[str, float] = {}
    for chunk in raw.split(","):
        key, _, value = chunk.strip().rpartition("|")
        key = key.strip()
        if not key:
            continue
        try:
            parsed[key] = float(value)
        except ValueError:
            continue
    return parsed


class PickFolderPayload(BaseModel):
    """弹窗选择文件夹：initial 为对话框起始目录，可为空。"""
    initial: str = ""


class OpenFolderPayload(BaseModel):
    """在后端那台电脑上打开一个文件夹。"""
    path: str = Field(min_length=1)


class OutputOrderPayload(BaseModel):
    """成品页手动排序：按展示顺序给出 order_key 列表，空列表恢复默认排序。"""
    order: list[str] = Field(default_factory=list)


class SupplementPayload(BaseModel):
    voice_id: str
    text: str = Field(min_length=1)
    language: str = "Chinese"


class DesignPayload(BaseModel):
    text: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    language: str = "Chinese"


class DesignArchivePayload(BaseModel):
    """把一次 VoiceDesign 试听固化入库；``design_id`` 由 ``/api/tools/design`` 回传。"""

    design_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=80)
    prompt: str = Field(min_length=1)
    text: str = Field(min_length=1)
    language: str = "Chinese"
    tags: str = ""
    notes: str = ""


class TagAssignPayload(BaseModel):
    """标签写入：``tag_ids`` 优先，``tags`` 逗号串保留给旧调用（上传表单那条路）。"""

    tag_ids: list[str] | None = None
    tags: str | None = None


class TagCreatePayload(BaseModel):
    group_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=20)
    color: str = ""


class TagUpdatePayload(BaseModel):
    name: str | None = Field(default=None, max_length=20)
    color: str | None = None


class TagMergePayload(BaseModel):
    into_tag_id: str = Field(min_length=1)


class ConversionArchivePayload(BaseModel):
    """转换产物的打包清单；每项形如 ``<conversion_id>/<文件名>``，由前端回传。"""

    items: list[str] = Field(min_length=1)


def json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value


def file_url(path: Any) -> str | None:
    """Map a local file under the data directory to a served URL."""
    if not path:
        return None
    try:
        relative = Path(path).resolve().relative_to(_DATA_DIR_RESOLVED)
    except (TypeError, ValueError, OSError):
        return None
    if relative.parts and relative.parts[0] == "backups":
        return None
    url = f"/api/files/{relative.as_posix()}"
    # 鉴权开启时，音频 URL 必须带 query token（<audio src> 带不了 header）。
    if AUTH_TOKEN:
        url += f"?token={quote(AUTH_TOKEN, safe='')}"
    return url


def _audio_response(target: Path) -> FileResponse:
    """浏览器取文件的通道只给音频：数据库、备份、模型权重、清单一律读不到。"""
    suffix = target.suffix.lower()
    if suffix not in MEDIA_TYPES:
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(target, filename=target.name, media_type=MEDIA_TYPES[suffix])


def require_project(project_id: str) -> None:
    if not service.project_exists(project_id):
        raise HTTPException(status_code=404, detail="项目不存在")


def as_http_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


def enrich_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    return {**candidate, "preview_url": file_url(candidate.get("preview_path"))}


def enrich_voice(voice: dict[str, Any]) -> dict[str, Any]:
    enriched = {
        **voice,
        "preview_url": file_url(voice.get("preview_mp3")),
        "reference_url": file_url(voice.get("reference_wav")),
    }
    # 全局库中同名声线靠来源标记区分；来源项目被删除后显示「XX（已删除）」。
    # 判据必须是 source_project_id：v5 已把 voices.project_id 改成这个列名，
    # 原来取 project_id 恒为 None，导致所有有来源的声线都显示"公共库"。
    if "source_project_name" in voice or "source_project_status" in voice:
        if not voice.get("source_project_id"):
            enriched["source_display"] = "公共库"
        elif not voice.get("source_project_name") or voice.get("source_project_status") == "deleted":
            enriched["source_display"] = f"{voice.get('source_project_name') or '已删除项目'}（已删除）"
        else:
            enriched["source_display"] = voice["source_project_name"]
    return enriched


def enrich_voices(voices: list[dict[str, Any]], project_id: str | None = None) -> list[dict[str, Any]]:
    """声线列表统一出口：附带标签（一次批量查完，不逐行打库）。"""
    tag_map = service.tags.voice_tag_map(voice["id"] for voice in voices)
    project_map = service.tags.project_tag_map(project_id) if project_id else {}
    rows: list[dict[str, Any]] = []
    for voice in voices:
        item = enrich_voice(voice)
        item["tag_list"] = tag_map.get(voice["id"], [])
        item["tag_ids"] = [tag["id"] for tag in item["tag_list"]]
        if project_id:
            # 项目语义标签：全库页只读展示，不在那里编辑（否则全局库会变成项目数据镜像）。
            item["project_tags"] = project_map.get(voice["id"], [])
            item["project_tag_ids"] = [tag["id"] for tag in item["project_tags"]]
        rows.append(item)
    return rows


def enrich_batch_item(item: dict[str, Any], versions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    enriched = {**item, "output_url": file_url(item.get("output_mp3"))}
    if versions is not None:
        # 保留的历史版本（含当前版），最新在前，供前端试听旧版。
        enriched["versions"] = [
            {
                "version": entry["version"],
                "created_at": entry["created_at"],
                "url": file_url(entry["output_mp3"]),
            }
            for entry in versions
        ]
    return enriched


# ---------------------------------------------------------------- system


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "model": json_safe(service.models.status())}


@app.get("/api/system/status")
def system_status() -> dict[str, Any]:
    return {
        "model": json_safe(service.models.status()),
        "data_dir": str(DATA_DIR),
        "projects": len(service.list_projects()),
    }


# tkinter 必须持有主窗口事件循环才能弹对话框，放进独立 Python 子进程，
# 避免阻塞/污染 FastAPI 进程本身；stdout 强制 UTF-8，中文路径不依赖本机 locale。
FOLDER_PICKER_SCRIPT = (
    "import sys, tkinter as tk\n"
    "from tkinter import filedialog\n"
    "root = tk.Tk()\n"
    "root.withdraw()\n"
    "root.attributes('-topmost', True)\n"
    "kwargs = {'initialdir': sys.argv[1]} if len(sys.argv) > 1 else {}\n"
    "path = filedialog.askdirectory(title='选择导出文件夹', **kwargs)\n"
    "root.destroy()\n"
    "print(path)\n"
)


def _open_folder_dialog(initial_dir: str = "", timeout_seconds: int = 600) -> str | None:
    command = [sys.executable, "-c", FOLDER_PICKER_SCRIPT]
    if initial_dir:
        command.append(initial_dir)
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    if result.returncode != 0:
        tail = (result.stderr or "").strip().splitlines()
        raise RuntimeError(tail[-1] if tail else f"文件夹选择框进程异常退出（{result.returncode}）")
    return result.stdout.strip().strip('"') or None


@app.post("/api/dialog/pick-folder")
def pick_folder(payload: PickFolderPayload) -> dict[str, Any]:
    """在后端所在机器弹出系统文件夹选择框，返回选中路径；取消返回 null。

    仅单机自用模式开放：服务化部署时这个弹框会弹在后端那台电脑的桌面上，对调用者毫无意义。
    """
    require_host_fs()
    initial = payload.initial.strip()
    if initial and not Path(initial).is_dir():
        initial = ""
    try:
        path = _open_folder_dialog(initial)
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=504, detail="打开文件夹选择框超时，请重试") from exc
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        raise HTTPException(status_code=500, detail=f"无法弹出文件夹选择框：{exc}") from exc
    return {"path": path}


@app.post("/api/system/open-folder")
def open_folder(payload: OpenFolderPayload) -> dict[str, str]:
    """导出完成后一键跳转：在后端所在机器的资源管理器中打开该文件夹。"""
    require_host_fs()
    target = Path(payload.path.strip())
    if not target.is_dir():
        raise HTTPException(status_code=404, detail="文件夹不存在，可能已被移动或删除")
    if os.name != "nt":
        raise HTTPException(status_code=500, detail="打开文件夹目前仅支持 Windows")
    os.startfile(str(target))  # noqa: S606 - 本机工具，路径来自本机弹窗/导出记录
    return {"opened": str(target)}


# ---------------------------------------------------------------- projects


@app.get("/api/projects")
def projects(include_archived: bool = Query(default=False)) -> list[dict[str, Any]]:
    return json_safe(service.project_summaries(include_archived))


@app.put("/api/projects/{project_id}")
def rename_project(project_id: str, payload: ProjectRenamePayload) -> dict[str, str]:
    require_project(project_id)
    try:
        return {"name": service.rename_project(project_id, payload.name)}
    except (ValueError, RuntimeError) as exc:
        raise as_http_error(exc) from exc


@app.post("/api/projects")
def create_project(payload: ProjectPayload) -> dict[str, Any]:
    try:
        project_id = service.create_project(payload.name)
    except (ValueError, RuntimeError) as exc:
        raise as_http_error(exc) from exc
    project = service.get_project(project_id)
    return json_safe(project or {"id": project_id, "name": payload.name})


@app.post("/api/projects/{project_id}/activate")
def activate_project(project_id: str) -> dict[str, str]:
    try:
        context = service.switch_project_context(project_id)
    except (ValueError, RuntimeError) as exc:
        raise as_http_error(exc) from exc
    return {"current": context.current_project_id, "name": context.name}


@app.delete("/api/projects/{project_id}")
def delete_project(project_id: str, mode: str = Query(default="archive")) -> dict[str, Any]:
    """删除项目双选项：archive = 删除并保留声线（软删）；purge = 彻底删除。

    purge 时仍被其他有效项目清单或任务引用的声线强制保留，并在结果里列名。
    """
    require_project(project_id)
    if mode not in {"archive", "purge"}:
        raise HTTPException(status_code=400, detail="删除模式仅支持 archive 或 purge")
    try:
        if mode == "archive":
            return {"deleted": service.archive_project(project_id), "mode": mode, "retained_voices": []}
        result = service.purge_project(project_id)
        return {"deleted": result["name"], "mode": mode, "retained_voices": result["retained_voices"]}
    except (ValueError, RuntimeError, OSError) as exc:
        raise as_http_error(exc) from exc


@app.get("/api/projects/{project_id}/last-step")
def get_last_step(project_id: str) -> dict[str, int]:
    require_project(project_id)
    return {"step": service.resolve_last_step(project_id)}


@app.put("/api/projects/{project_id}/last-step")
def set_last_step(project_id: str, payload: LastStepPayload) -> dict[str, int]:
    require_project(project_id)
    try:
        service.set_last_step(project_id, payload.step)
    except (ValueError, RuntimeError) as exc:
        raise as_http_error(exc) from exc
    return {"step": payload.step}


# ---------------------------------------------------------------- voice workflow


@app.get("/api/projects/{project_id}/voice-requests")
def voice_requests(project_id: str) -> list[dict[str, Any]]:
    require_project(project_id)
    rows = service.list_voice_requests(project_id)
    tag_map = service.tags.request_tag_map(row["id"] for row in rows)
    return json_safe([
        {
            **row,
            "direct_output_url": file_url(row.get("direct_output_mp3")),
            "tag_list": tag_map.get(row["id"], []),
            "tag_ids": [tag["id"] for tag in tag_map.get(row["id"], [])],
        }
        for row in rows
    ])


@app.post("/api/projects/{project_id}/voice-requests")
def create_voice_slots(project_id: str, payload: VoiceSlotsPayload) -> dict[str, Any]:
    require_project(project_id)
    try:
        created = service.create_voice_slots(
            project_id, payload.count, payload.prefix, payload.names_text, payload.language
        )
        return {"created": created, "items": json_safe(service.list_voice_requests(project_id))}
    except (ValueError, RuntimeError) as exc:
        raise as_http_error(exc) from exc


@app.post("/api/projects/{project_id}/voice-requests/attach-library")
def attach_library_voices(project_id: str, payload: AttachLibraryPayload) -> dict[str, Any]:
    """把「全部声线」库里已有的声线挂成这个项目的已固化槽位。

    与「全部声线」页的 /voices/attach 区别：那个只写项目声线清单（第 2 步可绑定），
    这个同时建需求单槽位，第 1 步的表里能看见"这个角色的声线已经定了"。
    """
    require_project(project_id)
    try:
        created = service.attach_library_voices(project_id, payload.voice_ids)
        return {"created": created, "items": json_safe(service.list_voice_requests(project_id))}
    except (ValueError, RuntimeError) as exc:
        raise as_http_error(exc) from exc


@app.delete("/api/projects/{project_id}/voice-requests/{request_id}")
def delete_voice_request(project_id: str, request_id: str) -> dict[str, Any]:
    require_project(project_id)
    try:
        name, preserved = service.delete_voice_request(project_id, request_id)
        return {"deleted": name, "preserved_asset": preserved}
    except (ValueError, RuntimeError) as exc:
        raise as_http_error(exc) from exc


@app.put("/api/voice-requests/{request_id}")
def save_voice_request(request_id: str, payload: RequestDraftPayload) -> dict[str, str]:
    require_project(payload.project_id)
    try:
        service.save_voice_request(
            request_id, payload.project_id, payload.name, payload.prompt,
            payload.audition_text, payload.language,
        )
        return {"status": "saved"}
    except (ValueError, RuntimeError) as exc:
        raise as_http_error(exc) from exc


@app.post("/api/projects/{project_id}/voice-requests/generate")
def generate_candidates(project_id: str, payload: CandidatePayload) -> dict[str, Any]:
    """Start candidate generation in the background; poll /api/voice-generation/status."""
    require_project(project_id)
    try:
        task = service.start_candidate_generation(
            project_id, payload.name, payload.prompt, payload.audition_text,
            payload.language, payload.count, payload.request_id,
        )
    except RuntimeError as exc:
        # RuntimeError 在这里是"已有任务在跑"，属于状态冲突而不是参数错误。
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise as_http_error(exc) from exc
    return json_safe({"started": True, "task": task})


@app.get("/api/voice-generation/status")
def candidate_generation_status() -> dict[str, Any] | None:
    return json_safe(service.candidate_generation_status())


@app.post("/api/voice-generation/cancel")
def cancel_candidate_generation() -> dict[str, bool]:
    """Stop candidate generation after the current candidate finishes."""
    return {"cancelled": service.cancel_candidate_generation()}


@app.get("/api/voice-requests/{request_id}/candidates")
def request_candidates(request_id: str) -> dict[str, Any]:
    request = service.get_voice_request(request_id)
    if not request:
        raise HTTPException(status_code=404, detail="声线工作项不存在")
    return json_safe({
        "request": request,
        "candidates": [enrich_candidate(row) for row in service.request_candidates(request_id, 16)],
        "shortlisted": [enrich_candidate(row) for row in service.shortlisted_candidates(request_id, 8)],
    })


@app.post("/api/voice-requests/{request_id}/select/{candidate_id}")
def select_candidate(request_id: str, candidate_id: str) -> dict[str, str]:
    try:
        service.select_candidate(request_id, candidate_id)
        return {"status": "selected"}
    except (ValueError, RuntimeError) as exc:
        raise as_http_error(exc) from exc


@app.post("/api/voice-requests/{request_id}/deselect")
def deselect_candidate(request_id: str) -> dict[str, str]:
    """取消选定：再点一次「已选定」的候选就走这里。已固化的槽位会返回 400 要求先解冻。"""
    try:
        return {"status": service.deselect_candidate(request_id)}
    except (ValueError, RuntimeError) as exc:
        raise as_http_error(exc) from exc


@app.post("/api/voice-requests/{request_id}/candidates/{candidate_id}/shortlist")
def shortlist_candidate(request_id: str, candidate_id: str, payload: ShortlistPayload) -> dict[str, str]:
    try:
        service.set_candidate_shortlisted(request_id, candidate_id, payload.shortlisted)
        return {"status": "shortlisted" if payload.shortlisted else "removed"}
    except (ValueError, RuntimeError) as exc:
        raise as_http_error(exc) from exc


@app.post("/api/voice-requests/{request_id}/candidates/{candidate_id}/keep-single")
def keep_single_output(request_id: str, candidate_id: str) -> dict[str, Any]:
    try:
        output = service.keep_candidate_as_single_output(request_id, candidate_id)
        return {"output_path": output, "url": file_url(output)}
    except (ValueError, RuntimeError, OSError) as exc:
        raise as_http_error(exc) from exc


@app.post("/api/projects/{project_id}/candidates/keep-all-single")
def keep_all_single_outputs(project_id: str) -> dict[str, Any]:
    require_project(project_id)
    try:
        return {"retained": service.keep_all_selected_as_single_outputs(project_id)}
    except (ValueError, RuntimeError, OSError) as exc:
        raise as_http_error(exc) from exc


@app.get("/api/projects/{project_id}/voice-requests/{request_id}/history")
def voice_request_history(project_id: str, request_id: str) -> list[dict[str, Any]]:
    """这个槽位换掉的旧声线。

    它们已经是 ``replaced``，全局库默认不列，所以只能从槽位这一侧翻出来。
    删除走 ``DELETE /api/library/voices/{voice_id}``（带着项目 id），
    那边会拦住"还有成品引用"的资产。
    """
    require_project(project_id)
    try:
        rows = service.voice_request_history(project_id, request_id)
        return json_safe([enrich_voice(row) for row in rows])
    except (ValueError, RuntimeError) as exc:
        raise as_http_error(exc) from exc


@app.post("/api/voice-requests/{request_id}/reopen")
def reopen_request(request_id: str) -> dict[str, str]:
    try:
        return {"reopened": service.reopen_fixed_request(request_id)}
    except (ValueError, RuntimeError) as exc:
        raise as_http_error(exc) from exc


@app.get("/api/projects/{project_id}/voice-progress")
def voice_progress(project_id: str) -> dict[str, int]:
    require_project(project_id)
    return service.request_progress(project_id)


# ---------------------------------------------------------------- voice assets


@app.get("/api/projects/{project_id}/voices")
def voices(project_id: str, include_replaced: bool = Query(default=False)) -> list[dict[str, Any]]:
    """项目清单。默认只给当前在用的版本；``include_replaced=1`` 带上换版历史（归属溯源用）。"""
    require_project(project_id)
    return json_safe(enrich_voices(service.list_voices(project_id, include_replaced), project_id))


@app.get("/api/library/voices")
def library_voices(
    tags: str = Query(default=""),
    tag_mode: str = Query(default="any", pattern="^(any|all)$"),
) -> list[dict[str, Any]]:
    """全局声线库：所有有效声线资产，带来源项目标记与已被多少项目引用。

    ``tags`` 为逗号分隔的标签名；``tag_mode=all`` 要求全部命中，默认 any。
    """
    return json_safe(enrich_voices(service.library_voices(tags, tag_mode)))


# ---------------------------------------------------------------- tags


@app.get("/api/tags")
def list_tags(scope: str = Query(default="", pattern="^(|voice|project|request)$")) -> list[dict[str, Any]]:
    """标签组 + 词条 + 使用计数（一次取全，前端按组分段渲染）。"""
    return json_safe(service.list_tags(scope or None))


@app.post("/api/tags")
def create_tag(payload: TagCreatePayload) -> dict[str, Any]:
    try:
        return json_safe(service.create_tag(payload.group_id, payload.name, payload.color))
    except (ValueError, RuntimeError) as exc:
        raise as_http_error(exc) from exc


@app.patch("/api/tags/{tag_id}")
def update_tag(tag_id: str, payload: TagUpdatePayload) -> dict[str, Any]:
    try:
        return json_safe(service.update_tag(tag_id, payload.name, payload.color))
    except (ValueError, RuntimeError) as exc:
        raise as_http_error(exc) from exc


@app.post("/api/tags/{tag_id}/merge")
def merge_tag(tag_id: str, payload: TagMergePayload) -> dict[str, Any]:
    """把同组里的一个词条合并进另一个：关联整体迁移，旧词条留 merged_into。"""
    try:
        return json_safe(service.merge_tag(tag_id, payload.into_tag_id))
    except (ValueError, RuntimeError) as exc:
        raise as_http_error(exc) from exc


@app.put("/api/library/voices/{voice_id}/tags")
def set_voice_tags(voice_id: str, payload: TagAssignPayload) -> dict[str, Any]:
    try:
        tags = service.set_voice_tags(voice_id, payload.tag_ids, payload.tags)
        return json_safe({"tag_list": tags, "tag_ids": [tag["id"] for tag in tags]})
    except (ValueError, RuntimeError) as exc:
        raise as_http_error(exc) from exc


@app.put("/api/projects/{project_id}/voices/{voice_id}/tags")
def set_project_voice_tags(project_id: str, voice_id: str, payload: TagAssignPayload) -> dict[str, Any]:
    require_project(project_id)
    try:
        tags = service.set_project_voice_tags(project_id, voice_id, payload.tag_ids, payload.tags)
        return json_safe({"project_tags": tags, "project_tag_ids": [tag["id"] for tag in tags]})
    except (ValueError, RuntimeError) as exc:
        raise as_http_error(exc) from exc


@app.put("/api/voice-requests/{request_id}/tags")
def set_request_tags(request_id: str, payload: TagAssignPayload) -> dict[str, Any]:
    try:
        tags = service.set_request_tags(request_id, payload.tag_ids, payload.tags)
        return json_safe({"tag_list": tags, "tag_ids": [tag["id"] for tag in tags]})
    except (ValueError, RuntimeError) as exc:
        raise as_http_error(exc) from exc


@app.delete("/api/library/voices/{voice_id}")
def delete_library_voice(voice_id: str, project_id: str = Query(default="")) -> dict[str, str]:
    """从全局库删除资产；仍被任何项目清单或任务引用时后端直接拦截。

    不传 project_id 时按全局删除处理：所有关联都视为占用。
    """
    try:
        return {"deleted": service.delete_voice_asset(project_id, voice_id)}
    except (ValueError, RuntimeError, OSError) as exc:
        raise as_http_error(exc) from exc


@app.post("/api/projects/{project_id}/voices/attach")
def attach_voice(project_id: str, payload: AttachVoicePayload) -> dict[str, Any]:
    """把全局库中已有声线加入本项目清单（不复制音频文件，仅建关联）。"""
    require_project(project_id)
    try:
        display = service.attach_voice(project_id, payload.voice_id, payload.display_name)
        return {"attached": payload.voice_id, "display_name": display}
    except (ValueError, RuntimeError) as exc:
        raise as_http_error(exc) from exc


@app.delete("/api/projects/{project_id}/voices/{voice_id}")
def delete_voice(project_id: str, voice_id: str) -> dict[str, str]:
    require_project(project_id)
    try:
        return {"deleted": service.delete_voice_asset(project_id, voice_id)}
    except (ValueError, RuntimeError, OSError) as exc:
        raise as_http_error(exc) from exc


@app.post("/api/voices/upload")
def upload_voice(
    file: UploadFile = File(...),
    project_id: str = Form(""),
    name: str = Form(...),
    ref_text: str = Form(""),
    language: str = Form("Chinese"),
    ref_language: str = Form(""),
    x_vector_only: bool = Form(False),
    preview_text: str = Form(...),
    tags: str = Form(""),
    notes: str = Form(""),
) -> dict[str, Any]:
    """上传声线入全局库；project_id 可为空（公共库），指定时同步加入该项目清单。

    ``language`` 是**试听文本**的语种（只用于朗读 preview_text）；
    ``ref_language`` 是**参考音频本身**的语种，仅作资产标注，不参与建 prompt。
    """
    clean_project = project_id.strip()
    if clean_project:
        require_project(clean_project)
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_UPLOAD_SUFFIXES:
        raise HTTPException(status_code=400, detail="参考音频仅支持 WAV/MP3/FLAC/OGG/M4A/AAC/WMA")
    uploads_dir = TEMP_DIR / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    source = uploads_dir / f"{uuid.uuid4().hex}{suffix}"
    _copy_upload(file, source, "参考音频")
    try:
        voice_id, preview = service.archive_uploaded_voice(
            clean_project or None, name, str(source), ref_text, language,
            bool(x_vector_only), preview_text, tags, notes, ref_language,
        )
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        raise as_http_error(exc) from exc
    finally:
        source.unlink(missing_ok=True)
    voice = service.get_voice(voice_id)
    return json_safe({
        "voice_id": voice_id,
        "preview_path": preview,
        "preview_url": file_url(preview),
        "voice": json_safe(enrich_voices([voice])[0]) if voice else None,
    })


@app.post("/api/projects/{project_id}/voices/{voice_id}/generate")
def generate_with_voice(project_id: str, voice_id: str, payload: GenerateWithVoicePayload) -> dict[str, Any]:
    require_project(project_id)
    if not service.project_voice(voice_id, project_id):
        raise HTTPException(status_code=404, detail="声线不在当前项目清单中或已失效")
    output_root = PROJECT_DIR / project_id / "outputs" / "quick" / voice_id[:8]
    output_root.mkdir(parents=True, exist_ok=True)
    stem = f"quick_{uuid.uuid4().hex[:8]}"
    try:
        mp3_path = service.generate_with_voice(voice_id, payload.text, payload.language, output_root / stem, project_id)
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        raise as_http_error(exc) from exc
    return json_safe({"output_path": mp3_path, "url": file_url(mp3_path)})


@app.post("/api/projects/{project_id}/voices/{voice_id}/regenerate")
def regenerate_voice(project_id: str, voice_id: str) -> dict[str, Any]:
    require_project(project_id)
    try:
        return json_safe(service.start_voice_regeneration(project_id, voice_id))
    except (ValueError, RuntimeError) as exc:
        raise as_http_error(exc) from exc


@app.get("/api/voice-regeneration/progress")
def voice_regeneration_progress() -> dict[str, Any] | None:
    return json_safe(service.voice_regeneration_progress())


@app.post("/api/voice-regeneration/cancel")
def cancel_voice_regeneration() -> dict[str, bool]:
    """Stop the running voice-wide regeneration after the current item."""
    return {"cancelled": service.cancel_voice_regeneration()}


@app.post("/api/projects/{project_id}/voices/fix")
def fix_voices(project_id: str, payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    require_project(project_id)
    request_id = payload.get("request_id") if isinstance(payload, dict) else None
    try:
        return {
            "fixed": service.fix_selected_voices(project_id, request_id),
            "voices": json_safe(enrich_voices(service.list_voices(project_id), project_id)),
        }
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        raise as_http_error(exc) from exc


# ---------------------------------------------------------------- batch


@app.get("/api/projects/{project_id}/batch")
def batch(project_id: str) -> list[dict[str, Any]]:
    require_project(project_id)
    histories = service.batch_version_histories(project_id)
    return json_safe([
        enrich_batch_item(item, histories.get(item["id"], []))
        for item in service.list_batch_items(project_id)
    ])


@app.post("/api/projects/{project_id}/batch")
def add_batch(project_id: str, payload: BatchLines) -> dict[str, Any]:
    require_project(project_id)
    try:
        added = service.add_batch_lines(project_id, payload.voice_id, payload.language, payload.text)
        histories = service.batch_version_histories(project_id)
        return {"added": added, "items": json_safe([
            enrich_batch_item(item, histories.get(item["id"], []))
            for item in service.list_batch_items(project_id)
        ])}
    except (ValueError, RuntimeError) as exc:
        raise as_http_error(exc) from exc


@app.post("/api/projects/{project_id}/batch/run")
def run_batch(project_id: str) -> dict[str, Any]:
    require_project(project_id)
    try:
        task = service.start_batch(project_id)
    except RuntimeError as exc:
        # RuntimeError 在这里是"已有任务在跑"，属于状态冲突而不是参数错误。
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise as_http_error(exc) from exc
    return json_safe({"started": True, "task": task})


@app.get("/api/projects/{project_id}/batch/status")
def batch_status(project_id: str) -> dict[str, Any]:
    require_project(project_id)
    counts = service.batch.status_counts(project_id)
    task = service.batch_task_status() or {}
    return {
        "task": json_safe(task or None),
        "counts": counts,
        "running": bool(task) and task.get("project_id") == project_id and bool(task.get("running")),
    }


@app.post("/api/projects/{project_id}/batch/cancel")
def cancel_batch(project_id: str) -> dict[str, str]:
    require_project(project_id)
    service.cancel_running_batch(project_id)
    return {"status": "cancelled"}


@app.post("/api/projects/{project_id}/batch/clear")
def clear_batch(project_id: str) -> dict[str, str]:
    require_project(project_id)
    service.clear_pending_batch(project_id)
    return {"status": "cleared"}


@app.post("/api/projects/{project_id}/batch/save-edits")
def save_batch_edits(project_id: str, payload: BatchEditsPayload) -> dict[str, int]:
    require_project(project_id)
    rows = [[item.position, "", item.text, item.language] for item in payload.items]
    return {"updated": service.save_batch_edits(project_id, rows)}


@app.post("/api/projects/{project_id}/batch/{item_id}/regenerate")
def regenerate_batch_item(project_id: str, item_id: str, payload: RegeneratePayload) -> dict[str, Any]:
    require_project(project_id)
    try:
        mp3_path = service.regenerate_batch_item(project_id, item_id, payload.text, payload.language)
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        raise as_http_error(exc) from exc
    return json_safe({"output_path": mp3_path, "url": file_url(mp3_path)})


@app.post("/api/projects/{project_id}/batch/{item_id}/select")
def select_batch_item(project_id: str, item_id: str, payload: BatchSelectPayload) -> dict[str, Any]:
    """Promote a completed result into the deliverable assets (or revert it)."""
    require_project(project_id)
    try:
        return json_safe({"item": service.select_batch_item(project_id, item_id, payload.selected)})
    except (ValueError, RuntimeError) as exc:
        raise as_http_error(exc) from exc


@app.post("/api/projects/{project_id}/batch/{item_id}/use-version")
def use_batch_version(project_id: str, item_id: str, payload: UseVersionPayload) -> dict[str, Any]:
    """把某条任务的历史版本换回当前成品（复制成新的一版，旧版继续留档）。"""
    require_project(project_id)
    try:
        return json_safe(service.use_batch_version(project_id, item_id, payload.version))
    except (ValueError, RuntimeError, OSError) as exc:
        raise as_http_error(exc) from exc


@app.delete("/api/projects/{project_id}/batch/{position}")
def delete_batch(project_id: str, position: int) -> dict[str, Any]:
    require_project(project_id)
    try:
        return json_safe(service.delete_batch_item(project_id, position))
    except (ValueError, RuntimeError) as exc:
        raise as_http_error(exc) from exc


@app.post("/api/projects/{project_id}/batch/delete")
def delete_batches(project_id: str, payload: PositionsPayload) -> dict[str, Any]:
    require_project(project_id)
    try:
        return {"deleted": json_safe(service.delete_batch_items(project_id, payload.positions))}
    except (ValueError, RuntimeError) as exc:
        raise as_http_error(exc) from exc


# ---------------------------------------------------------------- outputs


@app.get("/api/projects/{project_id}/single-outputs")
def single_outputs(project_id: str) -> list[dict[str, Any]]:
    require_project(project_id)
    rows = service.single_line_outputs(project_id)
    return json_safe([{**row, "url": file_url(row.get("direct_output_mp3"))} for row in rows])


@app.delete("/api/projects/{project_id}/single-outputs/{request_id}")
def delete_single_output(project_id: str, request_id: str) -> dict[str, Any]:
    require_project(project_id)
    try:
        name, removed = service.delete_single_line_output(project_id, request_id)
        return {"deleted": name, "file_removed": removed}
    except (ValueError, RuntimeError, OSError) as exc:
        raise as_http_error(exc) from exc


@app.get("/api/projects/{project_id}/outputs")
def outputs(project_id: str) -> dict[str, Any]:
    require_project(project_id)
    output_root = (PROJECT_DIR / project_id / "outputs").resolve()

    def deliverable_row(item: dict[str, Any], source: str, order_key: str, audio_key: str) -> dict[str, Any]:
        row = {**item, "source": source, "order_key": order_key}
        if not item.get(audio_key):
            # 失败/中断的补录行没有音频，保留错误信息供成品页展示与删除。
            row["download_path"] = None
            row["url"] = None
            return row
        try:
            row["download_path"] = str(Path(item[audio_key]).resolve().relative_to(output_root)).replace("\\", "/")
        except (KeyError, ValueError):
            row["download_path"] = None
        row["url"] = file_url(item.get(audio_key))
        return row

    rows: list[dict[str, Any]] = []
    # 批量行的历史版本（含当前版，最新在前）：成品页据此展开「换用这一版」，
    # 否则重新生成一次就把旧音频顶掉、再没有回到它的入口。单句/补录没有版本表。
    histories = service.batch.version_histories(project_id)
    for item in service.list_batch_items(project_id):
        # Only user-selected results are deliverable assets.
        if item.get("status") == "selected" and item.get("output_mp3"):
            row = deliverable_row(item, "batch", f"batch:{item['id']}", "output_mp3")
            row["versions"] = [
                {
                    "version": int(entry["version"]),
                    "created_at": entry["created_at"],
                    "url": file_url(entry["output_mp3"]),
                }
                for entry in histories.get(item["id"], [])
            ]
            rows.append(row)
    for item in service.single_line_outputs(project_id):
        rows.append(deliverable_row({**item, "version": None}, "single", f"single:{item['id']}", "direct_output_mp3"))
    # 补录单句全部返回（含失败行），失败行没有音频但保留错误信息，供成品页展示与删除。
    for item in service.supplements.list_lines(project_id):
        row = deliverable_row({**item, "name": item["voice_name"], "version": None}, "supplement", f"supplement:{item['id']}", "output_mp3")
        rows.append(row)
    # 声线版本号：同角色第几版。成品页靠它显示「v1 / v2」，混版交付时才解释得清。
    versions = voice_versions(service.db, project_id)
    for row in rows:
        voice_id = row.get("voice_id")
        row["voice_version"] = versions.get(str(voice_id), 0) if voice_id else 0
    return {
        "items": json_safe(service.exports.order_output_rows(project_id, rows)),
    }


@app.put("/api/projects/{project_id}/outputs/order")
def save_outputs_order(project_id: str, payload: OutputOrderPayload) -> dict[str, int]:
    require_project(project_id)
    try:
        return {"saved": service.exports.set_output_order(project_id, payload.order)}
    except ValueError as exc:
        raise as_http_error(exc) from exc


@app.post("/api/projects/{project_id}/outputs/supplement")
def create_supplement(project_id: str, payload: SupplementPayload) -> dict[str, Any]:
    """Record one supplementary line with an archived voice and synthesize it now."""
    require_project(project_id)
    try:
        row = service.supplements.create(project_id, payload.voice_id, payload.text, payload.language)
    except (ValueError, RuntimeError) as exc:
        raise as_http_error(exc) from exc
    if row is None:
        raise HTTPException(status_code=500, detail="补录单句写入失败")
    row = {**row, "source": "supplement", "name": row.get("voice_name"), "version": None,
           "order_key": f"supplement:{row['id']}", "url": file_url(row.get("output_mp3"))}
    return json_safe(row)


@app.delete("/api/projects/{project_id}/outputs/supplement/{line_id}")
def delete_supplement(project_id: str, line_id: str) -> dict[str, Any]:
    require_project(project_id)
    try:
        name, removed = service.supplements.delete_line(project_id, line_id)
        return {"deleted": name, "file_removed": removed}
    except (ValueError, RuntimeError, OSError) as exc:
        raise as_http_error(exc) from exc


@app.post("/api/projects/{project_id}/outputs/export")
def export_outputs(project_id: str, payload: ExportPayload) -> dict[str, Any]:
    """把成品复制到后端那台电脑上的目录——仅单机自用模式开放。

    服务化部署时调用者要的是自己电脑上的文件夹，那条路是 export-plan + 浏览器写盘；
    这里保留给同机使用，并且目标路径被限制在后端数据目录内。
    """
    require_project(project_id)
    require_host_fs()
    try:
        destination, count = service.export_completed_batch(
            project_id, payload.destination, payload.excerpt_length, payload.speed, payload.speeds
        )
        return {"destination": destination, "count": count, "speed": payload.speed}
    except (ValueError, RuntimeError, OSError) as exc:
        raise as_http_error(exc) from exc


@app.get("/api/projects/{project_id}/outputs/archive.zip")
def archive_outputs(
    project_id: str,
    excerpt_length: int = Query(default=8, ge=5, le=10),
    speed: float = Query(default=1.0, ge=0.5, le=2.0),
    speeds: str | None = Query(default=None),
) -> StreamingResponse:
    """全部成品打成 ZIP 直接流给调用者；后端那台电脑上不留这份交付文件。

    ``speed`` 是整批兜底倍率，``speeds`` 按 ``order_key|倍率`` 覆盖到单条：
    有变速的条目逐条变速到临时目录再打包，源文件不动。
    """
    require_project(project_id)
    per_row = _parse_speeds(speeds)
    try:
        entries, paths = service.exports.export_plan(project_id, excerpt_length, speed, per_row)
    except (ValueError, RuntimeError, OSError) as exc:
        raise as_http_error(exc) from exc
    project = service.get_project(project_id) or {}
    download_name = f"{safe_filename(project.get('name', 'project'), 'project')}_成品.zip"
    retimed = sum(1 for entry in entries if abs(float(entry["speed"]) - 1.0) > 1e-6)
    speed_note = f" · {retimed} 条变速" if retimed else ""
    database.log_activity(
        project_id, "batch", None, "打包全部成品", f"{len(entries)} 条 · 流式下载{speed_note}"
    )
    return StreamingResponse(
        service.exports.iter_archive(entries, paths, speed),
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"outputs.zip\"; filename*=UTF-8''{quote(download_name)}"
            )
        },
    )


@app.get("/api/projects/{project_id}/outputs/export-preflight")
def export_preflight(project_id: str) -> dict[str, Any]:
    """导出前检查：只报告事实，不替用户做取舍，也不拦导出。

    两件事值得在他点导出前说清楚：同一句台词有几条成品、交付时取了哪一条；
    以及同一个角色是不是混着几版声线（换版后没重录本来就会这样，但该知道）。
    """
    require_project(project_id)
    try:
        return json_safe(service.export_preflight(project_id))
    except (ValueError, RuntimeError, OSError) as exc:
        raise as_http_error(exc) from exc


@app.get("/api/projects/{project_id}/outputs/export-plan")
def export_plan(
    project_id: str,
    excerpt_length: int = Query(default=8, ge=5, le=10),
    speed: float = Query(default=1.0, ge=0.5, le=2.0),
    speeds: str | None = Query(default=None),
) -> dict[str, Any]:
    """导出清单：每条叫什么文件名、用哪个倍率由后端决定，音频以 /api/files/… 地址提供给浏览器写盘。

    部署为共享服务时，调用者的文件夹选择框只能长在调用者自己的机器上，
    因此这里只出一份「该写什么文件名」的清单，实际落盘交给前端。
    包里只有音频文件，不再附带 manifest.json。

    ``speed`` / ``speeds`` 直接编码进每条的 url：前端不必知道变速是怎么实现的，
    照 url 取回的就是该条倍率的副本。原速的条目不加参数，URL 与改造前逐字节一致。
    """
    require_project(project_id)
    per_row = _parse_speeds(speeds)
    try:
        entries, paths = service.exports.export_plan(project_id, excerpt_length, speed, per_row)
    except (ValueError, RuntimeError, OSError) as exc:
        raise as_http_error(exc) from exc
    files: list[dict[str, Any]] = []
    for entry, path in zip(entries, paths):
        url = file_url(path)
        entry_speed = float(entry["speed"])
        if url and abs(entry_speed - 1.0) > 1e-6:
            # file_url 在开启鉴权时末尾已经带 ?token=：追加参数得看有没有 query。
            url += f"{'&' if '?' in url else '?'}speed={entry_speed:g}"
        files.append({**entry, "url": url})
    missing = [entry["file"] for entry in files if not entry["url"]]
    if missing:
        raise HTTPException(status_code=500, detail=f"以下成品无法对外提供：{', '.join(missing)}")
    return {"files": json_safe(files), "count": len(files)}


@app.get("/api/projects/{project_id}/files/{file_path:path}")
def download_project_file(project_id: str, file_path: str) -> FileResponse:
    require_project(project_id)
    project_root = (PROJECT_DIR / project_id / "outputs").resolve()
    target = (project_root / file_path).resolve()
    if project_root not in target.parents or not target.is_file():
        raise HTTPException(status_code=404, detail="输出文件不存在")
    return _audio_response(target)


@app.get("/api/files/{file_path:path}")
def download_data_file(
    file_path: str, speed: float = Query(default=1.0, ge=0.5, le=2.0)
) -> Any:
    """Serve generated audio (candidates, previews, outputs) to the frontend.

    ``speed`` 只给成品导出用：它作用在"这次交付的副本"上，源文件一个字节都不改。
    不带这个参数（或恰好 1.0）时仍走原来的 FileResponse 通道，行为与改造前一致；
    要变速时改走 ffmpeg 流式现算，不落临时文件，路径安全校验与上面完全相同。
    """
    target = (_DATA_DIR_RESOLVED / file_path).resolve()
    if _DATA_DIR_RESOLVED not in target.parents or not target.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    if "backups" in target.parts:
        raise HTTPException(status_code=404, detail="文件不存在")
    if abs(speed - 1.0) < 1e-6:
        return _audio_response(target)
    if target.suffix.lower() not in MEDIA_TYPES:
        raise HTTPException(status_code=404, detail="文件不存在")
    return StreamingResponse(
        tempo_stream(target, speed),
        media_type="audio/mpeg",
        # 同一条音频在不同倍率下字节不同，别让任何一层缓存拿旧倍率的结果凑数。
        headers={"Cache-Control": "no-store"},
    )


# ---------------------------------------------------------------- standalone tools


def _copy_upload(file: UploadFile, target: Path, label: str = "音频") -> Path:
    """把上传文件按块写到 ``target``，边写边计数。

    先 ``read()`` 全量进内存再判长度，等于让一个请求最多占住 80MB；音频转换接口
    一次收多个文件时还会叠加。这里超限立刻中断并删掉半成品。
    """
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    try:
        with target.open("wb") as sink:
            while True:
                chunk = file.file.read(UPLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=400, detail=f"{label}超过 80MB 限制")
                sink.write(chunk)
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    if written == 0:
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"{label}内容为空")
    return target


def _save_upload(file: UploadFile, directory: Path) -> Path:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_UPLOAD_SUFFIXES:
        raise HTTPException(status_code=400, detail="仅支持 WAV/MP3/FLAC/OGG/M4A/AAC/WMA 音频")
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{uuid.uuid4().hex}{suffix}"
    return _copy_upload(file, target)


@app.post("/api/tools/design")
def tool_design(payload: DesignPayload) -> dict[str, Any]:
    try:
        mp3_path = service.design_sample(payload.text, payload.language, payload.prompt)
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        raise as_http_error(exc) from exc
    # design_id 由后端生成并用作文件名：前端点「入库」时原样回传，
    # 后端据此定位**用户刚试听的这一段**，不用重新生成（重跑会换一个 seed）。
    return json_safe({
        "design_id": Path(mp3_path).stem,
        "output_path": mp3_path,
        "url": file_url(mp3_path),
    })


DESIGN_ROOT = TEMP_DIR / "standalone" / "voicedesign"
# 与转换产物同理：这是"由前端指定后端该读哪个文件"的第二处接口，所以也把标识卡死成
# uuid 形态（不含路径分隔符），再加 relative_to 兜底，拒绝 ../ 逃出设计产物目录。
_DESIGN_ID = re.compile(r"^[0-9a-fA-F-]{36}$")


def _resolve_design_audio(design_id: str) -> Path:
    clean = (design_id or "").strip()
    if not _DESIGN_ID.match(clean):
        raise HTTPException(status_code=400, detail=f"非法的设计产物：{design_id}")
    root = DESIGN_ROOT.resolve()
    # 优先取中间 wav：固化要拿它原样做 reference.wav，mp3 只是试听件。
    for candidate in (root / f"{clean}.wav", root / f"{clean}.mp3"):
        try:
            candidate.resolve().relative_to(root)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"非法的设计产物：{design_id}") from None
        if candidate.is_file():
            return candidate
    raise HTTPException(status_code=400, detail="设计产物不存在或已过期，请重新生成后再入库")


@app.post("/api/voices/design-archive")
def archive_design(payload: DesignArchivePayload) -> dict[str, Any]:
    """把一段 VoiceDesign 试听固化成公共声线（无参考音频，只有设计描述）。"""
    audio = _resolve_design_audio(payload.design_id)
    try:
        voice_id, preview = service.archive_designed_voice(
            payload.name, payload.prompt, payload.language, payload.text,
            str(audio), payload.tags, payload.notes,
        )
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        raise as_http_error(exc) from exc
    voice = service.get_voice(voice_id)
    return json_safe({
        "voice_id": voice_id,
        "preview_path": preview,
        "preview_url": file_url(preview),
        "voice": json_safe(enrich_voices([voice])[0]) if voice else None,
    })


@app.post("/api/tools/clone")
def tool_clone(
    file: UploadFile = File(...),
    text: str = Form(""),
    language: str = Form("Chinese"),
    ref_text: str = Form(""),
    x_vector_only: bool = Form(False),
    preview_text: str = Form(""),
) -> dict[str, Any]:
    """一次性克隆试听（不写库）。

    要朗读的文本同时接受 ``text`` 与 ``preview_text`` 两个字段名：「上传声线」页的
    两个按钮共用一份表单，表单按 ``/api/voices/upload`` 的叫法发 ``preview_text``。
    这里两个都收（text 优先），否则同一个表单只因为字段名不同就会 422。
    """
    spoken = text.strip() or preview_text.strip()
    source = _save_upload(file, TEMP_DIR / "uploads")
    try:
        mp3_path = service.clone_sample(
            str(source), ref_text, bool(x_vector_only), spoken, language
        )
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        raise as_http_error(exc) from exc
    finally:
        source.unlink(missing_ok=True)
    return json_safe({"output_path": mp3_path, "url": file_url(mp3_path)})


def _rename_with_originals(converted: list[str], originals: list[str]) -> list[str]:
    """把转换产物改回客户端原始文件名，同名时加 (2)(3) 后缀。

    上传落盘用的是随机 hex 名，而 `convert_files` 按"源文件 stem"给产物命名，
    直接沿用会让用户拿到一串随机名——单文件下载还能忍，打包成 ZIP 就完全不可用了。
    """
    finalized: list[str] = []
    used: set[str] = set()
    for path, original in zip(converted, originals):
        target = Path(path)
        stem = safe_filename(original, target.stem) if original else target.stem
        candidate, index = stem, 2
        while candidate.lower() in used:
            candidate, index = f"{stem}({index})", index + 1
        used.add(candidate.lower())
        renamed = target.with_name(f"{candidate}{target.suffix}")
        if renamed != target:
            renamed.unlink(missing_ok=True)
            target.replace(renamed)
        finalized.append(str(renamed))
    return finalized


@app.post("/api/tools/convert")
def tool_convert(
    files: list[UploadFile] = File(...),
    target: str = Form("MP3 (128kbps)"),
) -> dict[str, Any]:
    upload_dir = TEMP_DIR / "uploads" / uuid.uuid4().hex
    saved: list[Path] = []
    # 客户端原始文件名只在这里拿得到（_save_upload 落盘时已换成随机名），先记下来。
    originals = [Path(file.filename or "").stem for file in files]
    try:
        for file in files:
            saved.append(_save_upload(file, upload_dir))
        try:
            converted = _rename_with_originals(
                service.convert_files([str(path) for path in saved], target), originals,
            )
        except (ValueError, RuntimeError, FileNotFoundError) as exc:
            raise as_http_error(exc) from exc
    finally:
        for path in saved:
            path.unlink(missing_ok=True)
        if upload_dir.exists():
            upload_dir.rmdir()
    # conversion_id 就是产物所在目录名（db.new_id() 生成的 uuid4）。前端逐文件发起
    # 转换时靠它把各次结果归拢，最后一次性交给 conversions/archive 打包下载。
    conversion_id = Path(converted[0]).parent.name if converted else ""
    return json_safe({
        "conversion_id": conversion_id,
        "files": [{"path": path, "url": file_url(path), "name": Path(path).name} for path in converted]
    })


CONVERSION_ROOT = TEMP_DIR / "conversions"
# 这是唯一一处"由前端指定后端该读哪个文件"的接口，所以前缀卡死成 uuid 形态、文件名禁止
# 任何路径分隔符：正则 + relative_to 双保险，拒绝 ../ 逃出转换目录。
_CONVERSION_ITEM = re.compile(r"^([0-9a-fA-F-]{36})/([^/\\]+\.(?:mp3|wav))$")


@app.post("/api/tools/conversions/archive")
def archive_conversions(payload: ConversionArchivePayload) -> StreamingResponse:
    """把若干转换产物打包成 ZIP 流式返回；后端那台电脑上不留这份交付文件。"""
    root = CONVERSION_ROOT.resolve()
    entries: list[tuple[str, str]] = []
    for item in payload.items:
        match = _CONVERSION_ITEM.match(item.strip())
        if not match:
            raise HTTPException(status_code=400, detail=f"非法的转换产物：{item}")
        target = (root / item.strip()).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"非法的转换产物：{item}") from None
        if not target.is_file():
            raise HTTPException(status_code=400, detail=f"转换产物不存在或已过期：{item}")
        entries.append((match.group(2), str(target)))
    return StreamingResponse(
        service.exports.iter_file_archive(entries),
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                "attachment; filename=\"conversions.zip\"; "
                f"filename*=UTF-8''{quote('音频转换结果.zip')}"
            )
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Qwen TTS Studio API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7871)
    args = parser.parse_args()
    # 启动时后台回收过期临时文件（temp/、quick/），不阻塞监听。
    from .cleanup import start_background_cleanup
    start_background_cleanup()
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
