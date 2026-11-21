#!/usr/bin/env python
"""카카오톡 1:1 대화 → 연구용 데이터(D4 언어 통계, D5 대화 조각) 추출 파이프라인.

  1) prepare     : 파싱 + 익명화 + 대화 구간(세션) 나누기 + 검토 시트 생성
  2) (사람 검토)  : review/<참여자>_sessions.csv 의 include 열 확인 (1=사용, 0=제외)
  3) build       : 대화 조각 + 80/20 분리 + D4 통계(입력용/P2 기준) + 블록3 문장 + 요약
  보조 stats-text : 텍스트 파일(짧은 응답 과제 등)로 D4 통계 계산
       style-match: 생성 답변 vs 기준 통계로 '표현 유사' 판정 (P2)

외부 네트워크를 쓰지 않는다. 원본 내보내기 파일은 읽기만 하고, out/ 아래에는 익명화된 텍스트만 쓴다.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import random
import re
import shutil
import sys
from collections import Counter, OrderedDict, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

from kakao_anonymize import Anonymizer, generic_fingerprint_text, load_keywords, split_list
from kakao_features import render_block3, split_lines, style_match, style_stats, text_len
from kakao_parser import ParsedChat, parse_file

VERSION = "1.0"
REQUIRED = ["participant_id", "chat_id", "file", "relation"]


# ============================================================ 공통 입출력
def read_csv_any(path: Path) -> List[Dict[str, str]]:
    raw = Path(path).read_bytes()
    text = None
    for enc in ("utf-8-sig", "cp949"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw.decode("utf-8", errors="replace")
    return [{(k or "").strip(): (v or "").strip() for k, v in row.items()} for row in csv.DictReader(io.StringIO(text))]


def write_csv(path: Path, rows: Sequence[Dict], fields: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:  # 엑셀에서 한글 깨짐 방지
        w = csv.DictWriter(f, fieldnames=list(fields), extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def write_jsonl(path: Path, rows: Sequence[Dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> List[Dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


# ============================================================ prepare
class ManifestError(Exception):
    pass


def identify_speakers(chat: ParsedChat, row: Dict[str, str]) -> Tuple[Set[str], Set[str]]:
    speakers = Counter(m.speaker for m in chat.messages)
    names = set(speakers)
    self_given = set(split_list(row.get("self_name")))
    partner_given = set(split_list(row.get("partner_name")))
    listing = ", ".join(f"{n}({c})" for n, c in speakers.most_common())

    if self_given:
        self_set = names & self_given
        if not self_set:
            raise ManifestError(f"self_name {sorted(self_given)} 이(가) 화자 목록에 없음 → 화자: {listing}")
        partner_set = (names & partner_given) if partner_given else names - self_set
    elif partner_given:
        partner_set = names & partner_given
        if not partner_set:
            raise ManifestError(f"partner_name {sorted(partner_given)} 이(가) 화자 목록에 없음 → 화자: {listing}")
        self_set = names - partner_set
    elif chat.title and chat.title in names:
        partner_set = {chat.title}
        self_set = names - partner_set
    else:
        raise ManifestError(
            "본인/상대를 자동으로 구분하지 못함 (파일 제목의 이름과 화자 이름이 다름). "
            f"manifest 의 self_name 열에 본인 표시 이름을 적어 주세요 → 화자: {listing}")

    others = names - self_set - partner_set
    if others:
        raise ManifestError(f"1:1 대화가 아님 — 본인/상대 외 화자 {sorted(others)} (단톡방은 지원하지 않음)")
    if len(self_set) > 1 and not self_given:
        raise ManifestError(f"본인으로 추정되는 화자가 여러 명 {sorted(self_set)} → self_name 열을 지정해 주세요")
    if not partner_set:
        chat.warnings.append("상대 메시지가 없음")
    return self_set, partner_set


def _in_range(dt: datetime, row: Dict[str, str]) -> bool:
    lo, hi = row.get("date_from"), row.get("date_to")
    d = dt.date().isoformat()
    if lo and d < lo:
        return False
    if hi and d > hi:
        return False
    return True


def default_include(flags: Set[str]) -> str:
    return "0" if any(f.startswith("민감") or f == "제3자" for f in flags) else "1"


def cmd_prepare(args) -> int:
    manifest = Path(args.manifest)
    base = manifest.parent
    rows = read_csv_any(manifest)
    out = Path(args.out)
    missing = [c for c in REQUIRED if rows and c not in rows[0]]
    if not rows or missing:
        print(f"[오류] manifest 필수 열 누락: {missing or REQUIRED}", file=sys.stderr)
        return 1
    keywords = load_keywords(args.keywords)

    groups: "OrderedDict[Tuple[str, str], List[Dict[str, str]]]" = OrderedDict()
    extra_by_pid: Dict[str, Dict[str, str]] = defaultdict(dict)
    for r in rows:
        if r.get("include", "1") == "0":
            continue
        groups.setdefault((r["participant_id"], r["chat_id"]), []).append(r)
        mp = extra_by_pid[r["participant_id"]]
        for n in split_list(r.get("extra_names")):
            mp.setdefault(n, f"<인물{len(mp) + 1}>")

    report: List[str] = [f"# prepare 보고서  ({datetime.now():%Y-%m-%d %H:%M}, v{VERSION}, 세션 간격 {args.gap_min}분)", ""]
    errors = 0
    review_rows: Dict[str, List[Dict]] = defaultdict(list)
    review_text: Dict[str, List[str]] = defaultdict(list)
    written: Set[Path] = set()

    for (pid, chat_id), rs in groups.items():
        relation = rs[0]["relation"]
        if any(r["relation"] != relation for r in rs):
            print(f"[오류] {pid}/{chat_id}: 같은 chat_id 인데 relation 이 다름", file=sys.stderr)
            errors += 1
            continue
        per_file: List[List[Dict]] = []
        report.append(f"## {pid} / {chat_id}  (관계: {relation})")
        for r in rs:
            msgs: List[Dict] = []
            per_file.append(msgs)
            path = Path(r["file"])
            path = path if path.is_absolute() else base / path
            if not path.exists():
                print(f"[오류] 파일 없음: {path}", file=sys.stderr)
                errors += 1
                continue
            chat = parse_file(path)
            try:
                self_set, partner_set = identify_speakers(chat, r)
            except ManifestError as e:
                print(f"[오류] {pid}/{chat_id} {path.name}: {e}", file=sys.stderr)
                report.append(f"- {path.name}: 오류 — {e}")
                errors += 1
                continue
            anon = Anonymizer(
                self_aliases=sorted(self_set) + split_list(r.get("self_aliases")),
                partner_aliases=sorted(partner_set) + ([chat.title] if chat.title else []) + split_list(r.get("partner_aliases")),
                extra_names=extra_by_pid[pid],
                places=split_list(r.get("places")),
                keywords=keywords,
            )
            kept = 0
            dts = []
            for raw in chat.messages:
                if not _in_range(raw.dt, r):
                    continue
                norm = anon.process(raw.text)
                ts = raw.dt.isoformat(timespec="minutes")
                fp = hashlib.sha1(f"{ts}|{generic_fingerprint_text(norm.text)}".encode("utf-8")).hexdigest()[:16]
                msgs.append({
                    "participant_id": pid, "chat_id": chat_id, "relation": relation, "ts": ts,
                    "role": "self" if raw.speaker in self_set else "partner",
                    "kind": norm.kind, "text": norm.text, "flags": norm.flags, "fp": fp,
                })
                kept += 1
                dts.append(raw.dt)
            spk = Counter(m.speaker for m in chat.messages)
            report += [
                f"- 파일: {path.name}  형식={chat.fmt}  전체 메시지={len(chat.messages)}  기간 내={kept}"
                + (f"  ({min(dts):%Y-%m-%d} ~ {max(dts):%Y-%m-%d})" if dts else ""),
                f"  - 화자: 본인={sorted(self_set)} 상대={sorted(partner_set)}  "
                + ", ".join(f"{n}:{c}" for n, c in spk.most_common()),
                f"  - 이름 치환: " + (", ".join(f"{k}→{anon.alias_map[k]} {c}회" for k, c in anon.alias_hits.most_common()) or "없음"),
                f"  - 개인정보 치환: " + (", ".join(f"{k} {c}회" for k, c in anon.pii_hits.most_common()) or "없음"),
                f"  - 시스템 줄 {chat.system_lines}, 해석 못한 줄 {chat.skipped_lines}",
            ] + [f"  - 경고: {w}" for w in chat.warnings]

        # 같은 방을 여러 파일로 낸 경우만 겹치는 메시지 제거.
        # 한 파일 안에서 같은 분에 같은 말(예: ㅋㅋ 두 번)은 실제로 두 번 보낸 것이므로 유지:
        # 파일마다 (시각, 화자, 내용) 개수를 세고, 앞 파일들의 최대 개수를 넘는 것만 추가.
        seen: Counter = Counter()
        uniq = []
        for fm in per_file:
            local: Counter = Counter()
            for m in fm:
                key = (m["ts"], m["role"], m["text"])
                local[key] += 1
                if local[key] > seen[key]:
                    uniq.append(m)
            seen |= local
        uniq.sort(key=lambda m: m["ts"])  # 안정 정렬: 같은 분 안의 순서 유지

        # 세션(연속 대화 구간) 나누기
        sessions: "OrderedDict[str, List[Dict]]" = OrderedDict()
        last: Optional[datetime] = None
        sid = ""
        for m in uniq:
            t = datetime.fromisoformat(m["ts"])
            if last is None or t - last > timedelta(minutes=args.gap_min):
                sid = f"{pid}-{chat_id}-S{t:%Y%m%d%H%M}"
            m["session_id"] = sid
            sessions.setdefault(sid, []).append(m)
            last = t
        write_jsonl(out / "work" / pid / f"{chat_id}.messages.jsonl", uniq)
        written.add(out / "work" / pid / f"{chat_id}.messages.jsonl")

        n_flag = 0
        for sid, ms in sessions.items():
            flags = set(f for m in ms for f in m["flags"])
            inc = default_include(flags)
            n_flag += inc == "0"
            preview = " | ".join(("나" if m["role"] == "self" else "상대") + ": " + m["text"].replace("\n", " ")
                                 for m in ms[:4])
            review_rows[pid].append({
                "include": inc, "participant_id": pid, "chat_id": chat_id, "relation": relation,
                "session_id": sid, "start": ms[0]["ts"].replace("T", " "), "end": ms[-1]["ts"].replace("T", " "),
                "n_msgs": len(ms), "n_self_msgs": sum(m["role"] == "self" for m in ms),
                "flags": ";".join(sorted(flags)), "preview": preview[:200],
            })
            review_text[pid].append(f"===== {sid}  [{relation}]  include={inc}  flags={';'.join(sorted(flags)) or '-'}")
            for m in ms:
                who = "나" if m["role"] == "self" else "상대"
                review_text[pid].append(f"{m['ts'][11:]} {who}: " + m["text"].replace("\n", "\n" + " " * 12))
            review_text[pid].append("")
        report.append(f"- 세션 {len(sessions)}개 (flag 로 기본 제외 {n_flag}개), 메시지 {len(uniq)}개")
        report.append("")

    fields = ["include", "participant_id", "chat_id", "relation", "session_id", "start", "end",
              "n_msgs", "n_self_msgs", "flags", "preview"]
    for pid, rws in review_rows.items():
        path = out / "review" / f"{pid}_sessions.csv"
        if path.exists() and not args.reset_review:
            prev = {r["session_id"]: r.get("include", "") for r in read_csv_any(path)}
            kept = 0
            for r in rws:
                if prev.get(r["session_id"]) in ("0", "1"):
                    r["include"] = prev[r["session_id"]]
                    kept += 1
            report.append(f"- {pid}: 기존 검토 결과 {kept}개 세션 유지 (--reset-review 로 초기화)")
        write_csv(path, rws, fields)
        (out / "review" / f"{pid}_sessions.txt").write_text("\n".join(review_text[pid]), encoding="utf-8")

    # manifest 에서 빠진 참여자·채팅방의 작업 파일은 지운다 (남아 있으면 build·DB 에 옛 데이터가 섞임).
    # 검토 결정(review/*.csv)은 남겨 두어 다시 넣으면 이어서 쓸 수 있게 한다.
    stale_pids, stale_chats = [], []
    work = out / "work"
    for pdir in sorted(p for p in work.iterdir() if p.is_dir()) if work.exists() else []:
        for f in sorted(pdir.glob("*.messages.jsonl")):
            if f not in written:
                f.unlink()
                stale_chats.append(f"{pdir.name}/{f.name.split('.')[0]}")
        if not any(pdir.iterdir()):
            shutil.rmtree(pdir)
            stale_pids.append(pdir.name)
    if stale_chats:
        report.append(f"- 이번 manifest 에 없는 작업 파일 정리: {', '.join(stale_chats)}"
                      + (f" (참여자 {', '.join(stale_pids)} 제외됨)" if stale_pids else ""))

    write_json(out / "work" / "prepare_config.json",
               {"version": VERSION, "gap_min": args.gap_min, "manifest": str(manifest), "keywords": args.keywords,
                "created": datetime.now().isoformat(timespec="seconds")})
    (out / "prepare_report.md").write_text("\n".join(report), encoding="utf-8")
    print("\n".join(report))
    print(f"\n검토 시트: {out / 'review'}  (include 열 확인 후 build 실행)")
    return 1 if errors else 0


# ============================================================ build
def make_turns(ms: List[Dict]) -> List[Dict]:
    turns: List[Dict] = []
    for m in ms:
        if m["kind"] == "deleted":
            continue
        if turns and turns[-1]["role"] == m["role"]:
            turns[-1]["msgs"].append(m)
        else:
            turns.append({"role": m["role"], "msgs": [m]})
    return turns


def assign_split(sessions: List[Dict], ratio: float, mode: str, seed: int, pid: str,
                 warnings: List[str]) -> Set[str]:
    """관계(relation)별로 세션 단위 분리. 본인 텍스트 줄 수 기준으로 holdout ≈ ratio."""
    holdout: Set[str] = set()
    if ratio <= 0:
        return holdout
    by_rel: Dict[str, List[Dict]] = defaultdict(list)
    for s in sessions:
        by_rel[s["relation"]].append(s)
    for rel, ss in sorted(by_rel.items()):
        total = sum(s["n_self_lines"] for s in ss)
        if len(ss) < 2 or total == 0:
            warnings.append(f"관계 '{rel}' 세션 {len(ss)}개 → 분리 안 함(전부 입력용)")
            continue
        order = sorted(ss, key=lambda s: s["start"])
        if mode == "chrono":
            order = order[::-1]
        else:
            random.Random(f"{seed}|{pid}|{rel}").shuffle(order)
        target = ratio * total
        acc = 0
        picked = 0
        for s in order:
            if acc >= target or picked >= len(ss) - 1:
                break
            if acc > 0 and acc + s["n_self_lines"] > target * 1.5:
                continue  # 너무 큰 세션은 건너뛰어 비율 초과 방지
            holdout.add(s["session_id"])
            acc += s["n_self_lines"]
            picked += 1
    return holdout


def fmt_block4(partner_msgs: List[Dict], self_msgs: List[Dict], label: str) -> str:
    out = []
    for m in partner_msgs:
        out.append("상대: " + m["text"].replace("\n", "\n  "))
    for m in self_msgs:
        out.append(f"{label}: " + m["text"].replace("\n", "\n  "))
    return "\n".join(out)


def cmd_build(args) -> int:
    out = Path(args.out)
    work = out / "work"
    if not work.exists():
        print("[오류] work 폴더 없음 — prepare 먼저 실행", file=sys.stderr)
        return 1
    pids = sorted(p.name for p in work.iterdir() if p.is_dir())
    for pdir in sorted(p for p in out.iterdir() if p.is_dir()):  # prepare 에서 빠진 참여자의 옛 결과 정리
        if pdir.name not in pids and pdir.name not in ("work", "review") and (pdir / "sessions_split.csv").exists():
            shutil.rmtree(pdir)
    summary: List[Dict] = []
    fps: Dict[Tuple[str, str], Set[str]] = {}
    frag_fields = ["fragment_id", "participant_id", "chat_id", "relation", "session_id", "split", "start",
                   "n_partner_msgs", "n_self_msgs", "self_chars", "retrieval_eligible", "exclude_reason",
                   "flags", "partner_text", "self_text", "block4_text"]

    for pid in pids:
        warnings: List[str] = []
        msgs = []
        for f in sorted((work / pid).glob("*.messages.jsonl")):
            msgs += read_jsonl(f)
        for m in msgs:
            fps.setdefault((pid, m["chat_id"]), set()).add(m["fp"])

        review_path = out / "review" / f"{pid}_sessions.csv"
        include: Dict[str, bool] = {}
        if review_path.exists():
            include = {r["session_id"]: r.get("include", "1") != "0" for r in read_csv_any(review_path)}
        else:
            warnings.append("검토 시트 없음 → 모든 세션 사용")

        sessions: "OrderedDict[str, List[Dict]]" = OrderedDict()
        for m in msgs:
            sessions.setdefault(m["session_id"], []).append(m)
        n_total = len(sessions)
        info = []
        turns_by_sid: Dict[str, List[Dict]] = {}
        for sid, ms in sessions.items():
            if not include.get(sid, True):
                continue
            turns = make_turns(ms)
            turns_by_sid[sid] = turns
            n_self_lines = len([ln for ln in split_lines(m["text"] for m in ms if m["role"] == "self")
                                if text_len(ln) > 0])
            info.append({"session_id": sid, "relation": ms[0]["relation"], "chat_id": ms[0]["chat_id"],
                         "start": ms[0]["ts"], "n_self_lines": n_self_lines})
        holdout = assign_split(info, args.holdout, args.split_mode, args.seed, pid, warnings)

        fragments: List[Dict] = []
        stat_turns: Dict[str, Dict[str, List[List[str]]]] = {
            sp: {"self": [], "partner": []} for sp in ("train", "holdout")}
        stat_rel: Dict[str, Dict[str, List[List[str]]]] = {"train": defaultdict(list), "holdout": defaultdict(list)}
        for s in info:
            sid = s["session_id"]
            split = "holdout" if sid in holdout else "train"
            turns = turns_by_sid[sid]
            for t in turns:
                stat_turns[split][t["role"]].append([m["text"] for m in t["msgs"]])
                if t["role"] == "self":
                    stat_rel[split][s["relation"]].append([m["text"] for m in t["msgs"]])
            k = 0
            for i, t in enumerate(turns):
                if t["role"] != "self":
                    continue
                k += 1
                partner = turns[i - 1] if i > 0 and turns[i - 1]["role"] == "partner" else None
                pm = partner["msgs"] if partner else []
                sm = t["msgs"]
                self_chars = sum(text_len(ln) for ln in split_lines(m["text"] for m in sm))
                block4 = fmt_block4(pm, sm, pid)
                reasons = []
                if split == "holdout":
                    reasons.append("holdout")
                if not partner and not args.allow_no_context:
                    reasons.append("no_context")
                if self_chars == 0:
                    reasons.append("media_only")
                elif self_chars < args.min_self_chars:
                    reasons.append("too_short")
                if len(block4) > args.max_fragment_chars:
                    reasons.append("too_long")
                flags = sorted({f for m in pm + sm for f in m["flags"]})
                fragments.append({
                    "fragment_id": f"{sid}-F{k:02d}", "participant_id": pid, "chat_id": s["chat_id"],
                    "relation": s["relation"], "session_id": sid, "split": split, "start": (pm or sm)[0]["ts"],
                    "n_partner_msgs": len(pm), "n_self_msgs": len(sm), "self_chars": self_chars,
                    "retrieval_eligible": int(not reasons), "exclude_reason": ";".join(reasons),
                    "flags": ";".join(flags),
                    "partner_text": "\n".join(m["text"] for m in pm),
                    "self_text": "\n".join(m["text"] for m in sm),
                    "partner_msgs": [m["text"] for m in pm], "self_msgs": [m["text"] for m in sm],
                    "block4_text": block4,
                    "embed_text": ("\n".join(m["text"] for m in pm) + "\n" + "\n".join(m["text"] for m in sm)).strip(),
                })

        pdir = out / pid
        write_csv(pdir / "fragments.csv", fragments, frag_fields)
        write_jsonl(pdir / "fragments.jsonl", fragments)
        pool = [{k: f[k] for k in ("fragment_id", "participant_id", "relation", "embed_text", "block4_text",
                                   "partner_msgs", "self_msgs")}
                for f in fragments if f["retrieval_eligible"]]
        write_jsonl(pdir / "retrieval_pool.jsonl", pool)
        write_csv(pdir / "sessions_split.csv",
                  [dict(s, split="holdout" if s["session_id"] in holdout else "train") for s in info],
                  ["session_id", "chat_id", "relation", "start", "n_self_lines", "split"])

        d4 = {}
        for sp in ("train", "holdout"):
            overall = style_stats(stat_turns[sp]["self"], stat_turns[sp]["partner"])
            by_rel = {rel: style_stats(ts) for rel, ts in sorted(stat_rel[sp].items())}
            d4[sp] = {"participant_id": pid, "split": sp,
                      "note": "train=입력용 D4(블록3), holdout=P2 평가 기준" if args.holdout > 0 else "분리 안 함",
                      "overall": overall, "by_relation": by_rel}
            write_json(pdir / f"d4_{sp}.json", d4[sp])
        (pdir / "block3.txt").write_text(render_block3(d4["train"]["overall"], d4["train"]["by_relation"]) + "\n",
                                         encoding="utf-8")
        # 질문의 상대 관계(D6 높임법 상황)에 맞춰 쓸 수 있도록 관계별 블록3도 저장
        for rel, st in d4["train"]["by_relation"].items():
            safe = re.sub(r"[^\w가-힣]+", "_", rel)
            (pdir / f"block3_{safe}.txt").write_text(render_block3(st, {rel: st}) + "\n", encoding="utf-8")

        n_self_train = d4["train"]["overall"]["n_messages"]
        n_self_hold = d4["holdout"]["overall"]["n_messages"]
        if len(pool) < args.pool_target:
            warnings.append(f"검색 후보 {len(pool)}개 < {args.pool_target} (조건 D 불가 — 대화 추가 수집 또는 개수 조정)")
        if args.holdout > 0 and d4["holdout"]["overall"]["n_text_lines"] < 30:
            warnings.append(f"P2 기준(holdout) 텍스트 줄 {d4['holdout']['overall']['n_text_lines']}개 — 통계가 불안정할 수 있음")
        summary.append({
            "participant_id": pid,
            "chats": len({s["chat_id"] for s in info}),
            "relations": ";".join(sorted({s["relation"] for s in info})),
            "sessions_total": n_total, "sessions_included": len(info), "sessions_holdout": len(holdout),
            "self_msgs_train": n_self_train, "self_msgs_holdout": n_self_hold,
            "holdout_ratio": round(n_self_hold / (n_self_train + n_self_hold), 3) if n_self_train + n_self_hold else 0,
            "fragments": len(fragments), "retrieval_pool": len(pool),
            "pool_ge_5": int(len(pool) >= 5), f"pool_ge_{args.pool_target}": int(len(pool) >= args.pool_target),
            "warnings": " / ".join(warnings),
        })

    # 공유 채팅방: 두 참여자가 같은 방을 제출한 경우 (교차 비교 시 표시용)
    shared = []
    keys = sorted(fps)
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            if a[0] == b[0]:
                continue
            common = len(fps[a] & fps[b])
            denom = min(len(fps[a]), len(fps[b])) or 1
            if common >= 5 and common / denom >= 0.3:
                shared.append({"participant_a": a[0], "chat_a": a[1], "participant_b": b[0], "chat_b": b[1],
                               "common_msgs": common, "overlap": round(common / denom, 3)})
    write_csv(out / "shared_rooms.csv", shared,
              ["participant_a", "chat_a", "participant_b", "chat_b", "common_msgs", "overlap"])

    sfields = ["participant_id", "chats", "relations", "sessions_total", "sessions_included", "sessions_holdout",
               "self_msgs_train", "self_msgs_holdout", "holdout_ratio", "fragments", "retrieval_pool",
               "pool_ge_5", f"pool_ge_{args.pool_target}", "warnings"]
    write_csv(out / "summary.csv", summary, sfields)
    write_json(out / "build_config.json", {
        "version": VERSION, "holdout": args.holdout, "split_mode": args.split_mode, "seed": args.seed,
        "min_self_chars": args.min_self_chars, "max_fragment_chars": args.max_fragment_chars,
        "allow_no_context": args.allow_no_context, "created": datetime.now().isoformat(timespec="seconds")})

    for s in summary:
        print(f"{s['participant_id']}: 세션 {s['sessions_included']}/{s['sessions_total']} 사용 "
              f"(holdout {s['sessions_holdout']}), 본인 메시지 입력용 {s['self_msgs_train']} / P2용 {s['self_msgs_holdout']} "
              f"(비율 {s['holdout_ratio']}), 조각 {s['fragments']}, 검색 후보 {s['retrieval_pool']}")
        if s["warnings"]:
            print("   경고: " + s["warnings"])
    if shared:
        print("공유 채팅방 감지: " + ", ".join(f"{r['participant_a']}/{r['chat_a']}↔{r['participant_b']}/{r['chat_b']}"
                                         for r in shared))
    print(f"\n결과: {out}  (summary.csv, <참여자>/fragments.csv, retrieval_pool.jsonl, d4_*.json, block3.txt)")
    return 0


# ============================================================ 보조 명령
def cmd_stats_text(args) -> int:
    text = Path(args.input).read_text(encoding="utf-8-sig")
    blocks = [b for b in re.split(r"\n\s*\n", text.replace("\r\n", "\n")) if b.strip()]
    turns = [[ln for ln in b.split("\n") if ln.strip()] for b in blocks]
    st = style_stats(turns)
    res = {"source": str(args.input), "stats": st, "block3": render_block3(st)}
    if args.out:
        write_json(Path(args.out), res)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


def cmd_style_match(args) -> int:
    ref = json.loads(Path(args.ref).read_text(encoding="utf-8"))
    stats = ref.get("overall", ref.get("stats", ref))
    if args.relation:
        stats = ref["by_relation"][args.relation]
    answer = Path(args.answer).read_text(encoding="utf-8-sig")
    print(json.dumps(style_match(answer, stats, args.tol), ensure_ascii=False, indent=2))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    try:
        sys.stdout.reconfigure(errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("prepare", help="파싱·익명화·세션 나누기·검토 시트 생성")
    p.add_argument("--manifest", required=True)
    p.add_argument("--out", default="outputs/kakao")
    p.add_argument("--gap-min", type=int, default=60, help="이 시간(분)보다 오래 끊기면 새 대화 구간 (기본 60)")
    p.add_argument("--keywords", help="민감 키워드 파일 (분류:키워드). 없으면 기본 목록")
    p.add_argument("--reset-review", action="store_true", help="기존 검토 시트의 include 결정을 버림")
    p.set_defaults(func=cmd_prepare)

    b = sub.add_parser("build", help="조각·분리·통계 생성")
    b.add_argument("--out", default="outputs/kakao")
    b.add_argument("--holdout", type=float, default=0.2, help="P2 비교용으로 떼어 둘 비율 (0 이면 분리 안 함)")
    b.add_argument("--split-mode", choices=["random", "chrono"], default="random",
                   help="random=관계별 무작위(seed 고정), chrono=가장 최근 구간을 holdout")
    b.add_argument("--seed", type=int, default=42)
    b.add_argument("--min-self-chars", type=int, default=2)
    b.add_argument("--max-fragment-chars", type=int, default=600)
    b.add_argument("--allow-no-context", action="store_true", help="상대 발화 없이 본인이 먼저 말한 조각도 검색 후보로")
    b.add_argument("--pool-target", type=int, default=30)
    b.set_defaults(func=cmd_build)

    s = sub.add_parser("stats-text", help="텍스트 파일로 D4 통계 (한 줄=메시지, 빈 줄=턴 구분)")
    s.add_argument("--input", required=True)
    s.add_argument("--out")
    s.set_defaults(func=cmd_stats_text)

    m = sub.add_parser("style-match", help="답변 1개의 '표현 유사' 판정 (P2)")
    m.add_argument("--answer", required=True, help="답변 txt (메시지마다 줄바꿈)")
    m.add_argument("--ref", required=True, help="d4_holdout.json 등")
    m.add_argument("--relation", help="관계별 기준 사용 (예: 선배)")
    m.add_argument("--tol", type=float, default=0.30)
    m.set_defaults(func=cmd_style_match)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
