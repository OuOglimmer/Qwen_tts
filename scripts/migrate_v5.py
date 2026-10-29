"""schema v4 -> v5 幂等迁移脚本（独立一次性，不引入第三方框架）。

设计依据：design/database-design-final.md（11 条已定决策）与 database-design.md §5。

用法：
    python scripts/migrate_v5.py                 # 迁移真实库（默认先备份快照）
    python scripts/migrate_v5.py --db PATH       # 迁移指定库文件
    python scripts/migrate_v5.py --db PATH --no-backup   # 副本演练时跳过备份

保障：
  * 全程 PRAGMA foreign_keys=OFF + 单事务，提交后 foreign_key_check 校验；
  * 每一步都按"对象是否存在 / 列是否存在 / 行是否已搬"守卫，重复执行无副作用；
  * 旧表 batch_items / supplement_lines / batch_item_versions 迁移后转为
    同名只读视图（兼容期），旧代码的读取不受影响；写入需等业务代码切到新表。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from studio.config import DB_PATH  # noqa: E402

NEW_TABLES_DDL = """
CREATE TABLE IF NOT EXISTS outputs (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    voice_id    TEXT REFERENCES voices(id) ON DELETE RESTRICT,
    voice_name  TEXT NOT NULL DEFAULT '',
    origin      TEXT NOT NULL CHECK (origin IN ('batch','supplement','single')),
    position    INTEGER NOT NULL DEFAULT 0,
    text        TEXT NOT NULL,
    language    TEXT NOT NULL,
    gen_status  TEXT NOT NULL DEFAULT 'pending'
                CHECK (gen_status IN ('pending','running','done','failed')),
    in_final    INTEGER NOT NULL DEFAULT 0,
    output_mp3  TEXT,
    output_hash TEXT,
    duration_ms INTEGER,
    file_size   INTEGER,
    version     INTEGER NOT NULL DEFAULT 0,
    error       TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_outputs_final  ON outputs(project_id, in_final, position);
CREATE INDEX IF NOT EXISTS idx_outputs_status ON outputs(project_id, gen_status);
CREATE INDEX IF NOT EXISTS idx_outputs_voice  ON outputs(voice_id);

CREATE TABLE IF NOT EXISTS output_versions (
    id          TEXT PRIMARY KEY,
    output_id   TEXT NOT NULL REFERENCES outputs(id) ON DELETE CASCADE,
    version     INTEGER NOT NULL,
    output_mp3  TEXT NOT NULL,
    output_hash TEXT,
    text        TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    UNIQUE (output_id, version)
);

CREATE TABLE IF NOT EXISTS output_order (
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    output_id  TEXT NOT NULL REFERENCES outputs(id) ON DELETE CASCADE,
    position   INTEGER NOT NULL,
    PRIMARY KEY (project_id, output_id),
    UNIQUE (project_id, position)
);

CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL CHECK (kind IN ('candidate','batch','regen','supplement')),
    project_id  TEXT REFERENCES projects(id) ON DELETE SET NULL,
    status      TEXT NOT NULL CHECK (status IN ('running','done','failed','cancelled')),
    total       INTEGER NOT NULL DEFAULT 0,
    succeeded   INTEGER NOT NULL DEFAULT 0,
    failed      INTEGER NOT NULL DEFAULT 0,
    started_at  TEXT NOT NULL,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_project ON jobs(project_id, started_at DESC);
"""

# 旧对象 -> 兼容视图（迁移完成后旧表被 DROP，同名视图接管读取）
VIEW_DDL = """
CREATE VIEW IF NOT EXISTS batch_items AS
SELECT id, project_id, voice_id, position, text, language,
       CASE WHEN in_final=1 THEN 'selected'
            WHEN gen_status='done' THEN 'completed'
            ELSE gen_status END AS status,
       NULL AS output_wav,
       output_mp3, error, created_at, updated_at, version
FROM outputs WHERE origin='batch';

CREATE VIEW IF NOT EXISTS supplement_lines AS
SELECT id, project_id, voice_id, text, language,
       CASE WHEN gen_status='done' THEN 'done' ELSE gen_status END AS status,
       output_mp3, error, created_at, updated_at
FROM outputs WHERE origin='supplement';

CREATE VIEW IF NOT EXISTS batch_item_versions AS
SELECT id, output_id AS item_id, version, output_mp3, text, created_at
FROM output_versions;
"""


def run_script(conn: sqlite3.Connection, script: str) -> None:
    """逐条执行 DDL。不用 executescript：它会先隐式 COMMIT，破坏外层事务。"""
    for statement in script.split(";"):
        statement = statement.strip()
        if statement:
            conn.execute(statement)


def log(msg: str) -> None:
    print(f"[migrate_v5] {msg}", flush=True)


def object_kind(conn: sqlite3.Connection, name: str) -> str | None:
    row = conn.execute(
        "SELECT type FROM sqlite_master WHERE name=?", (name,)
    ).fetchone()
    return row[0] if row else None


def columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def file_md5(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.md5()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def backup_db(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    root = backup_dir / f"pre-migration-v5-{stamp}"
    suffix = 1
    while (root / "app.db").exists():
        root = backup_dir / f"pre-migration-v5-{stamp}-{suffix}"
        suffix += 1
    root.mkdir(parents=True)
    destination = root / "app.db"
    source = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    target = sqlite3.connect(destination)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    return destination


def migrate(db_path: Path, do_backup: bool = True) -> None:
    if do_backup:
        snapshot = backup_db(db_path)
        log(f"备份快照: {snapshot}")
    else:
        log("跳过备份（--no-backup，仅限副本演练）")

    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.isolation_level = None  # 手动控制事务
        conn.execute("BEGIN IMMEDIATE")

        # ---------- 1. 新表 ----------
        run_script(conn, NEW_TABLES_DDL)
        log("新表 outputs / output_versions / output_order / jobs 就绪")

        # ---------- 2. 老表增量加列（SQLite 不能 ALTER 加约束，CHECK 只落新表） ----------
        if "export_dir" not in columns(conn, "projects"):
            conn.execute("ALTER TABLE projects ADD COLUMN export_dir TEXT")
            log("projects + export_dir")

        vcols = columns(conn, "voices")
        if "audio_hash" in vcols:
            conn.execute("ALTER TABLE voices RENAME COLUMN audio_hash TO reference_hash")
            log("voices.audio_hash -> reference_hash")
        if "project_id" in columns(conn, "voices"):
            conn.execute("ALTER TABLE voices RENAME COLUMN project_id TO source_project_id")
            log("voices.project_id -> source_project_id")
        vcols = columns(conn, "voices")
        if "x_vector_only" not in vcols:
            conn.execute("ALTER TABLE voices ADD COLUMN x_vector_only INTEGER NOT NULL DEFAULT 0")
            log("voices + x_vector_only")
        if "reference_ms" not in vcols:
            conn.execute("ALTER TABLE voices ADD COLUMN reference_ms INTEGER")
            log("voices + reference_ms")
        if "source_candidate_id" not in vcols:
            conn.execute(
                "ALTER TABLE voices ADD COLUMN source_candidate_id TEXT "
                "REFERENCES candidates(id) ON DELETE SET NULL"
            )
            log("voices + source_candidate_id")

        if "params" not in columns(conn, "candidates"):
            conn.execute("ALTER TABLE candidates ADD COLUMN params TEXT")
            log("candidates + params")

        if "position" not in columns(conn, "project_voices"):
            conn.execute("ALTER TABLE project_voices ADD COLUMN position INTEGER")
            rows = conn.execute(
                "SELECT rowid, project_id FROM project_voices ORDER BY project_id, created_at, rowid"
            ).fetchall()
            counters: dict[str, int] = {}
            for row in rows:
                counters[row["project_id"]] = counters.get(row["project_id"], 0) + 1
                conn.execute(
                    "UPDATE project_voices SET position=? WHERE rowid=?",
                    (counters[row["project_id"]], row["rowid"]),
                )
            log(f"project_voices + position（回填 {len(rows)} 行）")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_project_voices_position "
            "ON project_voices(project_id, position)"
        )

        # ---------- 3. 数据搬迁：三源产物 -> outputs ----------
        moved = {"batch": 0, "supplement": 0, "single": 0}
        if object_kind(conn, "batch_items") == "table":
            rows = conn.execute(
                """
                SELECT b.*, COALESCE(pv.display_name, v.name, '') AS vname
                FROM batch_items b
                LEFT JOIN project_voices pv ON pv.project_id=b.project_id AND pv.voice_id=b.voice_id
                LEFT JOIN voices v ON v.id=b.voice_id
                ORDER BY b.project_id, b.position, b.created_at
                """
            ).fetchall()
            for r in rows:
                gen_status = {"selected": "done", "completed": "done", "failed": "failed"}.get(
                    r["status"], "pending"
                )
                conn.execute(
                    """
                    INSERT OR IGNORE INTO outputs(
                        id, project_id, voice_id, voice_name, origin, position,
                        text, language, gen_status, in_final, output_mp3,
                        version, error, created_at, updated_at)
                    VALUES(?,?,?,?, 'batch', ?, ?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        r["id"], r["project_id"], r["voice_id"], r["vname"], r["position"],
                        r["text"], r["language"], gen_status,
                        1 if r["status"] == "selected" else 0,
                        r["output_mp3"], r["version"], r["error"],
                        r["created_at"], r["updated_at"],
                    ),
                )
                moved["batch"] += 1
            log(f"batch_items -> outputs: {moved['batch']} 行")

        if object_kind(conn, "supplement_lines") == "table":
            rows = conn.execute(
                """
                SELECT s.*, COALESCE(pv.display_name, v.name, '') AS vname
                FROM supplement_lines s
                LEFT JOIN project_voices pv ON pv.project_id=s.project_id AND pv.voice_id=s.voice_id
                LEFT JOIN voices v ON v.id=s.voice_id
                ORDER BY s.project_id, s.created_at, s.rowid
                """
            ).fetchall()
            for r in rows:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO outputs(
                        id, project_id, voice_id, voice_name, origin, position,
                        text, language, gen_status, in_final, output_mp3,
                        version, error, created_at, updated_at)
                    VALUES(?,?,?,?, 'supplement', 0, ?,?,?,?,?,1,?,?,?)
                    """,
                    (
                        r["id"], r["project_id"], r["voice_id"], r["vname"],
                        r["text"], r["language"],
                        "done" if r["status"] == "done" else ("failed" if r["status"] == "failed" else "pending"),
                        1 if r["status"] == "done" else 0,
                        r["output_mp3"], r["error"], r["created_at"], r["updated_at"],
                    ),
                )
                moved["supplement"] += 1
            log(f"supplement_lines -> outputs: {moved['supplement']} 行")

        rcols = columns(conn, "voice_requests")
        if "direct_output_mp3" in rcols:
            rows = conn.execute(
                """
                SELECT * FROM voice_requests
                WHERE direct_output_mp3 IS NOT NULL
                ORDER BY project_id, position, created_at
                """
            ).fetchall()
            for r in rows:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO outputs(
                        id, project_id, voice_id, voice_name, origin, position,
                        text, language, gen_status, in_final, output_mp3,
                        version, error, created_at, updated_at)
                    VALUES(?,?,?,?, 'single', 0, ?,?, 'done', 1, ?, 1, NULL, ?, ?)
                    """,
                    (
                        r["id"], r["project_id"], r["voice_id"], r["name"],
                        r["audition_text"], r["language"], r["direct_output_mp3"],
                        r["created_at"], r["updated_at"] or r["created_at"],
                    ),
                )
                moved["single"] += 1
            log(f"voice_requests.direct_output_mp3 -> outputs(single): {moved['single']} 行")

        # ---------- 4. 重排 outputs.position：batch 原序 -> single -> supplement ----------
        if any(moved.values()):
            project_ids = [r[0] for r in conn.execute("SELECT DISTINCT project_id FROM outputs")]
            for pid in project_ids:
                rows = conn.execute(
                    """
                    SELECT id, origin, position, created_at FROM outputs
                    WHERE project_id=?
                    ORDER BY CASE origin WHEN 'batch' THEN 0 WHEN 'single' THEN 1 ELSE 2 END,
                             position, created_at, id
                    """,
                    (pid,),
                ).fetchall()
                for index, row in enumerate(rows, start=1):
                    conn.execute(
                        "UPDATE outputs SET position=? WHERE id=?", (index, row["id"])
                    )
            log("outputs.position 已按 原批量序→单句→补录 重排为项目内 1..N")

        # ---------- 5. batch_item_versions -> output_versions ----------
        if object_kind(conn, "batch_item_versions") == "table":
            count = conn.execute(
                """
                INSERT OR IGNORE INTO output_versions(id, output_id, version, output_mp3, text, created_at)
                SELECT id, item_id, version, output_mp3, text, created_at FROM batch_item_versions
                """
            ).rowcount
            log(f"batch_item_versions -> output_versions: {count} 行")

        # ---------- 6. app_settings 清理与搬家 ----------
        now = datetime.now().isoformat(timespec="seconds")
        for row in conn.execute(
            "SELECT key, value FROM app_settings WHERE key LIKE 'batch_export_dir:%'"
        ).fetchall():
            pid = row["key"].split(":", 1)[1]
            conn.execute(
                "UPDATE projects SET export_dir=? WHERE id=? AND export_dir IS NULL",
                (row["value"], pid),
            )
            conn.execute("DELETE FROM app_settings WHERE key=?", (row["key"],))
        deleted = conn.execute("DELETE FROM app_settings WHERE key LIKE 'last_step:%'").rowcount
        log(f"batch_export_dir:* -> projects.export_dir；删除 last_step:* 共 {deleted} 键")

        skipped_refs: list[str] = []
        for row in conn.execute(
            "SELECT key, value FROM app_settings WHERE key LIKE 'output_order:%'"
        ).fetchall():
            pid = row["key"].split(":", 1)[1]
            try:
                refs = json.loads(row["value"])
            except json.JSONDecodeError:
                refs = []
            if not isinstance(refs, list):
                refs = []
            position = 0
            for ref in refs:
                if not isinstance(ref, str):
                    continue
                prefix, _, ref_id = ref.partition(":")
                if prefix not in ("batch", "supplement", "single") or not ref_id:
                    skipped_refs.append(f"{row['key']}:{ref}")
                    continue
                exists = conn.execute(
                    "SELECT 1 FROM outputs WHERE id=? AND project_id=?", (ref_id, pid)
                ).fetchone()
                if not exists:
                    skipped_refs.append(f"{row['key']}:{ref}")
                    continue
                position += 1
                conn.execute(
                    "INSERT OR IGNORE INTO output_order(project_id, output_id, position) VALUES(?,?,?)",
                    (pid, ref_id, position),
                )
            conn.execute("DELETE FROM app_settings WHERE key=?", (row["key"],))
        order_rows = conn.execute("SELECT COUNT(*) FROM output_order").fetchone()[0]
        log(f"output_order:* -> output_order 表共 {order_rows} 行；跳过不可解析 {len(skipped_refs)} 条")
        for ref in skipped_refs:
            log(f"  跳过: {ref}")

        # ---------- 7. 删旧表、建兼容视图 ----------
        for old in ("batch_item_versions", "batch_items", "supplement_lines"):
            if object_kind(conn, old) == "table":
                conn.execute(f"DROP TABLE {old}")
                log(f"DROP TABLE {old}")
        run_script(conn, VIEW_DDL)
        log("兼容视图 batch_items / supplement_lines / batch_item_versions 已建")

        # ---------- 8. 摘掉 voice_requests 寄生字段 ----------
        rcols = columns(conn, "voice_requests")
        for col in ("direct_output_mp3", "direct_candidate_id"):
            if col in rcols:
                conn.execute(f"ALTER TABLE voice_requests DROP COLUMN {col}")
                log(f"voice_requests DROP COLUMN {col}")

        # ---------- 9. 索引收口 ----------
        conn.execute("DROP INDEX IF EXISTS idx_settings_prefix")
        conn.execute("DROP INDEX IF EXISTS idx_voices_audio_hash")
        dup = conn.execute(
            """
            SELECT reference_hash, COUNT(*) c FROM voices
            WHERE status='active' AND reference_hash IS NOT NULL
            GROUP BY reference_hash HAVING c>1
            """
        ).fetchall()
        if dup:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_voices_hash ON voices(reference_hash) WHERE status='active'"
            )
            log(f"active 声线 hash 有 {len(dup)} 组重复，退化为普通索引")
        else:
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_voices_hash ON voices(reference_hash) WHERE status='active'"
            )
            log("idx_voices_hash（active 部分唯一索引）已建")

        # ---------- 10. metadata.json 回填 x_vector_only / reference_ms 占位 ----------
        filled = 0
        for row in conn.execute("SELECT id, prompt_path FROM voices").fetchall():
            metadata_path = Path(row["prompt_path"]).parent / "metadata.json"
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            conn.execute(
                "UPDATE voices SET x_vector_only=? WHERE id=?",
                (1 if metadata.get("x_vector_only") else 0, row["id"]),
            )
            filled += 1
        log(f"metadata.json 回填 x_vector_only: {filled} 行")

        # ---------- 11. outputs 元数据补录（file_size / output_hash，能读到的才填） ----------
        sized = 0
        for row in conn.execute(
            "SELECT id, output_mp3 FROM outputs WHERE output_mp3 IS NOT NULL AND file_size IS NULL"
        ).fetchall():
            path = Path(row["output_mp3"])
            if not path.is_file():
                continue
            conn.execute(
                "UPDATE outputs SET file_size=?, output_hash=COALESCE(output_hash, ?) WHERE id=?",
                (path.stat().st_size, file_md5(path), row["id"]),
            )
            sized += 1
        missing = conn.execute(
            "SELECT COUNT(*) FROM outputs WHERE output_mp3 IS NOT NULL AND file_size IS NULL"
        ).fetchone()[0]
        log(f"outputs 元数据补录: {sized} 行；文件缺失 {missing} 行（保留 NULL）")

        # ---------- 12. 版本号 ----------
        conn.execute(
            "INSERT INTO app_settings(key,value,updated_at) VALUES('schema_version','5',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
            (now,),
        )

        conn.execute("COMMIT")

        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"foreign_key_check 发现 {len(violations)} 处违规: {violations[:5]}")
        log("foreign_key_check 通过")

        conn.execute("PRAGMA foreign_keys = ON")
        counts = {
            r[0]: r[1]
            for r in conn.execute(
                "SELECT name, (SELECT COUNT(*) FROM sqlite_master m2 WHERE m2.name=m.name) FROM sqlite_master m WHERE type IN ('table','view')"
            )
        }
        summary = {}
        for name in sorted(counts):
            try:
                summary[name] = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
            except sqlite3.Error:
                summary[name] = "?"
        log("迁移完成，各对象行数: " + json.dumps(summary, ensure_ascii=False))
    except Exception:
        conn.execute("ROLLBACK") if conn.in_transaction else None
        raise
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="schema v4 -> v5 迁移")
    parser.add_argument("--db", type=Path, default=DB_PATH, help="目标库文件（默认真实库）")
    parser.add_argument("--no-backup", action="store_true", help="跳过备份（仅限副本演练）")
    args = parser.parse_args()
    db_path = args.db.resolve()
    log(f"目标库: {db_path}")
    migrate(db_path, do_backup=not args.no_backup)


if __name__ == "__main__":
    main()
