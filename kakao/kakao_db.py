#!/usr/bin/env python
"""파이프라인 결과(prepare+build 출력 폴더)를 SQLite DB 로 적재한다.

    python kakao/kakao_db.py --out outputs/kakao --db outputs/kakao/research.db
    python kakao/kakao_db.py --build-sample kakao/samples/sample.db   # 가상 샘플로 DB 새로 만들기
    python kakao/kakao_db.py --merge P01.db P02.db --db outputs/kakao/merged.db   # 여러 사람 DB 합치기

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


def check_built(out_dir: Path, pids: List[str]) -> None:
    """build 결과가 참여자마다 있고, 마지막 prepare·검토 저장보다 나중인지 확인한다."""
    built_at = (out_dir / "summary.csv").stat().st_mtime
    built = {r["participant_id"] for r in kp.read_csv_any(out_dir / "summary.csv")}
    missing, stale = [], []
    for pid in pids:
        need = [out_dir / pid / n for n in ("sessions_split.csv", "fragments.jsonl")]
        if pid not in built or not all(p.exists() for p in need):
            missing.append(pid)
            continue
        inputs = list((out_dir / "work" / pid).glob("*.messages.jsonl")) + [out_dir / "review" / f"{pid}_sessions.csv"]
        if any(p.exists() and p.stat().st_mtime > built_at for p in inputs):
            stale.append(pid)
    msgs = []
    if missing:
        msgs.append(f"{', '.join(missing)}: 만들기(build) 결과가 없음")
    if stale:
        msgs.append(f"{', '.join(stale)}: 익명화나 검토를 다시 한 뒤 만들기를 안 함 (결과가 옛날 것)")
    if msgs:
        raise SystemExit("[오류] DB에 넣지 않음 — " + " / ".join(msgs) + "\n→ 화면 4단계의 '실행'을 먼저 누른 뒤 다시 저장하세요")


def load(out_dir: Path, db_path: Path) -> Dict[str, int]:
    out_dir = Path(out_dir)
    work = out_dir / "work"
    pids = sorted(p.name for p in work.iterdir() if p.is_dir()) if work.exists() else []
    if not pids or not (out_dir / "summary.csv").exists():
        raise SystemExit("[오류] 아직 만들기(build)를 안 함 — 화면 4단계의 '실행'을 먼저 누르세요")
    check_built(out_dir, pids)
    con = connect(Path(db_path))
    counts: Dict[str, int] = {}
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
                    con.executemany("INSERT OR IGNORE INTO chat_fingerprint VALUES (?,?)",
                                    [(ck, fp) for fp in {m["fp"] for m in ms if m.get("fp")}])
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

            refresh_shared_rooms(con)  # DB 안의 모든 참여자 기준 (이전에 넣은 참여자 포함)

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

    counts.update(_counts(con))
    bad = con.execute("PRAGMA foreign_key_check").fetchall()
    con.close()
    if bad:
        raise SystemExit(f"[오류] 외래키 불일치 {len(bad)}건")
    return counts


def _counts(con: sqlite3.Connection) -> Dict[str, int]:
    counts = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("participant", "chat", "session", "message", "fragment", "d4_stats", "shared_room",
                        "survey_item", "survey_answer", "generation", "score")}
    counts["retrieval_pool"] = con.execute("SELECT COUNT(*) FROM v_retrieval_pool").fetchone()[0]
    return counts


# 공유 채팅방 기준 (kakao_pipeline build 와 같음): 공통 메시지 5개 이상 + 작은 쪽의 30% 이상
SHARED_MIN_COMMON = 5
SHARED_MIN_OVERLAP = 0.3


def refresh_shared_rooms(con: sqlite3.Connection) -> int:
    """chat_fingerprint 로 참여자 사이의 공유 채팅방을 다시 찾는다. 지문이 없는 (옛) 방의 기록은 그대로 둔다."""
    fps: Dict[tuple, set] = {}
    for ck, pid, fp in con.execute(
            "SELECT f.chat_key, c.participant_id, f.fp FROM chat_fingerprint f JOIN chat c USING (chat_key)"):
        fps.setdefault((pid, ck), set()).add(fp)
    with_fp = {ck for _, ck in fps}
    old = con.execute("SELECT chat_key_a, chat_key_b FROM shared_room").fetchall()
    con.executemany("DELETE FROM shared_room WHERE chat_key_a = ? AND chat_key_b = ?",
                    [r for r in old if r[0] in with_fp and r[1] in with_fp])
    keys = sorted(fps)
    n = 0
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            if a[0] == b[0]:
                continue
            common = len(fps[a] & fps[b])
            denom = min(len(fps[a]), len(fps[b])) or 1
            if common >= SHARED_MIN_COMMON and common / denom >= SHARED_MIN_OVERLAP:
                con.execute("INSERT OR REPLACE INTO shared_room VALUES (?,?,?,?)",
                            (a[1], b[1], common, round(common / denom, 3)))
                n += 1
    return n


# ------------------------------------------------------------------ 여러 DB 합치기

def _tables(con: sqlite3.Connection, schema: str = "main") -> set:
    return {r[0] for r in con.execute(f"SELECT name FROM {schema}.sqlite_master WHERE type = 'table'")}


def _cols(con: sqlite3.Connection, schema: str, table: str) -> List[str]:
    return [r[1] for r in con.execute(f"PRAGMA {schema}.table_info({table})")]


def _split_cfg(text: str):
    """build_config 에서 분할 조건(holdout, split_mode, seed)만 뽑는다."""
    try:
        c = json.loads(text or "{}")
    except ValueError:
        return None
    if not c:
        return None
    return (c.get("holdout"), c.get("split_mode"), c.get("seed"))


def inspect_db(path: Path) -> Dict:
    """합칠 DB 를 미리 읽어 참여자·문항·분할 조건을 확인한다. 카카오 연구 DB 가 아니면 오류."""
    path = Path(path)
    if not path.is_file():
        raise SystemExit(f"[오류] 파일 없음: {path.name}")
    con = sqlite3.connect(str(path))
    try:
        tables = _tables(con)
    except sqlite3.DatabaseError:
        con.close()
        raise SystemExit(f"[오류] SQLite DB 가 아님: {path.name}")
    if not {"participant", "chat", "session", "fragment"} <= tables:
        con.close()
        raise SystemExit(f"[오류] 카카오 연구 DB 가 아님 (participant/chat/session/fragment 표 없음): {path.name}")
    meta = dict(con.execute("SELECT key, value FROM meta").fetchall()) if "meta" in tables else {}
    info = {
        "file": path.name,
        "participants": [r[0] for r in con.execute("SELECT participant_id FROM participant ORDER BY 1")],
        "items": dict(((r[0], (r[1], r[2])) for r in con.execute("SELECT item_id, kind, question FROM survey_item")))
        if "survey_item" in tables else {},
        "split": _split_cfg(meta.get("build_config", "")),
        "pipeline_version": meta.get("pipeline_version"),
        "sample": "source" in meta,
        "messages": con.execute("SELECT COUNT(*) FROM message").fetchone()[0] if "message" in tables else 0,
    }
    con.close()
    return info


# 부모 → 자식 순서. message·generation 은 번호(id)를 새로 매기므로 따로 처리
_COPY = ["participant", "chat", "chat_fingerprint", "session", "fragment", "fragment_embedding", "d4_stats",
         "survey_answer"]


def _copy_table(con: sqlite3.Connection, table: str, skip=(), ignore: bool = False) -> int:
    if table not in _tables(con, "src"):
        return 0
    cols = [c for c in _cols(con, "main", table) if c in set(_cols(con, "src", table)) and c not in skip]
    cl = ", ".join(cols)
    verb = "INSERT OR IGNORE" if ignore else "INSERT"
    return con.execute(f"{verb} INTO main.{table} ({cl}) SELECT {cl} FROM src.{table}").rowcount


def merge(sources: List[Path], db_path: Path, replace: bool = False) -> Dict:
    """여러 사람이 만든 DB 를 db_path 하나로 합친다.

    - 같은 참여자 ID 가 이미 있으면 멈춘다. replace=True 면 기존 것을 지우고 새 것으로 바꾼다
      (그 참여자의 설문·생성·채점 기록도 새 DB 것으로 바뀜).
    - 설문 문항(survey_item)은 ID 가 같으면 내용도 같아야 한다.
    - 생성 기록(gen_id)·메시지 번호는 새로 매긴다.
    - 다 넣은 뒤 참여자 사이의 공유 채팅방을 다시 찾는다.
    """
    db_path = Path(db_path).resolve()
    srcs = [Path(s).resolve() for s in sources]
    if not srcs:
        raise SystemExit("[오류] 합칠 DB 가 없음")
    if db_path in srcs:
        raise SystemExit("[오류] 합칠 DB 와 결과 DB 가 같은 파일")
    if len(set(srcs)) != len(srcs):
        raise SystemExit("[오류] 같은 파일이 두 번 들어 있음")

    infos = [inspect_db(s) for s in srcs]
    con = connect(db_path)
    existing = {r[0] for r in con.execute("SELECT participant_id FROM participant")}
    items = {r[0]: (r[1], r[2]) for r in con.execute("SELECT item_id, kind, question FROM survey_item")}
    meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
    history = json.loads(meta.get("merged_sources", "[]"))

    # ---- 미리 검사 (하나라도 걸리면 아무것도 넣지 않음)
    errors, warnings = [], []
    seen: Dict[str, str] = {}
    for info in infos:
        for pid in info["participants"]:
            if pid in seen:
                errors.append(f"{pid}: {seen[pid]} 와 {info['file']} 에 모두 있음 — 참여자 ID 를 다르게 받아야 함")
            seen[pid] = info["file"]
        clash = sorted(set(info["participants"]) & existing)
        if clash and not replace:
            errors.append(f"{info['file']}: {', '.join(clash)} 이(가) 이미 합본에 있음 — 바꾸려면 '같은 참여자는 새 것으로' 선택")
        for iid, v in info["items"].items():
            if iid in items and items[iid] != v:
                errors.append(f"{info['file']}: 설문 문항 {iid} 내용이 합본과 다름")
            items.setdefault(iid, v)
        if info["sample"]:
            warnings.append(f"{info['file']}: 가상 샘플 DB 입니다")
    if errors:
        con.close()
        raise SystemExit("[오류] 합치지 않음\n- " + "\n- ".join(errors))

    splits = {}
    if _split_cfg(meta.get("build_config", "")):
        splits["합본"] = _split_cfg(meta["build_config"])
    for h in history:
        if h.get("split"):
            splits[h["file"]] = tuple(h["split"])
    for info in infos:
        if info["split"]:
            splits[info["file"]] = info["split"]
    if len(set(splits.values())) > 1:
        warnings.append("80/20 분할 조건(holdout·방식·seed)이 DB마다 다름: "
                        + ", ".join(f"{k}={v}" for k, v in splits.items()))

    added, replaced = [], []
    con.commit()
    for src, info in zip(srcs, infos):
        con.execute("ATTACH DATABASE ? AS src", (str(src),))
        try:
            with con:
                clash = sorted(set(info["participants"]) & existing)
                for pid in clash:  # → 채팅방·세션·메시지·조각·통계·설문·생성·채점 연쇄 삭제
                    con.execute("DELETE FROM participant WHERE participant_id = ?", (pid,))
                replaced += clash
                _copy_table(con, "survey_item", ignore=True)  # 문항은 공통 — 같은 ID 는 위에서 내용 확인함
                for t in _COPY:
                    _copy_table(con, t)
                _copy_table(con, "message", skip=("message_id",))
                if "generation" in _tables(con, "src"):
                    gcols = [c for c in _cols(con, "main", "generation")
                             if c != "gen_id" and c in set(_cols(con, "src", "generation"))]
                    idmap = {}
                    for row in con.execute(f"SELECT gen_id, {', '.join(gcols)} FROM src.generation").fetchall():
                        cur = con.execute(f"INSERT INTO main.generation ({', '.join(gcols)}) "
                                          f"VALUES ({', '.join('?' * len(gcols))})", row[1:])
                        idmap[row[0]] = cur.lastrowid
                    for t in ("generation_fragment", "score"):
                        if t not in _tables(con, "src"):
                            continue
                        cols = [c for c in _cols(con, "main", t) if c in set(_cols(con, "src", t))]
                        gi = cols.index("gen_id")
                        rows = [list(r) for r in con.execute(f"SELECT {', '.join(cols)} FROM src.{t}")]
                        for r in rows:
                            r[gi] = idmap[r[gi]]
                        con.executemany(f"INSERT INTO main.{t} ({', '.join(cols)}) "
                                        f"VALUES ({', '.join('?' * len(cols))})", rows)
                if "shared_room" in _tables(con, "src"):  # 지문 없는 옛 DB 대비 — 양쪽 방이 다 있을 때만
                    con.execute("INSERT OR IGNORE INTO main.shared_room SELECT * FROM src.shared_room "
                                "WHERE chat_key_a IN (SELECT chat_key FROM main.chat) "
                                "AND chat_key_b IN (SELECT chat_key FROM main.chat)")
        except sqlite3.IntegrityError as e:
            con.execute("DETACH DATABASE src")
            con.close()
            raise SystemExit(f"[오류] {info['file']} 합치는 중 중단 (앞의 DB 는 들어감): {e}")
        con.execute("DETACH DATABASE src")
        existing |= set(info["participants"])
        added += [p for p in info["participants"] if p not in replaced]
        history.append({"file": info["file"], "participants": info["participants"],
                        "split": list(info["split"]) if info["split"] else None,
                        "pipeline_version": info["pipeline_version"],
                        "merged_at": datetime.now().isoformat(timespec="seconds")})

    with con:
        n_shared = refresh_shared_rooms(con)
        con.execute("INSERT OR REPLACE INTO meta VALUES ('merged_sources', ?)",
                    (json.dumps(history, ensure_ascii=False),))
        con.execute("INSERT OR REPLACE INTO meta VALUES ('pipeline_version', ?)", (kp.VERSION,))
    counts = _counts(con)
    summary = [dict(zip(("participant_id", "chats", "sessions_included", "retrieval_pool"), r))
               for r in con.execute("SELECT * FROM v_participant_summary ORDER BY 1")]
    bad = con.execute("PRAGMA foreign_key_check").fetchall()
    con.close()
    if bad:
        raise SystemExit(f"[오류] 외래키 불일치 {len(bad)}건")
    return {"added": added, "replaced": replaced, "warnings": warnings, "shared_rooms": n_shared,
            "counts": counts, "participants": summary}


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
    ap.add_argument("--db", help="결과 DB (기본: outputs/kakao/research.db, --merge 일 때 outputs/kakao/merged.db)")
    ap.add_argument("--build-sample", metavar="DB", help="가상 샘플로 DB 를 새로 만듦")
    ap.add_argument("--merge", nargs="+", metavar="DB", help="여러 사람의 DB 를 --db 하나로 합침")
    ap.add_argument("--replace", action="store_true", help="--merge 때 이미 있는 참여자는 새 DB 것으로 바꿈")
    args = ap.parse_args(argv)
    if args.merge:
        db = args.db or "outputs/kakao/merged.db"
        r = merge([Path(p) for p in args.merge], Path(db), replace=args.replace)
        for w in r["warnings"]:
            print("경고: " + w)
        print(f"추가: {', '.join(r['added']) or '없음'}   바꿈: {', '.join(r['replaced']) or '없음'}   "
              f"공유 채팅방: {r['shared_rooms']}")
        for p in r["participants"]:
            print(f"  {p['participant_id']}: 채팅방 {p['chats']}, 사용 세션 {p['sessions_included']}, "
                  f"검색 후보 {p['retrieval_pool']}")
        print(json.dumps(r["counts"], ensure_ascii=False))
        print(f"DB: {db}")
        return 0
    db = args.db or "outputs/kakao/research.db"
    counts = build_sample(Path(args.build_sample)) if args.build_sample else load(Path(args.out), Path(db))
    print(json.dumps(counts, ensure_ascii=False))
    print(f"DB: {args.build_sample or db}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
