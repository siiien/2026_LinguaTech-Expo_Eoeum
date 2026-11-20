#!/usr/bin/env python
"""파이프라인 결과(prepare+build 출력 폴더)를 SQLite DB 로 적재한다.

    python kakao/kakao_db.py --out outputs/kakao --db outputs/kakao/research.db
    python kakao/kakao_db.py --build-sample kakao/samples/sample.db   # 가상 샘플로 DB 새로 만들기

같은 참여자를 다시 적재하면 그 참여자의 채팅방·세션·메시지·조각·D4 통계를 지우고 새로 넣는다.
설문·생성·채점 테이블은 건드리지 않는다. (생성 기록이 참조하는 조각이 있으면 멈춘다)
스키마: kakao/schema.sql
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import kakao_pipeline as kp  # noqa: E402

SCHEMA = ROOT / "schema.sql"


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMA.read_text(encoding="utf-8"))
    return con


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig") if path.exists() else ""


def load(out_dir: Path, db_path: Path) -> Dict[str, int]:
    out_dir = Path(out_dir)
    work = out_dir / "work"
    if not (out_dir / "summary.csv").exists():
        raise SystemExit(f"[오류] {out_dir} 에 build 결과가 없음 — prepare, build 를 먼저 실행")
    con = connect(Path(db_path))
    counts: Dict[str, int] = {}
    pids = sorted(p.name for p in work.iterdir() if p.is_dir())
    try:
        with con:
            for pid in pids:
                con.execute("INSERT OR IGNORE INTO participant(participant_id) VALUES (?)", (pid,))
                con.execute("DELETE FROM d4_stats WHERE participant_id = ?", (pid,))
                con.execute("DELETE FROM chat WHERE participant_id = ?", (pid,))  # → 세션·메시지·조각 연쇄 삭제

                msgs: List[Dict] = []
                for f in sorted((work / pid).glob("*.messages.jsonl")):
                    msgs += kp.read_jsonl(f)
                review = {r["session_id"]: r for r in kp.read_csv_any(out_dir / "review" / f"{pid}_sessions.csv")}
                split = {r["session_id"]: r["split"] for r in kp.read_csv_any(out_dir / pid / "sessions_split.csv")}

                by_chat: Dict[str, List[Dict]] = {}
                for m in msgs:
                    by_chat.setdefault(m["chat_id"], []).append(m)
                for chat_id, ms in by_chat.items():
                    ck = f"{pid}-{chat_id}"
                    con.execute("INSERT INTO chat VALUES (?,?,?,?,?,?,?)",
                                (ck, pid, chat_id, ms[0]["relation"], ms[0]["ts"], ms[-1]["ts"], len(ms)))
                    by_sid: Dict[str, List[Dict]] = {}
                    for m in ms:
                        by_sid.setdefault(m["session_id"], []).append(m)
                    for sid, sm in by_sid.items():
                        rv = review.get(sid, {})
                        inc = 0 if rv.get("include") == "0" else 1
                        con.execute("INSERT INTO session VALUES (?,?,?,?,?,?,?,?,?)", (
                            sid, ck, sm[0]["ts"], sm[-1]["ts"], len(sm), sum(m["role"] == "self" for m in sm),
                            rv.get("flags") or ";".join(sorted({f for m in sm for f in m["flags"]})),
                            inc, split.get(sid) if inc else None))
                        if inc:
                            con.executemany(
                                "INSERT INTO message(session_id, seq, ts, role, kind, text, flags) VALUES (?,?,?,?,?,?,?)",
                                [(sid, i, m["ts"], m["role"], m["kind"], m["text"], ";".join(m["flags"]))
                                 for i, m in enumerate(sm)])

                frags = kp.read_jsonl(out_dir / pid / "fragments.jsonl")
                con.executemany("INSERT INTO fragment VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [(
                    f["fragment_id"], f["session_id"], f["relation"], f["split"], f["start"],
                    f["n_partner_msgs"], f["n_self_msgs"], f["self_chars"],
                    len(f["partner_text"]) + len(f["self_text"]),
                    int(f["retrieval_eligible"]), f["exclude_reason"], f["flags"],
                    f["partner_text"], f["self_text"], f["block4_text"], f["embed_text"]) for f in frags])

                for sp in ("train", "holdout"):
                    p = out_dir / pid / f"d4_{sp}.json"
                    if not p.exists():
                        continue
                    d4 = json.loads(p.read_text(encoding="utf-8"))
                    items = [("전체", d4["overall"])] + list(d4["by_relation"].items())
                    for rel, st in items:
                        b3 = None
                        if sp == "train":
                            b3 = _read(out_dir / pid / ("block3.txt" if rel == "전체" else f"block3_{rel}.txt")).strip() or None
                        con.execute("INSERT INTO d4_stats VALUES (?,?,?,?,?,?,?,?)", (
                            pid, sp, rel, json.dumps(st, ensure_ascii=False), b3, st["line_len"]["mean"],
                            st["honorific"]["polite_ratio"], ";".join(st["endings"]["frequent"])))

            con.execute("DELETE FROM shared_room")
            for r in kp.read_csv_any(out_dir / "shared_rooms.csv"):
                a, b = f"{r['participant_a']}-{r['chat_a']}", f"{r['participant_b']}-{r['chat_b']}"
                con.execute("INSERT OR REPLACE INTO shared_room VALUES (?,?,?,?)",
                            (a, b, int(r["common_msgs"]), float(r["overlap"])))

            cfg = _read(out_dir / "build_config.json")
            pcfg = json.loads(_read(work / "prepare_config.json") or "{}")
            for key in ("manifest", "keywords"):  # 로컬 경로는 남기지 않음 (파일 이름만)
                if pcfg.get(key):
                    pcfg[key] = Path(pcfg[key]).name
            for k, v in (("loaded_at", datetime.now().isoformat(timespec="seconds")),
                         ("pipeline_version", kp.VERSION), ("build_config", cfg or "{}"),
                         ("prepare_config", json.dumps(pcfg, ensure_ascii=False))):
                con.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (k, v))
    except sqlite3.IntegrityError as e:
        con.close()
        raise SystemExit(f"[오류] 적재 중단 — 생성 기록(generation_fragment)이 참조하는 조각이 있어 다시 넣을 수 없음: {e}")

    for t in ("participant", "chat", "session", "message", "fragment", "d4_stats", "shared_room",
              "survey_item", "survey_answer", "generation", "score"):
        counts[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    counts["retrieval_pool"] = con.execute("SELECT COUNT(*) FROM v_retrieval_pool").fetchone()[0]
    bad = con.execute("PRAGMA foreign_key_check").fetchall()
    con.close()
    if bad:
        raise SystemExit(f"[오류] 외래키 불일치 {len(bad)}건")
    return counts


def build_sample(db_path: Path) -> Dict[str, int]:
    """가상 샘플(kakao/samples)로 prepare → build → DB 를 처음부터 만든다."""
    tmp = Path(tempfile.mkdtemp())
    try:
        out = tmp / "out"
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            if kp.main(["prepare", "--manifest", str(ROOT / "samples" / "manifest.csv"), "--out", str(out)]) != 0:
                raise SystemExit("[오류] 샘플 prepare 실패")
            kp.main(["build", "--out", str(out)])
        db_path = Path(db_path)
        if db_path.exists():
            db_path.unlink()
        counts = load(out, db_path)
        con = sqlite3.connect(str(db_path))
        with con:
            con.execute("INSERT OR REPLACE INTO meta VALUES ('source', '가상 샘플 (kakao/samples) — 실제 참여자 데이터 아님')")
        con.execute("VACUUM")
        con.close()
        return counts
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="outputs/kakao", help="prepare/build 결과 폴더")
    ap.add_argument("--db", default="outputs/kakao/research.db")
    ap.add_argument("--build-sample", metavar="DB", help="가상 샘플로 DB 를 새로 만듦")
    args = ap.parse_args(argv)
    counts = build_sample(Path(args.build_sample)) if args.build_sample else load(Path(args.out), Path(args.db))
    print(json.dumps(counts, ensure_ascii=False))
    print(f"DB: {args.build_sample or args.db}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
