"""Export component: deliverable packaging of finished audio.

Reads the three result sources (selected batch items via the batch component,
retained single-line outputs via the workflow component, and recorded
supplements) and turns them into deliverables: a streamed zip, a browser-side
export plan, or — only in single-machine mode — a copy into a folder on this
machine.  It also owns the products-page manual ordering: rows carry a
stable ``order_key`` and the saved order decides both the list the page shows
and the sequence (therefore the numbering) of exported files.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from .audio import check_speed, safe_filename, tempo_to_file
from .config import DATA_DIR, PROJECT_DIR
from .database import Database, utc_now
from .tags import TagService
from .takes import current_versions, dedupe_latest, voice_versions
from .voice_workflow import VoiceWorkflowService
from .batch import BatchService
from .supplement import SupplementService


# 历史导出目录里可能还留着旧版本写下的 manifest.json，其每条记录带这个应用标记。
# 只有认得出是自己的，才敢按它清理过时 mp3、并把这份旧清单删掉；别人的清单不动。
# 新版导出不再生成 manifest.json —— 交付就是纯音频文件。
MANIFEST_APP = "qwen-tts-voiceover-studio"


class _ChunkSink:
    """A write-only, non-seekable file-like that buffers bytes for streaming.

    ``zipfile`` detects the missing ``seek`` and switches to data descriptors,
    so each entry can be flushed to the HTTP response as soon as it is packed.
    """

    def __init__(self) -> None:
        self._chunks: list[bytes] = []

    def write(self, data: bytes) -> int:
        self._chunks.append(bytes(data))
        return len(data)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass

    def seekable(self) -> bool:
        return False

    def drain(self) -> list[bytes]:
        chunks, self._chunks = self._chunks, []
        return chunks



class ExportService:
    def __init__(
        self,
        database: Database,
        batch: BatchService,
        workflow: VoiceWorkflowService,
        supplements: SupplementService,
    ) -> None:
        self.db = database
        self.batch = batch
        self.workflow = workflow
        self.supplements = supplements
        self.tags = TagService(database)

    # ---------------------------------------------------------- ordering

    def stored_output_order(self, project_id: str) -> list[str]:
        """Saved manual order as ``<origin>:<id>`` keys (schema v5: output_order 表)."""
        return [
            f"{row['origin']}:{row['output_id']}"
            for row in self.db.fetch_all(
                """
                SELECT ord.output_id, o.origin
                FROM output_order ord JOIN outputs o ON o.id=ord.output_id
                WHERE ord.project_id=? ORDER BY ord.position
                """,
                (project_id,),
            )
        ]

    def set_output_order(self, project_id: str, keys: list[str]) -> int:
        """Persist the manual order as a list of ``source:id`` keys.

        An empty list resets to the default order (new rows then appear in
        their natural position order).
        """
        clean: list[str] = []
        for key in keys or []:
            if not isinstance(key, str) or not key.strip():
                raise ValueError("排序数据格式不正确")
            if key not in clean:
                clean.append(key)
        with self.db.transaction() as connection:
            connection.execute("DELETE FROM output_order WHERE project_id=?", (project_id,))
            position = 0
            for key in clean:
                prefix, _, output_id = key.partition(":")
                if prefix not in ("batch", "supplement", "single") or not output_id:
                    continue
                exists = connection.execute(
                    "SELECT 1 FROM outputs WHERE id=? AND project_id=? AND origin=?",
                    (output_id, project_id, prefix),
                ).fetchone()
                if not exists:
                    continue
                position += 1
                connection.execute(
                    "INSERT OR IGNORE INTO output_order(project_id,output_id,position) VALUES(?,?,?)",
                    (project_id, output_id, position),
                )
        return position

    def order_output_rows(self, project_id: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Apply the saved manual order; rows never seen before keep appending."""
        saved = self.stored_output_order(project_id)
        by_key = {row["order_key"]: row for row in rows}
        ordered = [by_key.pop(key) for key in saved if key in by_key]
        ordered.extend(by_key.values())
        return ordered

    # --------------------------------------------------------- deliverables

    @staticmethod
    def _batch_export_filename(row: dict[str, Any], excerpt_length: int) -> str:
        length = max(5, min(int(excerpt_length), 10))
        compact_text = "".join(str(row["text"]).split())
        excerpt = safe_filename(compact_text[:length], "台词")
        voice_name = safe_filename(row["voice_name"], "voice")
        # 项目语义标签做前缀（角色定位 / 出场分组）：旁白_第二幕_1_猪猪侠_xxx.mp3。
        # 没有打过项目标签的声线 row["tag_prefix"] 为空，命名与旧版逐字节一致。
        prefix = safe_filename(str(row.get("tag_prefix") or ""), "")
        head = f"{prefix}_" if prefix else ""
        return f"{head}{int(row['export_position'])}_{voice_name}_{excerpt}.mp3"

    def _collect_export_rows(self, project_id: str) -> list[dict[str, Any]]:
        """Batch results, retained single-line outputs and supplements in one order.

        The saved manual order decides the sequence; without one, rows follow
        their natural order (batch position, then workflow singles, then
        supplements).  Rows still carry duplicates here — collapsing them is
        :meth:`_completed_export_rows`'s job, so the preflight check can report
        exactly what a delivery would merge.
        """
        rows: list[dict[str, Any]] = []
        for item in self.batch.list_batch_items(project_id):
            # Only items the user explicitly selected are deliverable assets.
            if item["status"] != "selected" or not item["output_mp3"]:
                continue
            if not Path(item["output_mp3"]).exists():
                continue
            rows.append({
                "order_key": f"batch:{item['id']}",
                "source": "batch",
                "source_position": int(item["position"]),
                "voice_id": item["voice_id"],
                "voice_name": item["voice_name"],
                "text": item["text"],
                "language": item["language"],
                "output_mp3": item["output_mp3"],
                "version": item.get("version"),
                "updated_at": item.get("updated_at"),
            })

        for item in self.workflow.single_line_outputs(project_id):
            output_mp3 = item["direct_output_mp3"]
            if not output_mp3 or not Path(output_mp3).exists():
                continue
            rows.append({
                "order_key": f"single:{item['id']}",
                "source": "single",
                "source_position": int(item["position"]),
                "voice_id": item.get("voice_id"),
                "voice_name": item["name"],
                "text": item["text"],
                "language": item["language"],
                "output_mp3": output_mp3,
                "version": None,
                "updated_at": item.get("updated_at"),
            })

        for item in self.supplements.list_lines(project_id):
            output_mp3 = item["output_mp3"]
            if item["status"] != "done" or not output_mp3 or not Path(output_mp3).exists():
                continue
            rows.append({
                "order_key": f"supplement:{item['id']}",
                "source": "supplement",
                "source_position": None,
                "voice_id": item.get("voice_id"),
                "voice_name": item["voice_name"],
                "text": item["text"],
                "language": item["language"],
                "output_mp3": output_mp3,
                "version": None,
                "updated_at": item.get("updated_at"),
            })

        return self.order_output_rows(project_id, rows)

    def _completed_export_rows(self, project_id: str) -> list[dict[str, Any]]:
        """What a delivery actually contains: one row per script line.

        同一句台词可能被多把声线各录过一条，成品库里都留着；交付时只认最后
        被选中的那条（``updated_at`` 最新），其余当场合并掉，序号原地保留，
        所以文件编号不会因为去重而跳号。
        """
        rows, _dropped = dedupe_latest(self._collect_export_rows(project_id))
        for index, row in enumerate(rows, start=1):
            row["export_position"] = index
        versions = voice_versions(self.db, project_id)
        # 项目语义标签前缀：同一把声线只查一次（一条台词一条文件名，避免 N 次查询）。
        prefixes: dict[str, str] = {}
        for row in rows:
            voice_id = row.get("voice_id")
            row["voice_version"] = versions.get(str(voice_id), 0) if voice_id else 0
            if not voice_id:
                continue
            if voice_id not in prefixes:
                prefixes[voice_id] = self.tags.project_code_for(project_id, str(voice_id))
            row["tag_prefix"] = prefixes[voice_id]
        return rows

    def export_preflight(self, project_id: str) -> dict[str, Any]:
        """导出前检查：只报告事实，不替用户做取舍。

        两件事值得在他点导出前说清楚：一是同一个角色混着几把声线的成品
        （不是错误 —— 换版后不重录本来就会这样，但他该知道），二是哪几句
        台词有多条成品、交付时会取哪一条。
        """
        rows = self._collect_export_rows(project_id)
        kept, dropped = dedupe_latest(rows)
        versions = voice_versions(self.db, project_id)
        current = current_versions(self.db, project_id)

        buckets: dict[str, dict[str, Any]] = {}
        for row in kept:
            name = str(row["voice_name"])
            voice_id = str(row.get("voice_id") or "")
            version = versions.get(voice_id, 0)
            bucket = buckets.setdefault(name, {"name": name, "total": 0, "versions": {}})
            bucket["total"] += 1
            entry = bucket["versions"].setdefault(
                str(version), {"version": version, "voice_id": voice_id, "count": 0}
            )
            entry["count"] += 1

        roles: list[dict[str, Any]] = []
        for bucket in buckets.values():
            active = current.get(bucket["name"]) or {}
            items = sorted(bucket["versions"].values(), key=lambda item: item["version"])
            for item in items:
                item["is_current"] = item["voice_id"] == active.get("voice_id")
            roles.append({
                "name": bucket["name"],
                "total": bucket["total"],
                "current_version": active.get("version", 0),
                "versions": items,
                "mixed": len(items) > 1,
            })
        roles.sort(key=lambda item: (-item["total"], item["name"]))

        return {
            "total": len(kept),
            "deduped": len(dropped),
            "deduped_samples": [
                {"voice": row["voice_name"], "text": str(row["text"])[:40]}
                for row in dropped[:5]
            ],
            "mixed": any(role["mixed"] for role in roles),
            "roles": roles,
        }

    def _build_entries(
        self,
        rows: list[dict[str, Any]],
        excerpt_length: int,
        speed: float = 1.0,
        speeds: dict[str, float] | None = None,
    ) -> list[dict[str, Any]]:
        """The one place deliverable filenames and per-row metadata are decided.

        Disk export, zip packaging and the browser-side export plan all go
        through here, so a finished folder and a finished zip are byte-for-byte
        equivalent in naming. 这里产出的是"这一条叫什么名字、用哪个倍率"，
        不再写成 manifest.json —— 导出目录里只放音频文件本身。

        ``speed`` 是整批兜底倍率，``speeds`` 按 ``order_key`` 覆盖到单条 —— 交付
        常常只有几句需要提速，逐条记录才能解释"为什么这条比别家快"。
        """
        fallback_speed = round(check_speed(speed), 3)
        # 逐条倍率先整批校验：非法值在这里就 400，不等到写盘中途才炸。
        per_row = {
            str(key): round(check_speed(value), 3)
            for key, value in (speeds or {}).items()
        }
        return [
            {
                "position": row["export_position"],
                "source": row["source"],
                "source_position": row["source_position"],
                "voice": row["voice_name"],
                "voice_id": row.get("voice_id"),
                # 第几版声线（同角色的固化顺序）；混版交付时靠这个解释每条是谁的声音。
                "voice_version": row.get("voice_version") or 0,
                "text": row["text"],
                "language": row["language"],
                "version": row.get("version"),
                "tag_prefix": row.get("tag_prefix") or "",
                # 导出副本的播放倍率（1.0 = 原速）。源文件不受影响。
                "speed": per_row.get(str(row.get("order_key") or ""), fallback_speed),
                "file": self._batch_export_filename(row, excerpt_length),
            }
            for row in rows
        ]

    def export_plan(
        self,
        project_id: str,
        excerpt_length: int = 8,
        speed: float = 1.0,
        speeds: dict[str, float] | None = None,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Per-row entries paired with the local file each one should contain.

        The API layer turns the paths into served URLs; the browser then writes
        the files into a folder the caller picked on their own machine.
        每条条目都自带自己的 ``speed``，调用方据此决定该条 URL 要不要挂变速参数。
        """
        rows = self._completed_export_rows(project_id)
        if not rows:
            raise ValueError("当前项目没有可导出的批量结果或单句成品")
        return (
            self._build_entries(rows, excerpt_length, speed, speeds),
            [row["output_mp3"] for row in rows],
        )

    def export_completed_batch(
        self,
        project_id: str,
        destination: str,
        excerpt_length: int = 8,
        speed: float = 1.0,
        speeds: dict[str, float] | None = None,
    ) -> tuple[str, int]:
        project = self.db.fetch_one("SELECT name FROM projects WHERE id=?", (project_id,))
        if not project:
            raise ValueError("项目不存在")
        rows = self._completed_export_rows(project_id)
        if not rows:
            raise ValueError("当前项目没有可导出的批量结果或单句成品")
        # 整批先算一遍倍率：任何一条非法都还没碰磁盘就报错。
        entries = self._build_entries(rows, excerpt_length, speed, speeds)
        retimed = [entry for entry in entries if abs(float(entry["speed"]) - 1.0) > 1e-6]

        clean_destination = destination.strip()
        if not clean_destination:
            raise ValueError("请填写本机导出目录")
        output_dir = Path(clean_destination).expanduser().resolve()
        # 后端那台电脑上唯一允许被调用者指定的写入范围：数据目录之内。
        # 否则等于把「往服务器任意路径写并删除文件」的能力开放给整张局域网。
        if output_dir == DATA_DIR or DATA_DIR not in output_dir.parents:
            raise ValueError(f"导出目录必须位于后端数据目录内：{DATA_DIR}")
        if output_dir.exists() and not output_dir.is_dir():
            raise ValueError("导出路径必须是文件夹")
        output_dir.mkdir(parents=True, exist_ok=True)

        # 老版本会在这个目录里写 manifest.json。它现在有两个麻烦：交付里多一个用户
        # 不要的文件；而它又是唯一能证明"这些 mp3 是我上次写的"的凭据，直接忽略的话
        # 上一轮遗留的过时音频就永远清不掉了。所以先按它清，再把它本身删掉。
        previous_files: set[str] = set()
        existing_manifest = output_dir / "manifest.json"
        manifest_is_ours = False
        if existing_manifest.exists():
            try:
                previous_manifest = json.loads(existing_manifest.read_text(encoding="utf-8"))
                if (
                    isinstance(previous_manifest, list)
                    and previous_manifest
                    and isinstance(previous_manifest[0], dict)
                    and previous_manifest[0].get("app") == MANIFEST_APP
                ):
                    manifest_is_ours = True
                    previous_files = {
                        str(entry["file"]) for entry in previous_manifest
                        if isinstance(entry, dict) and entry.get("file")
                    }
            except (OSError, json.JSONDecodeError, TypeError):
                previous_files = set()

        if retimed:
            # 写完前先整体拦截：导出目录就是成品存放目录时，ffmpeg 会拿变速结果
            # 覆盖母本（run_ffmpeg 带 -y）。原速走的是"同路径跳过"，所以这个问题
            # 只在有变速条目时存在——必须先于任何写盘动作发现它。
            for row, entry in zip(rows, entries):
                if (output_dir / entry["file"]).resolve() == Path(row["output_mp3"]).resolve():
                    raise ValueError("导出目录就是成品存放目录，变速导出会覆盖原文件，请换一个目录")
        current_files: set[str] = set()
        for row, entry in zip(rows, entries):
            filename = entry["file"]
            current_files.add(filename)
            target = output_dir / filename
            source = Path(row["output_mp3"]).resolve()
            if source == target.resolve():
                continue
            entry_speed = float(entry["speed"])
            if abs(entry_speed - 1.0) < 1e-6:
                shutil.copy2(source, target)
            else:
                tempo_to_file(source, target, entry_speed)
        for stale_name in previous_files - current_files:
            stale_path = (output_dir / stale_name).resolve()
            if stale_path.parent == output_dir and stale_path.suffix.lower() == ".mp3":
                stale_path.unlink(missing_ok=True)
        # 只删自己写的那份：目录可能是用户选的，别人的 manifest.json 不该被动。
        if manifest_is_ours:
            existing_manifest.unlink(missing_ok=True)
        # schema v5：导出目录是项目自身的一列，不再塞进 app_settings。
        self.db.execute(
            "UPDATE projects SET export_dir=?,updated_at=? WHERE id=?",
            (str(output_dir), utc_now(), project_id),
        )
        speed_note = "" if not retimed else f" · {len(retimed)} 条变速"
        self.db.log_activity(
            project_id, "batch", None, "导出全部成品", f"{len(rows)} 条 · {output_dir}{speed_note}"
        )
        return str(output_dir), len(rows)

    def iter_archive(
        self, entries: list[dict[str, Any]], paths: list[str], speed: float = 1.0
    ) -> Iterator[bytes]:
        """Stream the deliverables as a zip straight to the caller.

        Nothing is written to this machine: the archive lives only in the bytes
        handed to the HTTP response, so a shared host never accumulates
        delivery copies of every project. 压缩包里只有音频，不放 manifest.json。

        变速逐条判断（倍率就在条目上），有需要时才进临时目录：临时目录用
        with 包住整个产出期，而且压实一条就立刻写进 zip 并删掉，磁盘峰值只占
        一条音频。
        """
        fallback = round(check_speed(speed), 3)
        speeds = [float(entry.get("speed", fallback)) for entry in entries]
        sink = _ChunkSink()
        if all(abs(value - 1.0) < 1e-6 for value in speeds):
            with zipfile.ZipFile(sink, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
                for entry, path in zip(entries, paths):
                    bundle.write(path, arcname=entry["file"])
                    yield from sink.drain()
            yield from sink.drain()
            return

        with tempfile.TemporaryDirectory(prefix="qwen-tts-speed-") as scratch:
            with zipfile.ZipFile(sink, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
                for index, (entry, path) in enumerate(zip(entries, paths)):
                    entry_speed = speeds[index]
                    if abs(entry_speed - 1.0) < 1e-6:
                        bundle.write(path, arcname=entry["file"])
                        yield from sink.drain()
                        continue
                    sped = Path(scratch) / f"{index:05d}.mp3"
                    tempo_to_file(path, sped, entry_speed)
                    bundle.write(sped, arcname=entry["file"])
                    # zip 已经把整条音频读进去了，临时文件到此为止。
                    sped.unlink(missing_ok=True)
                    yield from sink.drain()
            yield from sink.drain()

    def iter_file_archive(self, entries: list[tuple[str, str]]) -> Iterator[bytes]:
        """Stream arbitrary files as a zip — ``entries`` is a list of (arcname, path).

        Same no-disk policy as :meth:`iter_archive`.  Used by the standalone
        audio-conversion tool, whose outputs sit in the temp directory and are
        throwaway artefacts, so no ``manifest.json`` is added: the download is
        just the audio files the user asked to convert.
        """
        sink = _ChunkSink()
        with zipfile.ZipFile(sink, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for arcname, path in entries:
                bundle.write(path, arcname=arcname)
                yield from sink.drain()
        yield from sink.drain()

    def create_batch_archive(self, project_id: str, excerpt_length: int = 8) -> tuple[str, int]:
        """Pack the deliverables into a real file — for the same-machine Gradio page only.

        The web UI streams :meth:`iter_archive` instead; this exists because
        ``gr.File`` needs a path on disk.
        """
        project = self.db.fetch_one("SELECT name FROM projects WHERE id=?", (project_id,))
        if not project:
            raise ValueError("项目不存在")
        entries, paths = self.export_plan(project_id, excerpt_length)
        archive_dir = PROJECT_DIR / project_id / "exports"
        archive_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_path = archive_dir / f"{safe_filename(project['name'], 'project')}_{stamp}.zip"
        with archive_path.open("wb") as sink:
            for chunk in self.iter_archive(entries, paths):
                sink.write(chunk)
        self.db.log_activity(project_id, "batch", None, "打包全部成品", f"{len(entries)} 条")
        return str(archive_path), len(entries)

