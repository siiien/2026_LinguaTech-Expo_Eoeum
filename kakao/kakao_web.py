#!/usr/bin/env python
"""카카오톡 추출 파이프라인 로컬 화면.

    python kakao/kakao_web.py      → http://127.0.0.1:8765

이 컴퓨터(127.0.0.1)에서만 열리고, 외부로 아무것도 보내지 않는다.
올린 파일은 data/kakao/raw/, 결과는 outputs/kakao_web/, DB는 outputs/kakao/research.db 에 저장된다.
(세 곳 모두 .gitignore 대상 — 실제 대화는 저장소에 올라가지 않는다)
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import io
import json
import re
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import kakao_pipeline as kp  # noqa: E402
from collections import Counter  # noqa: E402
from kakao_parser import parse_file  # noqa: E402

REPO = ROOT.parent
DATA = REPO / "data" / "kakao"
RAW = DATA / "raw"
MANIFEST = DATA / "manifest.csv"
OUT = REPO / "outputs" / "kakao_web"
DB_PATH = REPO / "outputs" / "kakao" / "research.db"
FIELDS = ["participant_id", "chat_id", "file", "relation", "self_name", "partner_name", "self_aliases",
          "partner_aliases", "extra_names", "places", "date_from", "date_to"]


def run_cli(argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        try:
            rc = kp.main(argv)
        except SystemExit as e:
            rc = e.code if isinstance(e.code, int) else 1
        except Exception:
            traceback.print_exc()
            rc = 1
    return rc, buf.getvalue()


def read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8-sig") if p.exists() else ""


def state():
    rows = kp.read_csv_any(MANIFEST) if MANIFEST.exists() else []
    raw_files = sorted(p.name for p in RAW.glob("*")) if RAW.exists() else []
    pids = sorted(p.name for p in (OUT / "work").iterdir() if p.is_dir()) if (OUT / "work").exists() else []
    return {
        "manifest": rows, "raw_files": raw_files, "fields": FIELDS,
        "prepared": (OUT / "prepare_report.md").exists(), "built": (OUT / "summary.csv").exists(),
        "participants": pids,
    }


def inspect_file(rel: str):
    """올린 파일을 바로 읽어 형식·상대·화자·기간을 보여 준다 (원본은 화면에만, 저장 안 함)."""
    p = (DATA / rel).resolve()
    if REPO not in p.parents or not p.exists():
        return {"file": rel, "error": "파일 없음"}
    chat = parse_file(p)
    spk = Counter(m.speaker for m in chat.messages)
    dts = [m.dt for m in chat.messages]
    return {
        "file": rel, "fmt": chat.fmt, "title": chat.title, "n_messages": len(chat.messages),
        "speakers": [{"name": n, "count": c} for n, c in spk.most_common()],
        "first": f"{min(dts):%Y-%m-%d}" if dts else "", "last": f"{max(dts):%Y-%m-%d}" if dts else "",
        "title_matches": bool(chat.title and chat.title in spk), "warnings": chat.warnings,
    }


def review_data():
    res = {}
    work = OUT / "work"
    if not work.exists():
        return res
    for pdir in sorted(p for p in work.iterdir() if p.is_dir()):
        res[pdir.name] = kp.read_csv_any(OUT / "review" / f"{pdir.name}_sessions.csv")
    return res


_SESSION_CACHE = {}


def session_messages(pid: str, sid: str):
    pdir = OUT / "work" / Path(pid).name
    key = (str(pdir), tuple((f.name, f.stat().st_mtime) for f in sorted(pdir.glob("*.messages.jsonl"))))
    if _SESSION_CACHE.get("key") != key:
        by_sid = {}
        for f in sorted(pdir.glob("*.messages.jsonl")):
            for m in kp.read_jsonl(f):
                by_sid.setdefault(m["session_id"], []).append(
                    {"t": m["ts"][11:], "role": m["role"], "text": m["text"], "flags": m["flags"]})
        _SESSION_CACHE.clear()
        _SESSION_CACHE.update(key=key, data=by_sid)
    return _SESSION_CACHE["data"].get(sid, [])


def results():
    if not (OUT / "summary.csv").exists():
        return {}
    out = {"summary": kp.read_csv_any(OUT / "summary.csv"),
           "shared": kp.read_csv_any(OUT / "shared_rooms.csv"), "participants": {}}
    for row in out["summary"]:
        pid = row["participant_id"]
        pdir = OUT / pid
        blocks = {"전체": read_text(pdir / "block3.txt")}
        for f in sorted(pdir.glob("block3_*.txt")):
            blocks[f.stem.replace("block3_", "")] = read_text(f)
        d4 = {}
        for sp in ("train", "holdout"):
            p = pdir / f"d4_{sp}.json"
            if p.exists():
                d4[sp] = json.loads(p.read_text(encoding="utf-8"))
        frags = [{k: f[k] for k in ("fragment_id", "relation", "split", "retrieval_eligible", "exclude_reason",
                                    "flags", "block4_text")}
                 for f in kp.read_jsonl(pdir / "fragments.jsonl")] if (pdir / "fragments.jsonl").exists() else []
        out["participants"][pid] = {"block3": blocks, "d4": d4, "fragments": frags}
    return out


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # 조용히
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n).decode("utf-8") or "{}")

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path in ("/", "/index.html"):
                return self._send(200, (ROOT / "web" / "index.html").read_bytes(), "text/html; charset=utf-8")
            if path == "/api/state":
                return self._send(200, state())
            if path == "/api/report":
                return self._send(200, {"text": read_text(OUT / "prepare_report.md")})
            if path == "/api/review":
                return self._send(200, review_data())
            if path == "/api/results":
                return self._send(200, results())
            q = parse_qs(urlparse(self.path).query)
            if path == "/api/inspect":
                return self._send(200, inspect_file(q.get("file", [""])[0]))
            if path == "/api/session":
                return self._send(200, session_messages(q.get("pid", [""])[0], q.get("sid", [""])[0]))
            self._send(404, {"error": "not found"})
        except Exception as e:
            self._send(500, {"error": str(e)})

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            body = self._json()
            if path == "/api/upload":
                name = re.sub(r"[\\/:*?\"<>|]+", "_", Path(body["name"]).name)
                RAW.mkdir(parents=True, exist_ok=True)
                (RAW / name).write_bytes(base64.b64decode(body["b64"]))
                return self._send(200, inspect_file(f"raw/{name}"))
            if path == "/api/manifest":
                rows = [{k: (r.get(k) or "").strip() for k in FIELDS} for r in body["rows"]]
                kp.write_csv(MANIFEST, rows, FIELDS)
                return self._send(200, {"ok": True})
            if path == "/api/load-sample":
                rows = kp.read_csv_any(ROOT / "samples" / "manifest.csv")
                for r in rows:
                    r["file"] = "../../kakao/samples/" + r["file"]
                kp.write_csv(MANIFEST, rows, FIELDS)
                return self._send(200, {"ok": True})
            if path == "/api/prepare":
                argv = ["prepare", "--manifest", str(MANIFEST), "--out", str(OUT),
                        "--gap-min", str(int(body.get("gap_min", 60)))]
                if body.get("reset"):
                    argv.append("--reset-review")
                rc, log = run_cli(argv)
                return self._send(200, {"rc": rc, "log": log})
            if path == "/api/review":
                pid = Path(body["pid"]).name
                p = OUT / "review" / f"{pid}_sessions.csv"
                rows = kp.read_csv_any(p)
                inc = body["include"]
                for r in rows:
                    if r["session_id"] in inc:
                        r["include"] = "1" if inc[r["session_id"]] else "0"
                kp.write_csv(p, rows, list(rows[0].keys()) if rows else [])
                return self._send(200, {"ok": True})
            if path == "/api/build":
                argv = ["build", "--out", str(OUT), "--holdout", str(float(body.get("holdout", 0.2))),
                        "--split-mode", body.get("split_mode", "random"), "--seed", str(int(body.get("seed", 42))),
                        "--pool-target", str(int(body.get("pool_target", 30)))]
                if body.get("allow_no_context"):
                    argv.append("--allow-no-context")
                rc, log = run_cli(argv)
                return self._send(200, {"rc": rc, "log": log})
            if path == "/api/db":
                import kakao_db
                try:
                    counts = kakao_db.load(OUT, DB_PATH)
                except SystemExit as e:
                    return self._send(200, {"rc": 1, "log": str(e)})
                return self._send(200, {"rc": 0, "counts": counts, "db": str(DB_PATH.relative_to(REPO))})
            self._send(404, {"error": "not found"})
        except Exception as e:
            self._send(500, {"error": f"{type(e).__name__}: {e}"})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"카카오톡 추출 화면: http://127.0.0.1:{args.port}  (종료: Ctrl+C)", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
