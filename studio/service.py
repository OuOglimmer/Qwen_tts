"""Composition facade wiring the domain components together.

``StudioService`` is the single object the API and the legacy Gradio UI talk
to.  It owns no business logic: every domain lives in its own component
(:mod:`projects`, :mod:`voice_workflow`, :mod:`voice_assets`, :mod:`batch`,
:mod:`exports`, :mod:`tools`) and the facade only composes them and
delegates.  Dependency direction is one-way::

    exports -> batch -> voice_assets -> model_manager
       \\------------------> voice_workflow --^
    exports -> supplements -> voice_assets
    tools -> model_manager

New code should depend on the individual components (``service.batch`` etc.);
the delegating methods below keep the historical ``service.*`` surface
compatible for the existing API/UI layers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

from .batch import BatchService
from .database import Database
from .exports import ExportService
from .model_manager import ModelManager
from .projects import ProjectService
from .supplement import SupplementService
from .tags import TagService
from .tools import ToolsService
from .voice_assets import VoiceAssetService
from .voice_workflow import VoiceWorkflowService


class StudioService:
    def __init__(self, database: Database, models: ModelManager) -> None:
        self.db = database
        self.models = models
        self.tags = TagService(database)
        self.projects = ProjectService(database)
        self.workflow = VoiceWorkflowService(database, models)
        self.assets = VoiceAssetService(database, models)
        self.supplements = SupplementService(database, self.assets)
        self.batch = BatchService(database, models, self.assets)
        self.exports = ExportService(database, self.batch, self.workflow, self.supplements)
        self.tools = ToolsService(database, models)
        # 预设标签组/词条在启动时补齐：新装、迁移后的库都靠这一步收敛。
        self.tags.ensure_builtins()
        # 一个项目都没有时补一个「默认项目」。软删的项目仍占着名字，所以这一步
        # 得自己找不冲突的名字 —— 不能让它抛异常把后端起不来。
        self.projects.ensure_default_project()

    # ------------------------------------------------------------ system

    @property
    def database(self) -> Database:
        return self.db

    # ---------------------------------------------------------- projects

    def create_project(self, name: str) -> str:
        return self.projects.create_project(name)

    def list_projects(self) -> list[dict[str, Any]]:
        return self.projects.list_projects()

    def project_exists(self, project_id: str) -> bool:
        return self.projects.project_exists(project_id)

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        return self.projects.get_project(project_id)

    def project_summaries(self, include_archived: bool = False) -> list[dict[str, Any]]:
        return self.projects.project_summaries(include_archived)

    def activate_project(self, project_id: str) -> None:
        self.projects.activate_project(project_id)

    def switch_project_context(self, project_id: str | None):
        return self.projects.switch_project_context(project_id)

    def validate_data_integrity(self, project_id: str | None = None) -> list[str]:
        return self.projects.validate_data_integrity(project_id)

    def delete_project(self, project_id: str) -> str:
        # 历史（Gradio）入口：彻底删除。API 层请使用 archive/purge 双选项。
        return self.projects.purge_project(project_id)["name"]

    def rename_project(self, project_id: str, name: str) -> str:
        return self.projects.rename_project(project_id, name)

    def archive_project(self, project_id: str) -> str:
        return self.projects.archive_project(project_id)

    def purge_project(self, project_id: str) -> dict[str, Any]:
        return self.projects.purge_project(project_id)

    def set_last_step(self, project_id: str, step: int) -> None:
        self.projects.set_last_step(project_id, step)

    def resolve_last_step(self, project_id: str) -> int:
        return self.projects.resolve_last_step(project_id)

    # ----------------------------------------------------- voice workflow

    def create_voice_slots(self, project_id: str, count: int, prefix: str, names_text: str, language: str) -> list[str]:
        return self.workflow.create_voice_slots(project_id, count, prefix, names_text, language)

    def attach_library_voices(self, project_id: str, voice_ids: list[str]) -> list[dict[str, Any]]:
        """把全局库里的已有声线直接建成项目需求单里的已固化槽位。"""
        return self.workflow.attach_library_voices(project_id, voice_ids)

    def list_voice_requests(self, project_id: str) -> list[dict[str, Any]]:
        return self.workflow.list_voice_requests(project_id)

    def get_voice_request(self, request_id: str) -> dict[str, Any] | None:
        return self.workflow.get_voice_request(request_id)

    def delete_voice_request(self, project_id: str, request_id: str) -> tuple[str, bool]:
        return self.workflow.delete_voice_request(project_id, request_id)

    def request_candidates(self, request_id: str, limit: int = 4) -> list[dict[str, Any]]:
        return self.workflow.request_candidates(request_id, limit)

    def shortlisted_candidates(self, request_id: str, limit: int = 4) -> list[dict[str, Any]]:
        return self.workflow.shortlisted_candidates(request_id, limit)

    def set_candidate_shortlisted(self, request_id: str, candidate_id: str, shortlisted: bool) -> None:
        self.workflow.set_candidate_shortlisted(request_id, candidate_id, shortlisted)

    def save_voice_request(self, request_id: str, project_id: str, name: str, prompt: str, audition_text: str, language: str) -> None:
        self.workflow.save_voice_request(request_id, project_id, name, prompt, audition_text, language)

    def request_progress(self, project_id: str) -> dict[str, int]:
        return self.workflow.request_progress(project_id)

    def generate_candidates(self, project_id: str, name: str, prompt: str, audition_text: str, language: str, count: int, request_id: str | None = None) -> tuple[str, list[dict[str, Any]]]:
        return self.workflow.generate_candidates(project_id, name, prompt, audition_text, language, count, request_id)

    def start_candidate_generation(self, project_id: str, name: str, prompt: str, audition_text: str, language: str, count: int, request_id: str | None = None) -> dict[str, Any]:
        return self.workflow.start_candidate_generation(project_id, name, prompt, audition_text, language, count, request_id)

    def candidate_generation_status(self) -> dict[str, Any] | None:
        return self.workflow.candidate_generation_status()

    def cancel_candidate_generation(self) -> bool:
        return self.workflow.cancel_candidate_generation()

    def select_candidate(self, request_id: str, candidate_id: str) -> None:
        self.workflow.select_candidate(request_id, candidate_id)

    def deselect_candidate(self, request_id: str) -> str:
        return self.workflow.deselect_candidate(request_id)

    def keep_candidate_as_single_output(self, request_id: str, candidate_id: str) -> str:
        return self.workflow.keep_candidate_as_single_output(request_id, candidate_id)

    def keep_all_selected_as_single_outputs(self, project_id: str) -> list[str]:
        return self.workflow.keep_all_selected_as_single_outputs(project_id)

    def delete_single_line_output(self, project_id: str, request_id: str) -> tuple[str, bool]:
        return self.workflow.delete_single_line_output(project_id, request_id)

    def single_line_outputs(self, project_id: str) -> list[dict[str, Any]]:
        return self.workflow.single_line_outputs(project_id)

    def selected_requests(self, project_id: str, request_id: str | None = None) -> list[dict[str, Any]]:
        return self.workflow.selected_requests(project_id, request_id)

    def fix_selected_voices(self, project_id: str, request_id: str | None = None) -> list[str]:
        return self.workflow.fix_selected_voices(project_id, request_id)

    def reopen_fixed_request(self, request_id: str) -> str:
        return self.workflow.reopen_fixed_request(request_id)

    def voice_request_history(self, project_id: str, request_id: str) -> list[dict[str, Any]]:
        return self.workflow.voice_request_history(project_id, request_id)

    # ------------------------------------------------------- voice assets

    def get_voice(self, voice_id: str, project_id: str | None = None) -> dict[str, Any] | None:
        return self.assets.get_voice(voice_id, project_id)

    def project_voice(self, voice_id: str, project_id: str) -> dict[str, Any] | None:
        return self.assets.project_voice(voice_id, project_id)

    def library_voices(self, tag_names: str | list[str] | None = None, tag_mode: str = "any") -> list[dict[str, Any]]:
        return self.assets.library_voices(tag_names, tag_mode)

    def attach_voice(self, project_id: str, voice_id: str, display_name: str = "") -> str:
        return self.assets.attach_voice(project_id, voice_id, display_name)
    def archive_uploaded_voice(self, project_id: str | None, name: str, source_audio: str, ref_text: str, language: str, x_vector_only: bool, preview_text: str, tags: str = "", notes: str = "", ref_language: str = "") -> tuple[str, str]:
        return self.assets.archive_uploaded_voice(project_id, name, source_audio, ref_text, language, x_vector_only, preview_text, tags, notes, ref_language)

    def archive_designed_voice(self, name: str, prompt: str, language: str, audition_text: str, audio_path: str, tags: str = "", notes: str = "") -> tuple[str, str]:
        return self.assets.archive_designed_voice(name, prompt, language, audition_text, audio_path, tags, notes)

    def list_voices(self, project_id: str | None = None, include_replaced: bool = False) -> list[dict[str, Any]]:
        return self.assets.list_voices(project_id, include_replaced)

    # --------------------------------------------------------------- tags

    def list_tags(self, scope: str | None = None) -> list[dict[str, Any]]:
        return self.tags.groups(scope)

    def create_tag(self, group_id: str, name: str, color: str = "") -> dict[str, Any]:
        return self.tags.create_tag(group_id, name, color)

    def update_tag(self, tag_id: str, name: str | None = None, color: str | None = None) -> dict[str, Any]:
        return self.tags.update_tag(tag_id, name, color)

    def merge_tag(self, tag_id: str, into_tag_id: str) -> dict[str, Any]:
        return self.tags.merge_tag(tag_id, into_tag_id)

    def set_voice_tags(self, voice_id: str, tag_ids: list[str] | None, names: str | None) -> list[dict[str, Any]]:
        return self.tags.set_voice_tags(voice_id, tag_ids=tag_ids, names=names)

    def set_project_voice_tags(
        self, project_id: str, voice_id: str, tag_ids: list[str] | None, names: str | None
    ) -> list[dict[str, Any]]:
        return self.tags.set_project_voice_tags(project_id, voice_id, tag_ids=tag_ids, names=names)

    def set_request_tags(self, request_id: str, tag_ids: list[str] | None, names: str | None) -> list[dict[str, Any]]:
        return self.tags.set_request_tags(request_id, tag_ids=tag_ids, names=names)

    def generate_with_voice(self, voice_id: str, text: str, language: str, destination: Path, project_id: str | None = None) -> str:
        return self.assets.generate_with_voice(voice_id, text, language, destination, project_id)

    def delete_voice_asset(self, project_id: str, voice_id: str) -> str:
        return self.assets.delete_voice_asset(project_id, voice_id)

    # -------------------------------------------------------------- batch

    def add_batch_lines(self, project_id: str, voice_id: str, language: str, pasted_text: str) -> int:
        return self.batch.add_batch_lines(project_id, voice_id, language, pasted_text)

    def list_batch_items(self, project_id: str) -> list[dict[str, Any]]:
        return self.batch.list_batch_items(project_id)

    def batch_version_histories(self, project_id: str) -> dict[str, list[dict[str, Any]]]:
        return self.batch.version_histories(project_id)

    def delete_batch_item(self, project_id: str, position: int) -> dict[str, Any]:
        return self.batch.delete_batch_item(project_id, position)

    def delete_batch_items(self, project_id: str, positions: list[int]) -> list[dict[str, Any]]:
        return self.batch.delete_batch_items(project_id, positions)

    def clear_pending_batch(self, project_id: str) -> None:
        self.batch.clear_pending_batch(project_id)

    def save_batch_edits(self, project_id: str, rows: list[list[Any]]) -> int:
        return self.batch.save_batch_edits(project_id, rows)

    def cancel_running_batch(self, project_id: str) -> None:
        self.batch.cancel_running_batch(project_id)

    def start_batch(self, project_id: str) -> dict[str, Any]:
        return self.batch.start_batch(project_id)

    def run_batch(self, project_id: str) -> dict[str, int]:
        return self.batch.run_batch(project_id)

    def batch_task_status(self) -> dict[str, Any] | None:
        return self.batch.batch_task_status()

    def select_batch_item(self, project_id: str, item_id: str, selected: bool) -> dict[str, Any]:
        return self.batch.select_batch_item(project_id, item_id, selected)

    def regenerate_batch_item(self, project_id: str, item_id: str, text: str, language: str) -> str:
        return self.batch.regenerate_batch_item(project_id, item_id, text, language)

    def use_batch_version(self, project_id: str, item_id: str, version: int) -> dict[str, Any]:
        """把历史版本换回成品（复制成新的一版，见 BatchService.use_version）。"""
        return self.batch.use_version(project_id, item_id, version)

    def regenerate_voice_batch(self, project_id: str, voice_id: str) -> Iterator[dict[str, Any]]:
        return self.batch.regenerate_voice_batch(project_id, voice_id)

    def start_voice_regeneration(self, project_id: str, voice_id: str) -> dict[str, Any]:
        return self.batch.start_voice_regeneration(project_id, voice_id)

    def voice_regeneration_progress(self) -> dict[str, Any] | None:
        return self.batch.voice_regeneration_progress()

    def cancel_voice_regeneration(self) -> bool:
        return self.batch.cancel_voice_regeneration()

    # ------------------------------------------------------------ exports

    def export_completed_batch(
        self,
        project_id: str,
        destination: str,
        excerpt_length: int = 8,
        speed: float = 1.0,
        speeds: dict[str, float] | None = None,
    ) -> tuple[str, int]:
        return self.exports.export_completed_batch(
            project_id, destination, excerpt_length, speed, speeds
        )

    def export_preflight(self, project_id: str) -> dict[str, Any]:
        """导出前检查：同句多条成品会被合并成几条、哪个角色混着几版声线。"""
        return self.exports.export_preflight(project_id)

    def create_batch_archive(self, project_id: str, excerpt_length: int = 8) -> tuple[str, int]:
        return self.exports.create_batch_archive(project_id, excerpt_length)

    # -------------------------------------------------------------- tools

    def convert_files(self, files: list[str], target: str) -> list[str]:
        return self.tools.convert_files(files, target)

    def design_sample(self, text: str, language: str, prompt: str, design_id: str | None = None) -> str:
        return self.tools.design_sample(text, language, prompt, design_id)

    def clone_sample(self, source_audio: str, ref_text: str, x_vector_only: bool, text: str, language: str) -> str:
        return self.tools.clone_sample(source_audio, ref_text, x_vector_only, text, language)
