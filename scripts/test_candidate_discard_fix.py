"""Regression test: a fresh candidate round must survive its own discard step.

2026-09-30: after the service refactor, _discard_replaced_candidates selected
every unshortlisted candidate of the request — including the round it was
meant to keep — so the first generation after a server restart produced four
candidates and deleted them within the same second (GGB, project 随便).
The discard now only touches the ids snapshotted before the round started.

Runs against a temp data dir with a stubbed ModelManager; run directly:
    python scripts/test_candidate_discard_fix.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

tmp_dir = Path(tempfile.mkdtemp(prefix="qwen-tts-discard-test-"))
os.environ["QWEN_TTS_DATA_DIR"] = str(tmp_dir)

import studio.voice_workflow as voice_workflow  # noqa: E402  (after env override)
from studio.database import Database, utc_now  # noqa: E402
from studio.voice_workflow import VoiceWorkflowService  # noqa: E402


class FakeModels:
    """Stands in for ModelManager: instant, deterministic audio."""

    def design(self, text: str, language: str, prompt: str, seed: int):
        return ([0.0] * 1600, 16000)


def wait_finished(service: VoiceWorkflowService, timeout: float = 15.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = service.candidate_generation_status() or {}
        if state.get("finished") or state.get("error"):
            return state
        time.sleep(0.05)
    raise AssertionError("candidate generation did not finish in time")


def file_stem_set(rows) -> set[str]:
    return {Path(row["wav_path"]).stem for row in rows}


def count_audio_files(service: VoiceWorkflowService, project_id: str, request_id: str) -> int:
    request_dir = voice_workflow.PROJECT_DIR / project_id / "candidates" / request_id
    return len(list(request_dir.glob("*")))


def fake_wav_to_mp3(source, destination, bitrate="128k"):
    destination = Path(destination)
    shutil.copy2(source, destination)
    return destination


def main() -> int:
    db = Database(tmp_dir / "app.db")
    service = VoiceWorkflowService(db, FakeModels())
    voice_workflow.wav_to_mp3 = fake_wav_to_mp3

    project_id = "proj-test"
    db.execute(
        "INSERT INTO projects(id,name,status,created_at,updated_at) VALUES(?,?,?,?,?)",
        (project_id, "discard-regression", "draft", utc_now(), utc_now()),
    )

    # Round 1 via the background path (start_candidate_generation).
    service.start_candidate_generation(
        project_id, "GGB", "10岁，声音厚重，可爱", "“用心感受心中的那道光。”", "Chinese", 4
    )
    state = wait_finished(service)
    assert not state.get("error"), state
    assert state["generated"] == 4, state

    round1 = service.request_candidates(project_id.replace("proj-", ""), 99) if False else None
    # request_candidates takes the request id, so fetch it from the workflow table.
    request_id = db.fetch_one("SELECT id FROM voice_requests WHERE project_id=?", (project_id,))["id"]
    round1 = service.request_candidates(request_id, 99)
    assert len(round1) == 4, f"round 1 must keep its own candidates, got {len(round1)}"
    assert count_audio_files(service, project_id, request_id) == 8, "round 1 audio files missing"

    # Shortlist one round-1 candidate; it must survive across rounds.
    shortlisted_id = round1[0]["id"]
    service.set_candidate_shortlisted(request_id, shortlisted_id, True)
    shortlisted_wav = Path(db.fetch_one("SELECT wav_path FROM candidates WHERE id=?", (shortlisted_id,))["wav_path"])

    # Round 2 via the background path: drops round-1's unshortlisted trio.
    service.start_candidate_generation(
        project_id, "GGB", "10岁，声音厚重，可爱", "“用心感受心中的那道光。”", "Chinese", 4, request_id
    )
    state = wait_finished(service)
    assert not state.get("error"), state
    assert state["generated"] == 4, state

    current = db.fetch_all("SELECT * FROM candidates WHERE request_id=?", (request_id,))
    current_stems = file_stem_set(current)
    round1_stems = file_stem_set(round1)
    assert len(current) == 5, f"expected shortlisted + 4 new, got {len(current)}"
    assert shortlisted_id in {row["id"] for row in current}, "shortlisted candidate was dropped"
    assert shortlisted_wav.exists(), "shortlisted candidate audio was deleted"
    survivors_from_round1 = round1_stems & current_stems
    assert survivors_from_round1 == {shortlisted_wav.stem}, (
        f"round-1 unshortlisted candidates should be gone, survived: {survivors_from_round1}"
    )
    assert count_audio_files(service, project_id, request_id) == 10, "unexpected audio file count after round 2"

    # Round 3 via the synchronous path (generate_candidates).
    request_id2, generated = service.generate_candidates(
        project_id, "GGB", "10岁，声音厚重，可爱", "“用心感受心中的那道光。”", "Chinese", 4, request_id
    )
    assert request_id2 == request_id
    final = db.fetch_all("SELECT * FROM candidates WHERE request_id=?", (request_id,))
    assert len(final) == 5, f"expected shortlisted + 4 sync-round candidates, got {len(final)}"
    assert {row["id"] for row in final} >= {row["id"] for row in generated}, "sync round deleted its own candidates"
    assert shortlisted_id in {row["id"] for row in final}, "shortlisted candidate lost in sync path"

    request = service.get_voice_request(request_id)
    assert request["status"] == "candidate", request["status"]

    print("PASS: fresh rounds survive; only pre-round unshortlisted candidates are discarded")
    print(f"temp data dir: {tmp_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
