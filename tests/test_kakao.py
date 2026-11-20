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


if __name__ == "__main__":
    unittest.main()
