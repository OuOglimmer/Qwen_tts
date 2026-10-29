"""Batch component: script lines, batch synthesis and regeneration.

Owns the batch slice of the unified ``outputs`` table (``origin='batch'``),
the project ``manifest.json``, and the two
long-running jobs of this domain (whole-project batch, per-voice
regeneration).  Synthesis itself is delegated to the voice asset component's
:meth:`generate_with_voice`, keeping model/prompt plumbing out of here.
"""

from __future__ import annotations

import json
import logging
import shutil
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Iterator

from .config import DATA_DIR, LANGUAGES, PROJECT_DIR
from .database import Database, utc_now
from .jobs import BackgroundJob
from .model_manager import ModelManager
from .takes import demote_siblings, discard_output_files
from .voice_assets import VoiceAssetService
from .audio import safe_filename


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

ALLOWED_LANGUAGES = frozenset(LANGUAGES)

# 每条任务保留最近 N 个版本的音频，更旧的版本文件在成功生成新版本后删除。
# 取 5 是因为"重录几次才回到满意的那版"很常见，3 版太容易把最早那版剪掉。
VERSIONS_KEPT = 5

# schema v5：批量台词落在 outputs(origin='batch')。对上层仍暴露旧状态词：
#   selected  = gen_status='done' 且 in_final=1（选入成品）
#   completed = gen_status='done' 且 in_final=0
#   pending / running / failed 与 gen_status 同名
_STATUS_CASE = (
    "CASE WHEN {a}in_final=1 AND {a}gen_status='done' THEN 'selected'"
    " WHEN {a}gen_status='done' THEN 'completed'"
    " ELSE {a}gen_status END"
)

_ITEM_SELECT = f"""
SELECT b.id, b.project_id, b.voice_id, b.position, b.text, b.language,
       {_STATUS_CASE.format(a='b.')} AS status,
       b.output_mp3, b.version, b.error, b.roster_state, b.created_at, b.updated_at,
       COALESCE(r.display_name, v.name) AS voice_name,
       COALESCE(r.state, 'active') AS voice_state,
       r.replaced_by
FROM outputs b JOIN voices v ON v.id=b.voice_id
LEFT JOIN project_voices r ON r.voice_id=b.voice_id AND r.project_id=b.project_id
"""


def _record_job(
    db: Database,
    kind: str,
    project_id: str | None,
    status: str,
    total: int,
    succeeded: int,
    failed: int,
    started_at: str,
) -> None:
    """任务级事件落 jobs 表（activity_log 只留关键动作，任务流水归这里）。"""
    db.record_job(kind, project_id, status, total, succeeded, failed, started_at)


class BatchService:
    def __init__(self, database: Database, models: ModelManager, assets: VoiceAssetService) -> None:
        self.db = database
        self.models = models
        self.assets = assets
        self._batch_job = BackgroundJob("批量生成")
        self._regen_job = BackgroundJob("按人物重新生成")

    # ------------------------------------------------------------- lines

    def add_batch_lines(self, project_id: str, voice_id: str, language: str, pasted_text: str) -> int:
        lines = [line.strip() for line in pasted_text.splitlines() if line.strip()]
        if not lines:
            raise ValueError("没有检测到非空台词；一行应对应一个音频文件")
        voice = self.assets.project_voice(voice_id, project_id)
        if not voice:
            raise ValueError("请选择本项目声线清单中的有效声线；先在声线库中添加关联才能使用其他项目的声线")
        current = self.db.fetch_one(
            "SELECT COALESCE(MAX(position),0) AS max_position FROM outputs WHERE project_id=? AND origin='batch'",
            (project_id,),
        )
        start = int(current["max_position"]) + 1
        now = utc_now()
        voice_name = str(voice.get("display_name") or voice["name"])
        rows = [
            (self.db.new_id(), project_id, voice_id, voice_name, start + index, text, language, now, now)
            for index, text in enumerate(lines)
        ]
        self.db.executemany(
            """
            INSERT INTO outputs(id,project_id,voice_id,voice_name,origin,position,text,language,
                                gen_status,in_final,created_at,updated_at)
            VALUES(?,?,?,?,'batch',?,?,?,'pending',0,?,?)
            """,
            rows,
        )
        self.db.log_activity(
            project_id, "batch", None, "添加批量台词", f"声线 {voice_id[:8]} · {len(lines)} 条"
        )
        return len(lines)

    def list_batch_items(self, project_id: str) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            f"{_ITEM_SELECT} WHERE b.project_id=? AND b.origin='batch' ORDER BY b.position",
            (project_id,),
        )

    def status_counts(self, project_id: str) -> dict[str, int]:
        """Per-status task counts for the polling endpoint.

        Aggregated in SQL: /batch/status is polled every few seconds and must
        not load every row of the table just to count them.
        """
        counts = {"pending": 0, "running": 0, "completed": 0, "selected": 0, "failed": 0}
        for row in self.db.fetch_all(
            f"SELECT {_STATUS_CASE.format(a='')} AS status, COUNT(*) AS total FROM outputs "
            "WHERE project_id=? AND origin='batch' GROUP BY status",
            (project_id,),
        ):
            counts[row["status"]] = counts.get(row["status"], 0) + int(row["total"])
        return counts

    def delete_batch_item(self, project_id: str, position: int) -> dict[str, Any]:
        item = self.db.fetch_one(
            f"{_ITEM_SELECT} WHERE b.project_id=? AND b.origin='batch' AND b.position=?",
            (project_id, int(position)),
        )
        if not item:
            raise ValueError("该任务不存在")
        if item["status"] == "running":
            raise ValueError("该任务正在生成，请完成或取消后再删除")
        self.db.execute("DELETE FROM outputs WHERE id=?", (item["id"],))
        discard_output_files(project_id, [item["output_mp3"]])
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
        """Delete several text tasks together with the audio they produced."""
        clean_positions = sorted({int(position) for position in positions if int(position) > 0})
        if not project_id or not clean_positions:
            raise ValueError("请先输入要删除的任务序号")
        placeholders = ",".join("?" for _ in clean_positions)
        rows = self.db.fetch_all(
            f"{_ITEM_SELECT} WHERE b.project_id=? AND b.origin='batch' AND b.position IN ({placeholders}) ORDER BY b.position",
            (project_id, *clean_positions),
        )
        found = {int(row["position"]) for row in rows}
        missing = [position for position in clean_positions if position not in found]
        if missing:
            raise ValueError(f"任务序号不存在：{', '.join(str(item) for item in missing)}")
        running = [int(row["position"]) for row in rows if row["status"] == "running"]
        if running:
            raise ValueError(f"任务正在生成，暂不能删除：{', '.join(str(item) for item in running)}")

        # 删的是几行记录，资产目录完全不受影响：快照只要库，别把整个素材库复制一遍。
        self.db.backup_snapshot("pre-delete-batch-items", assets={})
        ids = [(row["id"],) for row in rows]
        self.db.executemany("DELETE FROM outputs WHERE id=?", ids)
        discard_output_files(project_id, [row["output_mp3"] for row in rows])
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
        stale = self.db.fetch_all(
            "SELECT output_mp3 FROM outputs WHERE project_id=? AND origin='batch' "
            "AND gen_status IN ('pending','failed') AND in_final=0",
            (project_id,),
        )
        self.db.execute(
            "DELETE FROM outputs WHERE project_id=? AND origin='batch' AND gen_status IN ('pending','failed') AND in_final=0",
            (project_id,),
        )
        discard_output_files(project_id, [row["output_mp3"] for row in stale])

    def save_batch_edits(self, project_id: str, rows: list[list[Any]]) -> int:
        updates: list[tuple[str, str, str, str, int]] = []
        for row in rows or []:
            if len(row) < 4:
                continue
            try:
                position = int(row[0])
            except (TypeError, ValueError):
                continue
            text = str(row[2]).strip()
            language = str(row[3]).strip()
            if not text or language not in ALLOWED_LANGUAGES:
                continue
            updates.append((text, language, utc_now(), project_id, position))
        if updates:
            self.db.executemany(
                """
                UPDATE outputs SET text=?,language=?,updated_at=?
                WHERE project_id=? AND position=? AND origin='batch' AND gen_status IN ('pending','failed')
                """,
                updates,
            )
        return len(updates)

    # --------------------------------------------------------- versions

    def _next_version(self, item_id: str) -> int:
        """Version number the next successful generation of this item gets."""
        row = self.db.fetch_one("SELECT version FROM outputs WHERE id=?", (item_id,))
        return int((row or {}).get("version") or 0) + 1

    def _record_version(self, item_id: str, version: int, mp3_path: str, text: str) -> None:
        """Register a successful generation as the item's current version."""
        self.db.execute(
            "INSERT OR REPLACE INTO output_versions(id,output_id,version,output_mp3,text,created_at) VALUES(?,?,?,?,?,?)",
            (self.db.new_id(), item_id, version, mp3_path, text, utc_now()),
        )
        self.db.execute(
            "UPDATE outputs SET version=?,output_mp3=? WHERE id=?",
            (version, mp3_path, item_id),
        )
        self._prune_versions(item_id)

    def _prune_versions(self, item_id: str) -> None:
        """Keep only the newest VERSIONS_KEPT audio files for this item."""
        rows = self.db.fetch_all(
            "SELECT id,output_mp3 FROM output_versions WHERE output_id=? ORDER BY version DESC",
            (item_id,),
        )
        outputs_root = PROJECT_DIR.resolve()
        for row in rows[VERSIONS_KEPT:]:
            try:
                stale = Path(row["output_mp3"]).resolve()
                # 只清理本应用产物目录内的文件，防止误删外部路径。
                if outputs_root in stale.parents:
                    stale.unlink(missing_ok=True)
            except OSError:
                pass
            self.db.execute("DELETE FROM output_versions WHERE id=?", (row["id"],))

    def version_histories(self, project_id: str) -> dict[str, list[dict[str, Any]]]:
        """Retained versions per batch item, newest first, for API display."""
        rows = self.db.fetch_all(
            """
            SELECT bv.output_id AS item_id, bv.version, bv.output_mp3, bv.created_at
            FROM output_versions bv JOIN outputs b ON b.id=bv.output_id
            WHERE b.project_id=? AND b.origin='batch' ORDER BY bv.output_id, bv.version DESC
            """,
            (project_id,),
        )
        histories: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            histories.setdefault(row["item_id"], []).append(row)
        return histories

    # ------------------------------------------------------- batch runs

    def cancel_running_batch(self, project_id: str) -> None:
        self._batch_job.request_cancel()
        self.db.execute(
            "UPDATE outputs SET gen_status='pending',error='用户取消，已恢复到等待状态',updated_at=? WHERE project_id=? AND origin='batch' AND gen_status='running'",
            (utc_now(), project_id),
        )

    def start_batch(self, project_id: str) -> dict[str, Any]:
        """Run the batch in a background thread so HTTP clients can poll."""

        def prepare() -> dict[str, Any]:
            if not project_id or not self.db.fetch_one("SELECT id FROM projects WHERE id=?", (project_id,)):
                raise ValueError("项目不存在")
            pending = self.db.fetch_one(
                "SELECT COUNT(*) AS total FROM outputs WHERE project_id=? AND origin='batch' AND gen_status IN ('pending','failed') AND in_final=0",
                (project_id,),
            )
            if not int(pending["total"] or 0):
                raise ValueError("当前项目没有待生成台词")
            return {
                "project_id": project_id,
                "total": int(pending["total"]),
                "succeeded": 0,
                "failed": 0,
                "error": None,
                "cancelled": False,
                "done": False,
            }

        return self._batch_job.start(
            prepare,
            lambda: self._run_batch_task(project_id),
            thread_name=f"batch-{project_id[:8]}",
        )

    def _run_batch_task(self, project_id: str) -> None:
        started_at = utc_now()
        try:
            result = self.run_batch(project_id)
            cancelled = self._batch_job.cancel_requested
            self._batch_job.finish(cancelled=cancelled, **result)
            _record_job(
                self.db, "batch", project_id,
                "cancelled" if cancelled else "done",
                result["succeeded"] + result["failed"], result["succeeded"], result["failed"],
                started_at,
            )
        except Exception as exc:
            self._batch_job.finish(error=f"{type(exc).__name__}: {exc}")
            _record_job(self.db, "batch", project_id, "failed", 0, 0, 1, started_at)

    def batch_task_status(self) -> dict[str, Any] | None:
        return self._batch_job.snapshot(add_running=True)

    def _record_batch_progress(self, succeeded: int, failed: int) -> None:
        """Live-update the background batch task counters while it runs."""
        snapshot = self._batch_job.snapshot()
        if snapshot and not snapshot.get("done"):
            self._batch_job.update(succeeded=succeeded, failed=failed)

    def run_batch(self, project_id: str) -> dict[str, int]:
        self._batch_job.clear_cancel()
        project = self.db.fetch_one("SELECT * FROM projects WHERE id=?", (project_id,))
        if not project:
            raise ValueError("项目不存在")
        items = self.db.fetch_all(
            "SELECT * FROM outputs WHERE project_id=? AND origin='batch' AND gen_status IN ('pending','failed') AND in_final=0 ORDER BY position",
            (project_id,),
        )
        if not items:
            raise ValueError("当前项目没有待生成台词")
        output_root = PROJECT_DIR / project_id / "outputs"
        succeeded = 0
        failed = 0
        for item in items:
            if self._batch_job.cancel_requested:
                break
            # A row deleted (or cancelled) while this batch was queued must
            # not be resurrected by the stale pre-loaded item list.
            if not self.db.fetch_one("SELECT id FROM outputs WHERE id=?", (item["id"],)):
                continue
            voice = self.db.fetch_one("SELECT * FROM voices WHERE id=?", (item["voice_id"],))
            if not voice:
                self.db.execute(
                    "UPDATE outputs SET gen_status='failed',error=?,updated_at=? WHERE id=?",
                    ("绑定声线不存在", utc_now(), item["id"]),
                )
                failed += 1
                continue
            # 产物目录/文件名用本项目清单里的展示名；已替换的历史声线无清单行，退回原名。
            roster_row = self.db.fetch_one(
                "SELECT display_name FROM project_voices WHERE project_id=? AND voice_id=?",
                (project_id, item["voice_id"]),
            )
            voice_name = roster_row["display_name"] if roster_row else voice["name"]
            self.db.execute(
                "UPDATE outputs SET gen_status='running',error=NULL,updated_at=? WHERE id=?",
                (utc_now(), item["id"]),
            )
            voice_dir = output_root / safe_filename(voice_name, "voice")
            version = self._next_version(item["id"])
            stem = f"{int(item['position']):04d}_{safe_filename(voice_name, 'voice')}_v{version:02d}"
            try:
                mp3_path = self.assets.generate_with_voice(
                    item["voice_id"], item["text"], item["language"], voice_dir / stem,
                )
                # 声线一经选定，批量产物直接计入成品；返修页仍可逐条取消选定。
                now = utc_now()
                self.db.execute(
                    "UPDATE outputs SET gen_status='done',in_final=1,output_mp3=?,voice_name=?,error=NULL,updated_at=? WHERE id=?",
                    (mp3_path, voice_name, now, item["id"]),
                )
                # 最后生成的这条算成品，同句在其它声线下录过的版本退回未选入。
                demote_siblings(self.db, project_id, item["id"])
                self._record_version(item["id"], version, mp3_path, item["text"])
                succeeded += 1
                self._record_batch_progress(succeeded, failed)
            except Exception as exc:
                self.db.execute(
                    "UPDATE outputs SET gen_status='failed',error=?,updated_at=? WHERE id=?",
                    (f"{type(exc).__name__}: {exc}", utc_now(), item["id"]),
                )
                failed += 1
                self._record_batch_progress(succeeded, failed)
        self._write_manifest(project_id)
        # 任务级事件（批量完成/成功失败计数）只落 jobs 表，不再写 activity_log。
        return {"succeeded": succeeded, "failed": failed}

    # -------------------------------------------------- selection/regen

    def select_batch_item(self, project_id: str, item_id: str, selected: bool) -> dict[str, Any]:
        """Toggle one batch result in or out of the deliverable assets.

        Batch synthesis auto-selects its results into the products (the voice
        is already chosen by the time batch runs), so this is the review-time
        escape hatch: pull a take back out of the finished outputs.  Only
        selected items count as finished for the products view and for
        exports/archives.
        """
        item = self.db.fetch_one(
            f"SELECT *, {_STATUS_CASE.format(a='')} AS status FROM outputs WHERE id=? AND project_id=? AND origin='batch'",
            (item_id, project_id),
        )
        if not item:
            raise ValueError("该批量任务不存在")
        if selected and item["status"] != "completed":
            raise ValueError("只有生成完成的结果才能选入成品")
        if not selected and item["status"] != "selected":
            raise ValueError("该结果尚未选入成品")
        next_status = "selected" if selected else "completed"
        self.db.execute(
            "UPDATE outputs SET in_final=?,updated_at=? WHERE id=?",
            (1 if selected else 0, utc_now(), item_id),
        )
        # 「最后选中的为成品」：同句的其它条（哪怕来自另一把声线）当场退出成品库，
        # 否则导出时两条都会进清单、抢同一句的位置。
        if selected:
            demote_siblings(self.db, project_id, item_id)
        self._write_manifest(project_id)
        self.db.log_activity(
            project_id,
            "batch",
            item_id,
            "选入成品" if selected else "取消选入成品",
            f"{int(item['position'])} · {str(item['text'])[:20]}",
        )
        return {**item, "status": next_status}

    def regenerate_batch_item(self, project_id: str, item_id: str, text: str, language: str) -> str:
        clean_text = text.strip()
        if not clean_text:
            raise ValueError("台词不能为空")
        if language not in ALLOWED_LANGUAGES:
            raise ValueError("请选择有效语言")
        item = self.db.fetch_one(
            f"{_ITEM_SELECT} WHERE b.id=? AND b.project_id=? AND b.origin='batch'",
            (item_id, project_id),
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
        version = self._next_version(item_id)
        stem = f"{int(item['position']):04d}_{safe_filename(item['voice_name'], 'voice')}_v{version:02d}"
        self.db.execute(
            "UPDATE outputs SET gen_status='running',error=NULL,updated_at=? WHERE id=?",
            (utc_now(), item_id),
        )
        try:
            mp3_path = self.assets.generate_with_voice(
                item["voice_id"], clean_text, language, voice_dir / stem,
            )
        except Exception as exc:
            BATCH_LOGGER.exception(
                "regeneration_failed project=%s item=%s position=%s",
                project_id,
                item_id,
                item["position"],
            )
            self.db.execute(
                "UPDATE outputs SET gen_status='failed',error=?,updated_at=? WHERE id=?",
                (f"{type(exc).__name__}: {exc}", utc_now(), item_id),
            )
            raise

        self.db.execute(
            """
            UPDATE outputs
            SET text=?,language=?,gen_status='done',in_final=1,output_mp3=?,voice_name=?,error=NULL,updated_at=?
            WHERE id=?
            """,
            (clean_text, language, mp3_path, item["voice_name"], utc_now(), item_id),
        )
        # 重录后这条算最新成交的成品：同句其它版本退回未选入。
        demote_siblings(self.db, project_id, item_id)
        self._record_version(item_id, version, mp3_path, clean_text)
        self._write_manifest(project_id)
        self.db.log_activity(
            project_id,
            "batch",
            item_id,
            "单句重新生成",
            f"{int(item['position']):04d} · {item['voice_name']} · v{version}",
        )
        BATCH_LOGGER.info(
            "regeneration_completed project=%s item=%s position=%s version=%s output=%s",
            project_id,
            item_id,
            item["position"],
            version,
            mp3_path,
        )
        return mp3_path

    def use_version(self, project_id: str, item_id: str, version: int) -> dict[str, Any]:
        """把某条任务的历史版本重新设回成品（成品页「换用这一版」）。

        语义刻意做成"复制成新的一版"，而不是让 ``output_mp3`` 直接指回旧文件：

        * ``_next_version`` 取 ``version+1``，把版本号改小之后下一次重录会撞上
          已存在的 ``(output_id, version)``，``INSERT OR REPLACE`` 当场覆盖掉
          那一版记录；
        * ``_prune_versions`` 按版本倒序删文件，两版共用一个路径时，剪掉其中
          一条会把另一条的音频一起删掉。

        代价是 ``outputs.version``（同一句的重录次数）会往上走一格：界面上
        显示成"第 N 次录音"，内容其实是第 ``version`` 版那一份。
        """
        target_version = int(version)
        item = self.db.fetch_one(
            f"{_ITEM_SELECT} WHERE b.id=? AND b.project_id=? AND b.origin='batch'",
            (item_id, project_id),
        )
        if not item:
            raise ValueError("该批量任务不存在")
        row = self.db.fetch_one(
            "SELECT version,output_mp3,text FROM output_versions WHERE output_id=? AND version=?",
            (item_id, target_version),
        )
        if not row:
            raise ValueError("该历史版本已被清理，无法换回")
        source = Path(str(row["output_mp3"]))
        if not source.exists():
            raise ValueError("该历史版本的音频文件已丢失，无法换回")

        voice_name = str(item["voice_name"])
        new_version = self._next_version(item_id)
        voice_dir = PROJECT_DIR / project_id / "outputs" / safe_filename(voice_name, "voice")
        voice_dir.mkdir(parents=True, exist_ok=True)
        stem = f"{int(item['position']):04d}_{safe_filename(voice_name, 'voice')}_v{new_version:02d}"
        restored = voice_dir / f"{stem}.mp3"
        shutil.copy2(source, restored)

        restored_text = str(row["text"]).strip() or str(item["text"])
        self.db.execute(
            """
            UPDATE outputs
            SET text=?,gen_status='done',in_final=1,output_mp3=?,error=NULL,updated_at=?
            WHERE id=?
            """,
            (restored_text, str(restored), utc_now(), item_id),
        )
        # 换用即"这一版重新成交"：同句在别的声线下录过的成品照例退出成品库。
        demote_siblings(self.db, project_id, item_id)
        self._record_version(item_id, new_version, str(restored), restored_text)
        self._write_manifest(project_id)
        self.db.log_activity(
            project_id,
            "batch",
            item_id,
            "换用历史版本",
            f"{int(item['position']):04d} · {voice_name} · v{target_version} → v{new_version}",
        )
        return {
            "id": item_id,
            "version": new_version,
            "restored_from": target_version,
            "output_mp3": str(restored),
        }

    def regenerate_voice_batch(self, project_id: str, voice_id: str) -> Iterator[dict[str, Any]]:
        voice = self.assets.project_voice(voice_id, project_id)
        if not voice:
            raise ValueError("请选择当前项目中的有效声线")
        items = self.db.fetch_all(
            """
            SELECT id,position,text,language
            FROM outputs
            WHERE project_id=? AND voice_id=? AND origin='batch' AND output_mp3 IS NOT NULL
            ORDER BY position
            """,
            (project_id, voice_id),
        )
        if not items:
            raise ValueError("该人物没有可重新生成的已有台词")

        # Voice-wide regeneration keeps its own cancel flag so a batch run
        # starting later cannot un-cancel it (and vice versa).
        self._regen_job.clear_cancel()
        succeeded = 0
        failed = 0
        total = len(items)
        for index, item in enumerate(items, start=1):
            if self._regen_job.cancel_requested:
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

        cancelled = self._regen_job.cancel_requested
        self._write_manifest(project_id)
        # 同上：按人物重生成属于任务级事件，落 jobs 表。
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
        """Start a voice-wide regeneration without holding the HTTP request open."""
        voice = self.assets.project_voice(voice_id, project_id)
        if not voice:
            raise ValueError("请选择当前项目中的有效声线")
        row = self.db.fetch_one(
            """
            SELECT COUNT(*) AS task_count
            FROM outputs
            WHERE project_id=? AND voice_id=? AND origin='batch' AND output_mp3 IS NOT NULL
            """,
            (project_id, voice_id),
        )
        total = int(row["task_count"] or 0) if row else 0
        if not total:
            raise ValueError("该人物没有可重新生成的已有台词")

        def busy_error() -> str:
            snapshot = self._regen_job.snapshot() or {}
            name = snapshot.get("voice_name", "当前人物")
            processed = snapshot.get("processed", 0)
            running_total = snapshot.get("total", "?")
            return f"‘{name}’的重新生成仍在运行（{processed}/{running_total}），请勿重复提交"

        return self._regen_job.start(
            lambda: {
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
            },
            lambda: self._run_voice_regeneration(project_id, voice_id),
            thread_name=f"voice-regeneration-{voice_id[:8]}",
            busy_error=busy_error,
        )

    def _run_voice_regeneration(self, project_id: str, voice_id: str) -> None:
        started_at = utc_now()
        succeeded = 0
        failed = 0
        try:
            for progress in self.regenerate_voice_batch(project_id, voice_id):
                self._regen_job.update(
                    **progress, project_id=project_id, voice_id=voice_id, done=False
                )
                succeeded = int(progress.get("succeeded") or 0)
                failed = int(progress.get("failed") or 0)
            cancelled = self._regen_job.cancel_requested
            self._regen_job.finish()
            _record_job(
                self.db, "regen", project_id,
                "cancelled" if cancelled else "done",
                succeeded + failed, succeeded, failed, started_at,
            )
        except Exception as exc:
            self._regen_job.finish(error=f"{type(exc).__name__}: {exc}")
            _record_job(self.db, "regen", project_id, "failed", 0, 0, 1, started_at)
        finally:
            self.models.unload()

    def voice_regeneration_progress(self) -> dict[str, Any] | None:
        return self._regen_job.snapshot()

    def cancel_voice_regeneration(self) -> bool:
        """Ask the running voice-wide regeneration to stop after this item."""
        return self._regen_job.request_cancel()

    # --------------------------------------------------------- manifest

    def _write_manifest(self, project_id: str) -> None:
        rows = self.list_batch_items(project_id)
        manifest = [{
            "position": row["position"],
            "voice": row["voice_name"],
            "text": row["text"],
            "language": row["language"],
            "status": row["status"],
            "version": row["version"],
            "file": row["output_mp3"],
            "error": row["error"],
        } for row in rows]
        path = PROJECT_DIR / project_id / "manifest.json"
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
