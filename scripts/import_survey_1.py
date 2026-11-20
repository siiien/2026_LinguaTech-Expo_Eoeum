#!/usr/bin/env python3
"""설문 1(인물 프로필) 구글폼 응답 CSV를 survey_1.db에 저장한다.

저장하는 테이블: participant, survey_item, survey_answer
열 대응표와 정제 규칙: docs/survey_1_fields.md

추가 설치는 필요 없다. (파이썬 기본 라이브러리만 사용)

사용 예 (레포 최상위 폴더에서 실행):
    # 1) DB에 쓰지 않고 CSV만 검사
    python scripts/import_survey_1.py --csv data/survey_1.csv --dry-run

    # 2) DB 복사본에 먼저 저장해 보기
    python scripts/import_survey_1.py --csv data/survey_1.csv --db data/research_test.db

    # 3) 실제 DB에 저장
    python scripts/import_survey_1.py --csv data/survey_1.csv --db data/survey_1.db

여러 번 실행해도 같은 행이 두 번 들어가지 않는다. 같은 참가자·문항은 최신 답변으로 덮어쓴다.
"""
from __future__ import annotations

import argparse
import csv
import re
import sqlite3
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# 1. 구글폼 열과 DB 필드 대응
#    문구를 바꿀 때는 docs/survey_1_fields.md도 같이 고친다.
# ---------------------------------------------------------------------------

TIMESTAMP_HEADER = "타임스탬프"
ID_HEADER = "참가자 ID를 입력해주세요."
ID_PATTERN = re.compile(r"^P\d{2,}$")

# D3 상황 전문 (참가자가 구글폼에서 읽은 문구 그대로)
D3_SCENARIOS = [
    ("01", "팀 제출일",
     "팀원 한 명이 제출을 하루 미루자고 합니다. 하루 더 쓰면 결과물이 좋아질 수 있지만, "
     "다른 팀원 두 명은 이미 잡힌 일정을 바꿔야 합니다. 제출일은 팀에서 정할 수 있습니다."),
    ("02", "발표 직전의 친구",
     "친한 친구가 발표 직전에 연습을 보여주고 솔직한 의견을 묻습니다. "
     "설명 한 부분이 이해하기 어렵지만, 발표까지 남은 시간은 10분입니다."),
    ("03", "팀 과제 상금",
     "팀 과제가 수상해 상금 10만 원을 받았습니다. 팀원들은 상금을 어떻게 나눌지 미리 정하지 않았습니다. "
     "자료 조사를 많이 맡은 사람도 있고, 맡은 분량은 적었지만 발표 당일 꼭 필요한 역할을 한 사람도 있습니다."),
    ("04", "겹친 저녁 약속",
     "친구와 저녁 약속을 잡아뒀는데, 같은 시간에 평소 만나기 어려운 지인이 잠깐 근처에 온다고 합니다. "
     "저녁 약속을 다른 날로 옮길 수는 있지만, 친구가 이미 그날 시간을 비워둔 상태입니다."),
    ("05", "다음 학기 모임",
     "친한 친구가 다음 학기에 다른 지역으로 갈까 고민 중이라고 말했습니다. "
     "며칠 뒤 공통 친구가 다음 학기 모임 계획을 짜면서 그 친구도 올 수 있는지 묻습니다. "
     "그 이야기를 다른 사람에게 해도 되는지는 들은 적이 없습니다."),
    ("06", "친구의 여행 준비",
     "친구가 처음으로 혼자 여행을 준비하고 있습니다. 친구가 정한 교통편은 저렴하지만 환승이 많고, "
     "비슷한 시간에 도착하는 더 쉬운 경로는 조금 비쌉니다. 친구는 결정하기 전까지는 직접 알아보고 싶다고 했습니다."),
    ("07", "마감 뒤에 온 신청",
     "동아리 행사 신청이 마감된 뒤 한 사람이 참여할 수 있냐고 연락했습니다. "
     "자리는 남아 있지만, 마감을 보고 신청을 포기한 사람이 있는지는 모릅니다."),
    ("08", "친구들과의 여행 일정",
     "친구들과 여행 일정을 정하고 있습니다. 대다수는 하루에 여러 곳을 보고 싶어 하고, "
     "한 친구는 중간에 쉬는 시간이 필요하다고 합니다. 일정을 나누면 각자 원하는 대로 움직일 수 있지만, "
     "다 같이 보내는 시간은 줄어듭니다."),
    ("09", "회의에서 나온 제안",
     "회의에서 팀원 한 명이 낸 제안에 걱정되는 점이 있습니다. 지금 공개적으로 말하면 모두 함께 검토할 수 있지만, "
     "제안한 팀원과 따로 이야기하면 사정을 먼저 들을 수 있습니다. 결정은 오늘 안에 해야 합니다."),
    ("10", "꾸준히 맡아온 역할",
     "단체 활동에서 꾸준히 맡아온 일이 있습니다. 앞으로 몇 주는 개인 일정 때문에 부담이 크지만, "
     "지금 역할을 넘기면 다른 사람이 새로 배워야 합니다. 역할을 나누면 부담은 줄어도 조율하는 일이 늘어납니다."),
]

# D4 카톡 답장: (번호, 상대, 받은 메시지, 질문 설명)
D4_MESSAGES = [
    ("01", "친한 친구", "미안, 지금 출발해서 20분 정도 늦을 것 같아.", None),
    ("02", "친한 친구", "혹시 오늘 저녁에 내가 쓴 글 잠깐 봐줄 수 있어?", None),
    ("03", "친한 친구", "오늘 준비한 거 잘 안 된 것 같아. 계속 신경 쓰여.", None),
    ("04", "친한 친구", "내일 저녁 같이 밥 먹을래?", "당신은 내일 저녁에 이미 일정이 있습니다."),
    ("05", "가끔 연락하는 선배", "미안, 지금 출발해서 20분 정도 늦을 것 같아.", None),
    ("06", "가끔 연락하는 선배", "혹시 오늘 저녁에 내가 쓴 글 잠깐 봐줄 수 있어?", None),
]


def build_items() -> list[dict]:
    """설문 1의 응답 칸 32개를 survey_item 행으로 만든다. (참가자 ID 열 제외)"""
    items: list[dict] = []

    def add(item_id, header, section, response_part, prompt_text,
            is_required=1, scenario_id=None, relation=None):
        items.append({
            "item_id": item_id,
            "header": header,
            "section": section,
            "scenario_id": scenario_id,
            "response_part": response_part,
            "relation": relation,
            "prompt_text": prompt_text,
            "is_required": is_required,
        })

    # D1 기본 정보
    add("D1_AGE", "나이대를 선택해주세요.", "D1", "choice",
        "나이대를 선택해주세요.\n"
        "(20세 미만(성인 참가자) / 20~24세 / 25~29세 / 30대 / 40대 이상 / 응답하지 않음)")
    add("D1_JOB", "현재 어떤 일을 하고 있나요?", "D1", "text",
        "현재 어떤 일을 하고 있나요?\n"
        "직업이나 전공을 넓은 범주로 적어주세요. 예를 들어 ‘인문계열 대학생’, ‘서비스직’처럼 적으면 됩니다.")

    # D2 자기소개와 취향
    add("D2_PERSONALITY", "본인의 성격을 어떻게 설명하고 싶나요?", "D2", "text",
        "본인의 성격을 어떻게 설명하고 싶나요?\n"
        "평소 성격이나 행동의 특징을 두세 가지 적고, 그중 하나가 드러난 일을 짧게 이야기해주세요.")
    add("D2_ACTIVITY",
        "평소 즐기거나 관심을 두는 활동 한 가지는 무엇인가요? 그 활동의 어떤 점을 좋아하나요?",
        "D2", "text",
        "평소 즐기거나 관심을 두는 활동 한 가지는 무엇인가요? 그 활동의 어떤 점을 좋아하나요?")
    add("D2_TEST", "이미 받아본 성격검사 결과 중 공유하고 싶은 내용이 있나요?", "D2", "text",
        "이미 받아본 성격검사 결과 중 공유하고 싶은 내용이 있나요?\n"
        "MBTI나 TCI 등 기억나는 결과를 편하게 적어주세요. 건너뛰어도 됩니다.",
        is_required=0)

    # D3 상황에 따른 선택과 이유 (상황마다 행동·이유 두 행)
    for num, title, situation in D3_SCENARIOS:
        scenario = f"상황 {num} · {title}\n{situation}"
        for part, label in (("action", "내가 할 행동"), ("reason", "가장 중요한 이유")):
            header = f"{num}. {label}"
            add(f"D3_{num}_{part.upper()}", header, "D3", part,
                f"{scenario}\n\n{header}", scenario_id=f"D3_{num}")

    # D4 카톡 답장
    for num, relation, message, note in D4_MESSAGES:
        prompt = message if note is None else f"{message}\n{note}"
        add(f"D4_{num}_REPLY", f"{num}. “{message}”", "D4", "reply", prompt,
            scenario_id=f"D4_{num}", relation=relation)
    add("D4_07_STYLE", "07. 방금 떠올린 선배에게 본인은 평소 어떤 말투를 쓰나요?", "D4", "choice",
        "방금 떠올린 선배에게 본인은 평소 어떤 말투를 쓰나요?\n(반말 / 존댓말 / 섞어 씀)",
        scenario_id="D4_07", relation="가끔 연락하는 선배")

    return items


ITEMS = build_items()
assert len(ITEMS) == 32, len(ITEMS)

# ---------------------------------------------------------------------------
# 2. DB 테이블 (없으면 만든다. 있으면 필요한 컬럼이 있는지만 확인한다.)
# ---------------------------------------------------------------------------

SCHEMA = {
    "participant": (
        """CREATE TABLE participant (
            participant_id TEXT PRIMARY KEY
        )""",
        {"participant_id"},
    ),
    "survey_item": (
        """CREATE TABLE survey_item (
            item_id       TEXT PRIMARY KEY,
            section       TEXT NOT NULL,
            scenario_id   TEXT,
            response_part TEXT NOT NULL,
            relation      TEXT,
            prompt_text   TEXT NOT NULL,
            is_required   INTEGER NOT NULL CHECK (is_required IN (0, 1))
        )""",
        {"item_id", "section", "scenario_id", "response_part", "relation",
         "prompt_text", "is_required"},
    ),
    "survey_answer": (
        """CREATE TABLE survey_answer (
            participant_id TEXT NOT NULL REFERENCES participant(participant_id),
            item_id        TEXT NOT NULL REFERENCES survey_item(item_id),
            answer_text    TEXT,
            PRIMARY KEY (participant_id, item_id)
        )""",
        {"participant_id", "item_id", "answer_text"},
    ),
}

# ---------------------------------------------------------------------------
# 3. CSV 읽기와 정제
# ---------------------------------------------------------------------------


class ImportError_(Exception):
    """사용자에게 보여줄 오류."""


def normalize_header(text: str) -> str:
    """열 이름 비교용: 유니코드 정규화, 따옴표 통일, 공백 정리."""
    text = unicodedata.normalize("NFC", text)
    for curly, straight in (("“", '"'), ("”", '"'), ("‘", "'"), ("’", "'")):
        text = text.replace(curly, straight)
    return re.sub(r"\s+", " ", text).strip()


def clean_answer(text: str | None) -> str | None:
    """답변 정제: 줄바꿈은 유지하고, 각 줄 끝 공백과 전체 앞뒤 공백만 지운다. 빈칸은 None."""
    if text is None:
        return None
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n")).strip()
    return text or None


def normalize_participant_id(text: str) -> str:
    return re.sub(r"\s+", "", text or "").upper()


TS_PATTERN = re.compile(
    r"(\d{4})[/.-](\d{1,2})[/.-](\d{1,2})\s+(오전|오후)?\s*(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(오전|오후)?"
)


def parse_timestamp(text: str) -> datetime | None:
    """'2026/11/20 2:36:23 오후 GMT+9' 형식을 읽는다. 못 읽으면 None."""
    m = TS_PATTERN.search(text or "")
    if not m:
        return None
    year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    hour, minute, second = int(m.group(5)), int(m.group(6)), int(m.group(7) or 0)
    ampm = m.group(4) or m.group(8)
    if ampm == "오후" and hour < 12:
        hour += 12
    elif ampm == "오전" and hour == 12:
        hour = 0
    try:
        return datetime(year, month, day, hour, minute, second)
    except ValueError:
        return None


def read_responses(csv_path: Path) -> tuple[dict[str, dict[str, str | None]], list[str]]:
    """CSV를 읽어 {참가자 ID: {item_id: 답변}}과 안내 메시지 목록을 돌려준다."""
    notes: list[str] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        try:
            raw_header = next(reader)
        except StopIteration:
            raise ImportError_("CSV가 비어 있습니다.")
        rows = list(reader)

    header = [normalize_header(h) for h in raw_header]
    dupes = sorted({h for h in header if header.count(h) > 1})
    if dupes:
        raise ImportError_("CSV에 이름이 같은 열이 있습니다: " + ", ".join(dupes))

    expected = {normalize_header(TIMESTAMP_HEADER), normalize_header(ID_HEADER)}
    expected |= {normalize_header(item["header"]) for item in ITEMS}
    missing = sorted(expected - set(header))
    extra = sorted(set(header) - expected)
    if missing or extra:
        msg = ["CSV 열 이름이 대응표와 다릅니다. 구글폼 문항 제목이 바뀌었는지 확인하세요."]
        if missing:
            msg.append("  대응표에는 있는데 CSV에 없는 열:")
            msg += [f"    - {h}" for h in missing]
        if extra:
            msg.append("  CSV에는 있는데 대응표에 없는 열:")
            msg += [f"    - {h}" for h in extra]
        raise ImportError_("\n".join(msg))

    col = {h: i for i, h in enumerate(header)}
    ts_idx = col[normalize_header(TIMESTAMP_HEADER)]
    id_idx = col[normalize_header(ID_HEADER)]

    # 참가자별로 가장 늦게 제출한 응답만 남긴다.
    latest: dict[str, tuple[tuple, list[str]]] = {}
    bad_ids: list[str] = []
    for row_no, row in enumerate(rows, start=2):
        if not any(cell.strip() for cell in row):
            continue  # 완전히 빈 줄
        row = row + [""] * (len(header) - len(row))
        pid = normalize_participant_id(row[id_idx])
        if not ID_PATTERN.match(pid):
            bad_ids.append(f"{row_no}번째 줄: '{row[id_idx]}'")
            continue
        ts = parse_timestamp(row[ts_idx])
        sort_key = (ts or datetime.min, row_no)
        if pid in latest:
            notes.append(f"참가자 {pid}의 응답이 두 개 이상 있습니다. 더 늦게 제출한 응답만 저장합니다.")
            if sort_key < latest[pid][0]:
                continue
        latest[pid] = (sort_key, row)

    if bad_ids:
        raise ImportError_("참가자 ID 형식(P01, P02 …)이 아닌 응답이 있습니다:\n  "
                           + "\n  ".join(bad_ids))
    if not latest:
        raise ImportError_("저장할 응답이 없습니다.")

    responses: dict[str, dict[str, str | None]] = {}
    missing_required: list[str] = []
    for pid, (_, row) in sorted(latest.items()):
        answers = {}
        for item in ITEMS:
            value = clean_answer(row[col[normalize_header(item["header"])]])
            answers[item["item_id"]] = value
            if value is None and item["is_required"]:
                missing_required.append(f"{pid} · {item['item_id']}")
        responses[pid] = answers

    if missing_required:
        raise ImportError_("필수 문항이 비어 있는 응답이 있습니다:\n  "
                           + "\n  ".join(missing_required))
    return responses, notes


# ---------------------------------------------------------------------------
# 4. DB 저장과 검증
# ---------------------------------------------------------------------------


def ensure_schema(conn: sqlite3.Connection) -> list[str]:
    created = []
    for table, (create_sql, required_cols) in SCHEMA.items():
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not exists:
            conn.execute(create_sql)
            created.append(table)
            continue
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        lacking = sorted(required_cols - cols)
        if lacking:
            raise ImportError_(
                f"DB의 {table} 테이블에 필요한 컬럼이 없습니다: {', '.join(lacking)}\n"
                "팀의 DB 설계와 이 스크립트의 컬럼 이름을 맞춰 주세요."
            )
    return created


def save(conn: sqlite3.Connection, responses: dict[str, dict[str, str | None]]) -> None:
    conn.executemany(
        "INSERT INTO participant (participant_id) VALUES (?) "
        "ON CONFLICT(participant_id) DO NOTHING",
        [(pid,) for pid in responses],
    )
    conn.executemany(
        """INSERT INTO survey_item
               (item_id, section, scenario_id, response_part, relation, prompt_text, is_required)
           VALUES (:item_id, :section, :scenario_id, :response_part, :relation, :prompt_text, :is_required)
           ON CONFLICT(item_id) DO UPDATE SET
               section = excluded.section,
               scenario_id = excluded.scenario_id,
               response_part = excluded.response_part,
               relation = excluded.relation,
               prompt_text = excluded.prompt_text,
               is_required = excluded.is_required""",
        ITEMS,
    )
    conn.executemany(
        """INSERT INTO survey_answer (participant_id, item_id, answer_text)
           VALUES (?, ?, ?)
           ON CONFLICT(participant_id, item_id) DO UPDATE SET
               answer_text = excluded.answer_text""",
        [(pid, item_id, text)
         for pid, answers in responses.items()
         for item_id, text in answers.items()],
    )


def verify(conn: sqlite3.Connection, pids: list[str]) -> bool:
    ids = [item["item_id"] for item in ITEMS]
    id_marks = ",".join("?" * len(ids))
    ok = True

    n_items = conn.execute(
        f"SELECT COUNT(*) FROM survey_item WHERE item_id IN ({id_marks})", ids
    ).fetchone()[0]
    print(f"  survey_item (설문 1 문항): {n_items}행 / 기대 {len(ids)}행")
    ok &= n_items == len(ids)

    for pid in pids:
        n = conn.execute(
            f"SELECT COUNT(*) FROM survey_answer WHERE participant_id = ? AND item_id IN ({id_marks})",
            [pid, *ids],
        ).fetchone()[0]
        multiline = conn.execute(
            f"SELECT COUNT(*) FROM survey_answer WHERE participant_id = ? AND item_id IN ({id_marks}) "
            "AND answer_text LIKE '%' || char(10) || '%'",
            [pid, *ids],
        ).fetchone()[0]
        print(f"  survey_answer {pid}: {n}행 / 기대 {len(ids)}행 (줄바꿈이 있는 답변 {multiline}개)")
        ok &= n == len(ids)

    req_ids = [item["item_id"] for item in ITEMS if item["is_required"]]
    req_marks = ",".join("?" * len(req_ids))
    pid_marks = ",".join("?" * len(pids))
    empty = conn.execute(
        f"SELECT participant_id, item_id FROM survey_answer "
        f"WHERE participant_id IN ({pid_marks}) AND item_id IN ({req_marks}) AND answer_text IS NULL",
        [*pids, *req_ids],
    ).fetchall()
    print(f"  필수 문항 빈칸: {len(empty)}개 / 기대 0개")
    ok &= not empty
    return ok


# ---------------------------------------------------------------------------
# 5. 실행
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="설문 1 구글폼 CSV를 survey_1.db에 저장합니다.")
    parser.add_argument("--csv", required=True, type=Path, help="구글폼 응답 CSV 경로")
    parser.add_argument("--db", type=Path, help="SQLite DB 경로 (예: data/survey_1.db)")
    parser.add_argument("--dry-run", action="store_true", help="DB에 쓰지 않고 CSV만 검사")
    parser.add_argument("--create-db", action="store_true",
                        help="DB 파일이 없을 때 새로 만듦 (경로 오타로 빈 DB가 생기는 것을 막기 위해 기본은 꺼짐)")
    args = parser.parse_args()

    try:
        if not args.csv.is_file():
            raise ImportError_(f"CSV 파일을 찾을 수 없습니다: {args.csv}")
        responses, notes = read_responses(args.csv)
        for note in notes:
            print(f"[안내] {note}")

        n_answers = sum(len(a) for a in responses.values())
        print(f"CSV 확인 완료: 참가자 {len(responses)}명 ({', '.join(responses)}), "
              f"답변 {n_answers}개")

        if args.dry_run:
            print("--dry-run: DB에는 아무것도 쓰지 않았습니다.")
            return 0
        if args.db is None:
            raise ImportError_("--db로 DB 경로를 지정하세요. (검사만 하려면 --dry-run)")
        if not args.db.exists() and not args.create_db:
            raise ImportError_(
                f"DB 파일이 없습니다: {args.db}\n"
                "경로를 확인하세요. 새 DB를 만들려는 것이라면 --create-db를 붙여 다시 실행하세요."
            )
        if sqlite3.sqlite_version_info < (3, 24, 0):
            raise ImportError_(f"SQLite 3.24 이상이 필요합니다. 현재 {sqlite3.sqlite_version}")

        conn = sqlite3.connect(args.db)
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            with conn:  # 전부 성공해야 저장되고, 하나라도 실패하면 전체 취소
                created = ensure_schema(conn)
                save(conn, responses)
            if created:
                print(f"[안내] 없던 테이블을 새로 만들었습니다: {', '.join(created)}")
            print(f"저장 완료: {args.db}")
            print("검증 결과")
            ok = verify(conn, list(responses))
        except sqlite3.Error as e:
            raise ImportError_(
                f"DB 저장 중 오류가 나서 이번 실행 내용은 모두 취소했습니다: {e}\n"
                "기존 테이블의 구조(기본 키, NOT NULL 컬럼)가 팀 설계와 같은지 확인하세요."
            )
        finally:
            conn.close()

        print("모든 검증을 통과했습니다." if ok else "검증에 실패한 항목이 있습니다. 위 결과를 확인하세요.")
        return 0 if ok else 1

    except ImportError_ as e:
        print(f"[오류] {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
