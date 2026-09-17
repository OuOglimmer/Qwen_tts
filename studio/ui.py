from __future__ import annotations

import html
import json
import os
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import gradio as gr

from .audio import wav_to_mp3, write_wav
from .config import LANGUAGES, ROOT, TEMP_DIR
from .database import Database
from .model_manager import ModelManager
from .services import StudioService


os.environ["NO_PROXY"] = "127.0.0.1,localhost,::1," + os.environ.get("NO_PROXY", "")
os.environ["no_proxy"] = os.environ["NO_PROXY"]

database = Database()
models = ModelManager()
service = StudioService(database, models)

STUDIO_THEME = gr.themes.Soft(
    font=[gr.themes.GoogleFont("Source Sans Pro"), "Arial", "sans-serif"]
)
STUDIO_CSS = """
.gradio-container {max-width: none !important;}
.workbench-shell {
    align-items: flex-start !important;
    flex-wrap: nowrap !important;
    gap: 16px !important;
}
.global-sidebar {
    position: sticky;
    top: 12px;
    align-self: flex-start;
    width: 250px;
    min-width: 240px;
    max-width: 270px;
    flex: 0 0 250px !important;
    padding: 14px;
    margin: 0 16px 16px 0;
    border: 1px solid var(--border-color-primary);
    border-radius: 12px;
    background: var(--background-fill-secondary);
}
.global-sidebar .sidebar-title {font-weight: 700; font-size: 18px; margin-bottom: 8px;}
.global-sidebar .sidebar-section {margin-top: 16px; font-size: 12px; font-weight: 700; color: var(--body-text-color-subdued); text-transform: uppercase; letter-spacing: .04em;}
.global-sidebar .sidebar-nav a {display: block; padding: 7px 9px; border-radius: 7px; color: var(--body-text-color); text-decoration: none; border-left: 3px solid transparent; cursor: pointer;}
.global-sidebar .sidebar-nav a:hover, .global-sidebar .sidebar-nav a.active {background: var(--background-fill-primary); border-left-color: var(--primary-500);}
.sidebar-summary {line-height: 1.65; color: var(--body-text-color-subdued); font-size: 13px;}
.main-workbench {flex: 1 1 auto !important; min-width: 0 !important;}
.studio-hero {padding: 4px 2px 10px 2px;}
.studio-muted {color: var(--body-text-color-subdued);}
.studio-native-audio {width: 100%; min-height: 42px;}
.audio-grid-row {
    display: grid !important;
    grid-template-columns: repeat(4, minmax(180px, 1fr)) !important;
    gap: var(--spacing-lg);
    overflow-x: auto;
    align-items: stretch !important;
}
.audio-grid-row > .candidate-card {
    width: 100% !important;
    min-width: 0 !important;
}
.candidate-card {
    border: 1px solid var(--border-color-primary);
    border-radius: 12px;
    padding: 8px;
    height: 360px;
    min-height: 360px;
    display: flex !important;
    flex-direction: column;
    overflow: visible !important;
}
.candidate-card > .voice-audio-player {
    flex: 1 1 auto !important;
    min-height: 220px;
    max-height: 280px;
    overflow: hidden;
}
.candidate-actions {
    display: grid !important;
    grid-template-columns: repeat(3, minmax(0, 1fr)) !important;
    gap: 6px !important;
    flex: 0 0 auto !important;
    margin-top: 8px !important;
    position: relative;
    z-index: 3;
}
.candidate-actions button {
    width: 100% !important;
    min-width: 0 !important;
    padding-left: 4px !important;
    padding-right: 4px !important;
    white-space: nowrap;
}
.shortlist-heading {
    opacity: .72;
    margin-top: 14px !important;
}
.shortlist-card {
    opacity: .84;
    border-color: var(--border-color-secondary);
    background: var(--background-fill-secondary);
    box-shadow: none !important;
}
.shortlist-card:hover {opacity: .96;}
.batch-result-card {
    border: 1px solid var(--border-color-primary);
    border-radius: 12px;
    padding: 12px;
    margin-bottom: 10px;
}
.batch-result-card audio {max-height: 90px;}
.sidebar-nav-button {width: 100%; text-align: left; margin: 2px 0; min-height: 32px; padding: 5px 9px !important;}
.block:has(> button.label-wrap) {
    border: 1px solid var(--border-color-primary) !important;
    border-left: 5px solid var(--primary-400) !important;
    border-radius: 12px !important;
    background: var(--background-fill-secondary) !important;
    box-shadow: 0 2px 8px color-mix(in srgb, var(--body-text-color) 9%, transparent);
    margin: 10px 0 !important;
    overflow: hidden !important;
}
.block:has(> button.label-wrap) > button.label-wrap {
    min-height: 48px;
    width: 100%;
    padding: 11px 14px !important;
    cursor: pointer !important;
    font-size: 15px !important;
    font-weight: 700 !important;
    color: var(--body-text-color) !important;
    background: transparent !important;
}
.block:has(> button.label-wrap) > button.label-wrap:hover {
    color: var(--primary-600) !important;
    background: color-mix(in srgb, var(--primary-100) 55%, transparent) !important;
}
.block:has(> button.label-wrap) > button.label-wrap::after {
    content: "点击展开";
    margin-left: auto;
    margin-right: 8px;
    padding: 3px 9px;
    border-radius: 999px;
    color: var(--primary-700);
    background: var(--primary-100);
    font-size: 12px;
    font-weight: 700;
}
.block:has(> [data-testid="accordion-content"]:not([style*="display: none"])) {
    border-left-color: var(--primary-600) !important;
    background: var(--background-fill-primary) !important;
}
.block:has(> [data-testid="accordion-content"]:not([style*="display: none"])) > button.label-wrap::after {
    content: "点击收起";
    color: white;
    background: var(--primary-600);
}
.block:has(> button.label-wrap) > [data-testid="accordion-content"] {
    border-top: 1px solid var(--border-color-secondary);
    padding: 10px 12px 12px 12px;
}
.workflow-compact-note {margin: 2px 0 8px 0 !important;}
.block.workflow-step-banner {
    margin-top: 30px !important;
    margin-bottom: 14px !important;
    padding: 16px 18px !important;
    border: 1px solid var(--border-color-primary);
    border-left: 6px solid var(--primary-500);
    border-radius: 12px;
    background: var(--background-fill-secondary);
    box-shadow: var(--shadow-drop-lg);
}
.block.workflow-step-banner[id] {scroll-margin-top: 92px;}
.block.workflow-step-banner h2 {margin: 0 0 4px 0 !important;}
.block.workflow-step-banner p {margin: 0 !important; color: var(--body-text-color-subdued);}
"""


def exclusive_audio_js(current_elem_id: str) -> str:
    """Start every play action at zero and pause every other player."""
    current_id = json.dumps(current_elem_id)
    return f"""
() => {{
    const current = document.getElementById({current_id});
    const resetCurrentAudio = () => {{
        const waveformHost = current?.querySelector('#waveform > div');
        const currentAudio = waveformHost?.shadowRoot?.querySelector('audio')
            ?? current?.querySelector('audio');
        if (currentAudio) currentAudio.currentTime = 0;
    }};
    resetCurrentAudio();
    requestAnimationFrame(() => {{
        resetCurrentAudio();
        requestAnimationFrame(resetCurrentAudio);
    }});
    setTimeout(resetCurrentAudio, 60);
    document.querySelectorAll('button.play-pause-button[aria-label="暂停"]').forEach((button) => {{
        if (!current || !current.contains(button)) button.click();
    }});
    document.querySelectorAll('audio.studio-native-audio').forEach((audio) => audio.pause());
}}
"""


def native_history_audio(path: str, elem_id: str, version: str = "") -> str:
    file_url = f"/gradio_api/file={quote(str(Path(path).resolve()), safe='')}"
    if version:
        file_url += f"?v={quote(str(version), safe='')}"
    on_play = (
        "this.currentTime=0;"
        "document.querySelectorAll('audio.studio-native-audio').forEach((audio)=>{"
        "if(audio!==this)audio.pause();});"
        "document.querySelectorAll('button.play-pause-button[aria-label=\"暂停\"]')"
        ".forEach((button)=>button.click());"
    )
    return (
        f'<audio id="{html.escape(elem_id, quote=True)}" '
        'class="studio-native-audio" controls preload="none" '
        f'src="{html.escape(file_url, quote=True)}" '
        f'onplay="{html.escape(on_play, quote=True)}"></audio>'
    )


def project_dropdown(value: str | None = None, label: str = "当前项目") -> gr.Dropdown:
    choices = service.project_choices()
    selected = value or (choices[0][1] if choices else None)
    return gr.Dropdown(label=label, choices=choices, value=selected)


def voice_dropdown(project_id: str | None = None, value: str | None = None) -> gr.Dropdown:
    choices = service.voice_choices(project_id)
    selected = value if value and any(item[1] == value for item in choices) else (choices[0][1] if choices else None)
    return gr.Dropdown(choices=choices, value=selected, filterable=False)


def batch_voice_selector(project_id: str | None = None, value: str | None = None) -> gr.Dropdown:
    choices = service.voice_choices(project_id)
    selected = value if value and any(item[1] == value for item in choices) else (choices[0][1] if choices else None)
    return gr.Dropdown(choices=choices, value=selected, filterable=True)


def batch_regen_voice_choices(project_id: str | None = None) -> list[tuple[str, str]]:
    if not project_id:
        return []
    rows = database.fetch_all(
        """
        SELECT v.id,v.name,COUNT(*) AS task_count,MIN(b.position) AS first_position
        FROM batch_items b JOIN voices v ON v.id=b.voice_id
        WHERE b.project_id=? AND b.status!='deleted' AND b.output_mp3 IS NOT NULL
        GROUP BY v.id,v.name
        ORDER BY first_position
        """,
        (project_id,),
    )
    return [(f"{row['name']} · {int(row['task_count'])} 条", row["id"]) for row in rows]


def batch_regen_voice_selector(project_id: str | None = None, value: str | None = None) -> gr.Dropdown:
    choices = batch_regen_voice_choices(project_id)
    selected = value if value and any(item[1] == value for item in choices) else (choices[0][1] if choices else None)
    return gr.Dropdown(choices=choices, value=selected, filterable=True)


def create_project(name: str):
    try:
        project_id = service.create_project(name)
        return (
            project_dropdown(project_id, "当前项目"),
            project_dropdown(project_id, "筛选/生成项目"),
            project_dropdown(project_id, "归属项目"),
            project_rows(project_id),
            project_summary_text(project_id),
            f"项目“{name.strip()}”已就绪，声线和任务将独立保存。",
        )
    except Exception as exc:
        return (
            project_dropdown(label="当前项目"),
            project_dropdown(label="筛选/生成项目"),
            project_dropdown(label="归属项目"),
            project_rows(),
            project_summary_text(),
            f"{type(exc).__name__}: {exc}",
        )


def refresh_projects(current_project=None):
    # Keep the currently active project selected when refreshing the project list.
    return (
        project_dropdown(current_project, "当前项目"),
        project_dropdown(current_project, "筛选/生成项目"),
        project_dropdown(current_project, "归属项目"),
        project_rows(current_project),
    )


def refresh_projects_on_load():
    """Reload project choices from the database whenever a browser session opens.

    Gradio can reuse a server-side Blocks configuration across browser refreshes,
    so choices captured during the original process startup may be stale after a
    project is created. Resolve the selection from the persisted setting and
    rebuild all project dropdowns on every page load.
    """
    choices = service.project_choices()
    saved_project = database.get_setting("last_project_id")
    valid_ids = {item[1] for item in choices}
    selected = saved_project if saved_project in valid_ids else (choices[0][1] if choices else None)
    if selected and selected != saved_project:
        database.set_setting("last_project_id", selected)
    return (
        project_dropdown(selected, "当前项目"),
        project_dropdown(selected, "当前项目（资产筛选）"),
        project_dropdown(selected, "当前项目（上传归属）"),
        project_rows(selected),
    )


def delete_project_action(project_id, confirmation):
    try:
        project = database.fetch_one("SELECT name FROM projects WHERE id=?", (project_id,)) if project_id else None
        if not project:
            raise ValueError("请先选择要删除的项目")
        if str(confirmation or "").strip() != project["name"]:
            raise ValueError(f"请输入完整项目名称“{project['name']}”以确认删除")
        deleted_name = service.delete_project(project_id)
        remaining = service.project_choices()
        next_project = remaining[0][1] if remaining else None
        return (
            project_dropdown(next_project, "当前项目"),
            project_rows(next_project),
            f"已永久删除项目“{deleted_name}”及其全部数据和项目文件。",
            "",
        )
    except Exception as exc:
        return project_dropdown(project_id, "当前项目"), project_rows(project_id), f"{type(exc).__name__}: {exc}", confirmation or ""


def sync_project_contexts(project_id: str | None):
    return (
        project_dropdown(project_id, "当前项目（资产筛选）"),
        project_dropdown(project_id, "当前项目（上传归属）"),
        project_rows(project_id),
        library_rows(project_id),
        voice_dropdown(project_id),
        batch_voice_selector(project_id),
    )


def project_rows(current_project: str | None = None) -> list[list[Any]]:
    return [[
        "✓" if row["id"] == current_project else "",
        row["name"],
        row["slot_count"],
        row["voice_count"],
        row["single_count"],
        row["task_count"],
        row["completed_count"],
        display_time(row["last_activity"]),
        row["id"][:8],
    ] for row in service.project_summaries()]


def project_summary_text(project_id: str | None = None) -> str:
    """Compact read-only project summary for the persistent sidebar."""
    if not project_id:
        return "暂无当前项目"
    row = next((item for item in service.project_summaries() if item["id"] == project_id), None)
    if not row:
        return "项目不存在"
    return (
        f"{row['slot_count']} 个角色<br>"
        f"{row['voice_count']} 个已固化<br>"
        f"{row['task_count']} 条任务<br>"
        f"{row['completed_count']} 条已完成"
    )


VIEW_NAMES = ("voice", "dubbing", "results", "assets", "upload", "projects", "design", "clone", "audio", "system")


def switch_view(target: str):
    """Pure UI routing: show exactly one page container, without touching data."""
    if target not in VIEW_NAMES:
        target = "voice"
    return [gr.update(visible=name == target) for name in VIEW_NAMES]


REQUEST_STATUS = {
    "draft": "待填写",
    "ready": "可生成",
    "candidate": "待选择",
    "selected": "已选择",
    "fixed": "已固化",
    "single": "单句成品",
}


def request_rows(project_id: str | None) -> list[list[Any]]:
    if not project_id:
        return []
    return [[
        index,
        row["name"],
        REQUEST_STATUS.get(row["status"], row["status"])
        + (" · 单句成品" if row["direct_output_mp3"] and row["status"] != "single" else ""),
        row["candidate_count"],
        row["shortlisted_count"],
        "✓" if row["selected_candidate_id"] else "",
        row["language"],
        row["updated_at"] or row["created_at"],
        row["id"][:8],
    ] for index, row in enumerate(service.list_voice_requests(project_id), start=1)]


def progress_text(project_id: str | None) -> str:
    if not project_id:
        return "尚未选择项目。"
    progress = service.request_progress(project_id)
    return (
        f"**本次工作表：共 {progress['total']} 个声线｜"
        f"待准备 {progress['draft']}｜待选择 {progress['candidate']}｜"
        f"已选择 {progress['selected']}｜已固化 {progress['fixed']}｜"
        f"单句成品 {progress['single']}**"
    )


def activity_rows(project_id: str | None) -> str:
    if not project_id:
        return "尚未选择项目。"
    rows = service.activity(project_id, limit=25)
    if not rows:
        return "暂无操作记录。"
    lines = []
    for row in rows:
        detail = f"　·　{row['detail']}" if row["detail"] else ""
        lines.append(f"- `{display_time(row['created_at'])}`　**{row['action']}**{detail}")
    return "\n".join(lines)


def bulk_create_requests(project_id, count, prefix, names_text, language):
    try:
        created = service.create_voice_slots(project_id, int(count), prefix, names_text, language)
        return request_rows(project_id), progress_text(project_id), f"已建立 {len(created)} 个声线槽位。点击工作表中的任意一行开始编辑。", activity_rows(project_id)
    except Exception as exc:
        return request_rows(project_id), progress_text(project_id), f"{type(exc).__name__}: {exc}", activity_rows(project_id)


def append_voice_slot(project_id, name, language):
    """Append one named slot without replacing the existing worksheet."""
    try:
        clean_name = str(name or "").strip()
        if not clean_name:
            raise ValueError("请输入要追加的声线名称")
        created = service.create_voice_slots(project_id, 1, "", clean_name, language or "Chinese")
        return (
            request_rows(project_id),
            progress_text(project_id),
            f"已追加声线槽位：{created[0]}。原有工作表顺序保持不变。",
            activity_rows(project_id),
            "",
        )
    except Exception as exc:
        return request_rows(project_id), progress_text(project_id), f"{type(exc).__name__}: {exc}", activity_rows(project_id), name or ""


def load_request(project_id: str, evt: gr.SelectData):
    try:
        row_index = evt.index[0] if isinstance(evt.index, (tuple, list)) else evt.index
        requests = service.list_voice_requests(project_id)
        request = requests[int(row_index)]
        candidates = service.request_candidates(request["id"], 4)
        candidate_ids = [item["id"] for item in candidates]
        previews = [item["preview_path"] for item in candidates]
        while len(candidate_ids) < 4:
            candidate_ids.append(None)
            previews.append(None)
        shortlisted_ids, shortlisted_previews = shortlist_payload(request["id"])
        return (
            request["id"], request["name"], request["prompt"], request["audition_text"],
            request["language"], candidate_ids, *previews, shortlisted_ids, *shortlisted_previews,
            f"正在编辑“{request['name']}” · {REQUEST_STATUS.get(request['status'], request['status'])}",
        )
    except Exception as exc:
        return None, "", "", "", "Chinese", [], None, None, None, None, [], None, None, None, None, f"{type(exc).__name__}: {exc}"


def save_request_draft(request_id, project_id, name, prompt, text, language):
    if not request_id:
        return request_rows(project_id), progress_text(project_id), "尚未选择工作表中的声线；生成候选时会自动创建并保存。"
    try:
        service.save_voice_request(request_id, project_id, name, prompt, text, language)
        return request_rows(project_id), progress_text(project_id), "草稿已自动保存。"
    except Exception as exc:
        return request_rows(project_id), progress_text(project_id), f"{type(exc).__name__}: {exc}"


def delete_voice_slot(project_id: str, request_id: str | None):
    try:
        if not request_id:
            raise ValueError("请先从声线工作表点选要删除的槽位")
        name, preserved_asset = service.delete_voice_request(project_id, request_id)
        asset_note = "；已固化声线资产仍保留在资产库" if preserved_asset else ""
        message = f"已删除声线槽位“{name}”{asset_note}。后续槽位已按原顺序向前补位。"
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
    return (
        request_rows(project_id),
        progress_text(project_id),
        message,
        activity_rows(project_id),
        None,
        "",
        "",
        "",
        "Chinese",
        [],
        None,
        None,
        None,
        None,
        [],
        None,
        None,
        None,
        None,
        "请从工作表选择另一个声线槽位继续编辑。",
        database.new_id(),
    )


def generate_candidates(project_id, request_id, name, prompt, text, language, count):
    try:
        request_id, generated = service.generate_candidates(
            project_id,
            name or "",
            prompt or "",
            text or "",
            language or "Chinese",
            int(count or 4),
            request_id=request_id,
        )
        ids = [item["id"] for item in generated]
        previews = [item["preview_path"] for item in generated]
        while len(ids) < 4:
            ids.append(None)
            previews.append(None)
        status = f"已生成 {len(generated)} 个候选。满意的可先暂存，或直接选定为最终声线。"
        return request_id, ids, *previews, status, request_rows(project_id), progress_text(project_id), activity_rows(project_id)
    except Exception as exc:
        return request_id, [], None, None, None, None, f"{type(exc).__name__}: {exc}", request_rows(project_id), progress_text(project_id), activity_rows(project_id)


def choose_candidate(index: int, project_id: str, request_id: str, candidate_ids: list[str | None]):
    try:
        if not request_id or not candidate_ids or index >= len(candidate_ids) or not candidate_ids[index]:
            raise ValueError("该位置没有可选择的候选")
        service.select_candidate(request_id, candidate_ids[index])
        return f"已选择候选 {index + 1}。可以从工作表切换到下一个声线。", request_rows(project_id), progress_text(project_id), activity_rows(project_id)
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}", request_rows(project_id), progress_text(project_id), activity_rows(project_id)


def keep_single_candidate(index: int, project_id: str, request_id: str, candidate_ids: list[str | None]):
    try:
        if not request_id or not candidate_ids or index >= len(candidate_ids) or not candidate_ids[index]:
            raise ValueError("该位置没有可保留的候选")
        output = service.keep_candidate_as_single_output(request_id, candidate_ids[index])
        return (
            f"候选 {index + 1} 已直接保留为单句成品；原有选择和固化状态保持不变。输出：`{output}`",
            request_rows(project_id),
            progress_text(project_id),
            activity_rows(project_id),
            database.new_id(),
        )
    except Exception as exc:
        return (
            f"{type(exc).__name__}: {exc}",
            request_rows(project_id),
            progress_text(project_id),
            activity_rows(project_id),
            database.new_id(),
        )


def keep_all_selected_single_outputs(project_id: str):
    try:
        names = service.keep_all_selected_as_single_outputs(project_id)
        return (
            f"已把当前项目 {len(names)} 个已选试听保留为单句成品。已固化资产和待固化状态均未改变。",
            request_rows(project_id),
            progress_text(project_id),
            activity_rows(project_id),
            database.new_id(),
        )
    except Exception as exc:
        return (
            f"{type(exc).__name__}: {exc}",
            request_rows(project_id),
            progress_text(project_id),
            activity_rows(project_id),
            database.new_id(),
        )


def single_output_records(project_id: str | None) -> list[dict[str, Any]]:
    if not project_id:
        return []
    return [
        row for row in service.single_line_outputs(project_id)
        if row["direct_output_mp3"] and Path(row["direct_output_mp3"]).exists()
    ]


def delete_single_output(project_id: str, request_id: str):
    try:
        name, _removed_file = service.delete_single_line_output(project_id, request_id)
        return (
            database.new_id(),
            request_rows(project_id),
            progress_text(project_id),
            activity_rows(project_id),
            f"已删除“{name}”的单句成品；候选、已选状态和固化声线资产均已保留。重新导出后不会再包含该音频。",
        )
    except Exception as exc:
        return (
            database.new_id(),
            request_rows(project_id),
            progress_text(project_id),
            activity_rows(project_id),
            f"{type(exc).__name__}: {exc}",
        )


def shortlist_payload(request_id: str | None):
    candidates = service.shortlisted_candidates(request_id, 4) if request_id else []
    ids = [item["id"] for item in candidates]
    previews = [item["preview_path"] for item in candidates]
    while len(ids) < 4:
        ids.append(None)
        previews.append(None)
    return ids, previews


def shortlist_candidate(index, project_id, request_id, candidate_ids):
    try:
        if not request_id or not candidate_ids or index >= len(candidate_ids) or not candidate_ids[index]:
            raise ValueError("该位置没有可暂存的候选")
        service.set_candidate_shortlisted(request_id, candidate_ids[index], True)
        ids, previews = shortlist_payload(request_id)
        return ids, *previews, f"候选 {index + 1} 已加入暂存对比池。", request_rows(project_id), activity_rows(project_id)
    except Exception as exc:
        ids, previews = shortlist_payload(request_id)
        return ids, *previews, f"{type(exc).__name__}: {exc}", request_rows(project_id), activity_rows(project_id)


def remove_shortlisted(index, project_id, request_id, shortlisted_ids):
    try:
        if not request_id or not shortlisted_ids or index >= len(shortlisted_ids) or not shortlisted_ids[index]:
            raise ValueError("该位置没有暂存候选")
        service.set_candidate_shortlisted(request_id, shortlisted_ids[index], False)
        ids, previews = shortlist_payload(request_id)
        return ids, *previews, "已移出暂存对比池。", request_rows(project_id), activity_rows(project_id)
    except Exception as exc:
        ids, previews = shortlist_payload(request_id)
        return ids, *previews, f"{type(exc).__name__}: {exc}", request_rows(project_id), activity_rows(project_id)


def choose_shortlisted(index, project_id, request_id, shortlisted_ids):
    return choose_candidate(index, project_id, request_id, shortlisted_ids)


def fix_selected(project_id: str):
    try:
        names = service.fix_selected_voices(project_id)
        models.unload()
        return f"已固化 {len(names)} 个声线：{', '.join(names)}", batch_voice_selector(project_id), library_rows(project_id), request_rows(project_id), progress_text(project_id), activity_rows(project_id)
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}", batch_voice_selector(project_id), library_rows(project_id), request_rows(project_id), progress_text(project_id), activity_rows(project_id)


def reopen_fixed(project_id: str, request_id: str | None):
    try:
        if not request_id:
            raise ValueError("请先在工作表中选择一个已固化的声线")
        name = service.reopen_fixed_request(request_id)
        models.unload()
        return (
            f"已解冻“{name}”。原固化资产已保留为历史版本；现在可以重新生成候选并再次固化。",
            batch_voice_selector(project_id), library_rows(project_id), request_rows(project_id),
            progress_text(project_id), activity_rows(project_id),
            "当前声线已解冻，可继续生成新候选。",
        )
    except Exception as exc:
        return (
            f"{type(exc).__name__}: {exc}", batch_voice_selector(project_id), library_rows(project_id),
            request_rows(project_id), progress_text(project_id), activity_rows(project_id),
            "解冻失败。",
        )


def library_rows(project_id: str | None, search: str | None = "") -> list[list[Any]]:
    needle = str(search or "").strip().lower()
    voices = service.list_voices(project_id)
    if needle:
        voices = [row for row in voices if needle in " ".join([
            str(row.get("name") or ""),
            str(row.get("source") or ""),
            str(row.get("language") or ""),
            str(row.get("tags") or ""),
            str(row.get("notes") or ""),
        ]).lower()]
    return [[
        row["name"], row["source"], row["language"], row["tags"], row["status"], row["created_at"], row["id"][:8]
    ] for row in voices]


def refresh_library(project_id: str | None = None, search: str | None = ""):
    return library_rows(project_id, search), voice_dropdown(project_id)


def upload_and_archive(project_id, name, audio, ref_text, language, xvec, preview_text, tags, notes):
    try:
        voice_id, preview = service.archive_uploaded_voice(
            project_id, name, audio, ref_text, language, bool(xvec), preview_text, tags, notes
        )
        return (
            preview,
            f"声线“{name.strip()}”已试听、固化并归档（ID {voice_id[:8]}）。"
            "已同步到项目工作流的“绑定声线”选择区，可直接批量生成。",
            library_rows(project_id),
            voice_dropdown(project_id, voice_id),
            batch_voice_selector(project_id, voice_id),
            batch_voice_selector(project_id, voice_id),
        )
    except Exception as exc:
        return (
            None,
            f"{type(exc).__name__}: {exc}",
            library_rows(project_id),
            voice_dropdown(project_id),
            batch_voice_selector(project_id),
            batch_voice_selector(project_id),
        )


def generate_library_voice(project_id: str, voice_id: str, text: str, language: str):
    try:
        if not project_id or not voice_id or not text.strip():
            raise ValueError("请选择声线并填写文本")
        voice = database.fetch_one(
            "SELECT id FROM voices WHERE id=? AND project_id=? AND status='active'",
            (voice_id, project_id),
        )
        if not voice:
            raise ValueError("当前项目不包含该声线，请刷新后重新选择")
        output = TEMP_DIR / "single" / service.db.new_id() / "output"
        mp3 = service.generate_with_voice(voice_id, text, language, output, project_id=project_id)
        return mp3, "生成完成。"
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def delete_library_voice(project_id: str, voice_id: str, confirmation: str):
    try:
        voice = database.fetch_one(
            "SELECT name FROM voices WHERE id=? AND project_id=?",
            (voice_id, project_id),
        )
        if not voice:
            raise ValueError("请选择当前项目中的声线资产")
        if str(confirmation or "").strip() != voice["name"]:
            raise ValueError(f"请输入声线名称“{voice['name']}”确认删除")
        name = service.delete_voice_asset(project_id, voice_id)
        return (
            f"已删除声线资产“{name}”。已有批量音频文件未删除。",
            library_rows(project_id),
            voice_dropdown(project_id),
            batch_voice_selector(project_id),
            batch_voice_selector(project_id),
            "",
        )
    except Exception as exc:
        return (
            f"{type(exc).__name__}: {exc}",
            library_rows(project_id),
            voice_dropdown(project_id, voice_id),
            batch_voice_selector(project_id),
            batch_voice_selector(project_id, voice_id),
            confirmation,
        )


def batch_rows(project_id: str) -> list[list[Any]]:
    if not project_id:
        return []
    return [[
        row["position"], row["voice_name"], row["text"], row["language"], row["status"], row["output_mp3"] or "", row["error"] or ""
    ] for row in service.list_batch_items(project_id)]


def batch_result_records(project_id: str | None) -> list[dict[str, Any]]:
    if not project_id:
        return []
    rows = database.fetch_all(
        """
        SELECT b.id,b.position,b.text,b.language,b.status,b.error,b.output_mp3,b.updated_at,v.name AS voice_name
        FROM batch_items b JOIN voices v ON v.id=b.voice_id
        WHERE b.project_id=? AND b.status!='deleted' AND b.output_mp3 IS NOT NULL
        ORDER BY b.position
        """,
        (project_id,),
    )
    return [
        row for row in rows if row["output_mp3"] and Path(row["output_mp3"]).exists()
    ]


def batch_result_payload(project_id: str | None, item_id: str | None):
    if not project_id or not item_id:
        return None, "当前项目还没有可试听的已完成结果。"
    row = database.fetch_one(
        """
        SELECT b.position,b.text,b.output_mp3,v.name AS voice_name
        FROM batch_items b JOIN voices v ON v.id=b.voice_id
        WHERE b.id=? AND b.project_id=? AND b.status='completed'
        """,
        (item_id, project_id),
    )
    if not row or not row["output_mp3"] or not Path(row["output_mp3"]).exists():
        return None, "该任务的输出文件不存在，请重新生成。"
    return row["output_mp3"], f"**{row['position']:04d} · {row['voice_name']}**\n\n{row['text']}"


def refresh_batch_results(project_id: str | None):
    records = batch_result_records(project_id)
    total = len(records)
    initial = min(8, total)
    return (
        project_id,
        database.new_id(),
        (
            f"正在加载历史结果：**{initial}/{total}**。系统会自动继续加载下一批。"
            if initial < total else f"已完整加载 **{total}/{total}** 条历史结果。"
        ),
        initial,
        gr.Timer(active=initial < total),
    )


def clear_batch_results(project_id: str | None):
    count = len(batch_result_records(project_id))
    return (
        None,
        database.new_id(),
        f"当前项目已有 **{count}** 条历史结果。点击“加载/刷新全部结果”展开试听列表。",
    )


def loading_batch_results(project_id: str | None):
    count = len(batch_result_records(project_id))
    return f"正在加载 **{count}** 条历史结果和音频播放器，请稍候……"


def continue_loading_batch_results(
    project_id: str | None,
    rendered_project_id: str | None,
    current_limit: int | None,
):
    if not project_id or project_id != rendered_project_id:
        return gr.skip(), gr.skip(), gr.skip(), gr.Timer(active=False)
    total = len(batch_result_records(project_id))
    loaded = min(int(current_limit or 0) + 8, total)
    done = loaded >= total
    message = (
        f"已完整加载 **{loaded}/{total}** 条历史结果。"
        if done else f"正在加载历史结果：**{loaded}/{total}**。"
    )
    return loaded, database.new_id(), message, gr.Timer(active=not done)


def select_batch_task(rows, evt: gr.SelectData):
    try:
        values = rows.values.tolist() if hasattr(rows, "values") else (rows or [])
        index = evt.index[0] if isinstance(evt.index, (list, tuple)) else evt.index
        row = values[int(index)]
        position = int(row[0])
        return position, f"已选中第 {position} 条（{row[1]}）。点击“删除选中任务”即可移除。"
    except Exception as exc:
        return None, f"无法选择该任务：{type(exc).__name__}: {exc}"


def delete_selected_batch_task(project_id: str, position: int | None):
    try:
        if position is None:
            raise ValueError("请先在任务表中点选要删除的一行")
        item = service.delete_batch_item(project_id, int(position))
        records = batch_result_records(project_id)
        return (
            batch_rows(project_id),
            f"已删除第 {int(position)} 条任务（{item['voice_name']}）。磁盘上的已有 MP3 文件未删除。",
            database.new_id(),
            f"当前展示 **{len(records)}** 条已有结果。",
            None,
        )
    except Exception as exc:
        records = batch_result_records(project_id)
        return (
            batch_rows(project_id),
            f"{type(exc).__name__}: {exc}",
            database.new_id(),
            f"当前展示 **{len(records)}** 条已有结果。",
            position,
        )


def delete_batch_tasks(project_id: str, positions_text: str):
    try:
        raw = str(positions_text or "").replace("，", ",").replace("、", ",")
        positions: set[int] = set()
        for token in raw.split(","):
            item = token.strip()
            if not item:
                continue
            if "-" in item:
                start_text, end_text = item.split("-", 1)
                start, end = int(start_text.strip()), int(end_text.strip())
                if start <= 0 or end < start or end - start > 5000:
                    raise ValueError("批量序号范围无效（单次最多 5000 条）")
                positions.update(range(start, end + 1))
            else:
                positions.add(int(item))
        deleted = service.delete_batch_items(project_id, sorted(positions))
        records = batch_result_records(project_id)
        return (
            batch_rows(project_id),
            f"已删除 {len(deleted)} 条文本任务；磁盘上的已有音频文件未删除。",
            database.new_id(),
            f"当前展示 **{len(records)}** 条已有结果。",
            "",
        )
    except Exception as exc:
        records = batch_result_records(project_id)
        return (
            batch_rows(project_id),
            f"{type(exc).__name__}: {exc}",
            database.new_id(),
            f"当前展示 **{len(records)}** 条已有结果。",
            positions_text,
        )


def load_batch_result(project_id: str | None, item_id: str | None):
    return batch_result_payload(project_id, item_id)


def regenerate_batch_result(
    project_id: str,
    item_id: str,
    text: str,
    language: str,
    elem_id: str,
):
    try:
        service.regenerate_batch_item(project_id, item_id, text, language)
        models.unload()
        row = database.fetch_one(
            """
            SELECT output_mp3,updated_at FROM batch_items
            WHERE id=? AND project_id=? AND status='completed'
            """,
            (item_id, project_id),
        )
        if not row or not row["output_mp3"] or not Path(row["output_mp3"]).exists():
            raise RuntimeError("生成已完成，但新的音频文件没有找到")
        return (
            native_history_audio(row["output_mp3"], elem_id, row.get("updated_at") or ""),
            f"当前版本：{display_time(row.get('updated_at'))}",
            batch_rows(project_id),
            "✅ 该句已重新生成，播放器已切换到新版本。",
            gr.Button(value="仅重新生成该句", interactive=True),
            "该句已重新生成；对应试听卡片和任务表已更新。",
        )
    except Exception as exc:
        models.unload()
        return (
            gr.skip(),
            gr.skip(),
            batch_rows(project_id),
            f"❌ 生成失败：{type(exc).__name__}: {exc}。原音频仍然保留。",
            gr.Button(value="仅重新生成该句", interactive=True),
            f"{type(exc).__name__}: {exc}",
        )


def regenerate_voice_results(project_id: str, voice_id: str, confirmed: bool):
    if not confirmed:
        yield (
            batch_rows(project_id), gr.skip(), gr.skip(), gr.skip(),
            "请先勾选确认，再开始按人物重新生成。", False,
        )
        return
    if not voice_id:
        yield (
            batch_rows(project_id), gr.skip(), gr.skip(), gr.skip(),
            "请选择要重新生成的人物。", False,
        )
        return

    last_progress = None
    try:
        for progress in service.regenerate_voice_batch(project_id, voice_id):
            last_progress = progress
            if progress["cancelled"]:
                message = (
                    f"已停止‘{progress['voice_name']}’的后续任务：完成 {progress['processed']}/{progress['total']}，"
                    f"成功 {progress['succeeded']}，失败 {progress['failed']}。"
                )
            else:
                message = (
                    f"正在重新生成‘{progress['voice_name']}’：{progress['processed']}/{progress['total']}，"
                    f"当前第 {progress['position']} 条；成功 {progress['succeeded']}，失败 {progress['failed']}。"
                )
                if progress["error"]:
                    message += f" 本句失败：{progress['error']}（原音频已保留）"
            yield batch_rows(project_id), gr.skip(), gr.skip(), gr.skip(), message, gr.skip()

        if not last_progress:
            raise ValueError("该人物没有可重新生成的已有台词")
        records = batch_result_records(project_id)
        final_message = (
            f"‘{last_progress['voice_name']}’处理完成：成功 {last_progress['succeeded']}，"
            f"失败 {last_progress['failed']}；其他人物未改动。"
        )
        if last_progress["cancelled"]:
            final_message = (
                f"‘{last_progress['voice_name']}’已停止：完成 {last_progress['processed']}/"
                f"{last_progress['total']}；成功 {last_progress['succeeded']}，失败 {last_progress['failed']}。"
            )
        yield (
            batch_rows(project_id),
            project_id,
            database.new_id(),
            f"正在更新 **{len(records)}** 条历史结果播放器……",
            final_message,
            False,
        )
    except Exception as exc:
        yield (
            batch_rows(project_id), gr.skip(), gr.skip(), gr.skip(),
            f"{type(exc).__name__}: {exc}", False,
        )
    finally:
        models.unload()


def voice_regeneration_started(project_id: str, voice_id: str, confirmed: bool):
    if not confirmed:
        return "请先勾选确认，再开始按人物重新生成。", gr.Timer(active=False), False
    try:
        progress = service.start_voice_regeneration(project_id, voice_id)
        return (
            f"已启动‘{progress['voice_name']}’重新生成，共 {progress['total']} 条。"
            "页面会自动显示进度；期间仍可加载和试听已有结果。",
            gr.Timer(active=True),
            False,
        )
    except Exception as exc:
        running = service.voice_regeneration_progress()
        keep_polling = bool(running and not running.get("done"))
        return f"{type(exc).__name__}: {exc}", gr.Timer(active=keep_polling), False


def poll_voice_regeneration(project_id: str | None):
    progress = service.voice_regeneration_progress()
    if not progress:
        return (
            gr.skip(), gr.skip(), gr.skip(), gr.skip(),
            "当前没有按人物重新生成任务。", gr.Timer(active=False), gr.skip(),
        )

    name = progress.get("voice_name", "当前人物")
    processed = int(progress.get("processed") or 0)
    total = int(progress.get("total") or 0)
    succeeded = int(progress.get("succeeded") or 0)
    failed = int(progress.get("failed") or 0)
    if not progress.get("done"):
        position = progress.get("position")
        position_text = f"，刚完成第 {position} 条" if position is not None else ""
        message = (
            f"正在重新生成‘{name}’：{processed}/{total}{position_text}；"
            f"成功 {succeeded}，失败 {failed}。"
        )
        return (
            gr.skip(), gr.skip(), gr.skip(), gr.skip(),
            message, gr.Timer(active=True), gr.skip(),
        )

    error = progress.get("error")
    if error:
        message = f"‘{name}’重新生成已停止：{error}；已成功的新版本仍然保留。"
    elif progress.get("cancelled"):
        message = (
            f"‘{name}’已停止：完成 {processed}/{total}；"
            f"成功 {succeeded}，失败 {failed}。"
        )
    else:
        message = f"‘{name}’处理完成：成功 {succeeded}，失败 {failed}；其他人物未改动。"

    if project_id != progress.get("project_id"):
        return (
            gr.skip(), gr.skip(), gr.skip(), gr.skip(),
            message, gr.Timer(active=False), gr.skip(),
        )
    records = batch_result_records(project_id)
    return (
        batch_rows(project_id),
        project_id,
        database.new_id(),
        f"已完整加载 **{len(records)}** 条历史结果。",
        message,
        gr.Timer(active=False),
        False,
    )


def regeneration_started(position: int, voice_name: str):
    return (
        f"⏳ 正在生成第 {int(position)} 条（{voice_name}）；原音频仍然保留，请稍候……",
        gr.Button(value="生成中…", interactive=False),
    )


def display_time(value: str | None) -> str:
    if not value:
        return "未知"
    try:
        return datetime.fromisoformat(value).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return value


def default_batch_export_dir(project_id: str | None) -> str:
    if not project_id:
        return str((ROOT / "outputs").resolve())
    saved = database.get_setting(f"batch_export_dir:{project_id}")
    if saved:
        return saved
    project = database.fetch_one("SELECT name FROM projects WHERE id=?", (project_id,))
    folder = project["name"] if project else project_id[:8]
    return str((ROOT / "outputs" / folder).resolve())


def default_batch_excerpt_length(project_id: str | None) -> int:
    if not project_id:
        return 8
    saved = database.get_setting(f"batch_export_excerpt_length:{project_id}", "8")
    try:
        return max(5, min(int(saved or 8), 10))
    except ValueError:
        return 8


def load_batch_export(project_id: str | None):
    return default_batch_export_dir(project_id), default_batch_excerpt_length(project_id), None, ""


def export_batch_results(project_id: str, destination: str, excerpt_length: int):
    try:
        length = max(5, min(int(excerpt_length), 10))
        output_dir, count = service.export_completed_batch(project_id, destination, length)
        database.set_setting(f"batch_export_excerpt_length:{project_id}", str(length))
        return output_dir, f"已导出 {count} 条成品（包含批量结果和单句成品）到：`{output_dir}`"
    except Exception as exc:
        return destination, f"{type(exc).__name__}: {exc}"


def archive_batch_results(project_id: str, excerpt_length: int):
    try:
        length = max(5, min(int(excerpt_length), 10))
        archive, count = service.create_batch_archive(project_id, length)
        database.set_setting(f"batch_export_excerpt_length:{project_id}", str(length))
        return archive, f"已打包 {count} 条成品（包含批量结果和单句成品）；点击右侧文件下载 ZIP。"
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def open_batch_export_dir(destination: str):
    try:
        path = Path(destination.strip()).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(path)  # type: ignore[attr-defined]
        return f"已打开：`{path}`"
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"


def add_batch(project_id, voice_id, language, text):
    try:
        count = service.add_batch_lines(project_id, voice_id, language, text)
        voice = database.fetch_one("SELECT name FROM voices WHERE id=?", (voice_id,))
        total = database.fetch_one(
            "SELECT COUNT(*) AS total FROM batch_items WHERE project_id=?", (project_id,)
        )
        voice_name = voice["name"] if voice else voice_id[:8]
        return (
            batch_rows(project_id),
            f"已成功绑定“{voice_name}”并加入 {count} 条台词；当前任务表共 {total['total']} 条。",
            "",
        )
    except Exception as exc:
        return batch_rows(project_id), f"{type(exc).__name__}: {exc}", text


def refresh_batch(project_id):
    total = database.fetch_one(
        "SELECT COUNT(*) AS total FROM batch_items WHERE project_id=?", (project_id,)
    ) if project_id else {"total": 0}
    return batch_rows(project_id), batch_voice_selector(project_id), f"任务表已刷新，共 {total['total']} 条。"


def switch_project(project_id):
    service.switch_project_context(project_id)
    return (
        batch_rows(project_id),
        batch_voice_selector(project_id),
        request_rows(project_id),
        progress_text(project_id),
        activity_rows(project_id),
        None, "", "", "", "Chinese",
        [], None, None, None, None,
        [], None, None, None, None,
        "已切换项目。请从本项目工作表选择一个声线开始编辑。",
        batch_regen_voice_selector(project_id),
    )


def clear_batch(project_id):
    service.clear_pending_batch(project_id)
    return batch_rows(project_id), "已清除等待中和失败的任务；已完成文件未删除。"


def save_batch_preview(project_id, rows):
    try:
        values = rows.values.tolist() if hasattr(rows, "values") else rows
        count = service.save_batch_edits(project_id, values or [])
        return batch_rows(project_id), f"已保存 {count} 条等待中/失败任务的文本与语言修改。"
    except Exception as exc:
        return batch_rows(project_id), f"{type(exc).__name__}: {exc}"


def cancel_batch(project_id):
    service.cancel_running_batch(project_id)
    return batch_rows(project_id), "已请求取消；未完成条目已恢复到等待状态。"


def run_batch(project_id):
    try:
        result = service.run_batch(project_id)
        models.unload()
        return (
            batch_rows(project_id),
            f"批量任务完成：成功 {result['succeeded']}，失败 {result['failed']}。全部结果已在下方展开。",
            project_id,
            database.new_id(),
            f"当前展示 **{len(batch_result_records(project_id))}** 条已完成结果。",
        )
    except Exception as exc:
        models.unload()
        records = batch_result_records(project_id)
        return (
            batch_rows(project_id), f"{type(exc).__name__}: {exc}", project_id, database.new_id(),
            f"当前展示 **{len(records)}** 条已有结果。",
        )


def standalone_design(text, language, prompt):
    try:
        seed = service._random.randint(1, 2_147_483_647)
        waveform, sample_rate = models.design(text.strip(), language, prompt.strip(), seed)
        target = TEMP_DIR / "standalone" / service.db.new_id()
        wav = write_wav(target / "voice_design.wav", waveform, sample_rate)
        mp3 = wav_to_mp3(wav, target / "voice_design.mp3")
        return str(mp3), f"生成完成 · seed {seed}"
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def standalone_clone(ref_audio, ref_text, xvec, text, language):
    try:
        if not ref_audio or not text.strip():
            raise ValueError("参考音频和目标文本不能为空")
        if not xvec and not ref_text.strip():
            raise ValueError("非仅声纹模式必须填写参考音频对应文字")
        waveform, sample_rate = models.clone(
            text.strip(), language,
            ref_audio=ref_audio,
            ref_text=ref_text.strip() or None,
            x_vector_only=bool(xvec),
        )
        target = TEMP_DIR / "standalone" / service.db.new_id()
        wav = write_wav(target / "voice_clone.wav", waveform, sample_rate)
        mp3 = wav_to_mp3(wav, target / "voice_clone.mp3")
        return str(mp3), "生成完成。"
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def convert_audio(files, target):
    try:
        paths = [str(item) for item in (files or [])]
        outputs = service.convert_files(paths, target)
        return outputs, f"已转换 {len(outputs)} 个文件。"
    except Exception as exc:
        return [], f"{type(exc).__name__}: {exc}"


def system_status():
    status = models.status()
    return (
        f"当前模型：{status['model']}\n"
        f"CUDA：{'可用' if status['cuda'] else '不可用'}\n"
        f"GPU：{status['gpu']}\n"
        f"本进程显存：{status['allocated_gib']:.2f} GiB"
    )


def build_app() -> gr.Blocks:
    projects = service.project_choices()
    saved_project = database.get_setting("last_project_id")
    valid_project_ids = {item[1] for item in projects}
    default_project = saved_project if saved_project in valid_project_ids else (projects[0][1] if projects else None)
    if default_project and saved_project != default_project:
        database.set_setting("last_project_id", default_project)
    initial_batch_results = batch_result_records(default_project)
    initial_export_dir = default_batch_export_dir(default_project)
    initial_excerpt_length = default_batch_excerpt_length(default_project)

    with gr.Blocks(title="Qwen3 TTS 声线工作台") as app:
        layout_stack = ExitStack()
        workbench_shell = layout_stack.enter_context(gr.Row(elem_classes=["workbench-shell"]))
        with gr.Column(
            scale=0,
            min_width=240,
            elem_id="global-sidebar",
            elem_classes=["global-sidebar"],
        ):
            gr.Markdown("Qwen TTS Studio", elem_classes=["sidebar-title"])
            project_select = gr.Dropdown(label="当前项目", choices=projects, value=default_project)
            sidebar_project_summary = gr.HTML(project_summary_text(default_project), elem_classes=["sidebar-summary"])
            gr.Markdown("工作台", elem_classes=["sidebar-section"])
            voice_nav = gr.Button("声线", elem_classes=["sidebar-nav-button"])
            dubbing_nav = gr.Button("配音", elem_classes=["sidebar-nav-button"])
            results_nav = gr.Button("成品", elem_classes=["sidebar-nav-button"])
            gr.Markdown("资产", elem_classes=["sidebar-section"])
            assets_nav = gr.Button("声线资产", elem_classes=["sidebar-nav-button"])
            upload_nav = gr.Button("上传声线", elem_classes=["sidebar-nav-button"])
            gr.Markdown("项目", elem_classes=["sidebar-section"])
            projects_nav = gr.Button("项目管理", elem_classes=["sidebar-nav-button"])
            gr.Markdown("工具", elem_classes=["sidebar-section"])
            design_nav = gr.Button("VoiceDesign", elem_classes=["sidebar-nav-button"])
            clone_nav = gr.Button("VoiceClone", elem_classes=["sidebar-nav-button"])
            audio_nav = gr.Button("音频转换", elem_classes=["sidebar-nav-button"])
            gr.Markdown("系统", elem_classes=["sidebar-section"])
            system_nav = gr.Button("系统状态", elem_classes=["sidebar-nav-button"])
            sidebar_view_status = gr.Markdown("当前页面：声线", elem_classes=["sidebar-view-status"])

        main_workbench = layout_stack.enter_context(gr.Column(scale=1, min_width=0, elem_classes=["main-workbench"]))
        with gr.Column():
            with gr.Column(visible=True, elem_id="page-voice") as voice_page:
                project_status = gr.Markdown()
                with gr.Accordion("📁 项目管理与项目列表", open=False):
                    with gr.Row():
                        new_project = gr.Textbox(label="新项目名称", placeholder="例如：有声书第一章", scale=5)
                        create_project_btn = gr.Button("新建项目", variant="secondary", scale=1)
                    gr.Markdown(
                        "### 项目列表（仅查看与管理）\n"
                        "项目切换请使用左侧‘当前项目’；每个项目的资产和任务彼此隔离。"
                    )
                    refresh_project_btn = gr.Button("刷新项目列表")
                    project_table = gr.Dataframe(
                        headers=[
                            "当前", "项目名称", "声线槽位", "可用固化声线", "单句成品",
                            "批量任务", "已完成", "最近活动", "短 ID",
                        ],
                        value=project_rows(default_project),
                        interactive=False,
                        wrap=True,
                        elem_id="project-list-table",
                    )
                    gr.Markdown("⚠️ 删除项目会永久移除该项目的工作表、候选、声线、任务、单句成品和项目文件，其他项目不受影响。")
                    with gr.Row(elem_classes=["danger-project-row"]):
                        delete_project_confirmation = gr.Textbox(
                            label="输入当前项目名称以确认删除",
                            placeholder="例如：星月远航2050-第一部",
                            scale=4,
                        )
                        delete_project_btn = gr.Button("永久删除当前项目", variant="stop", scale=1)

                gr.Markdown(
                    "## 0 · 建立本项目声线工作表\n"
                    "先确定本项目需要哪些人物声线；工作表会持续保存每个槽位的候选、选择和固化进度。",
                    elem_id="workflow-step-0",
                    elem_classes=["workflow-step-banner"],
                )
                worksheet_progress = gr.Markdown(progress_text(default_project))
                with gr.Accordion("🧩 编辑工作表：批量建槽、命名与默认语言", open=False):
                    with gr.Row():
                        slot_count = gr.Slider(label="批量创建个数", minimum=1, maximum=50, step=1, value=5)
                        slot_prefix = gr.Textbox(label="自动命名前缀", value="声线")
                        slot_language = gr.Dropdown(label="默认语言", choices=LANGUAGES, value="Chinese")
                    slot_names = gr.Textbox(
                        label="可选：直接粘贴声线名称（每行一个；填写后忽略上面的个数）",
                        lines=3,
                        placeholder="温柔旁白女声\n少年男声\n反派中年男声",
                    )
                    create_slots_btn = gr.Button("建立本次声线工作表", variant="primary")
                with gr.Row():
                    save_draft_btn = gr.Button("保存当前声线草稿")
                    delete_voice_slot_btn = gr.Button("删除当前声线槽位", variant="stop")
                with gr.Row(elem_classes=["append-slot-row"]):
                    append_slot_name = gr.Textbox(
                        label="追加一个声线槽位",
                        placeholder="例如：系统",
                        scale=4,
                    )
                    append_slot_btn = gr.Button("追加到当前工作表", variant="secondary", scale=1)
                worksheet_status = gr.Markdown("建立槽位后，点击表格中的某一行，在下方编辑并生成候选。")
                request_table = gr.Dataframe(
                    headers=["序号", "声线名称", "状态", "候选数", "暂存数", "已选择", "语言", "最近保存", "ID"],
                    value=request_rows(default_project),
                    interactive=False,
                    wrap=True,
                )
                with gr.Accordion("🕘 最近操作记录（不代表生成进度）", open=False):
                    refresh_activity_btn = gr.Button("刷新操作记录", variant="secondary")
                    activity_table = gr.Markdown(activity_rows(default_project))

                gr.Markdown(
                    "## 1 · 创建、挑选并固化声线\n"
                    "从工作表选择人物并生成候选；满意后可暂存、选为最终声线，并在本区直接统一固化。",
                    elem_id="workflow-step-1",
                    elem_classes=["workflow-step-banner"],
                )
                with gr.Row():
                    with gr.Column(scale=2):
                        request_name = gr.Textbox(label="声线名称", placeholder="例如：温柔旁白女声")
                        request_prompt = gr.Textbox(label="声线提示词", lines=4, placeholder="描述年龄、性别、音色、情绪、语速和表达方式")
                        audition_text = gr.Textbox(label="试听文本", lines=3, value="你好，这是用于挑选声线的一段试听文本。")
                        with gr.Row():
                            request_language = gr.Dropdown(label="语言", choices=LANGUAGES, value="Chinese")
                            candidate_count = gr.Slider(label="本轮候选数", minimum=1, maximum=4, step=1, value=4)
                        generate_candidates_btn = gr.Button("生成候选", variant="primary")
                    with gr.Column(scale=5):
                        request_state = gr.State()
                        candidate_ids_state = gr.State([])
                        with gr.Row(equal_height=True, min_height=320, elem_classes=["audio-grid-row"]):
                            candidate_audio = []
                            select_buttons = []
                            shortlist_buttons = []
                            single_output_buttons = []
                            for index in range(4):
                                with gr.Column(scale=1, min_width=180, elem_classes=["candidate-card"]):
                                    candidate_audio.append(gr.Audio(
                                        label=f"候选 {index + 1}", type="filepath",
                                        elem_id=f"candidate-audio-{index + 1}",
                                        elem_classes=["voice-audio-player"],
                                    ))
                                    with gr.Row(elem_classes=["candidate-actions"]):
                                        shortlist_buttons.append(gr.Button(f"暂存 {index + 1}"))
                                        single_output_buttons.append(gr.Button("单句成品"))
                                        select_buttons.append(gr.Button(f"选定 {index + 1}", variant="primary"))
                        candidate_status = gr.Markdown("生成后试听候选；可以继续使用同名需求追加候选。")

                        with gr.Accordion("📌 暂存对比池（跨轮比较时使用）", open=False):
                            shortlisted_ids_state = gr.State([])
                            with gr.Row(
                                equal_height=True,
                                min_height=320,
                                elem_classes=["audio-grid-row", "shortlist-grid"],
                            ):
                                shortlisted_audio = []
                                choose_shortlisted_buttons = []
                                remove_shortlisted_buttons = []
                                single_shortlisted_buttons = []
                                for index in range(4):
                                    with gr.Column(
                                        scale=1,
                                        min_width=180,
                                        elem_classes=["candidate-card", "shortlist-card"],
                                    ):
                                        shortlisted_audio.append(gr.Audio(
                                            label=f"暂存 {index + 1}", type="filepath",
                                            elem_id=f"shortlisted-audio-{index + 1}",
                                            elem_classes=["voice-audio-player"],
                                        ))
                                        with gr.Row(elem_classes=["candidate-actions"]):
                                            remove_shortlisted_buttons.append(gr.Button("移出"))
                                            single_shortlisted_buttons.append(gr.Button("单句成品"))
                                            choose_shortlisted_buttons.append(gr.Button("选为最终"))

                gr.Markdown(
                    "### 选定与固化\n"
                    "全部人物选择完成后在这里统一固化；解冻只会恢复选择状态，不删除已有声线信息。",
                    elem_classes=["workflow-compact-note"],
                )
                with gr.Row():
                    fix_selected_btn = gr.Button("固化当前项目所有已选声线", variant="primary")
                    keep_all_single_btn = gr.Button("全部已选试听保留为单句成品", variant="secondary")
                    reopen_fixed_btn = gr.Button("解冻当前声线（重新选择）", variant="secondary")
                fixed_status = gr.Markdown("固化时系统会释放 VoiceDesign 并加载 Base。")

                gr.Markdown(
                    "## 2 · 绑定声线并批量生成\n"
                    "选择已固化声线，按一行一句粘贴台词，加入任务表后开始或继续生成。",
                    elem_id="workflow-step-2",
                    elem_classes=["workflow-step-banner"],
                )
                with gr.Row():
                    with gr.Column(scale=2):
                        batch_voice = gr.Dropdown(
                            label="绑定声线",
                            choices=service.voice_choices(default_project),
                            value=(service.voice_choices(default_project)[0][1] if service.voice_choices(default_project) else None),
                            filterable=True,
                        )
                        batch_language = gr.Dropdown(label="语言", choices=LANGUAGES, value="Chinese")
                        batch_text = gr.Textbox(
                            label="台词文本",
                            lines=10,
                            placeholder="一行对应一个音频文件。\n空行会被忽略。",
                        )
                        with gr.Row():
                            add_batch_btn = gr.Button("加入任务", variant="secondary")
                            run_batch_btn = gr.Button("开始/继续批量生成", variant="primary")
                        with gr.Accordion("🛠 任务管理：刷新、保存、清理、取消与删除", open=False):
                            with gr.Row():
                                refresh_batch_btn = gr.Button("刷新任务表")
                                save_batch_btn = gr.Button("保存预览修改")
                                clear_batch_btn = gr.Button("清除未完成任务")
                            with gr.Row():
                                cancel_batch_btn = gr.Button("取消当前任务", variant="stop")
                                delete_batch_btn = gr.Button("删除选中任务", variant="stop")
                            with gr.Row():
                                batch_delete_positions = gr.Textbox(
                                    label="批量删除序号",
                                    placeholder="例如：1,2,3 或 10-20",
                                    scale=3,
                                )
                                delete_batch_many_btn = gr.Button("批量删除任务", variant="stop", scale=1)
                        selected_batch_position = gr.State()
                        batch_status = gr.Markdown()
                    with gr.Column(scale=5):
                        gr.Markdown("点选任务表中的任意单元格即可选中该行，再点击左侧“删除选中任务”。")
                        batch_table = gr.Dataframe(
                            headers=["序号", "声线", "台词", "语言", "状态", "输出", "错误"],
                            value=batch_rows(default_project),
                            interactive=True,
                            type="array",
                            wrap=True,
                            elem_id="batch-task-table",
                        )
                gr.Markdown(
                    "## 3 · 试听结果与单句返修\n"
                    "逐条试听全部结果；不满意时直接修改该句文本或语言，只重新生成这一条。",
                    elem_id="workflow-step-3",
                    elem_classes=["workflow-step-banner"],
                )
                initial_regen_choices = batch_regen_voice_choices(default_project)
                with gr.Accordion("♻ 按人物批量重新生成", open=False):
                    gr.Markdown("保持人物当前绑定的原声线和原台词不变，只重新生成该人物已有的全部音频。")
                    batch_regen_voice = gr.Dropdown(
                        label="选择人物（括号内为已有台词数）",
                        choices=initial_regen_choices,
                        value=(initial_regen_choices[0][1] if initial_regen_choices else None),
                        filterable=True,
                    )
                    batch_regen_confirm = gr.Checkbox(
                        label="我确认重新生成该人物全部已有台词；其他人物不受影响",
                        value=False,
                    )
                    batch_regen_btn = gr.Button("重新生成该人物全部台词", variant="primary")
                    batch_regen_status = gr.Markdown(
                        "每句成功后才替换当前结果；失败时保留该句原音频。"
                    )
                batch_regen_timer = gr.Timer(1.0, active=False)
                with gr.Row():
                    batch_result_refresh = gr.Button("加载/刷新全部结果")
                    batch_result_count = gr.Markdown(
                        f"当前项目已有 **{len(initial_batch_results)}** 条结果；点击按钮后展开试听列表。"
                    )
                batch_results_project = gr.State(None)
                batch_result_limit = gr.State(0)
                batch_load_timer = gr.Timer(0.8, active=False)
                batch_results_revision = gr.Textbox(
                    value=database.new_id(), visible=False, interactive=False
                )

                @gr.render(
                    inputs=[batch_results_project, batch_results_revision, batch_result_limit],
                    queue=False,
                    show_progress="hidden",
                )
                def render_batch_results(project_id, _revision, result_limit):
                    if not project_id:
                        gr.Markdown("试听列表尚未加载，以保证项目切换快速稳定。")
                        return
                    records = batch_result_records(project_id)
                    if not records:
                        gr.Markdown("当前项目还没有可试听的已完成结果。")
                        return
                    visible_count = min(int(result_limit or len(records)), len(records))
                    visible_records = records[:visible_count]
                    for offset in range(0, len(visible_records), 2):
                        with gr.Row(equal_height=False, key=f"batch-row-{offset}"):
                            for record in visible_records[offset:offset + 2]:
                                item_id = record["id"]
                                elem_id = f"batch-audio-{item_id}"
                                with gr.Column(
                                    scale=1,
                                    min_width=420,
                                    elem_classes=["batch-result-card"],
                                    key=f"batch-card-{item_id}",
                                ):
                                    gr.Markdown(
                                        f"#### {int(record['position']):04d} · {record['voice_name']}"
                                    )
                                    version_markdown = gr.Markdown(
                                        f"当前版本：{display_time(record.get('updated_at'))}",
                                        elem_classes=["studio-muted"],
                                    )
                                    if record.get("status") == "running":
                                        gr.Markdown("⏳ 正在重新生成；原版本仍可试听。")
                                    elif record.get("status") == "failed":
                                        gr.Markdown(
                                            f"⚠️ 上次重新生成失败，当前仍播放原版本。{record.get('error') or ''}"
                                        )
                                    audio_player = gr.HTML(
                                        native_history_audio(
                                            record["output_mp3"], elem_id, record.get("updated_at") or ""
                                        ),
                                        key=f"audio-{item_id}",
                                    )
                                    edit_text = gr.Textbox(
                                        label="修改该句文本",
                                        value=record["text"],
                                        lines=3,
                                        interactive=True,
                                        key=f"text-{item_id}",
                                    )
                                    with gr.Row():
                                        edit_language = gr.Dropdown(
                                            label="语言",
                                            choices=LANGUAGES,
                                            value=record["language"],
                                            interactive=True,
                                            scale=2,
                                            key=f"language-{item_id}",
                                        )
                                        regenerate_btn = gr.Button(
                                            "仅重新生成该句",
                                            variant="primary",
                                            scale=3,
                                            key=f"regenerate-{item_id}",
                                        )
                                    item_status = gr.Markdown()
                                    regenerate_btn.click(
                                        lambda pos=record["position"], voice=record["voice_name"]: regeneration_started(
                                            pos, voice
                                        ),
                                        outputs=[item_status, regenerate_btn],
                                        queue=False,
                                    ).then(
                                        lambda pid, text, language, iid=item_id, eid=elem_id: regenerate_batch_result(
                                            pid, iid, text, language, eid
                                        ),
                                        inputs=[project_select, edit_text, edit_language],
                                        outputs=[
                                            audio_player,
                                            version_markdown,
                                            batch_table,
                                            item_status,
                                            regenerate_btn,
                                            batch_status,
                                        ],
                                    )
                    gr.Markdown(
                        f"已展示 **{visible_count}/{len(records)}** 条历史批量结果。"
                    )

                single_outputs_revision = gr.Textbox(
                    value=database.new_id(), visible=False, interactive=False
                )
                with gr.Accordion("🎧 已保留的单句成品（独立管理）", open=False):
                    single_output_status = gr.Markdown(
                        "删除只会移除单句成品副本，不会删除候选、固化声线或批量任务。"
                    )

                    @gr.render(
                        inputs=[project_select, single_outputs_revision],
                        queue=False,
                        show_progress="hidden",
                    )
                    def render_single_outputs(project_id, _revision):
                        records = single_output_records(project_id)
                        if not records:
                            gr.Markdown("当前项目还没有单句成品。")
                            return
                        for offset in range(0, len(records), 2):
                            with gr.Row(equal_height=False, key=f"single-row-{offset}"):
                                for record in records[offset:offset + 2]:
                                    elem_id = f"single-output-{record['id']}"
                                    with gr.Column(
                                        scale=1,
                                        min_width=420,
                                        elem_classes=["batch-result-card"],
                                        key=f"single-card-{record['id']}",
                                    ):
                                        gr.Markdown(
                                            f"#### 单句 {int(record['position'])} · {record['name']}\n\n"
                                            f"{record['text']}"
                                        )
                                        gr.HTML(
                                            native_history_audio(
                                                record["direct_output_mp3"], elem_id, record.get("updated_at") or ""
                                            ),
                                            key=f"single-audio-{record['id']}-{record['updated_at']}",
                                        )
                                        delete_single_btn = gr.Button(
                                            "删除该单句成品",
                                            variant="stop",
                                            key=f"delete-single-{record['id']}",
                                        )
                                        delete_single_btn.click(
                                            lambda pid, rid=record["id"]: delete_single_output(pid, rid),
                                            inputs=[project_select],
                                            outputs=[
                                                single_outputs_revision,
                                                request_table,
                                                worksheet_progress,
                                                activity_table,
                                                single_output_status,
                                            ],
                                            queue=False,
                                        )

                gr.Markdown(
                    "## 4 · 导出成品与批量下载\n"
                    "批量生成结果和已保留的单句成品会合并导出，可复制到指定本机目录，或打包为 ZIP 一次下载。",
                    elem_id="workflow-step-4",
                    elem_classes=["workflow-step-banner"],
                )
                with gr.Accordion("⚙ 导出设置：目录、文件名截取字数与命名规则", open=False):
                    with gr.Row():
                        with gr.Column(scale=4):
                            batch_export_dir = gr.Textbox(
                                label="本机导出目录",
                                value=initial_export_dir,
                                placeholder=r"例如：D:\配音成品\当前项目",
                            )
                            batch_excerpt_length = gr.Slider(
                                label="文件名中的台词截取字数",
                                minimum=5,
                                maximum=10,
                                step=1,
                                value=initial_excerpt_length,
                            )
                            gr.Markdown(
                                "命名格式：`序号_人物名字_朗读文本前N字.mp3`（序号不补零）"
                            )
                with gr.Row():
                    with gr.Column(scale=4):
                        with gr.Row():
                            export_batch_btn = gr.Button("导出全部成品到设定目录", variant="primary")
                            open_export_dir_btn = gr.Button("打开导出文件夹")
                            archive_batch_btn = gr.Button("生成全部成品 ZIP", variant="secondary")
                        batch_export_status = gr.Markdown(
                            "导出设置默认收起；ZIP 的最终下载位置由浏览器下载设置决定。"
                        )
                    with gr.Column(scale=2):
                        batch_archive_file = gr.File(label="全部成品下载 ZIP", interactive=False)

            with gr.Column(visible=False, elem_id="page-dubbing") as dubbing_page:
                gr.Markdown("## 配音\n选择已固化声线，粘贴台词并生成批量任务。")
                with gr.Row():
                    with gr.Column(scale=2):
                        dubbing_voice = gr.Dropdown(label="绑定声线", choices=service.voice_choices(default_project), value=(service.voice_choices(default_project)[0][1] if service.voice_choices(default_project) else None), filterable=True)
                        dubbing_language = gr.Dropdown(label="语言", choices=LANGUAGES, value="Chinese")
                        dubbing_text = gr.Textbox(label="台词文本", lines=12, placeholder="一行对应一个音频文件。\n空行会被忽略。")
                        with gr.Row():
                            dubbing_add_btn = gr.Button("加入任务", variant="secondary")
                            dubbing_run_btn = gr.Button("开始/继续批量生成", variant="primary")
                        dubbing_status = gr.Markdown()
                    with gr.Column(scale=5):
                        dubbing_table = gr.Dataframe(headers=["序号", "声线", "台词", "语言", "状态", "输出", "错误"], value=batch_rows(default_project), interactive=True, type="array", wrap=True, elem_id="dubbing-task-table")
            with gr.Column(visible=False, elem_id="page-results") as results_page:
                gr.Markdown("## 成品\n查看当前项目已完成的批量结果，并进入原有试听、返修与导出工作流。")
                results_refresh_btn = gr.Button("加载/刷新全部结果", variant="primary")
                results_count = gr.Markdown(f"当前项目已有 **{len(initial_batch_results)}** 条结果。")
                results_table = gr.Dataframe(headers=["序号", "声线", "台词", "语言", "状态", "输出", "错误"], value=batch_rows(default_project), interactive=False, wrap=True, elem_id="results-task-table")
                gr.Markdown("详细音频试听、单句返修及导出设置可在“声线”工作流对应区继续使用。")
            with gr.Column(visible=False, elem_id="page-projects") as projects_page:
                gr.Markdown("## 项目管理\n创建、刷新和删除项目；切换项目请使用左侧“当前项目”。")
                with gr.Row():
                    projects_name = gr.Textbox(label="新项目名称", placeholder="例如：有声书第一章", scale=4)
                    projects_create_btn = gr.Button("新建项目", variant="primary")
                    projects_refresh_btn = gr.Button("刷新项目列表")
                projects_status = gr.Markdown()
                projects_table = gr.Dataframe(headers=["当前", "项目名称", "声线槽位", "可用固化声线", "单句成品", "批量任务", "已完成", "最近活动", "短 ID"], value=project_rows(default_project), interactive=False, wrap=True, elem_id="projects-table")
                projects_confirm = gr.Textbox(label="输入当前项目名称以确认删除")
                projects_delete_btn = gr.Button("永久删除当前项目", variant="stop")
            with gr.Column(visible=False, elem_id="page-assets") as assets_page:
                with gr.Row():
                    library_refresh = gr.Button("刷新声线库")
                    library_project = gr.Dropdown(
                        label="当前项目（资产筛选）", choices=projects,
                        value=default_project, interactive=False,
                    )
                    library_search = gr.Textbox(label="搜索", placeholder="名称、标签、语言或备注")
                library_table = gr.Dataframe(
                    headers=["名称", "来源", "语言", "标签", "状态", "创建时间", "ID"],
                    value=library_rows(default_project), interactive=False, wrap=True,
                )
                gr.Markdown("### 使用已归档声线快速生成")
                with gr.Row():
                    with gr.Column(scale=2):
                        library_voice = gr.Dropdown(label="声线", choices=service.voice_choices(default_project))
                        library_language = gr.Dropdown(label="语言", choices=LANGUAGES, value="Chinese")
                        library_text = gr.Textbox(label="文本", lines=5)
                        library_generate_btn = gr.Button("生成 128kbps MP3", variant="primary")
                        library_delete_confirmation = gr.Textbox(
                            label="删除资产确认（输入完整声线名称）",
                            placeholder="删除前请输入上方选中的声线名称",
                        )
                        library_delete_btn = gr.Button("删除当前声线资产", variant="stop")
                    with gr.Column(scale=3):
                        library_audio = gr.Audio(label="输出", type="filepath", elem_id="library-audio")
                        library_status = gr.Markdown()

            with gr.Column(visible=False, elem_id="page-upload") as upload_page:
                gr.Markdown(
                    "上传已有 WAV 或 MP3，生成一段克隆试听并固化为可复用资产。"
                    "成功后会自动进入声线资产库和项目工作流的“绑定声线”列表。"
                )
                with gr.Row():
                    with gr.Column(scale=2):
                        upload_project = gr.Dropdown(
                            label="当前项目（上传归属）", choices=projects,
                            value=default_project, interactive=False,
                        )
                        upload_name = gr.Textbox(label="声线名称")
                        upload_audio = gr.Audio(
                            label="参考音频（WAV/MP3）", type="filepath", sources=["upload"],
                            elem_id="upload-reference-audio",
                        )
                        upload_ref_text = gr.Textbox(label="参考音频对应文字", lines=3)
                        upload_xvec = gr.Checkbox(label="仅声纹模式（无需参考文字，但质量可能降低）", value=False)
                        upload_language = gr.Dropdown(label="语言", choices=LANGUAGES, value="Chinese")
                        upload_preview_text = gr.Textbox(label="克隆试听文本", lines=3, value="你好，这是已有声线的克隆试听。")
                        upload_tags = gr.Textbox(label="标签", placeholder="例如：女声, 旁白, 温柔")
                        upload_notes = gr.Textbox(label="备注", lines=2)
                        upload_btn = gr.Button("试听、固化并归档", variant="primary")
                    with gr.Column(scale=3):
                        upload_preview = gr.Audio(label="克隆试听", type="filepath", elem_id="upload-preview-audio")
                        upload_status = gr.Markdown()

            with gr.Column(visible=False, elem_id="page-design") as design_page:
                with gr.Row():
                    with gr.Column(scale=2):
                        design_text = gr.Textbox(label="待合成文本", lines=4)
                        design_language = gr.Dropdown(label="语言", choices=LANGUAGES, value="Chinese")
                        design_prompt = gr.Textbox(label="声线描述", lines=4)
                        design_btn = gr.Button("生成", variant="primary")
                    with gr.Column(scale=3):
                        design_audio = gr.Audio(label="输出", type="filepath", elem_id="design-audio")
                        design_status = gr.Markdown()

            with gr.Column(visible=False, elem_id="page-clone") as clone_page:
                with gr.Row():
                    with gr.Column(scale=2):
                        clone_ref_audio = gr.Audio(
                            label="参考音频", type="filepath", sources=["upload"],
                            elem_id="clone-reference-audio",
                        )
                        clone_ref_text = gr.Textbox(label="参考音频对应文字", lines=3)
                        clone_xvec = gr.Checkbox(label="仅声纹模式", value=False)
                    with gr.Column(scale=2):
                        clone_text = gr.Textbox(label="待合成文本", lines=5)
                        clone_language = gr.Dropdown(label="语言", choices=LANGUAGES, value="Chinese")
                        clone_btn = gr.Button("克隆并生成", variant="primary")
                    with gr.Column(scale=3):
                        clone_audio = gr.Audio(label="输出", type="filepath", elem_id="clone-output-audio")
                        clone_status = gr.Markdown()

            with gr.Column(visible=False, elem_id="page-audio") as audio_page:
                with gr.Row():
                    converter_files = gr.File(label="音频文件", file_count="multiple", type="filepath")
                    with gr.Column():
                        converter_target = gr.Radio(label="目标格式", choices=["MP3 (128kbps)", "WAV (24kHz Mono)"], value="MP3 (128kbps)")
                        converter_btn = gr.Button("批量转换", variant="primary")
                        converter_status = gr.Markdown()
                converter_outputs = gr.File(label="转换结果", file_count="multiple")

            with gr.Column(visible=False, elem_id="page-system") as system_page:
                status_box = gr.Textbox(label="运行状态", value=system_status(), lines=5, interactive=False)
                with gr.Row():
                    status_refresh = gr.Button("刷新状态")
                    unload_btn = gr.Button("释放当前模型")

        create_project_btn.click(
            create_project,
            inputs=[new_project],
            outputs=[project_select, library_project, upload_project, project_table, sidebar_project_summary, project_status],
        )
        page_outputs = [voice_page, dubbing_page, results_page, assets_page, upload_page, projects_page, design_page, clone_page, audio_page, system_page]
        for nav_button, target in (
            (voice_nav, "voice"), (dubbing_nav, "dubbing"), (results_nav, "results"),
            (assets_nav, "assets"), (upload_nav, "upload"), (projects_nav, "projects"),
            (design_nav, "design"), (clone_nav, "clone"), (audio_nav, "audio"),
            (system_nav, "system"),
        ):
            nav_button.click(lambda target=target: switch_view(target), outputs=page_outputs, queue=False)
        dubbing_add_btn.click(add_batch, inputs=[project_select, dubbing_voice, dubbing_language, dubbing_text], outputs=[dubbing_table, dubbing_status, dubbing_text])
        dubbing_run_btn.click(lambda: (gr.update(interactive=False), "⏳ 正在生成，请勿重复点击……"), outputs=[dubbing_run_btn, dubbing_status], queue=False).then(
            run_batch, inputs=[project_select], outputs=[dubbing_table, dubbing_status, batch_results_project, batch_results_revision, batch_result_count]
        ).then(lambda: gr.update(interactive=True), outputs=[dubbing_run_btn], queue=False)
        results_refresh_btn.click(refresh_batch_results, inputs=[project_select], outputs=[batch_results_project, batch_results_revision, results_count, batch_result_limit, batch_load_timer], queue=False)
        projects_create_btn.click(create_project, inputs=[projects_name], outputs=[project_select, library_project, upload_project, projects_table, sidebar_project_summary, projects_status], queue=False)
        projects_refresh_btn.click(refresh_projects, inputs=[project_select], outputs=[project_select, library_project, upload_project, projects_table], queue=False)
        projects_delete_btn.click(delete_project_action, inputs=[project_select, projects_confirm], outputs=[project_select, projects_table, projects_status, projects_confirm], queue=False, show_progress="hidden")
        delete_project_btn.click(
            delete_project_action,
            inputs=[project_select, delete_project_confirmation],
            outputs=[project_select, project_table, project_status, delete_project_confirmation],
            queue=False,
            show_progress="hidden",
        )
        refresh_project_btn.click(
            refresh_projects,
            inputs=[project_select],
            outputs=[project_select, library_project, upload_project, project_table],
            queue=False,
        ).then(
            switch_project,
            inputs=[project_select],
            outputs=[
                batch_table, batch_voice, request_table, worksheet_progress, activity_table,
                request_state, request_name, request_prompt, audition_text, request_language,
                candidate_ids_state, *candidate_audio,
                shortlisted_ids_state, *shortlisted_audio,
                candidate_status, batch_regen_voice,
            ],
            queue=False,
            show_progress="hidden",
        )
        create_slots_btn.click(
            bulk_create_requests,
            inputs=[project_select, slot_count, slot_prefix, slot_names, slot_language],
            outputs=[request_table, worksheet_progress, worksheet_status, activity_table],
        )
        append_slot_btn.click(
            append_voice_slot,
            inputs=[project_select, append_slot_name, slot_language],
            outputs=[request_table, worksheet_progress, worksheet_status, activity_table, append_slot_name],
            queue=False,
            show_progress="hidden",
        )
        request_table.select(
            load_request,
            inputs=[project_select],
            outputs=[
                request_state, request_name, request_prompt, audition_text, request_language,
                candidate_ids_state, *candidate_audio,
                shortlisted_ids_state, *shortlisted_audio,
                candidate_status,
            ],
            queue=False,
            show_progress="hidden",
        )
        draft_inputs = [request_state, project_select, request_name, request_prompt, audition_text, request_language]
        save_draft_btn.click(
            save_request_draft,
            inputs=draft_inputs,
            outputs=[request_table, worksheet_progress, worksheet_status],
        )
        delete_voice_slot_btn.click(
            delete_voice_slot,
            inputs=[project_select, request_state],
            outputs=[
                request_table,
                worksheet_progress,
                worksheet_status,
                activity_table,
                request_state,
                request_name,
                request_prompt,
                audition_text,
                request_language,
                candidate_ids_state,
                *candidate_audio,
                shortlisted_ids_state,
                *shortlisted_audio,
                candidate_status,
                single_outputs_revision,
            ],
            queue=False,
        )
        for draft_component in (request_name, request_prompt, audition_text):
            draft_component.blur(
                save_request_draft,
                inputs=draft_inputs,
                outputs=[request_table, worksheet_progress, worksheet_status],
                queue=False,
            )
        request_language.change(
            save_request_draft,
            inputs=draft_inputs,
            outputs=[request_table, worksheet_progress, worksheet_status],
            queue=False,
        )
        generate_candidates_btn.click(
            lambda: (gr.update(interactive=False), "⏳ 正在生成候选声线（会逐个生成，请勿重复点击）……"),
            outputs=[generate_candidates_btn, candidate_status],
            queue=False,
        ).then(
            generate_candidates,
            inputs=[project_select, request_state, request_name, request_prompt, audition_text, request_language, candidate_count],
            outputs=[request_state, candidate_ids_state, *candidate_audio, candidate_status, request_table, worksheet_progress, activity_table],
        ).then(
            lambda: gr.update(interactive=True),
            outputs=[generate_candidates_btn],
            queue=False,
        )
        for index, button in enumerate(select_buttons):
            button.click(
                lambda project_id, request_id, candidate_ids, i=index: choose_candidate(i, project_id, request_id, candidate_ids),
                inputs=[project_select, request_state, candidate_ids_state],
                outputs=[candidate_status, request_table, worksheet_progress, activity_table],
            )
        for index, button in enumerate(single_output_buttons):
            button.click(
                lambda project_id, request_id, candidate_ids, i=index: keep_single_candidate(
                    i, project_id, request_id, candidate_ids
                ),
                inputs=[project_select, request_state, candidate_ids_state],
                outputs=[
                    candidate_status,
                    request_table,
                    worksheet_progress,
                    activity_table,
                    single_outputs_revision,
                ],
                queue=False,
            )
        for index, button in enumerate(shortlist_buttons):
            button.click(
                lambda project_id, request_id, candidate_ids, i=index: shortlist_candidate(i, project_id, request_id, candidate_ids),
                inputs=[project_select, request_state, candidate_ids_state],
                outputs=[shortlisted_ids_state, *shortlisted_audio, candidate_status, request_table, activity_table],
            )
        for index, button in enumerate(remove_shortlisted_buttons):
            button.click(
                lambda project_id, request_id, candidate_ids, i=index: remove_shortlisted(i, project_id, request_id, candidate_ids),
                inputs=[project_select, request_state, shortlisted_ids_state],
                outputs=[shortlisted_ids_state, *shortlisted_audio, candidate_status, request_table, activity_table],
            )
        for index, button in enumerate(choose_shortlisted_buttons):
            button.click(
                lambda project_id, request_id, candidate_ids, i=index: choose_shortlisted(i, project_id, request_id, candidate_ids),
                inputs=[project_select, request_state, shortlisted_ids_state],
                outputs=[candidate_status, request_table, worksheet_progress, activity_table],
            )
        for index, button in enumerate(single_shortlisted_buttons):
            button.click(
                lambda project_id, request_id, candidate_ids, i=index: keep_single_candidate(
                    i, project_id, request_id, candidate_ids
                ),
                inputs=[project_select, request_state, shortlisted_ids_state],
                outputs=[
                    candidate_status,
                    request_table,
                    worksheet_progress,
                    activity_table,
                    single_outputs_revision,
                ],
                queue=False,
            )
        fix_selected_btn.click(
            fix_selected,
            inputs=[project_select],
            outputs=[fixed_status, batch_voice, library_table, request_table, worksheet_progress, activity_table],
        )
        keep_all_single_btn.click(
            keep_all_selected_single_outputs,
            inputs=[project_select],
            outputs=[
                fixed_status,
                request_table,
                worksheet_progress,
                activity_table,
                single_outputs_revision,
            ],
            queue=False,
        )
        reopen_fixed_btn.click(
            reopen_fixed,
            inputs=[project_select, request_state],
            outputs=[fixed_status, batch_voice, library_table, request_table, worksheet_progress, activity_table, candidate_status],
        )
        project_select.change(
            switch_project,
            inputs=[project_select],
            outputs=[
                batch_table, batch_voice, request_table, worksheet_progress, activity_table,
                request_state, request_name, request_prompt, audition_text, request_language,
                candidate_ids_state, *candidate_audio,
                shortlisted_ids_state, *shortlisted_audio,
                candidate_status, batch_regen_voice,
            ],
            queue=False,
            show_progress="hidden",
        )
        project_select.change(
            project_summary_text,
            inputs=[project_select],
            outputs=[sidebar_project_summary],
            queue=False,
        )
        project_select.change(
            sync_project_contexts,
            inputs=[project_select],
            outputs=[
                library_project,
                upload_project,
                project_table,
                library_table,
                library_voice,
                dubbing_voice,
            ],
            queue=False,
        )
        project_select.change(
            clear_batch_results,
            inputs=[project_select],
            outputs=[batch_results_project, batch_results_revision, batch_result_count],
            queue=False,
        )
        project_select.change(lambda: None, outputs=[selected_batch_position], queue=False)
        refresh_activity_btn.click(
            activity_rows,
            inputs=[project_select],
            outputs=[activity_table],
            queue=False,
        )
        project_select.change(
            load_batch_export,
            inputs=[project_select],
            outputs=[batch_export_dir, batch_excerpt_length, batch_archive_file, batch_export_status],
            queue=False,
            show_progress="hidden",
        )
        add_batch_btn.click(
            add_batch,
            inputs=[project_select, batch_voice, batch_language, batch_text],
            outputs=[batch_table, batch_status, batch_text],
        )
        refresh_batch_btn.click(
            refresh_batch,
            inputs=[project_select],
            outputs=[batch_table, batch_voice, batch_status],
        )
        clear_batch_btn.click(clear_batch, inputs=[project_select], outputs=[batch_table, batch_status])
        save_batch_btn.click(
            save_batch_preview, inputs=[project_select, batch_table], outputs=[batch_table, batch_status]
        )
        batch_table.select(
            select_batch_task,
            inputs=[batch_table],
            outputs=[selected_batch_position, batch_status],
            queue=False,
        )
        delete_batch_btn.click(
            delete_selected_batch_task,
            inputs=[project_select, selected_batch_position],
            outputs=[
                batch_table,
                batch_status,
                batch_results_revision,
                batch_result_count,
                selected_batch_position,
            ],
            queue=False,
        )
        delete_batch_many_btn.click(
            delete_batch_tasks,
            inputs=[project_select, batch_delete_positions],
            outputs=[
                batch_table,
                batch_status,
                batch_results_revision,
                batch_result_count,
                batch_delete_positions,
            ],
            queue=False,
        )
        run_batch_btn.click(
            lambda: (gr.update(interactive=False), "⏳ 正在生成，请勿重复点击……"),
            outputs=[run_batch_btn, batch_status],
            queue=False,
        ).then(
            run_batch,
            inputs=[project_select],
            outputs=[
                batch_table,
                batch_status,
                batch_results_project,
                batch_results_revision,
                batch_result_count,
            ],
        ).then(lambda: gr.update(interactive=True), outputs=[run_batch_btn], queue=False)
        cancel_batch_btn.click(
            cancel_batch,
            inputs=[project_select],
            outputs=[batch_table, batch_status],
            queue=False,
        )
        batch_regen_btn.click(
            voice_regeneration_started,
            inputs=[project_select, batch_regen_voice, batch_regen_confirm],
            outputs=[batch_regen_status, batch_regen_timer, batch_regen_confirm],
            queue=False,
        )
        batch_regen_timer.tick(
            poll_voice_regeneration,
            inputs=[project_select],
            outputs=[
                batch_table,
                batch_results_project,
                batch_results_revision,
                batch_result_count,
                batch_regen_status,
                batch_regen_timer,
                batch_regen_confirm,
            ],
            queue=False,
        )
        batch_result_refresh.click(
            refresh_batch_results,
            inputs=[project_select],
            outputs=[
                batch_results_project,
                batch_results_revision,
                batch_result_count,
                batch_result_limit,
                batch_load_timer,
            ],
            queue=False,
        )
        batch_load_timer.tick(
            continue_loading_batch_results,
            inputs=[project_select, batch_results_project, batch_result_limit],
            outputs=[
                batch_result_limit,
                batch_results_revision,
                batch_result_count,
                batch_load_timer,
            ],
            queue=False,
        )
        export_batch_btn.click(
            export_batch_results,
            inputs=[project_select, batch_export_dir, batch_excerpt_length],
            outputs=[batch_export_dir, batch_export_status],
        )
        open_export_dir_btn.click(
            open_batch_export_dir,
            inputs=[batch_export_dir],
            outputs=[batch_export_status],
            queue=False,
        )
        archive_batch_btn.click(
            archive_batch_results,
            inputs=[project_select, batch_excerpt_length],
            outputs=[batch_archive_file, batch_export_status],
        )

        library_refresh.click(
            refresh_library, inputs=[library_project, library_search], outputs=[library_table, library_voice]
        )
        library_search.submit(
            lambda project_id, value: library_rows(project_id, value),
            inputs=[library_project, library_search],
            outputs=[library_table],
            queue=False,
            show_progress="hidden",
        )
        library_project.change(
            refresh_library,
            inputs=[library_project, library_search],
            outputs=[library_table, library_voice],
            queue=False,
            show_progress="hidden",
        )
        library_generate_btn.click(
            generate_library_voice,
            inputs=[library_project, library_voice, library_text, library_language],
            outputs=[library_audio, library_status],
        )
        upload_btn.click(
            upload_and_archive,
            inputs=[upload_project, upload_name, upload_audio, upload_ref_text, upload_language, upload_xvec, upload_preview_text, upload_tags, upload_notes],
            outputs=[upload_preview, upload_status, library_table, library_voice, batch_voice, dubbing_voice],
        )
        library_delete_btn.click(
            delete_library_voice,
            inputs=[library_project, library_voice, library_delete_confirmation],
            outputs=[
                library_status,
                library_table,
                library_voice,
                batch_voice,
                dubbing_voice,
                library_delete_confirmation,
            ],
            queue=False,
        )
        design_btn.click(
            standalone_design,
            inputs=[design_text, design_language, design_prompt],
            outputs=[design_audio, design_status],
        )
        clone_btn.click(
            standalone_clone,
            inputs=[clone_ref_audio, clone_ref_text, clone_xvec, clone_text, clone_language],
            outputs=[clone_audio, clone_status],
        )
        converter_btn.click(
            convert_audio,
            inputs=[converter_files, converter_target],
            outputs=[converter_outputs, converter_status],
        )
        status_refresh.click(system_status, outputs=[status_box])
        unload_btn.click(lambda: (models.unload(), system_status())[1], outputs=[status_box])

        # Bind directly to every Audio component. Gradio's Audio.play event is
        # dispatched from the actual player, including players rendered lazily
        # after a candidate is loaded.
        exclusive_audio_components = [
            *candidate_audio,
            *shortlisted_audio,
            library_audio,
            upload_audio,
            upload_preview,
            design_audio,
            clone_ref_audio,
            clone_audio,
        ]
        for audio_component in exclusive_audio_components:
            audio_component.play(
                None,
                js=exclusive_audio_js(audio_component.elem_id),
                queue=False,
            )

        project_load = app.load(
            refresh_projects_on_load,
            outputs=[project_select, library_project, upload_project, project_table],
            queue=False,
            show_progress="hidden",
        )
        project_load = project_load.then(
            project_summary_text,
            inputs=[project_select],
            outputs=[sidebar_project_summary],
            queue=False,
            show_progress="hidden",
        )
        project_load = project_load.then(
            switch_project,
            inputs=[project_select],
            outputs=[
                batch_table, batch_voice, request_table, worksheet_progress, activity_table,
                request_state, request_name, request_prompt, audition_text, request_language,
                candidate_ids_state, *candidate_audio,
                shortlisted_ids_state, *shortlisted_audio,
                candidate_status, batch_regen_voice,
            ],
            queue=False,
            show_progress="hidden",
        )
        project_load = project_load.then(
            clear_batch_results,
            inputs=[project_select],
            outputs=[batch_results_project, batch_results_revision, batch_result_count],
            queue=False,
            show_progress="hidden",
        )
        project_load.then(
            load_batch_export,
            inputs=[project_select],
            outputs=[batch_export_dir, batch_excerpt_length, batch_archive_file, batch_export_status],
            queue=False,
            show_progress="hidden",
        )

        layout_stack.close()

    return app
