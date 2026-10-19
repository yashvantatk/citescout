"""SQLite audit store: every run, every piece of source evidence, every per-paper decision."""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid

DB_PATH = os.getenv("CITESCOUT_DB", "citescout.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY, created_at REAL, question TEXT, keywords TEXT,
  year_from INT, year_to INT, status TEXT, idempotency_key TEXT, summary TEXT
);
CREATE TABLE IF NOT EXISTS source_evidence (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, ts REAL, tool TEXT,
  url TEXT, http_status INT, from_cache INT, note TEXT, payload TEXT
);
CREATE TABLE IF NOT EXISTS paper_decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, paper_key TEXT, title TEXT,
  doi TEXT, decision TEXT, reason TEXT, verification TEXT, relevance TEXT, record TEXT
);
CREATE TABLE IF NOT EXISTS test_case_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, tc TEXT, passed INT, expected TEXT,
  actual TEXT, evidence TEXT
);
"""


def conn(path: str = DB_PATH) -> sqlite3.Connection:
    c = sqlite3.connect(path, check_same_thread=False)
    c.executescript(SCHEMA)
    return c


def new_run(c, question, keywords, year_from, year_to, idem_key=None) -> str:
    rid = uuid.uuid4().hex[:12]
    c.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?)",
              (rid, time.time(), question, json.dumps(keywords), year_from, year_to, "running", idem_key, ""))
    c.commit()
    return rid


def log_evidence(c, run_id, tool, url, http_status=None, from_cache=False, note="", payload=None):
    c.execute("INSERT INTO source_evidence (run_id,ts,tool,url,http_status,from_cache,note,payload) VALUES (?,?,?,?,?,?,?,?)",
              (run_id, time.time(), tool, url, http_status, int(from_cache), note, json.dumps(payload)[:20000] if payload else None))
    c.commit()


def log_decision(c, run_id, paper: dict, decision: str, reason: str):
    c.execute("INSERT INTO paper_decisions (run_id,paper_key,title,doi,decision,reason,verification,relevance,record) VALUES (?,?,?,?,?,?,?,?,?)",
              (run_id, paper.get("best_doi") or paper.get("title"), paper.get("title"), paper.get("best_doi"),
               decision, reason, json.dumps(paper.get("verification")), json.dumps(paper.get("relevance")), json.dumps(paper)))
    c.commit()


def log_tc(c, tc, passed, expected, actual, evidence):
    c.execute("INSERT INTO test_case_runs (ts,tc,passed,expected,actual,evidence) VALUES (?,?,?,?,?,?)",
              (time.time(), tc, int(passed), expected, actual, json.dumps(evidence, default=str)))
    c.commit()


def finish_run(c, run_id, status, summary):
    c.execute("UPDATE runs SET status=?, summary=? WHERE run_id=?", (status, json.dumps(summary), run_id))
    c.commit()
