"""Voice asset component: the reusable voice library.

The library is **global**: ``voices`` rows are shippable assets (reference
audio, archived clone prompt, preview) that are no longer owned by a single
project.  ``voices.project_id`` only records the *source* project (nullable:
uploads may belong to no project at all), while ``project_voices`` is the
per-project roster that decides which voices a project may bind.  Within one
project's roster display names are unique; across projects the same voice may
appear repeatedly and names may legitimately collide (shown as
``名称 · 来源项目``).
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from .audio import copy_original, normalize_to_wav, safe_filename, wav_duration_ms, wav_to_mp3, write_wav
from .config import VOICE_DIR
from .database import Database, utc_now
from .model_manager import ModelManager
from .tags import TagService


def _file_md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class VoiceAssetService:
    def __init__(self, database: Database, models: ModelManager) -> None:
        self.db = database
        self.models = models
        self.tags = TagService(database)

    def _next_roster_position(self, project_id: str) -> int:
        row = self.db.fetch_one(
            "SELECT COALESCE(MAX(position),0) AS max_position FROM project_voices WHERE project_id=?",
            (project_id,),
        )
        return int(row["max_position"]) + 1

    def get_voice(self, voice_id: str, project_id: str | None = None) -> dict[str, Any] | None:
        # v5 起 voices 不再"属于"某个项目，project_id 只表示可选的来源项目；
        # 想按项目校验请用 project_voice()（查清单）。这里保留入参只为兼容调用方。
        voice = self.db.fetch_one("SELECT * FROM voices WHERE id=?", (voice_id,))
        if voice and project_id:
            listed = self.db.fetch_one(
                "SELECT 1 FROM project_voices WHERE project_id=? AND voice_id=?",
                (project_id, voice_id),
            )
            if not listed:
                return None
        return voice

    def project_voice(self, voice_id: str, project_id: str) -> dict[str, Any] | None:
        """Active voice as bound in this project's roster (project display name wins)."""
        voice = self.db.fetch_one(
            """
            SELECT v.*, r.display_name, r.state AS roster_state, r.replaced_by
            FROM voices v
            JOIN project_voices r ON r.voice_id=v.id
            WHERE v.id=? AND r.project_id=? AND v.status='active' AND r.state='active'
            """,
            (voice_id, project_id),
        )
        if voice:
            voice["name"] = voice["display_name"]
        return voice

    def attach_voice(self, project_id: str, voice_id: str, display_name: str = "") -> str:
        """Add an existing library voice to this project's roster."""
        if not self.db.fetch_one(
            "SELECT id FROM projects WHERE id=? AND status!='deleted'", (project_id,)
        ):
            raise ValueError("项目不存在或已被删除")
        voice = self.db.fetch_one(
            "SELECT * FROM voices WHERE id=? AND status='active'", (voice_id,)
        )
        if not voice:
            raise ValueError("声线不存在或已被替换，无法添加到项目")
        existing = self.db.fetch_one(
            "SELECT state, display_name FROM project_voices WHERE project_id=? AND voice_id=?",
            (project_id, voice_id),
        )
        if existing and existing["state"] == "active":
            raise ValueError("该声线已在本项目清单中")
        clean_display = str(display_name or "").strip() or str(existing["display_name"] if existing else "").strip() or str(voice["name"]).strip()
        if not clean_display:
            raise ValueError("清单名称不能为空")
        clash = self.db.fetch_one(
            "SELECT voice_id FROM project_voices WHERE project_id=? AND display_name=? AND state='active'",
            (project_id, clean_display),
        )
        if clash:
            raise ValueError(f"本项目声线清单已有同名「{clean_display}」，请使用其他名称")
        if existing:
            # 换版留下的历史行：重新挂载等于复活它，而不是再插一条同名记录。
            self.db.execute(
                """
                UPDATE project_voices
                SET state='active', display_name=?, replaced_by=NULL, updated_at=?
                WHERE project_id=? AND voice_id=?
                """,
                (clean_display, utc_now(), project_id, voice_id),
            )
        else:
            self.db.execute(
                """
                INSERT INTO project_voices(project_id,voice_id,display_name,position,state,created_at,updated_at)
                VALUES(?,?,?,?,'active',?,?)
                """,
                (
                    project_id, voice_id, clean_display,
                    self._next_roster_position(project_id), utc_now(), utc_now(),
                ),
            )
        self.db.log_activity(project_id, "voice", voice_id, "从声线库添加声线", clean_display)
        return clean_display

    def archive_uploaded_voice(
        self,
        project_id: str | None,
        name: str,
        source_audio: str,
        ref_text: str,
        language: str,
        x_vector_only: bool,
        preview_text: str,
        tags: str = "",
        notes: str = "",
        ref_language: str = "",
    ) -> tuple[str, str]:
        project_id = str(project_id or "").strip() or None
        if project_id and not self.db.fetch_one(
            "SELECT id FROM projects WHERE id=? AND status!='deleted'", (project_id,)
        ):
            raise ValueError("来源项目不存在或已被删除")
        if not name.strip() or not source_audio:
            raise ValueError("声线名称和参考音频不能为空")
        if not x_vector_only and not ref_text.strip():
            raise ValueError("高质量克隆模式必须填写参考音频对应文字")
        if not preview_text.strip():
            raise ValueError("请填写用于确认声线的试听文本")
        voice_id = self.db.new_id()
        voice_dir = VOICE_DIR / voice_id
        voice_dir.mkdir(parents=True, exist_ok=True)
        # Normalize first so the duplicate check compares the same encoding on
        # both sides; a repeated upload of the same take must not archive twice.
        reference_wav = normalize_to_wav(source_audio, voice_dir / "reference.wav")
        audio_hash = _file_md5(reference_wav)
        # 全局按参考音频去重：命中即提示复用现有资产，仅补一条清单关联。
        duplicate = self.db.fetch_one(
            """
            SELECT v.name, p.name AS source_name FROM voices v
            LEFT JOIN projects p ON p.id=v.source_project_id
            WHERE v.reference_hash=? AND v.status='active'
            """,
            (audio_hash,),
        )
        if duplicate:
            shutil.rmtree(voice_dir, ignore_errors=True)
            source = duplicate["source_name"] or "公共库"
            raise ValueError(
                f"声线库已有相同参考音频的声线「{duplicate['name']}」（来源：{source}），"
                "请直接将其添加到项目清单，无需重复上传"
            )
        if project_id:
            clash = self.db.fetch_one(
                "SELECT 1 FROM project_voices WHERE project_id=? AND display_name=? AND state='active'",
                (project_id, name.strip()),
            )
            if clash:
                shutil.rmtree(voice_dir, ignore_errors=True)
                raise ValueError(f"本项目声线清单已有同名「{name.strip()}」，请更换名称")
        copy_original(source_audio, voice_dir)
        prompt_items = self.models.create_prompt(
            str(reference_wav), ref_text.strip() or None, x_vector_only
        )
        prompt_path = self.models.save_prompt(prompt_items, voice_dir / "prompt.safetensors")
        waveform, sample_rate = self.models.clone(
            preview_text.strip(), language, prompt_items=prompt_items
        )
        preview_wav = write_wav(voice_dir / "preview.wav", waveform, sample_rate)
        preview_mp3 = wav_to_mp3(preview_wav, voice_dir / "preview.mp3")
        preview_wav.unlink(missing_ok=True)
        metadata = {
            "id": voice_id,
            "name": name.strip(),
            "source": "upload",
            "language": language,
            "ref_language": ref_language.strip(),
            "ref_text": ref_text.strip(),
            "x_vector_only": x_vector_only,
        }
        (voice_dir / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        now = utc_now()
        self.db.execute(
            """
            INSERT INTO voices(id,source_project_id,name,source,prompt,language,ref_language,ref_text,reference_wav,preview_mp3,prompt_path,reference_hash,tags,notes,reference_ms,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (voice_id, project_id, name.strip(), "upload", None, language, ref_language.strip(), ref_text.strip() or None, str(reference_wav), str(preview_mp3), str(prompt_path), audio_hash, "", notes.strip(), wav_duration_ms(reference_wav), now, now),
        )
        # tags 入参还是逗号串（上传表单没变），由 TagService 解析成关系表再回刷
        # voices.tags 缓存列 —— 前端与接口契约都不用动。
        self.tags.set_voice_tags(voice_id, names=tags)
        if project_id:
            self.db.execute(
                """
                INSERT INTO project_voices(project_id,voice_id,display_name,position,state,created_at,updated_at)
                VALUES(?,?,?,?,'active',?,?)
                """,
                (
                    project_id, voice_id, name.strip(),
                    self._next_roster_position(project_id), now, now,
                ),
            )
        self.db.log_activity(project_id, "voice", voice_id, "上传并固化声线", name.strip())
        return voice_id, str(preview_mp3)

    def archive_designed_voice(
        self,
        name: str,
        prompt: str,
        language: str,
        audition_text: str,
        audio_path: str,
        tags: str = "",
        notes: str = "",
    ) -> tuple[str, str]:
        """把一次 VoiceDesign 试听固化成可复用声线。

        这种声线没有参考音频，只有自然语言描述 —— ``voices`` 表本来就支持
        （``source='voice_design'``、``prompt`` 存描述、``reference_wav`` 指向
        这次生成的声音本身），工作台第 1 步的「固化声线」走的就是同一条路。
        这里把那套写法抽出来，去掉对项目和 ``voice_requests`` 槽位的依赖，
        让「全部声线」页能"生成 → 一步入库"。

        一律进公共库（``project_id = NULL``）：设计声线不属于任何项目，
        某个项目要用它，去工作台第 1 步「复用声线库」挂载即可。
        """
        clean_name = str(name or "").strip()
        clean_prompt = str(prompt or "").strip()
        clean_audition = str(audition_text or "").strip()
        if not clean_name or not clean_prompt:
            raise ValueError("声线名称和声线提示词不能为空")
        if not clean_audition:
            raise ValueError("请先填写用于生成这段试听的文本")
        source = Path(str(audio_path or ""))
        if not source.is_file():
            raise ValueError("试听产物不存在或已过期，请重新生成后再入库")
        voice_id = self.db.new_id()
        voice_dir = VOICE_DIR / voice_id
        voice_dir.mkdir(parents=True, exist_ok=True)
        reference_wav = normalize_to_wav(source, voice_dir / "reference.wav")
        # create_prompt 要成对的"音频 + 对应文本"：设计声线没有素材文本，
        # 这段生成音频本身就是音色来源，用生成它的那段试听文本即可（与固化路径一致）。
        prompt_items = self.models.create_prompt(str(reference_wav), clean_audition, False)
        prompt_path = self.models.save_prompt(prompt_items, voice_dir / "prompt.safetensors")
        preview_mp3 = voice_dir / "preview.mp3"
        if source.suffix.lower() == ".mp3":
            # 已经是成品 MP3，直接搬过来，不再走一次解码→编码。
            shutil.copy2(source, preview_mp3)
        else:
            wav_to_mp3(source, preview_mp3)
        metadata = {
            "id": voice_id,
            "name": clean_name,
            "source": "voice_design",
            "prompt": clean_prompt,
            "language": language,
            "ref_text": clean_audition,
            "x_vector_only": False,
        }
        # 试听时写的血缘边车（seed / design_id / 原文）：有就跟入库记录一起存下来，
        # "这只是哪次设计试出来的"从此可回答，而不是靠名字猜。
        provenance_path = source.with_suffix(".json")
        if provenance_path.is_file():
            try:
                metadata["design"] = json.loads(provenance_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
        (voice_dir / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        now = utc_now()
        self.db.execute(
            """
            INSERT INTO voices(id,source_project_id,name,source,prompt,language,ref_language,ref_text,reference_wav,preview_mp3,prompt_path,reference_hash,tags,notes,reference_ms,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (voice_id, None, clean_name, "voice_design", clean_prompt, language, "", clean_audition, str(reference_wav), str(preview_mp3), str(prompt_path), _file_md5(reference_wav), "", notes.strip(), wav_duration_ms(reference_wav), now, now),
        )
        self.tags.set_voice_tags(voice_id, names=tags)
        self.db.log_activity(None, "voice", voice_id, "设计声线固化入库", clean_name)
        return voice_id, str(preview_mp3)

    def list_voices(
        self, project_id: str | None = None, include_replaced: bool = False
    ) -> list[dict[str, Any]]:
        """项目清单。

        默认只给 ``state='active'`` 的当前版本；``include_replaced=True`` 时把换版
        留下的历史行一并带上（成品归属溯源面板用）。两个条件都要：
        资产被 replaced 但清单行还没跟上时（历史数据），不该出现在选声线下拉里。
        """
        if not project_id:
            return []
        # 历史行指向的资产本身就是 replaced 状态，所以放开 include_replaced 时
        # 连资产状态过滤一起放开，否则只剩清单侧的 replaced 行、看不到资产信息。
        asset_filter = "" if include_replaced else "AND v.status='active'"
        state_filter = "" if include_replaced else "AND r.state='active'"
        return self.db.fetch_all(
            f"""
            SELECT v.*, r.display_name, r.state AS roster_state, r.replaced_by, r.position AS roster_position,
                   p.name AS source_project_name, p.status AS source_project_status
            FROM project_voices r
            JOIN voices v ON v.id=r.voice_id {asset_filter}
            LEFT JOIN projects p ON p.id=v.source_project_id
            WHERE r.project_id=? {state_filter}
            ORDER BY r.position, r.created_at
            """,
            (project_id,),
        )

    def library_voices(
        self, tag_names: str | list[str] | None = None, tag_mode: str = "any"
    ) -> list[dict[str, Any]]:
        """The global voice library: every active asset with its source mark."""
        rows = self.db.fetch_all(
            """
            SELECT v.*,
                   p.name AS source_project_name, p.status AS source_project_status,
                   (SELECT COUNT(*) FROM project_voices r
                     WHERE r.voice_id=v.id AND r.state='active') AS usage_count
            FROM voices v
            LEFT JOIN projects p ON p.id=v.source_project_id
            WHERE v.status='active'
            ORDER BY v.created_at DESC
            """
        )
        allowed = self.tags.filter_voice_ids(tag_names, tag_mode)
        if allowed is None:
            return rows
        keep = set(allowed)
        return [row for row in rows if row["id"] in keep]

    def _voice_choices(self, project_id: str | None = None) -> list[tuple[str, str]]:
        """(显示名, id) 形态的声线列表。曾供已下线的 Gradio 下拉框使用，现仅内部复用。"""
        if not project_id:
            return []
        voices = self.db.fetch_all(
            """
            SELECT v.id, v.source, r.display_name
            FROM project_voices r JOIN voices v ON v.id=r.voice_id
            WHERE r.project_id=? AND v.status='active' AND r.state='active'
            ORDER BY r.position, r.created_at
            """,
            (project_id,),
        )
        return [(f"{row['display_name']} · {row['source']}", row["id"]) for row in voices]

    def generate_with_voice(
        self,
        voice_id: str,
        text: str,
        language: str,
        destination: Path,
        project_id: str | None = None,
    ) -> str:
        # Existing batch items may intentionally reference a replaced voice,
        # so the asset itself is looked up by id only（v5 起 voices 没有
        # project_id 列了：传项目只用来校验"在不在本项目清单里"）。
        voice = self.db.fetch_one("SELECT * FROM voices WHERE id=?", (voice_id,))
        if not voice:
            raise ValueError("声线不存在")
        if project_id and not self.db.fetch_one(
            "SELECT 1 FROM project_voices WHERE project_id=? AND voice_id=?",
            (project_id, voice_id),
        ):
            raise ValueError("声线不在当前项目清单中")
        metadata_path = Path(voice["prompt_path"]).parent / "metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        prompt_items = self.models.load_prompt(
            Path(voice["prompt_path"]),
            voice["ref_text"],
            bool(metadata.get("x_vector_only", False)),
        )
        waveform, sample_rate = self.models.clone(text.strip(), language, prompt_items=prompt_items)
        wav_path = write_wav(destination.with_suffix(".wav"), waveform, sample_rate)
        mp3_path = wav_to_mp3(wav_path, destination.with_suffix(".mp3"))
        wav_path.unlink(missing_ok=True)
        return str(mp3_path)

    def delete_voice_asset(self, project_id: str, voice_id: str) -> str:
        """Delete one library voice asset (not just this project's association).

        Generated outputs are never touched, but an asset still listed in
        another live project's roster — or bound to any project's tasks — is
        refused outright, so nobody loses a sound another tab is using.
        """
        voice = self.db.fetch_one("SELECT * FROM voices WHERE id=?", (voice_id,))
        if not voice:
            raise ValueError("请选择有效声线资产")
        # 换版留下的 replaced 资产同样允许删（它们本来就是因为"删不掉"才成了僵尸）：
        # 只要没有任何成品绑定，删掉它不影响交付物。
        if voice["status"] != "active":
            bound = self.db.fetch_one(
                "SELECT COUNT(*) AS total FROM outputs WHERE voice_id=?", (voice_id,)
            )
            if int(bound["total"]):
                raise ValueError(
                    f"该声线是已换版的旧资产，但仍被 {int(bound['total'])} 条成品引用，"
                    "请先用新声线重录或删除这些成品"
                )
        # 本项目里任何来源的成品都算占用：只查 origin='batch' 会漏掉单句成品和补录，
        # 那两种同样带着 voice_id 外键，DELETE 时被 RESTRICT 拦下变成 500。
        linked = self.db.fetch_all(
            "SELECT origin, COUNT(*) AS total FROM outputs WHERE project_id=? AND voice_id=? GROUP BY origin",
            (project_id, voice_id),
        )
        if linked:
            counts = {row["origin"]: int(row["total"]) for row in linked}
            labels = []
            if counts.get("batch"):
                labels.append(f"{counts['batch']} 条批量任务")
            if counts.get("single"):
                labels.append(f"{counts['single']} 条单句成品")
            if counts.get("supplement"):
                labels.append(f"{counts['supplement']} 条补录成品")
            raise ValueError(
                f"该声线仍被本项目的 {'、'.join(labels) or '成品'}占用，请先删除这些成品或任务"
            )
        others = self.db.fetch_all(
            """
            SELECT DISTINCT p.name FROM (
                SELECT r.project_id AS pid FROM project_voices r
                JOIN projects p2 ON p2.id=r.project_id AND p2.status!='deleted'
                WHERE r.voice_id=? AND r.project_id<>? AND r.state='active'
                UNION
                SELECT o.project_id FROM outputs o
                WHERE o.voice_id=? AND o.project_id<>?
            ) use
            JOIN projects p ON p.id=use.pid
            """,
            (voice_id, project_id, voice_id, project_id),
        )
        if others:
            names = "、".join(row["name"] for row in others)
            raise ValueError(f"该声线仍被其他项目使用：{names}，请先在这些项目中解除关联")

        voice_dir = Path(voice["reference_wav"]).resolve().parent
        voices_root = VOICE_DIR.resolve()
        # 目录校验和删除都用同一组判断：只有确实位于 voices/<id>/ 时才动它。
        removable_dir = voice_dir.parent == voices_root and voice_dir.name == voice_id
        self.db.backup_snapshot(
            f"pre-delete-voice-{safe_filename(voice['name'], 'voice')}",
            assets={f"voices/{voice_id}": voice_dir} if removable_dir and voice_dir.exists() else {},
        )
        request_rows = self.db.fetch_all(
            "SELECT id FROM voice_requests WHERE project_id=? AND voice_id=?",
            (project_id, voice_id),
        )
        # 解绑槽位和删资产必须一起成功：只做一半会留下指向不存在声线的槽位。
        with self.db.transaction() as connection:
            for request in request_rows:
                selected = connection.execute(
                    "SELECT 1 FROM candidates WHERE request_id=? AND selected=1 LIMIT 1",
                    (request["id"],),
                ).fetchone()
                next_status = "selected" if selected else "candidate"
                connection.execute(
                    "UPDATE voice_requests SET voice_id=NULL,status=?,updated_at=? WHERE id=?",
                    (next_status, utc_now(), request["id"]),
                )
            connection.execute("DELETE FROM voices WHERE id=?", (voice_id,))
        # 全局删除时 project_id 为空串，不能写进 activity_log 的外键列。
        self.db.log_activity(project_id or None, "voice", voice_id, "删除声线资产", voice["name"])

        if removable_dir and voice_dir.exists():
            shutil.rmtree(voice_dir)
        return voice["name"]
