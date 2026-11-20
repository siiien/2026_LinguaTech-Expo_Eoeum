#!/usr/bin/env python3
"""Validate draft cases and build model requests locally; never calls an API."""

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pragmatics import DATASETS, PROMPTS, build_messages, build_payload, language_pair
ACTS = {"refusal", "request", "information", "question", "acceptance",
        "deferral", "suggestion", "appreciation", "other"}
STATES = {"clear", "multiple_plausible", "insufficient_context"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def load_cases(path):
    cases = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            case = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_no}: invalid JSON: {exc}") from exc
        require(isinstance(case, dict), f"line {line_no}: expected object")
        language_pair(case)
        for key in ("id", "group_id", "contrast_axis", "source_text", "relationship",
                    "context", "target_audience", "annotation", "provenance", "split"):
            require(key in case, f"line {line_no}: missing {key}")
        for key in ("id", "group_id", "source_text", "relationship", "target_audience"):
            require(isinstance(case[key], str) and case[key].strip(),
                    f"line {line_no}: empty or invalid {key}")
        require(case["contrast_axis"] in {"content_context", "relationship", "sufficiency"},
                f"{case['id']}: invalid contrast axis")
        require(case["split"] == "pilot_dev", f"{case['id']}: pilot must stay in development")
        require(case["provenance"] == "ai_authored_synthetic", f"{case['id']}: unexpected provenance")
        require(isinstance(case["context"], list), f"{case['id']}: invalid context")
        for turn in case["context"]:
            require(isinstance(turn, dict) and set(turn) == {"speaker", "text"},
                    f"{case['id']}: invalid turn fields")
            require(turn["speaker"] in {"speaker", "listener"} and isinstance(turn["text"], str)
                    and turn["text"].strip(), f"{case['id']}: invalid turn")
        ann = case["annotation"]
        require(isinstance(ann, dict), f"{case['id']}: invalid annotation")
        require(ann.get("status") == "draft_ai", f"{case['id']}: expected unreviewed draft")
        acts = ann.get("possible_acts")
        require(isinstance(acts, list) and acts and all(act in ACTS for act in acts),
                f"{case['id']}: invalid acts")
        require(ann.get("ambiguity") in STATES, f"{case['id']}: invalid ambiguity")
        for key in ("candidate_translations", "invariants", "risks"):
            require(isinstance(ann.get(key), list) and ann[key]
                    and all(isinstance(v, str) and v.strip() for v in ann[key]),
                    f"{case['id']}: invalid {key}")
        require(isinstance(ann.get("note"), str) and ann["note"].strip(),
                f"{case['id']}: missing interpretation note")
        cases.append(case)
    require(cases, "No cases found")
    require(len({c["id"] for c in cases}) == len(cases), "Duplicate case IDs")
    groups = defaultdict(list)
    for case in cases:
        groups[case["group_id"]].append(case)
    for group, items in groups.items():
        require(len(items) >= 2, f"{group}: need multiple contexts")
        require(len({c["source_text"] for c in items}) == 1, f"{group}: source must be fixed")
        require(len({language_pair(c) for c in items}) == 1, f"{group}: language direction must be fixed")
        require(len({c["target_audience"] for c in items}) == 1,
                f"{group}: audience must be fixed")
        controlled = [c for c in items if c["contrast_axis"] != "sufficiency"]
        axes = {c["contrast_axis"] for c in controlled}
        require(len(axes) <= 1, f"{group}: inconsistent controlled contrast")
        if axes == {"content_context"}:
            require(len({c["relationship"] for c in controlled}) == 1,
                    f"{group}: content contrast changes relationship")
        if axes == {"relationship"}:
            require(len({json.dumps(c["context"], ensure_ascii=False) for c in controlled}) == 1,
                    f"{group}: relationship contrast changes dialogue")
    return cases, groups


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, help="Override the dataset file")
    parser.add_argument("--direction", choices=("en-ko", "ko-en"), default="en-ko")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "outputs")
    args = parser.parse_args()
    cases, groups = load_cases(args.data or DATASETS[args.direction])
    prompts = PROMPTS
    requests = []
    for method in ("B0", "B1", "P1"):
        config = prompts[method]
        require(isinstance(config["system"], str) and config["system"].strip(),
                f"{method}: invalid prompt")
        require(all(isinstance(config[k], bool) for k in ("include_context", "include_relationship")),
                f"{method}: invalid input options")
        for case in cases:
            requests.append({
                "request_id": f"{case['id']}__{method}",
                "case_id": case["id"], "method": method,
                "prompt_version": prompts["version"],
                "messages": build_messages(case, method),
            })
    args.out_dir.mkdir(parents=True, exist_ok=True)
    request_path = args.out_dir / "requests.jsonl"
    request_path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in requests), encoding="utf-8")
    fields = ["case_id", "source_language", "target_language", "source_text", "relationship", "context", "rater_id", "primary_act",
              "possible_acts", "ambiguity", "directness_1_5", "politeness_1_5",
              "formality_1_5", "distance_1_5", "evidence", "notes"]
    with (args.out_dir / "source_annotation.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for case in cases:
            writer.writerow({"case_id": case["id"], "source_text": case["source_text"],
                             "source_language": language_pair(case)[0], "target_language": language_pair(case)[1],
                             "relationship": case["relationship"],
                             "context": json.dumps(case["context"], ensure_ascii=False)})
    print(f"Validated structure: {len(cases)} draft cases / {len(groups)} groups")
    print(f"Built {len(requests)} requests: {request_path}")
    print("Draft ambiguity counts:", dict(Counter(c["annotation"]["ambiguity"] for c in cases)))
    print("No API calls made. Human validation and model evaluation remain pending.")


if __name__ == "__main__":
    main()
