# 写作包数据规范（pack-format.md v1）

写作包是独立、可迁移、可校验的小说文风数据包，默认存放在当前项目的 `写作包/<包名称>/` 目录下。

---

## 一、标准目录结构

```text
写作包/某小说写作包/
  PACK.md                      # 总控入口：文风速览、核心配方、按场景读取路由表、适用边界
  manifest.json                # 元数据：包名、别名、版本、状态（draft/ready）、模块声明
  corpus.jsonl                 # 语料索引：每份输入原文的ID（C0001...）、编码、行数、字数、SHA-256
  corpus/
    C0001.md                   # 用户提供的原文副本（只读，供行号定位与黄金切片调阅）
  stats.json                   # L1 语言学量化统计（句长均值/SD、短长句占比、单句成段率、标志词频次）
  analysis.jsonl               # 逐章精读记录：每份输入的叙事结构、核心技法观察与未决跨章问题
  scenes.jsonl                 # 段落级黄金场景切片索引（带精确起止行号与多维标签）
  rules.jsonl                  # 可执行规则库（含触发条件、步点、效果、禁忌、原文逐字证据与反例）
  coverage.json                # 九个基础维度（及扩展维度）的覆盖状态与补充建议
  lexicon.json                 # 正向替换数据库（11类词汇库、5类衔接库、剧情语义句段库、角色声线卡、设定转换槽）
  规则索引.md                  # （可选）按维度归类的规则速查表
  词句库/                      # 供写作与改写直接调用的“正向替换四件套”（由 init / scan-lexicon 自动生成并可精修）
    词汇库.md                  # 11 类原著实感词汇库（含原著行号例证与跨题材新书设定转换槽）
    衔接库.md                  # 5 类原著句内推演与段间转场衔接词库（顶替 AI 的“然而/就在这时/与此同时”）
    句段库.md                  # 剧情微语义句段碰撞库（算账、看破陷阱、破招定格、地位施压、绝境取舍）
    声线卡.md                  # 角色声线卡与多人物对白防串味档案
  指纹/                        # 高密度分层写作 DNA 模块（按实际语料覆盖生成）
    语言与节奏.md              # L1 语言与叙事腔调 + 变速节奏
    人物对白与势力.md          # L2 人物塑造与双轨对白 + L3 利益博弈与势力约束
    战斗与设定.md              # L4 战斗回合拆解 + 能力克制 + 设定落地
    修辞用典与信息.md          # L5 诗词典故/嵌套寓言 + L6 信息差与连载钩子
    情绪与主题.md              # L5 情绪蓄势 + 主题分化 + 去AI修订边界
    日常与职业.md              # （按需）识货算账、经营交易、小人物处境与日常过渡
    <扩展维度名>.md            # （按需）作者独有的特色写法，如 喜剧与调剂.md、系统与数值.md
  缺口与补充建议.md            # 面向作者的缺口诊断与定向场景补充指南
```

> 上面列的六个指纹文件是常见分组，不是固定数量：语料没覆盖的维度可以不单独成文件（例如没有日常经营戏就不写 `日常与职业.md`），作者有鲜明特色时可以加扩展维度文件。判断写作包够不够用，看 `validate` 返回的 `depth` 统计（规则数、切片数、被引用章节占比），不看文件个数。

> 新建写作包时，可直接参考 `novel-dna-skill/templates/pack-zh/` 中的高标准模板编写 `PACK.md`、`指纹/*.md` 与 `缺口与补充建议.md`。

---

## 二、核心 JSON / JSONL 字段规范

### 1. `manifest.json`

```json
{
  "type": "novel-writing-pack",
  "format_version": 1,
  "name": "示例作品写作包",
  "aliases": ["示例包"],
  "kind": "writing",
  "status": "ready",
  "revision": 1,
  "source_mode": "bundled",
  "scope": "用户提供的精选章节，不冒充未读章节",
  "modules": [
    {"file": "指纹/语言与节奏.md", "dimensions": ["语言与叙述", "场景与节奏"]}
  ],
  "intake_notes": []
}
```
- `kind`：`"writing"`（正式写作包）或 `"demo"`（轻量演示包）。
- `status`：`"draft"`（收集中或分析未完成）或 `"ready"`（本批输入已全部精读、提取并校验通过）。

### 2. `stats.json`（由 `novel_pack.py` 自动生成）
包含全包聚合（`aggregate`）与单文件（`files`）的客观语言特征：
- `sentence_chars`：`count`、`mean`、`sd`（句长标准差）、`median`、`p10`、`p90`、`short_le_10_share`（≤10字短句率）、`long_ge_40_share`（≥40字长句率）。按句末标点切分，含对白。
- `narrative_sentence_chars`：只算叙述句的句长分布（去掉引号内对白与 `【】` 面板，句末标点和换行都算句子结束）。`check_novel_ai.py --pack` 用它和草稿的叙述句均值比较，两边算法相同。
- `zhe_per_1k`、`narrative_semicolons_per_1k`：“着”字与叙述正文分号的千字频率，供门禁按目标作者习惯放宽上限。
- `paragraph_chars`：`count`、`mean`、`sd`、`median`、`p10`、`p90`、`single_sentence_share`（单句成段率）。
- `quoted_char_share`：引号内文字占比。
- `punctuation`：中文标点分布。
- `markers_per_10k_chars`：高频转折、推演与判断标志词每万字出现频次。

### 3. `analysis.jsonl`
每份 `corpus.jsonl` 中的来源必须有一条对应的精读观察记录（编写时 `sha256` 可填 `""`，运行 `novel_pack.py resolve` 会自动从 `corpus.jsonl` 填入精确哈希）：
```json
{"source_id": "C0004", "sha256": "...", "observation": "书房训诫通过新消息改变上位者判断；年轻辈将信物视为挑衅，掌权老手从放置位置与器物尺寸拆解出妥协与威胁并存的筹码，最终以四项处置平衡派系规矩。", "rule_ids": ["R002", "R005", "R006", "R007", "R008", "R016"], "unresolved": ["本章前置的冲突现场不在本批语料内"]}
```

### 4. `scenes.jsonl`（段落级黄金场景切片）
```json
{"id": "S004", "tags": ["地位差对白", "妥协与威胁", "筹码拆解"], "summary": "上下位角色围绕后门木盒展开认知交锋，拆解对手既求和又威慑的算计并拍板处置", "spans": [{"source_id": "C0004", "start_line": 14, "end_line": 58}]}
```
- **精度与免数行号支持**：`spans` 指向章内具体场景所在的段落行号区间；生成时可在 span 中先写 `"start_quote": "起始句原文", "end_quote": "结束句原文"`，运行 `novel_pack.py resolve --pack <包路径>` 即可自动换算并替换为标准 `start_line` / `end_line`。

### 5. `rules.jsonl`（可执行规则与原文铁证）
```json
{
  "id": "R005",
  "dimension": "人物与对白",
  "claim": "让同一事实的不同解读表现阅历和权力差",
  "when": "原剧情已有一方误读、另一方看破门道的上下位或老少关系时",
  "do": "先展示浅层角色的情绪化直觉反应，再让老练者抓住被忽略的具体物理/规矩条件（如放置地点、尺寸、时机）逐层反问推演，不用旁白硬贴聪明标签",
  "effect": "人物城府与权柄差异在拆解问题的过程中自然显影",
  "avoid": "不能替原本不知情的角色补上帝视角，不能让配角无故降智",
  "strength": "repeated",
  "evidence": [
    {"source_id": "C0004", "start_line": 40, "end_line": 43, "quote": "何不将这箱子放在人来人往的正门，反而放在了人迹罕至的后门？"}
  ],
  "counterevidence": []
}
```
- `quote` 必须**逐字存在于所引用的 `start_line ~ end_line` 之间**；生成时可只填 `source_id` 与 `quote`，由 `novel_pack.py resolve` 自动定位 `start_line ~ end_line`，再由 `novel_pack.py validate` 执行严格的精确子串校验，彻底杜绝 AI 编造假引文。

### 6. `coverage.json`
必须覆盖九个基础维度（`语言与叙述`、`场景与节奏`、`人物与对白`、`博弈与关系`、`战斗与行动`、`世界与题材`、`修辞与用典`、`信息与连载`、`情绪与主题`）。作者有九个维度装不下的鲜明特色时，可以加扩展维度（判断标准见 `references/dimensions.md` 末尾“扩展维度”）：规则里用到的每个维度名都必须在这里登记，扩展维度通常再配一个同名指纹模块。
- `status`：`"observed"` / `"partial"` / `"unobserved"` / `"not_applicable"`。
- `rule_ids`：归属该维度的全部规则 ID。
- `note`：当前掌握程度与局限说明。
- `suggested_inputs`：建议作者补充的具体场景类型。

### 7. `lexicon.json` 与 `词句库/*.md`（正向替换词句数据库）
由 `novel_pack.py init`、`add` 或 `scan-lexicon` 零 Token 自动扫描生成（支持 `--lexicon-source <全书目录或zip>` 在主体只蒸馏精选章节以节省 Token 的同时，毫秒级扫描全书扩充 11 类词汇库与衔接库），并由大模型在蒸馏阶段按需精修补充：
- `categories`（11 类原著实感词汇库，每条含 `word`、`count`、`replaces_ai` 及绑定 `source_id/start_line/end_line/quote` 的 `evidence`）：
  1. `发力与博弈动词`
  2. `眼神与视线词`
  3. `面容与神态词`
  4. `肢体与生理反馈词`
  5. `开口与言语导引词`
  6. `心理算计与决断词`
  7. `程度与范围副词`
  8. `鲜活四字格与俗语`
  9. `声光打击与感官词`
  10. `环境器物与空间词`
  11. `世界观与筹码计量词`（附带 `transfer_slot` 跨书设定转换说明）
- `connectors`（5 类衔接库）：`句内转折与推演`、`段间过渡与承接`、`突发打断与变故`、`时间压缩与快进`、`视角与空间切入`。
- `sentence_bank`：按剧情微语义（`利害权衡与算账推演`、`看破陷阱与认知差`、`战局压迫与破招定格`、`地位施压与给台阶`、`绝境决断与狠辣取舍`）索引的原著句段骨架。
- `voice_cards`：按角色/身份槽位统计的平均句长、腔调特征、防串味禁忌与原著对白例证。
- `world_terms_lock` 与 `term_transfer_map`：记录原著独有的世界观体系专名（如 `蛊虫/仙窍/道痕`），供 `check_novel_ai.py` 在跨书改写时拦截专名串戏，并指导转换为新书设定。
- `resolve` 与 `validate` 会自动解析并严格校验 `lexicon.json` 中所有引自 `Cxxxx` 的例句行号与精确子串。

---

## 三、指纹模块（`指纹/*.md`）编写规范

每个声明在 `manifest.json` 中的 Markdown 模块必须满足：
1. **显式引用归属该模块的规则 ID（如 `R001`）**（校验器会检查模块中出现的每个 `R` + 三位以上数字是否都在 `rules.jsonl` 中存在，紧贴汉字的写法如 `见R999条` 同样会被检查）。
2. 包含 **【句法配方】+【场景推进结构模板（Step 1~4）】+【原著黄金段落解剖】+【Before/After 迁移示例】** 四要素。
3. **不得残留模板占位符**：`status` 为 `ready` 时，`validate` 会把 `Rxxx`、`Sxxx`、`Cxxxx`、`XX.X`、`X–Y`、`<作品名>` 以及 `templates/pack-zh/` 中原样留下的 `<……>` 填写提示报为错误。
4. **纯净性铁律**：指纹模块中所有原著例证必须 100% 来自该写作包的 `corpus/` 原文，严禁把外部测试稿（如用户的待改稿）的人物或物件混入写作包指纹中！
