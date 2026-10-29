"""schema v5 业务回归：在迁移后的副本库上跑读写全链路（不加载模型）。

用法：
    python scripts/v5_smoke.py --db .tmp-migration/app-v5-test.db [--destructive]

非破坏性用例默认全跑；带 --destructive 的用例（删除/清空/彻底删项目）只在
明确传入该开关、且库名含 "test" 时执行，避免误伤真实数据。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from studio.batch import BatchService  # noqa: E402
from studio.config import PROJECT_DIR  # noqa: E402
from studio.database import Database, utc_now  # noqa: E402
from studio.exports import ExportService  # noqa: E402
from studio.projects import ProjectService  # noqa: E402
from studio.supplement import SupplementService  # noqa: E402
from studio.voice_assets import VoiceAssetService  # noqa: E402
from studio.voice_workflow import VoiceWorkflowService  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  PASS  {name}" + (f" — {detail}" if detail else ""))
    else:
        FAILED.append(f"{name} {detail}")
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


class StubAssets:
    """替掉模型调用：只负责"生成"出一个 mp3 文件并返回路径。"""

    def __init__(self, database: Database) -> None:
        self.db = database

    def project_voice(self, voice_id: str, project_id: str) -> dict[str, Any] | None:
        return self.db.fetch_one(
            """
            SELECT v.*, r.display_name FROM voices v
            JOIN project_voices r ON r.voice_id=v.id AND r.project_id=?
            WHERE v.id=? AND v.status='active'
            """,
            (project_id, voice_id),
        )

    def generate_with_voice(self, voice_id: str, text: str, language: str, stem: Path) -> str:
        stem.parent.mkdir(parents=True, exist_ok=True)
        target = stem.with_suffix(".mp3")
        target.write_bytes(b"ID3\x03\x00\x00\x00fake-mp3")
        return str(target)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--destructive", action="store_true")
    args = parser.parse_args()
    db_path = args.db.resolve()
    if args.destructive and "test" not in db_path.name:
        raise SystemExit("危险：--destructive 只允许在名字含 test 的库上跑")

    db = Database(db_path)
    projects_service = ProjectService(db)
    assets = VoiceAssetService(db, None)
    stub = StubAssets(db)
    batch = BatchService(db, None, stub)  # type: ignore[arg-type]
    supplements = SupplementService(db, stub)  # type: ignore[arg-type]
    workflow = VoiceWorkflowService(db, None)  # type: ignore[arg-type]
    exports = ExportService(db, batch, workflow, supplements)

    print(f"\n== 1. 结构 ==")
    version = db.get_setting("schema_version")
    check("schema_version=5", version == "5", f"实际 {version}")
    objects = {
        row["name"]: row["type"]
        for row in db.fetch_all(
            "SELECT name,type FROM sqlite_master WHERE name IN "
            "('outputs','output_versions','output_order','jobs','batch_items','supplement_lines','batch_item_versions')"
        )
    }
    check("outputs/output_versions/output_order/jobs 为表", all(objects.get(k) == "table" for k in ("outputs", "output_versions", "output_order", "jobs")), str(objects))
    check("旧三表已转视图", all(objects.get(k) == "view" for k in ("batch_items", "supplement_lines", "batch_item_versions")), str(objects))
    check("foreign_key_check 通过", not db.fetch_all("PRAGMA foreign_key_check"))

    print("\n== 2. 项目与声线读路径 ==")
    summaries = projects_service.project_summaries(include_archived=True)
    check("project_summaries 返回 15 项", len(summaries) == 15, f"实际 {len(summaries)}")
    sample = summaries[0]
    check("single_count 有值", "single_count" in sample and isinstance(sample["single_count"], int),
          f"single={sample.get('single_count')} task={sample.get('task_count')} done={sample.get('completed_count')}")
    check("last_step 推断在 1..4", all(1 <= int(row["last_step"]) <= 4 for row in summaries))
    step = projects_service.set_last_step(summaries[0]["id"], 2)
    check("set_last_step 空操作不写库", step is None)
    check("声线库 14 条", len(assets.library_voices()) == 14)

    print("\n== 3. 批量读路径 ==")
    pid = db.fetch_one(
        "SELECT project_id FROM outputs WHERE origin='batch' GROUP BY project_id ORDER BY COUNT(*) DESC LIMIT 1"
    )["project_id"]
    # 副本库没有配套的项目目录，manifest.json / 产物目录按需补建。
    (PROJECT_DIR / pid).mkdir(parents=True, exist_ok=True)
    items = batch.list_batch_items(pid)
    check("list_batch_items 非空", bool(items), f"{len(items)} 条")
    check("状态词仍是旧词汇", set(i["status"] for i in items) <= {"pending", "running", "completed", "selected", "failed"},
          str(sorted({i["status"] for i in items})))
    counts = batch.status_counts(pid)
    check("status_counts 与列表一致", sum(counts.values()) == len(items), str(counts))
    histories = batch.version_histories(pid)
    check("版本历史可读", isinstance(histories, dict) and bool(histories), f"{len(histories)} 个任务有历史")

    print("\n== 4. 批量写路径（新表） ==")
    voice = assets.project_voice(db.fetch_one(
        "SELECT voice_id FROM outputs WHERE origin='batch' AND project_id=? GROUP BY voice_id LIMIT 1", (pid,)
    )["voice_id"], pid)
    added = batch.add_batch_lines(pid, voice["id"], "Chinese", "回归测试台词一\n回归测试台词二")
    check("add_batch_lines 写 outputs", added == 2)
    new_rows = db.fetch_all(
        "SELECT * FROM outputs WHERE project_id=? AND origin='batch' AND text LIKE '回归测试台词%'", (pid,)
    )
    check("新行 gen_status=pending 且 in_final=0", all(r["gen_status"] == "pending" and r["in_final"] == 0 for r in new_rows))
    check("新行带 voice_name 快照", all(r["voice_name"] for r in new_rows), new_rows[0]["voice_name"])
    edited = batch.save_batch_edits(pid, [[int(new_rows[0]["position"]), "", "改后的台词", "Chinese"]])
    check("save_batch_edits 生效", edited == 1 and db.fetch_one(
        "SELECT text FROM outputs WHERE id=?", (new_rows[0]["id"],))["text"] == "改后的台词")
    # 直接按 outputs 语义模拟生成成功（跳过模型）
    db.execute(
        "UPDATE outputs SET gen_status='done',in_final=1,output_mp3=?,version=1,updated_at=? WHERE id=?",
        (str(Path(new_rows[0]["output_mp3"] or "").parent / "fake.mp3") if new_rows[0]["output_mp3"] else "C:/tmp/fake.mp3",
         utc_now(), new_rows[0]["id"]),
    )
    db.execute(
        "INSERT INTO output_versions(id,output_id,version,output_mp3,text,created_at) VALUES(?,?,?,?,?,?)",
        (db.new_id(), new_rows[0]["id"], 1, "C:/tmp/fake.mp3", "改后的台词", utc_now()),
    )
    toggled = batch.select_batch_item(pid, new_rows[0]["id"], False)
    check("select_batch_item(False) 置 in_final=0", toggled["status"] == "completed" and db.fetch_one(
        "SELECT in_final FROM outputs WHERE id=?", (new_rows[0]["id"],))["in_final"] == 0)
    toggled = batch.select_batch_item(pid, new_rows[0]["id"], True)
    check("select_batch_item(True) 置 in_final=1", db.fetch_one(
        "SELECT in_final FROM outputs WHERE id=?", (new_rows[0]["id"],))["in_final"] == 1)

    print("\n== 5. 补录写路径 ==")
    created = supplements.create(pid, voice["id"], "补录回归台词", "Chinese")
    check("补录写入 outputs(supplement)", created["status"] == "done" and db.fetch_one(
        "SELECT origin,gen_status,in_final FROM outputs WHERE id=?", (created["id"],))["in_final"] == 1)
    check("补录行可列举", any(r["id"] == created["id"] for r in supplements.list_lines(pid)))
    check("补录落 jobs", db.fetch_one("SELECT COUNT(*) AS c FROM jobs WHERE kind='supplement'")["c"] >= 1)

    print("\n== 6. 单句保留 ==")
    singles = workflow.single_line_outputs(db.fetch_one("SELECT project_id FROM outputs WHERE origin='single' LIMIT 1")["project_id"])
    check("single_line_outputs 可读", len(singles) == 1, f"{len(singles)} 条")
    progress = workflow.request_progress(db.fetch_one("SELECT project_id FROM outputs WHERE origin='single' LIMIT 1")["project_id"])
    check("request_progress.single 计数正确", progress["single"] == 1, str(progress))
    req = db.fetch_one(
        """
        SELECT r.id, r.project_id, r.name, r.audition_text AS text, r.language, r.voice_id, c.id AS candidate_id
        FROM voice_requests r JOIN candidates c ON c.request_id=r.id AND c.selected=1
        WHERE NOT EXISTS (SELECT 1 FROM outputs o WHERE o.id=r.id AND o.origin='single')
        LIMIT 1
        """
    )
    if req:
        from pathlib import Path as _P
        preview = db.fetch_one("SELECT preview_path FROM candidates WHERE id=?", (req["candidate_id"],))["preview_path"]
        _P(preview).parent.mkdir(parents=True, exist_ok=True)
        if not _P(preview).exists():
            _P(preview).write_bytes(b"ID3\x03\x00\x00\x00fake")
        kept = workflow.keep_candidate_as_single_output(req["id"], req["candidate_id"])
        row = db.fetch_one("SELECT * FROM outputs WHERE id=? AND origin='single'", (req["id"],))
        check("keep 写 outputs(single)，行 id = 槽位 id", row is not None and row["in_final"] == 1, str(bool(row)))
        check("keep 后再读得到", any(s["id"] == req["id"] for s in workflow.single_line_outputs(req["project_id"])))
        before = db.fetch_one("SELECT COUNT(*) AS c FROM outputs")["c"]
        name, removed = workflow.delete_single_line_output(req["project_id"], req["id"])
        check("delete_single_line_output 删行", db.fetch_one("SELECT COUNT(*) AS c FROM outputs")["c"] == before - 1, f"removed_file={removed}")
    else:
        print("  SKIP  没有可用于 keep 的候选（数据不足）")

    print("\n== 7. 导出排序 ==")
    keys = exports.stored_output_order(pid)
    check("stored_output_order 从 output_order 表读", bool(keys) and all(":" in k for k in keys), f"{len(keys)} 个键")
    exports.set_output_order(pid, list(reversed(keys)))
    reread = exports.stored_output_order(pid)
    check("set_output_order 回写正确", reread == list(reversed(keys)), f"{len(reread)} 个键")
    exports.set_output_order(pid, keys)
    check("还原原顺序", exports.stored_output_order(pid) == keys)
    rows, paths = exports.export_plan(pid)
    check("export_plan 出清单", bool(rows), f"{len(rows)} 条")

    print("\n== 8. jobs 表 ==")
    check("jobs 表存在且有补录记录", db.fetch_one("SELECT COUNT(*) AS c FROM jobs")["c"] >= 1)

    if args.destructive:
        print("\n== 9. 破坏性用例 ==")
        before = db.fetch_one("SELECT COUNT(*) AS c FROM outputs")["c"]
        batch.clear_pending_batch(pid)
        check("clear_pending_batch 清掉 pending/failed", db.fetch_one("SELECT COUNT(*) AS c FROM outputs")["c"] < before)
        target = db.fetch_one("SELECT project_id, COUNT(*) c FROM outputs GROUP BY project_id ORDER BY c LIMIT 1")
        count_before = db.fetch_one("SELECT COUNT(*) AS c FROM outputs WHERE project_id=?", (target["project_id"],))["c"]
        result = projects_service.purge_project(target["project_id"])
        after = db.fetch_one("SELECT COUNT(*) AS c FROM outputs WHERE project_id=?", (target["project_id"],))["c"]
        check("purge_project 清掉全部产物", after == 0, f"{count_before} -> 0，保留声线 {result['retained_voices']}")
        check("purge 后 FK 仍然干净", not db.fetch_all("PRAGMA foreign_key_check"))

    print(f"\n结果：{len(PASSED)} 通过 / {len(FAILED)} 失败")
    for item in FAILED:
        print(f"  × {item}")
    if FAILED:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
