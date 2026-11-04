# 小说语料蒸馏与增量更新流程（distill.md）

本指南规定如何将用户提供的小说章节高效蒸馏为高可用、可验证的命名写作包（如 `写作包/<书名>写作包/`）。

---

## 一、核心原则：给多少蒸馏多少，重质不凑数

1. **输入范围完全服从用户，严禁凭预训练记忆猜书名或作者**：
   - 用户给 1 章、6 章、44 章或整卷，就只蒸馏这些正文；**不设最低章数门槛**。
   - **严禁私自扩充语料或脑补作者**：不自动读取用户未指定的相邻章节，**更绝对禁止根据文中人名去猜“这是某某作者写的某某书”并在思考或文档里乱套外部作者名号**！一切判断只认当前 `corpus/` 文本。
   - 目录中的写作教材、说明文档只作为分析方法参考，绝不混入 `corpus/` 充当目标小说原文。
2. **用 `resolve` 自动化机械定位，把精力花在深度技法拆解上**：
   - 严禁浪费时间手工数行号或手抄 64 位哈希！`rules.jsonl` 和 `scenes.jsonl` 中的原文行号、`analysis.jsonl` 中的文件哈希全部交给 `novel_pack.py resolve` 一键精确换算。

---

## 二、标准蒸馏六步法

### Step 1：初始化包、语料偏科预检与表层语言统计（`init` & `stats`）

运行脚本（`<SKILL_DIR>` 为本技能 `SKILL.md` 所在目录）收集明确选定的 `.md / .txt` 语料：

```bash
python "<SKILL_DIR>/scripts/novel_pack.py" --json-out .work/init.json init --name "<书名>写作包" --output "写作包/<书名>写作包" --input <章节路径...>

# （可选省Token高阶模式）主体只蒸馏精选章节（如193章），同时用本地脚本 0 Token 扫描全书目录/压缩包扩充 11 类词汇库与衔接库：
python "<SKILL_DIR>/scripts/novel_pack.py" --json-out .work/init.json init --name "<书名>写作包" --output "写作包/<书名>写作包" --input <精选章节路径...> --lexicon-source <全书目录或zip路径...>
```

- **语料含金量与偏科预警**：若用户提供的章节场景单一（例如只有赶路过渡而无核心战斗或谈判），照常完成蒸馏，但在交付报告和 `缺口与补充建议.md` 中明确指出可补充哪几类代表性章节（不要把“补充第一人称叙述章节”列为建议；语料本身是第一人称时如实记录其人称）。
- **输入顺序**：传入目录时，`init`/`add` 按章节号自然排序编号（`第2章` 排在 `第10章` 前，`第四节` 排在 `第十节` 前）；逐个传入文件时按传入顺序编号。`C0001`、`C0002` 的顺序就是阅读顺序。
- 读取自动生成的 `stats.json`，提取 **L1 语言基线指标**：
  - **句长分布**：`sentence_chars.mean`（均值）、`sd`（标准差）、`p90`、`short_le_10_share`（≤10字短句率）、`long_ge_40_share`（≥40字长复句率）。
  - **叙述句句长**：`narrative_sentence_chars.mean` 与 `p90`（去掉对白后的叙述句，`check_novel_ai.py --pack` 用这一口径做句长校准）。
  - **段落节奏**：`paragraph_chars.mean`、`single_sentence_share`（单句成段占比）。
  - **引号内文本占比**：`quoted_char_share` 与 `punctuation`、`markers_per_10k_chars`。
  - **世界文风肌理提取**：
    - **本土语域实词库**（30~50词）：收集该世界特有的名物系统（仙侠的「丹田/符箓/洞府」、末世的「基地/变异/晶核」、历史的「折子/圣旨/家老」）。
    - **比喻母题配方**（5~8组）：提炼该世界的隐喻框架（仙侠的「灵泉/道果/天劫」比喻、西幻的「圣光/黑暗/契约」比喻）。
    - **世界推演句式**（3~5式）：提取如何把逻辑转折词嵌入世界观推演（仙侠的「若是渡劫失败便…」、末世的「纵然丧尸围城也…」）。
    - **情感极值温度标尺**：确认愤怒、绝望、欣喜、压抑在这个世界如何克制或放大表达。
    - 区分**角色特殊语域**（某个角色独有的口头禅、文言古语、方言）与**本土世界叙事层**（整体世界观的名物与修辞），前者属于人物对白，后者属于世界文风肌理。

### Step 2：分批通读与建立精准场景切片（`analysis.jsonl` & `scenes.jsonl`）

按章节顺序分批通读 `corpus/` 原文，完成两项记录：

1. **逐章观察记录（`analysis.jsonl`）**：
   - 每份输入对应一条记录（`sha256` 可直接写 `""`，后续由 `resolve` 自动填入）：
     ```json
     {"source_id": "C0001", "sha256": "", "observation": "本章切入手法、冲突推进步点、第三人称限知视角调度与断章方式", "rule_ids": ["R001", "R003"], "unresolved": []}
     ```
   - `observation` 必须写清**叙事手法与调度决策**，严禁写成单纯的剧情梗概。
2. **段落级黄金场景切片（`scenes.jsonl`）**：
   - 除非输入本身不足 50 行，否则**严禁把整章打包成一个 `scene`**！切出最具代表性的交锋、战斗、算账、用典或情绪片段（通常 15~80 行）。`validate` 发现切片覆盖 50 行以上章节的 90% 以上时会给出警告。
   - **免数行号写法**：直接在 `spans` 中提供该片段首句与尾句的原文锚点（`start_quote` 与 `end_quote`），无需人工计算行号：
     ```json
     {"id": "S004", "tags": ["地位差对白", "妥协与威胁"], "summary": "家老从碎尸木盒放置位置拆解求和与威慑双层算计", "spans": [{"source_id": "C0004", "start_quote": "书房内陷入短暂的沉默", "end_quote": "这四条处置，你亲自去办"}]}
     ```

### Step 3：提炼有原文铁证的可执行规则（`rules.jsonl`）

按 `references/dimensions.md` 的 L1–L6 观察层次拆解，再按该文件开头的对应表，给每条规则的 `dimension` 填九个基础维度名之一（`语言与叙述`、`场景与节奏`、`人物与对白`、`博弈与关系`、`战斗与行动`、`世界与题材`、`修辞与用典`、`信息与连载`、`情绪与主题`），提炼规则写入 `rules.jsonl`。作者有九个维度装不下的鲜明特色（例如大量喜剧调剂），按 `dimensions.md` 末尾“扩展维度”另建维度和指纹模块；扩展维度必须登记在 `coverage.json`，否则 `validate` 报错。

每条规则包含 `id`（`R001`...）、`dimension`、`claim`、`when`、`do`、`effect`、`avoid`、`strength`（`repeated` / `local` / `tentative`）、`evidence` 与 `counterevidence`。
- **免数行号写法**：`evidence` 中只需提供 `source_id` 与逐字原文 `quote`（例如 `{"source_id": "C0004", "quote": "何不将这箱子放在人来人往的正门，反而放在了人迹罕至的后门？"}`），行号交给下一步 `resolve` 自动定位！
- **`strength` 要有依据**：标 `repeated` 的规则，`evidence` 至少引用 2 个不同章节；只在一章里见到的写 `local`；推测性的写 `tentative`。
- **写反例**：`counterevidence` 记录原著里“同类场景却没这样写”的段落，或这条规则不适用的情形。规则越核心越要写，避免改写时机械套用。

### 蒸馏深度：语料越多，产出越多

用户给多少章就读多少章，读过的内容要在写作包里留下可追溯的东西，不能只给每个维度凑三五条规则。`validate` 会报告 `depth`（深度统计），达不到下表时给出 `depth:` 警告（只提醒，不影响 `PASS`）：

| 指标 | 建议值（N = 章节数） | 说明 |
| --- | --- | --- |
| 规则数 | 至少 `27 + N/6` 条，最多要求 80 条 | 117 章约 46 条；10 章以下不检查 |
| 场景切片数 | 至少 `N/2` 个（不少于 10，最多要求 120） | 117 章约 58 个 |
| 被引用章节占比 | 至少 60% 的章节被规则证据或场景切片引用 | 读过却没留下证据的章节越少越好 |
| 写了反例的规则 | 至少 20% | 核心规则优先写 |
| `repeated` 规则 | 每条至少 2 章证据 | 不满足时改为 `local`，或补第二章证据 |
| **蒸馏密度（蒸馏比）** | `产出总字符(不含空白) / 原文字数(不含空白) ≥ 0.04` | 监控蒸馏密度，防止 AI 读到后面疲劳偷懒；`validate` 自动在 `depth.distillation_ratio` 统计 |
| **11 类词汇库与四库完整性** | `lexicon.json` 覆盖全部 11 类词汇、5 类衔接词、句段库与声线卡 | `init` 或 `scan-lexicon` 自动扫描底表，大模型在 Step 5 结合精读核对补充 |

`depth` 警告是写作包偏薄的信号：说明大量章节读完没产出东西，或“反复出现”缺少依据。交付前能补就补；语料确实单一时，在 `缺口与补充建议.md` 里说明原因。

### Step 4：一键自动解析行号与哈希（`resolve`）

写完 `analysis.jsonl`、`scenes.jsonl`、`rules.jsonl` 后，立即运行：

```bash
python "<SKILL_DIR>/scripts/novel_pack.py" --json-out .work/resolve.json resolve --pack "写作包/<书名>写作包"
```

- 脚本会自动填入 `analysis.jsonl` 的精确 `sha256`，将 `rules.jsonl` 中的 `quote` 定位到精确的 `start_line ~ end_line`（自动兼容直引号与弯引号并修正为原文逐字切片），并将 `scenes.jsonl` 的 `start_quote / end_quote` 换算为标准 `start_line / end_line`。
- `analysis.jsonl` 某条记录的 `rule_ids` 为空、但写了临时字段 `dims`（维度名列表）时，`resolve` 只会挂上**该维度下、且证据就出自这一章**的规则；没有本章证据的规则不会被挂上，需要时请手填 `rule_ids`。
- `slice` 依赖 `start_line / end_line`，必须在 `resolve` 之后运行。
- 若个别引文拼写有误，`resolve` 会在 `errors` 中精准报出哪条规则未命中，用 `edit` 修正该条引文后重跑一次即可。

### Step 5：编写高密度分层指纹模块与总控台（`指纹/*.md` & `PACK.md` & `coverage.json`）

1. 参考 `templates/pack-zh/` 编写 `指纹/*.md`，每个模块必须包含：**核心句法与叙事配方（引用真实规则编号如 `R001`） + Step 1~4 场景推进结构模板 + 原著黄金段落解剖 + Before/After 迁移改写示范**。模板中的 `Rxxx`、`Sxxx`、`Cxxxx`、`XX.X`、`X–Y` 与 `<粘贴原著…>` 一类占位符必须全部替换；`status` 为 `ready` 时 `validate` 会把残留占位符报为错误。
2. 编写 `PACK.md`（调用名、L1 量化基线、十大核心技法、按场景检索路由表、迁移红线）、`coverage.json` 与 `缺口与补充建议.md`。

### Step 6：运行校验与交付（`validate`）

1. 将 `manifest.json` 中的 `status` 设为 `"ready"`，并核对 `modules` 列表完整。
2. 运行校验命令：
   ```bash
   python "<SKILL_DIR>/scripts/novel_pack.py" --json-out .work/validate.json validate --pack "写作包/<书名>写作包"
   ```
3. 确认 `"status": "PASS"` 且 `errors` 为空后，查看 `warnings` 里的 `depth:` 项：能补的规则证据、切片和反例先补上，再重跑一次 `validate`。
4. 向用户汇报写作包路径、核心文风指纹亮点、`depth` 统计（规则数、切片数、被引用章节占比）及可补充的场景建议。

---

## 三、增量更新流程（`add`）

当用户后续补充新章节时：
1. 运行 `python "<SKILL_DIR>/scripts/novel_pack.py" add --pack <包路径> --input <新章节>`（保留旧分析，仅导入新文件并将状态回退为 `"draft"`，自动重算 `stats.json`）。
2. 仅通读新增的 `Cxxxx` 文件，追加 `analysis.jsonl`、`scenes.jsonl` 与新增/强化的 `rules.jsonl`，运行 `novel_pack.py resolve --pack <包路径>` 自动解析行号与哈希。
3. 同步更新对应的 `指纹/*.md`、`PACK.md`、`coverage.json` 与 `缺口与补充建议.md`，运行 `validate` 通过后将 `status` 恢复为 `"ready"`。

---

## 四、可选增强：整合外部 `oh-story` 拆文库（`integrate-analysis`，可有可无）

> **核心原则**：**拆文库在应用上是可选增强项（可有可无）**。
> - **没有拆文库时**：写作包依靠自身语料蒸馏出的 `PACK.md`、`指纹/*.md`、`rules.jsonl`、`scenes.jsonl` 与 `词句库/` 即可 100% 独立完成高质量改写与按纲创作；
> - **有拆文库时**：如果用户自行使用 `oh-story` 技能（如 `story-long-analyze` / `story-short-analyze`）对目标小说做了拆文，并把拆完的拆文库目录发给本技能，可一键整合进写作包 `拆文增强/` 目录，在**第一步：依照写作包改写/创作**阶段提供全书级宏观写法、潜台词范式与可复现戏剧模块卡（`EM-001`~`EM-xxx`），让改写更得神韵！

### 整合步骤
1. 运行整合命令：
   ```bash
   python "<SKILL_DIR>/scripts/novel_pack.py" integrate-analysis --pack "写作包/<书名>写作包" --analysis-dir "<oh-story拆文库路径>"
   ```
2. 脚本会自动读取拆文库中的 `文风.md`、`快速预览.md`、`拆文报告.md`、`剧情/情绪模块.md`、`剧情/节奏.md`，在写作包下生成：
   - `拆文增强/文风与对话技法.md`（全书整体语感、句段鼓点、潜台词三模式、角色语气区分）
   - `拆文增强/核心写法与可借鉴套路.md`（全书核心发现、读者追读点、可借鉴套路、写法技巧）
   - `拆文增强/情绪模块与戏剧单元.md`（读者需求引擎与全部 `EM-001`~`EM-xxx` 可复现戏剧模块卡）
   - `拆文增强/节奏循环与信息差机制.md`（3章小循环/7章中循环/15章大循环与双时间线信息差机制）
   - 同时自动在 `PACK.md` 与 `manifest.json` 挂载入口，并支持通过 `novel_pack.py lookup` 检索匹配的情绪模块卡。
3. 运行 `python "<SKILL_DIR>/scripts/novel_pack.py" validate --pack "写作包/<书名>写作包"` 确认校验通过。
