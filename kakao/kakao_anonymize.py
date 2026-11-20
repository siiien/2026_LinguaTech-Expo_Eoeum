"""메시지 정규화 + 익명화 + 검토 표시(flag).

원칙 (연구 설계 '제출 전 처리'):
- 이름·연락처·장소 등은 치환하되 어미·줄바꿈·이모티콘 표식은 그대로 둔다.
- 민감한 이야기·제3자 식별 가능 구간은 자동 삭제하지 않고 flag만 단다.
  → 검토 시트에서 사람이 최종 판단 (flag 달린 구간은 기본값 '제외').

치환 태그
  <본인> <상대> <인물1..n> <장소> <전화번호> <이메일> <링크> <주민번호>
  <카드번호> <계좌번호> <주소> <번호>
미디어 표식
  <사진> <동영상> <이모티콘> <삭제됨> <음성메시지> <통화> <파일> <송금>
  <위치> <연락처> <샵검색>
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

TAG_RE = re.compile(r"<[^<>\s]{1,12}>")

# ---------------------------------------------------------------- 미디어/시스템
_MEDIA_RULES: List[Tuple["re.Pattern[str]", str, str]] = [
    (re.compile(r"(?:사진(?:\s*\d+\s*장)?|Photos?)"), "<사진>", "media"),
    (re.compile(r"(?:동영상|Video)"), "<동영상>", "media"),
    (re.compile(r"(?:이모티콘|Emoticons?)"), "<이모티콘>", "media"),
    (re.compile(r"(?:삭제된 메시지입니다\.?|메시지가 삭제되었습니다\.?|This message has been deleted\.?)"),
     "<삭제됨>", "deleted"),
    (re.compile(r"(?:음성\s?메시지|Voice Message)"), "<음성메시지>", "media"),
    (re.compile(r"(?:부재중\s*)?(?:보이스톡|페이스톡|Voice ?Talk|Face ?Talk)(?:\s.*)?"), "<통화>", "media"),
    (re.compile(r"(?:파일|File)\s*[:：].*"), "<파일>", "media"),
    (re.compile(r"(?:(?:송금|이체)\s*[:：].*|카카오페이\s*(?:송금|정산).*)"), "<송금>", "media"),
    (re.compile(r"(?:지도|위치|Map|Location)\s*[:：].*"), "<위치>", "media"),
    (re.compile(r"(?:연락처|Contact)\s*[:：].*"), "<연락처>", "media"),
    (re.compile(r"샵검색\s*[:：].*"), "<샵검색>", "media"),
]
_STICKER_PREFIX = re.compile(r"^\((?:이모티콘|Emoticon)\)\s*")

# ---------------------------------------------------------------- 개인정보 패턴
_URL = re.compile(r"(?:https?://|www\.)\S+", re.I)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_RRN = re.compile(r"(?<!\d)\d{6}\s?-\s?[1-4]\d{6}(?!\d)")
_CARD = re.compile(r"(?<!\d)\d{4}[- ]\d{4}[- ]\d{4}[- ]\d{4}(?!\d)")
_PHONE = re.compile(r"(?<![\d-])(?:01[016789]|02|0[3-6][1-5]|070)[-. ]?\d{3,4}[-. ]?\d{4}(?![\d-])")
_ACCT = re.compile(r"(?<![\d-])\d{2,6}(?:-\d{2,8}){2,3}(?![\d-])")
_LONGNUM = re.compile(r"(?<!\d)\d{8,}(?!\d)")
_ADDR = re.compile(
    r"[가-힣]{2,}(?:특별시|광역시|특별자치시|특별자치도|시|도)\s[가-힣]{1,}(?:시|군|구)"
    r"(?:\s[가-힣0-9]+(?:읍|면|동|로|길))?(?:\s\d+(?:-\d+)?(?:번길)?)?"
)
_ROAD = re.compile(r"[가-힣0-9]{1,10}(?:로|길)\s?\d{1,4}(?:-\d{1,4})?(?:번길)?(?![0-9]*\s?(?:분|시|개|명|번|원|만|천|살|km|m|cm|층|호|등|위|일|월|년|kg|g))")
_APT = re.compile(r"(?<!\d)\d{1,4}동\s?\d{1,4}호")

# 제3자 언급 (이름 + 호칭) — flag만
_THIRD = re.compile(
    r"(?<![가-힣])([가-힣]{2,3})(씨|선배님?|후배|교수님?|쌤|선생님|언니|오빠|누나|팀장님?|과장님?|대리님?|부장님?|사장님?)"
)
_THIRD_STOP = {
    "우리", "친구", "아는", "학교", "회사", "동네", "옆집", "담당", "같은", "다른", "무슨", "어떤", "남자",
    "여자", "친한", "사촌", "직장", "동아리", "학과", "조교", "전공", "담임", "교양", "그냥", "그때", "우리반",
    "저희", "울반", "알바", "편의점", "카페", "식당", "가게", "옆반", "과외", "학원", "새로", "그런", "이런", "저런",
    "지도", "외부", "본부", "대표", "원래", "예전", "최근", "요즘", "옛날",
}

DEFAULT_SENSITIVE: Dict[str, List[str]] = {
    "건강": ["병원", "진료", "진단", "수술", "입원", "처방", "정신과", "우울", "공황", "상담센터", "약 먹", "임신", "생리"],
    "금전": ["대출", "빚", "카드값", "연체", "월급", "연봉"],
    "종교정치": ["교회", "성당", "절에", "예배", "정당", "투표", "대통령"],
    "성": ["성관계", "섹스", "야한"],
    "가족갈등": ["이혼", "부부싸움", "가정폭력"],
    "법": ["경찰", "고소", "소송", "합의금"],
}


def load_keywords(path: Optional[str]) -> Dict[str, List[str]]:
    """'분류:키워드' 또는 '키워드' 한 줄씩. # 은 주석."""
    if not path:
        return {k: list(v) for k, v in DEFAULT_SENSITIVE.items()}
    out: Dict[str, List[str]] = {}
    for ln in Path(path).read_text(encoding="utf-8-sig").splitlines():
        ln = ln.split("#", 1)[0].strip()
        if not ln:
            continue
        cat, _, kw = ln.partition(":") if ":" in ln else ("기타", "", ln)
        out.setdefault(cat.strip(), []).append(kw.strip())
    return out


def name_variants(name: str) -> List[str]:
    """표시 이름에서 본문 치환용 변형을 만든다. (홍길동 → 홍길동, 길동)"""
    out = {name.strip()}
    clean = re.sub(r"[^가-힣A-Za-z0-9 ]", "", name).strip()
    if clean:
        out.add(clean)
    if re.fullmatch(r"[가-힣]{3}", clean):
        out.add(clean[1:])
    return sorted((v for v in out if len(v) >= 2), key=len, reverse=True)


def split_list(cell: Optional[str]) -> List[str]:
    if not cell:
        return []
    return [x.strip() for x in re.split(r"[;|]", cell) if x.strip()]


@dataclass
class Normalized:
    text: str
    kind: str  # text | media | deleted
    flags: List[str] = field(default_factory=list)


class Anonymizer:
    def __init__(
        self,
        self_aliases: Iterable[str],
        partner_aliases: Iterable[str],
        extra_names: Optional[Dict[str, str]] = None,
        places: Iterable[str] = (),
        keywords: Optional[Dict[str, List[str]]] = None,
    ) -> None:
        pairs: List[Tuple[str, str]] = []
        for a in self_aliases:
            pairs += [(v, "<본인>") for v in name_variants(a)]
        for a in partner_aliases:
            pairs += [(v, "<상대>") for v in name_variants(a)]
        for a, tag in (extra_names or {}).items():
            pairs += [(v, tag) for v in name_variants(a)]
        for p in places:
            if len(p) >= 2:
                pairs.append((p, "<장소>"))
        seen: Dict[str, str] = {}
        for k, v in sorted(pairs, key=lambda kv: len(kv[0]), reverse=True):
            seen.setdefault(k, v)
        self.alias_map = seen
        self._alias_re = (re.compile("|".join(re.escape(k) for k in seen), re.I) if seen else None)
        self.keywords = keywords if keywords is not None else load_keywords(None)
        self.alias_hits: Counter = Counter()
        self.pii_hits: Counter = Counter()

    # -- 개별 단계 --------------------------------------------------------
    def _sub(self, pattern: "re.Pattern[str]", tag: str, text: str) -> str:
        new, n = pattern.subn(tag, text)
        if n:
            self.pii_hits[tag] += n
        return new

    def _acct(self, text: str) -> str:
        def rep(m: "re.Match[str]") -> str:
            s = m.group(0)
            if sum(c.isdigit() for c in s) >= 10:
                self.pii_hits["<계좌번호>"] += 1
                return "<계좌번호>"
            return s
        return _ACCT.sub(rep, text)

    def _names(self, text: str) -> str:
        if not self._alias_re:
            return text

        def rep(m: "re.Match[str]") -> str:
            key = m.group(0)
            for k, v in self.alias_map.items():
                if k.lower() == key.lower():
                    self.alias_hits[k] += 1
                    return v
            return key
        return self._alias_re.sub(rep, text)

    def scrub(self, text: str) -> str:
        t = self._sub(_URL, "<링크>", text)
        t = self._sub(_EMAIL, "<이메일>", t)
        t = self._sub(_RRN, "<주민번호>", t)
        t = self._sub(_CARD, "<카드번호>", t)
        t = self._sub(_PHONE, "<전화번호>", t)
        t = self._acct(t)
        t = self._sub(_ADDR, "<주소>", t)
        t = self._sub(_APT, "<주소>", t)
        t = self._sub(_ROAD, "<주소>", t)
        t = self._sub(_LONGNUM, "<번호>", t)
        t = self._names(t)
        return t

    def flags_for(self, text: str) -> List[str]:
        flags: List[str] = []
        for cat, kws in self.keywords.items():
            if any(kw and kw in text for kw in kws):
                flags.append(f"민감:{cat}")
        for m in _THIRD.finditer(text):
            if m.group(1) not in _THIRD_STOP:
                flags.append("제3자")
                break
        for tag in ("<인물", "<주민번호>", "<카드번호>", "<계좌번호>", "<송금>", "<주소>", "<연락처>", "<위치>"):
            if tag in text:
                flags.append("개인정보:" + tag.strip("<>"))
        return sorted(set(flags))

    # -- 메시지 1개 처리 --------------------------------------------------
    def process(self, raw_text: str) -> Normalized:
        s = raw_text.strip()
        for pat, tag, kind in _MEDIA_RULES:
            if pat.fullmatch(s):
                return Normalized(tag, kind, self.flags_for(tag))
        prefix = ""
        if _STICKER_PREFIX.match(s):
            prefix = "<이모티콘> "
            s = _STICKER_PREFIX.sub("", s)
        text = prefix + self.scrub(s)
        return Normalized(text, "text", self.flags_for(text))


def generic_fingerprint_text(text: str) -> str:
    """공유 채팅방 탐지용: 본인/상대 태그를 구분하지 않는 텍스트."""
    return re.sub(r"<(?:본인|상대)>", "<이름>", re.sub(r"\s+", " ", text)).strip()
