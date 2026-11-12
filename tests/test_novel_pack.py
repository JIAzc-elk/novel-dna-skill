import argparse
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest import mock

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

    def test_failed_lexicon_scan_keeps_added_sources_in_draft_and_can_retry(self):
        self.init()
        self.finish()
        preserved = {
            name: (self.pack / name).read_bytes()
            for name in ("analysis.jsonl", "rules.jsonl", "scenes.jsonl", "lexicon.json")
        }
        more = self.root / "第二章.txt"
        more.write_text("他关了门。", encoding="utf-8")
        external = self.root / "全书语料"
        args = argparse.Namespace(pack=str(self.pack), input=[str(more)], recursive=False,
                                  lexicon_source=[str(external)])

        with self.assertRaisesRegex(ValueError, "Lexicon source does not exist"):
            np.add_sources(args)

        manifest = np.read_json(self.pack / "manifest.json")
        self.assertEqual((manifest["status"], manifest["revision"]), ("draft", 2))
        self.assertEqual(len(np.read_rows(self.pack / "corpus.jsonl")), 2)
        for name, before in preserved.items():
            self.assertEqual((self.pack / name).read_bytes(), before, name)
        self.assertEqual(np.validate_pack(self.pack)["status"], "FAIL")

        external.mkdir()
        (external / "第三章.txt").write_text("他拂袖而去。", encoding="utf-8")
        retry = np.add_sources(args)
        self.assertEqual((retry["added"], retry["status"]), (0, "draft"))
        self.assertEqual(np.read_json(self.pack / "manifest.json")["revision"], 2)
        self.assertEqual(np.read_json(self.pack / "lexicon.json")["external_lexicon_files"], 1)

    def test_failed_stats_refresh_never_leaves_new_sources_ready(self):
        self.init()
        self.finish()
        more = self.root / "第二章.txt"
        more.write_text("他关了门。", encoding="utf-8")
        with mock.patch.object(np, "stats_for_pack", side_effect=OSError("stats unavailable")):
            with self.assertRaisesRegex(OSError, "stats unavailable"):
                np.add_sources(argparse.Namespace(
                    pack=str(self.pack), input=[str(more)], recursive=False))
        self.assertEqual(np.read_json(self.pack / "manifest.json")["status"], "draft")
        self.assertEqual(len(np.read_rows(self.pack / "corpus.jsonl")), 2)

    def test_import_conflict_is_detected_before_copying_any_sources(self):
        self.init()
        self.finish()
        more = self.root / "第二章.txt"
        more.write_text("他关了门。", encoding="utf-8")
        last = self.root / "第三章.txt"
        last.write_text("门外传来脚步声。", encoding="utf-8")
        conflict = self.pack / "corpus/C0003.txt"
        conflict.write_text("用户保留的未登记语料。", encoding="utf-8")
        before = {p.relative_to(self.pack): p.read_bytes()
                  for p in self.pack.rglob("*") if p.is_file()}

        with self.assertRaisesRegex(ValueError, "Refusing to overwrite unlisted source"):
            np.add_sources(argparse.Namespace(
                pack=str(self.pack), input=[str(more), str(last)], recursive=False))

        after = {p.relative_to(self.pack): p.read_bytes()
                 for p in self.pack.rglob("*") if p.is_file()}
        self.assertEqual(after, before)

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

    def test_target_pack_downgrades_style_shape_warnings(self):
        stats_path = self.root / "target_pack_stats.json"
        np.write_json(stats_path, {
            "aggregate": {
                "sentence_chars": {"mean": 21.0, "sd": 5.0, "p90": 30},
                "paragraph_chars": {"single_sentence_share": 0.65},
            }
        })
        text = "\n\n".join([
            "这不是退让，而是把选择交给对方！",
            *["林峰已经站在门口！"] * 10,
            "“真正的强大来自坚持与选择，这就是修行的意义。”",
            "“真正的强大来自坚持与选择，这就是修行的意义。”",
            "“真正的强大来自坚持与选择，这就是修行的意义。”",
            "“真正的强大来自坚持与选择，这就是修行的意义。”",
            "“真正的强大来自坚持与选择，这就是修行的意义。”",
            "“真正的强大来自坚持与选择，这就是修行的意义。”",
        ])
        res = cna.scan_text(text, pack_path=stats_path)
        kinds = blocking_kinds(res)
        self.assertNotIn("contrast-inversion-formula", kinds)
        narr = cna.scan_narrative_architecture(text, cna.load_pack_calibration(stats_path))
        joined_blocking = " ".join(narr["blocking"])
        joined_advisory = " ".join(narr["advisory"])
        for kind in (
            "dialogue-preaching",
            "paragraph-uniformity",
            "exclamation-echo-abuse",
            "subject-monotony",
            "sentence-rhythm",
            "voice-card-homogeneity",
        ):
            self.assertNotIn(kind, joined_blocking)
            self.assertIn(kind, joined_advisory)

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

    def test_telling_words_block_when_overused_in_narration(self):
        cases = {
            "程度副词": "他非常生气，十分愤怒。山路十分难走，风极其冷，夜格外长，人相当累，心无比乱。",
            "情绪词": "他感到很难过。她心里很高兴。他觉得很害怕。众人有些尴尬。",
            "多余身体部位": "他用眼睛看着她，用手拿起茶杯。",
            "模糊形容词": "她很漂亮，院子很大，人很多，钱很少，屋子很小。",
            "口头禅过渡词": "他推开门，然后走进去，然后坐下来，然后掏出手机，接着打了个电话，于是出门。",
        }
        for name, text in cases.items():
            with self.subTest(name=name):
                res = cna.scan_text(text)
                self.assertIn("telling-words", blocking_kinds(res))
                self.assertIn(name, " ".join(res["blocking"]))
                self.assertGreater(res["telling_words"][name], 0)

    def test_telling_words_in_dialogue_are_not_counted(self):
        text = "“我非常生气，然后我很难过，她很漂亮，我用手拿的。”他说。\n\n" * 6
        res = cna.scan_text(text)
        self.assertNotIn("telling-words", blocking_kinds(res))
        self.assertEqual(res["telling_words"], {})

    def test_occasional_telling_word_is_only_a_note(self):
        res = cna.scan_text("他推开门，然后坐下。山路十分难走，他走得很慢。")
        self.assertNotIn("telling-words", blocking_kinds(res))
        self.assertTrue(any(w.startswith("telling-words-note") for w in res["warnings"]))

    def test_telling_word_lookalikes_are_not_counted(self):
        text = ("他在非常时期行事谨慎。此人修为相当于筑基。他十分钟后回来。特别是那人。"
                "由于是旧识，他点头。他用手指着门口。漂亮话谁都会说。他接着那碗汤。")
        res = cna.scan_text(text)
        self.assertEqual(res["telling_words"], {}, res["telling_words"])

    def test_telling_word_gate_is_the_same_for_every_pack(self):
        self.assertTrue((SKILL_ROOT / "references/通用指纹/告知词禁忌.md").is_file())
        self.assertTrue((SKILL_ROOT / "references/通用指纹/转场技法.md").is_file())
        self.assertIn(cna.TELLING_DOC, " ".join(
            cna.scan_text("他非常生气，十分愤怒，极其不满，格外烦躁，相当失落，无比疲惫。")["blocking"]))

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

    def test_write_md_contains_only_principles_without_copyable_templates(self):
        doc = (SKILL_ROOT / "references/write.md").read_text(encoding="utf-8")
        self.assertNotIn("蜘蛛在结网", doc)
        self.assertNotIn("缩了缩脖子", doc)
        self.assertIn("narrative-check", doc)

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


class DepthReportTests(unittest.TestCase):
    """validate only warns about shallow packs; it never fails them."""

    @staticmethod
    def rule(rid, sources, strength="repeated", counter=False):
        return {"id": rid, "strength": strength,
                "evidence": [{"source_id": s} for s in sources],
                "counterevidence": [{"source_id": sources[0]}] if counter else []}

    def test_shallow_large_pack_gets_every_depth_warning(self):
        sources = [{"id": f"C{i:04d}"} for i in range(1, 61)]
        rules = [self.rule(f"R{i:03d}", ["C0001"]) for i in range(1, 11)]
        scenes = [{"spans": [{"source_id": "C0002"}]}]
        rep = np.depth_report(sources, rules, scenes)
        text = " ".join(rep["warnings"])
        for needle in ("只有 1 章证据", "条规则", "场景切片", "章被规则证据或场景切片引用", "counterevidence"):
            self.assertIn(needle, text)
        self.assertEqual(rep["sources_used"], 2)
        self.assertEqual(len(rep["repeated_rules_with_one_source"]), 10)

    def test_deep_pack_has_no_depth_warnings(self):
        n = 30
        sources = [{"id": f"C{i:04d}"} for i in range(1, n + 1)]
        ids = [s["id"] for s in sources]
        rules = [self.rule(f"R{i:03d}", [ids[i % n], ids[(i + 1) % n]], counter=i % 3 == 0) for i in range(40)]
        scenes = [{"spans": [{"source_id": ids[i]}]} for i in range(n)]
        self.assertEqual(np.depth_report(sources, rules, scenes)["warnings"], [])

    def test_small_pack_skips_size_targets(self):
        sources = [{"id": "C0001"}, {"id": "C0002"}]
        rules = [self.rule("R001", ["C0001", "C0002"])]
        self.assertEqual(np.depth_report(sources, rules, [])["warnings"], [])

class DepthValidateTests(PackFixture):
    def test_depth_is_reported_but_never_fails_validate(self):
        self.init()
        self.finish()
        res = np.validate_pack(self.pack)
        self.assertEqual(res["status"], "PASS")
        self.assertEqual(res["depth"]["source_units"], 1)
        self.assertEqual(res["depth"]["sources_used"], 1)


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

    def test_extension_dimension_is_accepted_when_registered(self):
        self.init()
        self.finish()
        rules = np.read_rows(self.pack / "rules.jsonl")
        rules.append({**rules[0], "id": "R002", "dimension": "喜剧与调剂"})
        np.write_rows(self.pack / "rules.jsonl", rules)
        self.assertIn("喜剧与调剂", " ".join(np.validate_pack(self.pack)["errors"]))
        cov = np.read_json(self.pack / "coverage.json")
        cov["dimensions"].append({"dimension": "喜剧与调剂", "status": "partial", "rule_ids": ["R002"],
                                  "note": "作者特色", "suggested_inputs": []})
        np.write_json(self.pack / "coverage.json", cov)
        self.assertEqual(np.validate_pack(self.pack)["errors"], [])

    def test_slice_before_resolve_explains_what_to_do(self):
        self.init()
        s = np.read_rows(self.pack / "corpus.jsonl")[0]
        np.write_rows(self.pack / "scenes.jsonl", [{"id": "S001", "tags": ["对白"], "summary": "s",
            "spans": [{"source_id": s["id"], "start_quote": "先把门", "end_quote": "他仍站着。"}]}])
        with self.assertRaisesRegex(ValueError, "resolve"):
            np.slice_scenes(self.pack, tag="对白")


class ZhuqueAntiDetectionTests(unittest.TestCase):
    def test_contrast_inversion_and_omniscient_trailer_blocked_in_narration(self):
        bad = (
            "这不是他第一次进山，而是他第一次独自面对妖兽。\n\n"
            "他不知道的是，一场风暴正在酝酿。命运的齿轮开始转动，而这只是故事的开始。"
        )
        res = cna.scan_text(bad)
        kinds = blocking_kinds(res)
        self.assertIn("contrast-inversion-formula", kinds)
        self.assertIn("omniscient-trailer-hook", kinds)

    def test_dash_abuse_blocked(self):
        bad = "他拔出剑——剑刃映出冷光——对面的人却没有动。"
        res = cna.scan_text(bad)
        self.assertIn("dash-abuse", blocking_kinds(res))

    def test_narrative_architecture_catches_cliche_body_and_choppy_telegraph_and_subject_monotony(self):
        bad = "\n\n".join([
            "林峰站在山顶，倒吸一口凉气。",
            "林峰看着远方，瞳孔骤缩。",
            "林峰攥紧了拳头，没说话。",
            "林峰转身下山。",
            "林峰走得很慢。",
            "林峰听见了风声。",
            "林峰停下脚步。",
            "林峰回头看了一眼。",
            "林峰摸了摸剑柄。",
            "林峰走进了药房。",
            "林峰关上了木门。",
            "林峰坐在了凳子上。",
        ])
        narr = cna.scan_narrative_architecture(bad, cna.default_calibration())
        joined = " ".join(narr["blocking"])
        self.assertIn("cliche-body-reaction", joined)
        self.assertIn("choppy-telegraph-prose", joined)
        self.assertIn("subject-monotony", joined)

    def test_ascii_quotes_dialogue_preaching_is_detected(self):
        bad = (
            '"师父，这次失败让我明白了，真正的强大来自内心的坚持，所谓的修炼就是战胜自己。"\n\n'
            '"你说得对，修炼的意义和真谛就在于此。"\n'
        )
        narr = cna.scan_narrative_architecture(bad, cna.default_calibration())
        self.assertIn("dialogue-preaching", " ".join(narr["blocking"]))
        self.assertIn("ascii-straight-quotes", " ".join(narr["advisory"]))

    def test_pack_single_para_share_allows_high_staccato_pack_and_blocks_over_merged(self):
        cal = cna.default_calibration()
        cal.update({
            "target_single_para_share": 0.654,
            "min_single_para_share": 0.38,
            "max_single_para_share": 0.80,
            "warn_single_para_share": 0.77,
        })
        staccato_paras = [
            "开船梆子敲过头遍，离拔锚只剩半个时辰。纪衡跨进当铺门槛，掏出怀里的布包往高柜上一摊。",
            "碎银并着两锭成银，拢共十八两。",
            "柜台后的韩掌柜眼皮都没抬，拨了拨算盘珠子：“赎簪子，二十两。”",
            "少一分，柜门不开。",
            "“船不等人，这十八两您先收下，差下的二两明早家姐送来补齐。”",
            "韩掌柜嗤笑一声，巴掌拍在账册上：“当铺只认现银，不认人情。”",
            "柜前静了两息。",
            "越是露出急色，对方越笃定还能从牙缝里挤出二两银子。纪衡没再争，伸手把柜面上的碎银一粒接一粒拨回布包，挽了个死结。",
            "算盘珠子反倒不响了。",
            "“慢着！看在你姐弟不易的份上，十九两，少一文都不行。”韩掌柜终究松了口。",
            "布包里满打满算只有十八两。",
            "纪衡抱紧布包，拔腿往外走。掌柜在身后追喊再留三天，他脚步也没停。",
        ]
        narr_ok = cna.scan_narrative_architecture("\n\n".join(staccato_paras), cal)
        self.assertNotIn("choppy-telegraph-prose", " ".join(narr_ok["blocking"]))

        merged_paras = [
            "开船梆子敲过头遍。离拔锚只剩半个时辰。纪衡跨进门槛。",
            "柜台后的韩掌柜眼皮没抬。他拨了拨算盘珠子。",
            "“船不等人。”纪衡按住柜沿。韩掌柜嗤笑一声。",
            "柜前静了两息。纪衡没再争。他把碎银拨回布包。",
            "算盘珠子不响了。韩掌柜松了口。纪衡还是不够银子。",
            "布包里只有十八两。差这一两带不上船。",
            "纪衡抱紧布包。他拔腿往门外走去。",
            "掌柜在身后追喊。纪衡脚步没停。",
            "雨水打湿了青石阶。渡口就在巷子尽头。",
            "船夫已经解开了缆绳。跳板正要往回收。",
            "甲板上挤满了避雨的客商。没人注意这个少年。",
            "纪衡一脚踏上跳板。怀里的布包硌着肋骨。",
        ]
        narr_merged = cna.scan_narrative_architecture("\n\n".join(merged_paras), cal)
        self.assertIn("style-too-merged", " ".join(narr_merged["blocking"]))

    def test_real_world_ai_failure_modes_are_blocked(self):
        bad = (
            "寻常人骤然流落异界，多半惊惶无措。但许千秋不同。\n\n"
            "带不走实物，不代表不能变现！\n\n"
            "单子上写着闷雷石三十六方、焦雷木十八截、电光砂五十袋、赤雷铜三百斤。\n\n"
            "小翠假意咬了咬下唇，眼底深处掠过一抹异色，状似无意地退后半步。\n\n"
            "整盘棋局的前后因果，顿时在许千秋脑中彻底贯通！\n\n"
            "既然许天纵既想要灵脉，又想保住名声，便只能火急火燎地出手，再次火急火燎地派人堵门。\n\n"
            "他瞥见墙角有只蜘蛛在结网。\n\n"
            "“这账目有古怪。”\n\n"
            "这账目果然有古怪！\n"
        )
        res = cna.scan_text(bad)
        kinds = blocking_kinds(res)
        self.assertIn("formulaic-exception-hook", kinds)
        self.assertIn("contrast-inversion-formula", kinds)
        self.assertIn("inventory-number-list", kinds)
        self.assertIn("spoiler-acting-tags", kinds)
        self.assertIn("grammar-glitch", kinds)
        self.assertIn("cliche-idiom-stuffing", kinds)

        narr = cna.scan_narrative_architecture(bad, cna.default_calibration())
        narr_joined = " ".join(narr["blocking"])
        self.assertIn("exclamation-echo-abuse", narr_joined)
        self.assertIn("periodic-prop-filler", narr_joined)


class LexiconAndVoiceCardTests(PackFixture):
    def test_init_builds_11_category_lexicon_connectors_and_markdown_banks(self):
        self.source.write_text(
            "# 第一章\n\n"
            "方源冷笑一声，目光如炬，神色凛然，拂袖暴退。\n\n"
            "倘若此时催动春秋蝉硬撼，只怕真元海顷刻枯竭，得不偿失。\n\n"
            "话音未落，山巅轰隆炸响，寒芒席卷百丈苍穹。\n\n"
            "方源沉声道：“交出元石与蛊虫，否则休怪我当机立断！”\n\n"
            "古月博厉声道：“放肆！区区小辈也敢跟老夫谈规矩条件？”\n",
            encoding="utf-8",
        )
        self.init()
        lex_path = self.pack / "lexicon.json"
        self.assertTrue(lex_path.is_file())
        lex = np.read_json(lex_path)
        self.assertEqual(len(lex["categories"]), 11)
        for md_name in ("词汇库.md", "衔接库.md", "句段库.md", "声线卡.md"):
            self.assertTrue((self.pack / "词句库" / md_name).is_file())
        self.assertIn("蛊虫", lex["world_terms_lock"])
        self.assertGreater(len(lex["connectors"]["句内转折与推演"]), 0)

        lookup_res = np.lookup_lexicon(self.pack, query="算账 规矩")
        self.assertIn("categories", lookup_res)
        self.assertIn("connectors", lookup_res)
        self.assertIn("voice_cards", lookup_res)

    def test_external_full_book_lexicon_scan_enriches_vocab_without_polluting_bundled_corpus(self):
        ext_dir = self.root / "全书语料"
        ext_dir.mkdir()
        (ext_dir / "第100章.md").write_text(
            "他嘴角嗤笑，双眸微眯，袖袍一甩，轰鸣声震耳欲聋，纵然面对仙窍杀招也怡然不惧。\n",
            encoding="utf-8",
        )
        self.init()
        res = np.scan_lexicon_for_pack(self.pack, lexicon_source_paths=[str(ext_dir)])
        self.assertEqual(res["distillation_mode"], "selected_chapters_plus_full_lexicon")
        self.assertEqual(res["bundled_sources_scanned"], 1)
        self.assertEqual(res["external_lexicon_files"], 1)
        self.assertEqual(len(np.read_rows(self.pack / "corpus.jsonl")), 1)

    def test_cross_genre_world_term_leak_is_blocked_and_voice_homogeneity_caught(self):
        self.source.write_text(
            "# 第一章\n\n方源冷笑一声，催动蛊虫与真元海，在仙窍内盘算元石得失。\n",
            encoding="utf-8",
        )
        self.init()
        src_urban = "陈立坐在写字楼会议室里，翻开今年的财务报表，核对三笔亏损账目。"
        leaked_rewrite = "陈立坐在会议室里，冷笑一声，催动体内的蛊虫与真元海，核对三笔亏损账目。"
        res = cna.scan_text(leaked_rewrite, source_text=src_urban, pack_path=self.pack)
        self.assertIn("pack-world-term-leak", blocking_kinds(res))

        monotone_dlg = "\n\n".join([
            "“我们现在必须立刻出发去山谷。”",
            "“你们不要在这里继续浪费时间。”",
            "“他们已经把所有的退路封死了。”",
            "“我们只有一条路可以走下去了。”",
            "“大家准备好手里的兵器冲出去。”",
            "“今天就算拼了命也要杀出重围。”",
        ])
        narr = cna.scan_narrative_architecture(monotone_dlg, cna.default_calibration())
        self.assertIn("voice-card-homogeneity", " ".join(narr["blocking"]))

    def test_resolve_and_validate_check_lexicon_quotes_and_distillation_ratio(self):
        self.source.write_text(
            "# 第一章\n\n“先把门关上。”\n他仍站着。\n目光一凝，倘若此时硬撼，只怕真元海枯竭。\n",
            encoding="utf-8",
        )
        self.init()
        self.finish()
        lex = np.read_json(self.pack / "lexicon.json")
        s_id = np.read_rows(self.pack / "corpus.jsonl")[0]["id"]
        # Put an unresolved quote (missing start_line/end_line) into lexicon
        lex["categories"]["眼神与视线词"] = [{
            "word": "目光一凝",
            "count": 1,
            "replaces_ai": "替代瞳孔骤缩",
            "evidence": {"source_id": s_id, "quote": "目光一凝，倘若此时硬撼"},
        }]
        np.write_json(self.pack / "lexicon.json", lex)
        res_resolve = np.resolve_pack(self.pack)
        self.assertEqual(res_resolve["status"], "PASS", res_resolve["errors"])
        self.assertGreaterEqual(res_resolve["resolved_lexicon_quotes"], 1)

        res_val = np.validate_pack(self.pack)
        self.assertEqual(res_val["status"], "PASS", res_val["errors"])
        self.assertGreater(res_val["depth"]["distillation_ratio"], 0.0)
        self.assertTrue(res_val["depth"]["lexicon"]["present"])
        self.assertFalse(res_val["depth"]["analysis_enhancement"]["present"])

    def test_optional_oh_story_analysis_integration_enriches_pack_and_lookup(self):
        self.init()
        self.finish()
        # Without analysis library: validate passes cleanly
        res_before = np.validate_pack(self.pack)
        self.assertEqual(res_before["status"], "PASS", res_before["errors"])
        self.assertFalse(res_before["depth"]["analysis_enhancement"]["present"])

        # Create a mock oh-story 拆文库
        analysis_dir = self.root / "示例拆文库"
        (analysis_dir / "剧情").mkdir(parents=True)
        (analysis_dir / "文风.md").write_text(
            "# 示例 文风\n\n## 整体语感\n- 冷、短、利，情绪靠动作节拍。\n\n## 对话技法\n- 问非所答、语气反差。\n",
            encoding="utf-8",
        )
        (analysis_dir / "拆文报告.md").write_text(
            "# 拆文报告\n\n## 核心发现\n1. 每一次跃升都明码标价。\n\n## 写法技巧\n1. 一笔两用。\n",
            encoding="utf-8",
        )
        (analysis_dir / "剧情" / "情绪模块.md").write_text(
            "# 情绪模块\n\n### EM-001 先知沉默局\n\n| 字段 | 内容 |\n|---|---|\n"
            "| 读者想看什么 | 主角掌握内情却保持沉默 |\n| 情绪链 | 信息差 → 误判 → 翻盘 |\n"
            "| 戏剧单元 | 沉默执行 |\n| 复现步骤 | 1. 给信息 2. 最小干预 |\n",
            encoding="utf-8",
        )
        (analysis_dir / "剧情" / "节奏.md").write_text(
            "# 节奏索引\n\n## 循环单元\n- 小循环（约3章）：缺口→布局→得手。\n",
            encoding="utf-8",
        )

        res_int = np.integrate_analysis_for_pack(self.pack, analysis_dir)
        self.assertEqual(res_int["status"], "PASS")
        self.assertEqual(len(res_int["generated_files"]), 4)
        self.assertEqual(res_int["emotion_modules_count"], 1)
        self.assertIn("拆文增强/01_文风与黄金三章深度拆解.md", (self.pack / "PACK.md").read_text(encoding="utf-8"))

        res_after = np.validate_pack(self.pack)
        self.assertEqual(res_after["status"], "PASS", res_after["errors"])
        self.assertTrue(res_after["depth"]["analysis_enhancement"]["present"])
        self.assertEqual(res_after["depth"]["analysis_enhancement"]["emotion_modules"], 1)

        lk = np.lookup_lexicon(self.pack, query="先知 沉默")
        self.assertIn("emotion_modules", lk)
        self.assertEqual(lk["emotion_modules"][0]["id"], "EM-001")


class EditorVideoNotesGateTests(unittest.TestCase):
    """Tests for the AI-writing tells highlighted in the editor's video notes."""

    def test_editor_video_examples_are_blocked(self):
        bad = (
            "张三 ## 站在山门前 ， 喉间发涩 ， 血腥味涌上舌尖。\n\n"
            "柳如烟轻声开口，声音听不出半分波澜：“你手中的灵根秘钥，是进入青云宗外门甄选的关键。”\n\n"
            "旁边一名黑衣修士嗤笑一声：“小子，识相点就把秘钥交出来。如烟姑娘天资出众，入青云宗才是理所应当，你一个凡夫，别挡别人的仙路。”\n\n"
            "剧痛不止来自伤口，更是从心底蔓延开来的寒凉。\n\n"
            "他没有秘钥，没有旁人帮扶，凭着一股不肯认输的执念，赶到了青云宗山门之下。\n\n"
            "他缓缓抬头，微微一怔，陡然握拳，忽而冷笑，半晌不语，不由得看向远方，不知为何心中一沉。\n\n"
            "指尖触到玉佩，舌尖咬破，喉间滚过一声低吼，眉梢染上寒霜。\n\n"
            "沉默了片刻之后，缓缓开口，声音带着不容置喙的严肃。\n\n"
            "“往后仙路漫漫，青云之上，他日自会再相逢。”\n"
        )
        res = cna.scan_text(bad)
        kinds = blocking_kinds(res)
        self.assertIn("abnormal-symbol-spacing", kinds)
        self.assertIn("redundant-speech-tone", kinds)
        self.assertIn("dialogue-info-dump", kinds)
        self.assertIn("contrast-inversion-formula", kinds)
        self.assertIn("status-adverb-overuse", kinds)
        self.assertIn("body-part-sensory-repetition", kinds)
        self.assertIn("hollow-vow-cliche", kinds)

    def test_face_swapping_acting_cliche_and_pleonastic_face_are_blocked(self):
        bad = "她眼波一转，脸上已换作一副惊喜关切的面孔。"
        res = cna.scan_text(bad)
        kinds = blocking_kinds(res)
        self.assertIn("llm-slop-vocab", kinds)
        self.assertIn("spoiler-acting-tags", kinds)
        self.assertIn("telling-words", kinds)

        bad2 = "掌柜敛去眼底寒意，面上旋即挂上一副热情周到的笑容。"
        res2 = cna.scan_text(bad2)
        self.assertIn("spoiler-acting-tags", blocking_kinds(res2))

    def test_review_items_locates_resume_variation_micro_tics_and_pov_per_paragraph(self):
        draft = (
            "三人推门入屋。入目处，这位昔日高高在上、不苟言笑的雷脉首席亲传弟子，此刻正缩在床头。\n\n"
            "许千秋揉了一下太阳穴，扫了一眼桌上的药碗，心里觉得非常不安。\n\n"
            "难道我真的不是在玩游戏？\n"
        )
        res = cna.scan_text(draft)
        self.assertIn("elegant-variation", blocking_kinds(res))
        self.assertGreaterEqual(res["review_items_count"], 5)
        cats = [item["category"] for item in res["review_items"]]
        self.assertTrue(any("elegant-variation" in c for c in cats))
        self.assertTrue(any("micro-action-tic" in c for c in cats))
        self.assertTrue(any("telling-words" in c for c in cats))
        self.assertTrue(any("first-person-narration" in c for c in cats))
        paras_hit = {item["para"] for item in res["review_items"]}
        self.assertEqual(paras_hit, {1, 2, 3})

    def test_deletable_ai_bloat_and_review_suggestion_self_check(self):
        bloat_draft = (
            "许千秋在心中默默盘算起来。入目处，桌上的药碗已经凉了。\n\n"
            "他端起茶杯喝了一口，以此来掩饰内心的慌乱。这种剧痛让他几乎快要晕厥过去。\n"
        )
        res = cna.scan_text(bloat_draft)
        self.assertIn("deletable-ai-bloat", blocking_kinds(res))
        bloat_items = [it for it in res["review_items"] if "deletable-ai-bloat" in it["category"]]
        self.assertGreaterEqual(len(bloat_items), 3)

        bad_review_report = (
            "### 审阅意见 1\n"
            "- **【原句】**：小翠低声说了几句。\n"
            "- **【可直接复制替换的改写建议】**\n"
            "话音微顿，她拿起竹夹拨了拨炉底暗炭，抬眼觑着许千秋的神色，低声道："
            "“只是奴婢有一事总想不通。您昔日乃是紫府巅峰，天纵少爷既承了您的神雷体与雷泽气海，外头传言却只堪堪停在筑基境。”\n"
        )
        extracted = cna.extract_review_suggestion_blocks(bad_review_report)
        self.assertIn("抬眼觑着", extracted)
        sug_res = cna.scan_text(extracted)
        sug_kinds = blocking_kinds(sug_res)
        self.assertIn("llm-slop-vocab", sug_kinds)
        self.assertIn("dialogue-info-dump", sug_kinds)

        # Inline suggestion & 删除后段落定稿 extraction + periodic prop filler in review_items
        inline_report = (
            "- **【删除后段落定稿】**：她拨了拨暗炭，低声说了药堂的事。\n"
            "- **【可直接复制替换的改写建议】**：许千秋看着炉底暗炭，没有吭声。\n\n"
            "小翠又看了一眼炭火，退了出去。\n"
        )
        extracted2 = cna.extract_review_suggestion_blocks(inline_report)
        self.assertIn("她拨了拨暗炭", extracted2)
        self.assertIn("许千秋看着炉底暗炭", extracted2)
        res2 = cna.scan_text(extracted2)
        prop_bloat = [it for it in res2["review_items"] if "周期性道具节拍器垫片" in it["category"]]
        self.assertEqual(len(prop_bloat), 2)

    def test_second_gen_oversteered_ai_slop_and_full_name_repetition_are_blocked(self):
        oversteered = (
            "小翠骇了一跳，往后退开半步。\n\n"
            "话音未落，小翠手脚却半点不慢，顺势掰开许千秋五指，将其中一块严丝合缝塞进他掌心："
            "“要用灵石作引介，才能将游离道痕牵引入窍，由灵根吸附固定在气海中……”\n\n"
            "榻旁，小翠双手绞着围裙退开半步，目光却一眨不眨地钉在他小腹气海处。\n\n"
            "倘若许天纵当真完整继承了十寸满窍的【紫霄神雷体】，灵根吸附道痕涓滴不漏，怎会刚入筑基便出岔子？\n\n"
            "小翠视线落在他衣襟上：“您怀里不是收着那块【首席道牌】么？那是宗门赐下的储物道牌，正面记着功绩贡献，内里自带一方储物空间。只需渡入一缕道痕便能显影查验。”\n\n"
            "小翠双手接过【首席道牌】，指尖渡入一缕微弱道痕，往牌面一按。牌面正面亮起一行紫金大字，又取出一枚四阶雷鹏妖丹，仔细打量这枚四阶雷鹏妖丹。\n\n"
            "小翠端起地上的污水盆背过身去，连连摆手。许家毁掉一尊真正的先天道体，却只弄出个连气海都锁不住的残次水货。\n"
        )
        res = cna.scan_text(oversteered)
        kinds = blocking_kinds(res)
        self.assertIn("coined-or-malaprop-slop", kinds)
        self.assertIn("bracket-highlight-abuse", kinds)
        self.assertIn("full-name-repetition", kinds)
        self.assertIn("dialogue-info-dump", kinds)

        clean_human = (
            "小翠吓了一跳，往后退开半步。\n\n"
            "小翠从身上摸出一卷青皮小册，又取出两块下品灵石，顺势掰开许千秋五指，塞进他掌心。\n\n"
            "榻旁，小翠退开半步，眼睛却一眨不眨地钉在他气海处。\n\n"
            "倘若许天纵当真完整继承了那紫霄神雷体，接手现成的雷泽气海理当运转自如，怎会急着搜刮灵材？\n\n"
            "小翠视线落在他微敞的衣襟上：“您怀里不是一直收着那块首席道牌么？那道牌记着功绩贡献，还自带一方储物空间，往日攒下的灵材一般都收在里头。只需催动便能查验，您如今气海有伤催动不得，要不奴婢替您打开瞧瞧？”\n\n"
            "小翠接过道牌，随即催动起来。牌面正面亮起一行字，显出三万八千余点功绩点，里头还躺着一枚四阶雷鹏妖丹。许千秋盯住那妖丹。\n\n"
            "小翠背过身去，连连摆手。一旦让各脉长老察觉，许家毁掉一尊真正的先天道体，却只弄出个残次水货，许家在极霄宗的根基当场便要崩塌。\n"
        )
        res_clean = cna.scan_text(clean_human)
        self.assertEqual(res_clean["status"], "PASS", res_clean["blocking"])


class ReliabilityRegressionTests(PackFixture):
    def analysis_library(self):
        library = self.root / "analysis"
        library.mkdir(exist_ok=True)
        (library / "文风.md").write_text("# 文风\n\n对白幽默，叙述简洁。\n", encoding="utf-8")
        return library

    def checker_error(self, text="陈立推门出去。", **kwargs):
        draft = self.root / "draft.md"
        draft.write_text(text, encoding="utf-8")
        report = self.root / "report.json"
        argv = [str(draft), "--output", str(report)]
        for key, value in kwargs.items():
            argv.extend(["--" + key, str(value)])
        with contextlib.redirect_stdout(io.StringIO()):
            code = cna.main(argv)
        self.assertEqual(code, 2)
        result = np.read_json(report)
        self.assertEqual(result["status"], "ERROR")
        return result

    def test_reintegrating_preserves_following_user_content(self):
        self.init()
        library = self.analysis_library()
        np.integrate_analysis_for_pack(self.pack, library)
        entry = self.pack / "PACK.md"
        suffix = "\n\n用户追加的备注。\n\n## 用户创作约束\n保留这段内容。\n"
        entry.write_text(entry.read_text(encoding="utf-8") + suffix, encoding="utf-8")
        np.integrate_analysis_for_pack(self.pack, library)
        self.assertTrue(entry.read_text(encoding="utf-8").endswith(suffix))
        before = entry.read_bytes()
        np.integrate_analysis_for_pack(self.pack, library)
        self.assertEqual(entry.read_bytes(), before)

    def test_legacy_integration_preserves_custom_sections_and_files(self):
        self.init()
        entry = self.pack / "PACK.md"
        suffix = "\n用户追加的备注。\n\n## 自定义约束\n不能丢失。\n"
        entry.write_text(
            "# 写作包\n\n### 拆文库增强九大模块\n\n本写作包已全量整合外部拆文库。\n"
            "- **[拆文增强/旧模块.md](拆文增强/旧模块.md)**：旧入口\n" + suffix,
            encoding="utf-8",
        )
        legacy = self.pack / "拆文增强/文风与对话技法.md"
        np.atomic_text(legacy, "用户精修的旧版模块。")
        np.integrate_analysis_for_pack(self.pack, self.analysis_library())
        self.assertTrue(entry.read_text(encoding="utf-8").endswith(suffix))
        self.assertEqual(legacy.read_text(encoding="utf-8"), "用户精修的旧版模块。")

    def test_analysis_entry_only_links_generated_modules(self):
        self.init()
        result = np.integrate_analysis_for_pack(self.pack, self.analysis_library())
        entry = (self.pack / "PACK.md").read_text(encoding="utf-8")
        links = re.findall(r"\]\((拆文增强/[^)]+)\)", entry)
        self.assertEqual(links, result["generated_files"])
        self.assertNotIn("养蛊经济学", entry)
        self.assertNotIn("47 组", entry)

    def test_all_nine_analysis_modules_are_linked_and_searchable(self):
        self.init()
        self.finish()
        library = self.analysis_library()
        files = {
            "拆文报告.md": "# 拆文报告\n\n## 核心发现\n利益冲突推进故事。",
            "剧情/情绪模块.md": "### EM-001 夺船\n\n| 读者想看什么 | 夺回渡船 |\n",
            "剧情/节奏.md": "# 节奏\n\n先缓后急。",
            "剧情/渡口.md": "| 标题 | 渡口夺船 |\n| 起始目标 | 夺船过河 |\n",
            "角色/陈立.md": "# 陈立\n\n## 定位\n船主\n\n## 成长弧线\n夺回渡船。",
            "设定/世界观/背景设定.md": "# 背景\n\n渡口封锁。",
            "设定/势力/船帮.md": "# 船帮\n\n## 类型\n商帮\n\n## 立场倾向\n守住渡口。",
            "章节/第1章_摘要.md": "**因果**：夺船后过河\n**三维节奏**：5/5\n**章尾钩子**：对岸是谁？",
        }
        for relative, text in files.items():
            np.atomic_text(library / relative, text)
        result = np.integrate_analysis_for_pack(self.pack, library)
        self.assertEqual(len(result["generated_files"]), 9)
        entry = (self.pack / "PACK.md").read_text(encoding="utf-8")
        self.assertEqual(re.findall(r"\]\((拆文增强/[^)]+)\)", entry), result["generated_files"])
        self.assertTrue(all((self.pack / path).is_file() for path in result["generated_files"]))
        lookup = np.lookup_lexicon(self.pack)
        for key in ("emotion_modules", "plot_units", "character_arcs", "factions", "chapter_causality"):
            self.assertTrue(lookup.get(key), key)
        result = np.validate_pack(self.pack)
        self.assertEqual(result["status"], "PASS", result["errors"])

    def test_add_preserves_curated_and_external_lexicon(self):
        self.init()
        np.scan_lexicon_for_pack(self.pack, [str(self.source)])
        lex = np.read_json(self.pack / "lexicon.json")
        lex["term_transfer_map"].append({"source_term": "自定义专名", "urban": "人工映射"})
        np.write_json(self.pack / "lexicon.json", lex)
        bank = self.pack / "词句库/声线卡.md"
        bank.write_text("人工精修声线，必须保留。", encoding="utf-8")
        before = {p: p.read_bytes() for p in (self.pack / "lexicon.json", bank)}
        more = self.root / "第二章.txt"
        more.write_text("他关门离去。", encoding="utf-8")
        result = np.add_sources(argparse.Namespace(
            pack=str(self.pack), input=[str(more)], recursive=False))
        self.assertEqual(result["added"], 1)
        self.assertEqual(result["lexicon_status"], "preserved")
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content)

    def test_explicit_rescan_keeps_recoverable_backup(self):
        self.init()
        bank = self.pack / "词句库/声线卡.md"
        bank.write_text("人工精修声线。", encoding="utf-8")
        before = (self.pack / "lexicon.json").read_bytes()
        result = np.scan_lexicon_for_pack(self.pack)
        backup = Path(result["backup"])
        self.assertEqual((backup / "lexicon.json").read_bytes(), before)
        self.assertEqual((backup / "词句库/声线卡.md").read_text(encoding="utf-8"), "人工精修声线。")

    def test_backup_failure_does_not_overwrite_current_lexicon(self):
        self.init()
        paths = [self.pack / "lexicon.json", *sorted((self.pack / "词句库").glob("*.md"))]
        before = {path: path.read_bytes() for path in paths}
        with mock.patch.object(np.shutil, "copy2", side_effect=OSError("backup unavailable")):
            with self.assertRaisesRegex(OSError, "backup unavailable"):
                np.scan_lexicon_for_pack(self.pack)
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content)

    def test_missing_pack_lookup_and_scan_do_not_create_files(self):
        missing = self.root / "typo-pack"
        for command in ("lookup", "scan-lexicon"):
            with self.subTest(command=command), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(np.main([command, "--pack", str(missing)]), 1)
                self.assertFalse(missing.exists())

    def test_external_symlink_is_rejected_before_overwriting_lexicon(self):
        self.init()
        external = self.root / "external"
        external.mkdir()
        link = external / "chapter.txt"
        try:
            link.symlink_to(self.source)
        except (OSError, NotImplementedError):
            self.skipTest("Symlink creation is unavailable")
        before = (self.pack / "lexicon.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "symlink"):
            np.scan_lexicon_for_pack(self.pack, [str(external)])
        self.assertEqual((self.pack / "lexicon.json").read_bytes(), before)

    def test_ready_pack_requires_stats(self):
        self.init()
        self.finish()
        (self.pack / "stats.json").unlink()
        result = np.validate_pack(self.pack)
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("stats.json", " ".join(result["errors"]))

    def test_malformed_stats_produce_structured_error(self):
        self.init()
        stats = np.read_json(self.pack / "stats.json")
        stats["aggregate"]["narrative_sentence_chars"] = {"count": 1, "mean": 10}
        np.write_json(self.pack / "stats.json", stats)
        self.assertIn("p90", self.checker_error(pack=self.pack)["error"])
        self.assertEqual(np.validate_pack(self.pack, True)["status"], "FAIL")

    def test_nonfinite_stats_are_rejected_and_zero_share_is_preserved(self):
        self.init()
        stats = np.read_json(self.pack / "stats.json")
        stats["aggregate"]["paragraph_chars"]["single_sentence_share"] = 0
        np.write_json(self.pack / "stats.json", stats)
        self.assertEqual(cna.load_pack_calibration(self.pack)["target_single_para_share"], 0)
        for value in (float("nan"), float("inf"), -1, True):
            with self.subTest(value=value):
                stats["aggregate"]["sentence_chars"]["mean"] = value
                np.write_json(self.pack / "stats.json", stats)
                self.checker_error(pack=self.pack)
                self.assertEqual(np.validate_pack(self.pack, True)["status"], "FAIL")

    def test_invalid_connector_shape_is_rejected_by_both_tools(self):
        self.init()
        lex = np.read_json(self.pack / "lexicon.json")
        lex["connectors"] = "not-an-object"
        np.write_json(self.pack / "lexicon.json", lex)
        self.assertEqual(np.validate_pack(self.pack, True)["status"], "FAIL")
        self.assertIn("connectors", self.checker_error(pack=self.pack)["error"])

    def test_corrupt_lexicon_cannot_disable_world_term_gate(self):
        self.init()
        lex = np.read_json(self.pack / "lexicon.json")
        lex["world_terms_lock"] = ["禁域词"]
        np.write_json(self.pack / "lexicon.json", lex)
        text = "陈立走进禁域词，关上房门。"
        result = cna.scan_text(text, source_text="陈立推开房门。", pack_path=self.pack)
        self.assertIn("pack-world-term-leak", blocking_kinds(result))
        (self.pack / "lexicon.json").write_text("{", encoding="utf-8")
        self.assertIn("lexicon.json", self.checker_error(text, pack=self.pack)["error"])

    def test_empty_draft_or_source_is_an_error(self):
        for text in ("", " \n", "# 第一章\n\n", "## 附：建议\n备注"):
            with self.subTest(text=text):
                self.checker_error(text)
        source = self.root / "empty-source.md"
        source.write_text("# 原稿\n", encoding="utf-8")
        self.checker_error(source=source)

    def test_short_first_person_cannot_bypass_pov_lock(self):
        first = "我推开门，走了进去。"
        third = "他推开门，走了进去。"
        self.assertIn("first-person-narration", blocking_kinds(cna.scan_text(first)))
        self.assertIn("pov-changed", blocking_kinds(cna.scan_text(third, source_text=first)))
        self.assertNotIn("pov-changed", blocking_kinds(
            cna.scan_text("我走到门外。", source_text=first)))
        self.assertNotIn("first-person-narration", blocking_kinds(
            cna.scan_text("陈立忘我地练剑。他心想：我该回去了。")))
        for text in ("任我行推门出去。", "我方斥候已经出发。", "“我来开门。”陈立走到门前。"):
            with self.subTest(text=text):
                self.assertNotIn("first-person-narration", blocking_kinds(cna.scan_text(text)))


if __name__ == "__main__":
    unittest.main()
