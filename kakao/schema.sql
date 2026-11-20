-- 어음(Eoeum) 페르소나 재현 연구 — 공통 저장 구조 (SQLite)
--
-- 계층: participant → chat → session → message / fragment
--       participant → d4_stats, survey_answer, generation → generation_fragment, score
--
-- 원칙
--  * 실명·원본 대화는 넣지 않는다. text 는 모두 익명화된 것(<본인>, <상대>, <인물1> …).
--  * 제외(include=0)된 세션은 메타데이터만 남기고 메시지는 넣지 않는다.
--  * D6(골드셋) 답변은 모델 입력에 쓰면 안 된다 → v_model_visible_answers 만 프롬프트 생성에 사용.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- 참여자 1명 (실명 없음. 번호↔실명 대응표는 DB 밖에 따로 보관)
CREATE TABLE IF NOT EXISTS participant (
    participant_id TEXT PRIMARY KEY,               -- P01
    note           TEXT
);

-- 1:1 채팅방 1개
CREATE TABLE IF NOT EXISTS chat (
    chat_key       TEXT PRIMARY KEY,               -- P01-C01
    participant_id TEXT NOT NULL REFERENCES participant(participant_id) ON DELETE CASCADE,
    chat_id        TEXT NOT NULL,                  -- C01
    relation       TEXT NOT NULL,                  -- 친구/선배/가족 …
    first_ts       TEXT,
    last_ts        TEXT,
    n_messages     INTEGER,
    UNIQUE (participant_id, chat_id)
);

-- 두 참여자가 같은 방을 제출한 경우 (교차 비교 시 표시)
CREATE TABLE IF NOT EXISTS shared_room (
    chat_key_a  TEXT NOT NULL REFERENCES chat(chat_key) ON DELETE CASCADE,
    chat_key_b  TEXT NOT NULL REFERENCES chat(chat_key) ON DELETE CASCADE,
    common_msgs INTEGER,
    overlap     REAL,
    PRIMARY KEY (chat_key_a, chat_key_b)
);

-- 연속 대화 구간 (80/20 분리 단위)
CREATE TABLE IF NOT EXISTS session (
    session_id   TEXT PRIMARY KEY,                 -- P01-C01-S202405021512
    chat_key     TEXT NOT NULL REFERENCES chat(chat_key) ON DELETE CASCADE,
    start_ts     TEXT NOT NULL,
    end_ts       TEXT NOT NULL,
    n_msgs       INTEGER,
    n_self_msgs  INTEGER,
    flags        TEXT,                             -- 민감:건강;제3자 …
    include      INTEGER NOT NULL CHECK (include IN (0, 1)),
    split        TEXT CHECK (split IN ('train', 'holdout'))   -- 제외 세션은 NULL
);

-- 메시지 1개 (전송 1회). 포함된 세션만.
CREATE TABLE IF NOT EXISTS message (
    message_id INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES session(session_id) ON DELETE CASCADE,
    seq        INTEGER NOT NULL,                   -- 세션 안 순서
    ts         TEXT NOT NULL,
    role       TEXT NOT NULL CHECK (role IN ('self', 'partner')),
    kind       TEXT NOT NULL CHECK (kind IN ('text', 'media', 'deleted')),
    text       TEXT NOT NULL,                      -- 익명화된 본문 (메시지 안 줄바꿈 유지)
    flags      TEXT,
    UNIQUE (session_id, seq)
);

-- 대화 조각 = 상대 턴 + 본인 턴 (블록4 / D5 검색 후보)
CREATE TABLE IF NOT EXISTS fragment (
    fragment_id        TEXT PRIMARY KEY,           -- P01-C01-S202405021512-F01
    session_id         TEXT NOT NULL REFERENCES session(session_id) ON DELETE CASCADE,
    relation           TEXT NOT NULL,
    split              TEXT NOT NULL CHECK (split IN ('train', 'holdout')),
    start_ts           TEXT,
    n_partner_msgs     INTEGER,
    n_self_msgs        INTEGER,
    self_chars         INTEGER,
    total_chars        INTEGER,                    -- ②-2 (D3 글자 수만큼 대화 빼기) 계산용
    retrieval_eligible INTEGER NOT NULL CHECK (retrieval_eligible IN (0, 1)),
    exclude_reason     TEXT,
    flags              TEXT,
    partner_text       TEXT,
    self_text          TEXT,
    block4_text        TEXT NOT NULL,              -- 프롬프트에 그대로 넣는 형태
    embed_text         TEXT NOT NULL               -- 임베딩 대상
);

-- 조각 임베딩 (검색 단계에서 채움)
CREATE TABLE IF NOT EXISTS fragment_embedding (
    fragment_id TEXT NOT NULL REFERENCES fragment(fragment_id) ON DELETE CASCADE,
    model       TEXT NOT NULL,                     -- 임베딩 모델 이름·버전
    dim         INTEGER NOT NULL,
    vector      BLOB NOT NULL,                     -- float32 little-endian
    PRIMARY KEY (fragment_id, model)
);

-- D4 언어 통계 + 블록3 문장. split=train → 입력용, holdout → P2 기준
CREATE TABLE IF NOT EXISTS d4_stats (
    participant_id TEXT NOT NULL REFERENCES participant(participant_id) ON DELETE CASCADE,
    split          TEXT NOT NULL CHECK (split IN ('train', 'holdout')),
    relation       TEXT NOT NULL,                  -- '전체' 또는 관계
    stats_json     TEXT NOT NULL,
    block3_text    TEXT,
    avg_line_len   REAL,
    polite_ratio   REAL,
    frequent_endings TEXT,                         -- ; 로 구분
    PRIMARY KEY (participant_id, split, relation)
);

-- 설문 문항 (D1~D3, D4 짧은 응답 과제, D6 골드셋). 모든 참여자에게 동일
CREATE TABLE IF NOT EXISTS survey_item (
    item_id          TEXT PRIMARY KEY,             -- D3-01, D6-04 …
    kind             TEXT NOT NULL CHECK (kind IN ('D1', 'D2', 'D3', 'D3_rank', 'D4_task', 'D6')),
    domain           TEXT,                         -- 관계/책임/공정성/자유 …
    asked_by         TEXT,                         -- 질문하는 상대 관계 (친구/선배 …)
    question         TEXT NOT NULL
);

-- 설문 답변 (구글폼 등)
CREATE TABLE IF NOT EXISTS survey_answer (
    participant_id TEXT NOT NULL REFERENCES participant(participant_id) ON DELETE CASCADE,
    item_id        TEXT NOT NULL REFERENCES survey_item(item_id),
    answer         TEXT NOT NULL,
    submitted_at   TEXT,
    PRIMARY KEY (participant_id, item_id)
);

-- 생성 답변 1개 = 참여자 × 조건 × 질문 × 회차
CREATE TABLE IF NOT EXISTS generation (
    gen_id         INTEGER PRIMARY KEY,
    participant_id TEXT NOT NULL REFERENCES participant(participant_id) ON DELETE CASCADE,
    condition      TEXT NOT NULL,                  -- 0, A, B, C, D, E, II-1, II-2
    item_id        TEXT NOT NULL REFERENCES survey_item(item_id),   -- D6 문항
    run_no         INTEGER NOT NULL DEFAULT 1,
    model          TEXT NOT NULL,                  -- 모델 이름 + 버전 고정
    temperature    REAL,
    split_seed     INTEGER,
    prompt_text    TEXT NOT NULL,
    answer_text    TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    human_scored   INTEGER NOT NULL DEFAULT 0 CHECK (human_scored IN (0, 1)),  -- 3회 중 사람 채점 대상
    UNIQUE (participant_id, condition, item_id, run_no, model)
);

-- 생성에 넣은 조각 (블록4) → P4 원문 중복 계산
CREATE TABLE IF NOT EXISTS generation_fragment (
    gen_id      INTEGER NOT NULL REFERENCES generation(gen_id) ON DELETE CASCADE,
    fragment_id TEXT NOT NULL REFERENCES fragment(fragment_id),
    rank        INTEGER NOT NULL,
    similarity  REAL,
    PRIMARY KEY (gen_id, fragment_id)
);

-- 채점 결과
CREATE TABLE IF NOT EXISTS score (
    gen_id    INTEGER NOT NULL REFERENCES generation(gen_id) ON DELETE CASCADE,
    metric    TEXT NOT NULL,   -- P1, P2_len, P2_ending, P2_style, P3_stance, P3_reason, P3_context, P4_max_overlap, P4_copy, P5
    value     REAL,            -- 수치. P3 은 일치=1, 부분=0.5, 불일치=0, 판단불가=NULL
    label     TEXT,            -- 일치/부분 일치/불일치/판단 불가, D3/D5 출처 등
    scorer    TEXT NOT NULL,   -- auto / rater1 / rater2 / self
    note      TEXT,
    PRIMARY KEY (gen_id, metric, scorer)
);

CREATE INDEX IF NOT EXISTS idx_session_chat   ON session(chat_key);
CREATE INDEX IF NOT EXISTS idx_message_sess   ON message(session_id, seq);
CREATE INDEX IF NOT EXISTS idx_fragment_sess  ON fragment(session_id);
CREATE INDEX IF NOT EXISTS idx_fragment_pool  ON fragment(retrieval_eligible, split);
CREATE INDEX IF NOT EXISTS idx_generation_key ON generation(participant_id, condition, item_id);

-- 검색 후보 (D5): 입력용 80% 중 조건을 만족한 조각
CREATE VIEW IF NOT EXISTS v_retrieval_pool AS
SELECT f.*, c.participant_id
FROM fragment f
JOIN session s ON s.session_id = f.session_id
JOIN chat c    ON c.chat_key   = s.chat_key
WHERE f.retrieval_eligible = 1 AND f.split = 'train';

-- 모델 입력에 써도 되는 설문 답변 (D6 골드셋 제외)
CREATE VIEW IF NOT EXISTS v_model_visible_answers AS
SELECT a.* FROM survey_answer a JOIN survey_item i ON i.item_id = a.item_id
WHERE i.kind <> 'D6';

-- 조건별 평균 (최종 결과 ①)
CREATE VIEW IF NOT EXISTS v_condition_means AS
SELECT g.condition, s.metric, COUNT(*) AS n, AVG(s.value) AS mean
FROM generation g JOIN score s ON s.gen_id = g.gen_id
GROUP BY g.condition, s.metric;

-- 참여자별 데이터 현황
CREATE VIEW IF NOT EXISTS v_participant_summary AS
SELECT p.participant_id,
       (SELECT COUNT(*) FROM chat c WHERE c.participant_id = p.participant_id) AS chats,
       (SELECT COUNT(*) FROM session s JOIN chat c ON c.chat_key = s.chat_key
         WHERE c.participant_id = p.participant_id AND s.include = 1) AS sessions_included,
       (SELECT COUNT(*) FROM v_retrieval_pool r WHERE r.participant_id = p.participant_id) AS retrieval_pool
FROM participant p;
