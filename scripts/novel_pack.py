#!/usr/bin/env python3
"""Mechanical helpers for novel writing packs; no model calls or AI scoring."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import statistics
import sys
import tempfile
import unicodedata
import zipfile

DIMENSIONS = (
    "语言与叙述", "场景与节奏", "人物与对白", "博弈与关系", "战斗与行动",
    "世界与题材", "修辞与用典", "信息与连载", "情绪与主题",
)
LEXICON_CATEGORIES = (
    "发力与博弈动词",
    "眼神与视线词",
    "面容与神态词",
    "肢体与生理反馈词",
    "开口与言语导引词",
    "心理算计与决断词",
    "程度与范围副词",
    "鲜活四字格与俗语",
    "声光打击与感官词",
    "环境器物与空间词",
    "世界观与筹码计量词",
)
CONNECTOR_TYPES = (
    "句内转折与推演",
    "段间过渡与承接",
    "突发打断与变故",
    "时间压缩与快进",
    "视角与空间切入",
)
IGNORE = {"readme.md", "agents.md", "skill.md", "pack.md", "chapter_index.md",
          "source_front_matter.md", "split_report.md"}
PENDING_COVERAGE_NOTE = "尚未完成阅读分析"
RULE_REF_RE = re.compile(r"(?<![A-Za-z0-9_])R\d{3,}(?!\d)")
TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates" / "pack-zh"
GENERIC_PLACEHOLDER_RE = re.compile(
    r"(?<![A-Za-z])(?:Rxxx|Sxxx|Cxxxx)(?![A-Za-z])|XX\.X|`XXXX`|`XX`|`X–Y`|<作品名>"
)
CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
             "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
CN_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10000}
NUMBER_TOKEN_RE = re.compile(r"\d+|[零〇一二两三四五六七八九十百千万]+")


def emit(value, json_out=None):
    rendered = json.dumps(value, ensure_ascii=False, indent=2)
    if json_out:
        out_path = Path(json_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_rows(path):
    if not path.exists():
        return []
    result = []
    for n, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if line.strip():
            try:
                item = json.loads(line)
            except ValueError as exc:
                raise ValueError(f"{path.name}:{n}: {exc}") from exc
            if not isinstance(item, dict):
                raise ValueError(f"{path.name}:{n}: expected JSON object")
            result.append(item)
    return result


def atomic_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                     dir=path.parent, delete=False, suffix=".tmp") as f:
        f.write(text)
        temporary = Path(f.name)
    os.replace(temporary, path)


def write_json(path, value):
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def write_rows(path, rows):
    atomic_text(path, "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def decode(data):
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            pass
    raise ValueError("Cannot decode input as UTF-8 or GB18030; convert it explicitly first")


def within(pack, relative):
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ValueError(f"Invalid relative path: {relative!r}")
    p = PurePosixPath(relative)
    if p.is_absolute() or PureWindowsPath(relative).drive or ".." in p.parts:
        raise ValueError(f"Path escapes pack: {relative}")
    result = (pack / relative).resolve()
    if not result.is_relative_to(pack.resolve()):
        raise ValueError(f"Path escapes pack (possibly symlink): {relative}")
    return result


def cn_to_int(s):
    if not any(ch in CN_UNITS for ch in s):
        return int("".join(str(CN_DIGITS[ch]) for ch in s))
    total = section = num = 0
    for ch in s:
        if ch in CN_DIGITS:
            num = CN_DIGITS[ch]
        elif CN_UNITS[ch] == 10000:
            total += ((section + num) or 1) * 10000
            section = num = 0
        else:
            section += (num or 1) * CN_UNITS[ch]
            num = 0
    return total + section + num


def natural_key(relative):
    """Sort 第2章 before 第10章 and 第四节 before 第十节."""
    parts = []
    for part in relative.parts:
        tokens = []
        pos = 0
        for m in NUMBER_TOKEN_RE.finditer(part):
            if m.start() > pos:
                tokens.append((1, part[pos:m.start()].casefold()))
            t = m.group(0)
            tokens.append((0, int(t) if t.isdigit() else cn_to_int(t)))
            pos = m.end()
        if pos < len(part):
            tokens.append((1, part[pos:].casefold()))
        parts.append(tuple(tokens))
    return tuple(parts), str(relative)


def collect(inputs, recursive, output):
    found = []
    for value in inputs:
        p = Path(value).resolve()
        if not p.exists():
            raise ValueError(f"Input does not exist: {p}")
        if p.is_dir():
            candidates = sorted(p.rglob("*") if recursive else p.iterdir(),
                                key=lambda q: natural_key(q.relative_to(p)))
        else:
            candidates = [p]
        for q in candidates:
            if not q.is_file() or q.suffix.lower() not in {".md", ".txt"}:
                continue
            if p.is_dir() and q.name.casefold() in IGNORE:
                continue
            resolved = q.resolve()
            if resolved == output or output in resolved.parents:
                continue
            if q.is_symlink():
                raise ValueError(f"Select the real source explicitly instead of a symlink: {q}")
            if resolved not in found:
                found.append(resolved)
    if not found:
        raise ValueError("No selected .md/.txt source files; pass explicit files or --recursive")
    prepared = []
    for p in found:
        data = p.read_bytes()
        text, encoding = decode(data)
        if not text.strip():
            raise ValueError(f"Empty input: {p}")
        prepared.append((p, data, text, encoding))
    return prepared


def import_prepared(pack, prepared, existing, notes):
    rows = list(existing)
    hashes = {x["sha256"]: x["id"] for x in rows}
    number = max((int(x["id"][1:]) for x in rows), default=0)
    added = 0
    for p, data, text, encoding in prepared:
        sha = digest(data)
        if sha in hashes:
            notes.append({"duplicate_input": p.name, "same_as": hashes[sha]})
            continue
        number += 1
        source_id = f"C{number:04d}"
        relative = f"corpus/{source_id}{p.suffix.lower()}"
        target = within(pack, relative)
        if target.exists():
            raise ValueError(f"Refusing to overwrite unlisted source: {relative}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        rows.append({"id": source_id, "file": relative, "input_name": p.name,
                     "sha256": sha, "encoding": encoding,
                     "line_count": len(text.splitlines()),
                     "chars_no_space": len(re.sub(r"\s", "", text))})
        hashes[sha] = source_id
        added += 1
    return rows, added


def distribution(values):
    if not values:
        return {"count": 0, "mean": 0, "sd": 0, "median": 0, "p10": 0, "p90": 0}
    values = sorted(values)
    sd = round(statistics.pstdev(values), 2) if len(values) > 1 else 0.0
    return {"count": len(values), "mean": round(statistics.mean(values), 2),
            "sd": sd, "median": statistics.median(values),
            "p10": values[int((len(values)-1)*.1)],
            "p90": values[int((len(values)-1)*.9)]}


MARKERS = (
    "不是", "而是", "并非", "其实", "事实上", "然而", "不过", "只是",
    "顿时", "忽然", "陡然", "猛地", "果然", "居然", "竟然", "莫非",
    "难道", "若是", "倘若", "一旦", "纵然", "即便", "何况", "反倒",
)


QUOTE_PAIRS = {"“": "”", "‘": "’", "「": "」", "『": "』", "【": "】"}


def _mask_line(line, fill):
    """Replace quoted speech/thought, 【】 panels and the quote marks with `fill`.

    Quote depth resets at every line so one unclosed quote cannot swallow
    the rest of the chapter.
    """
    closers = set(QUOTE_PAIRS.values())
    buf = []
    stack = []
    for ch in line:
        if ch in QUOTE_PAIRS:
            stack.append(QUOTE_PAIRS[ch])
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
            continue
        buf.append(fill)
    return re.sub(r'"[^"]*"', lambda m: fill * len(m.group(0)), "".join(buf))


def strip_quoted(text):
    """Drop quoted speech/thought and 【】 panels, keeping narration only."""
    return "\n".join(_mask_line(line, "") for line in text.splitlines())


def mask_quoted(text, fill="\x00"):
    """Like strip_quoted, but keeps every offset: dropped characters become `fill`."""
    return "\n".join(_mask_line(line, fill) for line in text.split("\n"))


def sentence_pieces(text):
    # The closing quote left after 。！？ (周管事道：“坐。”) is not a sentence.
    return [s for s in re.split(r"[。！？!?]+", text) if re.search(r"\w", s)]


def narrative_sentence_lengths(narrative):
    """Sentence lengths of quote-stripped narration.

    Line breaks also end a sentence: once 周管事淡淡道：“坐。” loses its quote,
    the speech tag must not merge with the following paragraphs.
    """
    lengths = []
    for s in re.split(r"[。！？!?\n]+", narrative):
        n = len(re.sub(r"\s", "", s))
        if n >= 2 and re.search(r"\w", s):
            lengths.append(n)
    return lengths


def text_stats(text):
    # Quoted text includes thought, quotations and dialogue; it is not a dialogue classifier.
    body = re.sub(r"(?m)^#{1,6}\s+.*$", "", text)
    compact = re.sub(r"\s", "", body)
    narrative = strip_quoted(body)
    raw_paras = [x.strip() for x in body.splitlines() if x.strip()]
    sentences = [len(re.sub(r"[\s“”‘’「」『』]", "", s))
                 for s in re.split(r"[。！？!?]+", body)]
    sentences = [x for x in sentences if x]
    paragraphs = [len(re.sub(r"\s", "", x)) for x in raw_paras]
    single_sent_paras = sum(1 for p in raw_paras if len(sentence_pieces(p)) <= 1)
    quoted = 0
    depth = 0
    for char in compact:
        if char in "“「『":
            depth += 1
        elif char in "”」』":
            depth = max(0, depth-1)
        elif depth:
            quoted += 1
    total_sents = max(1, len(sentences))
    short_sents = sum(1 for x in sentences if x <= 10)
    long_sents = sum(1 for x in sentences if x >= 40)
    scale = 10000.0 / max(1, len(compact))
    marker_rates = {
        m: round(body.count(m) * scale, 2)
        for m in MARKERS if body.count(m) > 0
    }
    sent_dist = distribution(sentences)
    sent_dist["short_le_10_share"] = round(short_sents / total_sents, 4)
    sent_dist["long_ge_40_share"] = round(long_sents / total_sents, 4)
    para_dist = distribution(paragraphs)
    para_dist["single_sentence_share"] = round(single_sent_paras / max(1, len(raw_paras)), 4)
    per_1k = 1000.0 / max(1, len(compact))
    return {"chars_no_space": len(compact), "sentence_chars": sent_dist,
            "narrative_sentence_chars": distribution(narrative_sentence_lengths(narrative)),
            "paragraph_chars": para_dist,
            "quoted_char_share": round(quoted/max(1, len(compact)), 4),
            "punctuation": {c: compact.count(c) for c in "，。！？；：—…"},
            "zhe_per_1k": round(body.count("着") * per_1k, 2),
            "narrative_semicolons_per_1k": round(narrative.count("；") * per_1k, 3),
            "markers_per_10k_chars": marker_rates}


def stats_for_pack(pack):
    sources = read_rows(pack / "corpus.jsonl")
    texts = []
    files = []
    for source in sources:
        data = within(pack, source["file"]).read_bytes()
        text = data.decode(source["encoding"])
        texts.append(text)
        files.append({"source_id": source["id"], **text_stats(text)})
    return {"source_units": len(sources), "meaning": "本批文本的机械统计，不代表语义分析或AI来源判断",
            "aggregate": text_stats("\n\n".join(texts)), "files": files}


# ---------------------------------------------------------------------------
# Lexicon, Connectors, Semantic Sentences & Voice Cards (Positive Replacement)
# ---------------------------------------------------------------------------

AI_SLOP_IDIOM_BLACKLIST = {
    "毋庸置疑", "显而易见", "理所当然", "截然不同", "彻头彻尾", "不折不扣",
    "天壤之别", "置若罔闻", "装聋作哑", "一清二楚", "五味杂陈", "百感交集",
    "不可思议", "难以置信", "不知所措", "手足无措", "不由自主", "情不自禁",
    "鬼使神差", "莫名其妙", "说来话长", "总而言之", "长话短说", "众所周知",
}
NON_IDIOM_4CHAR_RE = re.compile(
    r"[的了着过得地之与和及或而但把被让给向往从在当比跟同由对朝连]"
    r"|^[他她它我你这那哪一二两三四五六七八九十百千万第每各某再又也还都只才就便却更最很太真好]|"
    r"[他她它我你这那哪了着过呢吧吗啊呀啦哩]$"
)
CONNECTOR_PATTERNS = {
    "句内转折与推演": (
        "若是", "倘若", "纵然", "纵使", "即便", "饶是", "哪怕", "偏偏", "偏生",
        "殊不知", "岂料", "孰料", "反倒", "反而", "非但", "何况", "况且", "一旦",
        "固然", "诚然", "只怕", "莫说", "非要", "硬生生", "索性", "干脆",
    ),
    "段间过渡与承接": (
        "如此一来", "即便如此", "饶是如此", "念及此处", "想到这里", "思忖间",
        "电光石火间", "转念之间", "话虽如此", "饶是这般", "正因如此", "果不其然",
    ),
    "突发打断与变故": (
        "话音未落", "言未毕", "说时迟那时快", "陡然间", "蓦地", "倏忽", "冷不防",
        "猛然间", "刹那间", "顷刻间", "呼吸之间", "眨眼功夫", "说那迟", "异变突起",
    ),
    "时间压缩与快进": (
        "数日后", "半月后", "片刻之后", "盏茶功夫", "半晌", "良久", "不多时",
        "一连数日", "日复一日", "转眼间", "数息之后", "三日后", "数月过去",
    ),
    "视角与空间切入": (
        "反观", "再看", "放眼望去", "举目望去", "视线越过", "目光落向",
        "定睛一看", "循声望去", "抬眼望去", "凝神看去", "另一边", "场中",
    ),
}
CATEGORY_EXTRACT_RULES = (
    (
        "开口与言语导引词",
        re.compile(
            r"[\u4e00-\u9fff]{1,4}(?:冷笑|嗤笑|怪笑|大笑|轻笑|微笑|苦笑|狞笑|狂笑|失笑|哑然)"
            r"(?:一声|道|着)?"
            r"|[\u4e00-\u9fff]{1,3}(?:喝道|吼道|叫道|嚷道|叹道|斥道|骂道|问|应道|答道|沉声道|厉声道|冷声道|朗声道|低声道)"
            r"|(?:勃然大怒|破口大骂|随口应道|沉吟片刻|默然半晌|开门见山|直言不讳)"
        ),
        "替代 AI 的‘淡声道/缓声道/森然警告’",
    ),
    (
        "眼神与视线词",
        re.compile(
            r"(?:目光|眼神|视线|双目|双眸|眼眸|眼帘|眼皮|眼角|眸光|眸子)[\u4e00-\u9fff]{1,4}"
            r"|[\u4e00-\u9fff]{1,2}(?:眯起眼|睁大眼|瞥了|扫过|凝视|打量|注视|盯住|收回目光|移开视线)"
        ),
        "替代 AI 的‘垂眸/睨着/瞳孔骤缩/眼中闪过一丝’",
    ),
    (
        "面容与神态词",
        re.compile(
            r"(?:神色|脸色|面色|面容|神情|表情|眉头|眉宇|嘴角|唇角|牙关)[\u4e00-\u9fff]{1,4}"
            r"|(?:面无表情|面色如常|脸色铁青|面沉如水|神色凛然|眉头紧锁|眉头一皱|咬牙切齿|皮笑肉不笑|怡然不惧)"
        ),
        "替代 AI 的‘平淡无波/嘴角勾起一抹/不见半点波澜’",
    ),
    (
        "肢体与生理反馈词",
        re.compile(
            r"(?:袖袍|衣袖|手掌|五指|拳头|脚步|身形|身躯|步伐|后背|心头|心神)[\u4e00-\u9fff]{1,4}"
            r"|(?:拂袖|拱手|抱拳|负手|昂首|挺身|暴退|疾退|顿足|驻足|盘膝|长吐一口气|心神剧震|头皮发麻)"
        ),
        "替代 AI 的‘指腹摩挲/指节泛白/深吸一口气/抠进掌心’",
    ),
    (
        "心理算计与决断词",
        re.compile(
            r"(?:心中|心底|心头|心念|脑海|念头|心下|暗自)[\u4e00-\u9fff]{1,4}"
            r"|(?:暗忖|思忖|权衡|盘算|计议|笃定|断定|恍然|凛然|警兆|杀机|杀心|当机立断|退意)"
        ),
        "替代 AI 的‘心中自然雪亮/脑中彻底贯通/下意识’",
    ),
    (
        "发力与博弈动词",
        re.compile(
            r"(?:催动|催发|迸发|倾泻|席卷|笼罩|镇压|封锁|瓦解|洞穿|撕裂|吞噬|湮灭|硬撼|周旋|试探|牵制|压制|反扑|暴起|搏杀|催谷|祭出|掐诀|横扫|斩落|劈开)"
        ),
        "替代空泛的‘发动攻击/进行防御/开始反击’",
    ),
    (
        "声光打击与感官词",
        re.compile(
            r"(?:轰隆|咔嚓|噗嗤|铮然|呼啸|咆哮|轰鸣|炸响|震耳欲聋|刺目|耀眼|血腥|腥风|煞气|寒芒|剑光|刀芒|巨力|气浪|余波)"
        ),
        "替代通感比喻‘针扎似的凉意/潮水般的疲惫’",
    ),
    (
        "程度与范围副词",
        re.compile(
            r"(?:足足|整整|略微|稍稍|隐隐|俨然|赫然|断然|绝然|全然|尽数|一举|顷刻|瞬息|彻骨|滔天|莫大|一发|愈发|越发)"
        ),
        "替代 AI 告知副词‘非常/十分/极其/格外/相当’",
    ),
    (
        "环境器物与空间词",
        re.compile(
            r"(?:山巅|山谷|密室|大殿|洞府|书房|院落|苍穹|天际|半空|四周|方圆|百丈|千步|残骸|废墟|阴影|夜幕|血泊|案几|蒲团)"
        ),
        "用于动作咬合的空间地形与现场锚定",
    ),
    (
        "世界观与筹码计量词",
        re.compile(
            r"(?:元石|灵石|银两|铜钱|寿元|修为|境界|资质|底蕴|底牌|筹码|代价|利弊|得失|损耗|根基|战力|道痕|仙窍|仙元|真元|蛊虫|杀招|法宝|丹药|符箓|阵法|气运)"
        ),
        "原著筹码与体系名物（跨书调用时按【新书设定转换槽】对标替换）",
    ),
)

SEMANTIC_SENTENCE_RULES = (
    ("利害权衡与算账推演", ("利弊", "得失", "代价", "筹码", "划算", "止损", "风险", "若是", "纵然")),
    ("看破陷阱与认知差", ("破绽", "算计", "陷阱", "伪装", "试探", "虚实", "古怪", "岂料", "殊不知")),
    ("战局压迫与破招定格", ("杀招", "破绽", "硬撼", "暴退", "重创", "瞬息", "镇压", "一击", "生死")),
    ("地位施压与给台阶", ("规矩", "体面", "放肆", "老夫", "本座", "大人", "管事", "罪过", "台阶")),
    ("绝境决断与狠辣取舍", ("绝境", "生机", "弃子", "断腕", "不悔", "九死一生", "搏命", "决断")),
)


def empty_lexicon_skeleton(pack_name=""):
    return {
        "format_version": 1,
        "pack_name": pack_name,
        "distillation_mode": "selected_chapters",
        "external_lexicon_files": 0,
        "world_terms_lock": [],
        "term_transfer_map": [],
        "categories": {cat: [] for cat in LEXICON_CATEGORIES},
        "connectors": {ct: [] for ct in CONNECTOR_TYPES},
        "sentence_bank": [],
        "voice_cards": [],
    }


def _iter_lexicon_sources(pack, lexicon_source_paths=None, recursive=True):
    """Yield (source_id, input_name, text) from bundled corpus and optional external full-book folder/zip."""
    sources = read_rows(within(pack, "corpus.jsonl"))
    for s in sources:
        p = within(pack, s["file"])
        if p.is_file():
            text, _ = decode(p.read_bytes())
            yield s["id"], s.get("input_name", s["id"]), text
    if not lexicon_source_paths:
        return
    ext_idx = 0
    for raw in lexicon_source_paths:
        src_path = Path(raw).resolve()
        if not src_path.exists():
            raise ValueError(f"Lexicon source does not exist: {src_path}")
        if src_path.is_file() and src_path.suffix.lower() == ".zip":
            with zipfile.ZipFile(src_path, "r") as zf:
                for info in sorted(zf.infolist(), key=lambda i: natural_key(PurePosixPath(i.filename))):
                    if info.is_dir() or PurePosixPath(info.filename).suffix.lower() not in {".md", ".txt"}:
                        continue
                    if PurePosixPath(info.filename).name.casefold() in IGNORE:
                        continue
                    ext_idx += 1
                    text, _ = decode(zf.read(info.filename))
                    yield f"EXT{ext_idx:04d}", PurePosixPath(info.filename).name, text
        elif src_path.is_dir():
            cands = sorted(src_path.rglob("*") if recursive else src_path.iterdir(),
                           key=lambda q: natural_key(q.relative_to(src_path)))
            for q in cands:
                if not q.is_file() or q.suffix.lower() not in {".md", ".txt"}:
                    continue
                if q.name.casefold() in IGNORE:
                    continue
                ext_idx += 1
                text, _ = decode(q.read_bytes())
                yield f"EXT{ext_idx:04d}", q.name, text
        elif src_path.is_file() and src_path.suffix.lower() in {".md", ".txt"}:
            ext_idx += 1
            text, _ = decode(src_path.read_bytes())
            yield f"EXT{ext_idx:04d}", src_path.name, text


def _find_line_and_quote(lines, word):
    for idx, line in enumerate(lines, 1):
        if word in line:
            s = line.strip()
            if len(s) > 72:
                pos = s.find(word)
                start = max(0, pos - 20)
                end = min(len(s), pos + len(word) + 28)
                s = s[start:end]
            return idx, s
    return 1, word


def scan_lexicon_for_pack(pack, lexicon_source_paths=None, top_k=20):
    """Zero-token local extraction of 11-category vocabulary, connectors, sentence bank & voice cards."""
    pack = Path(pack).resolve()
    manifest_path = within(pack, "manifest.json")
    manifest = read_json(manifest_path) if manifest_path.is_file() else {}
    pack_name = manifest.get("name", pack.name)

    cat_counts = {cat: {} for cat in LEXICON_CATEGORIES}
    cat_first_ev = {cat: {} for cat in LEXICON_CATEGORIES}
    cat_replaces = {}
    conn_counts = {ct: {} for ct in CONNECTOR_TYPES}
    conn_first_ev = {ct: {} for ct in CONNECTOR_TYPES}
    idiom_counts = {}
    idiom_ev = {}
    sent_bank = []
    seen_sents = set()
    speaker_stats = {}

    bundled_lines = {}
    ext_files = 0
    total_chars = 0

    for sid, iname, text in _iter_lexicon_sources(pack, lexicon_source_paths):
        is_bundled = sid.startswith("C")
        if not is_bundled:
            ext_files += 1
        body = re.sub(r"(?m)^#{1,6}\s+.*$", "", text)
        total_chars += len(re.sub(r"\s", "", body))
        lines = text.splitlines()
        if is_bundled:
            bundled_lines[sid] = lines
        narrative = strip_quoted(body)

        # 1. Category regex extraction
        for cat, pat, rep_hint in CATEGORY_EXTRACT_RULES:
            cat_replaces[cat] = rep_hint
            target_text = body if cat == "世界观与筹码计量词" else narrative
            for m in pat.finditer(target_text):
                w = m.group(0).strip()
                if len(w) < 2 or len(w) > 8:
                    continue
                cat_counts[cat][w] = cat_counts[cat].get(w, 0) + 1
                if is_bundled and w not in cat_first_ev[cat]:
                    ln, q = _find_line_and_quote(lines, w)
                    cat_first_ev[cat][w] = {"source_id": sid, "start_line": ln, "end_line": ln, "quote": q}

        # 2. 4-char vivid expressions / idioms
        for clause in re.split(r"[，。！？；：“”‘’「」『』（）()\s]+", narrative):
            if len(clause) == 4 and re.fullmatch(r"[\u4e00-\u9fff]{4}", clause):
                if clause not in AI_SLOP_IDIOM_BLACKLIST and not NON_IDIOM_4CHAR_RE.search(clause):
                    idiom_counts[clause] = idiom_counts.get(clause, 0) + 1
                    if is_bundled and clause not in idiom_ev:
                        ln, q = _find_line_and_quote(lines, clause)
                        idiom_ev[clause] = {"source_id": sid, "start_line": ln, "end_line": ln, "quote": q}
            elif len(clause) >= 4:
                for m4 in re.finditer(
                    r"(?:[\u4e00-\u9fff]然[\u4e00-\u9fff]{2}|[\u4e00-\u9fff]{2}如[\u4e00-\u9fff]|"
                    r"不[\u4e00-\u9fff]不[\u4e00-\u9fff]|一[\u4e00-\u9fff]不[\u4e00-\u9fff]|"
                    r"半[\u4e00-\u9fff]半[\u4e00-\u9fff]|自[\u4e00-\u9fff]自[\u4e00-\u9fff]|"
                    r"千[\u4e00-\u9fff]万[\u4e00-\u9fff]|無[\u4e00-\u9fff]無[\u4e00-\u9fff]|"
                    r"无[\u4e00-\u9fff]无[\u4e00-\u9fff]|有[\u4e00-\u9fff]有[\u4e00-\u9fff])",
                    clause,
                ):
                    w4 = m4.group(0)
                    if w4 not in AI_SLOP_IDIOM_BLACKLIST and not NON_IDIOM_4CHAR_RE.search(w4):
                        idiom_counts[w4] = idiom_counts.get(w4, 0) + 1
                        if is_bundled and w4 not in idiom_ev:
                            ln, q = _find_line_and_quote(lines, w4)
                            idiom_ev[w4] = {"source_id": sid, "start_line": ln, "end_line": ln, "quote": q}

        # 3. Connectors
        for ctype, words in CONNECTOR_PATTERNS.items():
            for w in words:
                c = narrative.count(w)
                if c > 0:
                    conn_counts[ctype][w] = conn_counts[ctype].get(w, 0) + c
                    if is_bundled and w not in conn_first_ev[ctype]:
                        ln, q = _find_line_and_quote(lines, w)
                        conn_first_ev[ctype][w] = {"source_id": sid, "start_line": ln, "end_line": ln, "quote": q}

        # 4. Semantic sentence bank (only from bundled chapters so source_id is in corpus.jsonl)
        if is_bundled and len(sent_bank) < 45:
            for lno, line in enumerate(lines, 1):
                s = line.strip()
                if len(s) < 16 or len(s) > 110 or s.startswith("#"):
                    continue
                for sem_label, kws in SEMANTIC_SENTENCE_RULES:
                    if sum(1 for kw in kws if kw in s) >= 2 and s not in seen_sents:
                        seen_sents.add(s)
                        sent_bank.append({
                            "id": f"SB{len(sent_bank)+1:03d}",
                            "semantic": sem_label,
                            "scope": "通用写法",
                            "pattern": "先点处境条件/破绽 -> 转折推演利害 -> 简短动作或判断收束",
                            "source_id": sid,
                            "start_line": lno,
                            "end_line": lno,
                            "quote": s,
                        })
                        break

        # 5. Speaker dialogue voice cards
        for lno, line in enumerate(lines, 1):
            for m_sp in re.finditer(
                r"([\u4e00-\u9fff]{2,4}?)(?:冷笑|嗤笑|淡淡|沉声|厉声|开口|摇头|点头|缓步|眯眼|皱眉)?(?:道|说|问|喝道|叹道|笑道)[：:]?\s*[“「]([^”」\n]{3,80})[”」]",
                line,
            ):
                sp_name, sp_quote = m_sp.group(1), m_sp.group(2).strip()
                if any(bad in sp_name for bad in ("自己", "他们", "众人", "有人", "一人", "那人", "此人", "少女", "少年", "老者")):
                    continue
                info = speaker_stats.setdefault(sp_name, {"count": 0, "lengths": [], "quotes": []})
                info["count"] += 1
                info["lengths"].append(len(sp_quote))
                if is_bundled and len(info["quotes"]) < 3:
                    info["quotes"].append({"source_id": sid, "start_line": lno, "end_line": lno, "quote": sp_quote})

    for w4, cnt in idiom_counts.items():
        cat_counts["鲜活四字格与俗语"][w4] = cnt
        if w4 in idiom_ev:
            cat_first_ev["鲜活四字格与俗语"][w4] = idiom_ev[w4]
    cat_replaces["鲜活四字格与俗语"] = "替代 AI 套路成语‘毋庸置疑/截然不同/理所当然/五味杂陈’"

    categories_out = {}
    for cat in LEXICON_CATEGORIES:
        ranked = sorted(cat_counts[cat].items(), key=lambda x: (-x[1], x[0]))[:top_k]
        items = []
        for w, cnt in ranked:
            ev = cat_first_ev[cat].get(w)
            entry = {
                "word": w,
                "count": cnt,
                "replaces_ai": cat_replaces.get(cat, "替代 AI 默认套话"),
            }
            if cat == "世界观与筹码计量词":
                entry["transfer_slot"] = "跨世界观调用时按新书体系对标替换"
            if ev:
                entry["evidence"] = ev
            items.append(entry)
        categories_out[cat] = items

    connectors_out = {}
    for ct in CONNECTOR_TYPES:
        ranked = sorted(conn_counts[ct].items(), key=lambda x: (-x[1], x[0]))[:15]
        items = []
        for w, cnt in ranked:
            ev = conn_first_ev[ct].get(w)
            entry = {"word": w, "count": cnt, "replaces_ai": "替代 AI 机械过渡词‘然而/就在这时/与此同时/紧接着’"}
            if ev:
                entry["evidence"] = ev
            items.append(entry)
        connectors_out[ct] = items

    voice_cards = []
    ranked_speakers = sorted(speaker_stats.items(), key=lambda x: (-x[1]["count"], x[0]))[:8]
    for sp_name, info in ranked_speakers:
        if info["count"] < 2 and len(ranked_speakers) > 2:
            continue
        mean_l = round(statistics.mean(info["lengths"]), 1) if info["lengths"] else 14.0
        tone = "短促冷硬、祈使果决" if mean_l < 16 else "层层设问、算计施压" if mean_l > 26 else "言简意赅、直指利害"
        voice_cards.append({
            "role": sp_name,
            "dialogue_samples": info["count"],
            "avg_utterance_chars": mean_l,
            "tone_profile": tone,
            "taboo": "严禁与场内其他角色共用同一套口头禅或句长节奏；严禁书面说教腔",
            "evidence": info["quotes"][:2],
        })
    if not voice_cards:
        voice_cards = [
            {
                "role": "上位掌权者/老手（通用声线模板）",
                "dialogue_samples": 0,
                "avg_utterance_chars": 13.5,
                "tone_profile": "短句定性、掌握打断权、只问结果与期限",
                "taboo": "不替下位者解释、不用长篇感叹句",
                "evidence": [],
            },
            {
                "role": "算计博弈者/主角（通用声线模板）",
                "dialogue_samples": 0,
                "avg_utterance_chars": 21.0,
                "tone_profile": "先顺势应承、再点破利益漏洞、抛出折中条件",
                "taboo": "不喊空洞口号、不暴露全部底牌",
                "evidence": [],
            },
        ]

    # World-specific proper terms that should be converted when writing a different setting
    world_specific_candidates = {"蛊虫", "仙窍", "道痕", "真元海", "空窍", "仙蛊", "凡蛊", "杀招", "青茅山", "天庭", "长生天"}
    world_terms_lock = [
        x["word"] for x in categories_out.get("世界观与筹码计量词", [])
        if x["word"] in world_specific_candidates
    ]

    lex_data = {
        "format_version": 1,
        "pack_name": pack_name,
        "distillation_mode": "selected_chapters_plus_full_lexicon" if ext_files else "selected_chapters",
        "bundled_sources_scanned": len(bundled_lines),
        "external_lexicon_files": ext_files,
        "total_chars_scanned": total_chars,
        "world_terms_lock": world_terms_lock,
        "term_transfer_map": [
            {"source_term": "元石/仙元石", "xianxia": "灵石/仙玉", "historical": "现银/官银", "urban": "现金/流动资金"},
            {"source_term": "蛊虫/仙蛊/本命蛊", "xianxia": "法器/本命法宝/符箓", "historical": "兵刃/信物/暗器", "urban": "核心专利/底牌把柄"},
            {"source_term": "空窍/仙窍/真元海", "xianxia": "丹田/气海/紫府", "historical": "内息/体力/家底", "urban": "账户储备/精力"},
            {"source_term": "杀招/道痕", "xianxia": "神通/剑阵/法则道韵", "historical": "杀手锏/合击战阵", "urban": "组合做空/连环杀招"},
        ],
        "categories": categories_out,
        "connectors": connectors_out,
        "sentence_bank": sent_bank[:30],
        "voice_cards": voice_cards,
    }
    write_json(within(pack, "lexicon.json"), lex_data)
    render_lexicon_markdown_files(pack, lex_data)
    return {
        "status": "PASS",
        "pack": str(pack),
        "distillation_mode": lex_data["distillation_mode"],
        "bundled_sources_scanned": len(bundled_lines),
        "external_lexicon_files": ext_files,
        "total_chars_scanned": total_chars,
        "categories_populated": sum(1 for v in categories_out.values() if v),
        "total_vocab_items": sum(len(v) for v in categories_out.values()),
        "total_connectors": sum(len(v) for v in connectors_out.values()),
        "sentence_bank_count": len(lex_data["sentence_bank"]),
        "voice_cards_count": len(voice_cards),
    }


def render_lexicon_markdown_files(pack, lex_data):
    """Render human/LLM-readable 词句库/*.md from lexicon.json."""
    pack = Path(pack).resolve()
    # 1. 词句库/词汇库.md
    lines_v = [
        f"# 11类原著正向替换词汇库（{lex_data.get('pack_name', '')}）",
        "",
        "> **核心用途**：写作与改写时，用本库中的**原著高频实感词**正向替换大模型底层默认腻子词（如 `垂眸/睨着/瞳孔骤缩/深吸一口气/淡声道/五味杂陈`）。",
        f"> **扫描范围**：精选语料 `{lex_data.get('bundled_sources_scanned', 0)}` 章"
        + (f" + 全书词源 `{lex_data.get('external_lexicon_files', 0)}` 章" if lex_data.get("external_lexicon_files") else "")
        + f"（共 `{lex_data.get('total_chars_scanned', 0)}` 字）。",
        "",
    ]
    for idx, cat in enumerate(LEXICON_CATEGORIES, 1):
        items = lex_data.get("categories", {}).get(cat, [])
        rep = items[0].get("replaces_ai", "替代 AI 默认套话") if items else "替代 AI 默认套话"
        lines_v.append(f"## {idx}. {cat}")
        lines_v.append(f"- **正向替换目标**：{rep}")
        if items:
            word_tags = [f"`{x['word']}`({x['count']})" for x in items[:15]]
            lines_v.append(f"- **高频词表**：{'、'.join(word_tags)}")
            ev_items = [x for x in items if isinstance(x.get("evidence"), dict)][:3]
            for x in ev_items:
                ev = x["evidence"]
                lines_v.append(f"  - 例证（`{ev['source_id']}` 第 `{ev['start_line']}` 行）：「{ev['quote']}」")
        else:
            lines_v.append("- **高频词表**：（待补充）")
        lines_v.append("")

    if lex_data.get("term_transfer_map"):
        lines_v.extend([
            "## 附：跨题材/新书设定转换槽（防原著专名串戏）",
            "",
            "| 原著体系词 | 仙侠/修真对标转换 | 历史/武侠/权谋对标转换 | 都市/现代对标转换 |",
            "| --- | --- | --- | --- |",
        ])
        for row in lex_data["term_transfer_map"]:
            lines_v.append(f"| `{row['source_term']}` | {row['xianxia']} | {row['historical']} | {row['urban']} |")
        lines_v.append("")
    atomic_text(within(pack, "词句库/词汇库.md"), "\n".join(lines_v))

    # 2. 词句库/衔接库.md
    lines_c = [
        f"# 原著逻辑衔接与转场库（{lex_data.get('pack_name', '')}）",
        "",
        "> **核心用途**：彻底替换 AI 机械过渡词（`然而/就在这时/与此同时/紧接着/然后/不仅如此`），复刻原著句内推演与段间切换的真实咬合。",
        "",
    ]
    for idx, ct in enumerate(CONNECTOR_TYPES, 1):
        items = lex_data.get("connectors", {}).get(ct, [])
        lines_c.append(f"## {idx}. {ct}")
        if items:
            tags = [f"`{x['word']}`({x['count']})" for x in items]
            lines_c.append(f"- **原著高频衔接词**：{'、'.join(tags)}")
            for x in [i for i in items if isinstance(i.get("evidence"), dict)][:3]:
                ev = x["evidence"]
                lines_c.append(f"  - 原句（`{ev['source_id']}` 第 `{ev['start_line']}` 行）：「{ev['quote']}」")
        else:
            lines_c.append("- **原著高频衔接词**：靠动作承前省略主语直接推进，少用显性连接词。")
        lines_c.append("")
    atomic_text(within(pack, "词句库/衔接库.md"), "\n".join(lines_c))

    # 3. 词句库/句段库.md
    lines_s = [
        f"# 剧情语义句段库（通用碰撞与差异骨架）（{lex_data.get('pack_name', '')}）",
        "",
        "> **核心用途**：按当前剧情语义（算账、看破陷阱、施压、破招、绝境决断）对标原著句法骨架，只借其呼吸节奏与转折结构，严禁照抄原句专名。",
        "",
    ]
    by_sem = {}
    for item in lex_data.get("sentence_bank", []):
        by_sem.setdefault(item.get("semantic", "通用推演"), []).append(item)
    if by_sem:
        for sem, rows in by_sem.items():
            lines_s.append(f"## 【{sem}】")
            for r in rows[:6]:
                lines_s.append(
                    f"- **`{r.get('id', 'SB')}`**（{r.get('scope', '通用写法')} | `{r['source_id']}` 第 `{r['start_line']}` 行）："
                    f"「{r['quote']}」"
                )
            lines_s.append("")
    else:
        lines_s.append("（运行 `novel_pack.py scan-lexicon --pack <包路径>` 自动提取原著高频语义句段）\n")
    atomic_text(within(pack, "词句库/句段库.md"), "\n".join(lines_s))

    # 4. 词句库/声线卡.md
    lines_vc = [
        f"# 角色声线卡与多人物防串味档案（{lex_data.get('pack_name', '')}）",
        "",
        "> **核心用途**：写对白与写后复检时强制核对，杜绝 AI“全员同一个腔调、全员书面语讲道理”的通病。",
        "",
        "| 角色/身份槽位 | 样本数 | 平均句长 | 声线腔调特征 | 禁忌（防串味红线） |",
        "| --- | --- | --- | --- | --- |",
    ]
    for vc in lex_data.get("voice_cards", []):
        lines_vc.append(
            f"| **{vc.get('role')}** | {vc.get('dialogue_samples', 0)} | {vc.get('avg_utterance_chars', 0)}字 | "
            f"{vc.get('tone_profile', '')} | {vc.get('taboo', '')} |"
        )
    lines_vc.append("")
    for vc in lex_data.get("voice_cards", []):
        if vc.get("evidence"):
            lines_vc.append(f"### {vc.get('role')} 原著对白例证")
            for ev in vc["evidence"]:
                lines_vc.append(f"- （`{ev['source_id']}` 第 `{ev['start_line']}` 行）：「{ev['quote']}」")
            lines_vc.append("")
    atomic_text(within(pack, "词句库/声线卡.md"), "\n".join(lines_vc))


def lookup_lexicon(pack, query=None, category=None, limit=8):
    """Query the pack's lexicon, connectors, semantic sentence bank, scenes & rules before writing."""
    pack = Path(pack).resolve()
    lex_path = within(pack, "lexicon.json")
    if not lex_path.is_file():
        scan_lexicon_for_pack(pack)
    lex = read_json(lex_path)
    q_tokens = [t.strip() for t in re.split(r"[\s,，、/]+", query or "") if t.strip()]

    def matches_query(text_blob):
        if not q_tokens:
            return True
        return any(tok in text_blob for tok in q_tokens)

    matched_cats = {}
    for cat, items in (lex.get("categories") or {}).items():
        if category and category not in cat:
            continue
        filtered = [x for x in items if matches_query(cat + " " + x.get("word", "") + " " + x.get("replaces_ai", ""))]
        if not filtered and not category:
            filtered = items[: min(4, limit)]
        if filtered:
            matched_cats[cat] = filtered[:limit]

    matched_conns = {
        ct: items[: min(6, limit)]
        for ct, items in (lex.get("connectors") or {}).items()
        if items
    }

    sents = lex.get("sentence_bank") or []
    matched_sents = [
        s for s in sents
        if matches_query(s.get("semantic", "") + " " + s.get("quote", ""))
    ]
    if not matched_sents:
        matched_sents = sents[:limit]

    scenes_path = within(pack, "scenes.jsonl")
    matched_scenes = []
    if scenes_path.is_file() and q_tokens:
        for sc in read_rows(scenes_path):
            blob = " ".join(sc.get("tags") or []) + " " + sc.get("summary", "")
            if matches_query(blob):
                matched_scenes.append({"id": sc.get("id"), "tags": sc.get("tags"), "summary": sc.get("summary")})

    return {
        "pack": lex.get("pack_name", pack.name),
        "query": query or "",
        "categories": matched_cats,
        "connectors": matched_conns,
        "sentence_bank": matched_sents[:limit],
        "voice_cards": (lex.get("voice_cards") or [])[:5],
        "term_transfer_map": lex.get("term_transfer_map") or [],
        "matched_scenes": matched_scenes[:limit],
    }


def init_pack(args):
    output = Path(args.output).resolve()
    if output.exists():
        raise ValueError(f"Output already exists; init never overwrites: {output}")
    prepared = collect(args.input, args.recursive, output)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Keep a failed staging directory as evidence; never recursively remove user paths.
    staging = Path(tempfile.mkdtemp(prefix=".novel-pack-", dir=output.parent)).resolve()
    notes = []
    sources, count = import_prepared(staging, prepared, [], notes)
    manifest = {"type": "novel-writing-pack", "format_version": 1, "name": args.name,
                "aliases": args.alias or [], "kind": args.kind, "status": "draft",
                "revision": 1, "source_mode": "bundled",
                "scope": "仅本批用户提供的正文；尚未完成语义蒸馏", "modules": [],
                "intake_notes": notes}
    write_json(staging / "manifest.json", manifest)
    write_rows(staging / "corpus.jsonl", sources)
    for name in ("analysis.jsonl", "rules.jsonl", "scenes.jsonl"):
        write_rows(staging / name, [])
    write_json(staging / "coverage.json", {"dimensions": [
        {"dimension": d, "status": "unobserved", "rule_ids": [],
         "note": PENDING_COVERAGE_NOTE, "suggested_inputs": []} for d in DIMENSIONS]})
    atomic_text(staging / "PACK.md", f"# {args.name}\n\n已收集{count}份文本，尚未完成语义蒸馏。状态以 manifest.json 为准。\n")
    atomic_text(staging / "缺口与补充建议.md", "# 缺口与补充建议\n\n完成本批阅读后，依据实际覆盖填写。\n")
    write_json(staging / "stats.json", stats_for_pack(staging))
    lex_sources = getattr(args, "lexicon_source", None)
    scan_lexicon_for_pack(staging, lexicon_source_paths=lex_sources)
    if output.exists():
        raise ValueError(f"Output appeared during collection; staging retained at {staging}")
    staging.rename(output)
    return {"status": "collected", "pack": str(output), "source_units": count,
            "semantic_analysis": "pending", "intake_notes": notes}


def add_sources(args):
    pack = Path(args.pack).resolve()
    manifest = read_json(pack / "manifest.json")
    if manifest.get("type") != "novel-writing-pack" or manifest.get("source_mode") != "bundled":
        raise ValueError("add requires a bundled novel writing pack")
    integrity = validate_pack(pack, allow_draft=True)
    if integrity["errors"]:
        raise ValueError("Repair pack integrity before adding: " + "; ".join(integrity["errors"]))
    prepared = collect(args.input, args.recursive, pack)
    notes = list(manifest.get("intake_notes", []))
    sources, count = import_prepared(pack, prepared, read_rows(pack / "corpus.jsonl"), notes)
    lex_sources = getattr(args, "lexicon_source", None)
    if count or lex_sources:
        if count:
            manifest["status"] = "draft"
            manifest["revision"] += 1
            manifest["scope"] = "已追加输入；需完成新增阅读并复核指纹和缺口"
            write_rows(pack / "corpus.jsonl", sources)
            write_json(pack / "stats.json", stats_for_pack(pack))
        scan_lexicon_for_pack(pack, lexicon_source_paths=lex_sources)
    manifest["intake_notes"] = notes
    write_json(pack / "manifest.json", manifest)
    return {"status": manifest["status"], "added": count, "source_units": len(sources),
            "semantic_analysis": "pending" if count else "unchanged"}


DEPTH_MIN_SOURCES = 10  # smaller packs cannot meet corpus-size targets, so they get no size warnings


def depth_targets(n_sources):
    """Distillation depth that the amount of corpus should support."""
    return {
        "min_rules": min(80, 27 + n_sources // 6),
        "min_scenes": min(120, max(10, n_sources // 2)),
        "min_cited_share": 0.6,
        "min_counterevidence_share": 0.2,
    }


def depth_report(sources, rules, scenes):
    """Advisory measures of how much of the corpus the pack actually used.

    Never produces errors: a 1-chapter pack is legitimate, it is just shallow.
    """
    source_ids = {s.get("id") for s in sources if isinstance(s, dict)}
    n = len(source_ids)
    targets = depth_targets(n)

    def ev_sources(rule):
        return {e.get("source_id") for e in rule.get("evidence") or [] if isinstance(e, dict)}

    cited = set()
    for r in rules:
        cited |= ev_sources(r)
    sliced = {sp.get("source_id") for s in scenes for sp in s.get("spans") or [] if isinstance(sp, dict)}
    used = (cited | sliced) & source_ids
    thin_repeated = sorted(r.get("id") for r in rules
                           if r.get("strength") == "repeated" and len(ev_sources(r) & source_ids) < 2)
    with_counter = sum(1 for r in rules if r.get("counterevidence"))
    report = {
        "source_units": n,
        "rules": len(rules),
        "scenes": len(scenes),
        "sources_used": len(used),
        "sources_used_share": round(len(used) / n, 3) if n else 0.0,
        "repeated_rules_with_one_source": thin_repeated,
        "rules_with_counterevidence": with_counter,
        "targets": targets,
        "warnings": [],
    }
    warns = report["warnings"]
    if n >= 2 and thin_repeated:
        warns.append(
            f"depth: {len(thin_repeated)} 条标为 repeated 的规则只有 1 章证据 {thin_repeated[:10]}；"
            "补第二章证据，或把 strength 改为 local"
        )
    if n < DEPTH_MIN_SOURCES:
        return report
    if len(rules) < targets["min_rules"]:
        warns.append(f"depth: {n} 章语料只提炼出 {len(rules)} 条规则（建议至少 {targets['min_rules']} 条）")
    if len(scenes) < targets["min_scenes"]:
        warns.append(f"depth: {n} 章语料只有 {len(scenes)} 个场景切片（建议至少 {targets['min_scenes']} 个）")
    if report["sources_used_share"] < targets["min_cited_share"]:
        warns.append(
            f"depth: 只有 {len(used)}/{n} 章被规则证据或场景切片引用，"
            f"其余 {n - len(used)} 章读过却没留下可追溯的内容（建议至少 {int(targets['min_cited_share'] * 100)}%）"
        )
    if rules and with_counter / len(rules) < targets["min_counterevidence_share"]:
        warns.append(
            f"depth: 只有 {with_counter}/{len(rules)} 条规则写了 counterevidence（反例或不适用的情形，"
            f"建议至少 {int(targets['min_counterevidence_share'] * 100)}%）"
        )
    return report


def template_placeholders():
    """Fill-in slots such as `<粘贴原著…>` taken verbatim from templates/pack-zh."""
    found = set()
    if TEMPLATE_DIR.is_dir():
        for f in TEMPLATE_DIR.rglob("*.md"):
            found.update(re.findall(r"`(<[^`<>\n]*[\u4e00-\u9fff][^`<>\n]*>)`",
                                    f.read_text(encoding="utf-8-sig")))
    return found


def validate_pack(pack, allow_draft=False):
    errors = []
    def error(message):
        errors.append(message)
    def load_json(name, default):
        try:
            return read_json(within(pack, name))
        except (OSError, ValueError, TypeError) as exc:
            error(f"{name}: {exc}")
            return default
    def load_rows(name, required):
        try:
            p = within(pack, name)
            if required and not p.is_file():
                error(f"Missing {name}")
            return read_rows(p)
        except (OSError, ValueError, TypeError) as exc:
            error(f"{name}: {exc}")
            return []
    m = load_json("manifest.json", {})
    if not isinstance(m, dict):
        return {"status": "FAIL", "errors": ["manifest.json must be an object"]}
    if m.get("type") != "novel-writing-pack" or m.get("format_version") != 1:
        error("Unsupported pack type or format_version")
    for key in ("name", "scope"):
        if not isinstance(m.get(key), str) or not m[key].strip():
            error(f"manifest.{key} must be nonempty text")
    if m.get("status") not in {"draft", "ready"}:
        error("Invalid pack status")
    if m.get("kind") not in {"writing", "demo"}:
        error("Invalid pack kind")
    if m.get("source_mode") != "bundled":
        error("This validator supports bundled portable packs only")
    if type(m.get("revision")) is not int or m["revision"] < 1:
        error("revision must be a positive integer")
    aliases = m.get("aliases")
    if not isinstance(aliases, list) or any(not isinstance(x, str) or not x.strip() for x in aliases):
        error("aliases must be a list of nonempty strings")
    complete = m.get("status") == "ready" or not allow_draft
    if not allow_draft and m.get("status") != "ready":
        error("Pack is draft; semantic distillation is not complete")
    sources = load_rows("corpus.jsonl", True)
    by_source = {}
    texts = {}
    if not sources:
        error("No source units")
    source_files = set()
    for s in sources:
        sid = s.get("id")
        if not isinstance(sid, str) or not re.fullmatch(r"C\d{4,}", sid):
            error(f"Invalid source id: {sid}")
            continue
        if sid in by_source:
            error(f"Duplicate source id: {sid}")
        by_source[sid] = s
        try:
            path = within(pack, s.get("file"))
            if not s["file"].startswith("corpus/"):
                error(f"{sid}: source must be in corpus/")
            if path in source_files:
                error(f"{sid}: duplicate source path")
            source_files.add(path)
            data = path.read_bytes()
            if digest(data) != s.get("sha256"):
                error(f"{sid}: source hash mismatch")
            text = data.decode(s["encoding"])
            texts[sid] = text.splitlines()
            if len(texts[sid]) != s.get("line_count"):
                error(f"{sid}: line_count mismatch")
            if len(re.sub(r"\s", "", text)) != s.get("chars_no_space"):
                error(f"{sid}: chars_no_space mismatch")
        except (OSError, ValueError, KeyError, TypeError, LookupError) as exc:
            error(f"{sid}: {exc}")
    def span_check(span, label, quote=False):
        if not isinstance(span, dict):
            error(f"{label}: invalid span")
            return
        sid = span.get("source_id")
        if sid not in texts:
            error(f"{label}: unknown or unreadable source {sid}")
            return
        a, b = span.get("start_line"), span.get("end_line")
        if type(a) is not int or type(b) is not int or not (1 <= a <= b <= len(texts[sid])):
            error(f"{label}: invalid line range")
            return
        if quote:
            q = span.get("quote")
            if not isinstance(q, str) or not q.strip() or q not in "\n".join(texts[sid][a-1:b]):
                error(f"{label}: quote is not exact within the cited lines")
    rules = load_rows("rules.jsonl", complete)
    rule_ids = set()
    rule_dimensions = {}
    for rule in rules:
        rid = rule.get("id")
        if not isinstance(rid, str) or not re.fullmatch(r"R\d{3,}", rid):
            error(f"Invalid rule id: {rid}")
            continue
        if rid in rule_ids:
            error(f"Duplicate rule id: {rid}")
        rule_ids.add(rid)
        rule_dimensions[rid] = rule.get("dimension")
        for key in ("dimension", "claim", "when", "do", "effect", "avoid"):
            if not isinstance(rule.get(key), str) or not rule[key].strip():
                error(f"{rid}: missing {key}")
        if rule.get("strength") not in {"repeated", "local", "tentative"}:
            error(f"{rid}: invalid strength")
        for key in ("evidence", "counterevidence"):
            spans = rule.get(key)
            if not isinstance(spans, list) or (key == "evidence" and not spans):
                error(f"{rid}: invalid {key}")
            else:
                for span in spans:
                    span_check(span, f"{rid}.{key}", quote=True)
    analysis = load_rows("analysis.jsonl", complete)
    analyzed = set()
    for a in analysis:
        sid = a.get("source_id")
        if sid not in by_source:
            error(f"analysis: unknown source {sid}")
            continue
        if sid in analyzed:
            error(f"analysis: duplicate record {sid}")
        analyzed.add(sid)
        if a.get("sha256") != by_source[sid].get("sha256"):
            error(f"{sid}: stale analysis hash")
        if not isinstance(a.get("observation"), str) or not a["observation"].strip():
            error(f"{sid}: missing observation")
        if not isinstance(a.get("unresolved"), list) or any(not isinstance(x, str) for x in a["unresolved"]):
            error(f"{sid}: unresolved must be a string list")
        refs = a.get("rule_ids")
        if not isinstance(refs, list) or any(x not in rule_ids for x in refs):
            error(f"{sid}: invalid rule_ids")
    if complete and analyzed != set(by_source):
        error("Unanalyzed inputs: " + ", ".join(sorted(set(by_source)-analyzed)))
    scenes = load_rows("scenes.jsonl", complete)
    scene_ids = set()
    for s in scenes:
        sid = s.get("id")
        if not isinstance(sid, str) or sid in scene_ids:
            error(f"Invalid or duplicate scene id: {sid}")
        scene_ids.add(sid)
        if not isinstance(s.get("summary"), str) or not s["summary"].strip():
            error(f"{sid}: missing scene summary")
        if not isinstance(s.get("tags"), list) or not s["tags"] or any(not isinstance(x, str) for x in s["tags"]):
            error(f"{sid}: tags must be a nonempty string list")
        spans = s.get("spans")
        if not isinstance(spans, list) or not spans:
            error(f"{sid}: missing spans")
        else:
            for span in spans:
                span_check(span, sid)
    coverage_path = within(pack, "coverage.json")
    coverage = load_json("coverage.json", {}) if coverage_path.exists() or complete else {}
    dimensions = coverage.get("dimensions", []) if isinstance(coverage, dict) else []
    covered = set()
    covered_rules = set()
    if not isinstance(dimensions, list):
        error("coverage.dimensions must be a list")
        dimensions = []
    for d in dimensions:
        if not isinstance(d, dict):
            error("Invalid coverage entry")
            continue
        dim, state = d.get("dimension"), d.get("status")
        if not isinstance(dim, str) or not dim.strip() or dim in covered:
            error(f"Invalid or duplicate coverage dimension: {dim}")
            continue
        covered.add(dim)
        refs = d.get("rule_ids")
        if not isinstance(refs, list) or any(x not in rule_ids for x in refs):
            error(f"{dim}: invalid coverage rule_ids")
            refs = []
        covered_rules.update(refs)
        if state not in {"observed", "partial", "unobserved", "not_applicable"}:
            error(f"{dim}: invalid coverage status")
        if state in {"unobserved", "not_applicable"} and refs:
            error(f"{dim}: unobserved dimension has rules")
        if state in {"observed", "partial"} and not refs:
            error(f"{dim}: observed dimension has no rules")
        if any(rule_dimensions.get(r) != dim for r in refs):
            error(f"{dim}: rule belongs to a different dimension")
        if not isinstance(d.get("note"), str) or not d["note"].strip():
            error(f"{dim}: missing coverage note")
        if not isinstance(d.get("suggested_inputs"), list) or any(not isinstance(x, str) for x in d["suggested_inputs"]):
            error(f"{dim}: suggested_inputs must be a string list")
    if complete:
        missing_dims = [d for d in DIMENSIONS if d not in covered]
        if missing_dims:
            error("Coverage is missing base dimensions: " + "、".join(missing_dims))
        unknown_dims = sorted({d for d in rule_dimensions.values() if isinstance(d, str)} - covered)
        if unknown_dims:
            error("Rules use dimensions not listed in coverage.json: " + "、".join(unknown_dims)
                  + "（填九个基础维度名之一；扩展维度要先登记到 coverage.json，见 references/dimensions.md）")
        if covered_rules != rule_ids:
            error("Coverage does not account for every rule")
    modules = m.get("modules")
    if not isinstance(modules, list):
        error("manifest.modules must be a list")
        modules = []
    declared_files = ["PACK.md", "缺口与补充建议.md"]
    for module in modules:
        if not isinstance(module, dict):
            error("Invalid module declaration")
            continue
        declared_files.append(module.get("file"))
        dims = module.get("dimensions")
        if not isinstance(dims, list) or not dims or any(d not in covered for d in dims):
            error("Module references an unknown dimension")
    if complete and rules and not modules:
        error("Rules exist but no readable fingerprint module is declared")
    placeholders = template_placeholders() if complete else set()
    warnings = []
    for s in scenes:
        sid = s.get("id")
        for span in s.get("spans") or []:
            if isinstance(span, dict) and span.get("source_id") in texts:
                total_lines = len(texts[span["source_id"]])
                a, b = span.get("start_line"), span.get("end_line")
                if (isinstance(a, int) and isinstance(b, int)
                        and total_lines >= 50 and (b - a + 1) >= int(total_lines * 0.9)):
                    warnings.append(
                        f"{sid}: spans {b - a + 1}/{total_lines} lines of {span['source_id']}; "
                        "prefer paragraph-level golden scene slicing over whole-chapter spans"
                    )
    for relative in declared_files:
        try:
            p = within(pack, relative)
            if not p.is_file():
                if complete:
                    error(f"Missing module or entry: {relative}")
                continue
            text = p.read_text(encoding="utf-8-sig")
            if not text.strip():
                error(f"Empty module: {relative}")
            elif relative.startswith("指纹/") and len(rules) >= 10 and len(re.sub(r"\s", "", text)) < 300:
                warnings.append(f"{relative}: module is under 300 chars; include formulas, beat templates and examples")
            for rid in RULE_REF_RE.findall(text):
                if rid not in rule_ids:
                    error(f"{relative}: unknown rule reference {rid}")
            if complete:
                leftovers = set(GENERIC_PLACEHOLDER_RE.findall(text))
                leftovers.update(x for x in placeholders if x in text)
                if leftovers:
                    error(f"{relative}: unfilled template placeholders {sorted(leftovers)[:5]}")
        except (OSError, ValueError, TypeError) as exc:
            error(str(exc))

    # Optional/Enhanced lexicon.json validation (backward compatible with legacy packs)
    lex_path = within(pack, "lexicon.json")
    lex_summary = {"present": False, "vocab_items": 0, "connectors": 0, "sentence_bank": 0, "voice_cards": 0}
    if lex_path.is_file():
        lex = load_json("lexicon.json", {})
        if not isinstance(lex, dict):
            error("lexicon.json must be an object")
        else:
            lex_summary["present"] = True
            cats = lex.get("categories")
            if not isinstance(cats, dict):
                error("lexicon.categories must be an object")
            else:
                missing_cats = [c for c in LEXICON_CATEGORIES if c not in cats]
                if missing_cats:
                    error("lexicon.categories missing 11-class categories: " + "、".join(missing_cats))
                for c_name, c_items in cats.items():
                    if not isinstance(c_items, list):
                        error(f"lexicon.categories.{c_name} must be a list")
                        continue
                    lex_summary["vocab_items"] += len(c_items)
                    for idx_item, it in enumerate(c_items):
                        if not isinstance(it, dict) or not str(it.get("word", "")).strip():
                            error(f"lexicon.categories.{c_name}[{idx_item}]: missing word")
                            continue
                        ev = it.get("evidence")
                        if isinstance(ev, dict) and str(ev.get("source_id", "")).startswith("C"):
                            span_check(ev, f"lexicon.{c_name}.{it['word']}", quote=True)
            conns = lex.get("connectors")
            if isinstance(conns, dict):
                for ct_name, ct_items in conns.items():
                    if isinstance(ct_items, list):
                        lex_summary["connectors"] += len(ct_items)
                        for idx_c, it in enumerate(ct_items):
                            if isinstance(it, dict) and isinstance(it.get("evidence"), dict):
                                ev = it["evidence"]
                                if str(ev.get("source_id", "")).startswith("C"):
                                    span_check(ev, f"lexicon.connectors.{ct_name}[{idx_c}]", quote=True)
            sb = lex.get("sentence_bank")
            if isinstance(sb, list):
                lex_summary["sentence_bank"] = len(sb)
                for idx_s, s_it in enumerate(sb):
                    if isinstance(s_it, dict) and str(s_it.get("source_id", "")).startswith("C"):
                        span_check(s_it, f"lexicon.sentence_bank[{idx_s}]", quote=True)
            vc = lex.get("voice_cards")
            if isinstance(vc, list):
                lex_summary["voice_cards"] = len(vc)
                for idx_v, v_it in enumerate(vc):
                    if isinstance(v_it, dict):
                        for ev in v_it.get("evidence") or []:
                            if isinstance(ev, dict) and str(ev.get("source_id", "")).startswith("C"):
                                span_check(ev, f"lexicon.voice_cards[{idx_v}]", quote=True)

    # Compute distillation density ratio (蒸馏比 = 产出字符(不含空白) / 原文字数(不含空白))
    source_chars_total = sum(int(s.get("chars_no_space") or 0) for s in sources if isinstance(s, dict))
    output_chars_total = 0
    for f_rel in ("analysis.jsonl", "rules.jsonl", "scenes.jsonl", "coverage.json", "lexicon.json"):
        fp = within(pack, f_rel)
        if fp.is_file():
            output_chars_total += len(re.sub(r"\s", "", fp.read_text(encoding="utf-8-sig")))
    for md_dir in (pack, pack / "指纹", pack / "词句库"):
        if md_dir.is_dir():
            for md_f in md_dir.glob("*.md"):
                output_chars_total += len(re.sub(r"\s", "", md_f.read_text(encoding="utf-8-sig")))
    distill_ratio = round(output_chars_total / max(1, source_chars_total), 4)

    depth = depth_report(sources, rules, scenes)
    depth["source_chars_no_space"] = source_chars_total
    depth["distilled_output_chars_no_space"] = output_chars_total
    depth["distillation_ratio"] = distill_ratio
    depth["lexicon"] = lex_summary
    if complete and len(sources) >= DEPTH_MIN_SOURCES and distill_ratio < 0.04:
        depth["warnings"].append(
            f"depth: 蒸馏密度（蒸馏比）偏低 {distill_ratio:.3f}（产出 {output_chars_total} 字 / 原文 {source_chars_total} 字 < 0.040）；"
            "建议补充规则例证、黄金切片或运行 scan-lexicon 充实词句库"
        )
    if complete:
        warnings.extend(depth.pop("warnings"))
    else:
        depth.pop("warnings")
    return {"status": "FAIL" if errors else "PASS", "pack_status": m.get("status"),
            "source_units": len(sources), "analyzed_units": len(analyzed),
            "rules": len(rules), "scenes": len(scenes), "errors": errors,
            "warnings": warnings, "depth": depth,
            "limits": "Checks records and exact evidence, not semantic truth, plot fidelity or literary quality"}


def slice_scenes(pack, scene_ids=None, tag=None, limit=5):
    sources = {s["id"]: s for s in read_rows(within(pack, "corpus.jsonl"))}
    scenes = read_rows(within(pack, "scenes.jsonl"))
    wanted_ids = set(scene_ids or [])
    matched = []
    for scene in scenes:
        if wanted_ids and scene.get("id") not in wanted_ids:
            continue
        if tag and tag not in (scene.get("tags") or []):
            continue
        matched.append(scene)
        if not wanted_ids and len(matched) >= max(1, limit):
            break
    results = []
    for scene in matched:
        excerpts = []
        for span in scene.get("spans") or []:
            src = sources.get(span.get("source_id"))
            if not src:
                continue
            a, b = span.get("start_line"), span.get("end_line")
            if type(a) is not int or type(b) is not int:
                raise ValueError(f"{scene.get('id')}: span has no start_line/end_line yet; "
                                 "run `novel_pack.py resolve --pack <pack>` first")
            lines = within(pack, src["file"]).read_bytes().decode(src["encoding"]).splitlines()
            excerpts.append({
                "source_id": src["id"],
                "input_name": src["input_name"],
                "start_line": a,
                "end_line": b,
                "text": "\n".join(lines[a - 1:b]),
            })
        results.append({
            "id": scene["id"],
            "tags": scene.get("tags", []),
            "summary": scene.get("summary", ""),
            "excerpts": excerpts,
        })
    return {"count": len(results), "scenes": results}


def _norm_quotes_1to1(s):
    return s.translate(str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'"}))


def _locate_substring_span(lines, query, min_line=1):
    if not isinstance(query, str) or not query.strip():
        return None
    q = query.strip()
    sub_lines = lines[min_line - 1:]
    full = "\n".join(sub_lines)
    idx = full.find(q)
    if idx < 0:
        idx = _norm_quotes_1to1(full).find(_norm_quotes_1to1(q))
    if idx < 0:
        return None
    exact_quote = full[idx : idx + len(q)]
    start_line = min_line + full.count("\n", 0, idx)
    end_line = min_line + full.count("\n", 0, idx + len(q) - 1)
    return start_line, end_line, exact_quote


def resolve_pack(pack):
    """Auto-resolve rule quote line numbers, scene anchor quotes, and empty analysis hashes."""
    sources = {s["id"]: s for s in read_rows(within(pack, "corpus.jsonl"))}
    lines_by_sid = {}
    for sid, src in sources.items():
        data = within(pack, src["file"]).read_bytes()
        lines_by_sid[sid] = data.decode(src["encoding"]).splitlines()

    errors = []
    resolved_quotes = 0
    rules_by_dim = {}
    evidence_sources = {}
    rules_path = within(pack, "rules.jsonl")
    if rules_path.is_file():
        rules = read_rows(rules_path)
        rules_changed = False
        for rule in rules:
            rid = rule.get("id", "R???")
            dim = rule.get("dimension")
            if isinstance(rid, str) and isinstance(dim, str):
                rules_by_dim.setdefault(dim, []).append(rid)
            for key in ("evidence", "counterevidence"):
                for i, span in enumerate(rule.get(key) or []):
                    if not isinstance(span, dict):
                        continue
                    sid = span.get("source_id")
                    q = span.get("quote")
                    if sid not in lines_by_sid or not isinstance(q, str) or not q.strip():
                        errors.append(f"{rid}.{key}[{i}]: missing valid source_id or quote")
                        continue
                    lines = lines_by_sid[sid]
                    a, b = span.get("start_line"), span.get("end_line")
                    if (
                        type(a) is int
                        and type(b) is int
                        and 1 <= a <= b <= len(lines)
                        and q in "\n".join(lines[a - 1 : b])
                    ):
                        continue
                    loc = _locate_substring_span(lines, q)
                    if not loc:
                        alt_hits = [
                            (alt_sid, _locate_substring_span(alt_lines, q))
                            for alt_sid, alt_lines in lines_by_sid.items()
                            if alt_sid != sid
                        ]
                        alt_hits = [(s_id, l_span) for s_id, l_span in alt_hits if l_span]
                        if len(alt_hits) == 1:
                            span["source_id"] = alt_hits[0][0]
                            loc = alt_hits[0][1]
                    if not loc:
                        errors.append(f"{rid}.{key}[{i}]: quote not found in {sid}: {q[:28]!r}")
                        continue
                    span["start_line"], span["end_line"], span["quote"] = loc
                    resolved_quotes += 1
                    rules_changed = True
            evidence_sources[rid] = {s.get("source_id") for s in rule.get("evidence") or []
                                     if isinstance(s, dict)}
        if rules_changed:
            write_rows(rules_path, rules)

    updated_hashes = 0
    analysis_path = within(pack, "analysis.jsonl")
    if analysis_path.is_file():
        analysis = read_rows(analysis_path)
        analysis_changed = False
        for row in analysis:
            sid = row.get("source_id")
            if sid in sources and not row.get("sha256"):
                row["sha256"] = sources[sid]["sha256"]
                updated_hashes += 1
                analysis_changed = True
            if not row.get("rule_ids") and isinstance(row.get("dims"), list):
                # Only rules with evidence in this chapter may be attached to it.
                auto_rids = []
                for d in row["dims"]:
                    for rid in rules_by_dim.get(d, []):
                        if sid in evidence_sources.get(rid, ()) and rid not in auto_rids:
                            auto_rids.append(rid)
                row["rule_ids"] = auto_rids
                analysis_changed = True
            for temp_k in ("dims", "_batch"):
                if temp_k in row:
                    row.pop(temp_k, None)
                    analysis_changed = True
        if analysis_changed:
            write_rows(analysis_path, analysis)

    coverage_path = within(pack, "coverage.json")
    if coverage_path.is_file() and rules_by_dim:
        cov = read_json(coverage_path)
        if isinstance(cov, dict) and isinstance(cov.get("dimensions"), list):
            cov_changed = False
            for d_entry in cov["dimensions"]:
                if not isinstance(d_entry, dict):
                    continue
                dim_name = d_entry.get("dimension")
                expected_rids = rules_by_dim.get(dim_name, [])
                if expected_rids and d_entry.get("rule_ids") != expected_rids:
                    d_entry["rule_ids"] = expected_rids
                    if d_entry.get("status") in {"unobserved", "not_applicable", None}:
                        d_entry["status"] = "observed"
                    if d_entry.get("note") in {PENDING_COVERAGE_NOTE, None, ""}:
                        d_entry["note"] = f"已有 {len(expected_rids)} 条规则（由 resolve 按 rules.jsonl 归入）"
                    cov_changed = True
            if cov_changed:
                write_json(coverage_path, cov)

    resolved_scenes = 0
    scenes_path = within(pack, "scenes.jsonl")
    if scenes_path.is_file():
        scenes = read_rows(scenes_path)
        scenes_changed = False
        for scene in scenes:
            sc_id = scene.get("id", "S???")
            for i, span in enumerate(scene.get("spans") or []):
                if not isinstance(span, dict):
                    continue
                sid = span.get("source_id")
                if sid not in lines_by_sid:
                    errors.append(f"{sc_id}.spans[{i}]: unknown source_id {sid}")
                    continue
                lines = lines_by_sid[sid]
                sq = span.get("start_quote") or span.get("anchor_start")
                eq = span.get("end_quote") or span.get("anchor_end")
                if sq or eq:
                    loc_s = _locate_substring_span(lines, sq) if sq else (span.get("start_line", 1), span.get("start_line", 1), "")
                    if not loc_s and sq:
                        alt_s = [
                            (alt_sid, alt_lines, _locate_substring_span(alt_lines, sq))
                            for alt_sid, alt_lines in lines_by_sid.items()
                            if alt_sid != sid
                        ]
                        alt_s = [(a_id, a_lines, a_loc) for a_id, a_lines, a_loc in alt_s if a_loc]
                        if len(alt_s) == 1:
                            sid, lines, loc_s = alt_s[0]
                            span["source_id"] = sid
                    if not loc_s or type(loc_s[0]) is not int:
                        errors.append(f"{sc_id}.spans[{i}]: start_quote not found in {sid}: {sq!r}")
                        continue
                    a = loc_s[0]
                    loc_e = _locate_substring_span(lines, eq, min_line=a) if eq else (span.get("end_line", a), span.get("end_line", a), "")
                    if not loc_e or type(loc_e[1]) is not int:
                        errors.append(f"{sc_id}.spans[{i}]: end_quote not found after line {a} in {sid}: {eq!r}")
                        continue
                    span["start_line"] = a
                    span["end_line"] = loc_e[1]
                    for k in ("start_quote", "end_quote", "anchor_start", "anchor_end"):
                        span.pop(k, None)
                    resolved_scenes += 1
                    scenes_changed = True
        if scenes_changed:
            write_rows(scenes_path, scenes)

    resolved_lexicon = 0
    lex_path = within(pack, "lexicon.json")
    if lex_path.is_file():
        lex = read_json(lex_path)
        lex_changed = False
        if isinstance(lex, dict):
            def _resolve_ev_span(span, label):
                nonlocal resolved_lexicon, lex_changed
                if not isinstance(span, dict):
                    return
                sid = span.get("source_id")
                q = span.get("quote")
                if not isinstance(sid, str) or not sid.startswith("C"):
                    return
                if sid not in lines_by_sid or not isinstance(q, str) or not q.strip():
                    errors.append(f"{label}: missing valid source_id or quote")
                    return
                lines = lines_by_sid[sid]
                a, b = span.get("start_line"), span.get("end_line")
                if (
                    type(a) is int
                    and type(b) is int
                    and 1 <= a <= b <= len(lines)
                    and q in "\n".join(lines[a - 1 : b])
                ):
                    return
                loc = _locate_substring_span(lines, q)
                if not loc:
                    alt_hits = [
                        (alt_sid, _locate_substring_span(alt_lines, q))
                        for alt_sid, alt_lines in lines_by_sid.items()
                        if alt_sid != sid
                    ]
                    alt_hits = [(s_id, l_span) for s_id, l_span in alt_hits if l_span]
                    if len(alt_hits) == 1:
                        span["source_id"] = alt_hits[0][0]
                        loc = alt_hits[0][1]
                if not loc:
                    errors.append(f"{label}: quote not found in {sid}: {q[:28]!r}")
                    return
                span["start_line"], span["end_line"], span["quote"] = loc
                resolved_lexicon += 1
                lex_changed = True

            for cat_name, items in (lex.get("categories") or {}).items():
                if isinstance(items, list):
                    for idx_i, it in enumerate(items):
                        if isinstance(it, dict) and isinstance(it.get("evidence"), dict):
                            _resolve_ev_span(it["evidence"], f"lexicon.{cat_name}[{idx_i}]")
            for ct_name, items in (lex.get("connectors") or {}).items():
                if isinstance(items, list):
                    for idx_i, it in enumerate(items):
                        if isinstance(it, dict) and isinstance(it.get("evidence"), dict):
                            _resolve_ev_span(it["evidence"], f"lexicon.connectors.{ct_name}[{idx_i}]")
            for idx_s, s_it in enumerate(lex.get("sentence_bank") or []):
                _resolve_ev_span(s_it, f"lexicon.sentence_bank[{idx_s}]")
            for idx_v, v_it in enumerate(lex.get("voice_cards") or []):
                if isinstance(v_it, dict):
                    for idx_e, ev in enumerate(v_it.get("evidence") or []):
                        _resolve_ev_span(ev, f"lexicon.voice_cards[{idx_v}].evidence[{idx_e}]")
            if lex_changed:
                write_json(lex_path, lex)
                render_lexicon_markdown_files(pack, lex)

    return {
        "status": "FAIL" if errors else "PASS",
        "updated_analysis_hashes": updated_hashes,
        "resolved_rule_quotes": resolved_quotes,
        "resolved_scene_spans": resolved_scenes,
        "resolved_lexicon_quotes": resolved_lexicon,
        "errors": errors,
    }


def find_packs(root, name):
    def norm(s):
        return unicodedata.normalize("NFKC", s).strip().casefold()
    matches = []
    invalid = []
    for p in sorted(root.rglob("manifest.json")) if root.exists() else []:
        try:
            m = read_json(p)
            if not isinstance(m, dict) or m.get("type") != "novel-writing-pack":
                continue
            aliases = m.get("aliases", [])
            names = [m.get("name", "")] + (aliases if isinstance(aliases, list) else [])
            if any(isinstance(n, str) and norm(n) == norm(name) for n in names):
                matches.append({"path": str(p.parent.resolve()), "name": m.get("name"),
                                "kind": m.get("kind"), "status": m.get("status"),
                                "scope": m.get("scope")})
        except (OSError, ValueError) as exc:
            invalid.append({"path": str(p), "error": str(exc)})
    return {"status": "found" if len(matches) == 1 else "ambiguous" if matches else "not_found",
            "matches": matches, "invalid_manifests": invalid}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-out", help="Optional path to save UTF-8 JSON result directly to file")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("init", "add"):
        p = sub.add_parser(command)
        p.add_argument("--input", nargs="+", required=True)
        p.add_argument("--recursive", action="store_true", help="include nested source folders")
        p.add_argument("--lexicon-source", nargs="*", help="optional full-book directory/zip to scan for 11-category vocabulary & connectors without adding to bundled corpus")
        if command == "init":
            p.add_argument("--name", required=True)
            p.add_argument("--output", required=True)
            p.add_argument("--alias", action="append")
            p.add_argument("--kind", choices=("writing", "demo"), default="writing")
        else:
            p.add_argument("--pack", required=True)
    for command in ("stats", "validate", "resolve"):
        p = sub.add_parser(command)
        p.add_argument("--pack", required=True)
        if command == "validate":
            p.add_argument("--allow-draft", action="store_true")
    p = sub.add_parser("scan-lexicon")
    p.add_argument("--pack", required=True)
    p.add_argument("--lexicon-source", nargs="*", help="optional full-book directory/zip to scan for 11-category vocabulary & connectors")
    p.add_argument("--top-k", type=int, default=20, help="max items per lexicon category")
    p = sub.add_parser("lookup")
    p.add_argument("--pack", required=True)
    p.add_argument("--query", help="semantic query or keywords, e.g. '算账 陷阱 杀招'")
    p.add_argument("--category", help="filter by lexicon category substring")
    p.add_argument("--limit", type=int, default=8, help="max items returned per section")
    p = sub.add_parser("find")
    p.add_argument("--root", required=True)
    p.add_argument("--name", required=True)
    p = sub.add_parser("slice")
    p.add_argument("--pack", required=True)
    p.add_argument("--scene", nargs="*", help="explicit scene IDs, e.g. S001 S004")
    p.add_argument("--tag", help="filter scenes by tag")
    p.add_argument("--limit", type=int, default=5, help="max scenes when filtering by tag")
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            result = init_pack(args)
        elif args.command == "add":
            result = add_sources(args)
        elif args.command == "stats":
            result = stats_for_pack(Path(args.pack).resolve())
        elif args.command == "resolve":
            result = resolve_pack(Path(args.pack).resolve())
        elif args.command == "validate":
            result = validate_pack(Path(args.pack).resolve(), args.allow_draft)
        elif args.command == "scan-lexicon":
            result = scan_lexicon_for_pack(Path(args.pack).resolve(), args.lexicon_source, args.top_k)
        elif args.command == "lookup":
            result = lookup_lexicon(Path(args.pack).resolve(), args.query, args.category, args.limit)
        elif args.command == "slice":
            result = slice_scenes(Path(args.pack).resolve(), args.scene, args.tag, args.limit)
        else:
            result = find_packs(Path(args.root).resolve(), args.name)
        emit(result, args.json_out)
        return 1 if result.get("status") == "FAIL" else 2 if result.get("status") in {"ambiguous", "not_found"} else 0
    except (OSError, ValueError, KeyError, TypeError, LookupError) as exc:
        emit({"status": "FAIL", "error": str(exc)}, getattr(args, "json_out", None))
        return 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
