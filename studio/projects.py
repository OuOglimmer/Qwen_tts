"""Project component: project lifecycle, summaries and integrity checks.

Owns the ``projects`` table and the persisted last-project setting.  Deleting
a project is the one place allowed to purge every project-owned row across
other components' tables, because it is a project-lifecycle decision.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .config import PROJECT_DIR, VOICE_DIR
from .database import Database, utc_now
from .project_context import ProjectContext
from .audio import safe_filename


class ProjectService:
    def __init__(self, database: Database) -> None:
        self.db = database

    def create_project(self, name: str) -> str:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("项目名称不能为空")
        existing = self.db.fetch_one("SELECT id,status FROM projects WHERE name=?", (clean_name,))
        if existing:
            if existing["status"] == "deleted":
                raise ValueError("已删除的项目仍占用该名称，请更换项目名称")
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

    def ensure_default_project(self) -> str:
        """启动自举：保证库里至少有一个可用项目。

        ``create_project`` 有意不允许复用软删项目的名字（``projects.name`` 是
        UNIQUE，复活一条 deleted 行会把它的旧数据一起带回来），所以「默认项目」
        这个名称可能正被一条 deleted 行占着。这里逐个试到不撞名为止，而不是把
        ValueError 抛进 ``StudioService.__init__`` —— 那会让整个后端起不来。
        """
        live = self.list_projects()
        if live:
            return live[0]["id"]
        for index in range(1, 100):
            name = "默认项目" if index == 1 else f"默认项目 {index}"
            try:
                return self.create_project(name)
            except ValueError:
                continue
        raise RuntimeError("无法创建默认项目：默认项目相关的名称已被占用")

    def list_projects(self) -> list[dict[str, Any]]:
        return self.db.fetch_all("SELECT * FROM projects WHERE status!='deleted' ORDER BY created_at DESC")

    def project_exists(self, project_id: str) -> bool:
        return bool(self.db.fetch_one("SELECT id FROM projects WHERE id=?", (project_id,)))

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        return self.db.fetch_one("SELECT * FROM projects WHERE id=?", (project_id,))

    def _project_choices(self) -> list[tuple[str, str]]:
        """(显示名, id) 形态的项目列表。曾供已下线的 Gradio 下拉框使用，现仅内部复用。"""
        return [(row["name"], row["id"]) for row in self.list_projects()]

    def project_summaries(self, include_archived: bool = False) -> list[dict[str, Any]]:
        where = "" if include_archived else "WHERE p.status!='deleted'"
        rows = self.db.fetch_all(
            f"""
            SELECT p.*,
                   (SELECT COUNT(*) FROM voice_requests r WHERE r.project_id=p.id) AS slot_count,
                   (SELECT COUNT(*) FROM project_voices rv JOIN voices v ON v.id=rv.voice_id
                    WHERE rv.project_id=p.id AND v.status='active' AND rv.state='active') AS voice_count,
                   (SELECT COUNT(*) FROM outputs o
                    WHERE o.project_id=p.id AND o.origin='single' AND o.output_mp3 IS NOT NULL) AS single_count,
                   (SELECT COUNT(*) FROM outputs o
                    WHERE o.project_id=p.id AND o.origin='batch') AS task_count,
                   (SELECT COUNT(*) FROM outputs o
                    WHERE o.project_id=p.id AND o.origin='batch' AND o.gen_status='done') AS completed_count,
                   MAX(
                       p.updated_at,
                       COALESCE((SELECT MAX(r.updated_at) FROM voice_requests r WHERE r.project_id=p.id), p.updated_at),
                       COALESCE((SELECT MAX(v.updated_at) FROM voices v WHERE v.source_project_id=p.id), p.updated_at),
                       COALESCE((SELECT MAX(o.updated_at) FROM outputs o WHERE o.project_id=p.id), p.updated_at)
                   ) AS last_activity
            FROM projects p
            {where}
            ORDER BY last_activity DESC,p.created_at DESC
            """
        )
        # 总览页需要落回上次离开的步骤；一次性批量解析，避免每行一次查询。
        last_steps = self.resolve_last_steps([row["id"] for row in rows])
        for row in rows:
            row["last_step"] = last_steps.get(row["id"], 1)
        return rows

    def rename_project(self, project_id: str, name: str) -> str:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("项目名称不能为空")
        project = self.db.fetch_one("SELECT id,name FROM projects WHERE id=?", (project_id,))
        if not project:
            raise ValueError("项目不存在或已被删除")
        duplicate = self.db.fetch_one(
            "SELECT id FROM projects WHERE name=? AND id<>?", (clean_name, project_id)
        )
        if duplicate:
            raise ValueError("已存在同名项目")
        self.db.execute(
            "UPDATE projects SET name=?,updated_at=? WHERE id=?",
            (clean_name, utc_now(), project_id),
        )
        self.db.log_activity(project_id, "project", project_id, "重命名项目", f"{project['name']} → {clean_name}")
        return clean_name

    def archive_project(self, project_id: str) -> str:
        """Soft delete: hide from navigation, keep voices, tasks and files intact."""
        project = self.db.fetch_one("SELECT id,name FROM projects WHERE id=?", (project_id,))
        if not project:
            raise ValueError("项目不存在或已被删除")
        self.db.execute(
            "UPDATE projects SET status='deleted',updated_at=? WHERE id=?",
            (utc_now(), project_id),
        )
        if self.db.get_setting("last_project_id") == project_id:
            remaining = self._project_choices()
            if remaining:
                self.db.set_setting("last_project_id", remaining[0][1])
        # 软删不动任何文件，快照只需要库本身；assets={} 避免为了删一个项目
        # 把整个素材目录再复制一份。
        self.db.backup_snapshot(f"pre-archive-{safe_filename(project['name'], 'project')}", assets={})
        self.db.log_activity(project_id, "project", project_id, "删除项目（保留声线）", project["name"])
        return project["name"]

    def purge_project(self, project_id: str) -> dict[str, Any]:
        """Hard delete: remove the project row and its artifacts.

        Voice assets shared with other live projects (or still referenced by
        their tasks) are force-retained and reported back, so a purge never
        silently destroys a sound that somebody else is still using.
        """
        project = self.db.fetch_one("SELECT id,name FROM projects WHERE id=?", (project_id,))
        if not project:
            raise ValueError("项目不存在或已被删除")
        project_root = (PROJECT_DIR / project_id).resolve()
        projects_root = PROJECT_DIR.resolve()
        if project_root.parent != projects_root:
            raise ValueError("项目目录校验失败，已停止删除")

        # Which of this project's owned assets may go? A voice stays when
        # another live project lists it, or any project still has tasks bound
        # to it (FK RESTRICT would refuse the delete anyway).
        owned = self.db.fetch_all(
            "SELECT id,name FROM voices WHERE source_project_id=?", (project_id,)
        )
        retained: list[str] = []
        delete_ids: list[str] = []
        for voice in owned:
            shared = self.db.fetch_all(
                """
                SELECT r.project_id FROM project_voices r
                JOIN projects p ON p.id=r.project_id AND p.status!='deleted'
                WHERE r.voice_id=? AND r.project_id<>? AND r.state='active'
                UNION
                SELECT o.project_id FROM outputs o
                WHERE o.voice_id=? AND o.project_id<>?
                """,
                (voice["id"], project_id, voice["id"], project_id),
            )
            if shared:
                retained.append(voice["name"])
            else:
                delete_ids.append(voice["id"])
        # 只备份这次真会被删掉的那些目录：彻底删项目本来就可能带走几个 GB 的音频，
        # 再把整个素材库复制一遍毫无收益。
        voices_root = VOICE_DIR.resolve()
        assets: dict[str, Path] = {}
        if project_root.exists():
            assets[f"projects/{project_id}"] = project_root
        for voice_id in delete_ids:
            voice_dir = voices_root / voice_id
            if voice_dir.is_dir():
                assets[f"voices/{voice_id}"] = voice_dir
        self.db.backup_snapshot(f"pre-delete-project-{safe_filename(project['name'])}", assets=assets)

        with self.db.transaction() as connection:
            # 产物全部在 outputs（批量/补录/单句三源），删项目前先清掉它们对声线的 RESTRICT。
            connection.execute("DELETE FROM outputs WHERE project_id=?", (project_id,))
            if delete_ids:
                connection.executemany("DELETE FROM voices WHERE id=?", [(voice_id,) for voice_id in delete_ids])
            connection.execute("DELETE FROM projects WHERE id=?", (project_id,))
            connection.execute("DELETE FROM app_settings WHERE key LIKE ?", (f"%:{project_id}",))
            connection.execute(
                "DELETE FROM app_settings WHERE key='last_project_id' AND value=?", (project_id,)
            )

        if project_root.exists():
            shutil.rmtree(project_root)
        for voice in owned:
            if voice["id"] not in delete_ids:
                continue
            # 物理文件与数据库行同步清理；保留声线的目录原样不动。
            voice_dir = voices_root / voice["id"]
            if voice_dir.is_dir():
                shutil.rmtree(voice_dir, ignore_errors=True)
        remaining = self._project_choices()
        if remaining:
            self.db.set_setting("last_project_id", remaining[0][1])
        self.db.log_activity(
            None, "project", project_id, "彻底删除项目",
            f"{project['name']} · 保留被其他项目使用的声线 {len(retained)} 个" if retained else project["name"],
        )
        return {"name": project["name"], "retained_voices": retained}

    # ---------------------------------------------------------- last step

    def set_last_step(self, project_id: str, step: int) -> None:
        """空操作：schema v5 起步次只由数据推断，不再存 ``last_step:<id>`` 键。

        接口保留是为了不破坏仍在调用 ``PUT /last-step`` 的旧前端；校验依旧做，
        只是不再写库。
        """
        step = int(step)
        if step < 1 or step > 4:
            raise ValueError("步骤编号超出范围")
        if not self.project_exists(project_id):
            raise ValueError("项目不存在或已被删除")

    def infer_last_step(self, project_id: str) -> int:
        counts = self.db.fetch_one(
            """
            SELECT
                (SELECT COUNT(*) FROM project_voices rv JOIN voices v ON v.id=rv.voice_id
                 WHERE rv.project_id=? AND v.status='active' AND rv.state='active') AS voices,
                (SELECT COUNT(*) FROM outputs
                 WHERE project_id=? AND origin='batch') AS tasks,
                (SELECT COUNT(*) FROM outputs
                 WHERE project_id=? AND origin='batch' AND gen_status='done') AS done
            """,
            (project_id, project_id, project_id),
        )
        if not counts or int(counts["voices"]) == 0:
            return 1
        if int(counts["tasks"]) == 0:
            return 2
        if int(counts["done"]) < int(counts["tasks"]):
            return 3
        return 4

    def resolve_last_step(self, project_id: str) -> int:
        return self.resolve_last_steps([project_id]).get(project_id, 1)

    def resolve_last_steps(self, project_ids: list[str]) -> dict[str, int]:
        """Batch version of :meth:`resolve_last_step` — one query for N projects.

        The per-project version issued one (sometimes three) queries per row, so
        the overview page spent over a second on a dozen projects.  schema v5
        起不再存 ``last_step:<id>``：步次一律按数据推断（见 ``infer_last_step``），
        ``set_last_step`` 保留为空操作，只为兼容仍在调用它的旧前端。
        """
        if not project_ids:
            return {}
        stored: dict[str, int] = {}
        pending = list(project_ids)
        if pending:
            marks = ",".join("?" for _ in pending)
            # 分数聚合改成一次 GROUP BY，替代原来每个项目两次子查询。
            counts = self.db.fetch_all(
                f"""
                SELECT p.id,
                       COALESCE(v.total, 0) AS voices,
                       COALESCE(t.total, 0) AS tasks,
                       COALESCE(t.done, 0) AS done
                FROM projects p
                LEFT JOIN (
                    SELECT rv.project_id, COUNT(*) AS total
                    FROM project_voices rv JOIN voices v ON v.id=rv.voice_id
                    WHERE v.status='active' AND rv.state='active' GROUP BY rv.project_id
                ) v ON v.project_id=p.id
                LEFT JOIN (
                    SELECT project_id, COUNT(*) AS total,
                           SUM(CASE WHEN gen_status='done' THEN 1 ELSE 0 END) AS done
                    FROM outputs WHERE origin='batch' GROUP BY project_id
                ) t ON t.project_id=p.id
                WHERE p.id IN ({marks})
                """,
                tuple(pending),
            )
            for row in counts:
                voices = int(row["voices"])
                tasks = int(row["tasks"])
                done = int(row["done"])
                if voices == 0:
                    stored[row["id"]] = 1
                elif tasks == 0:
                    stored[row["id"]] = 2
                elif done < tasks:
                    stored[row["id"]] = 3
                else:
                    stored[row["id"]] = 4

        return {project_id: stored.get(project_id, 1) for project_id in project_ids}

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
        if project["status"] == "deleted":
            raise ValueError("该项目已删除，如需继续使用请恢复数据或新建项目")
        self.db.set_setting("last_project_id", project["id"])
        return ProjectContext(project["id"], project["name"], project["status"])

    def validate_data_integrity(self, project_id: str | None = None) -> list[str]:
        """Report missing asset paths without changing any user records."""
        params = (project_id,) if project_id else ()
        where_r = "WHERE r.project_id=?" if project_id else ""
        where_v = "WHERE v.source_project_id=?" if project_id else ""
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
        for row in self.db.fetch_all(f"SELECT b.id,b.output_mp3 FROM outputs b {where_b}", params):
            for field in ("output_mp3",):
                if row[field] and not Path(row[field]).exists():
                    issues.append(f"batch:{row['id'][:8]} {field} 不存在")
        return issues
