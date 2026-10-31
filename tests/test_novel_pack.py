import argparse
import importlib.util
import json
from pathlib import Path
import re
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts" if (ROOT / "scripts/novel_pack.py").exists() else ROOT / "novel-dna-skill/scripts"
SKILL_ROOT = SCRIPTS_DIR.parent
spec = importlib.util.spec_from_file_location("novel_pack", SCRIPTS_DIR / "novel_pack.py")
np = importlib.util.module_from_spec(spec)
spec.loader.exec_module(np)
cna_spec = importlib.util.spec_from_file_location("check_novel_ai", SCRIPTS_DIR / "check_novel_ai.py")
cna = importlib.util.module_from_spec(cna_spec)
cna_spec.loader.exec_module(cna)
WORK = ROOT / ".work/test-tmp"


class PackFixture(unittest.TestCase):
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


class PackTests(PackFixture):
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
            "然而林玄偏偏没有后退半步。"
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


def blocking_kinds(res):
    return {b.split(" ")[0].rstrip(":") for b in res["blocking"]}


class DialoguePackCalibrationTests(PackFixture):
    """An author's own dialogue-heavy chapter must pass its own pack."""

    TURNS = (
        ("周管事淡淡道：", "坐。"),
        ("陈立依言坐下，问道：", "管事找我何事？"),
        ("周管事道：", "账房缺人。"),
        ("陈立道：", "我只认得几个字。"),
        ("周管事冷笑：", "认得字就够了。"),
        ("陈立沉默片刻，道：", "月钱多少？"),
        ("周管事伸出两根手指：", "二两。"),
        ("陈立摇头：", "不够。"),
        ("周管事眯起眼：", "你倒敢开价。"),
        ("陈立道：", "三两，少一文我不去。"),
    )

    def chapter(self):
        lines = ["# 第一章", ""]
        for _ in range(4):
            for tag, speech in self.TURNS:
                lines += [f"{tag}“{speech}”", ""]
            lines += ["屋里静了一阵。", ""]
        return "\n".join(lines)

    def test_own_dialogue_chapter_passes_own_pack(self):
        text = self.chapter()
        self.source.write_text(text, encoding="utf-8")
        self.init()
        res = cna.scan_text(text, pack_path=self.pack)
        self.assertEqual(res["status"], "PASS", res["blocking"])
        self.assertLess(res["sentence_mean"], 20)

    def test_speech_tag_does_not_merge_with_next_paragraphs(self):
        narrative = np.strip_quoted(self.chapter())
        self.assertLessEqual(max(np.narrative_sentence_lengths(narrative)), 12)

    def test_closing_quote_is_not_counted_as_a_sentence(self):
        self.assertEqual(len(np.sentence_pieces("周管事淡淡道：“坐。”")), 1)

    def test_pack_stats_store_narrative_only_baseline(self):
        self.source.write_text(self.chapter(), encoding="utf-8")
        self.init()
        agg = np.read_json(self.pack / "stats.json")["aggregate"]
        self.assertGreater(agg["narrative_sentence_chars"]["count"], 0)
        self.assertIn("zhe_per_1k", agg)
        self.assertIn("narrative_semicolons_per_1k", agg)

    def test_legacy_stats_without_narrative_baseline_recomputed_from_corpus(self):
        text = self.chapter()
        self.source.write_text(text, encoding="utf-8")
        self.init()
        stats = np.read_json(self.pack / "stats.json")
        stats["aggregate"].pop("narrative_sentence_chars")
        np.write_json(self.pack / "stats.json", stats)
        cal = cna.load_pack_calibration(self.pack)
        self.assertEqual(cal["baseline"], "narrative_sentence_chars")
        self.assertEqual(cna.scan_text(text, pack_path=self.pack)["status"], "PASS")

    def test_wrong_pack_path_is_an_error_not_a_silent_default(self):
        with self.assertRaises(ValueError):
            cna.load_pack_calibration(self.root / "不存在的写作包")
        draft = self.root / "draft.md"
        draft.write_text("陈立推开门。", encoding="utf-8")
        out = self.root / "report.json"
        code = cna.main([str(draft), "--pack", str(self.root / "不存在的写作包"), "--output", str(out)])
        self.assertEqual(code, 2)
        self.assertEqual(np.read_json(out)["status"], "ERROR")


class CheckerFalsePositiveTests(unittest.TestCase):
    def assert_not_flagged(self, text, kind):
        self.assertNotIn(kind, blocking_kinds(cna.scan_text(text)), text)

    def assert_flagged(self, text, kind):
        self.assertIn(kind, blocking_kinds(cna.scan_text(text)), text)

    def test_reduplicated_classifiers_are_not_dropped_numerals(self):
        for text in ("湖面泛起层层涟漪。", "村口升起缕缕炊烟。", "山风带着阵阵寒意。",
                     "他眼里透出丝丝笑意。", "他带着阵旗蛊赶路。", "她带着丝绸进城。"):
            self.assert_not_flagged(text, "dropped-numeral")
        for text in ("心口蹿起阵凉意。", "眼底泛起股狠劲。", "话里透出丝冷意。"):
            self.assert_flagged(text, "dropped-numeral")

    def test_ban_meaning_such_is_not_a_simile(self):
        for text in ("他从未感到这般绝望。", "这不是一般的恐惧。", "他只觉万般疲惫。",
                     "那般寒意他早已习惯。", "远超一般的恐惧。"):
            self.assert_not_flagged(text, "embodied-simile")
        for text in ("心口蹿起一股针扎似的凉意。", "涌起潮水般的疲惫。", "露出千年寒冰一般的冷意。"):
            self.assert_flagged(text, "embodied-simile")

    def test_weather_characters_in_names_are_not_weather(self):
        tail = "\n\n她推开院门。\n\n她走到井边。"
        for name in ("林雪", "秦风", "苏雨"):
            self.assert_not_flagged(f"{name}今年十六岁，一身青色衣衫，袖口磨得发白。{tail}",
                                    "machine-opening-inventory")
        self.assert_flagged(f"夜里下着大雪，林雪今年十六岁，一身青色衣衫，袖口磨得发白。{tail}",
                            "machine-opening-inventory")

    def test_xianxia_verbs_are_not_micro_action_tics(self):
        text = "".join(f"第{i}名弟子停了下来，在山门外布下了阵法，又坐了半日，终于悟了道。" for i in range(1, 13))
        self.assert_not_flagged(text, "micro-action-tic")
        tics = "".join(f"他看了一眼门口，叹了口气，又挠了一下头。第{i}遍。" for i in range(1, 6))
        self.assert_flagged(tics, "micro-action-tic")


class RewriteGateTests(unittest.TestCase):
    PARA = "陈立在账房里翻着旧账，一页一页核对去年的出入，发现有三笔银子对不上数目。"

    def test_synopsis_of_long_source_is_blocked(self):
        src = "\n\n".join([self.PARA] * 150)
        dst = "\n\n".join(["陈立查账，发现三笔银子对不上。"] * 26)
        self.assertIn("length-synopsis", blocking_kinds(cna.scan_text(dst, source_text=src)))

    def test_cutting_filler_is_not_blocked(self):
        src = "\n\n".join(f"第{i}天，陈立又在账房里翻着旧账，一页一页核对去年的出入，没有发现什么。" for i in range(40))
        dst = "\n\n".join(f"第{i}天，陈立核对旧账，一无所获。" for i in range(40))
        res = cna.scan_text(dst, source_text=src)
        self.assertLess(res["length_ratio"], 0.60)
        self.assertNotIn("length-synopsis", blocking_kinds(res))
        self.assertTrue(any(w.startswith("length-shrink") for w in res["warnings"]))

    def test_long_rewrite_of_short_source_is_inflation(self):
        src = "\n\n".join([self.PARA] * 6)
        dst = "\n\n".join(f"陈立第{i}次翻开账册，又一页一页地核对，越看越觉得这里头的数目不对劲。" for i in range(20))
        self.assertIn("length-inflation", blocking_kinds(cna.scan_text(dst, source_text=src)))

    def test_tiny_snippets_are_not_blocked_on_ratio(self):
        res = cna.scan_text("陈立推开门，屋里坐着三个人，都没有起身。", source_text="陈立推门。")
        self.assertNotIn("length-inflation", blocking_kinds(res))
        res = cna.scan_text("陈立推门。", source_text="陈立推开门，屋里坐着三个人，都没有起身。")
        self.assertNotIn("length-synopsis", blocking_kinds(res))

    def test_write_md_correct_example_passes_the_gate(self):
        doc = (SKILL_ROOT / "references/write.md").read_text(encoding="utf-8")
        before = re.search(r"\*\*【待改原稿片段】\*\*：\n> (.+)", doc).group(1)
        block = doc.split("**✅ 【正确改写示范", 1)[1].split("### Step 4", 1)[0]
        after = "\n".join(line[2:] for line in block.splitlines() if line.startswith("> "))
        res = cna.scan_text(after, source_text=before)
        self.assertEqual(res["status"], "PASS", res["blocking"])
        self.assertLessEqual(res["length_ratio"], cna.LENGTH_INFLATION_LIMIT)

    def test_identical_copy_is_blocked_for_any_number_of_long_clauses(self):
        clauses = [
            "陈立在账房里翻着旧账一页一页核对去年的出入发现三笔对不上",
            "周管事站在门口看了他很久终究没有开口只是转身回了前院",
            "院子里的老槐树落了一地叶子扫地的小厮正弯腰把叶子归成一堆",
            "账册最后一页夹着一张发黄的借条落款是三年前的腊月初八",
            "陈立把借条折好塞进袖口心里已经把这笔账算到了二房头上",
            "前院传来脚步声几个护院簇拥着二少爷往账房这边走过来",
        ]
        for n in range(1, 7):
            text = "\n\n".join(c[:12] + "，" + c[12:] + "。" for c in clauses[:n])
            with self.subTest(clauses=n):
                res = cna.scan_text(text, source_text=text)
                self.assertIn("verbatim-narrative-copy", blocking_kinds(res))
        partial = "\n\n".join(c[:12] + "，" + c[12:] + "。" for c in clauses[:3])
        src = partial + "\n\n这句原稿里另有一句完全不同的叙述内容放在最后面收尾。"
        self.assertIn("verbatim-narrative-copy", blocking_kinds(cna.scan_text(partial, source_text=src)))


class PovLockTests(unittest.TestCase):
    FIRST = ("我推开墓门，一股霉味扑面而来。我心里一紧，摸出了黑驴蹄子。\n\n"
             "我蹲下身子，借着火折子的光去看地上的脚印。")
    THIRD = ("陈立推开墓门，一股霉味扑面而来。他心里一紧，摸出了黑驴蹄子。\n\n"
             "他蹲下身子，借着火折子的光去看地上的脚印。")
    FIRST_RW = ("墓门被我一肩撞开，霉味顶着鼻子灌进来。我手往怀里探，黑驴蹄子先攥在了掌心。\n\n"
                "火折子晃了晃，我矮身去瞧地上那串脚印。")
    THIRD_RW = ("墓门被陈立一肩撞开，霉味顶着鼻子灌进来。他手往怀里探，黑驴蹄子先攥在了掌心。\n\n"
                "火折子晃了晃，他矮身去瞧地上那串脚印。")

    def test_first_person_source_keeps_first_person(self):
        self.assertEqual(cna.scan_text(self.FIRST_RW, source_text=self.FIRST)["status"], "PASS")

    def test_first_person_source_cannot_switch_to_third(self):
        self.assertIn("pov-changed", blocking_kinds(cna.scan_text(self.THIRD_RW, source_text=self.FIRST)))

    def test_third_person_source_cannot_switch_to_first(self):
        res = cna.scan_text(self.FIRST_RW, source_text=self.THIRD)
        self.assertIn("first-person-narration", blocking_kinds(res))

    def test_outline_mode_still_requires_third_person(self):
        self.assertIn("first-person-narration", blocking_kinds(cna.scan_text(self.FIRST_RW)))
        self.assertEqual(cna.scan_text(self.THIRD_RW, source_text=self.THIRD)["status"], "PASS")


class PackTargetedHabitTests(PackFixture):
    def build(self, body):
        self.source.write_text(body, encoding="utf-8")
        self.init()

    def test_slop_word_used_by_target_author_is_allowed(self):
        para = "“说吧。”周管事淡声道。陈立把账册摊开，一笔一笔念给他听。\n\n"
        self.build("# 第一章\n\n" + para * 8)
        draft = "“坐。”韩掌柜淡声道。纪衡没有坐，只把布包搁在柜上。\n\n他等着对方开口。\n"
        self.assertIn("llm-slop-vocab", blocking_kinds(cna.scan_text(draft)))
        res = cna.scan_text(draft, pack_path=self.pack)
        self.assertNotIn("llm-slop-vocab", blocking_kinds(res))
        self.assertTrue(any("allowed-by-pack" in w for w in res["warnings"]))

    def test_zhe_limit_follows_target_author(self):
        heavy = "他提着灯，踩着泥，背着刀，喘着气，看着门，等着人。\n\n"
        self.build("# 第一章\n\n" + heavy * 30)
        draft = heavy * 30
        self.assertIn("excess-zhe-posture", blocking_kinds(cna.scan_text(draft)))
        self.assertNotIn("excess-zhe-posture", blocking_kinds(cna.scan_text(draft, pack_path=self.pack)))


class FileAndPackToolingTests(PackFixture):
    def test_checker_reads_gbk_files(self):
        draft = self.root / "gbk.txt"
        draft.write_bytes("陈立推开门，看见周管事坐在灯下。".encode("gbk"))
        source = self.root / "gbk_src.txt"
        source.write_bytes("陈立推门进去，周管事正坐在灯下。".encode("gbk"))
        out = self.root / "gbk.json"
        self.assertEqual(cna.main([str(draft), "--source", str(source), "--output", str(out)]), 0)
        self.assertEqual(np.read_json(out)["status"], "PASS")

    def test_directory_inputs_use_natural_chapter_order(self):
        d = self.root / "章节"
        d.mkdir()
        names = ["第10章.md", "第1章.md", "第2章.md"]
        for n in names:
            (d / n).write_text("正文。", encoding="utf-8")
        self.assertEqual([p[0].name for p in np.collect([str(d)], False, self.pack)],
                         ["第1章.md", "第2章.md", "第10章.md"])
        d2 = self.root / "小节"
        d2.mkdir()
        for n in ["第十节.md", "第四节.md", "第三节.md", "第二节.md", "第一节.md", "第十一节.md"]:
            (d2 / n).write_text("正文。", encoding="utf-8")
        self.assertEqual([p[0].name for p in np.collect([str(d2)], False, self.pack)],
                         ["第一节.md", "第二节.md", "第三节.md", "第四节.md", "第十节.md", "第十一节.md"])

    def test_rule_reference_next_to_cjk_is_checked(self):
        self.init()
        self.finish()
        for text in ("见R999条", "规则R999的"):
            with self.subTest(text=text):
                np.atomic_text(self.pack / "指纹/对白.md", f"# 对白\n\nR001 写法。{text}。\n")
                self.assertIn("unknown rule reference R999", " ".join(np.validate_pack(self.pack)["errors"]))

    def test_unfilled_template_placeholders_fail_ready_pack(self):
        self.init()
        self.finish()
        template = (SKILL_ROOT / "templates/pack-zh/指纹/语言与节奏.md").read_text(encoding="utf-8")
        np.atomic_text(self.pack / "指纹/对白.md", template.replace("`Rxxx`、`Rxxx`", "`R001`"))
        self.assertIn("unfilled template placeholders", " ".join(np.validate_pack(self.pack)["errors"]))
        np.atomic_text(self.pack / "指纹/对白.md", "# 对白\n\nR001 平均句长 XX.X 字。\n")
        self.assertIn("unfilled template placeholders", " ".join(np.validate_pack(self.pack)["errors"]))

    def two_chapter_pack(self):
        other = self.root / "第二章.md"
        other.write_text("# 第二章\n\n她冷笑一声。\n", encoding="utf-8")
        self.init([other])
        rows = np.read_rows(self.pack / "corpus.jsonl")
        return rows[0]["id"], rows[1]["id"]

    def test_resolve_only_attaches_rules_with_evidence_in_that_chapter(self):
        first, second = self.two_chapter_pack()
        np.write_rows(self.pack / "rules.jsonl", [{"id": "R001", "dimension": "人物与对白",
            "claim": "c", "when": "w", "do": "d", "effect": "e", "avoid": "a", "strength": "local",
            "evidence": [{"source_id": second, "quote": "她冷笑一声。"}], "counterevidence": []}])
        np.write_rows(self.pack / "analysis.jsonl", [
            {"source_id": first, "sha256": "", "observation": "甲", "rule_ids": [], "unresolved": [], "dims": ["人物与对白"]},
            {"source_id": second, "sha256": "", "observation": "乙", "rule_ids": [], "unresolved": [], "dims": ["人物与对白"]},
        ])
        np.resolve_pack(self.pack)
        rows = {r["source_id"]: r for r in np.read_rows(self.pack / "analysis.jsonl")}
        self.assertEqual(rows[first]["rule_ids"], [])
        self.assertEqual(rows[second]["rule_ids"], ["R001"])
        cov = next(d for d in np.read_json(self.pack / "coverage.json")["dimensions"] if d["dimension"] == "人物与对白")
        self.assertEqual(cov["status"], "observed")
        self.assertNotEqual(cov["note"], np.PENDING_COVERAGE_NOTE)

    def test_slice_before_resolve_explains_what_to_do(self):
        self.init()
        s = np.read_rows(self.pack / "corpus.jsonl")[0]
        np.write_rows(self.pack / "scenes.jsonl", [{"id": "S001", "tags": ["对白"], "summary": "s",
            "spans": [{"source_id": s["id"], "start_quote": "先把门", "end_quote": "他仍站着。"}]}])
        with self.assertRaisesRegex(ValueError, "resolve"):
            np.slice_scenes(self.pack, tag="对白")


if __name__ == "__main__":
    unittest.main()
