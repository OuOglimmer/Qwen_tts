"""Voice workflow component: the design-workflow state machine.

Owns ``voice_requests`` and ``candidates``: slots, candidate generation
(background job), shortlisting, selection, retained single-line outputs and
the fixed-voice transitions.  Generation calls into :class:`ModelManager`
directly; creating library assets (``voices`` table) is the terminal
"solidify" step of this workflow, so it stays here rather than in the asset
component.
"""

from __future__ import annotations

import json
import random
import shutil
import threading
from pathlib import Path
from typing import Any

from .audio import safe_filename, wav_duration_ms, wav_to_mp3, write_wav
from .config import PROJECT_DIR, VOICE_DIR
from .database import Database, file_md5, utc_now
from .jobs import BackgroundJob
from .model_manager import ModelManager
from .tags import TagService
from .takes import demote_siblings, discard_output_files


class VoiceWorkflowService:
    def __init__(self, database: Database, models: ModelManager) -> None:
        self.db = database
        self.models = models
        self.tags = TagService(database)
        self._random = random.SystemRandom()
        self._candidate_job = BackgroundJob("候选生成", done_key="finished")
        # Solidification reads request status then inserts assets, so two
        # concurrent calls (double-click, two tabs) would each see the same
        # requests as still 'selected' and each insert a duplicate asset.
        self._fix_lock = threading.Lock()

    # ------------------------------------------------------------ slots

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
        rows: list[tuple[Any, ...]] = []
        now = utc_now()
        for requested in requested_names[:50]:
            base = requested[:80]
            candidate = base
            suffix = 2
            while candidate in existing:
                candidate = f"{base}_{suffix}"
                suffix += 1
            request_id = self.db.new_id()
            rows.append(
                (request_id, project_id, next_position, candidate, "", "", language, "draft", now, now)
            )
            next_position += 1
            existing.add(candidate)
            created.append(candidate)
        if rows:
            self.db.executemany(
                """
                INSERT INTO voice_requests(id,project_id,position,name,prompt,audition_text,language,status,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                rows,
            )
        self.db.log_activity(
            project_id, "voice_request", None, "批量创建声线需求", f"共 {len(created)} 个：{', '.join(created)}"
        )
        return created

    def attach_library_voices(self, project_id: str, voice_ids: list[str]) -> list[dict[str, Any]]:
        """把全局声线库里已有的声线挂成项目需求单里的「已固化」槽位。

        语义：这个角色的声线已经定了——``voice_id`` 直接指向库里那条资产，不必再
        走候选试听。落库形态与 ``_fix_selected_voices_locked`` 的产物完全一致
        （``status='fixed'`` + ``voice_id`` + project_voices 一行），所以第 2 步绑定、
        第 3 步重录、进度统计都天然认得它，不需要第二套"库存声线"概念。
        """
        if not project_id:
            raise ValueError("请先选择项目")
        if not self.db.fetch_one(
            "SELECT id FROM projects WHERE id=? AND status!='deleted'", (project_id,)
        ):
            raise ValueError("项目不存在或已被删除")
        # 两张表各有一套名字唯一约束（voice_requests 是 UNIQUE(project_id,name)，
        # project_voices 是 active 行上的 display_name 唯一索引），新名字必须两边都不撞。
        request_names = {
            row["name"] for row in self.db.fetch_all(
                "SELECT name FROM voice_requests WHERE project_id=?", (project_id,)
            )
        }
        roster_names = {
            row["display_name"] for row in self.db.fetch_all(
                "SELECT display_name FROM project_voices WHERE project_id=? AND state='active'",
                (project_id,),
            )
        }
        next_position = int(self.db.fetch_one(
            "SELECT COALESCE(MAX(position),0) AS max_position FROM voice_requests WHERE project_id=?",
            (project_id,),
        )["max_position"]) + 1
        next_roster_position = int(self.db.fetch_one(
            "SELECT COALESCE(MAX(position),0) AS max_position FROM project_voices WHERE project_id=?",
            (project_id,),
        )["max_position"]) + 1
        created: list[dict[str, Any]] = []
        # 一批声线要么全挂上、要么一条都不挂：中途失败留下"需求单有、清单没有"的
        # 半套数据，第 2 步的下拉里就选不到它。
        with self.db.transaction() as connection:
            for voice_id in voice_ids:
                voice = connection.execute(
                    "SELECT * FROM voices WHERE id=? AND status='active'", (voice_id,)
                ).fetchone()
                if not voice:
                    raise ValueError("声线不存在或已被替换，无法添加到项目")
                base = str(voice["name"] or "声线").strip()[:80] or "声线"
                name = base
                suffix = 2
                while name in request_names or name in roster_names:
                    name = f"{base}_{suffix}"
                    suffix += 1
                request_id = self.db.new_id()
                now = utc_now()
                connection.execute(
                    """
                    INSERT INTO voice_requests(
                        id,project_id,position,name,prompt,audition_text,language,status,
                        candidate_round,voice_id,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,'fixed',0,?,?,?)
                    """,
                    (
                        request_id, project_id, next_position, name,
                        str(voice["prompt"] or ""), str(voice["ref_text"] or ""),
                        str(voice["language"] or "Chinese"), voice_id, now, now,
                    ),
                )
                next_position += 1
                request_names.add(name)
                # 需求单里有了，项目声线清单也得有，否则第 2 步的声线下拉里选不到它。
                roster = connection.execute(
                    "SELECT state FROM project_voices WHERE project_id=? AND voice_id=?",
                    (project_id, voice_id),
                ).fetchone()
                if roster is None:
                    connection.execute(
                        """
                        INSERT INTO project_voices(project_id,voice_id,display_name,position,state,created_at,updated_at)
                        VALUES(?,?,?,?,'active',?,?)
                        """,
                        (project_id, voice_id, name, next_roster_position, now, now),
                    )
                    next_roster_position += 1
                elif roster["state"] != "active":
                    # 换版留下的 replaced 行：重新挂载等于复活，而不是再插一条。
                    connection.execute(
                        """
                        UPDATE project_voices
                        SET state='active', display_name=?, replaced_by=NULL, updated_at=?
                        WHERE project_id=? AND voice_id=?
                        """,
                        (name, now, project_id, voice_id),
                    )
                roster_names.add(name)
                created.append({"id": request_id, "name": name, "voice_id": voice_id})
        # 活动日志不是事务的一部分：即使它写失败，也不该把已经挂好的声线回滚掉。
        for item in created:
            self.db.log_activity(project_id, "voice_request", item["id"], "从声线库添加声线", item["name"])
        return created

    def list_voice_requests(self, project_id: str) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            """
            SELECT r.*,
                   COUNT(c.id) AS candidate_count,
                   COALESCE(SUM(CASE WHEN c.shortlisted=1 THEN 1 ELSE 0 END),0) AS shortlisted_count,
                   MAX(CASE WHEN c.selected=1 THEN c.id ELSE NULL END) AS selected_candidate_id,
                   -- 单句成品（schema v5 落在 outputs.origin='single'，行 id = 槽位 id）
                   o.output_mp3 AS direct_output_mp3,
                   -- 这个槽位换掉过几把声线：表格据此决定「查看历史声线」按钮显不显示。
                   -- 新数据靠候选血缘回溯到槽位；v6 之前固化的老资产没有 source_candidate_id，
                   -- 只能按同名兜底（同项目内槽位名唯一，不会串到别的角色）。
                   (SELECT COUNT(*) FROM voices hv
                     WHERE hv.source_project_id = r.project_id
                       AND hv.id <> COALESCE(r.voice_id,'')
                       AND (hv.source_candidate_id IN (SELECT hc.id FROM candidates hc WHERE hc.request_id = r.id)
                            OR hv.name = r.name)) AS history_count
            FROM voice_requests r
            LEFT JOIN candidates c ON c.request_id=r.id
            LEFT JOIN outputs o ON o.id=r.id AND o.origin='single'
            WHERE r.project_id=?
            GROUP BY r.id
            ORDER BY r.position, r.rowid
            """,
            (project_id,),
        )

    def get_voice_request(self, request_id: str) -> dict[str, Any] | None:
        return self.db.fetch_one(
            """
            SELECT r.*, o.output_mp3 AS direct_output_mp3
            FROM voice_requests r
            LEFT JOIN outputs o ON o.id=r.id AND o.origin='single'
            WHERE r.id=?
            """,
            (request_id,),
        )

    def delete_voice_request(self, project_id: str, request_id: str) -> tuple[str, bool]:
        request = self.db.fetch_one(
            "SELECT * FROM voice_requests WHERE id=? AND project_id=?",
            (request_id, project_id),
        )
        if not request:
            raise ValueError("请先从声线工作表选择要删除的槽位")
        preserved_asset = bool(request["voice_id"])
        # 槽位删除时一并清掉它的单句成品行（outputs 没有指向槽位的外键，不删会留孤儿）。
        stale = self.db.fetch_one(
            "SELECT output_mp3 FROM outputs WHERE id=? AND origin='single'", (request_id,)
        )
        remaining = [
            row["id"] for row in self.db.fetch_all(
                "SELECT id FROM voice_requests WHERE project_id=? AND id<>? ORDER BY position,rowid",
                (project_id, request_id),
            )
        ]
        # 删行 + 重排位次必须一起成功，否则中途失败会留下断号。
        with self.db.transaction() as connection:
            connection.execute("DELETE FROM outputs WHERE id=? AND origin='single'", (request_id,))
            connection.execute("DELETE FROM voice_requests WHERE id=?", (request_id,))
            for position, row_id in enumerate(remaining, start=1):
                connection.execute(
                    "UPDATE voice_requests SET position=?,updated_at=? WHERE id=?",
                    (position, utc_now(), row_id),
                )
        if stale:
            discard_output_files(project_id, [stale["output_mp3"]])
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

    def request_candidates(self, request_id: str, limit: int = 8) -> list[dict[str, Any]]:
        # 候选栏 = 当前轮全部（暂存不把它从候选栏删掉）+ 历史轮中被移出暂存的。
        # 新生成轮在整轮结束后才推进 candidate_round，生成期间候选栏仍是上一轮。
        return self.db.fetch_all(
            "SELECT c.* FROM candidates c JOIN voice_requests r ON r.id=c.request_id "
            "WHERE c.request_id=? AND (c.round=r.candidate_round OR (c.shortlisted=0 AND c.round<r.candidate_round)) "
            "ORDER BY c.created_at, c.rowid LIMIT ?",
            (request_id, limit),
        )

    def shortlisted_candidates(self, request_id: str, limit: int = 8) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            "SELECT * FROM candidates WHERE request_id=? AND shortlisted=1 ORDER BY created_at, rowid LIMIT ?",
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
            if int(count["total"]) >= 8 and not candidate["shortlisted"]:
                raise ValueError("每个声线最多暂存 8 个候选，请先移出一个")
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

    # -------------------------------------------------------- candidates

    def _prepare_candidate_request(
        self,
        project_id: str,
        name: str,
        prompt: str,
        audition_text: str,
        language: str,
        request_id: str | None,
    ) -> str:
        """Validate inputs and create/update the voice request. Returns its id."""
        name = str(name or "")
        prompt = str(prompt or "")
        audition_text = str(audition_text or "")
        language = str(language or "Chinese")
        if not project_id:
            raise ValueError("请先选择项目")
        if not name.strip() or not prompt.strip() or not audition_text.strip():
            raise ValueError("声线名称、提示词和试听文本均为必填项")
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
            # 重新生成候选意味着旧的单句成品作废：删掉 outputs 里那一行，连音频一起收掉 ——
            # 行没了就再没有入口能找到那个文件，留着只是让 outputs/ 无限长大。
            stale = self.db.fetch_one(
                "SELECT output_mp3 FROM outputs WHERE id=? AND origin='single'", (request_id,)
            )
            self.db.execute("DELETE FROM outputs WHERE id=? AND origin='single'", (request_id,))
            if stale:
                discard_output_files(project_id, [stale["output_mp3"]])
            self.db.execute(
                """
                UPDATE voice_requests
                SET name=?,prompt=?,audition_text=?,language=?,updated_at=?
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
        return request_id

    def _next_candidate_round(self, request_id: str) -> int:
        row = self.db.fetch_one(
            "SELECT COALESCE(MAX(round),0)+1 AS next_round FROM candidates WHERE request_id=?",
            (request_id,),
        )
        return int(row["next_round"])

    def _advance_candidate_round(self, request_id: str, round_number: int) -> None:
        self.db.execute(
            "UPDATE voice_requests SET candidate_round=? WHERE id=? AND candidate_round<?",
            (round_number, request_id, round_number),
        )

    def _generate_one_candidate(
        self,
        project_id: str,
        request_id: str,
        request_dir: Path,
        prompt: str,
        audition_text: str,
        language: str,
        round_number: int,
    ) -> dict[str, Any]:
        candidate_id = self.db.new_id()
        seed = self._random.randint(1, 2_147_483_647)
        waveform, sample_rate = self.models.design(audition_text.strip(), language, prompt.strip(), seed)
        wav_path = write_wav(request_dir / f"{candidate_id}.wav", waveform, sample_rate)
        mp3_path = wav_to_mp3(wav_path, request_dir / f"{candidate_id}.mp3")
        # 血缘：这条候选是怎么生成的（模型 / seed / 时长）随候选一起落库。
        # 只靠 name 字符串对齐的话，槽位改名就把"项目→槽位→候选→声线"整条链断了。
        duration_ms = int(round(len(waveform) / float(sample_rate) * 1000)) if sample_rate else None
        params = json.dumps(
            {
                "model": "voice_design",
                "seed": seed,
                "language": language,
                "prompt": prompt.strip()[:800],
                "text": audition_text.strip()[:400],
                "duration_ms": duration_ms,
            },
            ensure_ascii=False,
        )
        self.db.execute(
            "INSERT INTO candidates(id,request_id,seed,wav_path,preview_path,selected,shortlisted,round,params,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (candidate_id, request_id, seed, str(wav_path), str(mp3_path), 0, 0, round_number, params, utc_now()),
        )
        return {"id": candidate_id, "wav_path": str(wav_path), "preview_path": str(mp3_path), "seed": seed}

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
        count = max(1, min(int(count), 8))
        request_id = self._prepare_candidate_request(
            project_id, name, prompt, audition_text, language, request_id
        )
        request_dir = PROJECT_DIR / project_id / "candidates" / request_id
        request_dir.mkdir(parents=True, exist_ok=True)
        previous_ids = self._snapshot_candidate_ids(request_id)
        round_number = self._next_candidate_round(request_id)
        generated = [
            self._generate_one_candidate(project_id, request_id, request_dir, prompt, audition_text, language, round_number)
            for _ in range(count)
        ]
        # 新一轮已落库：候选栏随之翻页，未暂存的上一轮候选就地丢弃。
        self._discard_replaced_candidates(project_id, request_id, previous_ids)
        self._advance_candidate_round(request_id, round_number)
        self.db.execute(
            "UPDATE voice_requests SET status='candidate',updated_at=? WHERE id=?",
            (utc_now(), request_id),
        )
        self.db.log_activity(
            project_id, "voice_request", request_id, "生成候选", f"{str(name).strip()} · 本轮 {len(generated)} 个"
        )
        return request_id, generated

    def start_candidate_generation(
        self,
        project_id: str,
        name: str,
        prompt: str,
        audition_text: str,
        language: str,
        count: int,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """Generate candidates in a background thread, reporting per-candidate progress."""
        count = max(1, min(int(count), 8))
        # ``prepare`` runs inside the job lock before the thread spawns, so the
        # resolved request id is ready for both the runner and the thread name.
        resolved: dict[str, str] = {}

        def prepare() -> dict[str, Any]:
            resolved["request_id"] = self._prepare_candidate_request(
                project_id, name, prompt, audition_text, language, request_id
            )
            return {
                "project_id": project_id,
                "request_id": resolved["request_id"],
                "name": str(name).strip(),
                "total": count,
                "generated": 0,
                "failed": 0,
                "error": None,
                "cancelled": False,
                "finished": False,
            }

        return self._candidate_job.start(
            prepare,
            lambda: self._run_candidate_generation(
                project_id,
                resolved["request_id"],
                str(prompt or "").strip(),
                str(audition_text or "").strip(),
                str(language or "Chinese"),
                count,
            ),
            thread_name=lambda: f"candidates-{resolved['request_id'][:8]}",
            busy_error="已有候选生成任务在运行，请等待完成或先取消",
        )

    def _run_candidate_generation(
        self,
        project_id: str,
        request_id: str,
        prompt: str,
        audition_text: str,
        language: str,
        count: int,
    ) -> None:
        started_at = utc_now()
        try:
            request_dir = PROJECT_DIR / project_id / "candidates" / request_id
            request_dir.mkdir(parents=True, exist_ok=True)
            # 本轮开跑前先记下已有候选：只有这批才是"上一轮"，本轮新生成的
            # 同样是 shortlisted=0，不能被收尾的丢弃逻辑误删。
            previous_ids = self._snapshot_candidate_ids(request_id)
            round_number = self._next_candidate_round(request_id)
            for _ in range(count):
                if self._candidate_job.cancel_requested:
                    break
                try:
                    self._generate_one_candidate(project_id, request_id, request_dir, prompt, audition_text, language, round_number)
                    snapshot = self._candidate_job.snapshot() or {}
                    self._candidate_job.update(generated=int(snapshot.get("generated", 0)) + 1)
                except Exception as exc:
                    snapshot = self._candidate_job.snapshot() or {}
                    self._candidate_job.update(
                        failed=int(snapshot.get("failed", 0)) + 1,
                        error=f"{type(exc).__name__}: {exc}",
                    )
            snapshot = self._candidate_job.snapshot() or {}
            # 只要有新候选落库就翻页；整轮取消且零产出时保留原有候选。
            if int(snapshot.get("generated", 0)) > 0:
                self._discard_replaced_candidates(project_id, request_id, previous_ids)
                # 整轮结束才把新轮换进候选栏；生成期间候选栏一直停留在上一轮。
                self._advance_candidate_round(request_id, round_number)
            self.db.execute(
                "UPDATE voice_requests SET status='candidate',updated_at=? WHERE id=?",
                (utc_now(), request_id),
            )
            self.db.log_activity(
                project_id, "voice_request", request_id, "生成候选",
                f"{snapshot.get('name', '')} · 本轮 {snapshot.get('generated', 0)} 个"
                + (" · 已取消" if self._candidate_job.cancel_requested else ""),
            )
            cancelled = self._candidate_job.cancel_requested
            self._candidate_job.finish(cancelled=cancelled)
            # 任务级事件落 jobs；"生成候选"这条流水同时保留在 activity_log（关键动作）。
            self.db.record_job(
                "candidate", project_id,
                "cancelled" if cancelled else "done",
                int(count),
                int(snapshot.get("generated", 0)),
                int(snapshot.get("failed", 0)),
                started_at,
            )
        except Exception as exc:
            self._candidate_job.finish(error=f"{type(exc).__name__}: {exc}")
            self.db.record_job("candidate", project_id, "failed", int(count), 0, 1, started_at)

    def candidate_generation_status(self) -> dict[str, Any] | None:
        return self._candidate_job.snapshot(add_running=True)

    def _snapshot_candidate_ids(self, request_id: str) -> list[str]:
        """Ids of the candidates a new round must treat as its predecessors."""
        return [
            row["id"]
            for row in self.db.fetch_all("SELECT id FROM candidates WHERE request_id=?", (request_id,))
        ]

    def _discard_replaced_candidates(self, project_id: str, request_id: str, previous_ids: list[str]) -> int:
        """Drop the previous round's candidates once a new round has landed.

        The candidate bar always reflects the newest round: only candidates
        the user shortlisted into the staging pool survive across rounds, so
        everything unshortlisted from ``previous_ids`` is deleted together
        with its audio files.  Freshly generated candidates are also
        ``shortlisted=0`` but absent from the snapshot, so the id list is
        what keeps the current round from deleting itself.  A selection
        lost this way downgrades the request back to 'candidate' so it
        cannot be solidified against a deleted audition.
        """
        if not previous_ids:
            return 0
        placeholders = ",".join("?" for _ in previous_ids)
        stale = self.db.fetch_all(
            f"SELECT id,wav_path,preview_path FROM candidates "
            f"WHERE request_id=? AND shortlisted=0 AND id IN ({placeholders})",
            (request_id, *previous_ids),
        )
        if not stale:
            return 0
        request_dir = (PROJECT_DIR / project_id / "candidates" / request_id).resolve()
        for row in stale:
            for column in ("wav_path", "preview_path"):
                try:
                    audio_file = Path(row[column]).resolve()
                except OSError:
                    continue
                if audio_file.parent == request_dir and audio_file.suffix.lower() in (".wav", ".mp3"):
                    audio_file.unlink(missing_ok=True)
        self.db.executemany("DELETE FROM candidates WHERE id=?", [(row["id"],) for row in stale])
        still_selected = self.db.fetch_one(
            "SELECT 1 FROM candidates WHERE request_id=? AND selected=1 LIMIT 1", (request_id,)
        )
        request = self.get_voice_request(request_id)
        if request and not still_selected and request["status"] == "selected":
            self.db.execute(
                "UPDATE voice_requests SET status='candidate',updated_at=? WHERE id=?",
                (utc_now(), request_id),
            )
        self.db.log_activity(
            project_id, "candidate", request_id, "丢弃上一轮候选",
            f"{request['name'] if request else request_id} · 清理 {len(stale)} 个未暂存候选",
        )
        return len(stale)

    def cancel_candidate_generation(self) -> bool:
        """Stop candidate generation after the current candidate finishes."""
        return self._candidate_job.request_cancel()

    # -------------------------------------------------- selection/output

    def select_candidate(self, request_id: str, candidate_id: str) -> None:
        request = self.db.fetch_one(
            "SELECT project_id FROM voice_requests WHERE id=?", (request_id,)
        )
        if not request:
            raise ValueError("声线槽位不存在")
        project_id = request["project_id"]
        candidate = self.db.fetch_one(
            "SELECT id FROM candidates WHERE id=? AND request_id=?", (candidate_id, request_id)
        )
        if not candidate:
            raise ValueError("候选声线不存在或不属于当前需求")
        # 换选中会把单句成品作废，对应音频也一并收掉（行没了就再也找不到它）。
        stale = self.db.fetch_one(
            "SELECT output_mp3 FROM outputs WHERE id=? AND origin='single'", (request_id,)
        )
        # 重置旧选中、写入新选中、推进槽位状态必须同生共死，中断在中间会
        # 留下"没有任何选中候选"的槽位。
        with self.db.transaction() as connection:
            connection.execute("UPDATE candidates SET selected=0 WHERE request_id=?", (request_id,))
            connection.execute("UPDATE candidates SET selected=1 WHERE id=?", (candidate_id,))
            connection.execute("DELETE FROM outputs WHERE id=? AND origin='single'", (request_id,))
            connection.execute(
                "UPDATE voice_requests SET status='selected',updated_at=? WHERE id=?",
                (utc_now(), request_id),
            )
        if stale:
            discard_output_files(project_id, [stale["output_mp3"]])
        self.db.log_activity(project_id, "voice_request", request_id, "选择候选", candidate_id)

    def deselect_candidate(self, request_id: str) -> str:
        """取消选定：清掉该槽位的选中候选，槽位退回还没选定任何候选的状态。

        点错了不必再选另一个候选来"覆盖"，直接取消即可。已经固化的槽位不允许：
        那等于把正在生效的声线从清单里抽走，必须先在声线槽位表里「解冻」。
        ``voice_id`` 一律不动（与 select_candidate 一致）—— 它记的是"这一槽位曾经固化过
        哪把声线"，重新固化前的覆盖确认靠它判断。
        """
        request = self.db.fetch_one(
            "SELECT project_id,status FROM voice_requests WHERE id=?", (request_id,)
        )
        if not request:
            raise ValueError("声线槽位不存在")
        if request["status"] == "fixed":
            raise ValueError("该槽位已固化声线，请先在声线槽位表里点「解冻」再取消选定")
        # 还有候选就退回 candidate，否则退回 ready（prompt 与试听文本都已填好）。
        has_candidates = self.db.fetch_one(
            "SELECT 1 FROM candidates WHERE request_id=? LIMIT 1", (request_id,)
        )
        status = "candidate" if has_candidates else "ready"
        with self.db.transaction() as connection:
            connection.execute("UPDATE candidates SET selected=0 WHERE request_id=?", (request_id,))
            connection.execute(
                "UPDATE voice_requests SET status=?,updated_at=? WHERE id=?",
                (status, utc_now(), request_id),
            )
        self.db.log_activity(request["project_id"], "voice_request", request_id, "取消选定候选", "")
        return status

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
        # schema v5：单句成品是 outputs 里的一行（id = 槽位 id），重复保留即覆盖该行。
        previous = self.db.fetch_one(
            "SELECT output_mp3 FROM outputs WHERE id=? AND origin='single'", (request_id,)
        )
        if previous and previous["output_mp3"] and Path(previous["output_mp3"]) != output_path:
            stale = Path(str(previous["output_mp3"])).resolve()
            if output_dir.resolve() in stale.parents:
                stale.unlink(missing_ok=True)
        now = utc_now()
        position_row = self.db.fetch_one(
            "SELECT COALESCE(MAX(position),0)+1 AS next_position FROM outputs WHERE project_id=?",
            (row["project_id"],),
        )
        self.db.execute(
            """
            INSERT INTO outputs(id,project_id,voice_id,voice_name,origin,position,text,language,
                                gen_status,in_final,output_mp3,version,created_at,updated_at)
            VALUES(?,?,?,?,'single',?,?,?,'done',1,?,1,?,?)
            ON CONFLICT(id) DO UPDATE SET
                voice_id=excluded.voice_id, voice_name=excluded.voice_name,
                text=excluded.text, language=excluded.language,
                output_mp3=excluded.output_mp3, updated_at=excluded.updated_at
            """,
            (
                request_id, row["project_id"], row["voice_id"], row["name"],
                int(position_row["next_position"]), row["audition_text"], row["language"],
                str(output_path), now, now,
            ),
        )
        # 单句成品一生成就进成品库，同句在别处录过的版本退出选入。
        demote_siblings(self.db, row["project_id"], request_id)
        # 旧逻辑把 status 写成 'single'；v5 起该枚举只作历史兼容，槽位状态不动
        # （界面靠 direct_output_mp3 判断是否有单句成品）。
        if row["status"] == "single":
            self.db.execute(
                "UPDATE voice_requests SET status='candidate',updated_at=? WHERE id=?",
                (now, request_id),
            )
        else:
            self.db.execute(
                "UPDATE voice_requests SET updated_at=? WHERE id=?", (now, request_id)
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
            """
            SELECT r.*, o.output_mp3 AS direct_output_mp3
            FROM voice_requests r
            LEFT JOIN outputs o ON o.id=r.id AND o.origin='single'
            WHERE r.id=? AND r.project_id=?
            """,
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
        with self.db.transaction() as connection:
            connection.execute("DELETE FROM outputs WHERE id=? AND origin='single'", (request_id,))
            connection.execute(
                "UPDATE voice_requests SET status=?,updated_at=? WHERE id=?",
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
        """保留下来的单句成品（schema v5：outputs.origin='single'）。

        带上清单里的 ``replaced_by``：成品页要能对"生成时用的声线已换版"的行给出
        「换绑新版」，没有这个指向就只能干看着。
        """
        return self.db.fetch_all(
            """
            SELECT o.id,o.position,o.voice_id,o.voice_name AS name,o.text,o.language,
                   o.output_mp3 AS direct_output_mp3,o.roster_state,o.updated_at,
                   r.replaced_by
            FROM outputs o
            LEFT JOIN project_voices r
                   ON r.voice_id=o.voice_id AND r.project_id=o.project_id
            WHERE o.project_id=? AND o.origin='single' AND o.output_mp3 IS NOT NULL
            ORDER BY o.position
            """,
            (project_id,),
        )

    def selected_requests(self, project_id: str, request_id: str | None = None) -> list[dict[str, Any]]:
        if request_id:
            return self.db.fetch_all(
                """
                SELECT r.*, c.id AS candidate_id, c.wav_path, c.preview_path, c.seed AS seed
                FROM voice_requests r
                JOIN candidates c ON c.request_id=r.id AND c.selected=1
                WHERE r.project_id=? AND r.id=? AND r.status='selected'
                ORDER BY r.created_at
                """,
                (project_id, request_id),
            )
        return self.db.fetch_all(
            """
            SELECT r.*, c.id AS candidate_id, c.wav_path, c.preview_path, c.seed AS seed
            FROM voice_requests r
            JOIN candidates c ON c.request_id=r.id AND c.selected=1
            WHERE r.project_id=? AND r.status='selected'
            ORDER BY r.created_at
            """,
            (project_id,),
        )

    # ------------------------------------------------------ solidification

    def fix_selected_voices(self, project_id: str, request_id: str | None = None) -> list[str]:
        # Serialize the whole read-modify-insert pass: a concurrent second
        # call then sees status='fixed' and fails loudly instead of
        # inserting another copy of the same voice.
        with self._fix_lock:
            return self._fix_selected_voices_locked(project_id, request_id)

    def _fix_selected_voices_locked(self, project_id: str, request_id: str | None = None) -> list[str]:
        selected = self.selected_requests(project_id, request_id)
        if not selected:
            if request_id:
                raise ValueError("当前槽位还没有可固化的已选候选")
            raise ValueError("当前项目没有待固化的已选声线")
        # 项目内声线名唯一是硬边界：导入的声线可能已占用同名，固化前统一拦截，
        # 避免清单写入时才失败留下半成品资产。只和 active 行比 —— 换版留下的
        # replaced 行必然与新版同名，拿它来拦就等于把"重新固化"这条路堵死。
        for row in selected:
            clash = self.db.fetch_one(
                """
                SELECT voice_id FROM project_voices
                WHERE project_id=? AND display_name=? AND state='active' AND voice_id IS NOT ?
                """,
                (project_id, row["name"], row["voice_id"]),
            )
            if clash:
                raise ValueError(
                    f"本项目声线清单已有同名声线「{row['name']}」，请先在声线工作表改用其他名称"
                )
        position_row = self.db.fetch_one(
            "SELECT COALESCE(MAX(position),0) AS max_position FROM project_voices WHERE project_id=?",
            (project_id,),
        )
        next_position = int(position_row["max_position"]) + 1
        created_names: list[str] = []
        for row in selected:
            voice_id = self.db.new_id()
            now = utc_now()
            voice_dir = VOICE_DIR / voice_id
            voice_dir.mkdir(parents=True, exist_ok=True)
            reference_wav = voice_dir / "reference.wav"
            preview_mp3 = voice_dir / "preview.mp3"
            try:
                shutil.copy2(row["wav_path"], reference_wav)
                shutil.copy2(row["preview_path"], preview_mp3)
                # 建 prompt（GPU）是最慢、最容易失败的一步：放在任何写库动作之前，
                # 失败时这一条还没落库，只要收掉刚建的目录就能干净退出。
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
                    # 血缘（schema v6）：从哪个槽位、哪条候选、哪个 seed 来的
                    "request_id": row["id"],
                    "candidate_id": row.get("candidate_id"),
                    "seed": row.get("seed"),
                }
                (voice_dir / "metadata.json").write_text(
                    json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                # 一条声线的全部写库动作同事务：换版标记、资产入库、清单、槽位状态、
                # 标签继承。中途任何一步失败都整体回滚，不会留下"资产在库里、
                # 清单里没有、槽位仍是 selected"的半成品。
                with self.db.transaction() as connection:
                    # Re-fixing a request (after re-selecting a candidate) must retire
                    # the previous asset, otherwise the library accumulates duplicates.
                    if row["voice_id"]:
                        connection.execute(
                            "UPDATE voices SET status='replaced',updated_at=? WHERE id=? AND status='active'",
                            (now, row["voice_id"]),
                        )
                        # 换版 = 状态迁移，不是删除：旧清单行留成 replaced 并指向新版，
                        # 历史成品才解释得清"这条音频当时用的是谁"，也才删得掉。
                        connection.execute(
                            """
                            UPDATE project_voices
                            SET state='replaced', replaced_by=?, updated_at=?
                            WHERE project_id=? AND voice_id=?
                            """,
                            (voice_id, now, project_id, row["voice_id"]),
                        )
                    # ref_language / tags / notes 显式写空，不再靠列默认值：设计声线没有
                    # "参考音频"可标语种，而 voice_requests 槽位本身不携带标签与备注。
                    # 标签在下面从槽位继承（inherit_on_solidify），不走这条 INSERT。
                    connection.execute(
                        """
                        INSERT INTO voices(id,source_project_id,name,source,prompt,language,ref_language,ref_text,
                                           reference_wav,preview_mp3,prompt_path,reference_hash,tags,notes,
                                           source_candidate_id,reference_ms,created_at,updated_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            voice_id, project_id, row["name"], "voice_design", row["prompt"], row["language"],
                            "", row["audition_text"], str(reference_wav), str(preview_mp3), str(prompt_path),
                            file_md5(reference_wav), "", "",
                            row.get("candidate_id"), wav_duration_ms(reference_wav), now, now,
                        ),
                    )
                    connection.execute(
                        """
                        INSERT INTO project_voices(project_id,voice_id,display_name,position,state,created_at,updated_at)
                        VALUES(?,?,?,?,'active',?,?)
                        """,
                        (project_id, voice_id, row["name"], next_position, now, now),
                    )
                    # 解冻后 voice_requests.voice_id 已置空，换版关系只剩清单里的 replaced 行：
                    # 同名旧行补上 replaced_by，换版链路才接得回新版。
                    connection.execute(
                        """
                        UPDATE project_voices SET replaced_by=?, updated_at=?
                        WHERE project_id=? AND display_name=? AND state='replaced' AND replaced_by IS NULL
                        """,
                        (voice_id, now, project_id, row["name"]),
                    )
                    connection.execute(
                        "UPDATE voice_requests SET status='fixed',voice_id=?,updated_at=? WHERE id=?",
                        (voice_id, now, row["id"]),
                    )
                    # 槽位标签按域上移：voice 域进 voice_tags、project 域进 project_voice_tags，
                    # request 域（优先级）留在槽位。与固化同事务，避免半套标签。
                    self.tags.inherit_on_solidify(connection, row["id"], project_id, voice_id)
            except BaseException:
                # 事务已回滚，磁盘上刚建的目录也要一起收掉，否则下次同 id 复用会撞上残件。
                shutil.rmtree(voice_dir, ignore_errors=True)
                raise
            next_position += 1
            self.tags.sync_voice_cache(voice_id)
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
        # 解冻同样走状态迁移：清单行留成 replaced，历史成品继续找得到归属。
        self.db.execute(
            """
            UPDATE project_voices SET state='replaced', updated_at=?
            WHERE project_id=? AND voice_id=? AND state='active'
            """,
            (utc_now(), request["project_id"], request["voice_id"]),
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

    def voice_request_history(self, project_id: str, request_id: str) -> list[dict[str, Any]]:
        """这个槽位用过、后来被换掉的声线。

        归属判据和 ``history_count`` 完全一致：``source_candidate_id`` 回溯到槽位的候选
        是最硬的证据（改名也不受影响）；v6 之前固化的老资产没有候选血缘，按同名兜底。
        ``bound_outputs`` 交给前端决定删除按钮能不能点 —— 还有成品挂着的旧声线一旦删掉，
        那些成品的「当时用的是哪把声音」就永久失去了归属。
        """
        request = self.db.fetch_one(
            "SELECT * FROM voice_requests WHERE id=? AND project_id=?", (request_id, project_id)
        )
        if not request:
            raise ValueError("槽位不存在或已被删除")
        return self.db.fetch_all(
            """
            SELECT v.id, v.name, v.status, v.source, v.language, v.prompt, v.notes,
                   v.preview_mp3, v.reference_wav, v.created_at,
                   (SELECT COUNT(*) FROM outputs o WHERE o.voice_id = v.id) AS bound_outputs
            FROM voices v
            WHERE v.source_project_id = ?
              AND v.id <> COALESCE(?,'')
              AND (v.source_candidate_id IN (SELECT c.id FROM candidates c WHERE c.request_id = ?)
                   OR v.name = ?)
            ORDER BY v.created_at DESC
            """,
            (project_id, request["voice_id"], request_id, request["name"]),
        )
