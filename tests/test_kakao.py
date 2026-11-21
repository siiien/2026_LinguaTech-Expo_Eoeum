import csv
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "kakao"
sys.path.insert(0, str(ROOT))

from kakao_anonymize import Anonymizer  # noqa: E402
from kakao_features import extract_ending, honorific, style_match, style_stats  # noqa: E402
from kakao_parser import parse_text  # noqa: E402
import kakao_pipeline  # noqa: E402


class ParserTest(unittest.TestCase):
    def test_pc_multiline_and_ampm(self):
        c = parse_text(
            "홍길동 님과 카카오톡 대화\n저장한 날짜 : 2024-05-01 12:00:00\n\n"
            "--------------- 2024년 5월 1일 수요일 ---------------\n"
            "[홍길동] [오전 12:05] 첫 줄\n둘째 줄\n\n"
            "[나나] [오후 12:30] 응\n")
        self.assertEqual(c.fmt, "pc")
        self.assertEqual(c.title, "홍길동")
        self.assertEqual(len(c.messages), 2)
        self.assertEqual(c.messages[0].text, "첫 줄\n둘째 줄")
        self.assertEqual(c.messages[0].dt.hour, 0)
        self.assertEqual(c.messages[1].dt.hour, 12)

    def test_android_and_ios(self):
        android = ("홍길동 님과 카카오톡 대화\n저장한 날짜 : 2024. 5. 1. 오후 1:00\n\n2024년 5월 1일 수요일\n"
                   "2024년 5월 1일 오후 3:12, 홍길동 : 안녕\n2024년 5월 1일 오후 3:13, 나나 : 웅 : 왜\n")
        ios = "2024년 5월 1일 수요일\n2024. 5. 1. 오후 3:12, 홍길동 : 안녕\n여러 줄\n2024. 5. 1. 15:13, 나나 : ㅇㅇ\n"
        a = parse_text(android)
        self.assertEqual((a.fmt, len(a.messages)), ("mobile", 2))
        self.assertEqual(a.messages[1].text, "웅 : 왜")
        i = parse_text(ios)
        self.assertEqual(i.messages[0].text, "안녕\n여러 줄")
        self.assertEqual(i.messages[1].dt.hour, 15)

    def test_mac_csv(self):
        c = parse_text('Date,User,Message\n2024-05-01 15:12:00,"홍길동","안녕\n하세요"\n')
        self.assertEqual(c.fmt, "mac_csv")
        self.assertEqual(c.messages[0].text, "안녕\n하세요")


class AnonymizeTest(unittest.TestCase):
    def setUp(self):
        self.a = Anonymizer(["박서연"], ["김민지"], {"지수": "<인물1>"}, ["이문동"])

    def test_pii(self):
        t = self.a.process("서연아 010-1234-5678 a@b.com https://x.y/z 2024-05-01 이문동 지수언니").text
        self.assertEqual(t, "<본인>아 <전화번호> <이메일> <링크> 2024-05-01 <장소> <인물1>언니")
        self.assertIn("<계좌번호>", self.a.process("110-123-456789 로 보내").text)
        self.assertIn("<주소>", self.a.process("서울시 동대문구 이문로 107 앞").text)
        self.assertNotIn("<주소>", self.a.process("버스로 20분 걸려").text)

    def test_media(self):
        self.assertEqual(self.a.process("사진 3장").text, "<사진>")
        self.assertEqual(self.a.process("이모티콘").text, "<이모티콘>")
        self.assertEqual(self.a.process("삭제된 메시지입니다.").kind, "deleted")
        self.assertEqual(self.a.process("사진 찍자").kind, "text")

    def test_flags(self):
        self.assertIn("민감:건강", self.a.process("요즘 병원 다녀").flags)
        self.assertIn("제3자", self.a.process("철수오빠가 그러던데").flags)
        self.assertEqual(self.a.process("근데 언니라 말하기 좀 그래").flags, [])


class FeatureTest(unittest.TestCase):
    def test_endings(self):
        cases = {"이번엔 말해야 할 듯": "듯", "피곤했거든 ㅋㅋ": "거든", "그냥 집에 있음": "음",
                 "내가 말할게": "ㄹ게", "오늘 밤에 올려둘게요!": "ㄹ게요", "감사합니다": "ㅂ니다",
                 "불공평하잖아 ㅠㅠ": "잖아", "ㅇㅇ": None, "넵": "넵"}
        for line, want in cases.items():
            self.assertEqual(extract_ending(line), want, line)

    def test_honorific(self):
        self.assertEqual(honorific("괜찮아요"), "존댓말")
        self.assertEqual(honorific("넵"), "존댓말")
        self.assertEqual(honorific("하니까"), "반말")
        self.assertEqual(honorific("그럴 듯"), "반말")
        self.assertIsNone(honorific("ㅋㅋㅋ"))

    def test_stats_and_match(self):
        st = style_stats([["아 그건 좀 아닌 듯", "따로 말할 듯"], ["걔만 손해거든"], ["<이모티콘>"]])
        self.assertEqual(st["n_messages"], 4)
        self.assertEqual(st["n_text_lines"], 3)
        self.assertIn("듯", st["endings"]["frequent"])
        self.assertAlmostEqual(st["expressive"]["sticker_per_message"], 0.25)
        r = style_match("일단 하지 말라고 할 듯\n불공평하잖아", st)
        self.assertTrue(r["ending_ok"])


class PipelineTest(unittest.TestCase):
    def test_same_minute_repeats_kept(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            chat = ("홍길동 님과 카카오톡 대화\n\n2024. 5. 1. 오후 3:12, 홍길동 : 뭐해\n"
                    "2024. 5. 1. 오후 3:12, 나나 : ㅋㅋ\n2024. 5. 1. 오후 3:12, 나나 : ㅋㅋ\n")
            (tmp / "a.txt").write_text(chat, encoding="utf-8")
            (tmp / "b.txt").write_text(chat, encoding="utf-8")  # 같은 방을 두 번 내보냄
            (tmp / "m.csv").write_text("participant_id,chat_id,file,relation\nP01,C01,a.txt,친구\nP01,C01,b.txt,친구\n",
                                       encoding="utf-8")
            kakao_pipeline.main(["prepare", "--manifest", str(tmp / "m.csv"), "--out", str(tmp / "out")])
            msgs = kakao_pipeline.read_jsonl(tmp / "out" / "work" / "P01" / "C01.messages.jsonl")
            self.assertEqual([m["text"] for m in msgs], ["뭐해", "ㅋㅋ", "ㅋㅋ"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_end_to_end_samples(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            out = tmp / "out"
            rc = kakao_pipeline.main(["prepare", "--manifest", str(ROOT / "samples" / "manifest.csv"), "--out", str(out)])
            self.assertEqual(rc, 0)
            # 검토 결정 유지 확인: 한 세션을 0 으로 바꾼 뒤 다시 prepare
            rv = out / "review" / "P01_sessions.csv"
            rows = kakao_pipeline.read_csv_any(rv)
            rows[0]["include"] = "0"
            with rv.open("w", encoding="utf-8-sig", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0]))
                w.writeheader()
                w.writerows(rows)
            kakao_pipeline.main(["prepare", "--manifest", str(ROOT / "samples" / "manifest.csv"), "--out", str(out)])
            rows2 = kakao_pipeline.read_csv_any(rv)
            self.assertEqual(rows2[0]["include"], "0")

            self.assertEqual(kakao_pipeline.main(["build", "--out", str(out)]), 0)
            frags = kakao_pipeline.read_jsonl(out / "P01" / "fragments.jsonl")
            self.assertTrue(all(not f["session_id"] == rows[0]["session_id"] for f in frags))
            pool = kakao_pipeline.read_jsonl(out / "P01" / "retrieval_pool.jsonl")
            ids = {f["fragment_id"] for f in frags if f["split"] == "holdout"}
            self.assertTrue(ids)
            self.assertFalse(ids & {p["fragment_id"] for p in pool})  # holdout 은 검색 후보에서 제외

            # 원문 이름·전화번호가 결과물에 남지 않아야 함
            blob = "".join(p.read_text(encoding="utf-8-sig") for p in out.rglob("*") if p.is_file()
                           and p.suffix in (".csv", ".jsonl", ".json", ".txt", ".md") and p.name != "prepare_report.md")
            for raw in ("박서연", "김민지", "정우진", "010-2345-6789", "이문로 107", "youtube"):
                self.assertNotIn(raw, blob, raw)
            shared = kakao_pipeline.read_csv_any(out / "shared_rooms.csv")
            self.assertEqual(len(shared), 1)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class DbTest(unittest.TestCase):
    def test_sample_db(self):
        import sqlite3
        import kakao_db
        tmp = Path(tempfile.mkdtemp())
        try:
            db = tmp / "s.db"
            c = kakao_db.build_sample(db)
            self.assertEqual(c["participant"], 2)
            self.assertEqual(c["chat"], 3)
            self.assertGreater(c["retrieval_pool"], 0)
            con = sqlite3.connect(str(db))
            # 제외 세션은 메시지 없음, holdout 조각은 검색 후보 아님
            self.assertEqual(con.execute("SELECT COUNT(*) FROM message m JOIN session s USING(session_id) "
                                         "WHERE s.include = 0").fetchone()[0], 0)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM v_retrieval_pool WHERE split = 'holdout'").fetchone()[0], 0)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM shared_room").fetchone()[0], 1)
            # 원문 이름이 DB에 없어야 함
            blob = "\n".join(r[0] for r in con.execute("SELECT text FROM message UNION ALL SELECT block4_text FROM fragment"))
            for raw in ("박서연", "김민지", "010-2345-6789"):
                self.assertNotIn(raw, blob)
            con.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class StaleOutputTest(unittest.TestCase):
    """manifest 를 바꾸거나 build 를 건너뛰고 DB 에 저장하는 경우."""

    def test_db_requires_fresh_build_and_old_participants_are_cleared(self):
        import os
        import kakao_db
        tmp = Path(tempfile.mkdtemp())
        try:
            out, db = tmp / "out", tmp / "r.db"
            m_all = ROOT / "samples" / "manifest.csv"
            kakao_pipeline.main(["prepare", "--manifest", str(m_all), "--out", str(out)])
            with self.assertRaises(SystemExit) as e:  # build 전에 저장
                kakao_db.load(out, db)
            self.assertIn("실행", str(e.exception))
            kakao_pipeline.main(["build", "--out", str(out)])
            kakao_db.load(out, db)

            # 검토를 다시 저장하면 build 가 옛날 것 → 저장 거부
            rv = out / "review" / "P01_sessions.csv"
            later = (out / "summary.csv").stat().st_mtime + 5
            os.utime(rv, (later, later))
            with self.assertRaises(SystemExit) as e:
                kakao_db.load(out, db)
            self.assertIn("P01", str(e.exception))

            # manifest 를 P02 만 남기고 다시 prepare → build: P01 작업·결과 폴더가 정리되어야 함
            rows = [r for r in kakao_pipeline.read_csv_any(m_all) if r["participant_id"] == "P02"]
            for r in rows:
                r["file"] = str(ROOT / "samples" / r["file"])
            m2 = tmp / "m2.csv"
            with m2.open("w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0]))
                w.writeheader()
                w.writerows(rows)
            kakao_pipeline.main(["prepare", "--manifest", str(m2), "--out", str(out)])
            self.assertFalse((out / "work" / "P01").exists())
            self.assertTrue(rv.exists())  # 검토 결정은 남김
            kakao_pipeline.main(["build", "--out", str(out)])
            self.assertFalse((out / "P01").exists())
            c = kakao_db.load(out, tmp / "r2.db")
            self.assertEqual(c["participant"], 1)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class DbMergeTest(unittest.TestCase):
    """한 사람씩 따로 만든 DB 를 합치면 한꺼번에 만든 DB 와 같아야 한다."""

    @classmethod
    def setUpClass(cls):
        import kakao_db
        cls.kdb = kakao_db
        cls.tmp = Path(tempfile.mkdtemp())
        cls.full = cls.tmp / "full.db"
        cls.full_counts = kakao_db.build_sample(cls.full)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _only(self, keep, name=None):
        import sqlite3
        p = self.tmp / (name or f"{keep}.db")
        shutil.copy(self.full, p)
        con = sqlite3.connect(str(p))
        con.execute("PRAGMA foreign_keys = ON")
        con.execute("DELETE FROM participant WHERE participant_id <> ?", (keep,))
        con.commit()
        con.close()
        return p

    def _gen(self, db, pid, gen_id, value, item_q="질문"):
        import sqlite3
        con = sqlite3.connect(str(db))
        con.execute("PRAGMA foreign_keys = ON")
        con.execute("INSERT OR IGNORE INTO survey_item VALUES ('D6-01','D6','관계','친구',?)", (item_q,))
        fid = con.execute("SELECT fragment_id FROM v_retrieval_pool LIMIT 1").fetchone()[0]
        con.execute("INSERT INTO generation(gen_id, participant_id, condition, item_id, model, prompt_text, "
                    "answer_text, created_at) VALUES (?,?,'D','D6-01','m','p','a','t')", (gen_id, pid))
        con.execute("INSERT INTO generation_fragment VALUES (?,?,1,0.9)", (gen_id, fid))
        con.execute("INSERT INTO score VALUES (?,'P1',?,NULL,'auto',NULL)", (gen_id, value))
        con.commit()
        con.close()

    def test_merge_equals_full_and_remaps_ids(self):
        import sqlite3
        a, b = self._only("P01"), self._only("P02")
        self._gen(a, "P01", 1, 3)
        self._gen(b, "P02", 1, 4)  # 두 DB 모두 gen_id=1 → 합치면 번호가 새로 매겨져야 함
        out = self.tmp / "merged.db"
        r = self.kdb.merge([a, b], out)
        self.assertEqual(r["added"], ["P01", "P02"])
        self.assertEqual(r["shared_rooms"], 1)  # 따로 넣어도 공유 채팅방을 다시 찾음
        for k in ("participant", "chat", "session", "message", "fragment", "d4_stats", "retrieval_pool"):
            self.assertEqual(r["counts"][k], self.full_counts[k], k)
        con = sqlite3.connect(str(out))
        rows = con.execute("SELECT g.participant_id, s.value, (SELECT COUNT(*) FROM generation_fragment f "
                           "WHERE f.gen_id = g.gen_id) FROM generation g JOIN score s USING (gen_id) "
                           "ORDER BY 1").fetchall()
        self.assertEqual(rows, [("P01", 3.0, 1), ("P02", 4.0, 1)])
        con.close()

    def test_conflicts(self):
        a = self._only("P01", "c_P01.db")
        out = self.tmp / "conflict.db"
        self.kdb.merge([a], out)
        with self.assertRaises(SystemExit):  # 같은 참여자 → 멈춤
            self.kdb.merge([a], out)
        a2 = self._only("P01", "c_P01_again.db")
        r = self.kdb.merge([a2], out, replace=True)
        self.assertEqual(r["replaced"], ["P01"])
        self.assertEqual(r["counts"]["participant"], 1)
        with self.assertRaises(SystemExit):  # 두 DB 에 같은 참여자
            self.kdb.merge([self._only("P02", "x1.db"), self._only("P02", "x2.db")], self.tmp / "dup.db")
        x, y = self._only("P01", "q1.db"), self._only("P02", "q2.db")
        self._gen(x, "P01", 1, 3, "질문")
        self._gen(y, "P02", 1, 3, "다른 질문")
        with self.assertRaises(SystemExit):  # 같은 문항 ID, 다른 내용
            self.kdb.merge([x, y], self.tmp / "items.db")
        import sqlite3
        con = sqlite3.connect(str(self.tmp / "items.db"))  # 미리 검사에서 걸리면 아무것도 들어가지 않음
        self.assertEqual(con.execute("SELECT COUNT(*) FROM participant").fetchone()[0], 0)
        con.close()

    def test_not_a_db(self):
        p = self.tmp / "junk.db"
        p.write_bytes(b"not a database" * 50)
        with self.assertRaises(SystemExit):
            self.kdb.merge([p], self.tmp / "j.db")


if __name__ == "__main__":
    unittest.main()
