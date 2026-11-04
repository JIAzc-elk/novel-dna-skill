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
    "长睫", "眼睫", "指尖摩挲", "悄悄觑", "泛起一丝笑意", "浮起一抹温和",
    "浮现出一抹微笑", "眼中精芒一闪", "精芒一闪", "眼波一转", "美眸一转", "眸光微转",
    "眸光一闪", "眸光流转",
)

ABSTRACT_WRAPPER_RE = re.compile(
    r"(?:一股|一丝|一抹|一缕|一层|几分)[^，。！？\n]{0,8}"
    r"(?:的异样|的滞闷|的潮腥|的弧度|不易察觉的|难以名状的|说不清道不明的)"
)
ABSTRACT_WRAPPER_KEY = "抽象包装短语"

# 5b. Contrastive inversion & omniscient trailer clichés (strong Zhuque AI detector signals)
CONTRAST_INVERSION_RE = re.compile(
    r"(?:不是|并非)[^，。！？\n]{1,28}[，,]\s*而是[^。！？\n]{1,35}"
    r"|与其说(?:是)?[^，。！？\n]{1,24}[，,]\s*不如说(?:是)?[^。！？\n]{1,30}"
    r"|(?:不仅(?:仅)?是?|不止(?:是)?)[^，。！？\n]{1,24}[，,]\s*更是[^。！？\n]{1,30}"
    r"|[^，。！？\n]{2,18}[，,]\s*不(?:代表|意味着|等于)不能[^。！？\n]{2,20}"
    r"|没有[^，。！？\n]{1,14}[，,]\s*没有[^，。！？\n]{1,14}[，,]\s*(?:全凭|只凭|仅凭|单凭|唯凭|凭着|靠着)[^。！？\n]{2,28}"
)
PROTAGONIST_EXCEPTION_RE = re.compile(
    r"(?:寻常人|常人|普通人|一般人|换作旁人|旁人)[^。！？\n]{2,35}[。，,]\s*(?:但|可|然而|偏偏)?[^，。！？\n]{1,12}(?:却)?(?:不同|是个例外|截然不同|恰恰相反)"
)
SPOILER_ACTING_RE = re.compile(
    r"(?:假意|故作|状似无意|佯装)[^，。！？\n]{1,12}"
    r"|眼底(?:深处)?(?:掠过|闪过|浮起|藏着)(?:一抹|一丝)[^，。！？\n]{1,8}"
    r"|(?:脸上|面上)?(?:已|便|又|旋即|立刻|瞬间)?(?:换作|换上|挂上|戴上)(?:了)?(?:一副)?[^，。！？\n]{1,10}的(?:面孔|表情|神情|神态|嘴脸|笑容|笑意)"
    r"|(?:敛去|收起)[^，。！？\n]{0,8}(?:神色|表情|寒意|笑意|冷意)[，,]\s*[^，。！？\n]{0,8}(?:换上|换作|挂上|堆起)[^，。！？\n]{1,12}"
    r"|(?:眼波|眼珠|眸光|心思|念头)一转[，,]\s*(?:脸上|面上|神色|嘴角)[^。！？\n]{1,22}"
    r"|(?:整盘棋局|前因后果|前後因果|前后因果|所有线索|一切关节)[^。！？\n]{0,18}(?:在[^，。！？\n]{1,8}脑(?:中|海)|心中)(?:彻底)?(?:贯通|串连|串联|豁然开朗|雪亮|明朗)"
    r"|真正的破绽[，,]?出在[^。！？\n]{2,25}"
    r"|心(?:中|底)自然雪亮"
    r"|心(?:中|里)(?:很)?清楚[，,]\s*(?:似|像|这等|此等)"
)
INVENTORY_ITEM_PAT = r"[\u4e00-\u9fff]{2,6}(?:[零〇一二两三四五六七八九十百千万\d]{1,6})(?:方|截|袋|斤|块|枚|颗|株|滴|柄|件|尊|口|张|卷|瓶|盒|箱)"
INVENTORY_LIST_RE = re.compile(
    rf"{INVENTORY_ITEM_PAT}\s*[、，,]\s*{INVENTORY_ITEM_PAT}\s*[、，,]\s*{INVENTORY_ITEM_PAT}"
)
GRAMMAR_GLITCH_RE = re.compile(
    r"既然[^。！？\n]{1,12}既[^。！？\n]{1,15}又"
    r"|寸步难行地(?:往上|向前|往前|朝前|一步步)"
)
CLICHE_IDIOM_LIST = (
    "如臂使指", "入不敷出", "土崩瓦解", "稳如泰山", "仁至义尽",
    "顺水推舟", "指鹿为马", "滔天丑闻", "火急火燎", "轻而易举",
    "截然不同", "彻头彻尾", "不折不扣", "毋庸置疑", "显而易见",
    "理所当然", "天壤之别", "置若罔闻", "装聋作哑", "一清二楚",
)
OMNISCIENT_TRAILER_RE = re.compile(
    r"命运的齿轮(?:开始)?转动"
    r"|只是(?:故事|一切|噩梦|传奇|传奇故事)的开始"
    r"|注定是[^。！？\n]{1,15}的?转折点"
    r"|(?:他|她|众人)(?:还|却)?不知道的是[，,]"
    r"|一场[^，。！？\n]{0,8}风暴正在酝酿"
    r"|仿佛在(?:诉说|宣告|无声地诉说|嘲笑)着?"
)
DASH_RE = re.compile(r"——|--")

# 5c. Editor video notes checks: abnormal symbols/spacing, high-frequency status adverbs,
#     body-part sensory repetition (舌尖/指尖/喉间),redundant speech-tone tags, hollow vows,
#     and "As-you-know-Bob" dialogue info-dumping.
MACHINE_SYMBOL_STACK_RE = re.compile(
    r"(?:\*{2,}|#{2,}|@{2,}|¥{2,}|~{2,})"
    r"|(?<=[\u4e00-\u9fff])[#@¥*](?=[\u4e00-\u9fff])"
)
ABNORMAL_CJK_SPACE_RE = re.compile(
    r"[\u4e00-\u9fff]+[ \t]+[，。！？；：、“”‘’「」『』（）]"
    r"|[，。！？；：、“”‘’「」『』（）][ \t]+[\u4e00-\u9fff]+"
    r"|[\u4e00-\u9fff]{1,4}[ \t]+[\u4e00-\u9fff]{1,4}[ \t]+[\u4e00-\u9fff]{1,4}"
)
STATUS_ADVERB_WORDS = (
    "缓缓", "微微", "陡然", "忽而", "半晌", "不由得", "不知为何",
)
BODY_SENSORY_WORDS = (
    "指尖", "舌尖", "喉间", "眉梢", "眼尾", "鼻尖", "唇齿",
)
REDUNDANT_SPEECH_TONE_RE = re.compile(
    r"(?:轻声|缓声|淡声|沉声|冷声|低声|缓缓)(?:开口|说道|道)[，,]\s*"
    r"(?:声音|语气|语调|话音)[^，。！？\n]{0,8}(?:听不出|没有|不见|不带|带着|透着)[^，。！？\n]{1,10}"
    r"|(?:语气|声音)(?:里|中)?(?:带着|透着)(?:几分|一丝|一抹)?(?:小心翼翼|不容置喙|不容置疑|难以抗拒|居高临下)[^：:。！？\n]{0,8}"
)
HOLLOW_VOW_CLICHE_RE = re.compile(
    r"往后(?:仙路|江湖|岁月|山高)[^。！？\n]{2,18}[，,]\s*[^。！？\n]{0,12}(?:他日|来日|日后)(?:自会|必会|定会|必当|终会)(?:再相逢|相见|奉还|清算)"
    r"|今日之(?:辱|仇|赐|事)[，,]\s*(?:他日|来日|日后)必(?:百倍|十倍|双倍|加倍|当)?(?:奉还|讨回|清算)"
    r"|三十年河东[，,]\s*三十年河西"
)
DIALOGUE_INFODUMP_RE = re.compile(
    r"[\u300C\u201C\u300E\"][^\u300D\u201D\u300F\"\n]{0,30}"
    r"(?:天资出众|天赋异禀|身份尊贵|乃是[^，。！？\u300D\u201D\n]{2,12}之[子女人选]|入[^，。！？\u300D\u201D\n]{2,8}才是理所应当)"
    r"[^\u300D\u201D\u300F\"\n]{0,25}(?:你一个|区区一个)(?:凡夫|废人|外人|杂役|庶子|凡人)"
    r"[^\u300D\u201D\u300F\"\n]*[\u300D\u201D\u300F\"]"
)

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

# 9. Universal fingerprint (通用指纹/告知词禁忌.md): five kinds of "telling" words.
# Counted in narration only, since dialogue is the character's own voice. The limits
# are the same for every pack: they are a shared floor, not part of an author's style.
TELLING_DOC = "references/通用指纹/告知词禁忌.md"
TELL_EMOTIONS = (
    "难过|伤心|悲伤|悲痛|高兴|开心|快乐|喜悦|欣喜|欢喜|害怕|恐惧|惊恐|恐慌|惶恐|愤怒|生气|恼火|"
    "紧张|激动|兴奋|失落|失望|委屈|尴尬|焦虑|着急|担心|担忧|忐忑|不安(?!全)|烦躁|郁闷|沮丧|"
    "痛苦|难受|心酸|欣慰|惊讶|震惊|惊喜|羞愧|惭愧|愧疚|后悔|懊恼|感动|幸福|孤独|无奈|慌张|绝望|"
    "得意|满意|嫉妒|害羞"
)
TELL_INTENSIFIERS = (
    "十分|非常|很是|很|有些|有点|有几分|一阵|一丝|几分|些许|无比|格外|极为|极其|万分|颇为|"
    "越发|更加|愈发|特别|相当|分外|略微|稍微"
)
TELLING_WORD_CLASSES = (
    # (class, pattern, block when count >= this, and per-1k narration rate > this, how to fix)
    (
        "程度副词",
        re.compile(
            r"(?<![并绝决似实岂若本])非常(?!人|时期|手段|规|态|之|事)"
            r"|特别(?![是的之处]|行动|顾问|小组|通道|待遇)"
            r"|(?<![零〇一二两三四五六七八九十百千万几数\d])十分(?![钟之])"
            r"|极其|格外|极为|无比|分外"
            r"|(?<![旗鼓力为纪龄份位均不之致本])相当(?![于的之])(?=[\u4e00-\u9fff])"
        ),
        6, 3.0,
        "能用动作和细节表现的就不用程度副词概括，例如‘他非常生气’改成‘他一把掀了桌子’",
    ),
    (
        "情绪词",
        re.compile(
            rf"(?:感到|感觉到?|觉得|心里|心中|心头|内心|心底|心生)(?:{TELL_INTENSIFIERS})?(?:{TELL_EMOTIONS})"
            rf"|(?:很是|很|有些|有点|颇为|万分|十分|非常|极其|格外|无比|特别|相当|分外|极为)(?:{TELL_EMOTIONS})"
        ),
        3, 0.6,
        "把情绪演出来，写人物的动作、表情和反应，不要直接说他难过、高兴、害怕",
    ),
    (
        "多余身体部位",
        re.compile(
            r"用(?:眼睛|双眼|眼)(?:看|瞧|望|盯|瞪|扫|瞄)"
            r"|用(?:耳朵|双耳)听|用鼻子(?:闻|嗅)|用嘴(?:说|咬|吹|吃|喊|叼)|用牙(?:齿)?咬"
            r"|用手(?:拿|推|抓|握|接|摸|拍|扶|端|提|取|递|挡|捂|按|擦|抹|揉|拉|拽|扯|捏|托|掐|撑|搭|拎|抱|敲|挥|招)"
            r"(?![指掌背心腕臂])"
            r"|用脚(?:踢|踩|踹|走|跺)"
            r"|嘴里(?:说|嚼|吃|叼|念叨|嘟囔)|脚下(?:走|迈)"
            r"|脸上[^，。！？\n]{0,14}的(?:面孔|脸庞|脸蛋|面庞)"
            r"|心里[^，。！？\n]{0,8}的心思"
        ),
        1, 0.0,
        "动作或部位已隐含在中心词中，删掉叠床架屋的部位重复（如‘用手推开门’→‘推开门’，‘脸上换作一副惊喜的面孔’→直接写神态动作，‘脸上…的面孔’语义重复）",
    ),
    (
        "模糊形容词",
        re.compile(
            r"美丽|漂亮(?!话)|英俊|帅气|丑陋|(?<![不你他她])好看"
            r"|很大(?!程度)|很小(?!的时候|就|时)|很多|很少"
        ),
        4, 1.5,
        "换成具体特征、尺寸和数量，例如‘她很漂亮’改成写她独有的样貌细节，‘院子很大’改成从院门走到正屋要多久",
    ),
    (
        "口头禅过渡词",
        re.compile(
            r"紧接着|然后|(?<![紧手盆碗])接着(?![那这一两半个它他她手球])"
            r"|(?<![由在对关属等至终过异当决])于是(?!否)"
        ),
        4, 1.5,
        "删掉大半，靠语序和上下文衔接，例如‘他推开门，然后走进去’改成‘他推开门，走进去’",
    ),
)


def scan_telling_words(text: str) -> list[dict]:
    """Count the five classes of telling words in narration (quotes and 【】 panels excluded)."""
    narrative = packlib.mask_quoted(text)
    narr_chars = max(1, len(re.sub(r"[\s\x00]", "", narrative)))
    found = []
    for name, pattern, min_count, max_rate, fix in TELLING_WORD_CLASSES:
        hits = [m.group(0) for m in pattern.finditer(narrative)]
        if not hits:
            continue
        rate = round(len(hits) * 1000.0 / narr_chars, 2)
        counts: dict[str, int] = {}
        for h in hits:
            counts[h] = counts.get(h, 0) + 1
        found.append({
            "class": name,
            "count": len(hits),
            "per_1k": rate,
            "words": sorted(counts.items(), key=lambda x: -x[1]),
            "blocking": len(hits) >= min_count and rate > max_rate,
            "fix": fix,
        })
    return found


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
        "target_single_para_share": None,
        "min_single_para_share": 0.15,
        "max_single_para_share": 0.62,
        "warn_single_para_share": 0.50,
        "zhe_limit": DEFAULT_ZHE_LIMIT,
        "semicolons_per_1k": None,
        "slop_rates": {},
        "lexicon_words": [],
        "connector_words": [],
        "world_terms_lock": [],
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
    key = (
        str(stats_file),
        _mtime(stats_file),
        _mtime(stats_file.parent / "corpus.jsonl"),
        _mtime(stats_file.parent / "lexicon.json"),
    )
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
        "target_single_para_share": round(single_share, 3),
        "min_single_para_share": round(max(0.12, min(0.38, single_share * 0.58)), 3),
        "max_single_para_share": round(min(0.85, max(0.62, single_share + 0.18)), 3),
        "warn_single_para_share": round(min(0.78, max(0.50, single_share + 0.12)), 3),
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
    lex_file = stats_file.parent / "lexicon.json"
    if lex_file.is_file():
        try:
            lex = json.loads(lex_file.read_text(encoding="utf-8-sig"))
            words = []
            for cat_name, items in (lex.get("categories") or {}).items():
                if cat_name == "世界观与筹码计量词":
                    continue
                if isinstance(items, list):
                    for it in items:
                        if isinstance(it, dict) and isinstance(it.get("word"), str) and len(it["word"]) >= 2:
                            words.append(it["word"])
            conns = []
            for items in (lex.get("connectors") or {}).values():
                if isinstance(items, list):
                    for it in items:
                        if isinstance(it, dict) and isinstance(it.get("word"), str) and len(it["word"]) >= 2:
                            conns.append(it["word"])
            cal["lexicon_words"] = list(dict.fromkeys(words))
            cal["connector_words"] = list(dict.fromkeys(conns))
            cal["world_terms_lock"] = [
                w for w in (lex.get("world_terms_lock") or []) if isinstance(w, str) and len(w) >= 2
            ]
        except (OSError, ValueError, TypeError):
            pass
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

    # 6b. Contrastive inversion formula ("不是X，而是Y" / "与其说X，不如说Y" in unquoted narration)
    contrast_hits = [m.group(0).strip() for m in CONTRAST_INVERSION_RE.finditer(narrative_only)]
    if contrast_hits:
        blocking.append(
            f"contrast-inversion-formula (AI翻案辩论句式 {len(contrast_hits)} 处): "
            f"{contrast_hits[:3]} -> 严禁先否定再肯定的‘不是X而是Y/与其说X不如说Y’，直接写具体动作或判断"
        )

    # 6c. Omniscient trailer hooks ("命运的齿轮开始转动" / "他不知道的是" / "只是故事的开始")
    trailer_hits = [m.group(0).strip() for m in OMNISCIENT_TRAILER_RE.finditer(narrative_only)]
    if trailer_hits:
        blocking.append(
            f"omniscient-trailer-hook (AI预告片旁白升华/全知剧透 {len(trailer_hits)} 处): "
            f"{trailer_hits[:3]} -> 删掉‘命运齿轮/只是开始/他不知道的是’，停在具体画面或动作上"
        )

    # 6d. Em-dash explanation tic ("——" in narration; high-weight Zhuque detector feature)
    dash_hits = DASH_RE.findall(narrative_only)
    dash_limit = 1 if char_count < 1500 else 2
    if len(dash_hits) > dash_limit:
        blocking.append(
            f"dash-abuse (AI破折号标点癖 {len(dash_hits)} 处 > 上限 {dash_limit}): "
            "朱雀等AI检测器对破折号补充说明极敏感，请将‘——’改为逗号、句号或拆成短句"
        )
    elif len(dash_hits) >= 1:
        warnings.append(
            f"dash-note (叙述出现破折号 {len(dash_hits)} 处): AI检测器对破折号敏感，建议改为逗号或句号"
        )

    # 6e. Formulaic protagonist exception ("寻常人……但他不同")
    exc_hits = [m.group(0).strip() for m in PROTAGONIST_EXCEPTION_RE.finditer(narrative_only)]
    if exc_hits:
        blocking.append(
            f"formulaic-exception-hook (AI套路对比开场 ‘寻常人…但XX不同’ {len(exc_hits)} 处): "
            f"{exc_hits[:3]} -> 删掉‘寻常人如何、但主角不同’的套话，直接写主角当下的动作或决断"
        )

    # 6f. Spoiler acting tags & omniscient deduction announcements ("假意/状似无意/眼底深处掠过/脑中彻底贯通")
    spoiler_hits = [m.group(0).strip() for m in SPOILER_ACTING_RE.finditer(narrative_only)]
    if spoiler_hits:
        blocking.append(
            f"spoiler-acting-tags (AI剧透式演技标签/破案宣布腔 {len(spoiler_hits)} 处): "
            f"{spoiler_hits[:5]} -> 严禁写‘假意/状似无意/眼底深处掠过/脑中彻底贯通/心中自然雪亮’，只写外部动作留给读者自己品"
        )

    # 6g. Game-panel inventory number list ("闷雷石三十六方、焦雷木十八截、电光砂五十袋")
    inv_hits = [m.group(0).strip() for m in INVENTORY_LIST_RE.finditer(body_no_head)]
    if inv_hits:
        blocking.append(
            f"inventory-number-list (游戏面板式精确数字报菜名 {len(inv_hits)} 处): "
            f"{inv_hits[:3]} -> 算账只抓 1 个核心大数或关键差额，其余用‘成箱杂项’带过，切勿连列3项以上精确数量清单"
        )

    # 6h. Grammar glitches ("既然…既…又…" / "寸步难行地往上熬")
    glitch_hits = [m.group(0).strip() for m in GRAMMAR_GLITCH_RE.finditer(body_no_head)]
    if glitch_hits:
        blocking.append(
            f"grammar-glitch (AI合成语病/成语误用 {len(glitch_hits)} 处): "
            f"{glitch_hits[:3]} -> 修正重复虚词（既然…既…）或误用成语（寸步难行不能修饰往前走）"
        )

    # 6i. Cliché 4-character idiom stuffing & exact idiom repetition
    idiom_counts: dict[str, int] = {}
    for idm in CLICHE_IDIOM_LIST:
        c = body_no_head.count(idm)
        if c:
            idiom_counts[idm] = c
    total_idioms = sum(idiom_counts.values())
    repeated_idioms = [f"{k}x{v}" for k, v in idiom_counts.items() if v >= 2]
    idiom_rate = round(total_idioms * 1000.0 / char_count, 2)
    if repeated_idioms or (total_idioms >= 6 and idiom_rate >= 3.0):
        detail = f"同章重复成语 {repeated_idioms}" if repeated_idioms else f"套路成语堆砌 {total_idioms} 处 ({idiom_rate}/千字: {list(idiom_counts.keys())[:6]})"
        blocking.append(
            f"cliche-idiom-stuffing (AI四字成语轰炸/复读: {detail}): "
            "请将套路化四字成语拆解为具体白话动作或实在账目，严禁一段塞满成语或同章复读同一成语"
        )

    # 6j. Abnormal symbols & CJK spacing (特殊符号堆叠与符号前后异常空格)
    sym_hits = [m.group(0) for m in MACHINE_SYMBOL_STACK_RE.finditer(body_no_head)]
    space_hits = [m.group(0) for m in ABNORMAL_CJK_SPACE_RE.finditer(body_no_head)]
    if sym_hits or len(space_hits) >= 2:
        detail = f"特殊符号堆叠 {sym_hits[:4]}" if sym_hits else f"中文字符与标点异常空格 {space_hits[:4]}"
        blocking.append(
            f"abnormal-symbol-spacing (机器排版符号或异常空格: {detail}): "
            "清除正文中的 #/@/¥/* 符号堆叠与中文标点前后的非自然空格"
        )
    elif len(space_hits) == 1:
        warnings.append(f"abnormal-symbol-spacing-note (存在异常空格 {space_hits[0]!r}): 建议清除中文与标点间的空格")

    # 6k. High-frequency status adverb repetition (缓缓/微微/陡然/忽而/半晌/不由得/不知为何)
    adv_counts = {w: narrative_only.count(w) for w in STATUS_ADVERB_WORDS if narrative_only.count(w) > 0}
    adv_total = sum(adv_counts.values())
    adv_rate = round(adv_total * 1000.0 / char_count, 2)
    adv_over = [f"{w}x{c}" for w, c in adv_counts.items() if c >= 4]
    if adv_over or (adv_total >= 7 and adv_rate >= 3.5):
        sample_adv = [f"{w}x{c}" for w, c in sorted(adv_counts.items(), key=lambda x: -x[1])]
        blocking.append(
            f"status-adverb-overuse (高频状态词/衔接词反复使用 {adv_total} 处, {adv_rate}/千字: {sample_adv}): "
            "‘缓缓/微微/陡然/忽而/半晌/不由得/不知为何’被当作万金油模板反复刷屏，请删减或换为契合人物的具体动作"
        )
    elif any(c >= 3 for c in adv_counts.values()) or (adv_total >= 4 and adv_rate >= 2.2):
        sample_adv = [f"{w}x{c}" for w, c in sorted(adv_counts.items(), key=lambda x: -x[1])]
        warnings.append(
            f"status-adverb-note (状态副词偏多 {adv_total} 处: {sample_adv}): 避免每次开口都‘缓缓’、每次反应都‘微微/陡然’"
        )

    # 6l. Body-part sensory repetition (舌尖/指尖/喉间/眉梢/鼻尖 等局部身体词高频重复)
    body_part_counts = {w: body_no_head.count(w) for w in BODY_SENSORY_WORDS if body_no_head.count(w) > 0}
    body_part_total = sum(body_part_counts.values())
    body_part_over = [f"{w}x{c}" for w, c in body_part_counts.items() if c >= 3]
    if body_part_over or (body_part_total >= 4 and body_part_total * 1000.0 / char_count >= 2.0):
        sample_bp = [f"{w}x{c}" for w, c in sorted(body_part_counts.items(), key=lambda x: -x[1])]
        blocking.append(
            f"body-part-sensory-repetition (局部身体感官词重复描写 {body_part_total} 处: {sample_bp}): "
            "‘指尖/舌尖/喉间/眉梢’等精致小动作词同章高频复读暴露AI固定遣词癖，请改为直接的器物处置或大肢体行动"
        )
    elif any(c >= 2 for c in body_part_counts.values()):
        sample_bp = [f"{w}x{c}" for w, c in sorted(body_part_counts.items(), key=lambda x: -x[1])]
        warnings.append(
            f"body-part-sensory-note (局部感官词重复 {sample_bp}): 注意避免同类身体部位词连续复用"
        )

    # 6m. Redundant speech-tone tags ("轻声开口，声音听不出半分波澜" / "声音带着不容置喙的严肃")
    tone_hits = [m.group(0).strip() for m in REDUNDANT_SPEECH_TONE_RE.finditer(narrative_only)]
    if tone_hits:
        blocking.append(
            f"redundant-speech-tone (对白前后语气标签重复强调 {len(tone_hits)} 处): "
            f"{tone_hits[:4]} -> 对白与上下文已传递情绪时，严禁再叠床架屋说明‘轻声开口声音听不出波澜/声音带着不容置喙的严肃’"
        )

    # 6n. Hollow vow & formulaic parting threat ("往后仙路漫漫，青云之上，他日自会再相逢")
    vow_hits = [m.group(0).strip() for m in HOLLOW_VOW_CLICHE_RE.finditer(body_no_head)]
    if vow_hits:
        blocking.append(
            f"hollow-vow-cliche (套路化空喊狠话/预告台词 {len(vow_hits)} 处): "
            f"{vow_hits[:3]} -> 删掉‘他日自会再相逢/今日之辱他日百倍奉还’等无筹码支撑的机械立誓点题，改为当下的实在善后或止损动作"
        )

    # 6o. Dialogue info-dumping ("如烟姑娘天资出众，入青云宗才是理所应当，你一个凡夫，别挡别人的仙路")
    infodump_hits = [m.group(0).strip() for m in DIALOGUE_INFODUMP_RE.finditer(body_no_head)]
    if infodump_hits:
        blocking.append(
            f"dialogue-info-dump (路人工具人式背景解说台词 {len(infodump_hits)} 处): "
            f"{infodump_hits[:2]} -> 严禁借旁人插嘴替作者向读者背诵人物身份高低与设定资料卡，对白只能围绕眼前动作与筹码交锋"
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

    # 13b. Universal fingerprint: telling words in narration (程度副词/情绪词/多余身体部位/模糊形容词/口头禅过渡词)
    telling = scan_telling_words(body_no_head)
    for item in telling:
        sample = "、".join(f"{w}x{c}" if c > 1 else w for w, c in item["words"][:8])
        line = (
            f"{item['class']} {item['count']} 处（{item['per_1k']}/千字叙述）：{sample} -> {item['fix']}"
            f"（见 {TELLING_DOC}）"
        )
        if item["blocking"]:
            blocking.append(f"telling-words ({line})")
        else:
            warnings.append(f"telling-words-note ({line})")

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

        # 14b. World-setting proper term leak check: if the target pack has world_terms_lock
        #      (e.g. 蛊虫/仙窍/道痕) that were NOT in the user's source draft, block their leak!
        if cal.get("world_terms_lock"):
            leaked_terms = [
                wt for wt in cal["world_terms_lock"]
                if wt in body_no_head and wt not in src_no_head
            ]
            if leaked_terms:
                blocking.append(
                    f"pack-world-term-leak (跨书改写漏入写作包原著体系专名 {leaked_terms}): "
                    "原稿并无此类设定词！请按 `词句库/词汇库.md` 的【新书设定转换槽】替换为原稿自己的体系名物"
                )

    # 15. Positive Lexicon & Connector Replacement Hit Rate ("中毒复检" - advisory only, never force tag-stuffing)
    lexicon_hits: list[str] = []
    connector_hits: list[str] = []
    if cal.get("lexicon_words") or cal.get("connector_words"):
        for w in cal.get("lexicon_words") or []:
            if w in body_no_head:
                lexicon_hits.append(w)
        for cw in cal.get("connector_words") or []:
            if cw in body_no_head:
                connector_hits.append(cw)

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
        "telling_words": {item["class"]: item["count"] for item in telling},
        "lexicon_hits_count": len(lexicon_hits),
        "connector_hits_count": len(connector_hits),
        "lexicon_hits_sample": (lexicon_hits + connector_hits)[:15],
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


def scan_narrative_architecture(text: str, pack_cal: dict) -> dict:
    """Scan narrative architecture & statistical AI fingerprints (StoryScope + Zhuque anti-detection).
    
    Returns:
        dict with keys:
        - blocking: list of must-fix issues
        - advisory: list of suggested improvements
        - green: list of passed checks
        - metrics: dict of quantitative measurements
    """
    body = strip_headings(strip_meta_sections(text))
    narrative = packlib.mask_quoted(body)
    chars = max(1, len(re.sub(r"\s", "", narrative)))
    paras = split_paragraphs(body)
    narrative_paras = split_paragraphs(packlib.strip_quoted(body))
    
    blocking = []
    advisory = []
    green = []
    metrics = {}
    
    # 1. Dialogue preaching & essay-talk ratio (supports “…”、「…」、『…』 and ASCII "...")
    dialogue_pattern = r'[\u300C\u201C\u300E"][^\u300D\u201D\u300F"\n]{4,}?[\u300D\u201D\u300F"]'
    dialogues = re.findall(dialogue_pattern, body)
    ascii_quotes = body.count('"')
    if ascii_quotes >= 2:
        advisory.append(
            f"ascii-straight-quotes (英文半角引号 {ascii_quotes} 处): "
            "中文小说请统一使用中文引号 “…” 或 「…」，避免半角双引号暴露模型生成痕迹"
        )
    if dialogues:
        preaching_keywords = [
            "意义", "道理", "真正的", "所谓的", "明白了", "这说明", "这告诉",
            "真谛", "心灵的", "本质", "战胜自己", "然而，", "因此，", "总而言之", "事实上，",
        ]
        abstract_concepts = ["勇气", "坚持", "强大", "成长", "命运", "选择", "修行", "内心"]
        
        preaching_dialogues = []
        for d in dialogues:
            kw_count = sum(1 for kw in preaching_keywords if kw in d)
            concept_count = sum(1 for c in abstract_concepts if c in d)
            clause_count = d.count("。") + d.count("！") + d.count("？")
            if (kw_count >= 2) or (kw_count >= 1 and concept_count >= 1) or (concept_count >= 2 and clause_count >= 2):
                preaching_dialogues.append(d[:32] + "...")
        
        preach_ratio = len(preaching_dialogues) / len(dialogues)
        metrics["dialogue_preaching_ratio"] = round(preach_ratio, 3)
        
        if preach_ratio > 0.35:
            excess = max(1, len(preaching_dialogues) - int(len(dialogues) * 0.35))
            blocking.append(
                f"dialogue-preaching (对话说教/书面腔比例 {preach_ratio:.1%} > 35%, 人类基线34%): "
                f"需改{excess}处对话。示例：{preaching_dialogues[:3]} -> 改为口语短句、半截话、反问或具体事务，删掉讲道理"
            )
        else:
            green.append(f"dialogue-preaching ({preach_ratio:.1%} ≤ 35%)")
    
    # 2. Emotion labeling & high-probability LLM body-reaction clichés (per 10k chars)
    emotion_patterns = [
        (r"很(?:愤怒|悲伤|震惊|失望|高兴|害怕|坚定|感动|幸福)", "情绪形容词"),
        (r"心中涌起(?:一股|一丝)", "情绪涌现"),
        (r"(?:语气|声音|眼中)(?:充满了|带着|闪过|透着)[^，。！？\n]{0,6}(?:愤怒|悲伤|失望|坚定|激动|欣慰|寒意|杀意)", "情绪贴签"),
    ]
    cliche_body_patterns = [
        r"(?:深吸|倒吸)(?:了)?一口(?:冷|凉)?气",
        r"瞳孔(?:骤然|猛地|微微)?(?:一缩|骤缩|微缩|放大)",
        r"(?:眼中|眼底|眸中|眼眸)(?:闪过|掠过|浮现出?)(?:一丝|一抹)",
        r"(?:身体|身躯|身子)(?:猛地|骤然)?一(?:僵|震|颤)",
        r"(?:手指|指甲)(?:死死)?(?:抠进|嵌进|掐进)(?:掌心|肉里)",
        r"攥紧(?:了)?(?:拳头|剑柄|衣角)[，,]?(?:没有?说话|一言不发|没吭声)",
        r"(?:空气|时间|气氛)仿佛(?:凝固|静止)了?",
        r"死一般的寂静",
        r"(?:五味杂陈|百感交集)",
    ]
    
    emotion_hits = []
    for pattern, desc in emotion_patterns:
        matches = re.findall(pattern, narrative)
        emotion_hits.extend([(m, desc) for m in matches])
    
    cliche_body_hits = []
    for pattern in cliche_body_patterns:
        cliche_body_hits.extend(re.findall(pattern, narrative))
    
    emotion_density = len(emotion_hits) * 10000 / chars
    cliche_body_density = len(cliche_body_hits) * 10000 / chars
    metrics["emotion_labeling_per_10k"] = round(emotion_density, 2)
    metrics["cliche_body_per_10k"] = round(cliche_body_density, 2)
    
    if emotion_density > 5:
        blocking.append(
            f"emotion-labeling (情绪直述密度 {emotion_density:.1f}处/万字 > 5, 红区): "
            f"示例：{[h[0] for h in emotion_hits[:5]]} -> 改为具体处境反应或手头动作，严禁贴情绪标签"
        )
    elif emotion_density > 3:
        advisory.append(
            f"emotion-labeling (情绪直述密度 {emotion_density:.1f}处/万字, 黄区 3-5): "
            "建议改为具体动作细节"
        )
    else:
        green.append(f"emotion-labeling ({emotion_density:.1f}处/万字 ≤ 3)")
    
    if len(cliche_body_hits) >= 2 or (len(cliche_body_hits) >= 1 and chars < 1200):
        blocking.append(
            f"cliche-body-reaction (AI高频生理套话 {len(cliche_body_hits)} 处, {cliche_body_density:.1f}/万字): "
            f"示例：{cliche_body_hits[:4]} -> ‘深吸一口气/倒吸凉气/瞳孔骤缩/抠进掌心/攥紧剑柄没说话’是朱雀高危AI套话，请换成该场景独有的具体动作或直接删掉"
        )
    elif len(cliche_body_hits) == 1:
        advisory.append(
            f"cliche-body-reaction (AI生理套话 1 处: {cliche_body_hits[0]}): 建议替换为具体场景动作"
        )
    else:
        green.append("cliche-body-reaction (0处生理套话)")
    
    # 3. Paragraph structure uniformity & choppy single-sentence paragraph spam
    para_lengths = [len(re.sub(r"\s", "", p)) for p in paras if len(p) > 20]
    if len(para_lengths) >= 5:
        min_std = float('inf')
        worst_window = None
        for i in range(len(para_lengths) - 4):
            window = para_lengths[i:i+5]
            std = statistics.stdev(window) if len(window) > 1 else 0
            if std < min_std:
                min_std = std
                worst_window = (i+1, window)
        
        metrics["paragraph_std_min"] = round(min_std, 2)
        
        if min_std < 5:
            blocking.append(
                f"paragraph-uniformity (段落字数标准差 {min_std:.1f} < 5): "
                f"第{worst_window[0]}-{worst_window[0]+4}段过于工整 {worst_window[1]} -> 拆分或合并"
            )
        elif min_std < 8:
            advisory.append(
                f"paragraph-uniformity (段落字数标准差 {min_std:.1f}, 黄区 5-8): "
                "建议增加段落长短变化"
            )
        else:
            green.append(f"paragraph-uniformity (标准差 {min_std:.1f} ≥ 8)")
    
    single_sent_paras = sum(1 for p in paras if len(packlib.sentence_pieces(p)) <= 1)
    single_para_share = round(single_sent_paras / max(1, len(paras)), 3)
    metrics["single_para_share"] = single_para_share
    max_single = pack_cal.get("max_single_para_share", 0.62)
    warn_single = pack_cal.get("warn_single_para_share", 0.50)
    min_single = pack_cal.get("min_single_para_share", 0.15)
    target_single = pack_cal.get("target_single_para_share")
    pack_single_hint = f"（目标包单句成段基线 {target_single:.1%}）" if target_single else ""
    if len(paras) >= 12 and single_para_share > max_single:
        blocking.append(
            f"choppy-telegraph-prose (单句成段率过高 {single_para_share:.1%} > {max_single:.1%}{pack_single_hint}): "
            "超出目标写作包断奏上限 -> 请将非关键节点的琐碎单句适度合并"
        )
    elif len(paras) >= 12 and target_single and target_single >= 0.50 and single_para_share < min_single:
        blocking.append(
            f"style-too-merged (单句成段率过低 {single_para_share:.1%} < {min_single:.1%}{pack_single_hint}): "
            "目标写作包属于高频单句成段的冷硬断奏风格，切勿为了迁就通用规则把短句全焊成长段！请恢复单句独立成段的铁锤落点"
        )
    elif len(paras) >= 10 and single_para_share > warn_single:
        advisory.append(
            f"choppy-telegraph-prose (单句成段率偏高 {single_para_share:.1%} > {warn_single:.1%}{pack_single_hint}): "
            "建议检查是否有无意义的一句一行碎句"
        )
    
    # 3b. Exclamation echo-chamber & narrative exclamation abuse ("角色说完 -> 旁白单起一行复读 + ！")
    unquoted_paras = [packlib.strip_quoted(p).strip() for p in paras]
    narr_exclam_paras = [up for up in unquoted_paras if up.endswith(("！", "!"))]
    exclam_share = round(len(narr_exclam_paras) / max(1, len(narrative_paras)), 3)
    metrics["narrative_exclam_para_share"] = exclam_share
    echo_hits = []
    for i in range(1, len(paras)):
        up = unquoted_paras[i]
        if not up or len(up) > 38:
            continue
        prev_cjk = re.sub(r"[^\u4e00-\u9fff]", "", paras[i - 1])
        curr_cjk = re.sub(r"[^\u4e00-\u9fff]", "", up)
        if not curr_cjk:
            continue
        if up.endswith(("！", "!")):
            prev_grams = {prev_cjk[j : j + 2] for j in range(len(prev_cjk) - 1)}
            curr_grams = {curr_cjk[j : j + 2] for j in range(len(curr_cjk) - 1)}
            overlap = prev_grams & curr_grams
            if overlap:
                echo_hits.append(f"{up} (复读前段:{sorted(overlap)[:2]})")
        elif len(curr_cjk) <= 8 and len(prev_cjk) >= len(curr_cjk) + 2 and prev_cjk.endswith(curr_cjk):
            echo_hits.append(f"{up} (原样复读前段末尾‘{curr_cjk}’)")
    if echo_hits or (len(narr_exclam_paras) >= 4 and exclam_share > 0.18):
        detail = f"回声虫复读 {echo_hits[:3]}" if echo_hits else f"叙述段尾感叹号 {len(narr_exclam_paras)} 处 ({exclam_share:.1%})"
        blocking.append(
            f"exclamation-echo-abuse (AI复读机强调/叙述感叹号泛滥: {detail}): "
            "严禁‘角色刚说出信息 -> 旁白单起一行复述加感叹号’，叙述正文请用句号冷峻收束"
        )

    # 3c. Periodic environmental prop filler & planted '蜘蛛结网/缩了缩脖子' clichés
    planted_cliches = re.findall(r"蜘蛛(?:在)?结网|缩了缩脖子", body)
    prop_counts = {
        prop: len(re.findall(pat, narrative))
        for prop, pat in (
            ("药炉/药罐", r"药炉|药罐|药吊子"),
            ("炭火/火星", r"炭火|火星|火苗|炉底"),
            ("茶盏/茶水", r"茶盏|茶杯|茶沫|茶水"),
        )
    }
    overused_props = [f"{k}x{v}" for k, v in prop_counts.items() if v >= 3 and v * 2500 / max(500, chars) >= 2.5]
    if planted_cliches or overused_props:
        detail = f"照抄模板闲笔 {planted_cliches}" if planted_cliches else f"周期性景物节拍器 {overused_props}"
        blocking.append(
            f"periodic-prop-filler (AI机械环境点缀: {detail}): "
            "严禁照抄‘蜘蛛结网/缩了缩脖子’或每隔几段就写一次药炉/炭火冒气充当节拍器；环境细节必须与人物当下动作或后续因果直接咬合"
        )
    
    # 4. Subject-opening monotony & mechanical '下意识/不自觉' filler check
    #    (Replaces old regex that falsely rewarded '下意识/墙角有', which increased AI rate!)
    unconscious_hits = re.findall(r"(?:下意识|不自觉|不由自主|鬼使神差|莫名其妙)地?", narrative)
    metrics["unconscious_tic_count"] = len(unconscious_hits)
    if len(unconscious_hits) >= 3 or (len(unconscious_hits) >= 2 and chars < 1500):
        blocking.append(
            f"unconscious-tic-abuse (‘下意识/不自觉’机械点缀 {len(unconscious_hits)} 处): "
            f"{unconscious_hits[:4]} -> 删掉‘下意识/不自觉’，直接写具体动作"
        )
    
    if len(narrative_paras) >= 6:
        openers = []
        for p in narrative_paras:
            clean_p = re.sub(r"^[\s“”「」『』\"'，。！？—…]+", "", p)
            if len(clean_p) >= 2:
                openers.append(clean_p[:2])
        # Check 3+ consecutive identical 2-char openings or pronoun openings
        consec_subject = []
        for i in range(len(openers) - 2):
            if openers[i] == openers[i+1] == openers[i+2]:
                consec_subject.append(openers[i])
            elif all(o[0] in ("他", "她") for o in openers[i:i+3]):
                consec_subject.append("他/她")
        # Also check overall ratio of paragraphs starting with 他/她 or top repeated 2-char name
        pronoun_starts = sum(1 for o in openers if o[0] in ("他", "她"))
        prefix_counts: dict[str, int] = {}
        for o in openers:
            if o[0] not in ("他", "她"):
                prefix_counts[o] = prefix_counts.get(o, 0) + 1
        top_name, top_name_count = max(prefix_counts.items(), key=lambda x: x[1], default=("", 0))
        subject_start_share = round((pronoun_starts + (top_name_count if top_name_count >= 3 else 0)) / max(1, len(openers)), 3)
        metrics["subject_opening_share"] = subject_start_share
        
        if consec_subject or (len(openers) >= 8 and subject_start_share > 0.50):
            detail = f"连续同主语起段 {consec_subject[:3]}" if consec_subject else f"主语起段占比 {subject_start_share:.1%} ('{top_name}'+他/她)"
            blocking.append(
                f"subject-monotony (句首/段首主语复读严重: {detail}): "
                "AI极爱每段以‘他/她/主角名’打头 -> 请大量采用‘承前省略主语’，或用动作、器物、声响、话语直接起段"
            )
        elif len(openers) >= 8 and subject_start_share > 0.38:
            advisory.append(
                f"subject-monotony (主语起段占比偏高 {subject_start_share:.1%}): "
                "建议省去部分段落开头的‘他/她/人名’，改用动作或物件起句"
            )
        else:
            green.append(f"subject-monotony (段首主语占比 {subject_start_share:.1%} ≤ 38%)")
    
    # 5. Sentence length std & mechanical equal-length run check (R000 priority rule)
    sentences = packlib.sentence_pieces(packlib.mask_quoted(body))
    if len(sentences) >= 10:
        sent_lengths = [len(re.sub(r"\s", "", s)) for s in sentences if len(s.strip()) > 2]
        if len(sent_lengths) >= 10:
            min_sent_std = float('inf')
            worst_sent_window = None
            for i in range(len(sent_lengths) - 9):
                window = sent_lengths[i:i+10]
                std = statistics.stdev(window) if len(window) > 1 else 0
                if std < min_sent_std:
                    min_sent_std = std
                    worst_sent_window = (i+1, window)
            
            metrics["sentence_std_min"] = round(min_sent_std, 2)
            
            has_short = any(l < 10 for l in sent_lengths)
            has_long = any(l > 36 for l in sent_lengths)
            
            if min_sent_std < 5:
                blocking.append(
                    f"sentence-rhythm (句长标准差 {min_sent_std:.1f} < 5, 红区): "
                    f"第{worst_sent_window[0]}-{worst_sent_window[0]+9}句过于平滑 -> 连贯动作请用逗号串起，避免句句等长"
                )
            elif min_sent_std < 8:
                advisory.append(
                    f"sentence-rhythm (句长标准差 {min_sent_std:.1f}, 黄区 5-8): "
                    "建议增加句长变化"
                )
            else:
                green.append(f"sentence-rhythm (标准差 {min_sent_std:.1f} ≥ 8)")
            
            if not has_short or not has_long:
                advisory.append(
                    f"sentence-extremes (缺少极端句长): "
                    f"{'缺<10字短句' if not has_short else ''} "
                    f"{'缺>36字连贯复句' if not has_long else ''}"
                )
    
    # 6. Gratification announcement & essay connector density
    gratification_patterns = [
        r"所有人都(?:震惊|惊呆|愣住|傻眼)了",
        r"(?:他|她)终于(?:逆袭|翻身|成功|证明)了",
        r"这是属于(?:他|她)的(?:高光|荣耀|胜利)时刻",
        r"全场(?:寂静|沸腾|哗然)",
        r"(?:众人|所有人|全场)(?:都|全部|纷纷)(?:震惊|惊讶|佩服|折服)",
    ]
    
    gratification_hits = []
    for pattern in gratification_patterns:
        matches = re.findall(pattern, narrative)
        gratification_hits.extend(matches)
    
    if len(gratification_hits) > 0:
        blocking.append(
            f"gratification-announcement (爽点直接宣布 {len(gratification_hits)}处): "
            f"示例：{gratification_hits[:3]} -> 不宣布爽，让读者从对手反应、现场静默、利益翻转里感到爽"
        )
    else:
        green.append("gratification-announcement (无爽点宣布)")
    
    metrics["gratification_hits"] = len(gratification_hits)
    
    # 7. Synopsis mode & omniscient explanation tail detection
    synopsis_markers = [
        r"(?:于是|然后|接着|随后|紧接着)[，,][^。！？]{5,30}(?:决定|意识到|明白了|发现)",
        r"(?:他|她)[^。！？]{5,20}(?:很快|马上|立刻|随即)[^。！？]{5,20}(?:成功|解决|找到|揭穿)",
        r"在[^。！？]{3,10}(?:之后|以后|过后)[，,][^。！？]{10,30}[。！？]",
        r"(?:他|她)心里(?:很)?(?:清楚|明白)[，,][^。！？\n]{8,40}",
    ]
    
    synopsis_hits = []
    for pattern in synopsis_markers:
        matches = re.findall(pattern, narrative)
        synopsis_hits.extend(matches)
    
    synopsis_density = len(synopsis_hits) * 10000 / chars
    metrics["synopsis_density_per_10k"] = round(synopsis_density, 2)
    
    if synopsis_density > 8:
        advisory.append(
            f"synopsis-mode (剧情梗概/旁白解释密度 {synopsis_density:.1f}处/万字 > 8): "
            f"段落连续交代结果或替角色解释动机。示例：{synopsis_hits[:3]} -> 砍掉旁白解释尾巴，直接写现场动作与反应"
        )
    else:
        green.append(f"synopsis-mode ({synopsis_density:.1f}处/万字 ≤ 8)")

    # 8. Voice Card & Multi-speaker Homogeneity Check (声线卡防多人物语气一致复检)
    if len(dialogues) >= 6:
        dlg_lens = [len(re.sub(r"[\s“”‘’「」『』\"']", "", d)) for d in dialogues]
        dlg_std = statistics.pstdev(dlg_lens) if len(dlg_lens) > 1 else 0.0
        metrics["dialogue_length_std"] = round(dlg_std, 2)
        if dlg_std < 3.5:
            blocking.append(
                f"voice-card-homogeneity (多角色对白句长高度雷同，标准差 {dlg_std:.1f} < 3.5): "
                "全章角色开口长短如出一辙（AI千人一面通病） -> 请对照 `词句库/声线卡.md`，上位者用短促祈使/打断句，算计者用设问与条件句，拉开不同人物的句长与语气词差异"
            )
        elif dlg_std < 5.5:
            advisory.append(
                f"voice-card-homogeneity (对白句长波动偏小，标准差 {dlg_std:.1f}): "
                "建议对照声线卡拉开不同身份角色的台词长短与用词习惯"
            )
        else:
            green.append(f"voice-card-differentiation (对白句长标准差 {dlg_std:.1f} ≥ 5.5)")

    return {
        "blocking": blocking,
        "advisory": advisory,
        "green": green,
        "metrics": metrics,
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
        help="Optional path to target writing pack directory (or stats.json) for calibration; a wrong path is an error",
    )
    parser.add_argument(
        "--narrative-check",
        action="store_true",
        help="Enable narrative architecture scanning (dialogue ratio, emotion density, paragraph/sentence rhythm)",
    )
    parser.add_argument("--output", help="Optional JSON file path to write full UTF-8 report")
    args = parser.parse_args(argv)
    try:
        src = read_text_file(args.source) if args.source else None
        res = scan_text(read_text_file(args.file), source_text=src, pack_path=args.pack)
        
        # Add narrative architecture check if requested
        if args.narrative_check:
            pack_cal = load_pack_calibration(args.pack) if args.pack else default_calibration()
            narr_arch = scan_narrative_architecture(read_text_file(args.file), pack_cal)
            res["narrative_architecture"] = narr_arch
            # Merge blocking issues
            res["blocking"].extend(narr_arch["blocking"])
            res["blocking_count"] = len(res["blocking"])
            # Update status if new blocking issues found
            if narr_arch["blocking"] and res["status"] == "PASS":
                res["status"] = "FAIL"
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
        base_info = (
            f"STATUS={res['status']} BLOCKING={res['blocking_count']} WARNINGS={len(res['warnings'])} "
            f"PACK={json.dumps(res['pack_calibration'])} SENT_MEAN={res['sentence_mean']} SENT_SD={res['sentence_sd']} "
            f"LEX_HITS={res.get('lexicon_hits_count', 0)+res.get('connector_hits_count', 0)} "
            f"UNQUOTED_WO={res['unquoted_wo_count']} LENGTH_RATIO={res['length_ratio']} "
            f"NARR_COPY={res['narrative_copy_rate']}"
        )
        if args.narrative_check and "narrative_architecture" in res:
            narr = res["narrative_architecture"]
            narr_info = (
                f" NARR_BLOCKING={len(narr['blocking'])} NARR_ADVISORY={len(narr['advisory'])} "
                f"NARR_GREEN={len(narr['green'])}"
            )
            print(base_info + narr_info)
        else:
            print(base_info)
    else:
        # Pretty print with narrative architecture section if available
        if args.narrative_check and "narrative_architecture" in res:
            narr = res.pop("narrative_architecture")
            print("=" * 60)
            print("基础门禁检查")
            print("=" * 60)
            print(json.dumps(res, ensure_ascii=False, indent=2))
            print("\n" + "=" * 60)
            print("叙事架构检查（StoryScope指纹）")
            print("=" * 60)
            
            if narr["blocking"]:
                print("\n【BLOCKING】必须修复：")
                for item in narr["blocking"]:
                    print(f"  ✗ {item}")
            
            if narr["advisory"]:
                print("\n【ADVISORY】建议优化：")
                for item in narr["advisory"]:
                    print(f"  ⚠ {item}")
            
            if narr["green"]:
                print("\n【GREEN】已达标：")
                for item in narr["green"]:
                    print(f"  ✓ {item}")
            
            print("\n【指标】")
            for key, value in narr["metrics"].items():
                print(f"  {key}: {value}")
            print("=" * 60)
        else:
            print(json.dumps(res, ensure_ascii=False, indent=2))
    return 1 if res["status"] == "FAIL" else 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
