"""schema v5 -> v6 幂等迁移脚本（项目 / 声线 / 成品关联闭环 + 标签体系）。

设计依据：design/voice-tag-and-linkage-design.md（§2 关联闭环、§3 标签、§5 阶段）。

用法：
    python scripts/migrate_v6.py                    # 迁移真实库（默认先备份快照）
    python scripts/migrate_v6.py --db PATH          # 迁移指定库文件
    python scripts/migrate_v6.py --db PATH --no-backup   # 副本演练时跳过备份

v6 干四件事：
  1. ``project_voices`` 从"只有一条关联"升级成"带状态机的清单"：
     加 ``state(active/replaced)`` / ``replaced_by`` / ``updated_at``，``position`` 收紧为
     NOT NULL；「项目内名称唯一」从表级约束改成 **只约束 active 行**的部分唯一索引
     —— 换版后旧行留痕必然与新版同名，表级唯一会把它挡在门外。
  2. ``outputs.roster_state``：成品记录当时用的是哪一版，历史交付物永远可解释。
  3. 补 ``voice_requests.voice_id`` 的外键（ON DELETE SET NULL）与 ``projects.notes``。
  4. 建标签四表 + 预设组/词条，并把历史 ``voices.tags`` 逗号串搬进 ``voice_tags``。

存量修复（v5 遗留的 25 条断链）：
  给"成品引用了、清单里却没有"的声线补一行 ``state='replaced'`` 的清单记录，
  于是旧成品显示「名称 · 已换版」、能顺着 ``replaced_by`` 找到新版，
  这批资产也不再是"看不见又删不掉"的僵尸。

保障：``PRAGMA foreign_keys=OFF`` + ``legacy_alter_table=ON`` + 单事务；
每一步都按"列/表/行是否存在"守卫，重复执行无副作用；提交后 ``foreign_key_check``。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from studio.config import DB_PATH  # noqa: E402
from studio.database import SCHEMA_V6_DDL, Database  # noqa: E402

# 期望的 voice_requests 列集合：只有完全等于这份清单时才做 12-step 重建，
# 遇到别的形状（更老或更未来的库）宁可跳过也不猜。
VOICE_REQUEST_COLUMNS = {
    "id", "project_id", "position", "name", "prompt", "audition_text",
    "language", "status", "candidate_round", "voice_id", "created_at", "updated_at",
}

NEW_PROJECT_VOICES_DDL = """
CREATE TABLE project_voices_new (
    project_id   TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    voice_id     TEXT NOT NULL REFERENCES voices(id)  ON DELETE CASCADE,
    display_name TEXT NOT NULL,
    position     INTEGER NOT NULL DEFAULT 0,
    state        TEXT NOT NULL DEFAULT 'active' CHECK (state IN ('active','replaced')),
    replaced_by  TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    PRIMARY KEY(project_id, voice_id)
)
"""

NEW_VOICE_REQUESTS_DDL = """
CREATE TABLE voice_requests_new (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    position        INTEGER NOT NULL,
    name            TEXT NOT NULL,
    prompt          TEXT NOT NULL,
    audition_text   TEXT NOT NULL,
    language        TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'candidate'
                    CHECK (status IN ('draft','ready','candidate','selected','fixed','single')),
    candidate_round INTEGER NOT NULL DEFAULT 0,
    voice_id        TEXT REFERENCES voices(id) ON DELETE SET NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    UNIQUE(project_id, name)
)
"""


def log(msg: str) -> None:
    print(f"[migrate_v6] {msg}", flush=True)


def run_script(conn: sqlite3.Connection, script: str) -> None:
    """逐条执行 DDL。不用 executescript：它会先隐式 COMMIT，破坏外层事务。"""
    for statement in script.split(";"):
        statement = statement.strip()
        if statement:
            conn.execute(statement)


def columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute("SELECT type FROM sqlite_master WHERE name=?", (name,)).fetchone()
    return bool(row) and row[0] == "table"


def backup_db(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    root = backup_dir / f"pre-migration-v6-{stamp}"
    suffix = 1
    while (root / "app.db").exists():
        root = backup_dir / f"pre-migration-v6-{stamp}-{suffix}"
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


# --------------------------------------------------------------- 重建


def _table_level_unique(conn: sqlite3.Connection, table: str, wanted: list[str]) -> bool:
    """表上是否还有 ``UNIQUE(wanted)`` 那种自动索引（不是我们建的部分索引）。"""
    for row in conn.execute(f"PRAGMA index_list({table})").fetchall():
        # (seq, name, unique, origin, partial) —— origin='u' 才是表级 UNIQUE 约束
        if row["origin"] != "u" or not row["unique"]:
            continue
        cols = [info[2] for info in conn.execute(f"PRAGMA index_info({row['name']})")]
        if cols == wanted:
            return True
    return False


def rebuild_project_voices(conn: sqlite3.Connection) -> int:
    """把 project_voices 重建成 v6 形状（12-step 里的核心几步）。返回搬迁行数。"""
    if not table_exists(conn, "project_voices"):
        log("project_voices 不存在，跳过重建（全新库由 initialize 建）")
        return 0

    cols = columns(conn, "project_voices")
    has_state = "state" in cols
    position_nullable = True
    for info in conn.execute("PRAGMA table_info(project_voices)"):
        if info[1] == "position":
            position_nullable = not info[3]
    # 判据不能只看 state 列：如果应用先用新代码启动过，_ensure_v6_schema 已经
    # ALTER 出 state 列，但表级 UNIQUE(project_id, display_name) 还在 ——
    # 那个约束会让"换版后保留旧行"直接失败，必须借这次机会换成部分唯一索引。
    needs_rebuild = (
        not has_state
        or position_nullable
        or _table_level_unique(conn, "project_voices", ["project_id", "display_name"])
    )
    if not needs_rebuild:
        log("project_voices 已是 v6 形状，跳过重建")
        return 0

    if "position" not in cols:
        conn.execute("ALTER TABLE project_voices ADD COLUMN position INTEGER")
    # 先把 position 补齐：唯一索引允许多 NULL，缺位置的行排序会漂。
    rows = conn.execute(
        "SELECT rowid, project_id FROM project_voices ORDER BY project_id, created_at, rowid"
    ).fetchall()
    counters: dict[str, int] = {}
    for row in rows:
        pid = row[1]
        counters[pid] = counters.get(pid, 0) + 1
        conn.execute("UPDATE project_voices SET position=? WHERE rowid=?", (counters[pid], row[0]))

    state_expr = "COALESCE(state, 'active')" if has_state else "'active'"
    by_expr = "replaced_by" if "replaced_by" in cols else "NULL"
    updated_expr = "COALESCE(NULLIF(updated_at, ''), created_at)" if "updated_at" in cols else "created_at"

    conn.execute("ALTER TABLE project_voices RENAME TO project_voices_v5tmp")
    run_script(conn, NEW_PROJECT_VOICES_DDL)
    conn.execute(
        f"""
        INSERT INTO project_voices_new(
            project_id, voice_id, display_name, position, state, replaced_by, created_at, updated_at)
        SELECT project_id, voice_id, display_name, COALESCE(position, 0), {state_expr},
               {by_expr}, created_at, {updated_expr}
        FROM project_voices_v5tmp
        """
    )
    moved = conn.execute("SELECT COUNT(*) FROM project_voices_new").fetchone()[0]
    conn.execute("DROP TABLE project_voices_v5tmp")
    conn.execute("ALTER TABLE project_voices_new RENAME TO project_voices")

    # 索引：名称唯一只约束 active 行；位置在项目内唯一。
    conn.execute("CREATE INDEX IF NOT EXISTS idx_project_voices_voice ON project_voices(voice_id)")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_project_voices_position "
        "ON project_voices(project_id, position)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_project_voices_display "
        "ON project_voices(project_id, display_name) WHERE state='active'"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_project_voices_state ON project_voices(project_id, state)"
    )
    log(f"project_voices 重建完成：{moved} 行")
    return moved


def rebuild_voice_requests(conn: sqlite3.Connection) -> bool:
    """重建 voice_requests，补 ``voice_id -> voices(id) ON DELETE SET NULL`` 外键。"""
    if not table_exists(conn, "voice_requests"):
        return False
    if columns(conn, "voice_requests") != VOICE_REQUEST_COLUMNS:
        log("voice_requests 列集合与 v5 期望不一致，跳过外键重建（不影响其余迁移）")
        return False
    # 已经有这条外键就说明重建过了：重复重建只会白白增加一次表复制。
    for fk in conn.execute("PRAGMA foreign_key_list(voice_requests)"):
        if fk["table"] == "voices" and fk["from"] == "voice_id":
            log("voice_requests 外键已就位，跳过重建")
            return False
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='voice_requests_new'"
    ).fetchone():
        conn.execute("DROP TABLE voice_requests_new")
    conn.execute("ALTER TABLE voice_requests RENAME TO voice_requests_v5tmp")
    run_script(conn, NEW_VOICE_REQUESTS_DDL)
    conn.execute(
        """
        INSERT INTO voice_requests_new(
            id, project_id, position, name, prompt, audition_text, language, status,
            candidate_round, voice_id, created_at, updated_at)
        SELECT id, project_id, position, name, prompt, audition_text, language, status,
               candidate_round, voice_id, created_at, COALESCE(updated_at, created_at)
        FROM voice_requests_v5tmp
        """
    )
    # 悬空引用先断掉，否则外键校验会失败：声线已物理删除的槽位不该抱着一个死 id。
    orphan = conn.execute(
        "UPDATE voice_requests_new SET voice_id=NULL WHERE voice_id IS NOT NULL "
        "AND voice_id NOT IN (SELECT id FROM voices)"
    ).rowcount
    conn.execute("DROP TABLE voice_requests_v5tmp")
    conn.execute("ALTER TABLE voice_requests_new RENAME TO voice_requests")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_requests_project ON voice_requests(project_id, position)")
    log(f"voice_requests 重建完成（外键就位；清掉悬空 voice_id {orphan} 处）")
    return True


# --------------------------------------------------------------- 修复


def repair_roster_links(conn: sqlite3.Connection) -> dict[str, int]:
    """把"成品引用了、清单里却没有"的声线补回清单，并给旧版打 replaced 状态。"""
    stats: dict[str, int] = {}

    # 1) 已有清单行但资产已 replaced：状态跟着资产走（reopen 解冻会留下这种行）
    stats["roster_marked_replaced"] = conn.execute(
        """
        UPDATE project_voices SET state='replaced'
        WHERE state='active'
          AND voice_id IN (SELECT id FROM voices WHERE status='replaced')
        """
    ).rowcount

    # 2) 成品引用了清单里没有的声线：补一行 replaced 记录（保留 created_at 以维持时间线）
    stats["roster_backfilled"] = conn.execute(
        """
        INSERT INTO project_voices(
            project_id, voice_id, display_name, position, state, replaced_by, created_at, updated_at)
        SELECT o.project_id, o.voice_id,
               COALESCE(NULLIF(MAX(o.voice_name), ''),
                        (SELECT name FROM voices v WHERE v.id=o.voice_id), ''),
               (SELECT COALESCE(MAX(position), 0) FROM project_voices pv2
                 WHERE pv2.project_id=o.project_id)
               + ROW_NUMBER() OVER (PARTITION BY o.project_id ORDER BY o.voice_id),
               'replaced', NULL, MIN(o.created_at), MIN(o.created_at)
        FROM outputs o
        WHERE o.voice_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM project_voices pv
                           WHERE pv.project_id=o.project_id AND pv.voice_id=o.voice_id)
        GROUP BY o.project_id, o.voice_id
        """
    ).rowcount

    # 3) 换版血缘：同名 active 行就是新版（重新固化沿用了同一个槽位名）
    stats["replaced_by_inferred"] = conn.execute(
        """
        UPDATE project_voices
        SET replaced_by=(
            SELECT pv2.voice_id FROM project_voices pv2
            WHERE pv2.project_id=project_voices.project_id
              AND pv2.state='active'
              AND pv2.display_name=project_voices.display_name
            LIMIT 1
        )
        WHERE state='replaced' AND replaced_by IS NULL
          AND EXISTS (
            SELECT 1 FROM project_voices pv3
            WHERE pv3.project_id=project_voices.project_id
              AND pv3.state='active'
              AND pv3.display_name=project_voices.display_name
          )
        """
    ).rowcount

    # 4) 成品快照：绑着 replaced 资产的成品标成"已换版"
    stats["outputs_marked_replaced"] = conn.execute(
        """
        UPDATE outputs SET roster_state='replaced'
        WHERE voice_id IN (SELECT id FROM voices WHERE status='replaced')
          AND roster_state<>'replaced'
        """
    ).rowcount

    stats["remaining_broken_outputs"] = conn.execute(
        """
        SELECT COUNT(*) FROM outputs o
        WHERE o.voice_id IS NOT NULL AND NOT EXISTS (
          SELECT 1 FROM project_voices pv
          WHERE pv.project_id=o.project_id AND pv.voice_id=o.voice_id)
        """
    ).fetchone()[0]
    return stats


def migrate(db_path: Path, do_backup: bool = True) -> None:
    if do_backup:
        log(f"备份快照: {backup_db(db_path)}")
    else:
        log("跳过备份（--no-backup，仅限副本演练）")

    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        # 关键：重建期间关掉 ALTER TABLE 的引用改写，否则 RENAME 会把
        # candidates 等表的 FK 子句顺手改成指向临时表名。
        conn.execute("PRAGMA legacy_alter_table = ON")
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")

        # ---------- 1. 标签表 / 索引 ----------
        run_script(conn, SCHEMA_V6_DDL)
        log("标签表 tag_groups / tags / voice_tags / project_voice_tags / request_tags 就绪")

        # ---------- 2. 补列（幂等） ----------
        if "notes" not in columns(conn, "projects"):
            conn.execute("ALTER TABLE projects ADD COLUMN notes TEXT NOT NULL DEFAULT ''")
            log("projects + notes")
        if "export_dir" not in columns(conn, "projects"):
            conn.execute("ALTER TABLE projects ADD COLUMN export_dir TEXT")
            log("projects + export_dir")
        if "roster_state" not in columns(conn, "outputs"):
            conn.execute("ALTER TABLE outputs ADD COLUMN roster_state TEXT NOT NULL DEFAULT 'active'")
            log("outputs + roster_state")

        # ---------- 3. 重建两张表 ----------
        rebuild_project_voices(conn)
        rebuild_voice_requests(conn)

        # ---------- 4. 存量修复 ----------
        stats = repair_roster_links(conn)
        log(
            "关联修复：清单补录 {roster_backfilled} 行 · 标记 replaced {roster_marked_replaced} 行 · "
            "推断 replaced_by {replaced_by_inferred} 处 · 成品标记已换版 {outputs_marked_replaced} 条 · "
            "剩余断链 {remaining_broken_outputs} 条".format(**stats)
        )
        if stats["remaining_broken_outputs"]:
            raise RuntimeError(f"仍有 {stats['remaining_broken_outputs']} 条成品找不到清单归属")

        # ---------- 5. 标签：预设词条 + 历史逗号串 ----------
        Database.seed_builtin_tags(conn)
        Database.backfill_voice_tag_rows(conn)
        log(
            "标签：{g} 组 / {t} 词条 · voice_tags {vt} 行".format(
                g=conn.execute("SELECT COUNT(*) FROM tag_groups").fetchone()[0],
                t=conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0],
                vt=conn.execute("SELECT COUNT(*) FROM voice_tags").fetchone()[0],
            )
        )

        # ---------- 6. 版本号 ----------
        now = datetime.now().isoformat(timespec="seconds")
        conn.execute(
            "INSERT INTO app_settings(key,value,updated_at) VALUES('schema_version','6',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
            (now,),
        )
        conn.execute("COMMIT")

        conn.execute("PRAGMA legacy_alter_table = OFF")
        conn.execute("PRAGMA foreign_keys = ON")
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"foreign_key_check 发现 {len(violations)} 处违规: {violations[:5]}")
        log("foreign_key_check 通过")

        summary = {}
        for name in sorted(
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view') ORDER BY name"
            )
        ):
            try:
                summary[name] = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
            except sqlite3.Error:
                summary[name] = "?"
        log("迁移完成，各对象行数: " + json.dumps(summary, ensure_ascii=False))
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="schema v5 -> v6 迁移")
    parser.add_argument("--db", type=Path, default=DB_PATH, help="目标库文件（默认真实库）")
    parser.add_argument("--no-backup", action="store_true", help="跳过备份（仅限副本演练）")
    args = parser.parse_args()
    db_path = args.db.resolve()
    log(f"目标库: {db_path}")
    migrate(db_path, do_backup=not args.no_backup)


if __name__ == "__main__":
    main()
