# Eoeum | 2026 LinguaTech Expo

Eoeum은 앞선 대화와 화자 간 관계를 참고해, 번역에서 말의 의도와 어조가 어떻게 달라지는지 살펴보는 연구 프로젝트입니다. 영어에서 한국어로 옮기는 업무 대화를 중심으로, 한국어에서 영어로 옮기는 경우도 함께 탐색합니다.

## 공유 범위

- **연구 과정** — 사례 수집·선정 기준, 비교 실험 조건, 평가 방법을 기록합니다.
- **연구 결과** — 조건별 평가 결과와 사례 수를 집계하고, 대표적인 성공·실패 사례를 정리합니다.
- **인사이트** — 어떤 문맥 정보가 번역에 도움이 됐는지, 효과가 제한된 경우와 그 이유를 살펴봅니다.

## 같은 문장, 다른 쓰임

영어 문장 “I’m good.”은 앞선 대화에 따라 달리 옮길 수 있습니다.

| 앞선 대화 | 말의 의도 | 번역 예시 |
| --- | --- | --- |
| “커피 더 드릴까요?” | 제안을 정중히 사양함 | “괜찮아요, 고마워요.” |
| “요즘 어떻게 지내세요?” | 자신의 상태를 설명함 | “잘 지내요.” |

관계에 따라 말투도 달라집니다. 문맥이 부족해 여러 해석이 가능하다면, 번역에서도 그 여지를 살리는 것이 목표입니다.

## 비교 방법

같은 문장을 같은 모델로 번역하되, 제공하는 정보와 지침을 세 가지로 나눕니다.

| 조건 | 모델에 주는 정보와 지침 |
| --- | --- |
| B0 | 번역할 원문과 번역 방향, 대상 독자 |
| B1 | B0에 앞선 대화와 화자 간 관계를 추가 |
| P1 | B1에 화행, 관계에 맞는 어조, 모호함을 보존하는 지침을 추가 |

평가에서는 번역을 읽은 사람이 원문의 요청이나 거절을 같은 의도로 받아들이는지 살펴봅니다. 원문의 사실이 유지되는지, 근거 없는 약속이나 설명이 새로 들어갔는지, 공손함과 격식이 어떻게 달라졌는지도 확인합니다.

## 연구에 사용하는 파일

```text
2026_LinguaTech-Expo_Eoeum/
├── README.md
├── .gitignore
├── .github/workflows/
│   └── release-source.yml       # main으로 향하는 PR의 출발 브랜치 확인
├── pragmatics.py                # 번역 요청 생성 및 모델 호출
├── prompts.json                 # B0·B1·P1 비교 조건
├── data/                        # 방향별 파일럿 사례
│   ├── pilot_en_ko.jsonl
│   └── pilot_ko_en.jsonl
├── scripts/
│   └── prepare_experiment.py    # 사례 검증 및 실험 요청 파일 생성
├── kakao/                       # 카카오톡 1:1 대화 추출·익명화·DB 저장
│   ├── kakao_pipeline.py        # prepare / build (조각·80/20 분리·D4 통계)
│   ├── kakao_db.py              # 결과 → SQLite 적재, 여러 사람 DB 합치기
│   ├── schema.sql               # 공통 저장 구조
│   ├── kakao_web.py             # 로컬 화면
│   └── samples/                 # 가상 인물 데이터 + sample.db
├── docs/
│   ├── research_plan.md
│   ├── annotation_guide.md
│   ├── related_work.md
│   └── kakao_extraction.md      # 카카오톡 추출 원리와 DB 구조
├── tests/
│   ├── test_core.py
│   └── test_kakao.py
├── data/kakao/                  # 실제 카톡 원본·manifest, Git 추적 제외
└── outputs/                     # 로컬 생성 결과물·실제 DB, Git 추적 제외
```

## 브랜치별 역할

작업 브랜치 → `develop` → `main` 순서로 PR을 합칩니다.

1. 작업이 끝나면 PR의 **base**를 `develop`으로 선택합니다. `develop`에서 변경사항을 모아 함께 검증합니다.
2. 최종본이 준비되면 **base**가 `main`이고 **compare**가 `develop`인 PR을 엽니다. 출발 브랜치 확인이 통과하면 병합합니다.

| 작업 브랜치 | 담당 범위 |
| --- | --- |
| `feat/translation-core` | 카카오톡 대화 사례 수집 및 DB 저장, 공통 저장 필드 정의 |
| `research/pilot-evaluation` | 구글폼 응답 수집 및 DB 저장, 실험 입력과 주석 자료 준비 |
| `research/results-insights` | 조건별 결과 집계, 번역 오류 분석, 연구 과정과 인사이트 정리 |

첫 작업은 카카오톡 사례 1건과 구글폼 테스트 응답 1건을 공통 형식으로 저장하고, 파일럿 사례를 기준으로 결과표와 오류 분류 기준을 잡는 것입니다.

## 카카오톡 대화 추출

카카오톡 1:1 대화 내보내기 파일을 익명화해 대화 조각과 말투 통계를 만들고 SQLite DB에 저장합니다. 실제 대화와 결과물은 Git에 올리지 않습니다.

```sh
python3 kakao/kakao_web.py                      # 로컬 화면 (http://127.0.0.1:8765)
python3 kakao/kakao_pipeline.py prepare --manifest data/kakao/manifest.csv --out outputs/kakao
python3 kakao/kakao_pipeline.py build --out outputs/kakao
python3 kakao/kakao_db.py --out outputs/kakao --db outputs/kakao/research.db
python3 kakao/kakao_db.py --merge P01.db P02.db --db outputs/kakao/merged.db   # 팀원 DB 합치기
```

각자 자기 컴퓨터에서 `research.db`를 만들고, DB 파일은 GitHub이 아닌 비공개 경로로 담당자에게 보내 합칩니다. 참여자 ID(P01, P02 …)는 담당자가 미리 나눠 줍니다.

**팀원·담당자 진행 순서는 [docs/kakao_team_guide.md](docs/kakao_team_guide.md)** 를 먼저 보세요. 자세한 사용법은 [kakao/README.md](kakao/README.md), 단계별 원리와 저장 구조는 [docs/kakao_extraction.md](docs/kakao_extraction.md)에 있습니다.

## 실험 준비

Python 3으로 두 번역 방향의 실험 요청 파일을 생성합니다.

```sh
python3 scripts/prepare_experiment.py --direction en-ko --out-dir outputs/en-ko
python3 scripts/prepare_experiment.py --direction ko-en --out-dir outputs/ko-en
```

각 명령은 모델 호출 없이 요청 목록과 주석 양식을 생성합니다.

## 연구 자료

- 초기 [연구 설계](docs/research_plan.md)
- [주석 가이드](docs/annotation_guide.md)
- [선행연구](docs/related_work.md)

영어→한국어와 한국어→영어 파일럿은 각각 합성 사례 24개로 구성됩니다. 사례와 참고 번역은 사람 검토를 앞두고 있으며, 모델 비교 평가도 예정되어 있습니다.
