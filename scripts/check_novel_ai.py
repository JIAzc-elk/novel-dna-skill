#!/usr/bin/env python3
"""Deterministic quality, POV & AI-slop gate for Chinese web fiction drafts.

Key guarantees:
1. Hard-blocks first-person narration ("我...") outside quotes — Chinese web
   fiction strictly requires third-person narration.
2. Dynamically calibrates sentence/paragraph rhythm against the target writing
   pack's `stats.json` (via `--pack`), never forcing long-sentence authors
   (mean ~36 chars) into short-sentence moulds (mean ~21 chars).
3. In rewrite mode (`--source`), checks only lazy verbatim copying of long
   unquoted narrative clauses (>=18 chars) and POV preservation — never
   penalizing character names, place names, numbers, system prompts, classical
   quotes, or preserved dialogue via naive 3-gram/5-gram overlap.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import statistics
import sys

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

# 1. Embodied simile & synesthetic inflation clichés (blocking)
EMBODIED_SIMILE_PATTERNS = (
    r"(?:胸口|心口|心头|心脏|心底|后背|脊背|脑海|识海|胃里|喉咙|指尖)[^，。！？\n]{0,6}(?:像|好似|仿佛|如同|宛如)[^，。！？\n]{0,10}(?:针|刺|刀|剑|锤|铁手|大手|冰水|冷水|电流|雷击|火烧)",
    r"(?<!凌迟)[^，。！？\n]{1,6}(?:似的|般的|一样的|般)[^，。！？\n]{0,3}(?:凉意|寒意|冷意|剧痛|刺痛|钝痛|疲惫|倦意|窒息|压迫感|死寂|恐惧|绝望|耻辱)",
)

# 2. Second-pass evasion ungrammatical mutations (dropped '一' before classifier)
DROPPED_NUMERAL_RE = re.compile(
    r"(?:蹿起|窜起|涌起|升起|泛起|透出|掠过|闪过|浮现|带着)(?!一)[阵股丝抹缕层]"
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

# 6. Bookend ending cliché: weather/ground description in final 2 paragraphs
WEATHER_ENDING_RE = re.compile(
    r"(?:雨还在下|风还在吹|雪还在落|夜色更深了|天色渐亮|青石[地板][^。！？\n]{0,12}发亮|阳光洒在[^。！？\n]{0,10}身上)"
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


def strip_quoted_and_system(text: str) -> str:
    """Strip dialogue/thought quotes and 【...】 system panels per paragraph.

    Resetting quote depth at paragraph boundaries prevents a single unclosed
    typographic quote from swallowing the remainder of the chapter.
    """
    out_lines: list[str] = []
    pairs = {"“": "”", "‘": "’", "「": "」", "『": "』", "【": "】"}
    closers = set(pairs.values())
    for line in text.splitlines():
        buf: list[str] = []
        stack: list[str] = []
        for ch in line:
            if ch in pairs:
                stack.append(pairs[ch])
            elif ch in closers:
                if stack and stack[-1] == ch:
                    stack.pop()
                elif ch in stack:
                    while stack and stack[-1] != ch:
                        stack.pop()
                    if stack:
                        stack.pop()
            elif not stack:
                buf.append(ch)
        cleaned = re.sub(r'"[^"]*"', "", "".join(buf))
        out_lines.append(cleaned)
    return "\n".join(out_lines)


def detect_first_person(narrative_paras: list[str], char_count: int) -> dict:
    """Detect prohibited first-person narration ('我') outside quotes."""
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


def extract_long_narrative_clauses(text: str, min_len: int = 18) -> list[str]:
    """Extract unquoted narrative clauses >= min_len chars for lazy-copy detection."""
    no_quotes = strip_quoted_and_system(text)
    raw = re.split(r"[。！？!?；;\n]+", no_quotes)
    clauses: list[str] = []
    for s in raw:
        compact = re.sub(r"[\s，、：:（）()《》—…,.!\-]", "", s)
        if len(compact) >= min_len:
            clauses.append(compact)
    return clauses


def load_pack_calibration(pack_path: str | Path | None) -> dict:
    """Load sentence/paragraph rhythm baselines from a pack's stats.json."""
    default = {
        "pack_name": None,
        "target_sent_mean": None,
        "max_sent_mean": 46.0,
        "min_sent_mean_warn": None,
        "runon_char_limit": 135,
        "runon_max_count": 3,
        "min_single_para_share": 0.15,
    }
    if not pack_path:
        return default
    p = Path(pack_path)
    stats_file = p / "stats.json" if p.is_dir() else p
    if not stats_file.is_file():
        return default
    try:
        data = json.loads(stats_file.read_text(encoding="utf-8-sig"))
        agg = data.get("aggregate", {})
        sc = agg.get("sentence_chars", {})
        pc = agg.get("paragraph_chars", {})
        mean = float(sc.get("mean") or 24.0)
        p90 = float(sc.get("p90") or 45.0)
        single_share = float(pc.get("single_sentence_share") or 0.40)
        manifest_file = stats_file.parent / "manifest.json"
        pack_name = None
        if manifest_file.is_file():
            pack_name = json.loads(manifest_file.read_text(encoding="utf-8-sig")).get("name")
        return {
            "pack_name": pack_name or stats_file.parent.name,
            "target_sent_mean": round(mean, 2),
            "max_sent_mean": round(max(30.0, mean * 1.45), 1),
            "min_sent_mean_warn": round(mean * 0.45, 1) if mean >= 28.0 else None,
            "runon_char_limit": max(95, int(round(max(p90 * 2.0, mean * 3.6)))),
            "runon_max_count": 3 if mean < 30.0 else 5,
            "min_single_para_share": round(max(0.12, min(0.22, single_share * 0.42)), 3),
        }
    except (OSError, ValueError, TypeError):
        return default


def scan_text(
    text: str,
    source_text: str | None = None,
    pack_path: str | Path | None = None,
) -> dict:
    cal = load_pack_calibration(pack_path)
    body = strip_meta_sections(text)
    body_no_head = re.sub(r"(?m)^#{1,6}\s+.*$", "", body)
    paras = [p.strip() for p in body_no_head.splitlines() if p.strip()]
    compact = re.sub(r"\s", "", body_no_head)
    char_count = max(1, len(compact))

    narrative_only = strip_quoted_and_system(body_no_head)
    narrative_paras = [p.strip() for p in narrative_only.splitlines() if p.strip()]

    blocking: list[str] = []
    warnings: list[str] = []

    # 0. HARD RED LINE: Prohibit first-person narration ("我") in web fiction
    pov = detect_first_person(narrative_paras, char_count)
    if pov["is_first_person"]:
        blocking.append(
            f"first-person-narration (网文严禁第一人称叙事！引号外叙述正文出现‘我’ "
            f"{pov['unquoted_wo_count']} 处, {pov['unquoted_wo_per_1k']}/千字): "
            f"{pov['samples']} -> 中文网络小说正文必须全程使用第三人称叙述，绝对禁止使用或改为第一人称‘我’！"
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

    # 3. Semicolon abuse check (1-2 semicolons in parallel vows are authentic; >=4 is essay slop)
    semicolons = narrative_only.count("；")
    if semicolons >= 4 or (semicolons >= 2 and char_count < 800):
        blocking.append(
            f"semicolon-abuse: 叙述正文出现分号 {semicolons} 处 -> 网文白话叙述忌论文式分号排比，请改为句号或逗号"
        )
    elif semicolons == 3:
        warnings.append("semicolon: 叙述正文出现分号 3 处，建议减少论文式分号")

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

    # 6. High-confidence AI-slop vocabulary & abstract wrappers
    slop_hits: list[str] = []
    for w in PURE_SLOP_WORDS:
        c = body_no_head.count(w)
        if c > 0:
            slop_hits.append(f"{w}x{c}")
    for m in ABSTRACT_WRAPPER_RE.finditer(body_no_head):
        slop_hits.append(m.group(0))
    if slop_hits:
        blocking.append(f"llm-slop-vocab (高危AI腻子词 {len(slop_hits)} 项): {', '.join(slop_hits[:20])}")

    # 7. Micro-action tic (「了一下/了一眼/了一口气」高密度复读)
    micro_tics = re.findall(r"了(?:[一两三几半])?[下阵圈道声眼口气会]", body_no_head)
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

    # 12. Sentence & Paragraph Rhythm (dynamically calibrated to --pack when provided)
    # Match novel_pack.py sentence splitting ([。！？!?]+) and evaluate narrative sentences
    # outside short dialogue lines so dialogue-rich chapters are never falsely flagged as choppy.
    sents = [len(re.sub(r"[\s“”‘’「」『』]", "", x)) for x in re.split(r"[。！？!?]+", body_no_head) if re.sub(r"[\s“”‘’「」『』]", "", x)]
    narr_sents = [len(re.sub(r"\s", "", x)) for x in re.split(r"[。！？!?]+", narrative_only) if len(re.sub(r"\s", "", x)) >= 2]
    sent_mean = round(statistics.mean(narr_sents), 2) if narr_sents else (round(statistics.mean(sents), 2) if sents else 0.0)
    sent_sd = round(statistics.pstdev(sents), 2) if len(sents) > 1 else 0.0

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

    single_sent_paras = sum(
        1 for p in paras if len([x for x in re.split(r"[。！？!?]+", p) if x.strip()]) <= 1
    )
    single_para_share = round(single_sent_paras / max(1, len(paras)), 3)
    min_single = cal["min_single_para_share"]
    if len(paras) >= 18 and single_para_share < min_single:
        blocking.append(
            f"dense-paragraph-blocks (单句成段率过低 {single_para_share*100:.1f}% < {min_single*100:.0f}%): "
            "段落普遍堆成大豆腐块！网文应在关键定格、转折、动作落点处果断单句成段"
        )

    if len(sents) >= 20 and sent_mean > cal["max_sent_mean"]:
        pack_hint = f"（目标包均值 {cal['target_sent_mean']} 字）" if cal["target_sent_mean"] else ""
        blocking.append(
            f"bloated-sentence-length (平均句长过长 {sent_mean} 字 > {cal['max_sent_mean']} 字{pack_hint}): "
            "分句粘连过密，请按目标写作包的呼吸节奏适当用句号断句"
        )
    elif len(sents) >= 20 and cal["min_sent_mean_warn"] and sent_mean < cal["min_sent_mean_warn"]:
        warnings.append(
            f"style-too-choppy (平均句长 {sent_mean} 字明显短于目标包基线 {cal['target_sent_mean']} 字): "
            "句子切得过碎，注意复刻目标作者的长复句推演呼吸感，切勿写成碎句电报体"
        )

    # 13. Machine Opening Inventory & V+着 posture density
    if paras and not (paras[0].startswith("“") or paras[0].startswith("「") or paras[0].startswith("\"")):
        p0 = paras[0]
        has_weather = bool(re.search(r"雨|雪|风|天亮|夜色|晨光|返潮|阴沉|烈日", p0))
        has_bio_inventory = len(re.findall(r"岁|短袍|衣衫|袖口|伤疤|旧疤|细腕|个头|身量|悬空", p0)) >= 2
        if has_weather and has_bio_inventory:
            blocking.append(
                "machine-opening-inventory (AI开篇天气环境+人物外貌简历同时堆砌): "
                f"{p0[:45]!r}... -> 严禁首段同时报天气与主角年龄/衣着/伤疤简历，请从冲突动作、判断或对话切入"
            )

    zhe_count = body_no_head.count("着")
    zhe_rate = round(zhe_count * 1000.0 / char_count, 2)
    if char_count >= 600 and zhe_rate > 11.5:
        zhe_samples = re.findall(r"[\u4e00-\u9fff]{1,2}着", body_no_head)[:12]
        blocking.append(
            f"excess-zhe-posture (伴随状态‘V着’泛滥 {zhe_count} 处, {zhe_rate}/千字 > 11.5/千字): "
            f"{', '.join(zhe_samples)} -> 删掉冗余的伴随状态词，改用干净的动作动词"
        )

    # 14. Rewrite Mode (--source): POV Lock, Length Sanity & Verbatim Long-Sentence Copy Check
    # NOTE: Never penalize proper nouns, numbers, system panels, citations, or dialogue via n-grams!
    narrative_copy_rate = 0.0
    if source_text:
        src_body = strip_meta_sections(source_text)
        src_no_head = re.sub(r"(?m)^#{1,6}\s+.*$", "", src_body)
        src_chars = max(1, len(re.sub(r"\s", "", src_no_head)))
        inflation_ratio = round(char_count / src_chars, 2)
        if char_count >= 1500 and inflation_ratio > 1.60:
            blocking.append(
                f"length-inflation (改写篇幅注水膨胀率过高 {inflation_ratio}x > 1.60x, 原稿{src_chars}字->改写{char_count}字): "
                "严禁编造原稿没有的废话注水，请精简冗余铺垫（目标 0.85~1.45x）"
            )
        elif char_count >= 500 and src_chars >= 1500 and inflation_ratio < 0.78:
            blocking.append(
                f"length-truncation (改写篇幅缩水过多 {inflation_ratio}x < 0.78x, 原稿{src_chars}字->改写{char_count}字): "
                "删减过多原稿细节，请完整保留原稿事件、环境质感与交锋细节（目标 0.85~1.45x）"
            )

        dst_clauses = extract_long_narrative_clauses(body_no_head, min_len=18)
        src_compact = re.sub(
            r"[\s，、：:（）()《》—…,.!\-]", "", strip_quoted_and_system(src_no_head)
        )
        if dst_clauses and src_compact:
            copied = [c for c in dst_clauses if c in src_compact]
            narrative_copy_rate = round(len(copied) / len(dst_clauses), 3)
            if len(dst_clauses) >= 5 and narrative_copy_rate > 0.35:
                sample_cp = [c[:22] + "..." for c in copied[:5]]
                blocking.append(
                    f"verbatim-narrative-copy (引号外长叙述句原样照搬比例过高 {narrative_copy_rate*100:.1f}% > 35.0%): "
                    f"{sample_cp} -> 保留人名/设定/关键台词即可，叙述分镜与句法结构必须按目标写作包文风重写"
                )
            elif len(dst_clauses) <= 2 and len(compact) >= 35 and compact == re.sub(r"\s", "", src_no_head):
                blocking.append(
                    "verbatim-narrative-copy: 改写稿与原稿完全一致，未执行文风改写"
                )
            elif len(dst_clauses) == 1 and len(copied) == 1:
                blocking.append(
                    f"verbatim-narrative-copy (长叙述句原样照搬未改写): {copied[0][:24]!r}... -> 请按目标写作包文风重写叙述句式"
                )

    return {
        "status": "FAIL" if blocking else "PASS",
        "pack_calibration": cal["pack_name"] or "default",
        "chars": char_count,
        "paragraphs": len(paras),
        "single_para_share": single_para_share,
        "sentence_mean": sent_mean,
        "sentence_sd": sent_sd,
        "paragraph_cv": para_cv,
        "zhe_per_1k": zhe_rate,
        "unquoted_wo_count": pov["unquoted_wo_count"],
        "narrative_copy_rate": narrative_copy_rate,
        "blocking_count": len(blocking),
        "blocking": blocking,
        "warnings": warnings,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", help="Path to markdown/txt novel draft")
    parser.add_argument(
        "--source",
        help="Optional path to original draft (when rewriting) to check POV lock, length sanity & verbatim narrative copy",
    )
    parser.add_argument(
        "--pack",
        help="Optional path to target writing pack directory (or stats.json) for dynamic sentence-rhythm calibration",
    )
    parser.add_argument("--output", help="Optional JSON file path to write full UTF-8 report")
    args = parser.parse_args(argv)
    p = Path(args.file).resolve()
    src = Path(args.source).resolve().read_text(encoding="utf-8-sig") if args.source else None
    res = scan_text(p.read_text(encoding="utf-8-sig"), source_text=src, pack_path=args.pack)
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(
            f"STATUS={res['status']} BLOCKING={res['blocking_count']} WARNINGS={len(res['warnings'])} "
            f"PACK={res['pack_calibration']} SENT_MEAN={res['sentence_mean']} SENT_SD={res['sentence_sd']} "
            f"UNQUOTED_WO={res['unquoted_wo_count']} NARR_COPY={res['narrative_copy_rate']}"
        )
    else:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    return 1 if res["status"] == "FAIL" else 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
