"""Supplement component: 补录单句（事后追加的一句台词）.

成品列表混合了三个来源：选定的批量结果、声线流程保留的单句成品，以及这里的
补录单句 —— 台词后期补充时，用任意已归档声线即录即得，插入位置由成品页的手动
排序决定。生成走 :meth:`VoiceAssetService.generate_with_voice`，同步完成；
失败时保留 failed 行（带错误信息）供用户查看/删除，而不是静默消失。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from .audio import safe_filename
from .config import LANGUAGES, PROJECT_DIR
from .database import Database, utc_now
from .takes import demote_siblings
from .voice_assets import VoiceAssetService

ALLOWED_LANGUAGES = frozenset(LANGUAGES)

# schema v5：补录单句落在 outputs(origin='supplement')；上层沿用的旧状态词
# pending / done / failed 与 gen_status 同名（done 时 in_final=1）。
_LINE_SELECT = """
SELECT s.id, s.project_id, s.voice_id, s.text, s.language, s.gen_status AS status,
       s.output_mp3, s.error, s.roster_state, s.created_at, s.updated_at,
       COALESCE(r.display_name, v.name) AS voice_name,
       COALESCE(r.state, 'active') AS voice_state
FROM outputs s JOIN voices v ON v.id=s.voice_id
LEFT JOIN project_voices r ON r.voice_id=s.voice_id AND r.project_id=s.project_id
"""


class SupplementService:
    def __init__(self, database: Database, assets: VoiceAssetService) -> None:
        self.db = database
        self.assets = assets

    def get_line(self, project_id: str, line_id: str) -> dict[str, Any] | None:
        return self.db.fetch_one(
            f"{_LINE_SELECT} WHERE s.project_id=? AND s.origin='supplement' AND s.id=?",
            (project_id, line_id),
        )

    def list_lines(self, project_id: str) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            f"{_LINE_SELECT} WHERE s.project_id=? AND s.origin='supplement' ORDER BY s.created_at, s.rowid",
            (project_id,),
        )

    def create(self, project_id: str, voice_id: str, text: str, language: str) -> dict[str, Any]:
        """Record one supplementary line and synthesize it immediately."""
        clean_text = str(text or "").strip()
        if not clean_text:
            raise ValueError("补录台词不能为空")
        language = str(language or "Chinese")
        if language not in ALLOWED_LANGUAGES:
            raise ValueError("请选择有效语言")
        voice = self.assets.project_voice(voice_id, project_id)
        if not voice:
            raise ValueError("请选择本项目声线清单中的有效声线；其他项目的声线需先添加到本项目")

        line_id = self.db.new_id()
        now = utc_now()
        position_row = self.db.fetch_one(
            "SELECT COALESCE(MAX(position),0)+1 AS next_position FROM outputs WHERE project_id=?",
            (project_id,),
        )
        self.db.execute(
            """
            INSERT INTO outputs(id,project_id,voice_id,voice_name,origin,position,text,language,
                                gen_status,in_final,created_at,updated_at)
            VALUES(?,?,?,?,'supplement',?,?,?,'pending',0,?,?)
            """,
            (
                line_id, project_id, voice_id,
                str(voice.get("display_name") or voice["name"]),
                int(position_row["next_position"]), clean_text, language, now, now,
            ),
        )

        output_root = (
            PROJECT_DIR / project_id / "outputs" / "supplement"
            / safe_filename(voice["name"], "voice")
        )
        output_root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        stem = f"{stamp}_{safe_filename(clean_text[:8], 'line')}_{line_id[:6]}"
        try:
            mp3_path = self.assets.generate_with_voice(
                voice_id, clean_text, language, output_root / stem
            )
        except Exception as exc:  # 保留失败行，让用户能在成品页看到并删除
            self.db.execute(
                "UPDATE outputs SET gen_status='failed',error=?,updated_at=? WHERE id=?",
                (f"{type(exc).__name__}: {exc}", utc_now(), line_id),
            )
            self.db.record_job("supplement", project_id, "failed", 1, 0, 1, now)
            return self.get_line(project_id, line_id)  # type: ignore[return-value]

        self.db.execute(
            "UPDATE outputs SET gen_status='done',in_final=1,output_mp3=?,version=1,error=NULL,updated_at=? WHERE id=?",
            (mp3_path, utc_now(), line_id),
        )
        # 补录即入成品库，同句的旧版本（可能是另一把声线录的）退出选入。
        demote_siblings(self.db, project_id, line_id)
        self.db.record_job("supplement", project_id, "done", 1, 1, 0, now)
        self.db.log_activity(
            project_id, "supplement", line_id, "补录单句",
            f"{voice['name']} · {clean_text[:20]}",
        )
        return self.get_line(project_id, line_id)  # type: ignore[return-value]

    def delete_line(self, project_id: str, line_id: str) -> tuple[str, bool]:
        """Delete one supplement row and, when safe, its generated mp3."""
        row = self.get_line(project_id, line_id)
        if not row:
            raise ValueError("补录单句不存在或已删除")
        removed_file = False
        if row["output_mp3"]:
            supplement_root = (PROJECT_DIR / project_id / "outputs" / "supplement").resolve()
            output_path = Path(row["output_mp3"]).resolve()
            if output_path.suffix.lower() == ".mp3" and supplement_root in output_path.parents:
                output_path.unlink(missing_ok=True)
                removed_file = True
        self.db.execute("DELETE FROM outputs WHERE id=?", (line_id,))
        self.db.log_activity(project_id, "supplement", line_id, "删除补录单句", row["voice_name"])
        return row["voice_name"], removed_file
