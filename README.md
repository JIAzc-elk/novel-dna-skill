# 网文写法蒸馏器（novel-dna-skill）

[![tests](https://github.com/JIAzc-elk/novel-dna-skill/actions/workflows/tests.yml/badge.svg)](https://github.com/JIAzc-elk/novel-dna-skill/actions/workflows/tests.yml)
[![python](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![license](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![no dependencies](https://img.shields.io/badge/dependencies-none-brightgreen.svg)](#命令行工具速查)

> **一句话**：把你喜欢的一部中文网络小说拆成一套带原文铁证的「实战写法说明书（写作包）」，然后拿这套说明书去改写润色你自己的稿子，或者按大纲写新章。

一个给 AI Agent（OpenCode / Claude Code / Codex 等）用的网文创作 Skill，专治三件事：

1. **严守网文铁律，绝不乱改第一人称**——全程锁死第三人称叙述与原稿剧情事实、专名、系统面板和数字，第一人称叙事零容忍硬拦截。
2. **真正学会目标大神的呼吸，拒绝千篇一律**——量化并动态适配目标作品的句法节奏（无论是均句 21 字的冷峻短促风，还是均句 36 字的长复句推演风），绝不拿同一把死尺子削足适履。
3. **铲除大模型网文腻子腔**——精准拦截「指腹」「垂眸」「睨着」「嘴角勾起一抹」「针扎似的凉意」「蹿起阵」「章末写天气打卡」，不做逼模型胡乱换词注水的机械降重。

---

## 它到底是怎么分工的？

本技能采用 **「宿主大模型深度阅读创作 + 本地 Python 标准库确定性校验」** 的双层架构（脚本本身零第三方依赖、无需额外配置 API Key，语义拆解与写作由你当前接入的宿主模型执行）：

| 步骤 | 谁负责 | 做什么 | 产出 |
| --- | --- | --- | --- |
| **① 语料入库与统计** | 本地 Python (`novel_pack.py`) | 复制语料、算 SHA-256、统计句长均值/标准差/P90、单句成段率、标点与连接词频次 | `corpus/` + `stats.json` |
| **② 深度蒸馏与自动定位** | 宿主模型 + `novel_pack.py resolve` | 模型逐章拆解 L1–L6 叙事技法并摘录原文引文；脚本自动将引文锚点换算为精确行号并补齐哈希 | `PACK.md` + `指纹/*.md` + `rules.jsonl` + `scenes.jsonl` |
| **③ 证据完整性校验** | 本地 Python (`novel_pack.py validate`) | 逐条核对每条规则的引文是否在原文对应行号内**逐字存在**，杜绝模型编造假引文 | `PASS / FAIL` 报告 |
| **④ 改写或按纲写正文** | 宿主模型 + `check_novel_ai.py` | 模型调阅匹配指纹与原文切片一次性写出第三人称正文；门禁按目标包 `stats.json` 动态校验人称红线与 AI 病灶 | 成稿 + 门禁报告 |

蒸馏出来的写作包结构（`写作包/书名写作包/`）：

```text
PACK.md              ← 总入口：L1 量化基线 + 最鲜明的十大核心写法 + 场景路由表
指纹/语言与节奏.md   ← 长短句怎么呼吸、单句成段在哪里落槌
指纹/人物对白与势力.md
指纹/战斗与设定.md
指纹/日常与职业.md
指纹/修辞用典与信息.md
指纹/情绪与主题.md
rules.jsonl          ← 每条规则均挂着原文行号和逐字引文证据
scenes.jsonl         ← 段落级黄金场景切片，写作前精准调阅
stats.json           ← 句长均值/SD/P90、单句成段率、标点分布（供门禁动态校准）
corpus/              ← 本地原文副本（仅保留在本地用于引文核对与切片调阅）
```

---

## 30 秒上手

### 1. 安装到你的 Skills 目录

```bash
# OpenCode 全局技能目录
git clone https://github.com/JIAzc-elk/novel-dna-skill.git ~/.config/opencode/skills/novel-dna-skill

# Claude Code / 通用 Agents 目录
git clone https://github.com/JIAzc-elk/novel-dna-skill.git ~/.agents/skills/novel-dna-skill
```

### 2. 直接用自然语言调用

```text
把这几个章节蒸馏成《大圣传》写作包
用《大圣传》写作包改写这篇正文
用《蛊真人》写作包按这个纲要写第四章
看看《大圣传》写作包能写什么、还缺什么
```

---

## 三种核心用法

### 用法一：蒸馏一个写作包
提供一章、几章或一整卷原文（`.txt` / `.md` 均可，不设最低章数门槛）。模型精读后会生成分层技法配方、Step 1~4 场景推进模板、原著段落解剖及 `缺口与补充建议.md`。借助 `novel_pack.py resolve`，模型只需填写原文逐字引文，脚本自动计算精确行号，快速通过校验。

### 用法二：保剧情深度改写（锁死第三人称与事实，升级笔力）
- **100% 锁死**：**全程第三人称叙述（严禁改成第一人称“我”）**、剧情因果、人物性格、全部专有名词、系统面板提示（`【……】`）、古籍引文、具体数字，以及原稿中本身自然出彩的关键台词。
- **深度重构**：打破平庸的“开头报天气+主角简历”铺垫、监控摄像头式的琐碎家务动作链与章末写景打卡，按目标写作包的分镜节奏与长短句呼吸重写叙述正文。**不做愚蠢的同义词替换降重，不凭空编造支线注水。**

### 用法三：按章节纲要写正文
全程第三人称严格落实纲要给定的冲突、手段、胜负与章末停点；普通环境、过招回合、潜台词与心理算计按写作包风格自主补全。

---

## 本地确定性门禁（`check_novel_ai.py`）拦什么？

支持传入 `--pack <写作包路径>` 读取目标书的 `stats.json` 动态适配句法区间（实测《蛊真人》193 章与《大圣传》44 章原著自然通过率均 **> 97%**）：

| 拦截项 | 级别 | 抓的是什么 |
| --- | --- | --- |
| `first-person-narration` | **硬拦截** | **网文头号死忌**：叙述正文（引号与系统框外）使用第一人称“我”自述或擅自篡改人称 |
| `embodied-simile` | **硬拦截** | 「针扎似的凉意」「胸口像被冷针扎」「潮水般的疲惫感」等 AI 生理通感比喻 |
| `dropped-numeral` | **硬拦截** | 「蹿起阵」「泛起股」「透出丝」等大模型二修吞掉“一”字造成的语病 |
| `llm-slop-vocab` | **硬拦截** | 「指腹」「垂眸」「睨着」「嘴角勾起一抹」「平淡无波」「悄然收紧」等高危网文腻子词 |
| `machine-opening-inventory` | **硬拦截** | 首段同时堆砌天气环境 + 主角年龄/衣着/伤疤简历打卡 |
| `bookend-weather-ending` | **硬拦截** | 章末最后两段写「雨还在下 / 风还在吹 / 青石板发亮」首尾呼应打卡 |
| `clue-recap-list` | **硬拦截** | 章末把前文已演过的线索重新罗列盘点一遍 |
| `verbatim-narrative-copy` | **硬拦截** | 改写模式下大段原样照搬引号外长叙述句（≥18字）偷懒不改（**注：绝不误伤人名、地名、系统提示词、古籍引文与保留台词**） |
| `length-inflation / truncation` | **硬拦截** | 改写时胡乱编造废话注水（>1.85倍）或严重删减缩水成梗概（<0.55倍） |
| `bloated / style-too-choppy` | **动态校准** | 结合 `--pack` 的 `stats.json` 动态判断句长粘连或切得过碎，绝不拿单一作品尺子一刀切 |

---

## 命令行工具速查

仅依赖 Python 3.10+ 标准库，无需安装任何第三方包（`<SKILL_DIR>` 为本技能所在目录）：

```bash
# 1. 建包：扫描语料、算哈希、生成 stats.json
python "<SKILL_DIR>/scripts/novel_pack.py" init --name 大圣传写作包 --output 写作包/大圣传写作包 --input <章节文件或目录>

# 2. 增量补充新章节
python "<SKILL_DIR>/scripts/novel_pack.py" add --pack 写作包/大圣传写作包 --input <新章节路径>

# 3. 自动补齐 analysis 哈希，并将 rules/scenes 中的引文锚点精确换算为行号
python "<SKILL_DIR>/scripts/novel_pack.py" resolve --pack 写作包/大圣传写作包

# 4. 重算语言学统计 / 查找写作包 / 调阅黄金场景切片
python "<SKILL_DIR>/scripts/novel_pack.py" stats --pack 写作包/大圣传写作包
python "<SKILL_DIR>/scripts/novel_pack.py" find --root 写作包 --name 大圣传写作包
python "<SKILL_DIR>/scripts/novel_pack.py" --json-out .work/slices.json slice --pack 写作包/大圣传写作包 --tag 地位差对白 --limit 3

# 5. 严格校验写作包完整性（哈希、引文逐字匹配、场景行号、覆盖声明）
python "<SKILL_DIR>/scripts/novel_pack.py" --json-out .work/validate.json validate --pack 写作包/大圣传写作包

# 6. 成稿门禁（传入 --pack 动态适配目标书句法，传入 --output 写入 UTF-8 报告避免 Windows 终端乱码）
python "<SKILL_DIR>/scripts/check_novel_ai.py" 成稿.md --source 原稿.md --pack 写作包/大圣传写作包 --output .work/ai_check.json
```

运行单元测试：

```bash
python -m unittest discover -s tests -v
```

---

## 常见问题

**Q：一定要给指定数量章节吗（比如 193 章）？**
不用。给多少就拆多少，1 章也能出包，只是覆盖的场景类型会少。蒸馏完它会主动告诉你缺什么（比如「缺少逆风逃亡战的样本」），你再按需补充。

**Q：真的不需要联网或付费 API 吗？**
分两部分说清楚：
1. 本仓库自带的 Python 脚本（`novel_pack.py` 与 `check_novel_ai.py`）**100% 只用 Python 标准库，不联网、不需要额外配置任何 API Key**。
2. 但本仓库是一个 **Agent Skill（给 AI 代理看的技能规范 + 本地校验工具链）**——阅读小说提炼规则、按写作包改写或创作正文，是由你在 OpenCode / Claude Code / Codex 里当前使用的宿主大模型执行的，因此会正常消耗你所选宿主模型的对话 Token（除非你宿主环境接的是本地大模型）。

**Q：为什么以前有的改写工具会擅自把小说改成第一人称“我”，或者逼着改掉好台词？**
那是用机械字符重合率（3-gram / 5-gram）强逼模型“降重”导致的恶果——模型为了消灭主角名字的重复率，甚至会把主角名全换成“我”。本工具已**彻底废除字符重合率门禁**，并将**“严禁第一人称叙事（全程锁死第三人称）”**设为第一顺位硬拦截红线，完整保护专有名词、系统面板、引文与精彩原台词。

**Q：长句派作者（比如均句 36 字）和短句派作者（比如均句 21 字）都能兼容吗？**
能。改写或创作后跑 `check_novel_ai.py` 时加上 `--pack 写作包/某某写作包`，门禁会自动读取该包 `stats.json` 的真实句长分布动态设定阈值，不会把长复句派大神的原著风格误判为病句。

---

## 版权与免责

- 本仓库**不附带任何受版权保护的小说原文语料或成品写作包**。写作包需由用户使用自己合法持有的文本在本地生成，`corpus/` 副本仅保存在本地用于校验。
- 本项目基于 MIT 协议开源，按「现状」提供。
