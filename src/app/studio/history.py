"""逐字稿修改紀錄：從 WhisperX 輸出的那一版走到目前版本。API（…/history）與匯出（*.history.json）共用。"""
from __future__ import annotations

from .store import StudioError


def build_history(store, current, project_id=None):
    chain = []
    node = current
    seen = set()
    while node and node.get("change") and node.get("parent_id") and node["id"] not in seen:
        seen.add(node["id"])
        chain.append(node)
        try:
            node = store.get(node["parent_id"], "transcript")
        except StudioError:
            node = None
    base = node or current
    steps = []
    for item in reversed(chain):
        change = item["change"]
        steps.append({"revision": item["id"], "parent_revision": item.get("parent_id"), "origin": change.get("origin"),
                      "job_id": change.get("job_id"), "note": change.get("note", ""), "at": change.get("at"), "edits": change.get("edits", [])})
    return {"project_id": project_id, "current": current["id"], "source_id": current.get("source_id"),
            "base": {"revision": base["id"], "settings": base.get("settings", {}), "cue_count": len(base.get("cues", [])),
                     "asset_ids": base.get("asset_ids", []), "engine_output": "whisperx" if not base.get("change") else "unknown"},
            "steps": steps, "total_edits": sum(len(s["edits"]) for s in steps), "cue_count": len(current.get("cues", []))}


def transcript_record(transcript):
    """給人與其他工具讀的逐字稿 JSON：每句的時間、文字、語言與旗標（不含內部欄位）。"""
    cues = []
    for cue in transcript.get("cues", []):
        cues.append({"id": cue["id"], "start_us": cue.get("start_us"), "end_us": cue.get("end_us"),
                     "text": cue.get("accepted_text") or cue.get("raw_text") or cue.get("text", ""),
                     "raw_text": cue.get("raw_text") or cue.get("text", ""), "lang": cue.get("lang"),
                     "edited": bool(cue.get("edited")), "review_flags": cue.get("review_flags") or []})
    return {"revision": transcript["id"], "source_id": transcript.get("source_id"), "settings": transcript.get("settings", {}),
            "cue_count": len(cues), "cues": cues}
