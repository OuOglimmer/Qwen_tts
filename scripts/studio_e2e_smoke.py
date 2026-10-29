"""Reusable end-to-end smoke test for the local Voice Studio workflow."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys

import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from studio.database import Database
from studio.audio import wav_to_mp3
from studio.model_manager import ModelManager
from studio.service import StudioService


def main() -> None:
    database = Database()
    models = ModelManager()
    service = StudioService(database, models)
    suffix = datetime.now().strftime("%Y%m%d-%H%M%S")
    project_id = service.create_project(f"系统验收-{suffix}")

    request_id, candidates = service.generate_candidates(
        project_id=project_id,
        name="验收声线",
        prompt="清晰、自然、温和的成年女性普通话声音，语速适中。",
        audition_text="你好，这是声线候选测试。",
        language="Chinese",
        count=1,
    )
    assert len(candidates) == 1
    service.select_candidate(request_id, candidates[0]["id"])
    fixed = service.fix_selected_voices(project_id)
    assert fixed == ["验收声线"]

    voices = service.list_voices(project_id)
    voice = next(item for item in voices if item["name"] == "验收声线")
    assert Path(voice["prompt_path"]).is_file()
    assert Path(voice["reference_wav"]).is_file()
    assert Path(voice["preview_mp3"]).is_file()

    upload_source = wav_to_mp3(
        ROOT / "outputs" / "voicedesign" / "smoke_design.wav",
        ROOT / "data" / "projects" / project_id / "upload_input.mp3",
    )
    uploaded_id, uploaded_preview = service.archive_uploaded_voice(
        project_id=project_id,
        name="上传验收声线",
        source_audio=str(upload_source),
        ref_text="你好，这是声线创建模型的部署测试。",
        language="Chinese",
        x_vector_only=False,
        preview_text="你好，这是上传 MP3 后的克隆试听。",
    )
    uploaded = database.fetch_one("SELECT * FROM voices WHERE id=?", (uploaded_id,))
    assert uploaded is not None
    assert Path(uploaded["reference_wav"]).is_file()
    assert Path(uploaded_preview).is_file()
    print(f"OK upload: {uploaded_preview}")

    added = service.add_batch_lines(
        project_id,
        voice["id"],
        "Chinese",
        "这是第一条批量配音。\n这是第二条批量配音。",
    )
    assert added == 2
    result = service.run_batch(project_id)
    assert result == {"succeeded": 2, "failed": 0}

    rows = service.list_batch_items(project_id)
    assert len(rows) == 2
    for row in rows:
        output = Path(row["output_mp3"])
        # 批量产物自动选入成品。
        assert row["status"] == "selected"
        assert output.is_file() and output.stat().st_size > 0
        assert sf.info(output).duration > 0
        print(f"OK {row['position']}: {output} ({sf.info(output).duration:.2f}s)")

    models.unload()
    print(f"PASS project={project_id}")


if __name__ == "__main__":
    main()
