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
  coverage.json                # 九大基础维度的覆盖状态与补充建议
  规则索引.md                  # （可选）按维度归类的规则速查表
  指纹/                        # 高密度分层写作 DNA 模块（按实际语料覆盖生成）
    语言与节奏.md              # L1 语言与叙事腔调 + 变速节奏
    人物对白与势力.md          # L2 人物塑造与双轨对白 + L3 利益博弈与势力约束
    战斗与设定.md              # L4 战斗回合拆解 + 能力克制 + 设定落地
    修辞用典与信息.md          # L5 诗词典故/嵌套寓言 + L6 信息差与连载钩子
    情绪与主题.md              # L5 情绪蓄势 + 主题分化 + 去AI修订边界
    日常与职业.md              # （按需）识货算账、经营交易、小人物处境与日常过渡
  缺口与补充建议.md            # 面向作者的缺口诊断与定向场景补充指南
```

> 新建写作包时，可直接参考 `novel-dna-skill/templates/pack-zh/` 中的高标准模板编写 `PACK.md`、`指纹/*.md` 与 `缺口与补充建议.md`。

---

## 二、核心 JSON / JSONL 字段规范

### 1. `manifest.json`

```json
{
  "type": "novel-writing-pack",
  "format_version": 1,
  "name": "蛊真人写作包",
  "aliases": ["蛊真人包"],
  "kind": "writing",
  "status": "ready",
  "revision": 1,
  "source_mode": "bundled",
  "scope": "用户提供的《蛊真人》精选章节，不冒充未读章节",
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
- `sentence_chars`：`count`、`mean`、`sd`（句长标准差）、`median`、`p10`、`p90`、`short_le_10_share`（≤10字短句率）、`long_ge_40_share`（≥40字长句率）。
- `paragraph_chars`：`count`、`mean`、`sd`、`median`、`p10`、`p90`、`single_sentence_share`（单句成段率）。
- `quoted_char_share`：引号内文字占比。
- `punctuation`：中文标点分布。
- `markers_per_10k_chars`：高频转折、推演与判断标志词每万字出现频次。

### 3. `analysis.jsonl`
每份 `corpus.jsonl` 中的来源必须有一条对应的精读观察记录（编写时 `sha256` 可填 `""`，运行 `novel_pack.py resolve` 会自动从 `corpus.jsonl` 填入精确哈希）：
```json
{"source_id": "C0004", "sha256": "...", "observation": "书房训诫通过新消息改变上位者判断；孙女将碎尸木盒视为挑衅，家老从后门位置与盒子尺寸拆解出妥协与威胁并存的筹码，最终以四项处置平衡派系规矩。", "rule_ids": ["R002", "R005", "R006", "R007", "R008", "R016"], "unresolved": ["本章前置的高碗被杀现场不在本批语料内"]}
```

### 4. `scenes.jsonl`（段落级黄金场景切片）
```json
{"id": "S004", "tags": ["地位差对白", "妥协与威胁", "筹码拆解"], "summary": "古月漠尘与漠颜围绕后门碎尸木盒展开认知交锋，拆解方源既求和又威慑的算计并拍板处置", "spans": [{"source_id": "C0004", "start_line": 14, "end_line": 58}]}
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
必须覆盖九个基础维度（`语言与叙述`、`场景与节奏`、`人物与对白`、`博弈与关系`、`战斗与行动`、`世界与题材`、`修辞与用典`、`信息与连载`、`情绪与主题`），也可根据题材扩展新维度：
- `status`：`"observed"` / `"partial"` / `"unobserved"` / `"not_applicable"`。
- `rule_ids`：归属该维度的全部规则 ID。
- `note`：当前掌握程度与局限说明。
- `suggested_inputs`：建议作者补充的具体场景类型。

---

## 三、指纹模块（`指纹/*.md`）编写规范

每个声明在 `manifest.json` 中的 Markdown 模块必须满足：
1. **显式引用归属该模块的 `Rxxx` 规则 ID**（校验器会自动检查模块中出现的 `R\d{3,}` 是否均在 `rules.jsonl` 中存在）。
2. 包含 **【句法配方】+【场景推进结构模板（Step 1~4）】+【原著黄金段落解剖】+【Before/After 迁移示例】** 四要素。
3. **纯净性铁律**：指纹模块中所有原著例证必须 100% 来自该写作包的 `corpus/` 原文，严禁把外部测试稿（如用户的待改稿）的人物或物件混入写作包指纹中！
