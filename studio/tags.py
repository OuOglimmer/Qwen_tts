"""Tag component: 三层语义标签体系（资产固有 / 项目语义 / 需求阶段）。

为什么不是一列逗号串：同一把声线在《西游记》里是"旁白"、在《红楼梦》里可能是
"王熙凤"。标签若只挂在 ``voices.tags``，这两种身份会互相污染 —— 全局库里出现
"旁白"，下个项目搜"旁白"就会捞到一把其实没用过的声线。所以按 ``scope`` 分域：

===========  ========================  ==========================  ========
域           表                        标签意味着                  跨项目
===========  ========================  ==========================  ========
voice        ``voice_tags``            这把声音客观是什么（女声/低沉）  是
project      ``project_voice_tags``    在本项目里扮演什么（旁白/第二幕）  否
request      ``request_tags``          这个槽位想找什么（P0/少年感）    否
===========  ========================  ==========================  ========

``voices.tags`` 仍然保留，但降级为**派生缓存**（由本服务单向刷新，只含 voice
域的标签名）。这样 ``AssetsPage`` / 上传接口那些 ``tags.split(',')`` 的老代码
不用改也能继续工作。

约束落在服务层而不是 CHECK：SQLite 的 CHECK 管不了跨行，"同一单选组只能选一个"
必须逐行校验才成立，也只有在这里才给得出"角色定位只能选一个"这种能看懂的错误。
"""

from __future__ import annotations

import sqlite3
from typing import Any, Iterable

from .database import (
    SCHEMA_V6_DDL,
    Database,
    builtin_group_id,
    custom_tag_id,
    utc_now,
)

SCOPES = ("voice", "project", "request")
MAX_TAG_LEN = 20
MAX_TAGS_PER_GROUP = 5
UNCLASSIFIED = "未分类"


def normalize_names(raw: Iterable[str] | str | None) -> list[str]:
    """逗号串或列表 → 干净的标签名列表（全角逗号收口、去重、限长）。"""
    if raw is None:
        return []
    tokens = raw.replace("，", ",").split(",") if isinstance(raw, str) else list(raw)
    cleaned: list[str] = []
    for token in tokens:
        name = str(token or "").strip().replace(",", "")[:MAX_TAG_LEN]
        if name and name not in cleaned:
            cleaned.append(name)
    return cleaned


class TagService:
    def __init__(self, database: Database) -> None:
        self.db = database

    # ------------------------------------------------------------ 初始化

    def ensure_builtins(self) -> None:
        with self.db.transaction() as connection:
            connection.executescript(SCHEMA_V6_DDL)
            Database.seed_builtin_tags(connection)

    # ------------------------------------------------------------ 读

    def groups(self, scope: str | None = None) -> list[dict[str, Any]]:
        """组 + 词条 + 使用计数，供标签管理面板与筛选面板共用。"""
        if scope and scope not in SCOPES:
            raise ValueError("标签域不正确")
        where = "WHERE g.scope=?" if scope else ""
        params: tuple[Any, ...] = (scope,) if scope else ()
        groups = self.db.fetch_all(
            f"SELECT g.* FROM tag_groups g {where} ORDER BY g.sort, g.name", params
        )
        tags = self.db.fetch_all(
            f"""
            SELECT t.id, t.group_id, t.name, t.color, t.sort, t.merged_into, g.scope, g.multi,
                   (SELECT COUNT(*) FROM voice_tags vt WHERE vt.tag_id=t.id) AS voice_usage,
                   (SELECT COUNT(*) FROM project_voice_tags pt WHERE pt.tag_id=t.id) AS project_usage,
                   (SELECT COUNT(*) FROM request_tags rt WHERE rt.tag_id=t.id) AS request_usage
            FROM tags t JOIN tag_groups g ON g.id=t.group_id
            {where}
            ORDER BY t.sort, t.name
            """,
            params,
        )
        bucket: dict[str, list[dict[str, Any]]] = {}
        for tag in tags:
            tag["usage"] = int(tag["voice_usage"]) + int(tag["project_usage"]) + int(tag["request_usage"])
            bucket.setdefault(tag["group_id"], []).append(tag)
        for group in groups:
            group["tags"] = bucket.get(group["id"], [])
        return groups

    def all_tag_names(self, scope: str = "voice") -> list[str]:
        return [
            row["name"]
            for row in self.db.fetch_all(
                "SELECT t.name FROM tags t JOIN tag_groups g ON g.id=t.group_id "
                "WHERE g.scope=? ORDER BY t.sort, t.name",
                (scope,),
            )
        ]

    def voice_tag_map(self, voice_ids: Iterable[str]) -> dict[str, list[dict[str, Any]]]:
        """``voice_id → [tag…]``，一次查完，避免列表页 N+1。"""
        ids = [str(voice_id) for voice_id in voice_ids]
        if not ids:
            return {}
        marks = ",".join("?" for _ in ids)
        rows = self.db.fetch_all(
            f"""
            SELECT vt.voice_id, t.id, t.name, t.color, t.group_id, g.name AS group_name,
                   g.scope, g.multi, t.sort
            FROM voice_tags vt
            JOIN tags t ON t.id=vt.tag_id
            JOIN tag_groups g ON g.id=t.group_id
            WHERE vt.voice_id IN ({marks})
            ORDER BY g.sort, t.sort, t.name
            """,
            tuple(ids),
        )
        return self._bucket(rows, key="voice_id")

    def project_tag_map(self, project_id: str) -> dict[str, list[dict[str, Any]]]:
        rows = self.db.fetch_all(
            """
            SELECT pt.voice_id, t.id, t.name, t.color, t.group_id, g.name AS group_name,
                   g.scope, g.multi, pt.origin, t.sort
            FROM project_voice_tags pt
            JOIN tags t ON t.id=pt.tag_id
            JOIN tag_groups g ON g.id=t.group_id
            WHERE pt.project_id=?
            ORDER BY g.sort, t.sort, t.name
            """,
            (project_id,),
        )
        return self._bucket(rows, key="voice_id")

    def request_tag_map(self, request_ids: Iterable[str]) -> dict[str, list[dict[str, Any]]]:
        ids = [str(request_id) for request_id in request_ids]
        if not ids:
            return {}
        marks = ",".join("?" for _ in ids)
        rows = self.db.fetch_all(
            f"""
            SELECT rt.request_id, t.id, t.name, t.color, t.group_id, g.name AS group_name,
                   g.scope, g.multi, t.sort
            FROM request_tags rt
            JOIN tags t ON t.id=rt.tag_id
            JOIN tag_groups g ON g.id=t.group_id
            WHERE rt.request_id IN ({marks})
            ORDER BY g.sort, t.sort, t.name
            """,
            tuple(ids),
        )
        return self._bucket(rows, key="request_id")

    @staticmethod
    def _bucket(rows: list[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
        bucket: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            owner = row.pop(key)
            bucket.setdefault(owner, []).append(row)
        return bucket

    def project_code_for(self, project_id: str, voice_id: str) -> str:
        """导出命名用的项目语义前缀：角色定位 / 出场分组 里的词条。"""
        rows = self.db.fetch_all(
            """
            SELECT t.name, g.name AS group_name, g.sort, t.sort
            FROM project_voice_tags pt
            JOIN tags t ON t.id=pt.tag_id
            JOIN tag_groups g ON g.id=t.group_id
            WHERE pt.project_id=? AND pt.voice_id=? AND g.scope='project'
            ORDER BY g.sort, t.sort, t.name
            """,
            (project_id, voice_id),
        )
        ordered: list[str] = []
        seen_groups: set[str] = set()
        for row in rows:
            # 每个组取一条：单选组自然只有一条，多选组（出场分组）取第一条可读词，
            # 拼成 "旁白_第二幕" 这种稳定前缀。
            if row["group_name"] in seen_groups:
                continue
            seen_groups.add(row["group_name"])
            ordered.append(str(row["name"]))
        return "_".join(ordered)

    # ------------------------------------------------------------ 写：组与词条

    def _group_row(self, group_id: str) -> dict[str, Any] | None:
        return self.db.fetch_one("SELECT * FROM tag_groups WHERE id=?", (group_id,))

    def _unclassified_group(self, scope: str) -> str:
        """取（必要时建）该域下的「未分类」组：用户随手输入的新名字落这里。"""
        row = self.db.fetch_one(
            "SELECT id FROM tag_groups WHERE scope=? AND name=?", (scope, UNCLASSIFIED)
        )
        if row:
            return row["id"]
        group_id = builtin_group_id(scope, UNCLASSIFIED)
        with self.db.transaction() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO tag_groups(id,scope,name,multi,sort,builtin,created_at)
                VALUES(?,?,?,1,90,0,?)
                """,
                (group_id, scope, UNCLASSIFIED, utc_now()),
            )
        return group_id

    def create_tag(self, group_id: str, name: str, color: str = "") -> dict[str, Any]:
        group = self._group_row(group_id)
        if not group:
            raise ValueError("标签组不存在")
        clean = str(name or "").strip()[:MAX_TAG_LEN]
        if not clean:
            raise ValueError("标签名不能为空")
        existing = self.db.fetch_one(
            "SELECT * FROM tags WHERE group_id=? AND name=?", (group_id, clean)
        )
        if existing:
            return existing
        # 顺手把别处已合并掉的同名标签指回来：改名历史不该再生一个新词条。
        tag_id = custom_tag_id(group_id, clean)
        with self.db.transaction() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO tags(id,group_id,name,color,sort,created_at) VALUES(?,?,?,?,999,?)",
                (tag_id, group_id, clean, str(color or "").strip(), utc_now()),
            )
        return self.db.fetch_one("SELECT * FROM tags WHERE id=?", (tag_id,)) or {}

    def update_tag(self, tag_id: str, name: str | None = None, color: str | None = None) -> dict[str, Any]:
        tag = self.db.fetch_one("SELECT * FROM tags WHERE id=?", (tag_id,))
        if not tag:
            raise ValueError("标签不存在")
        clean_name = tag["name"] if name is None else str(name).strip()[:MAX_TAG_LEN]
        clean_color = tag["color"] if color is None else str(color).strip()
        if not clean_name:
            raise ValueError("标签名不能为空")
        duplicate = self.db.fetch_one(
            "SELECT id FROM tags WHERE group_id=? AND name=? AND id<>?",
            (tag["group_id"], clean_name, tag_id),
        )
        if duplicate:
            raise ValueError(f"同一标签组里已有「{clean_name}」，请改用合并")
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE tags SET name=?, color=? WHERE id=?", (clean_name, clean_color, tag_id)
            )
        self._resync_tag_voices(tag_id)
        return self.db.fetch_one("SELECT * FROM tags WHERE id=?", (tag_id,)) or {}

    def merge_tag(self, tag_id: str, into_tag_id: str) -> dict[str, Any]:
        """把 tag_id 合并进 into_tag_id：关联整体迁移，旧词条留 merged_into 指过去。

        不物理删旧词条，历史关联就不用重写 —— "温柔女声 → 温柔"这种整理不会丢数据。
        """
        if tag_id == into_tag_id:
            raise ValueError("不能合并到自己")
        source = self.db.fetch_one("SELECT * FROM tags WHERE id=?", (tag_id,))
        target = self.db.fetch_one("SELECT * FROM tags WHERE id=?", (into_tag_id,))
        if not source or not target:
            raise ValueError("标签不存在")
        if source["group_id"] != target["group_id"]:
            raise ValueError("只能合并同一标签组里的词条")
        affected: set[str] = set()
        with self.db.transaction() as connection:
            for voice_id, in connection.execute(
                "SELECT voice_id FROM voice_tags WHERE tag_id=?", (tag_id,)
            ).fetchall():
                affected.add(voice_id)
            connection.execute(
                "INSERT OR IGNORE INTO voice_tags(voice_id,tag_id,created_at) "
                "SELECT voice_id,?,created_at FROM voice_tags WHERE tag_id=?",
                (into_tag_id, tag_id),
            )
            connection.execute("DELETE FROM voice_tags WHERE tag_id=?", (tag_id,))
            connection.execute(
                "INSERT OR IGNORE INTO project_voice_tags(project_id,voice_id,tag_id,origin,created_at) "
                "SELECT project_id,voice_id,?,origin,created_at FROM project_voice_tags WHERE tag_id=?",
                (into_tag_id, tag_id),
            )
            connection.execute("DELETE FROM project_voice_tags WHERE tag_id=?", (tag_id,))
            connection.execute(
                "INSERT OR IGNORE INTO request_tags(request_id,tag_id,created_at) "
                "SELECT request_id,?,created_at FROM request_tags WHERE tag_id=?",
                (into_tag_id, tag_id),
            )
            connection.execute("DELETE FROM request_tags WHERE tag_id=?", (tag_id,))
            connection.execute(
                "UPDATE tags SET merged_into=?, name=? WHERE id=?",
                (into_tag_id, source["name"], tag_id),
            )
        self._resync_voices(affected)
        return target

    # ------------------------------------------------------------ 写：关联

    def _tag_scope(self, tag_ids: Iterable[str]) -> list[dict[str, Any]]:
        ids = [str(tag_id) for tag_id in tag_ids if tag_id]
        if not ids:
            return []
        marks = ",".join("?" for _ in ids)
        rows = self.db.fetch_all(
            f"""
            SELECT t.id, t.name, t.color, t.group_id, g.name AS group_name, g.scope, g.multi, g.sort
            FROM tags t JOIN tag_groups g ON g.id=t.group_id
            WHERE t.id IN ({marks}) AND t.merged_into IS NULL
            """,
            tuple(ids),
        )
        if len(rows) != len(set(ids)):
            raise ValueError("部分标签不存在或已被合并，请刷新后重试")
        return rows

    def resolve_names(self, scope: str, names: Iterable[str] | str | None, *, create: bool = True) -> list[str]:
        """标签名 → tag_id（该域内解析；库里没有的名字按需建到「未分类」）。"""
        clean = normalize_names(names)
        if not clean:
            return []
        resolved: list[str] = []
        for name in clean:
            row = self.db.fetch_one(
                "SELECT t.id FROM tags t JOIN tag_groups g ON g.id=t.group_id "
                "WHERE g.scope=? AND t.name=? AND t.merged_into IS NULL "
                "ORDER BY g.sort, t.sort LIMIT 1",
                (scope, name),
            )
            if row:
                resolved.append(row["id"])
            elif create:
                group_id = self._unclassified_group(scope)
                resolved.append(self.create_tag(group_id, name)["id"])
            else:
                raise ValueError(f"标签「{name}」不在{self._scope_label(scope)}里，请先在标签管理里新建")
        return resolved

    @staticmethod
    def _scope_label(scope: str) -> str:
        return {"voice": "声线标签", "project": "项目标签", "request": "需求标签"}.get(scope, "标签")

    def _validate(self, tags: list[dict[str, Any]], owners: tuple[str, ...], label: str) -> None:
        """校验域归属 + 组内条数 + 单选组唯一。

        ``owners`` 是"这个实体允许挂哪些域"。声线/项目各自只有一个域，而需求
        槽位允许三个域 —— 槽位描述的是"我在找一把什么声音、它演谁、多急"，
        固化时才按域分流（见 :meth:`inherit_on_solidify`）。
        """
        for tag in tags:
            if tag["scope"] not in owners:
                raise ValueError(f"「{tag['name']}」属于{self._scope_label(tag['scope'])}，不能用作{label}")
        by_group: dict[str, list[dict[str, Any]]] = {}
        for tag in tags:
            by_group.setdefault(tag["group_id"], []).append(tag)
        for group_tags in by_group.values():
            if len(group_tags) > MAX_TAGS_PER_GROUP:
                raise ValueError(f"「{group_tags[0]['group_name']}」最多 {MAX_TAGS_PER_GROUP} 个标签")
            if int(group_tags[0]["multi"]) == 0 and len(group_tags) > 1:
                raise ValueError(f"「{group_tags[0]['group_name']}」是单选组，只能选一个")

    def set_voice_tags(
        self, voice_id: str, *, tag_ids: Iterable[str] | None = None, names: str | None = None
    ) -> list[dict[str, Any]]:
        if not self.db.fetch_one("SELECT id FROM voices WHERE id=?", (voice_id,)):
            raise ValueError("声线不存在")
        ids = list(tag_ids) if tag_ids is not None else self.resolve_names("voice", names)
        tags = self._tag_scope(ids)
        self._validate(tags, ("voice",), self._scope_label("voice"))
        now = utc_now()
        with self.db.transaction() as connection:
            connection.execute("DELETE FROM voice_tags WHERE voice_id=?", (voice_id,))
            connection.executemany(
                "INSERT OR IGNORE INTO voice_tags(voice_id,tag_id,created_at) VALUES(?,?,?)",
                [(voice_id, tag["id"], now) for tag in tags],
            )
        self.sync_voice_cache(voice_id)
        return self.voice_tag_map([voice_id]).get(voice_id, [])

    def set_project_voice_tags(
        self,
        project_id: str,
        voice_id: str,
        *,
        tag_ids: Iterable[str] | None = None,
        names: str | None = None,
    ) -> list[dict[str, Any]]:
        if not self.db.fetch_one(
            "SELECT 1 FROM project_voices WHERE project_id=? AND voice_id=?",
            (project_id, voice_id),
        ):
            raise ValueError("该声线不在本项目清单中")
        ids = list(tag_ids) if tag_ids is not None else self.resolve_names("project", names)
        tags = self._tag_scope(ids)
        self._validate(tags, ("project",), self._scope_label("project"))
        now = utc_now()
        with self.db.transaction() as connection:
            connection.execute(
                "DELETE FROM project_voice_tags WHERE project_id=? AND voice_id=?",
                (project_id, voice_id),
            )
            connection.executemany(
                """
                INSERT OR IGNORE INTO project_voice_tags(project_id,voice_id,tag_id,origin,created_at)
                VALUES(?,?,?,'manual',?)
                """,
                [(project_id, voice_id, tag["id"], now) for tag in tags],
            )
        return self.project_tag_map(project_id).get(voice_id, [])

    def set_request_tags(
        self, request_id: str, *, tag_ids: Iterable[str] | None = None, names: str | None = None
    ) -> list[dict[str, Any]]:
        if not self.db.fetch_one("SELECT id FROM voice_requests WHERE id=?", (request_id,)):
            raise ValueError("声线需求不存在")
        ids = list(tag_ids) if tag_ids is not None else self.resolve_names("request", names)
        tags = self._tag_scope(ids)
        self._validate(tags, ("request", "project", "voice"), self._scope_label("request"))
        now = utc_now()
        with self.db.transaction() as connection:
            connection.execute("DELETE FROM request_tags WHERE request_id=?", (request_id,))
            connection.executemany(
                "INSERT OR IGNORE INTO request_tags(request_id,tag_id,created_at) VALUES(?,?,?)",
                [(request_id, tag["id"], now) for tag in tags],
            )
        return self.request_tag_map([request_id]).get(request_id, [])

    def inherit_on_solidify(
        self, connection: sqlite3.Connection, request_id: str, project_id: str, voice_id: str
    ) -> None:
        """固化时把槽位标签按域上移。

        规则写死，否则重复固化会重复上移：
          * request 域（优先级等）**不上移**，留在槽位；
          * project 域（角色定位/出场分组）→ ``project_voice_tags``，``origin='inherited'``；
          * voice 域（性别年龄/音色特征）→ ``voice_tags``。
        必须在调用方的事务连接里执行：固化是一组写操作，标签不能单独提交。
        """
        now = utc_now()
        rows = connection.execute(
            """
            SELECT t.id, g.id AS group_id, g.scope, g.multi
            FROM request_tags rt
            JOIN tags t ON t.id=rt.tag_id
            JOIN tag_groups g ON g.id=t.group_id
            WHERE rt.request_id=?
            ORDER BY g.sort, t.sort
            """,
            (request_id,),
        ).fetchall()
        used_single_groups: set[str] = set()
        for tag_id, group_id, scope, multi in rows:
            if scope not in ("project", "voice"):
                continue  # request 域（优先级等）留在槽位，不上移
            if int(multi) == 0:
                # 单选组：目标实体上已经有一个就不再上移，否则清单里会出现两个「旁白」
                if group_id in used_single_groups:
                    continue
                if scope == "voice":
                    occupied = connection.execute(
                        """
                        SELECT 1 FROM voice_tags vt JOIN tags t ON t.id=vt.tag_id
                        WHERE vt.voice_id=? AND t.group_id=? LIMIT 1
                        """,
                        (voice_id, group_id),
                    ).fetchone()
                else:
                    occupied = connection.execute(
                        """
                        SELECT 1 FROM project_voice_tags pt JOIN tags t ON t.id=pt.tag_id
                        WHERE pt.project_id=? AND pt.voice_id=? AND t.group_id=? LIMIT 1
                        """,
                        (project_id, voice_id, group_id),
                    ).fetchone()
                if occupied:
                    continue
                used_single_groups.add(group_id)
            if scope == "voice":
                connection.execute(
                    "INSERT OR IGNORE INTO voice_tags(voice_id,tag_id,created_at) VALUES(?,?,?)",
                    (voice_id, tag_id, now),
                )
            else:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO project_voice_tags(project_id,voice_id,tag_id,origin,created_at)
                    VALUES(?,?,?,'inherited',?)
                    """,
                    (project_id, voice_id, tag_id, now),
                )

    # ------------------------------------------------------------ 缓存列

    def sync_voice_cache(self, voice_id: str) -> None:
        """把 voice 域标签单向刷进 ``voices.tags``（派生缓存，读取仍走关系表）。

        刻意不动 ``voices.updated_at``：那是项目"最后活动时间"的口径，改个标签
        不该把项目顶到总览第一行。
        """
        rows = self.db.fetch_all(
            """
            SELECT t.name FROM voice_tags vt
            JOIN tags t ON t.id=vt.tag_id
            JOIN tag_groups g ON g.id=t.group_id
            WHERE vt.voice_id=? ORDER BY g.sort, t.sort, t.name
            """,
            (voice_id,),
        )
        self.db.execute(
            "UPDATE voices SET tags=? WHERE id=?",
            (",".join(row["name"] for row in rows), voice_id),
        )

    def _resync_voices(self, voice_ids: Iterable[str]) -> None:
        for voice_id in voice_ids:
            self.sync_voice_cache(voice_id)

    def _resync_tag_voices(self, tag_id: str) -> None:
        rows = self.db.fetch_all("SELECT voice_id FROM voice_tags WHERE tag_id=?", (tag_id,))
        self._resync_voices(row["voice_id"] for row in rows)

    # ------------------------------------------------------------ 筛选

    def filter_voice_ids(self, tag_names: str | list[str] | None, mode: str = "any") -> list[str] | None:
        """按标签名反查声线 id（``None`` = 不启用筛选）。"""
        names = normalize_names(tag_names)
        if not names:
            return None
        marks = ",".join("?" for _ in names)
        rows = self.db.fetch_all(
            f"""
            SELECT vt.voice_id, COUNT(DISTINCT t.id) AS hits
            FROM voice_tags vt
            JOIN tags t ON t.id=vt.tag_id
            JOIN tag_groups g ON g.id=t.group_id
            WHERE g.scope='voice' AND t.name IN ({marks})
            GROUP BY vt.voice_id
            """,
            tuple(names),
        )
        if mode == "all":
            return [row["voice_id"] for row in rows if int(row["hits"]) >= len(set(names))]
        return [row["voice_id"] for row in rows]
