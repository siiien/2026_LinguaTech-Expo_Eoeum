"""카카오톡 '대화 내보내기' 파일 파서.

지원 형식
- pc       : Windows/Mac PC 앱 txt   ([이름] [오후 3:12] 내용, 날짜 구분선)
- mobile   : Android / iOS txt (한국어) (2024년 5월 1일 오후 3:12, 이름 : 내용
                                     / 2024. 5. 1. 오후 3:12, 이름 : 내용)
- mac_csv  : Mac 앱 csv               (Date,User,Message)

여러 줄 메시지는 다음 메시지 시작 줄이 나올 때까지 이어 붙여 메시지 하나로 유지한다.
(메시지 경계 = 사용자가 전송 버튼을 누른 단위, 메시지 안 줄바꿈 = '\n')
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional

__all__ = ["RawMessage", "ParsedChat", "parse_file", "parse_text", "detect_format"]


@dataclass
class RawMessage:
    dt: datetime
    speaker: str
    text: str
    line_no: int
    seq: int = 0


@dataclass
class ParsedChat:
    source: str
    fmt: str
    title: Optional[str] = None  # "OOO 님과 카카오톡 대화" 의 OOO
    messages: List[RawMessage] = field(default_factory=list)
    system_lines: int = 0
    skipped_lines: int = 0
    warnings: List[str] = field(default_factory=list)


_EN_MONTHS = {
    m: i
    for i, m in enumerate(
        ["january", "february", "march", "april", "may", "june", "july",
         "august", "september", "october", "november", "december"], start=1)
}
for _k, _v in list(_EN_MONTHS.items()):
    _EN_MONTHS[_k[:3]] = _v

_TITLE_RES = [
    re.compile(r"^(?P<name>.+?) 님과 카카오톡 대화\s*$"),
    re.compile(r"^KakaoTalk Chats with (?P<name>.+?)\s*$"),
]
_HEADER_RES = [
    re.compile(r"^저장한 날짜\s*:"),
    re.compile(r"^Date Saved\s*:"),
]

# ---- PC ----
_PC_DATE_KO = re.compile(r"^-{3,}\s*(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일\s*\S*요일\s*-{3,}\s*$")
_PC_DATE_EN = re.compile(r"^-{3,}\s*(?:[A-Za-z]+,\s*)?([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})\s*-{3,}\s*$")
_PC_MSG = re.compile(
    r"^\[(?P<name>.+?)\] \[(?:(?P<ap1>오전|오후|AM|PM)\s?)?(?P<h>\d{1,2}):(?P<m>\d{2})(?:\s?(?P<ap2>AM|PM))?\]\s?(?P<text>.*)$"
)

# ---- Mobile (Android / iOS, 한국어 설정) ----
_DATE_PART = (r"(?:(?P<y1>\d{4})년\s*(?P<mo1>\d{1,2})월\s*(?P<d1>\d{1,2})일"
              r"|(?P<y2>\d{4})\.\s*(?P<mo2>\d{1,2})\.\s*(?P<d2>\d{1,2})\.?)")
_TIME_PART = r"(?:(?P<ap>오전|오후)\s*)?(?P<h>\d{1,2}):(?P<m>\d{2})"
_MOB_MSG = re.compile(rf"^{_DATE_PART}\s*{_TIME_PART},\s(?P<name>.+?)\s:\s?(?P<text>.*)$")
_MOB_SYS = re.compile(rf"^{_DATE_PART}\s*{_TIME_PART}\s?[:,]\s?(?P<text>.*)$")
_MOB_DATE_HDR = re.compile(rf"^{_DATE_PART}(?:\s*\S*요일)?(?:\s*{_TIME_PART})?\s*$")


def _hour24(ap: Optional[str], h: str) -> int:
    hh = int(h)
    if ap in ("오전", "AM"):
        return 0 if hh == 12 else hh
    if ap in ("오후", "PM"):
        return hh if hh == 12 else hh + 12
    return hh


def _mob_date(m: "re.Match[str]") -> date:
    y = m.group("y1") or m.group("y2")
    mo = m.group("mo1") or m.group("mo2")
    d = m.group("d1") or m.group("d2")
    return date(int(y), int(mo), int(d))


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    for enc in ("utf-8-sig", "cp949"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def detect_format(lines: List[str]) -> str:
    if lines and lines[0].lstrip("\ufeff").startswith("Date,User,Message"):
        return "mac_csv"
    pc = mob = 0
    for ln in lines[:800]:
        if _PC_MSG.match(ln) or _PC_DATE_KO.match(ln) or _PC_DATE_EN.match(ln):
            pc += 1
        elif _MOB_MSG.match(ln):
            mob += 1
    if pc == 0 and mob == 0:
        return "unknown"
    return "pc" if pc >= mob else "mobile"


def _match_title(line: str) -> Optional[str]:
    for r in _TITLE_RES:
        m = r.match(line)
        if m:
            return m.group("name").strip()
    return None


def _is_header(line: str) -> bool:
    return any(r.match(line) for r in _HEADER_RES)


def _finish(chat: ParsedChat) -> ParsedChat:
    for i, msg in enumerate(chat.messages):
        msg.text = msg.text.rstrip()
        msg.seq = i
    return chat


def _parse_pc(lines: List[str], chat: ParsedChat) -> None:
    cur_date: Optional[date] = None
    last: Optional[RawMessage] = None
    for no, ln in enumerate(lines, 1):
        m = _PC_DATE_KO.match(ln)
        if m:
            cur_date = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            last = None
            continue
        m = _PC_DATE_EN.match(ln)
        if m and m.group(1).lower() in _EN_MONTHS:
            cur_date = date(int(m.group(3)), _EN_MONTHS[m.group(1).lower()], int(m.group(2)))
            last = None
            continue
        m = _PC_MSG.match(ln)
        if m and cur_date is not None:
            ap = m.group("ap1") or m.group("ap2")
            dt = datetime(cur_date.year, cur_date.month, cur_date.day,
                          _hour24(ap, m.group("h")), int(m.group("m")))
            last = RawMessage(dt, m.group("name").strip(), m.group("text"), no)
            chat.messages.append(last)
            continue
        if not chat.messages:
            t = _match_title(ln)
            if t and chat.title is None:
                chat.title = t
            elif ln.strip() and not _is_header(ln):
                chat.skipped_lines += 1
            continue
        if last is not None:
            last.text += "\n" + ln
        elif ln.strip():
            # 날짜 구분선 바로 뒤의 시스템 문구 (예: "OOO님이 나갔습니다.")
            chat.system_lines += 1


def _parse_mobile(lines: List[str], chat: ParsedChat) -> None:
    last: Optional[RawMessage] = None
    for no, ln in enumerate(lines, 1):
        m = _MOB_MSG.match(ln)
        if m:
            d = _mob_date(m)
            dt = datetime(d.year, d.month, d.day, _hour24(m.group("ap"), m.group("h")), int(m.group("m")))
            last = RawMessage(dt, m.group("name").strip(), m.group("text"), no)
            chat.messages.append(last)
            continue
        if _MOB_DATE_HDR.match(ln):
            last = None
            continue
        if _MOB_SYS.match(ln):
            chat.system_lines += 1
            last = None
            continue
        if not chat.messages:
            t = _match_title(ln)
            if t and chat.title is None:
                chat.title = t
            elif ln.strip() and not _is_header(ln):
                chat.skipped_lines += 1
            continue
        if last is not None:
            last.text += "\n" + ln
        elif ln.strip():
            chat.skipped_lines += 1


def _parse_mac_csv(text: str, chat: ParsedChat) -> None:
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    for no, row in enumerate(reader, 2):
        try:
            dt = datetime.strptime(row["Date"].strip(), "%Y-%m-%d %H:%M:%S")
        except (KeyError, ValueError):
            chat.skipped_lines += 1
            continue
        user = (row.get("User") or "").strip()
        if not user:
            chat.system_lines += 1
            continue
        chat.messages.append(RawMessage(dt, user, row.get("Message") or "", no))


def parse_text(text: str, source: str = "<text>") -> ParsedChat:
    text = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    fmt = detect_format(lines)
    chat = ParsedChat(source=source, fmt=fmt)
    if fmt == "pc":
        _parse_pc(lines, chat)
    elif fmt == "mobile":
        _parse_mobile(lines, chat)
    elif fmt == "mac_csv":
        _parse_mac_csv(text, chat)
    else:
        chat.warnings.append("형식을 인식하지 못함 (PC txt / 모바일 txt / Mac csv 내보내기 파일인지 확인)")
    if chat.skipped_lines:
        chat.warnings.append(f"해석하지 못한 줄 {chat.skipped_lines}개")
    return _finish(chat)


def parse_file(path) -> ParsedChat:
    p = Path(path)
    return parse_text(_read_text(p), source=str(p))
