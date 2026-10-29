"""成品取用规则：一句台词只认一条成品。

同一句台词可能在多把声线下各录过一次（换版后重录、单句保留、事后补录），
在成品库里它们是彼此独立的 ``outputs`` 行。交付时一句只能出一份，规则定死为
**最后被选中的那条算数** —— 本模块把这条规则收在一处，批量生成、补录、导出
都调它，免得三处各写一遍互相打架。

判「同一句」用 ``(角色显示名, 归一化文本)``：角色名在换版前后保持同名，正好把
v1/v2 的同一句串起来；归一化只去掉空白字符。改标点、改错别字会被当成新的一句，
这是刻意的取舍 —— 真要做到「改字不断链」得引入独立的台词表，目前不需要。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from .config import PROJECT_DIR
from .database import Database, utc_now


def discard_output_files(project_id: str, paths: Iterable[str | None]) -> list[str]:
    """删成品行时顺手收掉它们独占的音频文件。

    以前这些文件是故意留在磁盘上的，但行删掉之后就再没有任何入口能找到它们，
    于是 ``outputs/`` 只会越来越大。只删 ``<项目>/outputs`` 之内的文件：路径来自
    库里的历史值，不能拿它当凭据去动项目目录外的任何东西。
    """
    try:
        root = (PROJECT_DIR / project_id / "outputs").resolve()
    except OSError:
        return []
    removed: list[str] = []
    for raw in paths:
        if not raw:
            continue
        try:
            target = Path(raw).resolve()
        except OSError:
            continue
        if root not in target.parents or not target.is_file():
            continue
        try:
            target.unlink()
        except OSError:
            # 文件被占用/只读时不该让删除操作整体失败，留给人工清理。
            continue
        removed.append(str(target))
    return removed


def normalize_text(text: str) -> str:
    """去掉全部空白，用于判断两条成品是不是同一句台词。"""
    return "".join(str(text or "").split())


def take_key(voice_name: Any, text: Any) -> tuple[str, str]:
    """同一句台词的归并键：同角色 + 同文本。"""
    return (str(voice_name or "").strip(), normalize_text(str(text or "")))


def demote_siblings(database: Database, project_id: str, keep_id: str) -> list[str]:
    """把同一句台词的其它成品移出成品库，返回被移出的成品 id。

    这是「最后选中者胜」的落地：某条成品被选入时，同句的其它条（可能是另一把
    声线录的）自动退回「已生成未选入」，导出时就不会再抢同一句的位置。
    角色名与文本都取自被选中那行的快照，调用方不必再传一遍。
    """
    keep = database.fetch_one(
        "SELECT voice_name, text FROM outputs WHERE id=? AND project_id=?",
        (keep_id, project_id),
    )
    if not keep:
        return []
    norm = normalize_text(str(keep["text"]))
    rows = database.fetch_all(
        """
        SELECT id, text FROM outputs
        WHERE project_id=? AND voice_name=? AND in_final=1 AND id<>?
        """,
        (project_id, keep["voice_name"], keep_id),
    )
    ids = [str(row["id"]) for row in rows if normalize_text(str(row["text"])) == norm]
    if ids:
        marks = ",".join("?" for _ in ids)
        database.execute(
            f"UPDATE outputs SET in_final=0, updated_at=? WHERE id IN ({marks})",
            (utc_now(), *ids),
        )
    return ids


def dedupe_latest(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """同句只留 ``updated_at`` 最新的一条，保持原有相对顺序。

    位置取第一次出现的位置（序号不乱跳），内容换成分数最高的那条。
    返回 ``(保留的行, 被合并掉的行)``，后者用于导出前检查里如实告知。
    """
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    slots: dict[tuple[str, str], int] = {}
    for row in rows:
        key = take_key(row.get("voice_name"), row.get("text"))
        index = slots.get(key)
        if index is None:
            slots[key] = len(kept)
            kept.append(row)
            continue
        winner = kept[index]
        if str(row.get("updated_at") or "") > str(winner.get("updated_at") or ""):
            dropped.append(winner)
            kept[index] = row
        else:
            dropped.append(row)
    return kept, dropped


def voice_versions(database: Database, project_id: str) -> dict[str, int]:
    """本项目每把声线是同类第几版：``{voice_id: 2}``。

    按清单里的固化先后编号（同 display_name 的第 N 个就是 vN）。换版会新插一行，
    所以 v 号与用户的直觉一致：v1 是最早那版，v3 是最新那版。
    """
    rows = database.fetch_all(
        """
        SELECT voice_id, display_name FROM project_voices
        WHERE project_id=? ORDER BY created_at, rowid
        """,
        (project_id,),
    )
    counter: dict[str, int] = {}
    versions: dict[str, int] = {}
    for row in rows:
        name = str(row["display_name"])
        counter[name] = counter.get(name, 0) + 1
        versions[str(row["voice_id"])] = counter[name]
    return versions


def current_versions(database: Database, project_id: str) -> dict[str, dict[str, Any]]:
    """每个角色当前采用的那一版：``{display_name: {voice_id, version}}``。"""
    rows = database.fetch_all(
        """
        SELECT voice_id, display_name FROM project_voices
        WHERE project_id=? AND state='active'
        """,
        (project_id,),
    )
    versions = voice_versions(database, project_id)
    return {
        str(row["display_name"]): {
            "voice_id": str(row["voice_id"]),
            "version": versions.get(str(row["voice_id"]), 0),
        }
        for row in rows
    }
