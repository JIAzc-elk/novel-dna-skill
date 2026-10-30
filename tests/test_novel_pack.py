import argparse
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts" if (ROOT / "scripts/novel_pack.py").exists() else ROOT / "novel-dna-skill/scripts"
spec = importlib.util.spec_from_file_location("novel_pack", SCRIPTS_DIR / "novel_pack.py")
np = importlib.util.module_from_spec(spec)
spec.loader.exec_module(np)
cna_spec = importlib.util.spec_from_file_location("check_novel_ai", SCRIPTS_DIR / "check_novel_ai.py")
cna = importlib.util.module_from_spec(cna_spec)
cna_spec.loader.exec_module(cna)
WORK = ROOT / ".work/test-tmp"


class PackTests(unittest.TestCase):
    def setUp(self):
        WORK.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=WORK)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "第一章.md"
        self.source.write_text('# 第一章\n\n“先把门关上。”\n他仍站着。\n', encoding="utf-8")
        self.pack = self.root / "写作包/测试包"

    def tearDown(self):
        # Only recursively clean this test's own verified temporary directory.
        self.assertTrue(self.root.is_relative_to(WORK.resolve()))
        self.temp.cleanup()

    def init(self, extra=()):
        return np.init_pack(argparse.Namespace(output=str(self.pack), name="测试包", alias=["测试别名"],
                            kind="writing", recursive=False, input=[str(self.source), *map(str, extra)]))

    def finish(self):
        s = np.read_rows(self.pack / "corpus.jsonl")[0]
        np.write_rows(self.pack / "analysis.jsonl", [{"source_id": s["id"], "sha256": s["sha256"],
            "observation": "先下达关门要求，另一方仍站着，用未完成的动作保留关系压力。",
            "rule_ids": ["R001"], "unresolved": []}])
        np.write_rows(self.pack / "rules.jsonl", [{"id":"R001", "dimension":"人物与对白",
            "claim":"对话先作用于眼前动作", "when":"要求尚未落实", "do":"用回应动作承接对白",
            "effect":"显出关系中的阻力", "avoid":"不能新增拒绝或改变动机", "strength":"local",
            "evidence":[{"source_id":s["id"],"start_line":3,"end_line":4,"quote":"他仍站着。"}],
            "counterevidence":[]}])
        np.write_rows(self.pack / "scenes.jsonl", [{"id":"S001", "summary":"关门要求尚未得到执行",
            "tags":["对白"],"spans":[{"source_id":s["id"],"start_line":3,"end_line":4}]}])
        coverage = np.read_json(self.pack / "coverage.json")
        for d in coverage["dimensions"]:
            if d["dimension"] == "人物与对白":
                d.update(status="partial", rule_ids=["R001"], note="只有一句命令及回应")
        np.write_json(self.pack / "coverage.json", coverage)
        np.atomic_text(self.pack / "指纹/对白.md", "# 对白\n\nR001 在已有动作中体现回应，不改动事实。\n")
        m = np.read_json(self.pack / "manifest.json")
        m.update(status="ready", scope="仅一份四行短片段", modules=[{"file":"指纹/对白.md","dimensions":["人物与对白"]}])
        np.write_json(self.pack / "manifest.json", m)

    def test_single_fragment_is_accepted_without_minimum(self):
        r = self.init()
        self.assertEqual(r["source_units"], 1)
        self.assertEqual(r["semantic_analysis"], "pending")
        self.assertEqual(np.read_rows(self.pack / "analysis.jsonl"), [])
        self.assertEqual(np.validate_pack(self.pack, True)["status"], "PASS")
        self.assertEqual(np.validate_pack(self.pack)["status"], "FAIL")

    def test_ready_pack_can_have_real_coverage_gaps(self):
        self.init()
        self.finish()
        self.assertEqual(np.validate_pack(self.pack)["errors"], [])

    def test_ready_requires_every_input_actually_analyzed(self):
        self.init()
        self.finish()
        np.write_rows(self.pack / "analysis.jsonl", [])
        self.assertIn("Unanalyzed inputs", " ".join(np.validate_pack(self.pack)["errors"]))

    def test_inexact_quote_is_rejected(self):
        self.init()
        self.finish()
        rules = np.read_rows(self.pack / "rules.jsonl")
        rules[0]["evidence"][0]["quote"] = "他已经坐下。"
        np.write_rows(self.pack / "rules.jsonl", rules)
        self.assertIn("not exact", " ".join(np.validate_pack(self.pack)["errors"]))

    def test_out_of_range_evidence_is_rejected(self):
        self.init()
        self.finish()
        rows = np.read_rows(self.pack / "scenes.jsonl")
        rows[0]["spans"][0]["end_line"] = 500
        np.write_rows(self.pack / "scenes.jsonl", rows)
        self.assertIn("line range", " ".join(np.validate_pack(self.pack)["errors"]))

    def test_changed_source_and_stale_analysis_are_detected(self):
        self.init()
        self.finish()
        s = np.read_rows(self.pack / "corpus.jsonl")[0]
        (self.pack / s["file"]).write_text("已改动。", encoding="utf-8")
        self.assertIn("hash mismatch", " ".join(np.validate_pack(self.pack)["errors"]))

    def test_analysis_hash_is_checked_independently(self):
        self.init()
        self.finish()
        rows = np.read_rows(self.pack / "analysis.jsonl")
        rows[0]["sha256"] = "0" * 64
        np.write_rows(self.pack / "analysis.jsonl", rows)
        self.assertIn("stale analysis", " ".join(np.validate_pack(self.pack)["errors"]))

    def test_relative_and_windows_absolute_escapes_rejected(self):
        for relative in ("../private.md", "C:/private.md", "/private.md", "corpus/../../x", "corpus\\x"):
            with self.subTest(relative=relative), self.assertRaises(ValueError):
                np.within(self.pack, relative)

    def test_init_never_overwrites(self):
        self.init()
        before = (self.pack / "manifest.json").read_bytes()
        with self.assertRaises(ValueError):
            self.init()
        self.assertEqual((self.pack / "manifest.json").read_bytes(), before)

    def test_explicit_input_file_is_not_dropped_for_its_name(self):
        self.source = self.root / "README.md"
        self.source.write_text("这是作者明确选中的小说片段。", encoding="utf-8")
        self.assertEqual(self.init()["source_units"], 1)

    def test_directory_metadata_is_excluded_from_implicit_collection(self):
        (self.root / "README.md").write_text("目录说明", encoding="utf-8")
        prepared = np.collect([str(self.root)], False, self.pack)
        self.assertEqual([p[0].name for p in prepared], ["第一章.md"])

    def test_all_inputs_prevalidated_before_creating_pack(self):
        empty = self.root / "空章.txt"
        empty.write_text(" ", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.init([empty])
        self.assertFalse(self.pack.exists())

    def test_duplicate_input_is_recorded_not_double_counted(self):
        duplicate = self.root / "副本.md"
        duplicate.write_bytes(self.source.read_bytes())
        r = self.init([duplicate])
        self.assertEqual(r["source_units"], 1)
        self.assertEqual(len(r["intake_notes"]), 1)

    def test_add_preserves_work_but_marks_draft(self):
        self.init()
        self.finish()
        old = (self.pack / "rules.jsonl").read_bytes()
        more = self.root / "第二章.txt"
        more.write_bytes("他关了门。".encode("gb18030"))
        r = np.add_sources(argparse.Namespace(pack=str(self.pack), input=[str(more)], recursive=False))
        self.assertEqual((r["added"], r["status"]), (1, "draft"))
        self.assertEqual((self.pack / "rules.jsonl").read_bytes(), old)
        self.assertEqual(np.read_json(self.pack / "manifest.json")["revision"], 2)
        self.assertEqual(np.validate_pack(self.pack, True)["status"], "PASS")
        self.assertEqual(np.validate_pack(self.pack)["status"], "FAIL")

    def test_adding_duplicate_does_not_invalidate_ready_pack(self):
        self.init()
        self.finish()
        r = np.add_sources(argparse.Namespace(pack=str(self.pack), input=[str(self.source)], recursive=False))
        self.assertEqual((r["added"], r["status"]), (0, "ready"))

    def test_alias_resolution_reports_ambiguity(self):
        self.init()
        self.assertEqual(np.find_packs(self.pack.parent, "测试别名")["status"], "found")
        second = self.pack.parent / "另一包"
        second.mkdir()
        np.write_json(second / "manifest.json", np.read_json(self.pack / "manifest.json"))
        self.assertEqual(np.find_packs(self.pack.parent, "测试别名")["status"], "ambiguous")
        self.assertEqual(np.find_packs(self.pack.parent, "不存在")["status"], "not_found")

    def test_metadata_cannot_claim_unobserved_rules(self):
        self.init()
        self.finish()
        c = np.read_json(self.pack / "coverage.json")
        next(x for x in c["dimensions"] if x["dimension"] == "人物与对白")["status"] = "unobserved"
        np.write_json(self.pack / "coverage.json", c)
        self.assertIn("unobserved dimension has rules", " ".join(np.validate_pack(self.pack)["errors"]))

    def test_unknown_rule_in_module_is_rejected(self):
        self.init()
        self.finish()
        np.atomic_text(self.pack / "指纹/对白.md", "参考 R999。")
        self.assertIn("unknown rule reference", " ".join(np.validate_pack(self.pack)["errors"]))

    def test_no_scene_is_required_for_unclassifiable_fragment(self):
        self.init()
        s = np.read_rows(self.pack / "corpus.jsonl")[0]
        np.write_rows(self.pack / "analysis.jsonl", [{"source_id":s["id"],"sha256":s["sha256"],
            "observation":"短片段不足以形成可迁移规则，仅保留原文供后续补充。","rule_ids":[],"unresolved":[]}])
        m = np.read_json(self.pack / "manifest.json")
        m["status"] = "ready"
        np.write_json(self.pack / "manifest.json", m)
        self.assertEqual(np.validate_pack(self.pack)["status"], "PASS")

    def test_linguistic_stats_include_dispersion_and_markers(self):
        self.source.write_text(
            "# 第一章\n\n不是他不想动，而是时机未到。\n\n他停下。\n\n"
            "倘若此时贸然冲出树林去和对方硬拼消耗，不仅先前所有的隐忍都会付诸东流，反而会落入圈套。\n",
            encoding="utf-8",
        )
        self.init()
        stats = np.read_json(self.pack / "stats.json")["aggregate"]
        self.assertGreater(stats["sentence_chars"]["sd"], 5.0)
        self.assertGreater(stats["sentence_chars"]["short_le_10_share"], 0.0)
        self.assertGreater(stats["sentence_chars"]["long_ge_40_share"], 0.0)
        self.assertIn("不是", stats["markers_per_10k_chars"])
        self.assertIn("而是", stats["markers_per_10k_chars"])
        self.assertIn("倘若", stats["markers_per_10k_chars"])

    def test_slice_retrieves_exact_lines_by_id_and_tag(self):
        self.init()
        self.finish()
        by_id = np.slice_scenes(self.pack, scene_ids=["S001"])
        self.assertEqual(by_id["count"], 1)
        self.assertIn("他仍站着。", by_id["scenes"][0]["excerpts"][0]["text"])
        by_tag = np.slice_scenes(self.pack, tag="对白", limit=2)
        self.assertEqual(by_tag["count"], 1)

    def test_resolve_auto_fills_hashes_and_locates_rule_and_scene_quotes(self):
        self.init()
        s = np.read_rows(self.pack / "corpus.jsonl")[0]
        np.write_rows(self.pack / "analysis.jsonl", [{
            "source_id": s["id"], "sha256": "",
            "observation": "自动补全哈希测试", "rule_ids": ["R001"], "unresolved": [],
        }])
        np.write_rows(self.pack / "rules.jsonl", [{
            "id": "R001", "dimension": "人物与对白",
            "claim": "对话先作用于眼前动作", "when": "要求尚未落实", "do": "用回应动作承接对白",
            "effect": "显出关系中的阻力", "avoid": "不能新增拒绝", "strength": "local",
            "evidence": [{"source_id": s["id"], "quote": '"先把门关上。"'}],
            "counterevidence": [],
        }])
        np.write_rows(self.pack / "scenes.jsonl", [{
            "id": "S001", "summary": "关门对峙", "tags": ["对白"],
            "spans": [{"source_id": s["id"], "start_quote": '"先把门关上。"', "end_quote": "他仍站着。"}],
        }])
        coverage = np.read_json(self.pack / "coverage.json")
        for d in coverage["dimensions"]:
            if d["dimension"] == "人物与对白":
                d.update(status="partial", rule_ids=["R001"], note="已覆盖")
        np.write_json(self.pack / "coverage.json", coverage)
        np.atomic_text(self.pack / "指纹/对白.md", "# 对白\n\nR001 示例。\n")
        m = np.read_json(self.pack / "manifest.json")
        m.update(status="ready", scope="测试", modules=[{"file": "指纹/对白.md", "dimensions": ["人物与对白"]}])
        np.write_json(self.pack / "manifest.json", m)

        res = np.resolve_pack(self.pack)
        self.assertEqual(res["status"], "PASS")
        self.assertEqual(res["updated_analysis_hashes"], 1)
        self.assertEqual(res["resolved_rule_quotes"], 1)
        self.assertEqual(res["resolved_scene_spans"], 1)
        self.assertEqual(np.validate_pack(self.pack)["status"], "PASS")

    def test_whole_chapter_span_on_long_source_emits_warning(self):
        long_lines = ["# 长章", "", "“先把门关上。”", "他仍站着。"] + [f"第{i}行动作描写。" for i in range(1, 58)]
        self.source.write_text("\n".join(long_lines) + "\n", encoding="utf-8")
        self.init()
        self.finish()
        s = np.read_rows(self.pack / "corpus.jsonl")[0]
        np.write_rows(self.pack / "scenes.jsonl", [{
            "id": "S001", "summary": "整章打包", "tags": ["对白"],
            "spans": [{"source_id": s["id"], "start_line": 3, "end_line": s["line_count"]}],
        }])
        res = np.validate_pack(self.pack)
        self.assertEqual(res["status"], "PASS")
        self.assertTrue(any("golden scene slicing" in w for w in res["warnings"]))

    def test_check_novel_ai_blocks_embodied_similes_and_allows_natural_prose(self):
        bad_mutated = "陈立赶紧低头，心口猛地蹿起阵针扎似的凉意。\n\n外头雨还在下，青石地被冲得发亮。"
        res_bad = cna.scan_text(bad_mutated)
        self.assertEqual(res_bad["status"], "FAIL")
        joined = " ".join(res_bad["blocking"])
        self.assertIn("embodied-simile", joined)
        self.assertIn("dropped-numeral", joined)

        good_human = (
            "陈立赶紧低头，心中猛地升起一股寒意。\n\n"
            "“隔着这么大的雨，这人竟能察觉到书房里的动静？”陈立垂手站在书案旁，心底念头飞快转开。"
        )
        res_good = cna.scan_text(good_human)
        self.assertEqual(res_good["status"], "PASS")
        self.assertEqual(res_good["blocking_count"], 0)

    def test_check_novel_ai_blocks_first_person_narration_and_allows_quoted_dialogue(self):
        bad_fp = (
            "我这一辈子，只算错过一笔账。\n\n"
            "我缩在茶棚外的石阶上，脚边放着一只破碗。我今年六十三岁，记不清日子了。\n"
        )
        res_fp = cna.scan_text(bad_fp)
        self.assertEqual(res_fp["status"], "FAIL")
        self.assertTrue(any("first-person-narration" in b for b in res_fp["blocking"]))

        good_tp = (
            "方明觉缩在茶棚外的石阶上，脚边放着一只豁口碗。\n\n"
            "“我今年六十三，还是六十四？”他低头看着碗底的三枚铜钱，在一场你死我活的规矩里，他早已习惯了沉默。\n"
        )
        res_tp = cna.scan_text(good_tp)
        self.assertEqual(res_tp["status"], "PASS")

    def test_check_novel_ai_allows_proper_nouns_and_blocks_verbatim_long_clause_copy(self):
        src = (
            "方明觉蹲在南赡部洲港口茶摊边的石阶上，缺了口的破碗搁在脚边，里面躺着三枚铜钱。\n\n"
            "“我是东胜神洲傲来国花果山水帘洞的猴子。”猴子举起铜钱串。\n"
            "【收徒返还系统已绑定】\n"
        )
        rewritten = (
            "南赡部洲的秋风吹过码头，方明觉在茶摊石阶旁席地而坐，脚边那只豁口破碗里只有三枚铜钱。\n\n"
            "“我是东胜神洲傲来国花果山水帘洞的猴子。”猴子举起铜钱串。\n"
            "【收徒返还系统已绑定】\n"
        )
        res_ok = cna.scan_text(rewritten, source_text=src)
        self.assertEqual(res_ok["status"], "PASS")

        lazy_copy = (
            "方明觉蹲在南赡部洲港口茶摊边的石阶上，缺了口的破碗搁在脚边，里面躺着三枚铜钱。\n\n"
            "“我是东胜神洲傲来国花果山水帘洞的猴子。”猴子举起铜钱串。\n"
        )
        res_lazy = cna.scan_text(lazy_copy, source_text=src)
        self.assertEqual(res_lazy["status"], "FAIL")
        self.assertTrue(any("verbatim-narrative-copy" in b for b in res_lazy["blocking"]))

    def test_check_novel_ai_pack_calibration_adapts_to_long_sentence_authors(self):
        stats_path = self.root / "long_pack_stats.json"
        np.write_json(stats_path, {
            "aggregate": {
                "sentence_chars": {"mean": 36.1, "sd": 23.6, "p90": 68},
                "paragraph_chars": {"single_sentence_share": 0.52},
            }
        })
        long_style = "\n\n".join([
            "若是换作寻常江湖武夫，纵然侥幸看破了这一剑的来路，在全身真气近乎枯竭的绝境之下，也绝无可能避开这雷霆万钧的必杀一击。",
            "然而李青山偏偏没有后退半步。"
        ] * 12)
        res = cna.scan_text(long_style, pack_path=stats_path)
        self.assertEqual(res["status"], "PASS")

    def test_check_novel_ai_blocks_machine_opening(self):
        bad_open = (
            "连夜大雨到了天亮也没收势，账房里返潮。十二岁的陈立坐在门槛边的高凳上，"
            "灰布短袍的袖口短了一截，露出手腕上的旧伤疤。\n"
        )
        res = cna.scan_text(bad_open)
        self.assertEqual(res["status"], "FAIL")
        self.assertIn("machine-opening-inventory", " ".join(res["blocking"]))


if __name__ == "__main__":
    unittest.main()
