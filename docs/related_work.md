# 선행연구 검토 메모

확인일: 2026-10-17. 아래는 읽은 초록과 명시한 본문 범위에 근거한 초기 조사다. 체계적 문헌조사나 신규성 확정은 아니다. 코드 재현·데이터 라이선스 검토는 아직 하지 않았다. 아래 요약은 직접 인용이 아니다.

## 1. Towards Style Alignment in Cross-Cultural Translation

- Havaldar, Stein, Wong, Ungar. ACL 2025.
- 논문: https://aclanthology.org/2025.acl-long.1550/
- 본문: https://aclanthology.org/2025.acl-long.1550.pdf
- 저자가 공개한 코드: https://github.com/shreyahavaldar/style_alignment
- 확인 범위: 초록, 본문 1~4절 및 그림 6의 방법 개요.
- 번역에서 공손함·친밀감·격식이 유지되는지 측정하고, 문체에 맞는 예시를 검색해 번역에 제공하는 RASTA를 제안한다.
- 확인한 데이터 설명에는 공손함의 영·스페인·일·중, 친밀감의 6개 언어, 격식의 4개 언어가 제시된다. 해당 데이터 설명의 언어 목록에 한국어는 없다. 이를 논문의 모든 부가 실험에 대한 주장으로 확대하지 않는다.
- 이번 연구의 함의: 문체 보존 자체는 새 기여가 아니다. 문맥에 따른 화행 변화, 모호함 유지, 약속·거절의 과도한 명시화를 별도로 검증할 필요가 있다. RASTA의 단순 재명명을 피한다.
- 다음 확인: 전체 부록, 프롬프트, 평가자 설계, 한국어로 재현 가능한 데이터와 코드 조건.

## 2. MultiPragEval: Multilingual Pragmatic Evaluation of Large Language Models

- Park et al. GenBench 2024.
- 논문: https://aclanthology.org/2024.genbench-1.7/
- 확인 범위: 초록.
- 영·독·한·중 4개 언어의 함축과 문맥 이해를 평가한다.
- 이번 연구의 함의: 한국어 화용 이해 평가가 이미 존재한다. 화용 문제를 맞히는 능력과 번역 수신자가 같은 뜻으로 이해하는지는 구분해 실험한다.
- 다음 확인: 데이터 구성, 한국어 사례 작성 절차, 재사용 조건, 이번 화행 분류와의 중복.

## 3. Context-Aware Neural Machine Translation for Korean Honorific Expressions

- Hwang, Kim, Jung. Electronics 2021, 10(13), 1589.
- 논문: https://www.mdpi.com/2079-9292/10/13/1589
- 확인 범위: 검색으로 확인한 저널의 초록/요약. 본문 페이지 열람은 실패했다. 본문을 읽은 것으로 취급하지 않는다.
- 영어→한국어 높임 표현 번역에서 문맥을 사용하는 모델을 다룬다.
- 이번 연구의 함의: 관계 정보와 문맥을 넣는 것만으로 신규성을 주장할 수 없다. 초기 한국어→영어 화행 보존과는 방향·평가 목표가 다르다.
- 다음 확인: 원문 확보 후 입력 문맥과 관계 표지, 데이터, 사람 평가 확인.

## 4. Kosmic: Korean Text Similarity Metric Reflecting Honorific Distinctions

- Hwang et al. LREC-COLING 2024.
- 논문: https://aclanthology.org/2024.lrec-main.870/
- 확인 범위: 초록.
- 한국어 텍스트 쌍의 의미와 높임에 따른 어조를 함께 다루는 유사도 지표와 사람 주석 벤치마크를 제안한다.
- 이번 연구의 함의: 한국어 어조 보존에 대한 기존 평가를 참고할 수 있다. 한국어 내부 텍스트 유사도를 한→영 화행 보존의 검증된 지표로 바로 사용하지 않는다.
- 다음 확인: 평가 척도, 라이선스, 영어로의 확장 타당성.

## 5. PETra: A Multilingual Corpus of Pragmatic Explicitation in Translation

- Osmelak et al. LREC 2026.
- 논문: https://aclanthology.org/2026.lrec-1.689/
- 확인 범위: 초록.
- 번역에서 문화적 배경 등을 명시적으로 덧붙이는 현상을 12개 언어쌍의 2,900개 문장쌍으로 연구한다. 개체 설명, 단위 변환, 번역자 설명 등의 사례를 다룬다.
- 이번 연구의 함의: 명시화는 언제나 오류인 것은 아니다. 의도를 전달하는 자연스러운 표현과, 원문에 없는 거절·약속을 확정하는 추가를 구별해 주석해야 한다.
- 다음 확인: 명시화 분류, 화행 관련 범위, 추가 오류와 허용 명시화의 경계.

## 본 실험 전 보완할 조사

- speech act preservation / illocutionary force / implicature preservation in machine translation
- contextual and contrastive evaluation / ambiguity preservation / over-explicitation
- Korean–English refusal / request / pragmatic transfer
- receiver interpretation / functional equivalence / translator intervention

번역학·화용론의 거절 및 요청 연구와 최근 MT 평가 논문을 추가 검토한다. 대조 그룹 구성과 수신자 해석 평가가 기존 자료와 어떻게 겹치는지 확인한 후 논문 기여 문장을 확정한다.
