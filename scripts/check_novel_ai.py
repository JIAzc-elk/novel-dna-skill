#!/usr/bin/env python3
"""Deterministic quality, POV & AI-slop gate for Chinese web fiction drafts.

Key guarantees:
1. Narration POV is locked. Drafts written from an outline must use third
   person. In rewrite mode (`--source`) the draft keeps the source's POV: a
   third-person source may not turn into "我" narration, and a first-person
   source may not be switched to third person.
2. `--pack` calibrates sentence rhythm, "着" density, semicolons and slop words
   against the target pack. Sentence length is measured the same way on both
   sides: narration only, quotes removed, line breaks end a sentence. A wrong
   `--pack` path is an error, never a silent fallback to defaults.
3. In rewrite mode, cutting filler is never penalized; only padding (>1.60x)
   and a draft collapsed into a synopsis (<0.40x) are blocked. Lazy verbatim
   copying of long unquoted narrative clauses (>=18 chars) is blocked, never
   penalizing names, numbers, system panels, quotations or preserved dialogue
   via n-gram overlap.
"""
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
from pathlib import Path
import re
import statistics
import sys

# Shared decoding, quote stripping and sentence splitting, so the gate measures
# drafts exactly the way novel_pack.py measured the target pack.
_spec = importlib.util.spec_from_file_location(
    "novel_pack", Path(__file__).resolve().with_name("novel_pack.py"))
packlib = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(packlib)

# Cutting filler is part of the job, so shrinking is not limited. Only a draft
# collapsed into a synopsis is blocked; plot preservation is checked against the
# fact-lock list, not by character count.
LENGTH_SYNOPSIS_FLOOR = 0.40
LENGTH_SHRINK_NOTE = 0.60
LENGTH_INFLATION_LIMIT = 1.60
LENGTH_MIN_DIFF = 200  # ratio swings on tiny snippets are not padding or a synopsis
COPY_RATE_LIMIT = 0.35
DEFAULT_ZHE_LIMIT = 11.5
CLAUSE_PUNCT = r"[\s，、：:（）()《》—…,.!\-]"

# 0. Idioms and indirect-speech patterns where '我' is NOT first-person narration
IDIOM_WO_RE = re.compile(
    r"自我|忘我|无我|本我|真我|大我|小我|物我|敌我|故我|依然故我|"
    r"你死我活|我行我素|唯我独尊|舍我其谁|卿卿我我|你争我夺|尔虞我诈|"
    r"彼竭我盈|敌强我弱|敌众我寡|敌暗我明|敌明我暗|不分你我|你中有我|我中有你"
)
# Indirect thought/speech lead-ins that occasionally omit quotes in authentic web novels
INDIRECT_WO_CLAUSE_RE = re.compile(
    r"(?:告诉[他她它]|对[他她它]说|心[中里]?[道想说念]|暗[道想忖]|自语道?)[，：:\s]*[^。！？!?\n]{0,24}"
)

# 1. Embodied simile & synesthetic inflation clichés (blocking).
# 这般/那般/万般/百般 and a clause-initial 一般 mean "such/ordinary", not "like".
SIMILE_MARKER = (
    r"(?:似的|一样的|(?<![这那怎万百千诸两多别一])般的?"
    r"|(?<![这那怎万百千诸两多别同不非很是算较比超于过])一般的?)"
)
EMBODIED_SIMILE_PATTERNS = (
    r"(?:胸口|心口|心头|心脏|心底|后背|脊背|脑海|识海|胃里|喉咙|指尖)[^，。！？\n]{0,6}(?:像|好似|仿佛|如同|宛如)[^，。！？\n]{0,10}(?:针|刺|刀|剑|锤|铁手|大手|冰水|冷水|电流|雷击|火烧)",
    r"(?<!凌迟)[^，。！？\n]{1,6}" + SIMILE_MARKER
    + r"[^，。！？\n]{0,3}(?:凉意|寒意|冷意|剧痛|刺痛|钝痛|疲惫|倦意|窒息|压迫感|死寂|恐惧|绝望|耻辱)",
)

# 2. Second-pass evasion ungrammatical mutations (dropped '一' before classifier).
# Reduplication (阵阵/丝丝/层层/缕缕) and nouns (阵法/阵旗/丝绸/层次) are normal Chinese.
DROPPED_NUMERAL_RE = re.compile(
    r"(?:蹿起|窜起|涌起|升起|泛起|透出|掠过|闪过|浮现|带着)(?!一)"
    r"(?:阵(?![阵法旗盘眼图纹基营势型线脚前中容列])|股(?![股份东票掌])"
    r"|丝(?![丝绸线帕带巾袜毫缕织])|抹(?![抹布])|缕(?!缕)|层(?![层次面级楼]))"
)

# 3. Elegant variation name-swapping (换称呼油滑症; only flags when overused >= 3 times)
ELEGANT_VARIATION_RE = re.compile(
    r"这位(?:年轻|年迈|清瘦|灰衣|青衣|黑衣)?(?:管事|少爷|少年|老者|猎户|掌柜|修士|道人|姑娘|丫鬟)|"
    r"那名(?:清瘦|年轻|年老|青衣|黑衣|灰衣)(?:道人|修士|少年|猎户|女子|汉子)"
)

# 4. AI stage-summary & clue-recap lists (章末线索报菜名盘点)
CLUE_RECAP_SUMMARY_RE = re.compile(
    r"(?:线索|信息|事情|账目)[^。！？\n]{0,12}(?:梳理|盘点|汇总|理顺)了一遍|"
    r"[^。！？\n]{2,15}、[^。！？\n]{2,15}、[^。！？\n]{2,15}(?:，以及|，再加上)[^。！？\n]{2,20}……"
)

# 5. High-confidence AI-slop tokens (empirically verified ~0 in authentic web novels)
PURE_SLOP_WORDS = (
    "指腹", "垂眸", "睨着", "斜叼", "沉腕",
    "指节泛白", "嘴角勾起一抹", "唇角勾起", "喉结上下滚动",
    "平淡无波", "没有半分起伏", "不见半点波澜", "毫无起伏",
    "淡声道", "缓声道", "漫天雨幕", "重重雨幕",
    "悄然收紧", "敏锐地捕捉", "翻涌着一股", "森然警告",
)

ABSTRACT_WRAPPER_RE = re.compile(
    r"(?:一股|一丝|一抹|一缕|一层|几分)[^，。！？\n]{0,8}"
    r"(?:的异样|的滞闷|的潮腥|的弧度|不易察觉的|难以名状的|说不清道不明的)"
)
ABSTRACT_WRAPPER_KEY = "抽象包装短语"

# 6. Bookend ending cliché: weather/ground description in final 2 paragraphs
WEATHER_ENDING_RE = re.compile(
    r"(?:雨还在下|风还在吹|雪还在落|夜色更深了|天色渐亮|青石[地板][^。！？\n]{0,12}发亮|阳光洒在[^。！？\n]{0,10}身上)"
)

# 7. Weather words for the opening-inventory check. Bare 雨/雪/风 are left out
# because they are common in names (林雪、秦风、苏雨).
OPENING_WEATHER_RE = re.compile(
    r"下(?:着|起|了)?[雨雪](?![伞衣具靴帽])|[大暴细冷夜急骤阵]雨|雨[水丝势幕]|雨声(?!音)"
    r"|[大暴积]雪|风雪|雪片|刮(?:着|起|了)?风|[寒冷北狂大朔]风|风声(?!音)"
    r"|天亮|夜色|晨光|返潮|阴沉|烈日|乌云|天色|日头"
)
OPENING_BIO_RE = re.compile(r"岁|短袍|衣衫|袖口|伤疤|旧疤|细腕|个头|身量|悬空")

# 8. Micro-action tails: 了一下/了一眼/叹了口气/看了眼. Not 停了下来、布下了阵法、悟了道.
MICRO_TIC_RE = re.compile(
    r"了(?:[一两三几半](?:下|阵|圈|声|眼|口|会)|下(?![来去午山面方属风落文场巴令])"
    r"|眼(?![眶睛珠皮神界色光前])|口气|声(?![音势名望誉色调])|会儿)"
)

# Non-metaphor nouns/usages containing '像'
NON_METAPHOR_XIANG_RE = re.compile(
    r"[神佛石雕塑铜泥木玉金银祖圣魔妖人画肖头图影录摄偶想迹不]像|像[素头样是而非]"
)


def strip_meta_sections(text: str) -> str:
    m = re.search(r"(?m)^(?:---\s*\n+)?##\s*附[：:]", text)
    if m:
        return text[: m.start()]
    return text


strip_quoted_and_system = packlib.strip_quoted


def strip_headings(text: str) -> str:
    return re.sub(r"(?m)^#{1,6}\s+.*$", "", text)


def split_paragraphs(text: str) -> list[str]:
    return [p.strip() for p in text.splitlines() if p.strip()]


def detect_first_person(narrative_paras: list[str], char_count: int) -> dict:
    """Detect first-person narration ('我') outside quotes."""
    wo_count = 0
    wo_paras = 0
    tp_count = 0
    samples: list[str] = []
    for p in narrative_paras:
        tp_count += p.count("他") + p.count("她")
        cleaned = IDIOM_WO_RE.sub("", p)
        cleaned = INDIRECT_WO_CLAUSE_RE.sub("", cleaned)
        c = cleaned.count("我")
        if c > 0:
            wo_count += c
            wo_paras += 1
            if len(samples) < 5:
                m = re.search(r"[^，。！？\n]{0,8}我[^，。！？\n]{0,10}", cleaned)
                if m:
                    samples.append(m.group(0).strip())
    wo_rate = round(wo_count * 1000.0 / max(1, char_count), 2)
    wo_para_share = round(wo_paras / max(1, len(narrative_paras)), 3)
    is_first_person = (
        (wo_count >= 3 and wo_rate >= 6.5)
        or (wo_paras >= 3 and wo_para_share >= 0.18)
        or (wo_count >= 3 and wo_count > tp_count)
    )
    return {
        "is_first_person": is_first_person,
        "unquoted_wo_count": wo_count,
        "unquoted_wo_per_1k": wo_rate,
        "first_person_para_share": wo_para_share,
        "samples": samples,
    }


def pov_of(text: str) -> dict:
    body = strip_headings(strip_meta_sections(text))
    chars = max(1, len(re.sub(r"\s", "", body)))
    return detect_first_person(split_paragraphs(packlib.strip_quoted(body)), chars)


def extract_long_narrative_clauses(text: str, min_len: int = 18) -> list[str]:
    """Extract unquoted narrative clauses >= min_len chars for lazy-copy detection."""
    raw = re.split(r"[。！？!?；;\n]+", packlib.strip_quoted(text))
    clauses: list[str] = []
    for s in raw:
        compact = re.sub(CLAUSE_PUNCT, "", s)
        if len(compact) >= min_len:
            clauses.append(compact)
    return clauses


def default_calibration() -> dict:
    return {
        "pack_name": None,
        "baseline": "default",
        "target_sent_mean": None,
        "max_sent_mean": 46.0,
        "min_sent_mean_warn": None,
        "runon_char_limit": 135,
        "runon_max_count": 3,
        "min_single_para_share": 0.15,
        "zhe_limit": DEFAULT_ZHE_LIMIT,
        "semicolons_per_1k": None,
        "slop_rates": {},
        "notes": [],
    }


def read_pack_corpus(pack_dir: Path) -> str | None:
    index = pack_dir / "corpus.jsonl"
    if not index.is_file():
        return None
    texts = [
        packlib.within(pack_dir, src["file"]).read_bytes().decode(src["encoding"])
        for src in packlib.read_rows(index)
    ]
    return "\n\n".join(texts) if texts else None


_CALIBRATION_CACHE: dict[tuple, dict] = {}


def _mtime(path: Path) -> int:
    return path.stat().st_mtime_ns if path.is_file() else 0


def load_pack_calibration(pack_path: str | Path | None) -> dict:
    """Load rhythm and habit baselines from a writing pack.

    Raises ValueError when the path is not a readable pack, instead of silently
    falling back to defaults.
    """
    if not pack_path:
        return default_calibration()
    p = Path(pack_path)
    stats_file = (p / "stats.json" if p.is_dir() else p).resolve()
    if not stats_file.is_file():
        raise ValueError(
            f"--pack 找不到写作包的 stats.json：{stats_file}。请检查写作包路径；"
            "不带正确的 --pack 时，检查结果没有按目标书校准"
        )
    key = (str(stats_file), _mtime(stats_file), _mtime(stats_file.parent / "corpus.jsonl"))
    if key not in _CALIBRATION_CACHE:
        _CALIBRATION_CACHE[key] = _build_calibration(stats_file)
    return copy.deepcopy(_CALIBRATION_CACHE[key])


def _build_calibration(stats_file: Path) -> dict:
    cal = default_calibration()
    try:
        data = json.loads(stats_file.read_text(encoding="utf-8-sig"))
        agg = data["aggregate"]
        float(agg["sentence_chars"]["mean"])
        manifest_file = stats_file.parent / "manifest.json"
        pack_name = None
        if manifest_file.is_file():
            pack_name = json.loads(manifest_file.read_text(encoding="utf-8-sig")).get("name")
    except (OSError, ValueError, TypeError, KeyError, LookupError) as exc:
        raise ValueError(f"无法读取写作包 {stats_file.parent}：{exc}") from exc
    try:
        corpus = read_pack_corpus(stats_file.parent)
    except (OSError, ValueError, KeyError, TypeError, LookupError):
        corpus = None
        cal["notes"].append("pack-corpus-unreadable: 写作包 corpus/ 读取失败，只能使用 stats.json 中的统计")
    if corpus is not None and not (agg.get("narrative_sentence_chars") or {}).get("count"):
        # Pack built by an older version: recompute with the current algorithm.
        agg = packlib.text_stats(corpus)

    nsc = agg.get("narrative_sentence_chars") or {}
    if nsc.get("count"):
        mean, p90 = float(nsc["mean"]), float(nsc["p90"])
        cal["baseline"] = "narrative_sentence_chars"
    else:
        sc = agg["sentence_chars"]
        mean, p90 = float(sc.get("mean") or 24.0), float(sc.get("p90") or 45.0)
        cal["baseline"] = "sentence_chars"
        cal["notes"].append(
            "pack-legacy-stats: 写作包 stats.json 是旧版且包内没有 corpus/，句长基线只能用含对白的全文句长近似；"
            "重新 init 或 add 一次可生成叙述句口径"
        )
    single_share = float((agg.get("paragraph_chars") or {}).get("single_sentence_share") or 0.40)
    cal.update({
        "pack_name": pack_name or stats_file.parent.name,
        "target_sent_mean": round(mean, 2),
        "max_sent_mean": round(max(30.0, mean * 1.45), 1),
        "min_sent_mean_warn": round(mean * 0.45, 1) if mean >= 28.0 else None,
        "runon_char_limit": max(95, int(round(max(p90 * 2.0, mean * 3.6)))),
        "runon_max_count": 3 if mean < 30.0 else 5,
        "min_single_para_share": round(max(0.12, min(0.22, single_share * 0.42)), 3),
    })
    if agg.get("zhe_per_1k") is not None:
        cal["zhe_limit"] = round(max(DEFAULT_ZHE_LIMIT, float(agg["zhe_per_1k"]) * 1.6), 1)
    if agg.get("narrative_semicolons_per_1k") is not None:
        cal["semicolons_per_1k"] = float(agg["narrative_semicolons_per_1k"])
    if corpus is not None:
        corpus_chars = max(1, len(re.sub(r"\s", "", corpus)))
        counts = {w: corpus.count(w) for w in PURE_SLOP_WORDS}
        counts[ABSTRACT_WRAPPER_KEY] = len(ABSTRACT_WRAPPER_RE.findall(corpus))
        # One stray occurrence is not a habit; two or more in the target's own text is.
        cal["slop_rates"] = {k: c / corpus_chars for k, c in counts.items() if c >= 2}
    return cal


def slop_allowance(cal: dict, key: str, char_count: int) -> int:
    """How often the target author's own corpus licenses a 'slop' word in this draft."""
    rate = cal["slop_rates"].get(key)
    if not rate:
        return 0
    return max(1, round(rate * char_count * 2))


def scan_text(
    text: str,
    source_text: str | None = None,
    pack_path: str | Path | None = None,
) -> dict:
    cal = load_pack_calibration(pack_path)
    body = strip_meta_sections(text)
    body_no_head = strip_headings(body)
    paras = split_paragraphs(body_no_head)
    compact = re.sub(r"\s", "", body_no_head)
    char_count = max(1, len(compact))

    narrative_only = packlib.strip_quoted(body_no_head)
    narrative_paras = split_paragraphs(narrative_only)

    blocking: list[str] = []
    warnings: list[str] = list(cal["notes"])

    # 0. HARD RED LINE: narration POV. New drafts are third person; rewrites follow the source.
    pov = detect_first_person(narrative_paras, char_count)
    src_pov = pov_of(source_text) if source_text else None
    if src_pov and src_pov["is_first_person"]:
        if not pov["is_first_person"]:
            blocking.append(
                f"pov-changed (原稿是第一人称叙述，引号外‘我’ {src_pov['unquoted_wo_count']} 处；"
                f"改写稿只剩 {pov['unquoted_wo_count']} 处): 改写时叙述人称必须跟随原稿，"
                "不得把第一人称原稿改成第三人称"
            )
    elif pov["is_first_person"]:
        reason = (
            "原稿是第三人称，改写稿不得改成第一人称‘我’" if source_text
            else "按纲要新写的正文必须使用第三人称叙述"
        )
        blocking.append(
            f"first-person-narration (引号外叙述正文出现‘我’ {pov['unquoted_wo_count']} 处, "
            f"{pov['unquoted_wo_per_1k']}/千字): {pov['samples']} -> {reason}"
        )

    # 1. Embodied simile & synesthetic clichés
    for pat in EMBODIED_SIMILE_PATTERNS:
        for m in re.finditer(pat, body_no_head):
            if "凌迟" in m.group(0):
                continue
            blocking.append(
                f"embodied-simile (AI生理/通感比喻腔): {m.group(0).strip()!r} -> "
                "严禁给寒意/疼痛/疲惫强加比喻修饰，改为直述情绪/反应并紧接具体利害判断或动作"
            )

    # 2. Dropped numeral mutation ("蹿起阵", "泛起股")
    for m in DROPPED_NUMERAL_RE.finditer(body_no_head):
        blocking.append(
            f"dropped-numeral (二修吞量词‘一’语病): {m.group(0)!r} -> 恢复正常现代汉语‘一’字或改用直接动词"
        )

    # 3. Semicolon abuse (1-2 in parallel vows are authentic; >=4 is essay slop),
    #    loosened when the target author uses semicolons in narration.
    semicolons = narrative_only.count("；")
    semicolon_limit = 3
    if cal["semicolons_per_1k"]:
        semicolon_limit = max(3, round(cal["semicolons_per_1k"] * char_count / 1000 * 2))
    hard_limit = 1 if (char_count < 800 and semicolon_limit == 3) else semicolon_limit
    if semicolons > hard_limit:
        blocking.append(
            f"semicolon-abuse: 叙述正文出现分号 {semicolons} 处（上限 {hard_limit}）"
            " -> 网文白话叙述忌论文式分号排比，请改为句号或逗号"
        )
    elif semicolons == semicolon_limit:
        warnings.append(f"semicolon: 叙述正文出现分号 {semicolons} 处，建议减少论文式分号")

    # 4. Elegant variation name-swapping (only block when repeated >= 3 times)
    ev_hits = [m.group(0) for m in ELEGANT_VARIATION_RE.finditer(body_no_head)]
    if len(ev_hits) >= 3:
        blocking.append(
            f"elegant-variation (AI换称呼油滑腔 {len(ev_hits)} 处): {ev_hits[:5]} -> 直接写人名或省去主语"
        )

    # 5. AI stage-summary & clue-recap lists
    for m in CLUE_RECAP_SUMMARY_RE.finditer(body_no_head):
        blocking.append(
            f"clue-recap-list (AI章末线索报菜名盘点): {m.group(0)!r} -> "
            "严禁在章末把前文已演过的线索罗列汇总一遍，直接落到核心决断或悬念上"
        )

    # 6. High-confidence AI-slop vocabulary & abstract wrappers.
    #    Words the target author really uses are allowed at the author's own frequency.
    slop_hits: list[str] = []
    allowed_by_pack: list[str] = []
    for w in PURE_SLOP_WORDS:
        c = body_no_head.count(w)
        if c:
            (slop_hits if c > slop_allowance(cal, w, char_count) else allowed_by_pack).append(f"{w}x{c}")
    wrappers = [m.group(0) for m in ABSTRACT_WRAPPER_RE.finditer(body_no_head)]
    if wrappers:
        if len(wrappers) > slop_allowance(cal, ABSTRACT_WRAPPER_KEY, char_count):
            slop_hits.extend(wrappers)
        else:
            allowed_by_pack.extend(wrappers)
    if slop_hits:
        blocking.append(f"llm-slop-vocab (高危AI腻子词 {len(slop_hits)} 项): {', '.join(slop_hits[:20])}")
    if allowed_by_pack:
        warnings.append(
            f"slop-vocab-allowed-by-pack (目标写作包原文也这样写，按原著频率放行): {', '.join(allowed_by_pack[:10])}"
        )

    # 7. Micro-action tic (「了一下/了一眼/叹了口气」高密度复读)
    micro_tics = [m.group(0) for m in MICRO_TIC_RE.finditer(body_no_head)]
    micro_rate = round(len(micro_tics) * 1000.0 / char_count, 2)
    if len(micro_tics) >= 10 and micro_rate >= 2.8:
        blocking.append(
            f"micro-action-tic (微动作尾巴复读 {len(micro_tics)} 处, {micro_rate}/千字): "
            f"{', '.join(micro_tics[:10])} -> 删掉高频‘了一下/了一眼’，直接写看到的物体或结果"
        )

    # 8. Camera-style action list (监控摄像头式连续琐碎动作罗列)
    camera_paras: list[str] = []
    cam_re = re.compile(
        r"伸手|抬手|拿起|拿过|取出|摸出|抓起|推开|打开|合上|放下|扯开|翻开|转身|回头|抬头|低头|弯腰|走到|走向|坐下|站起"
    )
    for p in paras:
        hits = cam_re.findall(p)
        if len(hits) >= 6 and p.count("，") >= 5:
            camera_paras.append(f"{p[:30]}... ({','.join(hits[:6])})")
    if camera_paras:
        blocking.append(
            f"camera-action-list (监控摄像头式动作清单 {len(camera_paras)} 段): "
            + " | ".join(camera_paras[:3])
            + " -> 砍掉无戏剧阻力的进门/伸手/低头/转身过场步骤"
        )

    # 9. Metaphor density check (advisory warning only; excludes 神像/佛像/不像/像是而非 etc.)
    cleaned_for_metaphor = NON_METAPHOR_XIANG_RE.sub("", body_no_head)
    metaphors = re.findall(r"好像|像是|仿佛|宛如|如同|犹如|像", cleaned_for_metaphor)
    metaphor_rate = round(len(metaphors) * 1000.0 / char_count, 2)
    if len(metaphors) >= 12 and metaphor_rate >= 5.5:
        warnings.append(
            f"metaphor-density (比喻偏密 {len(metaphors)} 处, {metaphor_rate}/千字): "
            "保留核心意象，其余建议直接写具体动作或判断"
        )

    # 10. Bookend weather ending cliché in final 2 paragraphs
    if len(paras) >= 4:
        tail_text = "\n".join(paras[-2:])
        m_weather = WEATHER_ENDING_RE.search(tail_text)
        if m_weather:
            blocking.append(
                f"bookend-weather-ending (章末写景首尾呼应打卡): {m_weather.group(0)!r} -> "
                "严禁在章末用天气/地面发亮收尾，请在悬念、决断或关键台词处果断停笔"
            )

    # 11. Long paragraph check (>240 chars, advisory warning)
    long_paras = [f"{p[:25]}...({len(p)}字)" for p in paras if len(p) > 240]
    if long_paras:
        warnings.append(
            f"long-paragraph: 存在 {len(long_paras)} 段超过240字，建议按镜头或思路切换自然分段: {', '.join(long_paras[:3])}"
        )

    # 12. Sentence & paragraph rhythm. Narration only, quotes removed, line breaks end a
    #     sentence: the same measure novel_pack.py stores as narrative_sentence_chars.
    narr_sents = packlib.narrative_sentence_lengths(narrative_only)
    sent_mean = round(statistics.mean(narr_sents), 2) if narr_sents else 0.0
    sent_sd = round(statistics.pstdev(narr_sents), 2) if len(narr_sents) > 1 else 0.0

    runon_limit = cal["runon_char_limit"]
    runon_sents = [
        s.strip()[:30] + "..."
        for s in re.split(r"[。！？!?\n]+", narrative_only)
        if len(re.sub(r"\s", "", s)) > runon_limit
    ]
    if len(runon_sents) > cal["runon_max_count"]:
        blocking.append(
            f"runon-comma-sentence (超过{runon_limit}字的一逗到底超长句 {len(runon_sents)} 处): "
            f"{' | '.join(runon_sents[:3])} -> 请在逻辑落点处自然断句"
        )

    para_lens = [len(re.sub(r"\s", "", p)) for p in paras]
    para_cv = (
        round(statistics.pstdev(para_lens) / max(1.0, statistics.mean(para_lens)), 2)
        if len(para_lens) > 1
        else 0.0
    )
    if len(para_lens) >= 12 and para_cv < 0.38:
        warnings.append(f"low-paragraph-burstiness: 段落长度过于均匀 (CV={para_cv} < 0.38)，建议拉开长短段落差")

    single_sent_paras = sum(1 for p in paras if len(packlib.sentence_pieces(p)) <= 1)
    single_para_share = round(single_sent_paras / max(1, len(paras)), 3)
    min_single = cal["min_single_para_share"]
    if len(paras) >= 18 and single_para_share < min_single:
        blocking.append(
            f"dense-paragraph-blocks (单句成段率过低 {single_para_share*100:.1f}% < {min_single*100:.0f}%): "
            "段落普遍堆成大豆腐块！网文应在关键定格、转折、动作落点处果断单句成段"
        )

    if len(narr_sents) >= 20 and sent_mean > cal["max_sent_mean"]:
        pack_hint = f"（目标包叙述句均值 {cal['target_sent_mean']} 字）" if cal["target_sent_mean"] else ""
        blocking.append(
            f"bloated-sentence-length (叙述句平均句长过长 {sent_mean} 字 > {cal['max_sent_mean']} 字{pack_hint}): "
            "分句粘连过密，请按目标写作包的呼吸节奏适当用句号断句"
        )
    elif len(narr_sents) >= 20 and cal["min_sent_mean_warn"] and sent_mean < cal["min_sent_mean_warn"]:
        warnings.append(
            f"style-too-choppy (叙述句平均句长 {sent_mean} 字明显短于目标包基线 {cal['target_sent_mean']} 字): "
            "句子切得过碎，注意复刻目标作者的长复句推演呼吸感，切勿写成碎句电报体"
        )

    # 13. Machine Opening Inventory & V+着 posture density
    if paras and not (paras[0].startswith("“") or paras[0].startswith("「") or paras[0].startswith("\"")):
        p0 = paras[0]
        has_weather = bool(OPENING_WEATHER_RE.search(p0))
        has_bio_inventory = len(OPENING_BIO_RE.findall(p0)) >= 2
        if has_weather and has_bio_inventory:
            blocking.append(
                "machine-opening-inventory (AI开篇天气环境+人物外貌简历同时堆砌): "
                f"{p0[:45]!r}... -> 严禁首段同时报天气与主角年龄/衣着/伤疤简历，请从冲突动作、判断或对话切入"
            )

    zhe_count = body_no_head.count("着")
    zhe_rate = round(zhe_count * 1000.0 / char_count, 2)
    zhe_limit = cal["zhe_limit"]
    if char_count >= 600 and zhe_rate > zhe_limit:
        zhe_samples = re.findall(r"[\u4e00-\u9fff]{1,2}着", body_no_head)[:12]
        blocking.append(
            f"excess-zhe-posture (伴随状态‘V着’泛滥 {zhe_count} 处, {zhe_rate}/千字 > {zhe_limit}/千字): "
            f"{', '.join(zhe_samples)} -> 删掉冗余的伴随状态词，改用干净的动作动词"
        )

    # 14. Rewrite Mode (--source): length sanity & verbatim long-clause copy check.
    # NOTE: Never penalize proper nouns, numbers, system panels, citations, or dialogue via n-grams!
    narrative_copy_rate = 0.0
    length_ratio = None
    if source_text:
        src_no_head = strip_headings(strip_meta_sections(source_text))
        src_compact = re.sub(r"\s", "", src_no_head)
        src_chars = max(1, len(src_compact))
        ratio = char_count / src_chars
        length_ratio = round(ratio, 2)
        diff = abs(char_count - src_chars)
        sizes = f"原稿{src_chars}字->改写{char_count}字"
        if ratio > LENGTH_INFLATION_LIMIT and diff >= LENGTH_MIN_DIFF:
            blocking.append(
                f"length-inflation (改写篇幅注水 {length_ratio}x > {LENGTH_INFLATION_LIMIT}x, {sizes}): "
                "严禁编造原稿没有的支线、路人或空洞感慨；只能补足原稿已有事件的动作、对白与利害推演"
            )
        elif ratio < LENGTH_SYNOPSIS_FLOOR and diff >= LENGTH_MIN_DIFF:
            blocking.append(
                f"length-synopsis (改写稿只剩原稿的 {length_ratio}x, {sizes}): 正文被压成了梗概。"
                "删废话可以，但每个剧情节点都要以场景写出来，不能一句带过"
            )
        elif ratio < LENGTH_SHRINK_NOTE and diff >= LENGTH_MIN_DIFF:
            warnings.append(
                f"length-shrink ({length_ratio}x, {sizes}): 篇幅删减较多。若删的是原稿废话属于正常改写；"
                "交稿前对照锁死清单，确认每个剧情事件、专名、数字与结局都还在"
            )

        dst_clauses = extract_long_narrative_clauses(body_no_head, min_len=18)
        src_narr = re.sub(CLAUSE_PUNCT, "", packlib.strip_quoted(src_no_head))
        copied = [c for c in dst_clauses if src_narr and c in src_narr]
        if dst_clauses:
            narrative_copy_rate = round(len(copied) / len(dst_clauses), 3)
        if len(compact) >= 35 and compact == src_compact:
            narrative_copy_rate = 1.0
            blocking.append("verbatim-narrative-copy (改写稿与原稿完全一致，未执行文风改写)")
        elif copied and narrative_copy_rate > COPY_RATE_LIMIT:
            sample_cp = [c[:22] + "..." for c in copied[:5]]
            blocking.append(
                f"verbatim-narrative-copy (引号外长叙述句原样照搬 {len(copied)}/{len(dst_clauses)} 句, "
                f"{narrative_copy_rate*100:.1f}% > {COPY_RATE_LIMIT*100:.1f}%): "
                f"{sample_cp} -> 保留人名/设定/关键台词即可，叙述分镜与句法结构必须按目标写作包文风重写"
            )

    return {
        "status": "FAIL" if blocking else "PASS",
        "pack_calibration": cal["pack_name"] or "default",
        "sentence_baseline": cal["baseline"],
        "chars": char_count,
        "paragraphs": len(paras),
        "single_para_share": single_para_share,
        "sentence_mean": sent_mean,
        "sentence_sd": sent_sd,
        "paragraph_cv": para_cv,
        "zhe_per_1k": zhe_rate,
        "unquoted_wo_count": pov["unquoted_wo_count"],
        "length_ratio": length_ratio,
        "narrative_copy_rate": narrative_copy_rate,
        "blocking_count": len(blocking),
        "blocking": blocking,
        "warnings": warnings,
    }


def read_text_file(path: str | Path) -> str:
    """Read UTF-8 or GB18030/GBK text (e.g. files saved by Chinese Windows Notepad)."""
    text, _ = packlib.decode(Path(path).read_bytes())
    return text


def write_report(value: dict, output: str | None) -> None:
    if output:
        out_path = Path(output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", help="Path to markdown/txt novel draft")
    parser.add_argument(
        "--source",
        help="Optional path to original draft (when rewriting) to check POV lock, length sanity & verbatim narrative copy",
    )
    parser.add_argument(
        "--pack",
        help="Optional path to target writing pack directory (or stats.json) for calibration; a wrong path is an error",
    )
    parser.add_argument("--output", help="Optional JSON file path to write full UTF-8 report")
    args = parser.parse_args(argv)
    try:
        src = read_text_file(args.source) if args.source else None
        res = scan_text(read_text_file(args.file), source_text=src, pack_path=args.pack)
    except (OSError, ValueError) as exc:
        err = {"status": "ERROR", "error": str(exc)}
        if args.output:
            # Full message goes to the UTF-8 report; the console line stays ASCII
            # so non-UTF-8 consoles (cp1252, gbk) cannot crash on it.
            write_report(err, args.output)
            print(f"STATUS=ERROR REPORT={json.dumps(str(Path(args.output)))} (see report for details)")
        else:
            print(json.dumps(err, ensure_ascii=False, indent=2))
        return 2
    if args.output:
        write_report(res, args.output)
        print(
            f"STATUS={res['status']} BLOCKING={res['blocking_count']} WARNINGS={len(res['warnings'])} "
            f"PACK={json.dumps(res['pack_calibration'])} SENT_MEAN={res['sentence_mean']} SENT_SD={res['sentence_sd']} "
            f"UNQUOTED_WO={res['unquoted_wo_count']} LENGTH_RATIO={res['length_ratio']} "
            f"NARR_COPY={res['narrative_copy_rate']}"
        )
    else:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    return 1 if res["status"] == "FAIL" else 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
