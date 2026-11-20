# 카카오톡 1:1 대화 추출 파이프라인

연구 설계의 **D4(언어적 특성 통계)·D5(실제 대화 조각)** 를 카카오톡 '대화 내보내기' 파일에서 만드는 도구.
Python 3.9+ 표준 라이브러리만 사용, 네트워크 통신 없음.

```
내보내기 txt/csv ──prepare──▶ 익명화된 메시지 + 세션 검토 시트 ──(사람 검토)──▶ build ──▶ 조각·80/20 분리·D4 통계·블록3 ──▶ SQLite DB
```

모든 명령은 저장소 루트에서 실행한다. 단계별 원리는 [docs/kakao_extraction.md](../docs/kakao_extraction.md).

```text
kakao/
├── kakao_parser.py      # 내보내기 형식 판별·파싱
├── kakao_anonymize.py   # 미디어 표식·익명화·민감 flag
├── kakao_features.py    # D4 통계·블록3·P2 '표현 유사' 판정 (입력과 채점이 같은 함수 사용)
├── kakao_pipeline.py    # prepare / build / stats-text / style-match
├── kakao_db.py          # 결과 → SQLite 적재
├── schema.sql           # 공통 저장 구조 (참여자·채팅·세션·메시지·조각·D4·설문·생성·채점)
├── kakao_web.py         # 로컬 화면 서버
├── web/index.html
└── samples/             # 가상 인물 데이터 + sample.db (실제 참여자 아님)
data/kakao/              # 실제 내보내기 파일·manifest — Git 제외
outputs/                 # 결과·실제 DB — Git 제외
```

> **실제 카톡 원본, manifest(실명 포함), 결과 폴더, 실제 DB는 커밋하지 않는다.** `.gitignore`가 `data/kakao/`, `outputs/`, `*.db`(sample.db 제외)를 막고 있다.

## DB (SQLite)

```powershell
python kakao/kakao_db.py --out outputs/kakao --db outputs/kakao/research.db
```

화면에서는 4단계의 **DB에 저장** 버튼. 가상 샘플 DB 다시 만들기: `python kakao/kakao_db.py --build-sample kakao/samples/sample.db`

| 테이블 | 한 줄 = | 비고 |
| --- | --- | --- |
| `participant` | 참여자 | 실명 없음. 번호↔실명 대응표는 DB 밖에 보관 |
| `chat` | 1:1 채팅방 | 관계(relation), 기간 |
| `shared_room` | 같은 방을 낸 참여자 쌍 | 교차 비교 표시용 |
| `session` | 대화 구간 | include(검토 결과), split(train/holdout) |
| `message` | 메시지(전송 1회) | 익명화된 text. **포함된 세션만** 저장 |
| `fragment` | 조각(상대 턴+본인 턴) | block4_text, embed_text, 검색 후보 여부, total_chars(②-2 계산용) |
| `fragment_embedding` | 조각 벡터 | 검색 단계에서 채움 |
| `d4_stats` | 참여자×split×관계 | train=블록3 입력용, holdout=P2 기준 |
| `survey_item` / `survey_answer` | 설문 문항 / 답변 | D1~D3, D4 과제, D6 골드셋 (구글폼 적재 대상) |
| `generation` | 생성 답변 | 참여자×조건×질문×회차, 모델·temperature·seed 기록 |
| `generation_fragment` | 생성에 넣은 조각 | P4 원문 중복 계산 |
| `score` | 지표 값 | P1~P5, 채점자별 |

뷰: `v_retrieval_pool`(D5 검색 후보), `v_model_visible_answers`(D6 골드셋 제외 — 프롬프트 생성은 이 뷰만 사용), `v_condition_means`(조건별 평균), `v_participant_summary`.

같은 참여자를 다시 적재하면 채팅·세션·메시지·조각·D4만 새로 쓰고, 설문·생성·채점은 유지한다. 생성 기록이 참조하는 조각이 있으면 적재를 멈춘다(실험 후 데이터가 바뀌는 것 방지).

## 1. 무엇이 고도화됐나

| 기존(단순 추출) | 지금 |
| --- | --- |
| 한 가지 내보내기 형식만 | PC txt, Android/iOS txt(두 가지 날짜 표기), Mac csv 자동 판별 |
| 여러 줄 메시지가 깨짐 | 다음 메시지 시작 줄까지 이어 붙여 **메시지 1개로 유지** (메시지 경계 = 전송 단위) |
| 화자 직접 지정 | 파일 제목 "OOO 님과 카카오톡 대화"로 본인/상대 자동 구분, 단톡방(3인 이상) 감지 시 중단 |
| 사진·이모티콘이 텍스트로 섞임 | `<사진>` `<이모티콘>` `<삭제됨>` `<파일>` 등 표식으로 정규화 (이모티콘 빈도 통계에 사용) |
| 익명화 수동 | 이름(성 뗀 이름 포함)·전화·이메일·링크·계좌·카드·주민번호·주소 자동 치환, 제3자 이름은 manifest로 `<인물N>` |
| 민감 구간 판단 없음 | 건강·금전·종교·법 등 키워드와 "이름+호칭"(예: 지수언니) 감지 → 해당 세션은 **기본 제외**, 사람이 최종 결정 |
| 대화 덩어리 | **세션(연속 대화 구간)** → **턴(같은 사람 연속 메시지)** → **조각(상대 턴 + 본인 턴)** |
| 분리 없음 | 세션 단위 **80/20 분리**(관계별 층화, seed 고정). 20%는 검색 후보에서 빠지고 P2 기준 통계로만 사용 |
| 통계 없음 | 줄 길이, 끊어 보내기, 어미 Top, 존댓말/반말(관계별), ㅋㅋ·ㅠㅠ·이모티콘·이모지 빈도, 초성체, 상대 대비 특징 단어, 마침표 습관 |
| — | 블록3에 바로 넣을 문장(`block3.txt`, 관계별 `block3_<관계>.txt`) 자동 생성 |
| — | 같은 방을 두 참여자가 냈는지 감지(`shared_rooms.csv`) → 교차 비교에서 표시 |
| — | 조건 D(검색 후보 30개) 가능 여부, P2 기준 데이터 양 경고(`summary.csv`) |

## 화면으로 실행 (로컬)

```powershell
python kakao/kakao_web.py
```

브라우저에서 http://127.0.0.1:8765 를 연다. 1 파일 등록 → 2 익명화·세션 나누기 → 3 검토(세션별 대화 보기, 사용/제외 체크) → 4 결과(요약, 블록3, 입력용 vs P2 기준 통계, 조각 목록).
이 컴퓨터에서만 열리며, 올린 파일은 `data/kakao/raw/`, 결과는 `outputs/kakao_web/`에 저장된다. 아래 명령줄 방식과 결과가 같다.

## 2. 사용법

### ① 카톡 내보내기
- PC: 채팅방 → ≡ 메뉴 → **대화 내용 → 대화 내보내기** (txt)
- 모바일: 채팅방 → ≡ → ⚙ 설정 → **대화 내용 내보내기 → 텍스트만 보내기**

### ② manifest.csv 작성 (한 줄 = 내보내기 파일 1개)

| 열 | 필수 | 설명 |
| --- | --- | --- |
| participant_id | O | P01 … |
| chat_id | O | 참여자 안에서 방 구분 (C01, C02). 같은 방을 여러 파일로 냈으면 같은 chat_id → 자동 중복 제거 |
| file | O | manifest 기준 상대경로 |
| relation | O | 친구/가족/선배/후배/… (높임법 분석·층화 분리에 사용) |
| self_name / partner_name | | 자동 구분이 안 될 때만. 표시 이름이 바뀐 경우 `;`로 여러 개 |
| self_aliases / partner_aliases | | 본문에서 부르는 별명 (예: `서연이;연이`) |
| extra_names | | 대화에 나오는 제3자 이름 → `<인물1>`… (참여자 안에서 번호 고정) |
| places | | 치환할 장소명 → `<장소>` |
| date_from / date_to | | 참여자가 고른 기간 (YYYY-MM-DD) |

예시: [samples/manifest.csv](samples/manifest.csv)

### ③ 실행

```powershell
python kakao/kakao_pipeline.py prepare --manifest kakao/samples/manifest.csv --out outputs/kakao
```

`outputs/kakao/prepare_report.md` 에서 화자 구분·이름 치환 횟수·해석 못한 줄을 확인한다.
(성 뗀 이름이 일반 단어와 겹치면 치환 횟수가 비정상적으로 많아짐 → 그 경우 표시 이름만 남기도록 aliases 조정)

### ④ 검토 (참여자 본인 또는 팀)
- `outputs/kakao/review/P01_sessions.txt` : 익명화된 전체 대화를 세션별로 읽기
- `outputs/kakao/review/P01_sessions.csv` : `include` 열 1=사용, 0=제외. flag 있는 세션은 0으로 시작
- 다시 prepare 해도 검토 결정은 유지됨 (`--reset-review` 로 초기화)

### ⑤ 빌드

```powershell
python kakao/kakao_pipeline.py build --out outputs/kakao
```

| 옵션 | 기본 | 의미 |
| --- | --- | --- |
| `--holdout` | 0.2 | P2 비교용 비율. 0이면 분리 안 함(분리 미채택 시) |
| `--split-mode` | random | random=관계별 무작위(seed 고정) / chrono=가장 최근 구간을 holdout |
| `--seed` | 42 | 분리 재현용 |
| `--gap-min` (prepare) | 60 | 이 시간(분) 이상 끊기면 새 세션 |
| `--min-self-chars` | 2 | 본인 텍스트가 이보다 짧은 조각은 검색 후보 제외 |
| `--max-fragment-chars` | 600 | 너무 긴 조각 제외 (프롬프트 분량 관리) |
| `--allow-no-context` | 끔 | 본인이 먼저 말한 조각(상대 발화 없음)도 후보로 |

## 3. 결과물 (`outputs/kakao/`)

| 파일 | 용도 |
| --- | --- |
| `summary.csv` | 참여자별 세션·메시지·조각·검색 후보 수, 조건 D 가능 여부, 경고 |
| `P01/fragments.csv` | 모든 조각 (split, 제외 사유, flag 포함). 팀원 검토·채점 분배용 |
| `P01/retrieval_pool.jsonl` | **D5 검색 후보** (train 80% 중 조건 충족 조각). `embed_text` 를 임베딩, `block4_text` 를 프롬프트에 그대로 삽입. `fragment_id` 를 생성 기록에 남기면 P4 계산 가능 |
| `P01/d4_train.json` | **D4 입력용 통계** (전체 + 관계별) |
| `P01/d4_holdout.json` | **P2 평가 기준 통계** (입력에 안 들어간 20%) |
| `P01/block3.txt`, `block3_<관계>.txt` | 블록3에 넣을 문장. D6 질문의 상대 관계에 맞는 파일 사용 |
| `P01/sessions_split.csv` | 세션별 train/holdout 기록 |
| `shared_rooms.csv` | 두 참여자가 같은 방을 낸 경우 |
| `build_config.json` | 분리 비율·seed 등 재현 정보 |

block4_text 형식 (메시지 경계 유지, 메시지 안 줄바꿈은 들여쓰기):
```
상대: <본인>아 조별과제 걔 또 안 왔대
P01: 아 진짜 너무하다
P01: 이번엔 교수님한테 말해야 할 듯
```

## 4. 보조 명령

짧은 응답 과제(D4 산출용)나 골드셋 답변의 통계 — 한 줄=메시지, 빈 줄=다른 답변:
```powershell
python kakao/kakao_pipeline.py stats-text --input short_task_P01.txt
```

P2 '표현 유사' 판정 (줄당 평균 길이 ±30% + 자주 쓰는 어미 1개 이상):
```powershell
python kakao/kakao_pipeline.py style-match --answer answer.txt --ref outputs/kakao/P01/d4_holdout.json
```
P2 자동 채점 코드는 `kakao_features.style_stats` / `style_match` 를 import 해서 쓰면 입력 통계와 같은 기준으로 계산된다.

## 5. 정의 (P2와 공유)
- **메시지** = 전송 1회. **줄** = 메시지를 줄바꿈으로 나눈 것. `<사진>`처럼 표식만 있는 줄은 길이 계산 제외.
- **줄 길이** = 공백 포함 글자 수(표식 제외).
- **어미** = 줄 끝의 ㅋㅋ·ㅠㅠ·이모지·문장부호를 떼고 마지막 어절에서 구어체 어미 목록과 가장 길게 일치하는 것.
  받침을 보고 `-ㄹ게`, `-ㄹ게요`, `-ㅂ니다`를 구분. 목록은 `kakao_features.ENDINGS`.
- **존댓말** = 줄 끝이 요/죠/습니다/ㅂ니까 등, 또는 "네·넵·예". ㅋㅋ·ㅇㅇ만 있는 줄은 판단 불가.
- **세션** = 메시지 간격 60분 이하로 이어진 구간. 80/20 분리 단위(인접 대화가 양쪽에 섞이는 것 방지).

## 6. 한계·주의
- 어미·높임법은 형태소 분석기 없는 휴리스틱. 파일럿에서 100줄 정도 사람이 확인해 정확도를 보고서에 적는 것을 권장.
- 이름 자동 치환은 **카톡 표시 이름 기준**. 본문 별명, 제3자 이름, 학교·회사명은 manifest에 적어야 치환됨 → 검토 txt를 꼭 읽을 것.
- 민감 키워드 목록은 `--keywords` 파일(`분류:키워드` 한 줄씩)로 교체 가능.
- 원본 내보내기 파일, `outputs/kakao/prepare_report.md`(실제 표시 이름이 적힘), `outputs/kakao/work/`(타임스탬프 포함)는 외부 공유·외부 API 전송 금지. 외부로 나가는 것은 `retrieval_pool.jsonl` 의 익명화된 텍스트뿐이어야 함.
- `samples/` 는 가상 인물 데이터(테스트용). 영어 설정 모바일 내보내기는 미지원.

## 7. 테스트
```powershell
python -m unittest discover -s tests
```
