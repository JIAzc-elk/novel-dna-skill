import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fact_lock = load_module("fact_lock", ROOT / "scripts/fact_lock.py")
cna = load_module("check_novel_ai", ROOT / "scripts/check_novel_ai.py")
EXAMPLE = json.loads((ROOT / "templates/fact-lock.example.json").read_text(encoding="utf-8"))


class FactLockTests(unittest.TestCase):
    def test_explicit_facts_and_event_sequence_pass(self):
        text = (
            "陈立翻开旧账册，发现账册少了三两银子。"
            "周管事将月钱定为三两。两人签好雇工契约。"
        )
        report = fact_lock.check_fact_lock(text, EXAMPLE)

        self.assertEqual(report["status"], "PASS", report["blocking"])
        self.assertEqual([item["status"] for item in report["facts"]], ["pass", "pass"])
        self.assertEqual([item["status"] for item in report["order"]], ["pass", "pass"])

    def test_missing_fact_and_event_are_reported(self):
        report = fact_lock.check_fact_lock(
            "陈立翻开旧账册。周管事将月钱定为三两。",
            EXAMPLE,
        )

        self.assertEqual(report["status"], "FAIL")
        self.assertIn("fact-lock-missing (ledger-shortage)", " ".join(report["blocking"]))
        self.assertIn("fact-lock-event-missing (find-shortage)", " ".join(report["blocking"]))
        self.assertEqual(report["order"][0]["status"], "skipped_missing_event")

    def test_conflicting_amount_is_reported(self):
        report = fact_lock.check_fact_lock(
            "周管事每月给他五两银子。",
            EXAMPLE,
        )

        self.assertIn("fact-lock-conflict (monthly-pay)", " ".join(report["blocking"]))
        self.assertIn("fact-lock-missing (monthly-pay)", " ".join(report["blocking"]))

    def test_reversed_first_event_markers_are_reported(self):
        text = (
            "两人签好雇工契约后，陈立才翻开旧账册，"
            "发现账册少了三两银子。周管事将月钱定为三两。"
        )
        report = fact_lock.check_fact_lock(text, EXAMPLE)

        self.assertEqual(report["order"][0]["status"], "pass")
        self.assertEqual(report["order"][1]["status"], "violated")
        self.assertIn("fact-lock-order", " ".join(report["blocking"]))

    def test_invalid_schema_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            fact_lock.validate_fact_lock({"version": 1, "fact": []})

        duplicate = {
            "version": 1,
            "facts": [{"id": "same", "claim": "甲", "match_any": ["甲"]}],
            "events": [{"id": "same", "claim": "乙", "match_any": ["乙"]}],
        }
        with self.assertRaisesRegex(ValueError, "duplicate id"):
            fact_lock.validate_fact_lock(duplicate)

    def test_cli_combines_fact_lock_with_existing_rewrite_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.md"
            draft = root / "draft.md"
            ledger = root / "fact-lock.json"
            report_path = root / "report.json"
            source.write_text("陈立接到账房工作，账簿里有几笔旧账待查。", encoding="utf-8")
            draft.write_text(
                "陈立翻开旧账册，发现账册少了三两银子。"
                "周管事将月钱定为三两。两人签好雇工契约。",
                encoding="utf-8",
            )
            ledger.write_text(json.dumps(EXAMPLE, ensure_ascii=False), encoding="utf-8")
            output = io.StringIO()

            with contextlib.redirect_stdout(output):
                code = cna.main([
                    str(draft), "--source", str(source), "--fact-lock", str(ledger),
                    "--narrative-check", "--output", str(report_path),
                ])

            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(code, 0, report["blocking"])
            self.assertEqual(report["status"], "PASS")
            self.assertEqual(report["fact_lock"]["status"], "PASS")
            self.assertIn("FACT_LOCK=PASS", output.getvalue())

    def test_cli_returns_error_for_malformed_fact_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            draft = root / "draft.md"
            ledger = root / "fact-lock.json"
            report_path = root / "report.json"
            draft.write_text("陈立推开门。", encoding="utf-8")
            ledger.write_text('{"version": 1, "facts": [{"id": "x"}]}', encoding="utf-8")

            with contextlib.redirect_stdout(io.StringIO()):
                code = cna.main([
                    str(draft), "--fact-lock", str(ledger), "--output", str(report_path),
                ])

            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(code, 2)
            self.assertEqual(report["status"], "ERROR")

    def test_fact_lock_is_report_only_by_default_and_strict_when_requested(self):
        text = "周管事将月钱定为三两。"
        report = cna.scan_text(text, fact_lock_data=EXAMPLE)

        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["fact_lock"]["status"], "FAIL")
        self.assertTrue(any("fact-lock-warning" in w for w in report["warnings"]))

        strict = cna.scan_text(text, fact_lock_data=EXAMPLE, fact_lock_strict=True)
        self.assertEqual(strict["status"], "FAIL")
        self.assertTrue(any("fact-lock-missing" in b for b in strict["blocking"]))

class SampleEndToEndRegressionTests(unittest.TestCase):
    def test_existing_local_samples_keep_their_expected_cli_outcomes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lite_report = root / "lite.json"
            heavy_report = root / "heavy.json"
            with contextlib.redirect_stdout(io.StringIO()):
                lite_code = cna.main([
                    str(ROOT / "test_samples/ai_lite.md"), "--narrative-check",
                    "--output", str(lite_report),
                ])
                heavy_code = cna.main([
                    str(ROOT / "test_samples/ai_heavy.md"), "--narrative-check",
                    "--output", str(heavy_report),
                ])

            lite = json.loads(lite_report.read_text(encoding="utf-8"))
            heavy = json.loads(heavy_report.read_text(encoding="utf-8"))

        self.assertEqual(lite_code, 0, lite["blocking"])
        self.assertEqual(lite["status"], "PASS")
        self.assertEqual(lite["narrative_architecture"]["blocking"], [])
        self.assertEqual(heavy_code, 1)
        self.assertEqual(heavy["status"], "FAIL")
        heavy_kinds = {item.split(" ", 1)[0] for item in heavy["blocking"]}
        arch_kinds = {item.split(" ", 1)[0] for item in heavy["narrative_architecture"]["blocking"]}
        self.assertIn("telling-words", heavy_kinds)
        self.assertIn("omniscient-trailer-hook", heavy_kinds)
        self.assertIn("dialogue-preaching", arch_kinds)
        self.assertIn("emotion-labeling", arch_kinds)


if __name__ == "__main__":
    unittest.main()
