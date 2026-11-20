"""Shared bilingual prompt construction and optional Responses API adapter."""

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LANGUAGES = {"en": "English", "ko": "Korean"}
METHODS = ("B0", "B1", "P1")
PROMPTS = json.loads((ROOT / "prompts.json").read_text(encoding="utf-8"))
DATASETS = {"en-ko": ROOT / "data/pilot_en_ko.jsonl", "ko-en": ROOT / "data/pilot_ko_en.jsonl"}


class TranslationError(Exception):
    """A short error safe to show without exposing keys or upstream response bodies."""


def language_pair(case):
    # The original v0.1 dataset was Korean -> English and had no language fields.
    source, target = case.get("source_language", "ko"), case.get("target_language", "en")
    if (source, target) not in {("en", "ko"), ("ko", "en")}:
        raise ValueError("지원하는 방향은 en-ko와 ko-en입니다.")
    return source, target


def build_payload(case, config):
    source, target = language_pair(case)
    payload = {"source_language": source, "target_language": target,
               "target_audience": case["target_audience"],
               "utterance_to_translate": case["source_text"]}
    if config["include_context"]:
        payload["preceding_dialogue"] = case["context"]
    if config["include_relationship"]:
        payload["relationship"] = case["relationship"]
    return payload


def build_messages(case, method):
    if method not in METHODS:
        raise ValueError("알 수 없는 비교 조건입니다.")
    source, target = language_pair(case)
    config = PROMPTS[method]
    instruction = config["system"].format(source_language_name=LANGUAGES[source],
                                          target_language_name=LANGUAGES[target])
    if target == "ko":
        instruction += (" Shared Korean output policy for all conditions: when the supplied input has no "
                        "relationship or speech-level agreement, use ordinary polite haeyo-style as an "
                        "operational default. Do not invent job titles or kinship terms. When an explicit "
                        "speech-level agreement is supplied, follow it. The default is not an inference "
                        "about the true relationship.")
    return [{"role": "system", "content": instruction},
            {"role": "user", "content": json.dumps(build_payload(case, config), ensure_ascii=False)}]


def input_fingerprint(messages):
    return hashlib.sha256(json.dumps(messages, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def read_dataset(direction):
    return [json.loads(line) for line in DATASETS[direction].read_text(encoding="utf-8").splitlines()
            if line.strip()]


def provider_config():
    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    parsed = urllib.parse.urlsplit(base)
    valid_url = (parsed.scheme == "https" and bool(parsed.hostname)
                 and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment)
    return {"key": os.environ.get("OPENAI_API_KEY", ""),
            "model": os.environ.get("PRAGMATICS_MODEL", "").strip(),
            "base_url": base, "valid_url": valid_url}


def extract_text(response):
    if not isinstance(response, dict) or response.get("status") != "completed":
        raise TranslationError("모델 응답이 완료되지 않았습니다. 이 응답은 번역 결과로 사용하지 않습니다.")
    parts = []
    for item in response.get("output", []):
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "refusal":
                raise TranslationError("모델이 번역을 거부했습니다. 번역 결과는 생성되지 않았습니다.")
            if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                parts.append(content["text"])
    text = "\n".join(parts).strip()
    if not text:
        raise TranslationError("완료 응답에서 번역문을 찾을 수 없습니다.")
    return text


def translate(case, method):
    config = provider_config()
    if not config["key"] or not config["model"]:
        raise TranslationError("서버의 OPENAI_API_KEY와 PRAGMATICS_MODEL을 설정한 뒤 다시 실행해 주세요.")
    if not config["valid_url"]:
        raise TranslationError("OPENAI_BASE_URL은 인증 정보가 없는 HTTPS API 주소여야 합니다.")
    messages = build_messages(case, method)
    body = {"model": config["model"], "instructions": messages[0]["content"],
            "input": messages[1]["content"], "store": False}
    request = urllib.request.Request(config["base_url"] + "/responses",
                                     data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json",
                                              "Authorization": "Bearer " + config["key"]},
                                     method="POST")
    started = time.monotonic()
    try:
        # Refuse redirects so an upstream redirect cannot forward credentials.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        opener = urllib.request.build_opener(NoRedirect())
        with opener.open(request, timeout=90) as handle:
            response = json.load(handle)
    except urllib.error.HTTPError as exc:
        raise TranslationError(f"모델 API가 HTTP {exc.code}를 반환했습니다. 모델 접근 권한·요청 설정을 확인해 주세요.") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise TranslationError("모델 API 연결이 실패했거나 시간 제한을 넘었습니다.") from None
    except (ValueError, UnicodeError):
        raise TranslationError("모델 API 응답을 읽을 수 없습니다.") from None
    return {"translation": extract_text(response), "method": method, "mode": "live",
            "model_requested": config["model"], "model_returned": response.get("model"),
            "response_id": response.get("id"), "usage": response.get("usage"),
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            "prompt_version": PROMPTS["version"], "input_fingerprint": input_fingerprint(messages)}
