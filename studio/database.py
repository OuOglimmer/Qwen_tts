from __future__ import annotations

import hashlib
import sqlite3
import shutil
import sys
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .config import DB_PATH, PROJECT_DIR, VOICE_DIR


SCHEMA_VERSION = 6

# v5 新结构（outputs 三源合一等）。与 scripts/migrate_v5.py 的 DDL 保持一致；
# 全部幂等（IF NOT EXISTS），在 v4 老库上不会生效的部分由迁移脚本负责。
SCHEMA_V5_DDL = """
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

-- 兼容期：旧三张表被迁移脚本 DROP 后，同名视图接管读取；老库上同名表还在则本句为空操作。
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


# ---------------------------------------------------------------- schema v6
#
# v6 两件事：
#   1. 项目清单行不再删除，改状态迁移（project_voices.state / replaced_by），
#      成品记录当时用的是哪一版（outputs.roster_state）——历史成品的归属
#      从此永远可解释；
#   2. 标签从 voices.tags 逗号串升级成四张关系表，并按语义分域
#      （voice 资产固有 / project 项目语义 / request 需求阶段）。
#
# SCHEMA_V6_DDL 只建新表与索引（幂等），加列的 ALTER 由 ``_ensure_v6_schema``
# 逐个守卫，保证"跑过迁移的库"与"全新安装的库"收敛到同一形状。
SCHEMA_V6_DDL = """
CREATE TABLE IF NOT EXISTS tag_groups (
    id         TEXT PRIMARY KEY,
    scope      TEXT NOT NULL CHECK (scope IN ('voice','project','request')),
    name       TEXT NOT NULL,
    multi      INTEGER NOT NULL DEFAULT 1,
    sort       INTEGER NOT NULL DEFAULT 0,
    builtin    INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE (scope, name)
);

CREATE TABLE IF NOT EXISTS tags (
    id          TEXT PRIMARY KEY,
    group_id    TEXT NOT NULL REFERENCES tag_groups(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    color       TEXT NOT NULL DEFAULT '',
    sort        INTEGER NOT NULL DEFAULT 0,
    merged_into TEXT REFERENCES tags(id) ON DELETE SET NULL,
    created_at  TEXT NOT NULL,
    UNIQUE (group_id, name)
);
CREATE INDEX IF NOT EXISTS idx_tags_group ON tags(group_id);

CREATE TABLE IF NOT EXISTS voice_tags (
    voice_id   TEXT NOT NULL REFERENCES voices(id) ON DELETE CASCADE,
    tag_id     TEXT NOT NULL REFERENCES tags(id)   ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    PRIMARY KEY (voice_id, tag_id)
);
CREATE INDEX IF NOT EXISTS idx_voice_tags_tag ON voice_tags(tag_id);

-- 复合外键直接挂 project_voices 的复合主键：清单行没了，项目标签自动跟着走，
-- 不需要应用层再做一次清理。
CREATE TABLE IF NOT EXISTS project_voice_tags (
    project_id TEXT NOT NULL,
    voice_id   TEXT NOT NULL,
    tag_id     TEXT NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    origin     TEXT NOT NULL DEFAULT 'manual'
               CHECK (origin IN ('manual','inherited','imported')),
    created_at TEXT NOT NULL,
    PRIMARY KEY (project_id, voice_id, tag_id),
    FOREIGN KEY (project_id, voice_id)
        REFERENCES project_voices(project_id, voice_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_pvt_tag ON project_voice_tags(tag_id);

CREATE TABLE IF NOT EXISTS request_tags (
    request_id TEXT NOT NULL REFERENCES voice_requests(id) ON DELETE CASCADE,
    tag_id     TEXT NOT NULL REFERENCES tags(id)          ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    PRIMARY KEY (request_id, tag_id)
);
"""

# 预设标签组（builtin=1：可在设置里停用，但不能删）。
# id 由 (scope, 组名) / (scope, 组名, 词条名) 派生 → INSERT OR IGNORE 天然幂等。
BUILTIN_TAG_GROUPS: tuple[dict[str, Any], ...] = (
    {"scope": "voice", "name": "性别年龄", "multi": 0, "sort": 10,
     "tags": (("男声", "#3b82f6"), ("女声", "#ec4899"), ("童声", "#f59e0b"), ("老年", "#6b7280"))},
    {"scope": "voice", "name": "音色特征", "multi": 1, "sort": 20,
     "tags": (("低沉", ""), ("清亮", ""), ("沙哑", ""), ("磁性", ""), ("播音腔", ""), ("甜", ""), ("沧桑", ""))},
    # 未分类：历史 voices.tags 逗号串迁移的落点，也是用户随手新增标签的默认去处
    {"scope": "voice", "name": "未分类", "multi": 1, "sort": 90, "tags": ()},
    {"scope": "project", "name": "角色定位", "multi": 0, "sort": 10,
     "tags": (("主角", "#ef4444"), ("配角", "#3b82f6"), ("旁白", "#8b5cf6"), ("群演", "#6b7280"), ("反派", "#b91c1c"))},
    {"scope": "project", "name": "出场分组", "multi": 1, "sort": 20, "tags": ()},
    {"scope": "project", "name": "制作状态", "multi": 0, "sort": 30,
     "tags": (("待录", "#f59e0b"), ("已录", "#3b82f6"), ("待返修", "#ef4444"), ("已定稿", "#16a34a"))},
    {"scope": "request", "name": "需求优先级", "multi": 0, "sort": 10,
     "tags": (("P0", "#dc2626"), ("P1", "#f59e0b"), ("P2", "#6b7280"))},
)

# uuid5 命名空间：固定常量，保证同一台机器上重复初始化算出同一批 id。
_TAG_NAMESPACE = uuid.UUID("1f3c9a52-7d84-5b6e-9c1d-6a0e8b4d2f77")


def builtin_group_id(scope: str, name: str) -> str:
    return str(uuid.uuid5(_TAG_NAMESPACE, f"group:{scope}:{name}"))


def builtin_tag_id(scope: str, group: str, name: str) -> str:
    return str(uuid.uuid5(_TAG_NAMESPACE, f"tag:{scope}:{group}:{name}"))


def custom_tag_id(group_id: str, name: str) -> str:
    """用户新建标签的确定性 id：同名同组重复创建只会命中同一行。"""
    return str(uuid.uuid5(_TAG_NAMESPACE, f"custom:{group_id}:{name}"))


# 全新安装的规范结构。老 ``schema``（下面 initialize 里的那份）是 v2/v3 时代的
# 形状：voices 还叫 project_id、voice_requests 还带 direct_output_mp3 —— 照抄它
# 建新库，库会缺 v5 的列（source_project_id / reference_hash …），新装即坏。
# v6 起新库直接按这份 DDL 出生，只把 outputs 那组从 SCHEMA_V5_DDL 复用。
FRESH_V6_DDL = """
CREATE TABLE IF NOT EXISTS projects (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,
    status     TEXT NOT NULL DEFAULT 'draft'
               CHECK (status IN ('draft','active','archived','deleted')),
    notes      TEXT NOT NULL DEFAULT '',
    export_dir TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS voices (
    id                  TEXT PRIMARY KEY,
    source_project_id   TEXT REFERENCES projects(id) ON DELETE SET NULL,
    name                TEXT NOT NULL,
    source              TEXT NOT NULL,
    prompt              TEXT,
    language            TEXT NOT NULL,
    ref_language        TEXT NOT NULL DEFAULT '',
    ref_text            TEXT,
    reference_wav       TEXT NOT NULL,
    preview_mp3         TEXT NOT NULL,
    prompt_path         TEXT NOT NULL,
    reference_hash      TEXT,
    tags                TEXT NOT NULL DEFAULT '',
    notes               TEXT NOT NULL DEFAULT '',
    status              TEXT NOT NULL DEFAULT 'active',
    x_vector_only       INTEGER NOT NULL DEFAULT 0,
    reference_ms        INTEGER,
    source_candidate_id TEXT REFERENCES candidates(id) ON DELETE SET NULL,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_voices_project ON voices(source_project_id);

CREATE TABLE IF NOT EXISTS voice_requests (
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
);

CREATE TABLE IF NOT EXISTS candidates (
    id           TEXT PRIMARY KEY,
    request_id   TEXT NOT NULL REFERENCES voice_requests(id) ON DELETE CASCADE,
    seed         INTEGER NOT NULL,
    wav_path     TEXT NOT NULL,
    preview_path TEXT NOT NULL,
    selected     INTEGER NOT NULL DEFAULT 0,
    shortlisted  INTEGER NOT NULL DEFAULT 0,
    round        INTEGER NOT NULL DEFAULT 0,
    params       TEXT,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_candidates_request ON candidates(request_id);

-- 项目清单：一行 = 本项目里的一把声线。
-- state='active' 才是当前在用的版本；换版时旧行留痕（state='replaced' +
-- replaced_by），这样历史成品的归属可解释，也才删得掉（不再有"看不见又删不掉"的僵尸）。
-- 「项目内名称唯一」只约束 active 行：历史版本允许与新版同名，靠 state 区分。
CREATE TABLE IF NOT EXISTS project_voices (
    project_id   TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    voice_id     TEXT NOT NULL REFERENCES voices(id)  ON DELETE CASCADE,
    display_name TEXT NOT NULL,
    position     INTEGER NOT NULL DEFAULT 0,
    state        TEXT NOT NULL DEFAULT 'active' CHECK (state IN ('active','replaced')),
    replaced_by  TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    PRIMARY KEY(project_id, voice_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_project_voices_display
    ON project_voices(project_id, display_name) WHERE state='active';
CREATE UNIQUE INDEX IF NOT EXISTS idx_project_voices_position
    ON project_voices(project_id, position);
CREATE INDEX IF NOT EXISTS idx_project_voices_voice ON project_voices(voice_id);
CREATE INDEX IF NOT EXISTS idx_project_voices_state ON project_voices(project_id, state);

CREATE TABLE IF NOT EXISTS app_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS activity_log (
    id          TEXT PRIMARY KEY,
    project_id  TEXT REFERENCES projects(id) ON DELETE CASCADE,
    entity_type TEXT NOT NULL,
    entity_id   TEXT,
    action      TEXT NOT NULL,
    detail      TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_activity_project ON activity_log(project_id, created_at DESC);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def file_md5(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.md5()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class Database:
    """SQLite access layer.

    Connections are **thread-local and reused** rather than opened per query.
    Opening a connection on this Windows host costs ~54 ms (WAL + antivirus),
    which turned any N+1 query pattern into multi-second page loads; a reused
    connection answers the same query in ~0.15 ms. Each thread keeps its own
    handle, which also removes cross-thread contention on the same connection.
    """

    def __init__(self, path: Path = DB_PATH) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._local = threading.local()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def _new_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @property
    def _conn(self) -> sqlite3.Connection:
        """This thread's long-lived connection, opened on first use."""
        connection = getattr(self._local, "conn", None)
        if connection is None:
            connection = self._new_connection()
            self._local.conn = connection
        return connection

    def close_thread_connection(self) -> None:
        """Release this thread's connection (worker shutdown; leaks otherwise)."""
        connection = getattr(self._local, "conn", None)
        if connection is not None:
            connection.close()
            self._local.conn = None

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        """Reuse this thread's connection; commit on success, rollback on error."""
        connection = self._conn
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """One serialized connection for multi-statement writes."""
        with self._lock, self.connection() as connection:
            yield connection

    def initialize(self) -> None:
        existing_database = self.path.exists() and self.path.stat().st_size > 0
        stored_version = self._read_schema_version() if existing_database else 0
        if existing_database and stored_version < SCHEMA_VERSION:
            self.backup_snapshot("pre-migration")
        schema = """
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'draft',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS voice_requests (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            position INTEGER NOT NULL,
            name TEXT NOT NULL,
            prompt TEXT NOT NULL,
            audition_text TEXT NOT NULL,
            language TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'candidate',
            candidate_round INTEGER NOT NULL DEFAULT 0,
            voice_id TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(project_id, name)
        );

        CREATE TABLE IF NOT EXISTS candidates (
            id TEXT PRIMARY KEY,
            request_id TEXT NOT NULL REFERENCES voice_requests(id) ON DELETE CASCADE,
            seed INTEGER NOT NULL,
            wav_path TEXT NOT NULL,
            preview_path TEXT NOT NULL,
            selected INTEGER NOT NULL DEFAULT 0,
            shortlisted INTEGER NOT NULL DEFAULT 0,
            round INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS voices (
            id TEXT PRIMARY KEY,
            project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
            name TEXT NOT NULL,
            source TEXT NOT NULL,
            prompt TEXT,
            language TEXT NOT NULL,
            ref_language TEXT NOT NULL DEFAULT '',
            ref_text TEXT,
            reference_wav TEXT NOT NULL,
            preview_mp3 TEXT NOT NULL,
            prompt_path TEXT NOT NULL,
            audio_hash TEXT,
            tags TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS project_voices (
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            voice_id TEXT NOT NULL REFERENCES voices(id) ON DELETE CASCADE,
            display_name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(project_id, voice_id),
            UNIQUE(project_id, display_name)
        );

        CREATE TABLE IF NOT EXISTS batch_items (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            voice_id TEXT NOT NULL REFERENCES voices(id) ON DELETE RESTRICT,
            position INTEGER NOT NULL,
            text TEXT NOT NULL,
            language TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            output_wav TEXT,
            output_mp3 TEXT,
            version INTEGER NOT NULL DEFAULT 0,
            error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS batch_item_versions (
            id TEXT PRIMARY KEY,
            item_id TEXT NOT NULL REFERENCES batch_items(id) ON DELETE CASCADE,
            version INTEGER NOT NULL,
            output_mp3 TEXT NOT NULL,
            text TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            UNIQUE(item_id, version)
        );

        CREATE INDEX IF NOT EXISTS idx_candidates_request ON candidates(request_id);
        CREATE INDEX IF NOT EXISTS idx_voices_project ON voices(project_id);
        CREATE INDEX IF NOT EXISTS idx_project_voices_voice ON project_voices(voice_id);
        CREATE INDEX IF NOT EXISTS idx_batch_project_status ON batch_items(project_id, status);
        CREATE INDEX IF NOT EXISTS idx_batch_versions_item ON batch_item_versions(item_id, version);

        CREATE TABLE IF NOT EXISTS supplement_lines (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            voice_id TEXT NOT NULL REFERENCES voices(id) ON DELETE RESTRICT,
            text TEXT NOT NULL,
            language TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            output_mp3 TEXT,
            error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_supplement_project ON supplement_lines(project_id);

        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        -- last_step:<project_id> 按前缀批量查；主键已覆盖等值查询，
        -- 但 resolve_last_steps 的 LIKE 前缀匹配需要这条索引才不必全表扫。
        CREATE INDEX IF NOT EXISTS idx_settings_prefix ON app_settings(key);

        CREATE TABLE IF NOT EXISTS activity_log (
            id TEXT PRIMARY KEY,
            project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,
            entity_type TEXT NOT NULL,
            entity_id TEXT,
            action TEXT NOT NULL,
            detail TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_activity_project ON activity_log(project_id, created_at DESC);
        """
        with self._lock, self.connection() as connection:
            # WAL 持久化在库文件里，启动时设一次即可；每连接重复执行在首次
            # 建库时反而会因并发抢锁报 SQLITE_BUSY。
            connection.execute("PRAGMA journal_mode = WAL")
            if not existing_database:
                # 全新安装：直接按 v6 规范结构出生。老 ``schema`` 是 v2/v3 的形状
                # （voices.project_id、voice_requests.direct_output_mp3），照抄它
                # 建新库会缺 v5 的列，新装即坏 —— v6 顺手把这条路修好。
                connection.executescript(FRESH_V6_DDL)
                connection.executescript(SCHEMA_V5_DDL)
                self._ensure_v6_schema(connection)
                self._stamp_schema_version(connection)
                return
            if stored_version >= 5:
                # v5 及以后：老表已被迁移脚本重建/转视图，老 DDL 与老列的
                # ALTER 自愈一律跳过（否则会对视图建索引、把摘掉的列加回来）。
                connection.executescript(SCHEMA_V5_DDL)
                connection.execute(
                    "UPDATE outputs SET gen_status='pending', error='应用上次运行时中断，已自动恢复到等待状态' WHERE gen_status='running'"
                )
                self._ensure_v6_schema(connection)
                self._stamp_schema_version(connection)
                # 结构重建（清表级 UNIQUE / position 收紧 / 外键）只能由迁移脚本做：
                # 启动时只做加法，一旦顺序颠倒（先启动再跑脚本），版本号会写着 6 而表还是
                # v5 形状——那时换版会因 UNIQUE(project_id, display_name) 直接失败。
                # 这里只做体检并喊一声，把"看起来迁移好了、其实没迁"这种哑火暴露出来。
                self._warn_if_v6_shape_missing(connection)
                return
            connection.executescript(schema)
            request_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(voice_requests)").fetchall()
            }
            if "updated_at" not in request_columns:
                connection.execute("ALTER TABLE voice_requests ADD COLUMN updated_at TEXT")
                connection.execute("UPDATE voice_requests SET updated_at=created_at WHERE updated_at IS NULL")
            if "position" not in request_columns:
                connection.execute("ALTER TABLE voice_requests ADD COLUMN position INTEGER")
                connection.execute(
                    """
                    UPDATE voice_requests
                    SET position=(
                        SELECT COUNT(*) FROM voice_requests AS earlier
                        WHERE earlier.project_id=voice_requests.project_id
                          AND earlier.rowid<=voice_requests.rowid
                    )
                    WHERE position IS NULL
                    """
                )
            if "direct_output_mp3" not in request_columns:
                connection.execute("ALTER TABLE voice_requests ADD COLUMN direct_output_mp3 TEXT")
            if "direct_candidate_id" not in request_columns:
                connection.execute("ALTER TABLE voice_requests ADD COLUMN direct_candidate_id TEXT")
            if "candidate_round" not in request_columns:
                connection.execute("ALTER TABLE voice_requests ADD COLUMN candidate_round INTEGER NOT NULL DEFAULT 0")
            candidate_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(candidates)").fetchall()
            }
            if "shortlisted" not in candidate_columns:
                connection.execute("ALTER TABLE candidates ADD COLUMN shortlisted INTEGER NOT NULL DEFAULT 0")
            if "round" not in candidate_columns:
                connection.execute("ALTER TABLE candidates ADD COLUMN round INTEGER NOT NULL DEFAULT 0")
            batch_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(batch_items)").fetchall()
            }
            if "version" not in batch_columns:
                connection.execute("ALTER TABLE batch_items ADD COLUMN version INTEGER NOT NULL DEFAULT 0")
                # 已有成品无法追溯历史返修次数，统一登记为第 1 版并写入版本历史。
                connection.execute("UPDATE batch_items SET version=1 WHERE output_mp3 IS NOT NULL")
                connection.execute(
                    """
                    INSERT INTO batch_item_versions(id,item_id,version,output_mp3,text,created_at)
                    SELECT lower(hex(randomblob(16))), id, 1, output_mp3, text, COALESCE(updated_at, created_at)
                    FROM batch_items WHERE output_mp3 IS NOT NULL
                    """
                )
            voice_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(voices)").fetchall()
            }
            if "audio_hash" not in voice_columns:
                connection.execute("ALTER TABLE voices ADD COLUMN audio_hash TEXT")
            if "ref_language" not in voice_columns:
                # 参考音频本身的语种（区别于 language：那个是试听文本的语种）。
                # 历史声线无法追溯素材语种，统一留空由界面显示「未标注」；
                # 不拿 language 回填——语义不同，回填等于造伪数据。
                connection.execute(
                    "ALTER TABLE voices ADD COLUMN ref_language TEXT NOT NULL DEFAULT ''"
                )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_voices_audio_hash ON voices(audio_hash)")
            self._backfill_voice_globalization(connection)
            connection.execute(
                "UPDATE batch_items SET status='pending', error='应用上次运行时中断，已自动恢复到等待状态' WHERE status='running'"
            )
            # 新建库也直接带上 v5 结构（老表仍在，由迁移脚本负责搬数据与转视图）。
            connection.executescript(SCHEMA_V5_DDL)
            # 这个分支是"v5 之前的老库"，仍要走 scripts/migrate_v5.py + migrate_v6.py：
            # 版本号先记 5，下次启动才会进入上面的 v5 分支，不做越级自愈。
            self._stamp_schema_version(connection, version=5)

    def _stamp_schema_version(self, connection: sqlite3.Connection, version: int = SCHEMA_VERSION) -> None:
        connection.execute(
            "INSERT INTO app_settings(key,value,updated_at) VALUES('schema_version',?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
            (str(version), utc_now()),
        )

    @staticmethod
    def _warn_if_v6_shape_missing(connection: sqlite3.Connection) -> bool:
        """体检：project_voices 是否真的已经是 v6 形状。返回 True 表示没问题。

        两个判据都是"启动自愈做不到、只有迁移脚本能改"的东西：
        ``position`` 还是可空、以及表级 ``UNIQUE(project_id, display_name)`` 还挂着。
        后者尤其致命——换版要保留旧行留痕，旧行与新版同名，表级唯一会把它挡在门外。
        """
        position_nullable = any(
            info[1] == "position" and not info[3]
            for info in connection.execute("PRAGMA table_info(project_voices)")
        )
        legacy = position_nullable
        if not legacy:
            for row in connection.execute("PRAGMA index_list(project_voices)"):
                # (seq, name, unique, origin, partial) —— origin='u' 才是表级 UNIQUE 约束
                if row["origin"] != "u" or not row["unique"]:
                    continue
                cols = [item[2] for item in connection.execute(f"PRAGMA index_info({row['name']})")]
                if cols == ["project_id", "display_name"]:
                    legacy = True
                    break
        if legacy:
            print(
                "[database] 警告：project_voices 仍是 v5 形状（position 可空 / 表级 UNIQUE），"
                "换版与历史版本留痕会失败。请先执行：python scripts/migrate_v6.py",
                file=sys.stderr,
                flush=True,
            )
            return False
        return True

    # ------------------------------------------------------------ v6 自愈
    #
    # 只做"加"（补列、补表、补索引、补缓存），从不删列、不重建表：
    # 重建交给 scripts/migrate_v6.py 一次性完成（那里有 PRAGMA legacy_alter_table
    # 与显式列清单保护）。这样每次启动跑一遍是幂等的，既能给迁移过的库兜底，
    # 也能给全新安装的库补齐。

    @staticmethod
    def _add_column(connection: sqlite3.Connection, table: str, column: str, decl: str) -> bool:
        existing = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        if column in existing:
            return False
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        return True

    @staticmethod
    def _ensure_v6_schema(connection: sqlite3.Connection) -> None:
        Database._add_column(connection, "projects", "notes", "TEXT NOT NULL DEFAULT ''")
        Database._add_column(connection, "outputs", "roster_state", "TEXT NOT NULL DEFAULT 'active'")

        added_position = Database._add_column(connection, "project_voices", "position", "INTEGER")
        Database._add_column(connection, "project_voices", "state", "TEXT NOT NULL DEFAULT 'active'")
        Database._add_column(connection, "project_voices", "replaced_by", "TEXT")
        added_updated = Database._add_column(connection, "project_voices", "updated_at", "TEXT")

        if added_position:
            # 唯一索引允许多个 NULL，缺 position 的清单行排序会漂：按创建顺序补 1..N。
            rows = connection.execute(
                "SELECT rowid, project_id FROM project_voices ORDER BY project_id, created_at, rowid"
            ).fetchall()
            counters: dict[str, int] = {}
            for row in rows:
                counters[row[1]] = counters.get(row[1], 0) + 1
                connection.execute(
                    "UPDATE project_voices SET position=? WHERE rowid=?", (counters[row[1]], row[0])
                )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_project_voices_position "
            "ON project_voices(project_id, position)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_project_voices_state ON project_voices(project_id, state)"
        )
        if added_position or added_updated:
            connection.execute(
                "UPDATE project_voices SET updated_at=created_at "
                "WHERE updated_at IS NULL OR updated_at=''"
            )

        connection.executescript(SCHEMA_V6_DDL)
        Database.seed_builtin_tags(connection)
        Database.backfill_voice_tag_rows(connection)

    @staticmethod
    def seed_builtin_tags(connection: sqlite3.Connection) -> None:
        """写入预设标签组与词条（幂等）。"""
        now = utc_now()
        for group in BUILTIN_TAG_GROUPS:
            group_id = builtin_group_id(group["scope"], group["name"])
            connection.execute(
                """
                INSERT OR IGNORE INTO tag_groups(id,scope,name,multi,sort,builtin,created_at)
                VALUES(?,?,?,?,?,1,?)
                """,
                (group_id, group["scope"], group["name"], int(group["multi"]), int(group["sort"]), now),
            )
            for index, (name, color) in enumerate(group["tags"], start=1):
                connection.execute(
                    """
                    INSERT OR IGNORE INTO tags(id,group_id,name,color,sort,created_at)
                    VALUES(?,?,?,?,?,?)
                    """,
                    (builtin_tag_id(group["scope"], group["name"], name), group_id, name, color, index * 10, now),
                )

    @staticmethod
    def backfill_voice_tag_rows(connection: sqlite3.Connection) -> None:
        """把历史 ``voices.tags`` 逗号串搬进 voice_tags（落「未分类」组，原串保留）。

        只在某条声线还没有任何关系行时搬一次；之后 ``voices.tags`` 是 TagService
        单向刷新的派生缓存，读一律走关系表。
        """
        group_row = connection.execute(
            "SELECT id FROM tag_groups WHERE scope='voice' AND name='未分类'"
        ).fetchone()
        if not group_row:
            return
        group_id = group_row[0]
        rows = connection.execute(
            """
            SELECT v.id, v.tags FROM voices v
            WHERE v.tags<>'' AND NOT EXISTS (SELECT 1 FROM voice_tags vt WHERE vt.voice_id=v.id)
            """
        ).fetchall()
        now = utc_now()
        for voice_id, raw in rows:
            seen: set[str] = set()
            for token in str(raw).replace("，", ",").split(","):
                name = token.strip()[:20]
                if not name or name in seen:
                    continue
                seen.add(name)
                tag_id = custom_tag_id(group_id, name)
                connection.execute(
                    "INSERT OR IGNORE INTO tags(id,group_id,name,color,sort,created_at) VALUES(?,?,?,'',999,?)",
                    (tag_id, group_id, name, now),
                )
                connection.execute(
                    "INSERT OR IGNORE INTO voice_tags(voice_id,tag_id,created_at) VALUES(?,?,?)",
                    (voice_id, tag_id, now),
                )

    def _backfill_voice_globalization(self, connection: sqlite3.Connection) -> None:
        """声线全局化（schema v3）迁移：补参考音频 hash，并把历史归属声线登记进各项目清单。"""
        for row in connection.execute(
            "SELECT id, reference_wav FROM voices WHERE audio_hash IS NULL"
        ).fetchall():
            digest = file_md5(Path(row["reference_wav"]))
            if digest:
                connection.execute("UPDATE voices SET audio_hash=? WHERE id=?", (digest, row["id"]))
        orphans = connection.execute(
            """
            SELECT v.id, v.project_id, v.name, v.created_at FROM voices v
            JOIN projects p ON p.id=v.project_id
            WHERE v.status='active'
              AND NOT EXISTS (SELECT 1 FROM project_voices r WHERE r.voice_id=v.id)
            ORDER BY v.project_id, v.created_at, v.rowid
            """
        ).fetchall()
        if not orphans:
            return
        taken: dict[str, set[str]] = {}
        for row in connection.execute("SELECT project_id, display_name FROM project_voices").fetchall():
            taken.setdefault(row["project_id"], set()).add(row["display_name"])
        for row in orphans:
            names = taken.setdefault(row["project_id"], set())
            base = str(row["name"])[:80]
            display = base
            suffix = 2
            while display in names:
                display = f"{base}_{suffix}"
                suffix += 1
            names.add(display)
            connection.execute(
                "INSERT INTO project_voices(project_id,voice_id,display_name,created_at) VALUES(?,?,?,?)",
                (row["project_id"], row["id"], display, row["created_at"]),
            )

    def execute(self, sql: str, parameters: tuple[Any, ...] = ()) -> None:
        with self._lock, self.connection() as connection:
            connection.execute(sql, parameters)

    def executemany(self, sql: str, parameters: list[tuple[Any, ...]]) -> None:
        with self._lock, self.connection() as connection:
            connection.executemany(sql, parameters)

    def fetch_one(self, sql: str, parameters: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        with self._lock, self.connection() as connection:
            row = connection.execute(sql, parameters).fetchone()
        return dict(row) if row else None

    def fetch_all(self, sql: str, parameters: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self._lock, self.connection() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def new_id() -> str:
        return str(uuid.uuid4())

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self.fetch_one("SELECT value FROM app_settings WHERE key=?", (key,))
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        self.execute(
            """
            INSERT INTO app_settings(key,value,updated_at) VALUES(?,?,?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at
            """,
            (key, value, utc_now()),
        )

    def record_job(
        self,
        kind: str,
        project_id: str | None,
        status: str,
        total: int,
        succeeded: int,
        failed: int,
        started_at: str,
    ) -> None:
        """任务级事件落 ``jobs`` 表（activity_log 只留关键动作，批量/重生成等流水归这里）。"""
        self.execute(
            """
            INSERT INTO jobs(id,kind,project_id,status,total,succeeded,failed,started_at,finished_at)
            VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (self.new_id(), kind, project_id, status, total, succeeded, failed, started_at, utc_now()),
        )

    def log_activity(
        self,
        project_id: str | None,
        entity_type: str,
        entity_id: str | None,
        action: str,
        detail: str = "",
    ) -> None:
        self.execute(
            "INSERT INTO activity_log(id,project_id,entity_type,entity_id,action,detail,created_at) VALUES(?,?,?,?,?,?,?)",
            (self.new_id(), project_id, entity_type, entity_id, action, detail, utc_now()),
        )

    def _read_schema_version(self) -> int:
        try:
            connection = sqlite3.connect(self.path, timeout=5)
            try:
                row = connection.execute(
                    "SELECT value FROM app_settings WHERE key='schema_version'"
                ).fetchone()
            finally:
                connection.close()
            return int(row[0]) if row else 0
        except (OSError, sqlite3.Error, TypeError, ValueError):
            return 0

    def backup_snapshot(self, label: str = "snapshot", assets: dict[str, Path] | None = None) -> Path:
        """Create a unique, non-overwriting snapshot of the database plus asset folders.

        ``assets`` 是「快照里的相对路径 → 源目录」的映射，默认整库复制
        （``projects`` + ``voices``），够迁移和手动备份用。清理单行记录的调用方
        应该只传这次真要删掉的那几个目录，甚至传 ``{}`` 只备数据库 —— 否则
        「删 3 条台词」会先把整个素材库 copytree 一遍。
        """
        if assets is None:
            assets = {"projects": PROJECT_DIR, "voices": VOICE_DIR}
        backup_dir = self.path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        root = backup_dir / f"{label}-{stamp}"
        suffix = 1
        while root.exists():
            root = backup_dir / f"{label}-{stamp}-{suffix}"
            suffix += 1
        root.mkdir(parents=True)
        destination = root / "app.db"
        with self._lock:
            source_connection = sqlite3.connect(self.path)
            backup_connection = sqlite3.connect(destination)
            try:
                source_connection.backup(backup_connection)
            finally:
                backup_connection.close()
                source_connection.close()
        for name, source in assets.items():
            if not source.exists():
                continue
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, target)
        snapshots = sorted(
            (item for item in backup_dir.iterdir() if item.is_dir() and (item / "app.db").exists()),
            key=lambda item: item.name,
            reverse=True,
        )
        for old_snapshot in snapshots[30:]:
            shutil.rmtree(old_snapshot, ignore_errors=True)
        return destination

    def backup(self) -> Path:
        """Backward-compatible alias for manual backups."""
        return self.backup_snapshot("app")
