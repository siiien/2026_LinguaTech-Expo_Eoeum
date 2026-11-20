"""언어적 특성(D4) 계산 — 입력 통계(블록3)와 P2 평가가 같은 함수를 쓰도록 한 모듈.

단위 정의 (연구 설계 P2 기준과 맞춤)
- 메시지 : 전송 1회. 카톡 내보내기의 메시지 1개.
- 줄     : 메시지를 줄바꿈으로 나눈 것. '메시지(줄) 1개당 평균 길이(공백 포함)'의 분모.
           <사진> 같은 표식만 있는 줄은 길이 계산에서 제외.
- 턴     : 같은 사람이 연속으로 보낸 메시지 묶음. '끊어 보내기' = 턴당 메시지 수.
"""
from __future__ import annotations

import re
import statistics
from collections import Counter
from typing import Dict, Iterable, List, Optional, Sequence

TAG_RE = re.compile(r"<[^<>\s]{1,12}>")
EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿←-⇿⌀-⏿]")
LAUGH_RE = re.compile(r"ㅋ+|ㅎ{2,}|크크+|키키+|하하+|히히+")
CRY_RE = re.compile(r"[ㅠㅜ]{1,}")
FACE_RE = re.compile(r"\^\^;*|\^_\^|\^\.\^|;{2,}|:\)|:\(|:D|T_T|T\.T|ㅡㅡ;*|-_-;*|>_<|ㅇㅅㅇ|ㅎㅅㅎ|ㅇㅂㅇ|♡|♥")
CHOSUNG_RE = re.compile(r"(?<![ㄱ-ㅎㅏ-ㅣ가-힣])[ㄱ-ㅎ]{2,}(?![ㄱ-ㅎㅏ-ㅣ가-힣])")
_TRAIL = re.compile(
    r"(?:\s|[.!?~…,;:'\"`)\](\[\-_^*♡♥☆★/]|[ㄱ-ㅎㅏ-ㅣ]|<[^<>\s]{1,12}>|"
    "[\U0001F000-\U0001FAFF☀-➿⬀-⯿‍️]|T_T|:\\)|:D)+$"
)
_HANGUL_TAIL = re.compile(r"[가-힣]+$")

# 길이가 긴 것부터 비교 (가장 긴 어미 우선)
ENDINGS = sorted(set("""
습니다 습니까 니다 세요 셔요 이에요 에요 예요 어요 아요 여요 해요 래요 대요 네요 군요 지요 나요 가요 까요 게요 걸요 데요
는데요 던데요 거든요 잖아요 더라고요 더라구요 라고요 다고요 거예요 거에요 구요 고요 서요 니깐요
거든 잖아 는데 던데 인데 은데 텐데 건데 더라 더라고 더라구 구나 구만 거야 거지 건가 는가 려고 라고 다고 냐고 자고
지만 면서 다며 니까 니깐 거임 거임 듯요 는듯 을듯 할듯
다 야 어 아 여 해 지 네 냐 니 자 래 대 게 걸 데 듯 군 구 고 서 까 나 죠 요 쥬 셈 각 임 음 함 됨 옴 봄 줌 짐 씀 슴 심 감 냄 둠 놈 움 름
용 당 징 쟝 뎅 넹 넵 웅 엉 응 앙 옹 영 죵 염 셈
""".split()), key=len, reverse=True)
STANDALONE_POLITE = {"네", "넵", "넹", "넴", "예", "네네", "넵넵", "넹넹", "옙", "넷"}
_L_ENDINGS = {"게", "게요", "래", "래요", "까", "까요", "걸", "걸요", "거야", "듯"}


def _batchim(ch: str) -> int:
    code = ord(ch) - 0xAC00
    return code % 28 if 0 <= code < 11172 else -1


def _strip_tail(line: str) -> str:
    s = line
    for _ in range(10):
        new = _TRAIL.sub("", s)
        m = FACE_RE.search(new)
        if m and m.end() == len(new):
            new = new[: m.start()]
        if new == s:
            break
        s = new
    return s


def last_word(line: str) -> Optional[str]:
    s = _strip_tail(line)
    if not s:
        return None
    tok = s.split()[-1]
    m = _HANGUL_TAIL.search(tok)
    return m.group(0) if m else None


def extract_ending(line: str) -> Optional[str]:
    """줄 끝 어미(구어체 기준, 휴리스틱). 없으면 None (ㅇㅇ, 명사로 끝남 등)."""
    w = last_word(line)
    if not w:
        return None
    if w in STANDALONE_POLITE:
        return w
    for e in ENDINGS:
        if w.endswith(e):
            prev = w[-len(e) - 1] if len(w) > len(e) else ""
            if e in ("니다", "니까") or e.startswith("니"):
                if prev and _batchim(prev) == 17:  # ㅂ받침 → ㅂ니다/ㅂ니까
                    return "ㅂ" + e
            if e in _L_ENDINGS and prev and _batchim(prev) == 8:  # ㄹ받침 → ㄹ게/ㄹ래
                return "ㄹ" + e
            return e
    return None


def honorific(line: str) -> Optional[str]:
    """'존댓말' / '반말' / None(판단 불가)."""
    w = last_word(line)
    if not w:
        return None
    if w in STANDALONE_POLITE:
        return "존댓말"
    if re.search(r"(요|용|염|죠|쥬|시오|습니다|습니까)$", w):
        return "존댓말"
    if re.search(r"(니다|니까)$", w) and len(w) >= 3 and _batchim(w[-3]) == 17:
        return "존댓말"
    return "반말"


def text_len(line: str) -> int:
    return len(TAG_RE.sub("", line).strip())


def split_lines(messages: Iterable[str]) -> List[str]:
    out: List[str] = []
    for m in messages:
        out += [ln.strip() for ln in m.split("\n") if ln.strip()]
    return out


def _clean_tokens(line: str) -> List[str]:
    s = TAG_RE.sub(" ", line)
    s = EMOJI_RE.sub(" ", s)
    s = FACE_RE.sub(" ", s)
    s = LAUGH_RE.sub(" ", s)
    s = CRY_RE.sub(" ", s)
    toks = []
    for t in s.split():
        t = re.sub(r"^[^\w가-힣ㄱ-ㅎ]+|[^\w가-힣ㄱ-ㅎ]+$", "", t).lower()
        if t and re.search(r"[가-힣ㄱ-ㅎa-z]", t):
            toks.append(t)
    return toks


def _ratio(n: int, d: int) -> float:
    return round(n / d, 4) if d else 0.0


def _pct(values: Sequence[int], q: float) -> float:
    if not values:
        return 0.0
    v = sorted(values)
    k = (len(v) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return round(v[lo] + (v[hi] - v[lo]) * (k - lo), 1)


def style_stats(turns: Sequence[Sequence[str]], partner_turns: Optional[Sequence[Sequence[str]]] = None,
                top_k: int = 10) -> Dict:
    """turns: [[메시지, 메시지, ...], ...] (한 사람의 턴 목록)."""
    messages = [m for t in turns for m in t]
    lines = split_lines(messages)
    text_lines = [ln for ln in lines if text_len(ln) > 0]
    lens = [text_len(ln) for ln in text_lines]

    endings = Counter(e for e in (extract_ending(ln) for ln in text_lines) if e)
    n_end = sum(endings.values())
    top_endings = [{"ending": e, "count": c, "share": _ratio(c, n_end)} for e, c in endings.most_common(top_k)]
    frequent = [d["ending"] for d in top_endings if d["share"] >= 0.05][:5]

    hon = Counter(h for h in (honorific(ln) for ln in text_lines) if h)
    n_hon = sum(hon.values())

    all_msgs = len(messages)
    sticker = sum(m.count("<이모티콘>") for m in messages)
    media = Counter(t for m in messages for t in TAG_RE.findall(m))

    ends_with = Counter()
    for ln in text_lines:
        s = TAG_RE.sub("", ln).rstrip()
        if s.endswith(("..", "…")):
            ends_with["줄임표"] += 1
        elif s.endswith("."):
            ends_with["마침표"] += 1
        elif s.endswith("?"):
            ends_with["물음표"] += 1
        elif s.endswith("!"):
            ends_with["느낌표"] += 1
        elif s.endswith("~"):
            ends_with["물결"] += 1

    chosung = Counter(t for ln in text_lines for t in CHOSUNG_RE.findall(ln)
                      if not re.fullmatch(r"[ㅋㅎ]+", t))
    words = Counter(t for ln in text_lines for t in _clean_tokens(ln))

    distinctive: List[Dict] = []
    if partner_turns:
        p_words = Counter(t for ln in split_lines([m for t in partner_turns for m in t])
                          for t in _clean_tokens(ln))
        n_s, n_p = max(sum(words.values()), 1), max(sum(p_words.values()), 1)
        scored = []
        for w, c in words.items():
            if c < 3:
                continue
            score = ((c + 0.5) / n_s) / ((p_words.get(w, 0) + 0.5) / n_p)
            scored.append((score, c, w))
        distinctive = [{"word": w, "count": c, "ratio_vs_partner": round(s, 2)}
                       for s, c, w in sorted(scored, reverse=True)[:top_k] if s >= 2.0]

    msgs_per_turn = [len(t) for t in turns if t]
    return {
        "n_turns": len(msgs_per_turn),
        "n_messages": all_msgs,
        "n_text_lines": len(text_lines),
        "line_len": {
            "mean": round(statistics.mean(lens), 2) if lens else 0.0,
            "median": _pct(lens, 0.5), "p25": _pct(lens, 0.25), "p75": _pct(lens, 0.75), "p90": _pct(lens, 0.9),
        },
        "msgs_per_turn": {
            "mean": round(statistics.mean(msgs_per_turn), 2) if msgs_per_turn else 0.0,
            "share_multi": _ratio(sum(1 for n in msgs_per_turn if n >= 2), len(msgs_per_turn)),
        },
        "lines_per_message": round(len(lines) / all_msgs, 2) if all_msgs else 0.0,
        "endings": {"top": top_endings, "frequent": frequent, "lines_with_ending": n_end},
        "honorific": {"존댓말": hon.get("존댓말", 0), "반말": hon.get("반말", 0),
                      "polite_ratio": _ratio(hon.get("존댓말", 0), n_hon)},
        "expressive": {
            "laugh_line_ratio": _ratio(sum(1 for ln in text_lines if LAUGH_RE.search(ln)), len(text_lines)),
            "cry_line_ratio": _ratio(sum(1 for ln in text_lines if CRY_RE.search(ln)), len(text_lines)),
            "text_face_line_ratio": _ratio(sum(1 for ln in text_lines if FACE_RE.search(ln)), len(text_lines)),
            "emoji_line_ratio": _ratio(sum(1 for ln in text_lines if EMOJI_RE.search(ln)), len(text_lines)),
            "sticker_per_message": _ratio(sticker, all_msgs),
        },
        "line_end_punct": {k: _ratio(v, len(text_lines)) for k, v in ends_with.items()},
        "chosung_top": [{"token": t, "count": c} for t, c in chosung.most_common(top_k) if c >= 2],
        "words_top": [{"word": w, "count": c} for w, c in words.most_common(top_k) if c >= 2],
        "words_distinctive": distinctive,
        "media_tags": dict(media),
    }


# ---------------------------------------------------------------- 블록3 문장화
def _freq_word(r: float) -> str:
    if r < 0.02:
        return "거의 안 씀"
    if r < 0.10:
        return "가끔 씀"
    if r < 0.30:
        return "자주 씀"
    return "매우 자주 씀"


def render_block3(stats: Dict, by_relation: Optional[Dict[str, Dict]] = None) -> str:
    ll, ex = stats["line_len"], stats["expressive"]
    out = [
        f"메시지 길이: 한 줄 평균 {ll['mean']:.0f}자(공백 포함), 보통 {ll['p25']:.0f}~{ll['p75']:.0f}자",
        f"끊어 보내기: 한 번 말할 때 평균 {stats['msgs_per_turn']['mean']:.1f}개 메시지로 나눠 보냄",
    ]
    if stats["endings"]["frequent"]:
        out.append("자주 쓰는 어미: " + ", ".join("-" + e for e in stats["endings"]["frequent"]))
    out.append(f"웃음 표현(ㅋㅋ·ㅎㅎ): {_freq_word(ex['laugh_line_ratio'])}"
               f" / ㅠㅠ: {_freq_word(ex['cry_line_ratio'])}"
               f" / 카톡 이모티콘: {_freq_word(ex['sticker_per_message'])}"
               f" / 이모지: {_freq_word(ex['emoji_line_ratio'])}")
    if stats["chosung_top"]:
        out.append("자주 쓰는 초성체: " + ", ".join(d["token"] for d in stats["chosung_top"][:5]))
    if stats["words_distinctive"]:
        out.append("자주 쓰는 단어: " + ", ".join(d["word"] for d in stats["words_distinctive"][:6]))
    p = stats["line_end_punct"]
    out.append(f"문장 끝 마침표: {_freq_word(p.get('마침표', 0.0))}"
               + (f" / 물결(~): {_freq_word(p['물결'])}" if p.get("물결", 0) >= 0.02 else "")
               + (f" / 말줄임(..): {_freq_word(p['줄임표'])}" if p.get("줄임표", 0) >= 0.02 else ""))
    if by_relation:
        parts = []
        for rel, st in by_relation.items():
            h = st["honorific"]
            n = h["존댓말"] + h["반말"]
            if n < 5:
                continue
            pr = h["polite_ratio"]
            word = "존댓말" if pr >= 0.5 else "반말"
            parts.append(f"{rel}에게 {word} {round(max(pr, 1 - pr) * 100)}%")
        if parts:
            out.append("높임법: " + ", ".join(parts))
    return "\n".join(out)


# ---------------------------------------------------------------- P2 '표현 유사' 판정
def style_match(answer_text: str, ref_stats: Dict, tol: float = 0.30) -> Dict:
    """연구 설계의 '표현 유사' 기준:
    메시지(줄) 1개당 평균 길이 차이 ±tol 이내 + 자주 쓰는 어미 1개 이상 사용."""
    lines = [ln for ln in answer_text.splitlines() if text_len(ln) > 0]
    lens = [text_len(ln) for ln in lines]
    avg = statistics.mean(lens) if lens else 0.0
    ref_avg = ref_stats["line_len"]["mean"]
    diff = (avg - ref_avg) / ref_avg if ref_avg else 0.0
    endings = [extract_ending(ln) for ln in lines]
    used = sorted({e for e in endings if e and e in ref_stats["endings"]["frequent"]})
    hon = Counter(h for h in (honorific(ln) for ln in lines) if h)
    return {
        "n_lines": len(lines),
        "avg_line_len": round(avg, 2),
        "ref_avg_line_len": ref_avg,
        "len_diff_ratio": round(diff, 3),
        "len_ok": abs(diff) <= tol,
        "endings": [e for e in endings if e],
        "frequent_endings_used": used,
        "ending_ok": bool(used),
        "honorific": dict(hon),
        "style_similar": abs(diff) <= tol and bool(used),
    }
