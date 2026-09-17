from __future__ import annotations

import json
import logging
import random
import shutil
import threading
import zipfile
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from .audio import copy_original, normalize_to_wav, safe_filename, wav_to_mp3, write_wav
from .config import DATA_DIR, PROJECT_DIR, TEMP_DIR, VOICE_DIR
from .database import Database, utc_now
from .model_manager import ModelManager
from .project_context import ProjectContext


LOG_DIR = DATA_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
BATCH_LOGGER = logging.getLogger("qwentts.batch")
if not BATCH_LOGGER.handlers:
    handler = RotatingFileHandler(
        LOG_DIR / "batch-regeneration.log",
        maxBytes=2_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    BATCH_LOGGER.addHandler(handler)
    BATCH_LOGGER.setLevel(logging.INFO)
    BATCH_LOGGER.propagate = False


class StudioService:
    def __init__(self, database: Database, models: ModelManager) -> None:
        self.db = database
        self.models = models
        self._random = random.SystemRandom()
        self._batch_cancel = threading.Event()
        self._voice_regen_lock = threading.Lock()
        self._voice_regen_thread: threading.Thread | None = None
        self._voice_regen_progress: dict[str, Any] | None = None
        if not self.list_projects():
            self.create_project("默认项目")

    def create_project(self, name: str) -> str:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("项目名称不能为空")
        existing = self.db.fetch_one("SELECT id FROM projects WHERE name=?", (clean_name,))
        if existing:
            return existing["id"]
        project_id = self.db.new_id()
        now = utc_now()
        self.db.execute(
            "INSERT INTO projects(id,name,status,created_at,updated_at) VALUES(?,?,?,?,?)",
            (project_id, clean_name, "draft", now, now),
        )
        (PROJECT_DIR / project_id / "candidates").mkdir(parents=True, exist_ok=True)
        (PROJECT_DIR / project_id / "outputs").mkdir(parents=True, exist_ok=True)
        self.db.set_setting("last_project_id", project_id)
        self.db.log_activity(project_id, "project", project_id, "创建项目", clean_name)
        return project_id

    def list_projects(self) -> list[dict[str, Any]]:
        return self.db.fetch_all("SELECT * FROM projects ORDER BY created_at DESC")

    def project_choices(self) -> list[tuple[str, str]]:
        return [(row["name"], row["id"]) for row in self.list_projects()]

    def project_summaries(self) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            """
            SELECT p.*,
                   (SELECT COUNT(*) FROM voice_requests r WHERE r.project_id=p.id) AS slot_count,
                   (SELECT COUNT(*) FROM voices v WHERE v.project_id=p.id AND v.status='active') AS voice_count,
                   (SELECT COUNT(*) FROM voice_requests r
                    WHERE r.project_id=p.id AND r.direct_output_mp3 IS NOT NULL) AS single_count,
                   (SELECT COUNT(*) FROM batch_items b
                    WHERE b.project_id=p.id AND b.status!='deleted') AS task_count,
                   (SELECT COUNT(*) FROM batch_items b
                    WHERE b.project_id=p.id AND b.status='completed') AS completed_count,
                   MAX(
                       p.updated_at,
                       COALESCE((SELECT MAX(r.updated_at) FROM voice_requests r WHERE r.project_id=p.id), p.updated_at),
                       COALESCE((SELECT MAX(v.updated_at) FROM voices v WHERE v.project_id=p.id), p.updated_at),
                       COALESCE((SELECT MAX(b.updated_at) FROM batch_items b WHERE b.project_id=p.id), p.updated_at)
                   ) AS last_activity
            FROM projects p
            ORDER BY last_activity DESC,p.created_at DESC
            """
        )

    def activate_project(self, project_id: str) -> None:
        self.switch_project_context(project_id)

    def switch_project_context(self, project_id: str | None) -> ProjectContext:
        """Validate and persist the sole project context used by the UI."""
        if not project_id:
            raise ValueError("请先选择项目")
        project = self.db.fetch_one(
            "SELECT id,name,status FROM projects WHERE id=?", (project_id,)
        )
        if not project:
            raise ValueError("项目不存在或已被删除")
        self.db.set_setting("last_project_id", project["id"])
        return ProjectContext(project["id"], project["name"], project["status"])

    def validate_data_integrity(self, project_id: str | None = None) -> list[str]:
        """Report missing asset paths without changing any user records."""
        params = (project_id,) if project_id else ()
        where_r = "WHERE r.project_id=?" if project_id else ""
        where_v = "WHERE v.project_id=?" if project_id else ""
        where_b = "WHERE b.project_id=?" if project_id else ""
        issues: list[str] = []
        for row in self.db.fetch_all(f"SELECT id,name,reference_wav,preview_mp3,prompt_path FROM voices v {where_v}", params):
            for field in ("reference_wav", "preview_mp3", "prompt_path"):
                if row[field] and not Path(row[field]).exists():
                    issues.append(f"voice:{row['name']} {field} 不存在")
        for row in self.db.fetch_all(f"SELECT c.id,c.wav_path,c.preview_path FROM candidates c JOIN voice_requests r ON r.id=c.request_id {where_r}", params):
            for field in ("wav_path", "preview_path"):
                if row[field] and not Path(row[field]).exists():
                    issues.append(f"candidate:{row['id'][:8]} {field} 不存在")
        for row in self.db.fetch_all(f"SELECT b.id,b.output_wav,b.output_mp3 FROM batch_items b {where_b}", params):
            for field in ("output_wav", "output_mp3"):
                if row[field] and not Path(row[field]).exists():
                    issues.append(f"batch:{row['id'][:8]} {field} 不存在")
        return issues

    def delete_project(self, project_id: str) -> str:
        """Permanently remove one project and every project-owned artifact."""
        project = self.db.fetch_one("SELECT id,name FROM projects WHERE id=?", (project_id,))
        if not project:
            raise ValueError("项目不存在或已被删除")
        project_root = (PROJECT_DIR / project_id).resolve()
        projects_root = PROJECT_DIR.resolve()
        if project_root.parent != projects_root:
            raise ValueError("项目目录校验失败，已停止删除")

        # Snapshot all user data before destructive deletion; failure aborts.
        self.db.backup_snapshot(f"pre-delete-project-{safe_filename(project['name'])}")

        voice_ids = [row["id"] for row in self.db.fetch_all(
            "SELECT id FROM voices WHERE project_id=?", (project_id,)
        )]
        with self.db._lock, self.db.connection() as connection:
            connection.execute("DELETE FROM batch_items WHERE project_id=?", (project_id,))
            if voice_ids:
                connection.executemany("DELETE FROM voices WHERE id=?", [(voice_id,) for voice_id in voice_ids])
            connection.execute("DELETE FROM projects WHERE id=?", (project_id,))
            connection.execute("DELETE FROM app_settings WHERE key LIKE ?", (f"%:{project_id}",))
            connection.execute(
                "DELETE FROM app_settings WHERE key='last_project_id' AND value=?", (project_id,)
            )

        if project_root.exists():
            shutil.rmtree(project_root)
        remaining = self.project_choices()
        if remaining:
            self.db.set_setting("last_project_id", remaining[0][1])
        return project["name"]

    def create_voice_slots(
        self,
        project_id: str,
        count: int,
        prefix: str,
        names_text: str,
        language: str,
    ) -> list[str]:
        if not project_id:
            raise ValueError("请先选择项目")
        requested_names = [line.strip() for line in names_text.splitlines() if line.strip()]
        if not requested_names:
            clean_prefix = prefix.strip() or "声线"
            requested_names = [f"{clean_prefix}{index:02d}" for index in range(1, max(1, min(int(count), 50)) + 1)]
        existing = {
            row["name"] for row in self.db.fetch_all(
                "SELECT name FROM voice_requests WHERE project_id=?", (project_id,)
            )
        }
        position_row = self.db.fetch_one(
            "SELECT COALESCE(MAX(position),0) AS max_position FROM voice_requests WHERE project_id=?",
            (project_id,),
        )
        next_position = int(position_row["max_position"]) + 1
        created: list[str] = []
        now = utc_now()
        for requested in requested_names[:50]:
            base = requested[:80]
            candidate = base
            suffix = 2
            while candidate in existing:
                candidate = f"{base}_{suffix}"
                suffix += 1
            request_id = self.db.new_id()
            self.db.execute(
                """
                INSERT INTO voice_requests(id,project_id,position,name,prompt,audition_text,language,status,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (request_id, project_id, next_position, candidate, "", "", language, "draft", now, now),
            )
            next_position += 1
            existing.add(candidate)
            created.append(candidate)
        self.db.log_activity(
            project_id, "voice_request", None, "批量创建声线需求", f"共 {len(created)} 个：{', '.join(created)}"
        )
        return created

    def list_voice_requests(self, project_id: str) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            """
            SELECT r.*,
                   COUNT(c.id) AS candidate_count,
                   COALESCE(SUM(CASE WHEN c.shortlisted=1 THEN 1 ELSE 0 END),0) AS shortlisted_count,
                   MAX(CASE WHEN c.selected=1 THEN c.id ELSE NULL END) AS selected_candidate_id
            FROM voice_requests r
            LEFT JOIN candidates c ON c.request_id=r.id
            WHERE r.project_id=?
            GROUP BY r.id
            ORDER BY r.position, r.rowid
            """,
            (project_id,),
        )

    def get_voice_request(self, request_id: str) -> dict[str, Any] | None:
        return self.db.fetch_one("SELECT * FROM voice_requests WHERE id=?", (request_id,))

    def delete_voice_request(self, project_id: str, request_id: str) -> tuple[str, bool]:
        request = self.db.fetch_one(
            "SELECT * FROM voice_requests WHERE id=? AND project_id=?",
            (request_id, project_id),
        )
        if not request:
            raise ValueError("请先从声线工作表选择要删除的槽位")
        preserved_asset = bool(request["voice_id"])
        self.db.execute("DELETE FROM voice_requests WHERE id=?", (request_id,))
        remaining = self.db.fetch_all(
            "SELECT id FROM voice_requests WHERE project_id=? ORDER BY position,rowid",
            (project_id,),
        )
        self.db.executemany(
            "UPDATE voice_requests SET position=?,updated_at=? WHERE id=?",
            [
                (position, utc_now(), row["id"])
                for position, row in enumerate(remaining, start=1)
            ],
        )
        detail = request["name"]
        if preserved_asset:
            detail += " · 已固化资产继续保留"
        self.db.log_activity(
            project_id,
            "voice_request",
            request_id,
            "删除声线工作表槽位",
            detail,
        )
        return request["name"], preserved_asset

    def request_candidates(self, request_id: str, limit: int = 4) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            "SELECT * FROM candidates WHERE request_id=? ORDER BY selected DESC, created_at DESC LIMIT ?",
            (request_id, limit),
        )

    def shortlisted_candidates(self, request_id: str, limit: int = 4) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            "SELECT * FROM candidates WHERE request_id=? AND shortlisted=1 ORDER BY created_at LIMIT ?",
            (request_id, limit),
        )

    def set_candidate_shortlisted(self, request_id: str, candidate_id: str, shortlisted: bool) -> None:
        candidate = self.db.fetch_one(
            "SELECT * FROM candidates WHERE id=? AND request_id=?", (candidate_id, request_id)
        )
        if not candidate:
            raise ValueError("候选声线不存在")
        if shortlisted:
            count = self.db.fetch_one(
                "SELECT COUNT(*) AS total FROM candidates WHERE request_id=? AND shortlisted=1",
                (request_id,),
            )
            if int(count["total"]) >= 4 and not candidate["shortlisted"]:
                raise ValueError("每个声线最多暂存 4 个候选，请先移出一个")
        self.db.execute(
            "UPDATE candidates SET shortlisted=? WHERE id=?",
            (1 if shortlisted else 0, candidate_id),
        )
        request = self.get_voice_request(request_id)
        self.db.log_activity(
            request["project_id"] if request else None,
            "candidate",
            candidate_id,
            "暂存候选" if shortlisted else "移出暂存",
            request["name"] if request else "",
        )

    def save_voice_request(
        self,
        request_id: str,
        project_id: str,
        name: str,
        prompt: str,
        audition_text: str,
        language: str,
    ) -> None:
        request = self.db.fetch_one(
            "SELECT * FROM voice_requests WHERE id=? AND project_id=?", (request_id, project_id)
        )
        if not request:
            raise ValueError("请先从工作表中选择一个声线需求")
        name = str(name or "")
        prompt = str(prompt or "")
        audition_text = str(audition_text or "")
        language = str(language or "Chinese")
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("声线名称不能为空")
        duplicate = self.db.fetch_one(
            "SELECT id FROM voice_requests WHERE project_id=? AND name=? AND id<>?",
            (project_id, clean_name, request_id),
        )
        if duplicate:
            raise ValueError("当前项目中已经存在同名声线")
        status = request["status"]
        if status == "draft" and prompt.strip() and audition_text.strip():
            status = "ready"
        self.db.execute(
            "UPDATE voice_requests SET name=?,prompt=?,audition_text=?,language=?,status=?,updated_at=? WHERE id=?",
            (clean_name, prompt.strip(), audition_text.strip(), language, status, utc_now(), request_id),
        )

    def request_progress(self, project_id: str) -> dict[str, int]:
        rows = self.list_voice_requests(project_id)
        return {
            "total": len(rows),
            "draft": sum(row["status"] in ("draft", "ready") for row in rows),
            "candidate": sum(row["status"] == "candidate" for row in rows),
            "selected": sum(row["status"] == "selected" for row in rows),
            "fixed": sum(row["status"] == "fixed" for row in rows),
            # A direct single-line output can coexist with a selected or fixed
            # voice.  Count the saved file instead of treating it as an
            # exclusive workflow status.
            "single": sum(bool(row["direct_output_mp3"]) for row in rows),
        }

    def activity(self, project_id: str, limit: int = 100) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            "SELECT * FROM activity_log WHERE project_id=? ORDER BY created_at DESC LIMIT ?",
            (project_id, limit),
        )

    def generate_candidates(
        self,
        project_id: str,
        name: str,
        prompt: str,
        audition_text: str,
        language: str,
        count: int,
        request_id: str | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        name = str(name or "")
        prompt = str(prompt or "")
        audition_text = str(audition_text or "")
        language = str(language or "Chinese")
        if not project_id:
            raise ValueError("请先选择项目")
        if not name.strip() or not prompt.strip() or not audition_text.strip():
            raise ValueError("声线名称、提示词和试听文本均为必填项")
        count = max(1, min(int(count), 4))
        request = None
        if request_id:
            request = self.db.fetch_one(
                "SELECT * FROM voice_requests WHERE id=? AND project_id=?", (request_id, project_id)
            )
        if request is None:
            request = self.db.fetch_one(
                "SELECT * FROM voice_requests WHERE project_id=? AND name=?",
                (project_id, name.strip()),
            )
        if request and request["status"] == "fixed":
            raise ValueError("该名称已固化为声线，请使用新名称创建其他版本")
        if request:
            request_id = request["id"]
            self.db.execute(
                """
                UPDATE voice_requests
                SET name=?,prompt=?,audition_text=?,language=?,direct_output_mp3=NULL,
                    direct_candidate_id=NULL,updated_at=?
                WHERE id=?
                """,
                (name.strip(), prompt.strip(), audition_text.strip(), language, utc_now(), request_id),
            )
        else:
            request_id = self.db.new_id()
            position_row = self.db.fetch_one(
                "SELECT COALESCE(MAX(position),0)+1 AS next_position FROM voice_requests WHERE project_id=?",
                (project_id,),
            )
            self.db.execute(
                "INSERT INTO voice_requests(id,project_id,position,name,prompt,audition_text,language,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (request_id, project_id, int(position_row["next_position"]), name.strip(), prompt.strip(), audition_text.strip(), language, "candidate", utc_now(), utc_now()),
            )

        request_dir = PROJECT_DIR / project_id / "candidates" / request_id
        request_dir.mkdir(parents=True, exist_ok=True)
        generated: list[dict[str, Any]] = []
        for _ in range(count):
            candidate_id = self.db.new_id()
            seed = self._random.randint(1, 2_147_483_647)
            waveform, sample_rate = self.models.design(audition_text.strip(), language, prompt.strip(), seed)
            wav_path = write_wav(request_dir / f"{candidate_id}.wav", waveform, sample_rate)
            mp3_path = wav_to_mp3(wav_path, request_dir / f"{candidate_id}.mp3")
            self.db.execute(
                "INSERT INTO candidates(id,request_id,seed,wav_path,preview_path,selected,shortlisted,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (candidate_id, request_id, seed, str(wav_path), str(mp3_path), 0, 0, utc_now()),
            )
            generated.append({"id": candidate_id, "wav_path": str(wav_path), "preview_path": str(mp3_path), "seed": seed})
        self.db.execute(
            "UPDATE voice_requests SET status='candidate',updated_at=? WHERE id=?",
            (utc_now(), request_id),
        )
        self.db.log_activity(
            project_id, "voice_request", request_id, "生成候选", f"{name.strip()} · 本轮 {len(generated)} 个"
        )
        return request_id, generated

    def select_candidate(self, request_id: str, candidate_id: str) -> None:
        candidate = self.db.fetch_one(
            "SELECT id FROM candidates WHERE id=? AND request_id=?", (candidate_id, request_id)
        )
        if not candidate:
            raise ValueError("候选声线不存在或不属于当前需求")
        self.db.execute("UPDATE candidates SET selected=0 WHERE request_id=?", (request_id,))
        self.db.execute("UPDATE candidates SET selected=1 WHERE id=?", (candidate_id,))
        self.db.execute(
            """
            UPDATE voice_requests
            SET status='selected',direct_output_mp3=NULL,direct_candidate_id=NULL,updated_at=?
            WHERE id=?
            """,
            (utc_now(), request_id),
        )
        request = self.get_voice_request(request_id)
        self.db.log_activity(
            request["project_id"] if request else None,
            "voice_request", request_id, "选择候选", candidate_id,
        )

    def keep_candidate_as_single_output(self, request_id: str, candidate_id: str) -> str:
        row = self.db.fetch_one(
            """
            SELECT r.*,c.preview_path
            FROM voice_requests r JOIN candidates c ON c.request_id=r.id
            WHERE r.id=? AND c.id=?
            """,
            (request_id, candidate_id),
        )
        if not row:
            raise ValueError("候选声线不存在或不属于当前槽位")
        source = Path(row["preview_path"])
        if not source.exists():
            raise ValueError("候选试听文件不存在，请重新生成候选")
        output_dir = PROJECT_DIR / row["project_id"] / "outputs" / "single_line"
        output_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{int(row['position'])}_{safe_filename(row['name'], 'voice')}_{candidate_id[:8]}.mp3"
        output_path = output_dir / filename
        shutil.copy2(source, output_path)
        # Do not alter the selected candidate here.  In particular, a fixed
        # voice must keep pointing at the candidate used to create its Base
        # prompt even when another audition is retained as a one-line output.
        next_status = row["status"]
        if next_status in ("draft", "ready", "candidate", "single"):
            next_status = "single"
        self.db.execute(
            """
            UPDATE voice_requests
            SET status=?,direct_output_mp3=?,direct_candidate_id=?,updated_at=?
            WHERE id=?
            """,
            (next_status, str(output_path), candidate_id, utc_now(), request_id),
        )
        self.db.log_activity(
            row["project_id"],
            "voice_request",
            request_id,
            "保留为单句成品",
            row["name"],
        )
        return str(output_path)

    def keep_all_selected_as_single_outputs(self, project_id: str) -> list[str]:
        """Retain each request's currently selected audition as a direct output.

        This is deliberately non-destructive: selected requests stay eligible
        for solidification and fixed requests keep their voice assets.
        """
        rows = self.db.fetch_all(
            """
            SELECT r.id AS request_id,r.name,c.id AS candidate_id
            FROM voice_requests r
            JOIN candidates c ON c.request_id=r.id AND c.selected=1
            WHERE r.project_id=?
            ORDER BY r.position,r.rowid
            """,
            (project_id,),
        )
        if not rows:
            raise ValueError("当前项目还没有已选中的候选试听")
        retained: list[str] = []
        for row in rows:
            self.keep_candidate_as_single_output(row["request_id"], row["candidate_id"])
            retained.append(row["name"])
        self.db.log_activity(
            project_id,
            "voice_request",
            None,
            "批量保留单句成品",
            f"共 {len(retained)} 个已选试听",
        )
        return retained

    def delete_single_line_output(self, project_id: str, request_id: str) -> tuple[str, bool]:
        """Delete only the retained one-line copy, preserving voice workflow data."""
        row = self.db.fetch_one(
            "SELECT * FROM voice_requests WHERE id=? AND project_id=?",
            (request_id, project_id),
        )
        if not row or not row["direct_output_mp3"]:
            raise ValueError("该单句成品不存在或已经删除")

        output_path = Path(row["direct_output_mp3"]).resolve()
        single_root = (PROJECT_DIR / project_id / "outputs" / "single_line").resolve()
        removed_file = False
        if output_path.parent == single_root and output_path.suffix.lower() == ".mp3":
            output_path.unlink(missing_ok=True)
            removed_file = True

        next_status = row["status"]
        if next_status == "single":
            selected = self.db.fetch_one(
                "SELECT id FROM candidates WHERE request_id=? AND selected=1",
                (request_id,),
            )
            next_status = "selected" if selected else "candidate"
        self.db.execute(
            """
            UPDATE voice_requests
            SET status=?,direct_output_mp3=NULL,direct_candidate_id=NULL,updated_at=?
            WHERE id=?
            """,
            (next_status, utc_now(), request_id),
        )
        self.db.log_activity(
            project_id,
            "voice_request",
            request_id,
            "删除单句成品",
            row["name"],
        )
        return row["name"], removed_file

    def single_line_outputs(self, project_id: str) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            """
            SELECT id,position,name,audition_text AS text,language,direct_output_mp3,updated_at
            FROM voice_requests
            WHERE project_id=? AND direct_output_mp3 IS NOT NULL
            ORDER BY position
            """,
            (project_id,),
        )

    def selected_requests(self, project_id: str) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            """
            SELECT r.*, c.id AS candidate_id, c.wav_path, c.preview_path
            FROM voice_requests r
            JOIN candidates c ON c.request_id=r.id AND c.selected=1
            WHERE r.project_id=? AND r.status='selected'
            ORDER BY r.created_at
            """,
            (project_id,),
        )

    def fix_selected_voices(self, project_id: str) -> list[str]:
        selected = self.selected_requests(project_id)
        if not selected:
            raise ValueError("当前项目没有待固化的已选声线")
        created_names: list[str] = []
        for row in selected:
            voice_id = self.db.new_id()
            voice_dir = VOICE_DIR / voice_id
            voice_dir.mkdir(parents=True, exist_ok=True)
            reference_wav = voice_dir / "reference.wav"
            preview_mp3 = voice_dir / "preview.mp3"
            shutil.copy2(row["wav_path"], reference_wav)
            shutil.copy2(row["preview_path"], preview_mp3)
            prompt_items = self.models.create_prompt(str(reference_wav), row["audition_text"], False)
            prompt_path = self.models.save_prompt(prompt_items, voice_dir / "prompt.safetensors")
            metadata = {
                "id": voice_id,
                "name": row["name"],
                "source": "voice_design",
                "prompt": row["prompt"],
                "language": row["language"],
                "ref_text": row["audition_text"],
                "x_vector_only": False,
            }
            (voice_dir / "metadata.json").write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            now = utc_now()
            self.db.execute(
                """
                INSERT INTO voices(id,project_id,name,source,prompt,language,ref_text,reference_wav,preview_mp3,prompt_path,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (voice_id, project_id, row["name"], "voice_design", row["prompt"], row["language"], row["audition_text"], str(reference_wav), str(preview_mp3), str(prompt_path), now, now),
            )
            self.db.execute(
                "UPDATE voice_requests SET status='fixed',voice_id=?,updated_at=? WHERE id=?",
                (voice_id, utc_now(), row["id"]),
            )
            self.db.log_activity(project_id, "voice", voice_id, "固化声线", row["name"])
            created_names.append(row["name"])
        return created_names

    def reopen_fixed_request(self, request_id: str) -> str:
        """Reopen one fixed request so it can be regenerated and fixed again.

        The previous voice asset is retained as ``replaced`` so existing batch
        items and generated files remain reproducible; only the active choice
        for this request is reopened.
        """
        request = self.db.fetch_one(
            "SELECT * FROM voice_requests WHERE id=?", (request_id,)
        )
        if not request or request["status"] != "fixed" or not request["voice_id"]:
            raise ValueError("当前声线还没有可解冻的固化版本")
        self.db.execute(
            "UPDATE voices SET status='replaced',updated_at=? WHERE id=?",
            (utc_now(), request["voice_id"]),
        )
        self.db.execute(
            "UPDATE candidates SET selected=0 WHERE request_id=?", (request_id,)
        )
        self.db.execute(
            "UPDATE voice_requests SET status='candidate',voice_id=NULL,updated_at=? WHERE id=?",
            (utc_now(), request_id),
        )
        self.db.log_activity(
            request["project_id"], "voice_request", request_id, "解冻固化声线", request["name"]
        )
        return request["name"]

    def archive_uploaded_voice(
        self,
        project_id: str | None,
        name: str,
        source_audio: str,
        ref_text: str,
        language: str,
        x_vector_only: bool,
        preview_text: str,
        tags: str = "",
        notes: str = "",
    ) -> tuple[str, str]:
        if not project_id or not self.db.fetch_one(
            "SELECT id FROM projects WHERE id=?", (project_id,)
        ):
            raise ValueError("请先选择声线归属项目")
        if not name.strip() or not source_audio:
            raise ValueError("声线名称和参考音频不能为空")
        if not x_vector_only and not ref_text.strip():
            raise ValueError("高质量克隆模式必须填写参考音频对应文字")
        if not preview_text.strip():
            raise ValueError("请填写用于确认声线的试听文本")
        voice_id = self.db.new_id()
        voice_dir = VOICE_DIR / voice_id
        voice_dir.mkdir(parents=True, exist_ok=True)
        copy_original(source_audio, voice_dir)
        reference_wav = normalize_to_wav(source_audio, voice_dir / "reference.wav")
        prompt_items = self.models.create_prompt(
            str(reference_wav), ref_text.strip() or None, x_vector_only
        )
        prompt_path = self.models.save_prompt(prompt_items, voice_dir / "prompt.safetensors")
        waveform, sample_rate = self.models.clone(
            preview_text.strip(), language, prompt_items=prompt_items
        )
        preview_wav = write_wav(voice_dir / "preview.wav", waveform, sample_rate)
        preview_mp3 = wav_to_mp3(preview_wav, voice_dir / "preview.mp3")
        preview_wav.unlink(missing_ok=True)
        metadata = {
            "id": voice_id,
            "name": name.strip(),
            "source": "upload",
            "language": language,
            "ref_text": ref_text.strip(),
            "x_vector_only": x_vector_only,
        }
        (voice_dir / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        now = utc_now()
        self.db.execute(
            """
            INSERT INTO voices(id,project_id,name,source,prompt,language,ref_text,reference_wav,preview_mp3,prompt_path,tags,notes,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (voice_id, project_id, name.strip(), "upload", None, language, ref_text.strip() or None, str(reference_wav), str(preview_mp3), str(prompt_path), tags.strip(), notes.strip(), now, now),
        )
        self.db.log_activity(project_id, "voice", voice_id, "上传并固化声线", name.strip())
        return voice_id, str(preview_mp3)

    def list_voices(self, project_id: str | None = None) -> list[dict[str, Any]]:
        if not project_id:
            return []
        return self.db.fetch_all(
            "SELECT * FROM voices WHERE project_id=? ORDER BY created_at DESC",
            (project_id,),
        )

    def voice_choices(self, project_id: str | None = None) -> list[tuple[str, str]]:
        if not project_id:
            return []
        voices = self.db.fetch_all(
            """
            SELECT * FROM voices
            WHERE project_id=? AND status='active'
            ORDER BY created_at DESC
            """,
            (project_id,),
        )
        return [(f"{row['name']} · {row['source']}", row["id"]) for row in voices]

    def generate_with_voice(
        self,
        voice_id: str,
        text: str,
        language: str,
        destination: Path,
        project_id: str | None = None,
    ) -> str:
        # Existing batch items may intentionally reference a replaced voice.
        voice = self.db.fetch_one(
            "SELECT * FROM voices WHERE id=?" + (" AND project_id=?" if project_id else ""),
            (voice_id, project_id) if project_id else (voice_id,),
        )
        if not voice:
            raise ValueError("声线不存在")
        metadata_path = Path(voice["prompt_path"]).parent / "metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        prompt_items = self.models.load_prompt(
            Path(voice["prompt_path"]),
            voice["ref_text"],
            bool(metadata.get("x_vector_only", False)),
        )
        waveform, sample_rate = self.models.clone(text.strip(), language, prompt_items=prompt_items)
        wav_path = write_wav(destination.with_suffix(".wav"), waveform, sample_rate)
        mp3_path = wav_to_mp3(wav_path, destination.with_suffix(".mp3"))
        wav_path.unlink(missing_ok=True)
        return str(mp3_path)

    def add_batch_lines(self, project_id: str, voice_id: str, language: str, pasted_text: str) -> int:
        lines = [line.strip() for line in pasted_text.splitlines() if line.strip()]
        if not lines:
            raise ValueError("没有检测到非空台词；一行应对应一个音频文件")
        voice = self.db.fetch_one(
            """
            SELECT id FROM voices
            WHERE id=? AND project_id=? AND status='active'
            """,
            (voice_id, project_id),
        )
        if not voice:
            raise ValueError("请选择当前项目中已固化的有效声线；不同项目的声线不能互相绑定")
        current = self.db.fetch_one(
            "SELECT COALESCE(MAX(position),0) AS max_position FROM batch_items WHERE project_id=?",
            (project_id,),
        )
        start = int(current["max_position"]) + 1
        now = utc_now()
        rows = [
            (self.db.new_id(), project_id, voice_id, start + index, text, language, "pending", now, now)
            for index, text in enumerate(lines)
        ]
        self.db.executemany(
            "INSERT INTO batch_items(id,project_id,voice_id,position,text,language,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            rows,
        )
        self.db.log_activity(
            project_id, "batch", None, "添加批量台词", f"声线 {voice_id[:8]} · {len(lines)} 条"
        )
        return len(lines)

    def list_batch_items(self, project_id: str) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            """
            SELECT b.*, v.name AS voice_name
            FROM batch_items b JOIN voices v ON v.id=b.voice_id
            WHERE b.project_id=? AND b.status!='deleted' ORDER BY b.position
            """,
            (project_id,),
        )

    def delete_batch_item(self, project_id: str, position: int) -> dict[str, Any]:
        item = self.db.fetch_one(
            """
            SELECT b.*,v.name AS voice_name
            FROM batch_items b JOIN voices v ON v.id=b.voice_id
            WHERE b.project_id=? AND b.position=?
            """,
            (project_id, int(position)),
        )
        if not item:
            raise ValueError("该任务不存在")
        if item["status"] == "running":
            raise ValueError("该任务正在生成，请完成或取消后再删除")
        self.db.execute("DELETE FROM batch_items WHERE id=?", (item["id"],))
        self._write_manifest(project_id)
        self.db.log_activity(
            project_id,
            "batch",
            item["id"],
            "删除批量任务",
            f"{int(item['position'])} · {item['voice_name']}",
        )
        return item

    def delete_batch_items(self, project_id: str, positions: list[int]) -> list[dict[str, Any]]:
        """Delete several text tasks while keeping already-written audio files."""
        clean_positions = sorted({int(position) for position in positions if int(position) > 0})
        if not project_id or not clean_positions:
            raise ValueError("请先输入要删除的任务序号")
        placeholders = ",".join("?" for _ in clean_positions)
        rows = self.db.fetch_all(
            f"""
            SELECT b.*,v.name AS voice_name
            FROM batch_items b JOIN voices v ON v.id=b.voice_id
            WHERE b.project_id=? AND b.position IN ({placeholders})
            ORDER BY b.position
            """,
            (project_id, *clean_positions),
        )
        found = {int(row["position"]) for row in rows}
        missing = [position for position in clean_positions if position not in found]
        if missing:
            raise ValueError(f"任务序号不存在：{', '.join(str(item) for item in missing)}")
        running = [int(row["position"]) for row in rows if row["status"] == "running"]
        if running:
            raise ValueError(f"任务正在生成，暂不能删除：{', '.join(str(item) for item in running)}")

        self.db.backup_snapshot("pre-delete-batch-items")
        ids = [(row["id"],) for row in rows]
        self.db.executemany("DELETE FROM batch_items WHERE id=?", ids)
        self._write_manifest(project_id)
        self.db.log_activity(
            project_id,
            "batch",
            None,
            "批量删除文本任务",
            f"删除 {len(rows)} 条：{', '.join(str(item) for item in clean_positions)}",
        )
        return rows

    def clear_pending_batch(self, project_id: str) -> None:
        self.db.execute("DELETE FROM batch_items WHERE project_id=? AND status IN ('pending','failed')", (project_id,))

    def delete_voice_asset(self, project_id: str, voice_id: str) -> str:
        """Delete one active project voice asset without deleting generated outputs."""
        voice = self.db.fetch_one(
            "SELECT * FROM voices WHERE id=? AND project_id=? AND status='active'",
            (voice_id, project_id),
        )
        if not voice:
            raise ValueError("请选择当前项目中的有效声线资产")
        linked = self.db.fetch_one(
            "SELECT COUNT(*) AS total FROM batch_items WHERE project_id=? AND voice_id=?",
            (project_id, voice_id),
        )
        if int(linked["total"]):
            raise ValueError("该声线仍绑定了批量任务，请先批量删除这些文本任务")

        self.db.backup_snapshot(f"pre-delete-voice-{safe_filename(voice['name'], 'voice')}")
        request_rows = self.db.fetch_all(
            "SELECT id FROM voice_requests WHERE project_id=? AND voice_id=?",
            (project_id, voice_id),
        )
        for request in request_rows:
            selected = self.db.fetch_one(
                "SELECT 1 FROM candidates WHERE request_id=? AND selected=1 LIMIT 1",
                (request["id"],),
            )
            next_status = "selected" if selected else "candidate"
            self.db.execute(
                "UPDATE voice_requests SET voice_id=NULL,status=?,updated_at=? WHERE id=?",
                (next_status, utc_now(), request["id"]),
            )
        self.db.execute("DELETE FROM voices WHERE id=? AND project_id=?", (voice_id, project_id))
        self.db.log_activity(project_id, "voice", voice_id, "删除声线资产", voice["name"])

        voice_dir = Path(voice["reference_wav"]).resolve().parent
        voices_root = VOICE_DIR.resolve()
        if voice_dir.parent == voices_root and voice_dir.name == voice_id and voice_dir.exists():
            shutil.rmtree(voice_dir)
        return voice["name"]

    def save_batch_edits(self, project_id: str, rows: list[list[Any]]) -> int:
        allowed_languages = {
            "Chinese", "English", "Japanese", "Korean", "German", "French",
            "Russian", "Portuguese", "Spanish", "Italian", "Auto",
        }
        updated = 0
        for row in rows or []:
            if len(row) < 4:
                continue
            try:
                position = int(row[0])
            except (TypeError, ValueError):
                continue
            text = str(row[2]).strip()
            language = str(row[3]).strip()
            if not text or language not in allowed_languages:
                continue
            self.db.execute(
                """
                UPDATE batch_items SET text=?,language=?,updated_at=?
                WHERE project_id=? AND position=? AND status IN ('pending','failed')
                """,
                (text, language, utc_now(), project_id, position),
            )
            updated += 1
        return updated

    def cancel_running_batch(self, project_id: str) -> None:
        self._batch_cancel.set()
        self.db.execute(
            "UPDATE batch_items SET status='pending',error='用户取消，已恢复到等待状态',updated_at=? WHERE project_id=? AND status='running'",
            (utc_now(), project_id),
        )

    def run_batch(self, project_id: str) -> dict[str, int]:
        self._batch_cancel.clear()
        project = self.db.fetch_one("SELECT * FROM projects WHERE id=?", (project_id,))
        if not project:
            raise ValueError("项目不存在")
        items = self.db.fetch_all(
            "SELECT * FROM batch_items WHERE project_id=? AND status IN ('pending','failed') ORDER BY position",
            (project_id,),
        )
        if not items:
            raise ValueError("当前项目没有待生成台词")
        output_root = PROJECT_DIR / project_id / "outputs"
        succeeded = 0
        failed = 0
        for item in items:
            if self._batch_cancel.is_set():
                break
            voice = self.db.fetch_one(
                "SELECT * FROM voices WHERE id=? AND project_id=?",
                (item["voice_id"], project_id),
            )
            if not voice:
                self.db.execute(
                    "UPDATE batch_items SET status='failed',error=?,updated_at=? WHERE id=?",
                    ("绑定声线不存在", utc_now(), item["id"]),
                )
                failed += 1
                continue
            self.db.execute(
                "UPDATE batch_items SET status='running',error=NULL,updated_at=? WHERE id=?",
                (utc_now(), item["id"]),
            )
            voice_dir = output_root / safe_filename(voice["name"], "voice")
            stem = f"{int(item['position']):04d}_{safe_filename(voice['name'], 'voice')}"
            try:
                mp3_path = self.generate_with_voice(
                    item["voice_id"], item["text"], item["language"], voice_dir / stem,
                    project_id=project_id,
                )
                self.db.execute(
                    "UPDATE batch_items SET status='completed',output_mp3=?,error=NULL,updated_at=? WHERE id=?",
                    (mp3_path, utc_now(), item["id"]),
                )
                succeeded += 1
            except Exception as exc:
                self.db.execute(
                    "UPDATE batch_items SET status='failed',error=?,updated_at=? WHERE id=?",
                    (f"{type(exc).__name__}: {exc}", utc_now(), item["id"]),
                )
                failed += 1
        self._write_manifest(project_id)
        self.db.log_activity(
            project_id, "batch", None, "批量生成完成", f"成功 {succeeded} · 失败 {failed}"
        )
        return {"succeeded": succeeded, "failed": failed}

    def regenerate_batch_item(self, project_id: str, item_id: str, text: str, language: str) -> str:
        clean_text = text.strip()
        if not clean_text:
            raise ValueError("台词不能为空")
        if language not in {
            "Chinese", "English", "Japanese", "Korean", "German", "French",
            "Russian", "Portuguese", "Spanish", "Italian", "Auto",
        }:
            raise ValueError("请选择有效语言")
        item = self.db.fetch_one(
            """
            SELECT b.*,v.name AS voice_name
            FROM batch_items b JOIN voices v ON v.id=b.voice_id
            WHERE b.id=? AND b.project_id=? AND v.project_id=?
            """,
            (item_id, project_id, project_id),
        )
        if not item:
            raise ValueError("该批量任务不存在")

        BATCH_LOGGER.info(
            "regeneration_started project=%s item=%s position=%s voice=%s text_length=%s language=%s",
            project_id,
            item_id,
            item["position"],
            item["voice_name"],
            len(clean_text),
            language,
        )

        project_output = PROJECT_DIR / project_id / "outputs"
        voice_dir = project_output / safe_filename(item["voice_name"], "voice")
        revision = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        stem = f"{int(item['position']):04d}_{safe_filename(item['voice_name'], 'voice')}_r{revision}"
        self.db.execute(
            "UPDATE batch_items SET status='running',error=NULL,updated_at=? WHERE id=?",
            (utc_now(), item_id),
        )
        try:
            mp3_path = self.generate_with_voice(
                item["voice_id"], clean_text, language, voice_dir / stem,
                project_id=project_id,
            )
        except Exception as exc:
            BATCH_LOGGER.exception(
                "regeneration_failed project=%s item=%s position=%s",
                project_id,
                item_id,
                item["position"],
            )
            self.db.execute(
                "UPDATE batch_items SET status='failed',error=?,updated_at=? WHERE id=?",
                (f"{type(exc).__name__}: {exc}", utc_now(), item_id),
            )
            raise

        self.db.execute(
            """
            UPDATE batch_items
            SET text=?,language=?,status='completed',output_mp3=?,error=NULL,updated_at=?
            WHERE id=?
            """,
            (clean_text, language, mp3_path, utc_now(), item_id),
        )
        self._write_manifest(project_id)
        self.db.log_activity(
            project_id,
            "batch",
            item_id,
            "单句重新生成",
            f"{int(item['position']):04d} · {item['voice_name']}",
        )
        BATCH_LOGGER.info(
            "regeneration_completed project=%s item=%s position=%s output=%s",
            project_id,
            item_id,
            item["position"],
            mp3_path,
        )
        return mp3_path

    def regenerate_voice_batch(self, project_id: str, voice_id: str):
        voice = self.db.fetch_one(
            "SELECT id,name FROM voices WHERE id=? AND project_id=?",
            (voice_id, project_id),
        )
        if not voice:
            raise ValueError("请选择当前项目中的有效声线")
        items = self.db.fetch_all(
            """
            SELECT id,position,text,language
            FROM batch_items
            WHERE project_id=? AND voice_id=? AND status!='deleted' AND output_mp3 IS NOT NULL
            ORDER BY position
            """,
            (project_id, voice_id),
        )
        if not items:
            raise ValueError("该人物没有可重新生成的已有台词")

        self._batch_cancel.clear()
        succeeded = 0
        failed = 0
        total = len(items)
        for index, item in enumerate(items, start=1):
            if self._batch_cancel.is_set():
                break
            error = None
            try:
                self.regenerate_batch_item(
                    project_id,
                    item["id"],
                    item["text"],
                    item["language"],
                )
                succeeded += 1
            except Exception as exc:
                failed += 1
                error = f"{type(exc).__name__}: {exc}"
            yield {
                "voice_name": voice["name"],
                "position": int(item["position"]),
                "processed": index,
                "total": total,
                "succeeded": succeeded,
                "failed": failed,
                "error": error,
                "cancelled": False,
            }

        cancelled = self._batch_cancel.is_set()
        self._write_manifest(project_id)
        self.db.log_activity(
            project_id,
            "batch",
            voice_id,
            "按人物批量重新生成",
            f"{voice['name']} · 成功 {succeeded} · 失败 {failed}"
            + (" · 已取消后续任务" if cancelled else ""),
        )
        if cancelled:
            yield {
                "voice_name": voice["name"],
                "position": None,
                "processed": succeeded + failed,
                "total": total,
                "succeeded": succeeded,
                "failed": failed,
                "error": None,
                "cancelled": True,
            }

    def start_voice_regeneration(self, project_id: str, voice_id: str) -> dict[str, Any]:
        """Start a voice-wide regeneration without holding the Gradio request open."""
        voice = self.db.fetch_one(
            "SELECT id,name FROM voices WHERE id=? AND project_id=?",
            (voice_id, project_id),
        )
        if not voice:
            raise ValueError("请选择当前项目中的有效声线")
        row = self.db.fetch_one(
            """
            SELECT COUNT(*) AS task_count
            FROM batch_items
            WHERE project_id=? AND voice_id=? AND status!='deleted' AND output_mp3 IS NOT NULL
            """,
            (project_id, voice_id),
        )
        total = int(row["task_count"] or 0) if row else 0
        if not total:
            raise ValueError("该人物没有可重新生成的已有台词")

        with self._voice_regen_lock:
            if self._voice_regen_thread and self._voice_regen_thread.is_alive():
                current = dict(self._voice_regen_progress or {})
                name = current.get("voice_name", "当前人物")
                processed = current.get("processed", 0)
                running_total = current.get("total", "?")
                raise RuntimeError(
                    f"‘{name}’的重新生成仍在运行（{processed}/{running_total}），请勿重复提交"
                )
            self._voice_regen_progress = {
                "project_id": project_id,
                "voice_id": voice_id,
                "voice_name": voice["name"],
                "position": None,
                "processed": 0,
                "total": total,
                "succeeded": 0,
                "failed": 0,
                "error": None,
                "cancelled": False,
                "done": False,
            }
            thread = threading.Thread(
                target=self._run_voice_regeneration,
                args=(project_id, voice_id),
                name=f"voice-regeneration-{voice_id[:8]}",
                daemon=True,
            )
            self._voice_regen_thread = thread
            thread.start()
            return dict(self._voice_regen_progress)

    def _run_voice_regeneration(self, project_id: str, voice_id: str) -> None:
        try:
            for progress in self.regenerate_voice_batch(project_id, voice_id):
                with self._voice_regen_lock:
                    self._voice_regen_progress = {
                        **(self._voice_regen_progress or {}),
                        **progress,
                        "project_id": project_id,
                        "voice_id": voice_id,
                        "done": False,
                    }
            with self._voice_regen_lock:
                self._voice_regen_progress = {
                    **(self._voice_regen_progress or {}),
                    "done": True,
                }
        except Exception as exc:
            with self._voice_regen_lock:
                self._voice_regen_progress = {
                    **(self._voice_regen_progress or {}),
                    "error": f"{type(exc).__name__}: {exc}",
                    "done": True,
                }
        finally:
            self.models.unload()

    def voice_regeneration_progress(self) -> dict[str, Any] | None:
        with self._voice_regen_lock:
            if self._voice_regen_progress is None:
                return None
            return dict(self._voice_regen_progress)

    @staticmethod
    def _batch_export_filename(row: dict[str, Any], excerpt_length: int) -> str:
        length = max(5, min(int(excerpt_length), 10))
        compact_text = "".join(str(row["text"]).split())
        excerpt = safe_filename(compact_text[:length], "台词")
        voice_name = safe_filename(row["voice_name"], "voice")
        position = row.get("export_position", row["position"])
        return f"{int(position)}_{voice_name}_{excerpt}.mp3"

    def _completed_export_rows(self, project_id: str) -> list[dict[str, Any]]:
        """Return batch results and retained single-line outputs in one order.

        Existing batch positions are preserved.  Direct single-line outputs are
        appended after the largest batch position so their filenames cannot
        overwrite batch files with the same voice name or text.
        """
        batch_rows: list[dict[str, Any]] = []
        for item in self.list_batch_items(project_id):
            if item["status"] != "completed" or not item["output_mp3"]:
                continue
            if not Path(item["output_mp3"]).exists():
                continue
            row = dict(item)
            row["export_position"] = int(item["position"])
            row["source"] = "batch"
            row["source_position"] = int(item["position"])
            batch_rows.append(row)

        max_batch_position = max(
            (int(row["export_position"]) for row in batch_rows), default=0
        )
        single_rows: list[dict[str, Any]] = []
        next_export_position = max_batch_position
        for item in self.single_line_outputs(project_id):
            output_mp3 = item["direct_output_mp3"]
            if not output_mp3 or not Path(output_mp3).exists():
                continue
            next_export_position += 1
            single_rows.append({
                "position": int(item["position"]),
                "export_position": next_export_position,
                "source_position": int(item["position"]),
                "source": "single",
                "voice_name": item["name"],
                "text": item["text"],
                "language": item["language"],
                "output_mp3": output_mp3,
            })
        return batch_rows + single_rows

    def export_completed_batch(
        self, project_id: str, destination: str, excerpt_length: int = 8
    ) -> tuple[str, int]:
        project = self.db.fetch_one("SELECT name FROM projects WHERE id=?", (project_id,))
        if not project:
            raise ValueError("项目不存在")
        rows = self._completed_export_rows(project_id)
        if not rows:
            raise ValueError("当前项目没有可导出的批量结果或单句成品")

        clean_destination = destination.strip()
        if not clean_destination:
            raise ValueError("请填写本机导出目录")
        output_dir = Path(clean_destination).expanduser().resolve()
        if output_dir.exists() and not output_dir.is_dir():
            raise ValueError("导出路径必须是文件夹")
        output_dir.mkdir(parents=True, exist_ok=True)

        previous_files: set[str] = set()
        existing_manifest = output_dir / "manifest.json"
        if existing_manifest.exists():
            try:
                previous_manifest = json.loads(existing_manifest.read_text(encoding="utf-8"))
                previous_files = {
                    str(entry["file"]) for entry in previous_manifest
                    if isinstance(entry, dict) and entry.get("file")
                }
            except (OSError, json.JSONDecodeError, TypeError):
                previous_files = set()

        manifest: list[dict[str, Any]] = []
        current_files: set[str] = set()
        for row in rows:
            filename = self._batch_export_filename(row, excerpt_length)
            current_files.add(filename)
            target = output_dir / filename
            source = Path(row["output_mp3"]).resolve()
            if source != target.resolve():
                shutil.copy2(source, target)
            manifest.append({
                "position": row["export_position"],
                "source": row["source"],
                "source_position": row["source_position"],
                "voice": row["voice_name"],
                "text": row["text"],
                "language": row["language"],
                "file": filename,
            })
        for stale_name in previous_files - current_files:
            stale_path = (output_dir / stale_name).resolve()
            if stale_path.parent == output_dir and stale_path.suffix.lower() == ".mp3":
                stale_path.unlink(missing_ok=True)
        (output_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self.db.set_setting(f"batch_export_dir:{project_id}", str(output_dir))
        self.db.log_activity(project_id, "batch", None, "导出全部成品", f"{len(rows)} 条 · {output_dir}")
        return str(output_dir), len(rows)

    def create_batch_archive(self, project_id: str, excerpt_length: int = 8) -> tuple[str, int]:
        project = self.db.fetch_one("SELECT name FROM projects WHERE id=?", (project_id,))
        if not project:
            raise ValueError("项目不存在")
        rows = self._completed_export_rows(project_id)
        if not rows:
            raise ValueError("当前项目没有可打包的批量结果或单句成品")
        archive_dir = PROJECT_DIR / project_id / "exports"
        archive_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_path = archive_dir / f"{safe_filename(project['name'], 'project')}_{stamp}.zip"
        manifest: list[dict[str, Any]] = []
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for row in rows:
                filename = self._batch_export_filename(row, excerpt_length)
                bundle.write(row["output_mp3"], arcname=filename)
                manifest.append({
                    "position": row["export_position"],
                    "source": row["source"],
                    "source_position": row["source_position"],
                    "voice": row["voice_name"],
                    "text": row["text"],
                    "language": row["language"],
                    "file": filename,
                })
            bundle.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        self.db.log_activity(project_id, "batch", None, "打包全部成品", f"{len(rows)} 条")
        return str(archive_path), len(rows)

    def _write_manifest(self, project_id: str) -> None:
        rows = self.list_batch_items(project_id)
        manifest = [{
            "position": row["position"],
            "voice": row["voice_name"],
            "text": row["text"],
            "language": row["language"],
            "status": row["status"],
            "file": row["output_mp3"],
            "error": row["error"],
        } for row in rows]
        path = PROJECT_DIR / project_id / "manifest.json"
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

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
