"""Record a live agent run and replay it offline (demo insurance: no network, no LLM latency).

A recording holds the question, every agent step (thought, tool, args, result, timing), the final
papers in each bucket, and the metrics + source-evidence log. Replay re-renders the same trace and
results, clearly labelled as a recording; the approval/export action still runs live.
"""
from __future__ import annotations

import dataclasses
import json
import os
import time

from models import Paper

REPLAY_DIR = os.getenv("REPLAY_DIR", "replays")
_FIELDS = {f.name for f in dataclasses.fields(Paper)}


def paper_from_dict(d: dict) -> Paper:
    return Paper(**{k: v for k, v in d.items() if k in _FIELDS})


def save_run(meta: dict, steps: list[dict], buckets: dict[str, list[Paper]], stats: dict, evidence: list[dict],
             name: str | None = None) -> str:
    os.makedirs(REPLAY_DIR, exist_ok=True)
    name = name or time.strftime("run_%Y%m%d_%H%M%S")
    path = os.path.join(REPLAY_DIR, f"{name}.json")
    rec = {"meta": {**meta, "recorded_at": time.strftime("%Y-%m-%d %H:%M:%S")}, "steps": steps,
           "buckets": {k: [p.to_dict() for p in v] for k, v in buckets.items()},
           "stats": stats, "evidence": evidence}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, indent=1, default=str)
    return path


def list_runs() -> list[str]:
    if not os.path.isdir(REPLAY_DIR):
        return []
    return sorted((f for f in os.listdir(REPLAY_DIR) if f.endswith(".json")), reverse=True)


def load_run(fname: str) -> dict:
    with open(os.path.join(REPLAY_DIR, fname), encoding="utf-8") as fh:
        rec = json.load(fh)
    rec["buckets"] = {k: [paper_from_dict(d) for d in v] for k, v in rec["buckets"].items()}
    return rec
