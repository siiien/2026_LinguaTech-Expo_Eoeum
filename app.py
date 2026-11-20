#!/usr/bin/env python3
"""Local bilingual translation research prototype. No third-party dependencies."""

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from pragmatics import (ROOT, DATASETS, METHODS, PROMPTS, TranslationError,
                        language_pair, provider_config, read_dataset, translate)

CASES = {case["id"]: case for direction in DATASETS for case in read_dataset(direction)}
BUSY = threading.BoundedSemaphore(1)


def public_case(case):
    source, target = language_pair(case)
    return {"id": case["id"], "group_id": case["group_id"],
            "source_language": source, "target_language": target,
            "source_text": case["source_text"], "context": case["context"],
            "relationship": case["relationship"], "target_audience": case["target_audience"]}


def validate_case(raw):
    if not isinstance(raw, dict):
        raise ValueError("번역 입력은 객체여야 합니다.")
    source, target = language_pair(raw)
    case = {"source_language": source, "target_language": target}
    for key, limit in (("source_text", 6000), ("relationship", 2000), ("target_audience", 500)):
        value = raw.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise ValueError(f"{key}: 비어 있거나 입력 길이를 초과했습니다.")
        case[key] = value.strip()
    context = raw.get("context")
    if not isinstance(context, list) or len(context) > 12:
        raise ValueError("앞선 대화는 최대 12개 발화로 입력해 주세요.")
    case["context"] = []
    for turn in context:
        if not isinstance(turn, dict) or turn.get("speaker") not in {"speaker", "listener"}:
            raise ValueError("대화의 화자는 speaker 또는 listener여야 합니다.")
        text = turn.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise ValueError("각 대화 발화는 1~2000자로 입력해 주세요.")
        case["context"].append({"speaker": turn["speaker"], "text": text.strip()})
    return case


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # Input text, credentials, and API results are never logged by this app.
        pass

    def valid_host(self):
        port = self.server.server_address[1]
        return self.headers.get("Host") in {f"127.0.0.1:{port}", f"localhost:{port}"}

    def send_bytes(self, status, content, mime):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        try:
            self.wfile.write(content)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def send_json(self, status, data):
        self.send_bytes(status, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def do_GET(self):
        if not self.valid_host():
            return self.send_json(403, {"error": "로컬 주소로 접속해 주세요."})
        path = urlsplit(self.path).path
        assets = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"),
                  "/styles.css": ("styles.css", "text/css")}
        if path in assets:
            name, mime = assets[path]
            return self.send_bytes(200, (ROOT / "web" / name).read_bytes(), mime + "; charset=utf-8")
        if path == "/api/status":
            config = provider_config()
            ready = bool(config["key"] and config["model"] and config["valid_url"])
            return self.send_json(200, {"configured": ready, "model": config["model"],
                                        "connection_verified": False, "prompt_version": PROMPTS["version"],
                                        "default_direction": "en-ko", "case_count": len(CASES)})
        if path == "/api/cases":
            return self.send_json(200, {"cases": [public_case(c) for c in CASES.values()]})
        return self.send_json(404, {"error": "해당 경로가 없습니다."})

    def do_POST(self):
        if not self.valid_host():
            return self.send_json(403, {"error": "로컬 주소로 접속해 주세요."})
        origin = self.headers.get("Origin")
        if origin and origin != "http://" + self.headers.get("Host", ""):
            return self.send_json(403, {"error": "다른 웹사이트에서 보낸 요청은 허용하지 않습니다."})
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            return self.send_json(415, {"error": "JSON 요청이 필요합니다."})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 65536:
                return self.send_json(413, {"error": "요청 크기는 1~65536바이트여야 합니다."})
            raw = json.loads(self.rfile.read(length))
            if not isinstance(raw, dict):
                raise ValueError("JSON 객체가 필요합니다.")
            path = urlsplit(self.path).path
            if path == "/api/preview":
                case_id = raw.get("case_id")
                if not isinstance(case_id, str) or case_id not in CASES:
                    raise ValueError("저장된 사례를 먼저 선택해 주세요.")
                case = CASES[case_id]
                if validate_case(raw.get("case")) != validate_case(public_case(case)):
                    raise ValueError("입력을 수정했습니다. 저장된 예시를 다시 불러오거나 모델로 번역해 주세요.")
                return self.send_json(200, {"mode": "example", "translation": case["annotation"]["candidate_translations"][0],
                                            "note": case["annotation"]["note"], "case_id": case_id,
                                            "provenance": "ai_authored_synthetic_draft",
                                            "api_calls": 0})
            if path != "/api/translate":
                return self.send_json(404, {"error": "해당 경로가 없습니다."})
            case = validate_case(raw.get("case"))
            methods = raw.get("methods", ["P1"])
            if (not isinstance(methods, list) or not methods or len(methods) > 3
                    or any(not isinstance(m, str) or m not in METHODS for m in methods)
                    or len(set(methods)) != len(methods)):
                raise ValueError("비교 조건은 B0, B1, P1 중 중복 없이 선택해 주세요.")
            if not BUSY.acquire(blocking=False):
                return self.send_json(429, {"error": "현재 번역이 진행 중입니다. 완료 후 다시 시도해 주세요."})
            try:
                results = []
                for method in methods:
                    try:
                        results.append(translate(case, method))
                    except TranslationError as exc:
                        # Preserve completed results; never silently retry a paid request.
                        return self.send_json(502, {"error": str(exc), "failed_method": method,
                                                    "results": results, "mode": "live"})
                return self.send_json(200, {"mode": "live", "results": results})
            finally:
                BUSY.release()
        except (ValueError, TypeError, KeyError, UnicodeError) as exc:
            return self.send_json(400, {"error": str(exc)})
        except Exception:
            return self.send_json(500, {"error": "처리 중 오류가 발생했습니다. 입력과 서버 설정을 확인해 주세요."})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Prototype: http://127.0.0.1:{server.server_address[1]}", flush=True)
    print("Stored examples are drafts. Live translation requires an explicitly configured model.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
