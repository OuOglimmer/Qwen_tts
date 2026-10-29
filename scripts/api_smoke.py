"""FastAPI smoke test that exercises endpoints without loading any model.

Uses a temporary data directory so the developer's real database is never
touched. Run from the project root:

    .venv\\Scripts\\python.exe scripts\\api_smoke.py

对齐 schema v6：
- ``voices`` 用 ``source_project_id``（不再是 ``project_id``），且要能通过
  ``project_voice`` 就得同时写 ``voices``（status='active'）与
  ``project_voices``（state='active'）两行。
- ``batch_items`` / ``supplement_lines`` / ``batch_item_versions`` 是视图，
  底层数据落在 ``outputs`` / ``output_versions``，插入要写底层表。
- 声线走 ``attach_voice`` 挂进项目清单，批量台词走 ``add_batch_lines`` 写
  ``outputs``，不再手工 INSERT 视图。
"""

from __future__ import annotations

import io
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["QWEN_TTS_DATA_DIR"] = str(Path(tempfile.mkdtemp(prefix="qwentts-api-smoke-")).resolve())

from fastapi.testclient import TestClient  # noqa: E402

from studio.api import app  # noqa: E402

client = TestClient(app)
failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}{(' — ' + detail) if detail and not condition else ''}")
    if not condition:
        failures.append(name)


def main() -> None:
    response = client.get("/api/health")
    check("health", response.status_code == 200 and response.json()["status"] == "ok")

    response = client.get("/api/system/status")
    check("system status", response.status_code == 200 and "data_dir" in response.json())

    response = client.get("/api/projects")
    check("projects list", response.status_code == 200 and len(response.json()) >= 1)

    response = client.post("/api/projects", json={"name": "冒烟测试项目"})
    check("project create", response.status_code == 200, str(response.json()))
    smoke_id = response.json()["id"]

    response = client.post(f"/api/projects/{smoke_id}/activate")
    check("project activate", response.status_code == 200)

    response = client.get(f"/api/projects/{smoke_id}/batch/status")
    check(
        "batch status",
        response.status_code == 200
        and response.json()["counts"] == {"pending": 0, "running": 0, "completed": 0, "selected": 0, "failed": 0},
    )

    response = client.post(f"/api/projects/{smoke_id}/batch/run")
    check("batch run without items is rejected", response.status_code == 400)

    # ---------------------------------------------------------------- 声线
    # 通过真实的上传链路造一把声线，让它同时落在 voices + project_voices，
    # 这样后续批量/补录才能用 project_voice 查到它。
    from studio.api import database, service  # noqa: E402

    now = database.fetch_one("SELECT datetime('now') AS n")["n"]
    voice_id = database.new_id()
    voices_root = Path(os.environ["QWEN_TTS_DATA_DIR"]) / "voices"
    voice_dir = voices_root / voice_id
    voice_dir.mkdir(parents=True, exist_ok=True)
    reference_wav = voice_dir / "reference.wav"
    preview_mp3 = voice_dir / "preview.mp3"
    prompt_path = voice_dir / "prompt.safetensors"
    reference_wav.write_bytes(b"RIFFfake")
    preview_mp3.write_bytes(b"ID3fake")
    prompt_path.write_bytes(b"fake-tensor")
    database.execute(
        """
        INSERT INTO voices(id,source_project_id,name,source,prompt,language,ref_language,ref_text,
                           reference_wav,preview_mp3,prompt_path,reference_hash,tags,notes,status,
                           created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,'active',?,?)
        """,
        (
            voice_id, smoke_id, "测试声线", "upload", "温柔", "Chinese", "", "你好",
            str(reference_wav), str(preview_mp3), str(prompt_path), "fakehash", "", "",
            now, now,
        ),
    )
    response = client.post(
        f"/api/projects/{smoke_id}/voices/attach", json={"voice_id": voice_id}
    )
    check("attach voice to roster", response.status_code == 200, str(response.json()))

    # ---------------------------------------------------------------- 槽位
    response = client.post(
        f"/api/projects/{smoke_id}/voice-requests", json={"count": 2, "prefix": "角色", "language": "Chinese"}
    )
    check("voice slots create", response.status_code == 200 and len(response.json()["created"]) == 2)

    request_id = response.json()["items"][0]["id"]
    response = client.put(
        f"/api/voice-requests/{request_id}",
        json={"project_id": smoke_id, "name": "角色01", "prompt": "温柔", "audition_text": "你好", "language": "Chinese"},
    )
    check("voice request draft save", response.status_code == 200)

    response = client.get(f"/api/voice-requests/{request_id}/candidates")
    check("candidates empty list", response.status_code == 200 and response.json()["candidates"] == [])

    response = client.post(f"/api/projects/{smoke_id}/batch", json={"voice_id": "missing", "text": "台词"})
    check("batch add without voice is rejected", response.status_code == 400)

    # ---------------------------------------------------------------- 批量
    # 走真实 add_batch_lines 写 outputs。
    response = client.post(
        f"/api/projects/{smoke_id}/batch",
        json={"voice_id": voice_id, "text": "你好世界\n第二句台词", "language": "Chinese"},
    )
    check("batch add lines", response.status_code == 200 and response.json()["added"] == 2, str(response.json()))

    items = database.fetch_all(
        "SELECT id, position, text FROM outputs WHERE project_id=? AND origin='batch' ORDER BY position",
        (smoke_id,),
    )
    check("batch lines landed in outputs", len(items) == 2, str(items))
    item_id = items[0]["id"]
    pending_id = items[1]["id"]

    # 此时两行都是 pending：先测编辑（只对 pending/failed 生效）与选入拦截。
    response = client.post(
        f"/api/projects/{smoke_id}/batch/save-edits",
        json={"items": [{"position": items[0]["position"], "text": "修改后的台词", "language": "English"}]},
    )
    check("save batch edits", response.status_code == 200 and response.json()["updated"] == 1, str(response.json()))
    row = database.fetch_one("SELECT text,language FROM outputs WHERE id=?", (item_id,))
    check("edits persisted", row["text"] == "修改后的台词" and row["language"] == "English", str(row))
    # pending 状态不能选入成品。
    response = client.post(f"/api/projects/{smoke_id}/batch/{pending_id}/select", json={"selected": True})
    check("select pending item rejected", response.status_code == 400, str(response.json()))

    response = client.post("/api/voices/upload")
    check("upload without file is rejected", response.status_code == 422)

    # 置 done 模拟生成完成（跳过模型），让选入成品链路可走。编辑已回改文本。
    database.execute(
        "UPDATE outputs SET gen_status='done',output_mp3=?,version=1,text='你好世界',language='Chinese',updated_at=? WHERE id=?",
        (str(Path(os.environ["QWEN_TTS_DATA_DIR"]) / "projects" / smoke_id / "outputs" / "0001.mp3"),
         now, item_id),
    )

    response = client.get(f"/api/projects/{smoke_id}/outputs")
    check("outputs excludes unselected results", response.json()["items"] == [])

    response = client.post(f"/api/projects/{smoke_id}/batch/{item_id}/select", json={"selected": True})
    check("select batch item", response.status_code == 200 and response.json()["item"]["status"] == "selected", str(response.json()))
    response = client.get(f"/api/projects/{smoke_id}/outputs")
    check("outputs includes selected result", len(response.json()["items"]) == 1)
    response = client.post(f"/api/projects/{smoke_id}/batch/{item_id}/select", json={"selected": True})
    check("double select rejected", response.status_code == 400)
    response = client.post(f"/api/projects/{smoke_id}/batch/{item_id}/select", json={"selected": False})
    check("unselect returns to completed", response.status_code == 200 and response.json()["item"]["status"] == "completed")

    response = client.get("/api/voice-regeneration/progress")
    check("voice regen progress idle", response.status_code == 200 and response.json() is None)
    response = client.post("/api/voice-regeneration/cancel")
    check("voice regen cancel when idle", response.status_code == 200 and response.json()["cancelled"] is False)

    # ---------------------------------------------------------------- 版本
    outputs_dir = Path(os.environ["QWEN_TTS_DATA_DIR"]) / "projects" / smoke_id / "outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)

    def version_path(version: int) -> Path:
        return outputs_dir / f"0001_测试声线_v{version:02d}.mp3"

    def fake_output(version: int) -> Path:
        path = version_path(version)
        path.write_bytes(f"audio-v{version}".encode("utf-8"))
        return path

    # 重置为 completed 再重新选，避免上一步 already selected 的干扰。
    database.execute("UPDATE outputs SET in_final=0 WHERE id=?", (item_id,))
    for version in (1, 2, 3, 4):
        service.batch._record_version(item_id, version, str(fake_output(version)), f"第{version}版台词")
    response = client.get(f"/api/projects/{smoke_id}/batch")
    ver_item = next(item for item in response.json() if item["id"] == item_id)
    check("batch item version increments", ver_item["version"] == 4)
    # VERSIONS_KEPT=5：4 版未超上限，全部保留，最新的在前。
    check(
        "batch item keeps last versions",
        [entry["version"] for entry in ver_item["versions"]] == [4, 3, 2, 1]
        and all(entry["url"] for entry in ver_item["versions"]),
        str([entry["version"] for entry in ver_item["versions"]]),
    )
    check(
        "old version files kept within limit",
        version_path(1).exists() and version_path(2).exists() and version_path(4).exists(),
    )

    # ---------------------------------------------------------------- 导出
    database.execute("UPDATE outputs SET gen_status='done',in_final=1,text='第四版台词' WHERE id=?", (item_id,))
    export_dir = Path(os.environ["QWEN_TTS_DATA_DIR"]) / "export-target"

    guarded_calls = [
        (f"/api/projects/{smoke_id}/outputs/export", {"destination": str(export_dir), "excerpt_length": 6}),
        ("/api/dialog/pick-folder", {}),
        ("/api/system/open-folder", {"path": str(Path(os.environ["QWEN_TTS_DATA_DIR"]))}),
    ]
    for path, body in guarded_calls:
        response = client.post(path, json=body)
        check(f"{path} closed by default", response.status_code == 403, str(response.status_code))
    check("no export folder created while closed", not export_dir.exists())

    os.environ["QWEN_TTS_ALLOW_HOST_FS"] = "1"
    outside = Path(tempfile.gettempdir()) / "qwentts-outside-data-dir"
    response = client.post(
        f"/api/projects/{smoke_id}/outputs/export",
        json={"destination": str(outside), "excerpt_length": 6},
    )
    check(
        "export destination confined to the data dir",
        response.status_code == 400 and not outside.exists(),
        str(response.json()),
    )

    response = client.post(
        f"/api/projects/{smoke_id}/outputs/export",
        json={"destination": str(export_dir), "excerpt_length": 6},
    )
    check("export selected item", response.status_code == 200 and response.json()["count"] == 1, str(response.json()))
    entries_v1, _paths_v1 = service.exports.export_plan(smoke_id, 6)
    check(
        "export writes the planned file and no manifest",
        entries_v1[0]["version"] == 4
        and (export_dir / entries_v1[0]["file"]).is_file()
        and not (export_dir / "manifest.json").exists(),
        str(entries_v1[0]),
    )
    first_export_name = entries_v1[0]["file"]

    version5 = fake_output(5)
    service.batch._record_version(item_id, 5, str(version5), "第五版重录台词")
    database.execute("UPDATE outputs SET text='第五版重录台词' WHERE id=?", (item_id,))
    response = client.get(f"/api/projects/{smoke_id}/outputs")
    check("outputs row carries version", response.json()["items"][0]["version"] == 5)
    response = client.post(
        f"/api/projects/{smoke_id}/outputs/export",
        json={"destination": str(export_dir), "excerpt_length": 6},
    )
    entries_v2, _paths_v2 = service.exports.export_plan(smoke_id, 6)
    check(
        "re-export rewrites the audio under the new name",
        entries_v2[0]["version"] == 5
        and (export_dir / entries_v2[0]["file"]).is_file()
        and first_export_name != entries_v2[0]["file"],
    )
    # 导出不再写 manifest.json（交付只有音频）。代价是"上一轮遗留文件"失去了凭据，
    # 这里不复原也不猜测，明确留着 —— 免得以后有人以为漏了清理。
    check(
        "previous export file is left alone (no manifest to identify it)",
        (export_dir / first_export_name).is_file(),
    )

    # 浏览器导出清单：文件名必须和后端落盘导出完全一致（单一真相源）。
    response = client.get(f"/api/projects/{smoke_id}/outputs/export-plan?excerpt_length=6")
    check("export plan status", response.status_code == 200, str(response.json()))
    plan = response.json()
    check(
        "export plan matches on-disk export",
        [entry["file"] for entry in plan["files"]] == [entry["file"] for entry in entries_v2]
        and all(entry["url"].startswith("/api/files/") for entry in plan["files"])
        and plan["count"] == len(plan["files"]),
    )
    # 清单字段：混版追溯（voice_id / voice_version / tag_prefix）与逐条倍率(speed)都在，
    # 但不再有 app —— 那个字段只为 manifest.json 服务，文件已经不写了。
    check(
        "export plan carries per-row metadata",
        all(
            set(entry)
            == {"position", "source", "source_position", "voice", "voice_id", "voice_version",
                "text", "language", "version", "tag_prefix", "speed", "file", "url"}
            for entry in plan["files"]
        ),
        str(plan["files"][0].keys()),
    )
    response = client.get(f"/api/projects/{smoke_id}/outputs/export-plan?excerpt_length=99")
    check("export plan rejects bad excerpt length", response.status_code == 422)

    # ---------------------------------------------------------------- ZIP
    response = client.get(f"/api/projects/{smoke_id}/outputs/archive.zip?excerpt_length=6")
    archive_names: list[str] = []
    if response.status_code == 200:
        with zipfile.ZipFile(io.BytesIO(response.content)) as bundle:
            archive_names = bundle.namelist()
    check(
        "archive.zip holds audio only",
        archive_names == [entry["file"] for entry in entries_v2],
        str(archive_names),
    )
    check(
        "archive.zip leaves no copy on the server",
        not (Path(os.environ["QWEN_TTS_DATA_DIR"]) / "projects" / smoke_id / "exports").exists(),
    )
    check(
        "archive.zip names the download",
        "attachment" in response.headers.get("content-disposition", ""),
    )
    response = client.get(f"/api/projects/{smoke_id}/outputs/archive.zip?excerpt_length=6")
    check("archive.zip repeatable", response.status_code == 200)

    # ---------------------------------------------------------------- 文件通道
    probe = Path(os.environ["QWEN_TTS_DATA_DIR"]) / "probe.json"
    probe.write_text('{"secret": 1}', encoding="utf-8")
    check("non-audio data file blocked", client.get("/api/files/probe.json").status_code == 404)
    check(
        "audio data file served",
        client.get(f"/api/files/{quote('projects')}/{smoke_id}/outputs/{quote(version_path(5).name)}").status_code == 200,
    )
    probe.unlink(missing_ok=True)

    # 清理版本产物，进入下一段。
    database.execute("DELETE FROM output_versions WHERE output_id=?", (item_id,))
    for version in (2, 3, 4, 5):
        fake_output(version).unlink(missing_ok=True)

    # ---------------------------------------------------------------- 排序
    # 两条都置 done + selected，且 output_mp3 指向真实存在的文件，供排序/导出断言。
    database.execute(
        "UPDATE outputs SET in_final=1,gen_status='done',text='第一句',output_mp3=? WHERE id=?",
        (str(outputs_dir / "0001.mp3"), item_id),
    )
    (outputs_dir / "0001.mp3").write_bytes(b"audio")
    # 第二条台词也置 done + selected，供排序断言两条都进成品。
    second_id = items[1]["id"]
    database.execute(
        "UPDATE outputs SET gen_status='done',in_final=1,output_mp3=?,version=1 WHERE id=?",
        (str(outputs_dir / "0002.mp3"), second_id),
    )
    (outputs_dir / "0002.mp3").write_bytes(b"audio")

    order_keys = [f"batch:{item_id}", f"batch:{second_id}"]
    client.put(f"/api/projects/{smoke_id}/outputs/order", json={"order": [order_keys[1], order_keys[0]]})
    response = client.get(f"/api/projects/{smoke_id}/outputs")
    check(
        "outputs follow saved order",
        [row["order_key"] for row in response.json()["items"]] == [order_keys[1], order_keys[0]],
        str([row["order_key"] for row in response.json()["items"]]),
    )
    response = client.post(
        f"/api/projects/{smoke_id}/outputs/export",
        json={"destination": str(export_dir), "excerpt_length": 6},
    )
    entries_ordered, _paths_ordered = service.exports.export_plan(smoke_id, 6)
    check(
        "export numbering follows manual order",
        [entry["position"] for entry in entries_ordered] == [1, 2],
    )
    client.put(f"/api/projects/{smoke_id}/outputs/order", json={"order": []})
    response = client.get(f"/api/projects/{smoke_id}/outputs")
    check(
        "reset order restores default",
        [row["order_key"] for row in response.json()["items"]] == order_keys,
    )

    # ---------------------------------------------------------------- 补录
    response = client.post(
        f"/api/projects/{smoke_id}/outputs/supplement",
        json={"voice_id": "missing", "text": "台词", "language": "Chinese"},
    )
    check("supplement without voice rejected", response.status_code == 400)
    response = client.post(
        f"/api/projects/{smoke_id}/outputs/supplement",
        json={"voice_id": voice_id, "text": "  ", "language": "Chinese"},
    )
    check("supplement empty text rejected", response.status_code == 400)
    # 造一条 failed 补录行（底层 outputs），验证它仍可见。
    supp_id = database.new_id()
    database.execute(
        """
        INSERT INTO outputs(id,project_id,voice_id,voice_name,origin,position,text,language,
                            gen_status,in_final,error,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,'failed',0,?,?,?)
        """,
        (supp_id, smoke_id, voice_id, "测试声线", "supplement", 0, "补录台词", "Chinese", "模拟生成失败", now, now),
    )
    response = client.get(f"/api/projects/{smoke_id}/outputs")
    supp_rows = [row for row in response.json()["items"] if row["source"] == "supplement"]
    check(
        "failed supplement stays visible",
        len(supp_rows) == 1 and supp_rows[0]["url"] is None and supp_rows[0]["error"],
        str(supp_rows),
    )
    response = client.delete(f"/api/projects/{smoke_id}/outputs/supplement/{supp_id}")
    check("supplement delete", response.status_code == 200, str(response.json()))

    # 清理本段造的批量行，收尾。
    database.execute("DELETE FROM outputs WHERE project_id=? AND origin IN ('batch','supplement')", (smoke_id,))
    shutil.rmtree(export_dir, ignore_errors=True)

    response = client.get(f"/api/projects/{smoke_id}/outputs")
    check("outputs listing", response.status_code == 200 and response.json()["items"] == [])

    # ---------------------------------------------------------------- 安全
    response = client.get("/api/files/../../etc/passwd")
    check("path traversal blocked", response.status_code == 404)

    response = client.get("/api/files/app.db")
    check("database file blocked", response.status_code == 404)

    # ---------------------------------------------------------------- 删除
    # archive 是软删：项目从导航列表消失，但数据保留、接口仍可访问（可恢复）。
    response = client.delete(f"/api/projects/{smoke_id}")
    check("project archive", response.status_code == 200, str(response.json()))
    response = client.get("/api/projects")
    check(
        "archived project hidden from list",
        all(row["id"] != smoke_id for row in response.json()),
    )
    response = client.get(f"/api/projects/{smoke_id}/batch")
    check("archived project data still reachable", response.status_code == 200)

    # purge 是真删行：彻底删除后接口才 404。
    response = client.delete(f"/api/projects/{smoke_id}?mode=purge")
    check("project purge", response.status_code == 200, str(response.json()))
    response = client.get(f"/api/projects/{smoke_id}/batch")
    check("purged project returns 404", response.status_code == 404, str(response.status_code))

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s): {', '.join(failures)}")
        raise SystemExit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
