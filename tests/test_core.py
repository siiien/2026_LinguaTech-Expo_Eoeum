import io
import json
import unittest
from unittest.mock import patch

import app
import pragmatics as p
from scripts.prepare_experiment import load_cases


class CoreTests(unittest.TestCase):
    def test_both_datasets_and_directions(self):
        for direction, path in p.DATASETS.items():
            cases, groups = load_cases(path)
            self.assertEqual((len(cases), len(groups)), (24, 8))
            for case in cases:
                self.assertEqual("-".join(p.language_pair(case)), direction)

    def test_gold_never_enters_model_input(self):
        for direction in p.DATASETS:
            for case in p.read_dataset(direction):
                for method in p.METHODS:
                    messages = p.build_messages(case, method)
                    payload = json.loads(messages[1]["content"])
                    allowed = {"source_language", "target_language", "target_audience", "utterance_to_translate"}
                    if method != "B0":
                        allowed |= {"preceding_dialogue", "relationship"}
                    self.assertEqual(set(payload), allowed)
                    self.assertNotIn("annotation", payload)
                    self.assertNotIn("candidate_translations", payload)
                    self.assertIn(p.LANGUAGES[p.language_pair(case)[1]], messages[0]["content"])
                    self.assertNotIn("{target_language_name}", messages[0]["content"])

    def test_shared_korean_default_policy(self):
        case = p.read_dataset("en-ko")[0]
        for method in p.METHODS:
            self.assertIn("Shared Korean output policy", p.build_messages(case, method)[0]["content"])
        self.assertNotIn("Shared Korean output policy", p.build_messages(p.read_dataset("ko-en")[0], "B1")[0]["content"])

    def test_live_input_validation_and_no_gold(self):
        original = app.CASES["en01_a"]
        case = app.validate_case({**app.public_case(original), "annotation": {"possible_acts": ["refusal"]}})
        self.assertNotIn("annotation", case)
        with self.assertRaises(ValueError):
            app.validate_case({**case, "source_text": " "})
        with self.assertRaises(ValueError):
            app.validate_case({**case, "source_language": "fr"})
        with self.assertRaises(ValueError):
            app.validate_case({**case, "context": [{"speaker": "unknown", "text": "x"}]})

    def test_extracts_message_after_reasoning_and_rejects_partial(self):
        response = {"status": "completed", "output": [
            {"type": "reasoning", "summary": []},
            {"type": "message", "content": [{"type": "output_text", "text": "괜찮아요."}]}]}
        self.assertEqual(p.extract_text(response), "괜찮아요.")
        with self.assertRaises(p.TranslationError):
            p.extract_text({**response, "status": "incomplete"})
        with self.assertRaises(p.TranslationError):
            p.extract_text({"status": "completed", "output": []})
        with self.assertRaises(p.TranslationError):
            p.extract_text({"status": "completed", "output": [{"type": "message", "content": [{"type": "refusal"}]}]})

    def test_model_must_be_explicit_no_network_on_missing_config(self):
        with patch.dict("os.environ", {}, clear=True), patch("urllib.request.build_opener") as opener:
            with self.assertRaises(p.TranslationError):
                p.translate(app.CASES["en01_a"], "P1")
            opener.assert_not_called()

    def test_api_body_and_usage_with_mock_transport(self):
        response = {"id": "test-response", "status": "completed", "model": "test-model-version",
                    "usage": {"input_tokens": 50, "output_tokens": 5},
                    "output": [{"type": "message", "content": [{"type": "output_text", "text": "괜찮아요."}]}]}
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key-only", "PRAGMATICS_MODEL": "test-model"}, clear=True):
            with patch("urllib.request.build_opener") as factory:
                factory.return_value.open.return_value = io.BytesIO(json.dumps(response).encode())
                result = p.translate(app.CASES["en01_a"], "P1")
                request = factory.return_value.open.call_args.args[0]
                body = json.loads(request.data)
                self.assertFalse(body["store"])
                self.assertEqual(body["model"], "test-model")
                self.assertNotIn("annotation", json.loads(body["input"]))
                self.assertEqual(result["model_returned"], "test-model-version")
                self.assertEqual(result["translation"], "괜찮아요.")
                self.assertNotIn("test-key-only", json.dumps(result))

    def test_invalid_api_url_rejected_before_network(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key", "PRAGMATICS_MODEL": "test-model",
                                      "OPENAI_BASE_URL": "http://example.test/v1"}, clear=True):
            with patch("urllib.request.build_opener") as opener:
                with self.assertRaises(p.TranslationError):
                    p.translate(app.CASES["en01_a"], "P1")
                opener.assert_not_called()


if __name__ == "__main__":
    unittest.main()
