#!/usr/bin/env python3
"""Deterministic AIGC / AI-slop scanner for Chinese web fiction drafts.

Calibrated against 193 authentic chapters of 《蛊真人》 (611k chars) and 5,584
human web-novel chapters so that real human web fiction passes cleanly while
LLM structural/syntactic slop, embodied simile clichés, second-pass evasion
mutations, and source-AI overlap are deterministically blocked.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import statistics
import sys

# 1. Embodied simile & synesthetic inflation clichés (blocking)
# Catches both direct similes ("胸口像被冷针扎了一下") and mutated evasion forms
# ("心口蹿起阵针扎似的凉意", "潮水般的疲惫感") that humans never write in web fiction.
EMBODIED_SIMILE_PATTERNS = (
    r"(?:胸口|心口|心头|心脏|心底|后背|脊背|脑海|识海|胃里|喉咙|指尖)[^，。！？\n]{0,6}(?:像|好似|仿佛|如同|宛如)[^，。！？\n]{0,10}(?:针|刺|刀|剑|锤|铁手|大手|冰水|冷水|电流|雷击|火烧)",
    r"(?<!凌迟)[^，。！？\n]{1,6}(?:似的|般的|一样的|般)[^，。！？\n]{0,3}(?:凉意|寒意|冷意|剧痛|刺痛|钝痛|疲惫|倦意|窒息|压迫感|死寂|恐惧|绝望|耻辱)",
    r"像(?:是)?撞上[^，。！？\n]{0,8}看不见的[^，。！？\n]{1,6}",
    r"像吸饱了?水的馒头",
)

# 2. Second-pass evasion ungrammatical mutations (dropped '一' before classifier)
# Happens when an LLM deletes '一' to dodge a regex (e.g. "蹿起阵", "泛起股", "带着股").
DROPPED_NUMERAL_RE = re.compile(
    r"(?:蹿起|窜起|涌起|升起|泛起|透出|掠过|闪过|浮现|带着)(?!一)[阵股丝抹缕层]"
)

# 3. Elegant variation name-swapping (换称呼油滑症)
ELEGANT_VARIATION_PATTERNS = (
    r"这位(?:管事|少爷|少年|老者|猎户|掌柜|修士|道人|姑娘|丫鬟|账童)",
    r"那名(?:清瘦|年轻|年老|青衣|黑衣|灰衣)(?:道人|修士|少年|猎户|女子|汉子)",
    r"(?:高凳|书案|柜台|门槛|窗)上的(?:少年|老者|男子|少女)",
    r"身边的?(?:账童|少年|小厮|随从)",
)

# 4. Paragraph signpost openers & AI recap-summary lists (段首过渡/总结帽与章末线索报菜名盘点)
PARAGRAPH_SIGNPOST_OPENERS = (
    r"^(?:这种|这类)[^，。！？\n]{1,12}(?:根本)?算不上",
    r"^这话里已经带上了",
    r"^这便是他[^，。！？\n]{1,18}的(?:真正)?底牌",
    r"^一墙之隔，",
    r"^正思忖间，",
    r"^念及此处，",
    r"^一念及此，",
    r"^不过转念一想，",
    r"^眼下这[^，。！？\n]{1,12}也是一样的",
    r"^话答得极顺，",
    r"^嘴上应得",
)

CLUE_RECAP_SUMMARY_RE = re.compile(
    r"(?:线索|信息|事情|账目)[^。！？\n]{0,12}(?:梳理|盘点|汇总|过)了一遍|[^。！？\n]{2,15}、[^。！？\n]{2,15}、[^。！？\n]{2,15}(?:，以及|，再加上|，还有)[^。！？\n]{2,20}……"
)

# 5. Pure AI-slop tokens that do NOT appear in authentic web fiction
PURE_SLOP_WORDS = (
    "指腹", "探手", "垂眸", "睨着", "斜叼", "沉腕",
    "指节泛白", "嘴角勾起", "心头一凛", "神色一肃", "眼皮猛地一跳",
    "通禀", "腰悬", "展袖", "敛去", "散逸", "莅临", "负手",
    "平淡无波", "没有半分起伏", "不见半点波澜", "毫无起伏",
    "淡声道", "缓声道", "重得惊人", "绵软发潮", "像极了",
    "伶仃", "应声碎裂", "隐隐透着", "清冽", "漫天雨幕", "重重雨幕",
    "似有所察", "没有半点要停的意思", "留样备查",
    "大放异彩", "盖棺定论", "豁然开朗", "难以抑制", "鸦雀无声",
    "悄然收紧", "敏锐地捕捉", "翻涌着一股", "森然警告",
)

# Abstract wrapper ("一股难以名状的...", "一丝不易察觉的...")
# Note: natural "一股寒意" / "一阵寒意" and cultivation "气息" are explicitly allowed!
ABSTRACT_WRAPPER_RE = re.compile(
    r"(?:一股|一丝|一抹|一缕|一层|几分)[^，。！？\n]{0,8}(?:的异样|的气机|的神采|的滞闷|的潮腥|的弧度|的波动|不易察觉的|难以名状的|说不清的)"
)

# Bookend ending cliché: hiding/touching a prop + weather/ground description in final 2 paragraphs
WEATHER_ENDING_RE = re.compile(
    r"(?:雨还在下|风还在吹|雪还在落|夜色更深|天色渐亮|青石地[^。！？\n]{0,12}发亮|阳光洒在)"
)


def strip_meta_sections(text: str) -> str:
    m = re.search(r"(?m)^(?:---\s*\n+)?##\s*附[：:]", text)
    if m:
        return text[: m.start()]
    return text


def extract_char_ngrams(text: str, n: int = 5) -> set[str]:
    no_quotes = re.sub(r"[“「\"][^”」\"]*[”」\"]", "", text)
    clauses = re.split(r"[\s，。！？、：；（）《》—…,.!?\"'()\-]+", no_quotes)
    grams: set[str] = set()
    for c in clauses:
        if len(c) >= n:
            for i in range(len(c) - n + 1):
                grams.add(c[i : i + n])
    return grams


def scan_text(text: str, source_text: str | None = None) -> dict:
    body = strip_meta_sections(text)
    body_no_head = re.sub(r"(?m)^#{1,6}\s+.*$", "", body)
    paras = [p.strip() for p in body_no_head.splitlines() if p.strip()]
    compact = re.sub(r"\s", "", body_no_head)
    char_count = max(1, len(compact))

    blocking = []
    warnings = []

    # 1. Embodied simile & synesthetic clichés
    for pat in EMBODIED_SIMILE_PATTERNS:
        for m in re.finditer(pat, body_no_head):
            blocking.append(
                f"embodied-simile (AI生理/通感比喻腔): {m.group(0)!r} -> 严禁给寒意/疼痛/疲惫加比喻修饰，改为直写‘心中猛地升起一股寒意’或直接写心底算计"
            )

    # 2. Dropped numeral mutation ("蹿起阵", "泛起股")
    for m in DROPPED_NUMERAL_RE.finditer(body_no_head):
        blocking.append(
            f"dropped-numeral (二修吞量词‘一’怪句): {m.group(0)!r} -> 恢复正常现代汉语‘一’字或改用直接动词"
        )

    # 3. Semicolon check (in narrative outside quotes)
    narrative_only = re.sub(r"[“「\"][^”」\"]*[”」\"]", "", body_no_head)
    semicolons = narrative_only.count("；")
    if semicolons > 0:
        blocking.append(f"semicolon: 叙述正文出现分号 {semicolons} 处（网文白话叙述禁用分号，拆为句号或逗号）")

    # 4. Elegant variation name-swapping
    for pat in ELEGANT_VARIATION_PATTERNS:
        for m in re.finditer(pat, body_no_head):
            blocking.append(f"elegant-variation (AI换称呼腔): {m.group(0)!r} -> 直接写人名或省主语")

    # 5. Paragraph signpost openers & AI clue-recap lists
    for p in paras:
        for pat in PARAGRAPH_SIGNPOST_OPENERS:
            m = re.search(pat, p)
            if m:
                blocking.append(f"signpost-opener (段首路标句): {m.group(0)!r}")
    for m in CLUE_RECAP_SUMMARY_RE.finditer(body_no_head):
        blocking.append(
            f"clue-recap-list (AI章末线索报菜名盘点，朱雀0.99高危项): {m.group(0)!r} -> "
            "严禁在章末把前文发生过的线索用顿号罗列梳理一遍！直接抓住最致命的一件事切入心理决断"
        )

    # 6. Pure AI-slop vocabulary & abstract wrappers
    slop_hits = []
    for w in PURE_SLOP_WORDS:
        c = body_no_head.count(w)
        if c > 0:
            slop_hits.append(f"{w}x{c}")
    for m in ABSTRACT_WRAPPER_RE.finditer(body_no_head):
        slop_hits.append(m.group(0))
    if slop_hits:
        blocking.append(f"llm-slop-vocab (高危AI腻子词 {len(slop_hits)} 项): {', '.join(slop_hits[:20])}")

    # 7. Micro-action tic (「了一下/了一眼/了两下」轻量补语高密度复读)
    micro_tics = re.findall(r"了(?:[一两三几半])?[下阵圈道声眼口气会]", body_no_head)
    micro_rate = round(len(micro_tics) * 1000.0 / char_count, 2)
    if len(micro_tics) >= 8 and micro_rate >= 2.2:
        blocking.append(
            f"micro-action-tic (微动作尾巴复读 {len(micro_tics)} 处, {micro_rate}/千字): "
            f"{', '.join(micro_tics[:10])} -> 删掉‘了一下/了一眼’，直接写看到的物体或结果"
        )

    # 8. Camera-style action list (监控摄像头式连续动作罗列)
    camera_paras = []
    cam_re = re.compile(r"伸手|抬手|拿起|拿过|取出|摸出|抓起|推开|打开|合上|放下|扯开|翻开|转身|回头|抬头|低头|弯腰|走到|走向|坐下|站起")
    for p in paras:
        hits = cam_re.findall(p)
        if len(hits) >= 5 and p.count("，") >= 4:
            camera_paras.append(f"{p[:30]}... ({','.join(hits[:6])})")
    if camera_paras:
        blocking.append(
            f"camera-action-list (监控摄像头式动作清单 {len(camera_paras)} 段): "
            + " | ".join(camera_paras[:3])
            + " -> 砍掉无阻碍的进门/伸手/低头/转身步骤，顺着人物注意力和事理写"
        )

    # 9. Metaphor density check
    metaphors = re.findall(r"好像|像是|仿佛|宛如|如同|犹如|(?<![不头图画影录摄肖])像(?![头像素])", body_no_head)
    metaphor_rate = round(len(metaphors) * 1000.0 / char_count, 2)
    if len(metaphors) >= 7 and metaphor_rate >= 2.2:
        warnings.append(
            f"metaphor-density (比喻标记偏密 {len(metaphors)} 处, {metaphor_rate}/千字): "
            "保留核心比喻，其余改为直接写具体现象"
        )

    # 10. Bookend weather ending cliché in final 2 paragraphs
    if len(paras) >= 4:
        tail_text = "\n".join(paras[-2:])
        m_weather = WEATHER_ENDING_RE.search(tail_text)
        if m_weather:
            blocking.append(
                f"bookend-weather-ending (章末写景首尾呼应打卡): {m_weather.group(0)!r} -> 严禁在章末写天气/地面景物收尾，必须在悬念、决断或对话处直接停笔"
            )

    # 11. Long paragraph check (>200 chars)
    long_paras = [f"{p[:25]}...({len(p)}字)" for p in paras if len(p) > 200]
    if long_paras:
        warnings.append(f"long-paragraph: 存在 {len(long_paras)} 段超过200字，建议按镜头或思路切换自然断段: {', '.join(long_paras[:3])}")

    # 12. Sentence length stats (Realistic web-novel baseline: SD 9.0 ~ 22.0, no artificial 100-char run-on requirement!)
    sents = [len(re.sub(r"\s", "", x)) for x in re.split(r"[。！？!?\n]+", body_no_head) if x.strip()]
    sent_sd = round(statistics.pstdev(sents), 2) if len(sents) > 1 else 0.0
    runon_sents = [s.strip()[:30] + "..." for s in re.split(r"[。！？!?\n]+", narrative_only) if len(re.sub(r"\s", "", s)) > 85]
    if len(runon_sents) >= 2:
        blocking.append(
            f"runon-comma-sentence (超过85字的一逗到底畸形长句 {len(runon_sents)} 处): {' | '.join(runon_sents[:3])} -> 正常网文句长SD为10~16，严禁为刷SD把多句话用逗号强行粘连"
        )

    para_lens = [len(re.sub(r"\s", "", p)) for p in paras]
    para_cv = round(statistics.pstdev(para_lens) / max(1.0, statistics.mean(para_lens)), 2) if len(para_lens) > 1 else 0.0
    if len(para_lens) >= 10 and para_cv < 0.45:
        warnings.append(f"low-paragraph-burstiness: 段落长度过于均匀 (CV={para_cv} < 0.45)，需拉开短段与长段的落差")

    # 13. Anti-AI-Roleplay Register Calibration (Calibrated against 193 chapters of GuZhenRen, >95% pass)
    # (0) Machine Opening Inventory Check (StoryScope §6 & discourse-pass §4: strongest AI opening tell)
    if paras and not (paras[0].startswith("“") or paras[0].startswith("「") or paras[0].startswith("\"")):
        p0 = paras[0]
        has_weather = bool(re.search(r"雨|雪|风|天亮|夜色|晨光|返潮|阴沉|烈日", p0))
        has_bio_inventory = len(re.findall(r"岁|短袍|衣衫|袖口|伤疤|旧疤|細腕|细腕|个头|身量|悬空|悬着", p0)) >= 2
        if has_weather and has_bio_inventory:
            blocking.append(
                "machine-opening-inventory (AI开篇天气环境+人物外貌简历打卡): "
                f"{p0[:45]!r}... -> 严禁第一段同时堆砌天气环境与主角年龄/衣着/伤疤！请直接以对话“……”、内心算账或具体冲突开场（StoryScope最强真人标记），外貌细节延后随动作带出"
            )

    # (a) V+着 continuous-posture inflation (human mean 3.6/1k vs AI 13.5/1k)
    zhe_count = body_no_head.count("着")
    zhe_rate = round(zhe_count * 1000.0 / char_count, 2)
    if char_count >= 500 and zhe_rate > 9.8:
        zhe_samples = re.findall(r"[\u4e00-\u9fff]{1,2}着", body_no_head)[:12]
        blocking.append(
            f"excess-zhe-posture (AI伴随状态‘V着’泛滥 {zhe_count} 处, {zhe_rate}/千字 > 9.8/千字): "
            f"{', '.join(zhe_samples)} -> 删掉无意义的‘敞着/叼着/盯着/看着/透着/藏着’，改为干净的有限动词（目标 < 6.0/千字）"
        )

    # (b) Fake-folksy northern dialect roleplay (今儿/昨儿/里头/后头/瞅着/瞧着: ~0 in 611k GuZhenRen)
    folksy_words = ("今儿", "昨儿", "明儿", "里头", "外头", "后头", "前头", "瞅着", "瞧着", "瞧见", "这会儿", "那会儿", "心眼儿", "一块儿", "压根", "犯不着", "麻利")
    folksy_hits = []
    for fw in folksy_words:
        fc = body_no_head.count(fw)
        if fc > 0:
            folksy_hits.append(f"{fw}x{fc}")
    folksy_total = sum(body_no_head.count(fw) for fw in folksy_words)
    if folksy_total >= 3:
        blocking.append(
            f"fake-folksy-dialect (AI伪市井儿化/方言腔 {folksy_total} 处): {', '.join(folksy_hits)} -> "
            "《蛊真人》等主流网文使用标准现代白话（今日/今天/昨日/里面/后面/看着/此刻），严禁靠‘今儿/里头/瞅着’装活人"
        )

    # (c) Plain modern Chinese function-word starvation (Human mean 9.5/1k vs AI 0.9/1k)
    plain_connectives = ("但是", "因为", "虽然", "这个", "这些", "现在", "可以", "这是", "都是", "他的", "已经", "什么", "如果", "所以", "而且", "不过")
    plain_count = sum(body_no_head.count(pw) for pw in plain_connectives)
    plain_rate = round(plain_count * 1000.0 / char_count, 2)
    if char_count >= 800 and plain_rate < 4.5:
        blocking.append(
            f"plain-function-starvation (现代白话基础虚词严重缺失 {plain_rate}/千字 < 4.5/千字): "
            "模型在写高密度压缩伪古风！必须恢复自然现代汉语虚词（但是/因为/虽然/这个/这些/现在/可以/这是/他的/已经，目标 7~12/千字）"
        )

    # (d) Paragraph & Sentence Rhythm (Single-sentence paragraph share & mean sentence length)
    sent_mean = round(statistics.mean(sents), 2) if sents else 0.0
    single_sent_paras = sum(
        1 for p in paras if len([x for x in re.split(r"[。！？!?]+", p) if x.strip()]) <= 1
    )
    single_para_share = round(single_sent_paras / max(1, len(paras)), 3)
    if len(paras) >= 15 and single_para_share < 0.22:
        blocking.append(
            f"dense-paragraph-blocks (单句成段率过低 {single_para_share*100:.1f}% < 22%): "
            "段落全部塞满3~5句像散文豆腐块！网文必须在关键动作、转折、判断处大量使用独立单句成段（目标 35%~60%）"
        )
    if len(sents) >= 20 and sent_mean > 25.0:
        blocking.append(
            f"bloated-sentence-length (平均句长过长 {sent_mean} 字 > 25.0 字): "
            "分句粘连过密！多用句号断句，将平均句长压回 18~23 字的网文呼吸区间"
        )

    # 14. Optional Source-Draft Overlap & Cliché Retention Check (--source)
    source_overlap_rate = 0.0
    source_overlap_3gram = 0.0
    if source_text:
        src_body = strip_meta_sections(source_text)
        src_chars = max(1, len(re.sub(r"\s", "", src_body)))
        inflation_ratio = round(char_count / src_chars, 2)
        if char_count >= 1500 and inflation_ratio > 2.0:
            blocking.append(
                f"length-inflation (改写篇幅注水膨胀率过高 {inflation_ratio}x > 2.0x, 原稿{src_chars}字->改写{char_count}字): "
                "存在过多过渡套话与修饰注水，请执行删除测试精简冗余修饰"
            )
        src_ngrams = extract_char_ngrams(src_body, 5)
        dst_ngrams = extract_char_ngrams(body_no_head, 5)
        src_3grams = extract_char_ngrams(src_body, 3)
        dst_3grams = extract_char_ngrams(body_no_head, 3)
        if src_ngrams and dst_ngrams:
            overlap = src_ngrams & dst_ngrams
            source_overlap_rate = round(len(overlap) / max(1, len(dst_ngrams)), 3)
            if source_overlap_rate > 0.06:
                sample_ov = sorted(list(overlap))[:8]
                blocking.append(
                    f"source-phrase-retention (与待改原稿5字短语重合率过高 {source_overlap_rate*100:.1f}% > 6.0%): "
                    f"残留原稿短语 {sample_ov} -> 必须彻底脱离原稿句子骨架重写（Sepia recreate）"
                )
        if src_3grams and dst_3grams:
            ov3 = src_3grams & dst_3grams
            source_overlap_3gram = round(len(ov3) / max(1, len(dst_3grams)), 3)
            if source_overlap_3gram > 0.16:
                sample_ov3 = sorted(list(ov3))[:10]
                blocking.append(
                    f"source-clause-paraphrase (与待改原稿3字片段重合率过高 {source_overlap_3gram*100:.1f}% > 16.0%): "
                    f"存在逐句换词同义改写痕迹 {sample_ov3} -> 严禁看着原稿逐句换词！必须只按场景裸事实清单重起炉灶"
                )

    return {
        "status": "FAIL" if blocking else "PASS",
        "chars": char_count,
        "paragraphs": len(paras),
        "single_para_share": single_para_share,
        "sentence_mean": sent_mean,
        "sentence_sd": sent_sd,
        "paragraph_cv": para_cv,
        "zhe_per_1k": zhe_rate,
        "plain_func_per_1k": plain_rate,
        "source_overlap_5gram": source_overlap_rate,
        "source_overlap_3gram": source_overlap_3gram,
        "blocking_count": len(blocking),
        "blocking": blocking,
        "warnings": warnings,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", help="Path to markdown/txt novel draft")
    parser.add_argument("--source", help="Optional path to original draft (when rewriting) to block phrase/simile retention")
    parser.add_argument("--output", help="Optional JSON file path to write full report")
    args = parser.parse_args(argv)
    p = Path(args.file).resolve()
    src = Path(args.source).resolve().read_text(encoding="utf-8-sig") if args.source else None
    res = scan_text(p.read_text(encoding="utf-8-sig"), source_text=src)
    if args.output:
        Path(args.output).write_text(json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(
            f"STATUS={res['status']} BLOCKING={res['blocking_count']} WARNINGS={len(res['warnings'])} "
            f"PARA_CV={res['paragraph_cv']} SENT_SD={res['sentence_sd']} SRC_OVERLAP={res['source_overlap_5gram']}"
        )
    else:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    return 1 if res["status"] == "FAIL" else 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
