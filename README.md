# 网文写法蒸馏器（novel-dna-skill）

[![tests](https://github.com/JIAzc-elk/novel-dna-skill/actions/workflows/tests.yml/badge.svg)](https://github.com/JIAzc-elk/novel-dna-skill/actions/workflows/tests.yml)
[![python](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![license](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![no dependencies](https://img.shields.io/badge/dependencies-none-brightgreen.svg)](#命令行工具速查)

> **一句话**：把你喜欢的一部中文网络小说拆成一套带原文铁证的「实战写法说明书（写作包）」，然后拿这套说明书去改写润色你自己的稿子，或者按大纲写新章。

一个给 AI Agent（OpenCode / Claude Code / Codex 等）用的网文创作 Skill，专治三件事：

1. **提供人称与事实核对工具**——检测部分人称越界；可选的事实账本能核对作者列出的必保事实、冲突表述和事件顺序，但不会自行理解或证明全文语义一致。
2. **把目标作品当作参考基线**——统计句法节奏并供写作时参考，不代表已经证明模型能稳定复刻目标作品的完整风格。
3. **检查一组常见网文腻子问题**——规则覆盖「指腹」「垂眸」「针扎似的凉意」等启发式特征；它不是 AI 检测器，也不能保证识别或清除所有此类问题。

---

## 它到底是怎么分工的？

本技能采用 **「宿主大模型深度阅读创作 + 本地 Python 标准库确定性校验」** 的双层架构（脚本本身零第三方依赖、无需额外配置 API Key，语义拆解与写作由你当前接入的宿主模型执行）：

| 步骤 | 谁负责 | 做什么 | 产出 |
| --- | --- | --- | --- |
| **① 语料入库、统计与四库扫描** | 本地 Python (`novel_pack.py`) | 复制语料、算 SHA-256、统计句长/单句成段率，并**零 Token 扫描生成 11 类正向替换词汇库、5 类衔接库、语义句段库与声线卡**（支持 `--lexicon-source` 毫秒级扫全书目录/zip） | `corpus/` + `stats.json` + `lexicon.json` + `词句库/*.md` |
| **② 深度蒸馏与自动定位** | 宿主模型 + `novel_pack.py resolve` | 模型精读精选章节拆解 L1–L6 叙事技法并摘录原文引文；脚本自动将规则/切片/词库中的引文锚点换算为精确行号并补齐哈希 | `PACK.md` + `指纹/*.md` + `rules.jsonl` + `scenes.jsonl` |
| **②b 可选拆文库增强（可有可无）** | 本地 Python (`novel_pack.py integrate-analysis`) | **可选**：若你先用 `oh-story` 技能对目标书做了拆文，可将拆文库一键整合进写作包 `拆文增强/`（含全书语感、潜台词三模式、`EM-001`~`EM-xxx` 可复现戏剧模块卡）；**没有拆文库照样正常使用** | `拆文增强/*.md` + `analysis_meta.json` |
| **③ 证据完整性与蒸馏比校验** | 本地 Python (`novel_pack.py validate`) | 逐条核对引文是否在原文对应行号内**逐字存在**，校验 11 类词库结构并计算**字符蒸馏比（`distillation_ratio`）**防止 AI 疲劳偷懒 | `PASS / FAIL` + `depth` 报告 |
| **④ 按包写作 + 确定性检查** | 宿主模型 + `check_novel_ai.py` | 模型参考写作包组织成稿；本地门禁检查已编码的启发式规则，可选读取 `--fact-lock` 对账本中的明确断言做短语核对 | 成稿 + 检查报告 |

蒸馏出来的写作包结构（`写作包/书名写作包/`）：

```text
PACK.md              ← 总入口：L1 量化基线 + 最鲜明的十大核心写法 + 场景路由表
词句库/词汇库.md     ← 11 类原著高频实感词汇库（含原著行号例证 + 跨题材新书设定转换槽）
词句库/衔接库.md     ← 5 类原著句内转折与段间转场衔接库（顶替 AI 的“然而/就在这时/与此同时”）
词句库/句段库.md     ← 按剧情微语义（算账/识破/破招/施压/取舍）归档的原著句法骨架
词句库/声线卡.md     ← 角色声线卡与多人物防串味档案（句长、腔调、禁忌）
指纹/语言与节奏.md   ← 长短句怎么呼吸、单句成段在哪里落槌
指纹/人物对白与势力.md
指纹/战斗与设定.md
指纹/日常与职业.md
指纹/修辞用典与信息.md
指纹/情绪与主题.md
lexicon.json         ← 结构化正向替换词句数据库（供 lookup 检索与门禁复检）
rules.jsonl          ← 每条规则均挂着原文行号和逐字引文证据
scenes.jsonl         ← 段落级黄金场景切片，写作前精准调阅
stats.json           ← 句长均值/SD/P90、单句成段率、标点分布（供门禁动态校准）
corpus/              ← 本地原文副本（仅保留在本地用于引文核对与切片调阅）
```

除了每个写作包自己的指纹，技能还自带一套**通用指纹**（`references/通用指纹/`），不需要蒸馏，所有写作包共用：

```text
references/通用指纹/告知词禁忌.md  ← 禁忌，每章必查：五类告知词改成动作、细节和画面（门禁自动统计）
references/通用指纹/转场技法.md    ← 知识包，按需调用：时间、空间、媒介、情绪、动作、直接分节六种转场
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
把这几个章节蒸馏成《某作品》写作包
用《某作品》写作包改写这篇正文
用《某作品》写作包按这个纲要写第四章
看看《某作品》写作包能写什么、还缺什么
```

---

## 三种核心用法

### 用法一：蒸馏一个写作包
提供一章、几章或一整卷原文（`.txt` / `.md` 均可，UTF-8 或 GBK 编码，不设最低章数门槛；传入目录时按章节号自然排序，`第2章` 排在 `第10章` 前）。模型精读后会生成分层技法配方、Step 1~4 场景推进模板、原著段落解剖及 `缺口与补充建议.md`。借助 `novel_pack.py resolve`，模型只需填写原文逐字引文，脚本自动计算精确行号；`validate` 会拦下编造的规则编号和没填的模板占位符。

### 用法二：保剧情改写（锁死人称与事实，升级笔力）
- **写作约束**：叙述人称跟随原稿；将剧情因果、人物关系、关键数字、专名、系统面板与结局整理成事实账本并逐项复核。脚本只校验明确提供的短语和事件锚点，不保证自动发现所有事实变化。
- **深度重构**：打破平庸的“开头报天气+主角简历”铺垫、监控摄像头式的琐碎家务动作链与章末写景打卡，按目标写作包的分镜节奏与长短句呼吸重写叙述正文。**不做愚蠢的同义词替换降重，不凭空编造支线注水。**

### 用法三：按章节纲要写正文
全程第三人称严格落实纲要给定的冲突、手段、胜负与章末停点；普通环境、过招回合、潜台词与心理算计按写作包风格自主补全。

---

## 本地确定性门禁（`check_novel_ai.py`）拦什么？

支持传入 `--pack <写作包路径>` 按目标书校准：句长用“只算叙述句”的同一种算法和写作包比较，“着”字密度、分号和腻子词按目标作者自己的用词频率放宽。目标包模式下，段落/句长离散度、叙述感叹号回声、段首主语复读、对白长度同质化、翻案句式、对白说教比例和情绪直述密度等**风格形状项只作为 warnings，不会因通用统计阈值拦截目标作者自己的合法习惯**；明确语病、人称越界、事实/设定泄漏和高危 AI 套话仍然硬拦截。未传 `--pack` 时保留严格默认门禁。`--pack` 路径写错时直接报 `STATUS=ERROR`（退出码 2），不会悄悄换成默认标准。草稿与原稿支持 UTF-8 和 GBK 编码。

| 拦截项 | 级别 | 抓的是什么 |
| --- | --- | --- |
| `first-person-narration` / `pov-changed` | **硬拦截** | **人称红线**：按纲要新写或第三人称原稿的改写稿，叙述正文（引号与系统框外）用“我”自述；或第一人称原稿被擅自改成第三人称 |
| `embodied-simile` | **硬拦截** | 「针扎似的凉意」「胸口像被冷针扎」「潮水般的疲惫感」等 AI 生理通感比喻（「这般绝望」「一般的恐惧」不算） |
| `dropped-numeral` | **硬拦截** | 「蹿起阵」「泛起股」「透出丝」等大模型二修吞掉“一”字造成的语病（「层层涟漪」「丝丝笑意」等叠词不算） |
| `llm-slop-vocab` | **硬拦截** | 「指腹」「垂眸」「睨着」「嘴角勾起一抹」「平淡无波」「悄然收紧」等高危网文腻子词（目标作者原文常用的词按原著频率放行） |
| `machine-opening-inventory` | **硬拦截** | 首段同时堆砌天气环境 + 主角年龄/衣着/伤疤简历打卡（人名里的“雪”“风”“雨”不算天气） |
| `bookend-weather-ending` | **硬拦截** | 章末最后两段写「雨还在下 / 风还在吹 / 青石板发亮」首尾呼应打卡 |
| `clue-recap-list` | **硬拦截** | 章末把前文已演过的线索重新罗列盘点一遍 |
| `verbatim-narrative-copy` | **硬拦截** | 改写模式下照搬引号外长叙述句（≥18字）超过 35%，或与原稿完全一致（**注：绝不误伤人名、地名、系统提示词、古籍引文与保留台词**） |
| `fact-lock-*` | **仅在传入 `--fact-lock` 时启用** | 核对账本列出的必含短语、冲突短语和事件首次出现顺序；不做自动事实抽取，也不理解否定、反讽或任意同义改写 |
| `length-inflation / synopsis` | **硬拦截** | 删废话不受限制，只拦两种极端（两稿相差 200 字以上时）：高于原稿 1.60 倍的编造注水，低于 0.40 倍的梗概化 |
| `bloated / style-too-choppy` | **动态校准** | 按 `--pack` 写作包的叙述句句长判断句子粘连或切得过碎，绝不拿单一作品尺子一刀切 |
| `pack-world-term-leak` | **硬拦截** | 跨书改写时漏入目标写作包的原著专属体系词（如把《蛊真人》的“蛊虫/真元海/仙窍”漏进都市或传统修仙原稿），强制按【新书设定转换槽】对标转换 |
| `voice-card-homogeneity` | **默认硬拦截；目标包下提醒** | 全章对白句长高度雷同（标准差 `< 3.5`）时提示 AI“千人一面”风险；目标包模式下只提醒，避免误伤真实作者的连续短句或单角色声线 |
| `pack-lexicon-unused / low-lexicon-hit-rate` | **正向复检提醒** | 写后报告成稿是否自然命中写作包 `lexicon.json` 中的实感词汇与衔接词；零命中不会强迫改写，不得为了统计命中硬塞生词或成语 |
| `telling-words` | **硬拦截（通用指纹）** | 叙述里的五类告知词超标：程度副词（非常/十分）、情绪词（感到难过）、多余身体部位（用手推开门）、模糊形容词（很漂亮/很多）、口头禅过渡词（然后/于是）。只查叙述，不查对白；没超标的列为提醒 |
| `status-adverb-overuse` / `body-part-sensory-repetition` | **硬拦截** | 拦截 `缓缓/微微/陡然/忽而/半晌/不由得/不知为何` 等高频状态副词同章反复刷屏，以及 `指尖/舌尖/喉间/眉梢` 等局部身体感官词高频复用 |
| `redundant-speech-tone` / `dialogue-info-dump` / `hollow-vow-cliche` | **硬拦截** | 拦截对白前后叠床架屋的语气说明（如“轻声开口，声音听不出半分波澜”）、路人插嘴背诵人物资料卡设定、以及战败无筹码时空喊“他日自会再相逢/百倍奉还”等套路狠话 |
| `abnormal-symbol-spacing` / `contrast-inversion-formula` | **符号异常硬拦截；目标包下翻案句提醒** | 拦截 `#/@/¥/*` 特殊符号堆叠与中文标点前后异常空格；`不是/并非X而是Y`、`不仅是/不止是X更是Y`、`没有A没有B凭着C` 等公式化衔接在目标包模式下只提示 |
| `full-name-repetition` / `bracket-highlight-abuse` | **硬拦截** | 拦截器物/灵材/道牌全称在章内反复磨叽复读（强制“首提全称，后文脱帽简称”），以及非系统面板在句子中间给功法/器物乱加 `【】` 重点框 |
| `coined-or-malaprop-slop` | **硬拦截** | 拦截二代 AI 矫枉过正病灶：生造半文半白怪词（如`骇了一跳`）、主谓搭配不当（如`目光一眨不眨`）、乱塞成语（如`严丝合缝塞进掌心`）、造作伴随动作（如`双手绞着围裙`）、常规操作拆成微观说明书（如`指尖渡入一缕道痕往牌面一按`）与已知设定定语反刍 |

---

## 命令行工具速查

仅依赖 Python 3.10+ 标准库，无需安装任何第三方包（`<SKILL_DIR>` 为本技能所在目录）：

```bash
# 1. 建包：扫描语料、算哈希、生成 stats.json，并零 Token 自动扫描生成 11 类词汇库/衔接库/句段库/声线卡
python "<SKILL_DIR>/scripts/novel_pack.py" init --name 我的写作包 --output 写作包/我的写作包 --input <章节文件或目录>

# 1b. （省 Token 高阶模式）主体只蒸馏精选章节（如 193 章），同时 0 Token 本地扫描全书目录/zip 扩充 11 类词句库
python "<SKILL_DIR>/scripts/novel_pack.py" init --name 我的写作包 --output 写作包/我的写作包 --input <精选章节路径> --lexicon-source <全书目录或zip路径>

# 1c. 对已有写作包单独重扫或用全书语料扩充 11 类词句库
python "<SKILL_DIR>/scripts/novel_pack.py" scan-lexicon --pack 写作包/我的写作包 --lexicon-source <全书目录或zip路径>

# 1d. （可选增强，无拆文库无需执行）将 oh-story 拆文成果（拆文库）整合进写作包的 拆文增强/ 目录
python "<SKILL_DIR>/scripts/novel_pack.py" integrate-analysis --pack 写作包/我的写作包 --analysis-dir <拆文库路径>

# 2. 增量补充新章节
python "<SKILL_DIR>/scripts/novel_pack.py" add --pack 写作包/我的写作包 --input <新章节路径>

# 3. 自动补齐 analysis 哈希，并将 rules/scenes/lexicon 中的引文锚点精确换算为行号
python "<SKILL_DIR>/scripts/novel_pack.py" resolve --pack 写作包/我的写作包

# 4. 写前按剧情语义调取 11 类替换词、衔接词、句段骨架与声线卡 / 调阅黄金场景切片
python "<SKILL_DIR>/scripts/novel_pack.py" --json-out .work/lookup.json lookup --pack 写作包/我的写作包 --query "算账 试探 威慑"
python "<SKILL_DIR>/scripts/novel_pack.py" --json-out .work/slices.json slice --pack 写作包/我的写作包 --tag 地位差对白 --limit 3
python "<SKILL_DIR>/scripts/novel_pack.py" stats --pack 写作包/我的写作包
python "<SKILL_DIR>/scripts/novel_pack.py" find --root 写作包 --name 我的写作包

# 5. 严格校验写作包完整性（哈希、引文逐字匹配、11类词库结构、蒸馏比 distillation_ratio、覆盖声明）
python "<SKILL_DIR>/scripts/novel_pack.py" --json-out .work/validate.json validate --pack 写作包/我的写作包

# 6. 成稿启发式检查（传入 --pack 参考目标书句法，传入 --narrative-check 检查叙事架构与声线卡）
python "<SKILL_DIR>/scripts/check_novel_ai.py" 成稿.md --source 原稿.md --pack 写作包/我的写作包 --narrative-check --output .work/ai_check.json

# 可选：按原稿或大纲核对结构化事实账本
python "<SKILL_DIR>/scripts/check_novel_ai.py" 成稿.md --source 原稿.md --pack 写作包/我的写作包 --fact-lock .work/fact-lock.json --narrative-check --output .work/ai_check.json
```

### 事实账本与评测边界

从原稿或大纲整理账本时，为每项关键事实列出正文中可接受的短语变体；对金额、数量、关系等列出常见冲突表述；把有先后要求的事件写成锚点并添加 `order`。格式可参考 [`templates/fact-lock.example.json`](templates/fact-lock.example.json)。账本需要人工或模型先整理、再由作者核对。检查器做区分大小写的子串匹配，事件顺序按各锚点在正文中的首次出现位置判定；它不能判断短语是否被否定、反讽、转述或语义反转，也不能代替通读校对。传入 `--fact-lock` 默认只报告不匹配；确认账本覆盖充分且需要严格门禁时，再追加 `--fact-lock-strict`。未传 `--fact-lock` 时不会执行剧情事实检查。

仓库现有 `test_samples/ai_lite.md` 与 `test_samples/ai_heavy.md` 只作为本地门禁回归样稿：前者应通过（允许提醒），后者应被多项规则拦截。这个小型手工样本对只能验证检查器的预期行为，不能证明目标作者风格复刻、长篇事实一致性或真实写作质量。完整端到端质量评测仍需权利清晰的目标写作样本、留出的原稿/大纲、实际模型生成稿、固定提示与多轮运行记录，并由盲评者按预先定义的量表打分。

运行单元测试：

```bash
python -m unittest discover -s tests -v
```

增量入库的失败处理：`add` 会在复制前检查整批语料的目标文件冲突，遇到已存在的未登记语料时拒绝整批导入，不覆盖该文件。新增语料登记前，包会先保存为 `draft`；即使后续统计或词库扫描失败，也不会继续标为 `ready`。这不是整包事务回滚，已经导入的语料会保留，原有精读、规则与切片记录不会被删除。

普通 `add` 默认保留已有 `lexicon.json` 和 `词句库/*.md`，返回 `lexicon_status: preserved`，避免覆盖人工精修和全书扩充成果；新增章节的词汇需要另行运行 `scan-lexicon` 才会进入词库。若词库不存在，或显式向 `add` 传入 `--lexicon-source`，则会重新扫描。

若统计或词库生成失败，修正报错原因后可分别运行以下命令重建，再完成新增章节的精读与校验。显式重扫会更新 `lexicon.json` 和四份 `词句库/*.md`，但在写入前会将现有文件备份到包内 `.backups/lexicon-*`；命令结果的 `backup`（`add` 为 `lexicon_backup`）给出备份路径，可按相同相对路径恢复文件。曾使用全书词源的包重扫时应继续传入同一 `--lexicon-source`；没有全书词源时省略该参数。备份不会自动清理。

```bash
python "<SKILL_DIR>/scripts/novel_pack.py" --json-out 写作包/我的写作包/stats.json stats --pack 写作包/我的写作包
python "<SKILL_DIR>/scripts/novel_pack.py" scan-lexicon --pack 写作包/我的写作包 --lexicon-source <全书目录或zip路径>
```

输入异常不会作为通过处理：`check_novel_ai.py` 遇到空正文、空原稿、无效事实账本或损坏的统计/词库配置时返回 `STATUS=ERROR`（退出码 2）；`novel_pack.py` 遇到无效写作包返回 `FAIL`（退出码 1）。目录词源中的符号链接会被拒绝，应显式选择真实文件。

---

## 常见问题

**Q：一定要给指定数量章节吗（比如 193 章）？**
不用。给多少就拆多少，1 章也能出包，只是覆盖的场景类型会少。蒸馏完它会主动告诉你缺什么（比如「缺少逆风逃亡战的样本」），你再按需补充。

**Q：真的不需要联网或付费 API 吗？**
分两部分说清楚：
1. 本仓库自带的 Python 脚本（`novel_pack.py` 与 `check_novel_ai.py`）**100% 只用 Python 标准库，不联网、不需要额外配置任何 API Key**。
2. 但本仓库是一个 **Agent Skill（给 AI 代理看的技能规范 + 本地校验工具链）**——阅读小说提炼规则、按写作包改写或创作正文，是由你在 OpenCode / Claude Code / Codex 里当前使用的宿主大模型执行的，因此会正常消耗你所选宿主模型的对话 Token（除非你宿主环境接的是本地大模型）。

**Q：为什么以前有的改写工具会擅自把小说改成第一人称“我”，或者逼着改掉好台词？**
那是用机械字符重合率（3-gram / 5-gram）强逼模型“降重”导致的恶果——模型为了消灭主角名字的重复率，甚至会把主角名全换成“我”。本工具已**彻底废除字符重合率门禁**，并将**“叙述人称锁死”**设为第一顺位硬拦截红线（改写跟随原稿人称，按纲要新写一律第三人称），完整保护专有名词、系统面板、引文与精彩原台词。

**Q：我的原稿本身是第一人称（自述体），能改写吗？**
能。改写时人称跟随原稿：第一人称原稿保留“我”叙述，门禁带 `--source` 时会识别原稿人称，不会误拦；反过来如果改写稿擅自换成了第三人称，会报 `pov-changed`。按纲要新写正文仍然只用第三人称。

**Q：长句派作者（比如均句 36 字）和短句派作者（比如均句 21 字）都能兼容吗？**
能。改写或创作后跑 `check_novel_ai.py` 时加上 `--pack 写作包/某某写作包`，门禁会读取该包的叙述句句长分布设定阈值，不会把长复句派大神的原著风格误判为病句。对白很多的书也一样：草稿和写作包都只算叙述句，换行也算句子结束，“周管事淡淡道：“坐。””这类对白段不会和后面几段粘成一句。旧版本建的写作包没有叙述句统计时，门禁会用包内 `corpus/` 现场重算。

---

## 版权与免责

- 本仓库**不附带任何受版权保护的小说原文语料或成品写作包**。写作包需由用户使用自己合法持有的文本在本地生成，`corpus/` 副本仅保存在本地用于校验。
- 本项目基于 MIT 协议开源，按「现状」提供。
