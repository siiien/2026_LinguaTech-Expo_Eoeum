# Eoeum | 2026 LinguaTech Expo

Eoeum은 앞선 대화와 화자 간 관계를 참고해, 번역에서 말의 의도와 어조가 어떻게 달라지는지 살펴보는 연구 프로젝트입니다. 영어에서 한국어로 옮기는 업무 대화를 중심으로, 한국어에서 영어로 옮기는 경우도 함께 탐색합니다.

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

## 디렉토리 구조

```text
2026_LinguaTech-Expo_Eoeum/
├── README.md
├── .gitignore
├── app.py                       # 로컬 웹 서버 실행
├── pragmatics.py                # 번역 요청 생성 및 모델 호출
├── prompts.json                 # B0·B1·P1 비교 조건
├── web/                         # 데모 화면
│   ├── index.html
│   ├── app.js
│   └── styles.css
├── data/                        # 방향별 파일럿 사례
│   ├── pilot_en_ko.jsonl
│   └── pilot_ko_en.jsonl
├── scripts/
│   └── prepare_experiment.py    # 사례 검증 및 실험 요청 파일 생성
├── docs/
│   ├── research_plan.md
│   ├── annotation_guide.md
│   └── related_work.md
├── tests/
│   └── test_core.py
└── outputs/                     # 로컬 생성 결과물, Git 추적 제외
```

## 브랜치별 역할

`main`은 각 작업의 변경사항을 PR로 검토하고 합치는 기본 브랜치입니다.

| 작업 브랜치 | 담당 범위 |
| --- | --- |
| `feat/translation-core` | `app.py`와 `pragmatics.py`의 서버 API 및 번역 로직, `prompts.json`의 비교 조건, 핵심 테스트 |
| `feat/web-demo` | `web/`의 원문·문맥 입력, 번역 결과 표시, 세 조건 비교 화면 |
| `research/pilot-evaluation` | `data/`, `scripts/`, `docs/`의 파일럿 사례, 실험 준비, 평가 기준 |

## 로컬 실행

Python 3으로 데모를 실행합니다.

```sh
python3 app.py
```

브라우저에서 <http://127.0.0.1:8765>를 열고 사례를 선택하면 API 키 없이 저장된 번역 예시를 볼 수 있습니다. 실제 모델로 번역하려면 서버를 시작하기 전에 `OPENAI_API_KEY`와 `PRAGMATICS_MODEL`을 설정합니다.

두 번역 방향의 실험 요청 파일은 다음 명령으로 생성할 수 있습니다.

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
