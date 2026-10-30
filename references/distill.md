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
```

- **语料含金量与偏科预警**：若用户提供的章节场景单一（例如只有赶路过渡而无核心战斗或谈判），照常完成蒸馏，但在交付报告和 `缺口与补充建议.md` 中明确指出可补充哪几类代表性章节（严禁建议补充“第一人称叙述”章节，中文网文统一为第三人称体系）。
- 读取自动生成的 `stats.json`，提取 **L1 语言基线指标**：
  - **句长分布**：`sentence_chars.mean`（均值）、`sd`（标准差）、`p90`、`short_le_10_share`（≤10字短句率）、`long_ge_40_share`（≥40字长复句率）。（该数据后续也将用于 `check_novel_ai.py --pack` 的动态句法校准）。
  - **段落节奏**：`paragraph_chars.mean`、`single_sentence_share`（单句成段占比）。
  - **引号内文本占比**：`quoted_char_share` 与 `punctuation`、`markers_per_10k_chars`。

### Step 2：分批通读与建立精准场景切片（`analysis.jsonl` & `scenes.jsonl`）

按章节顺序分批通读 `corpus/` 原文，完成两项记录：

1. **逐章观察记录（`analysis.jsonl`）**：
   - 每份输入对应一条记录（`sha256` 可直接写 `""`，后续由 `resolve` 自动填入）：
     ```json
     {"source_id": "C0001", "sha256": "", "observation": "本章切入手法、冲突推进步点、第三人称限知视角调度与断章方式", "rule_ids": ["R001", "R003"], "unresolved": []}
     ```
   - `observation` 必须写清**叙事手法与调度决策**，严禁写成单纯的剧情梗概。
2. **段落级黄金场景切片（`scenes.jsonl`）**：
   - 除非输入本身不足 40 行，否则**严禁把整章打包成一个 `scene`**！切出最具代表性的交锋、战斗、算账、用典或情绪片段（通常 15~80 行）。
   - **免数行号写法**：直接在 `spans` 中提供该片段首句与尾句的原文锚点（`start_quote` 与 `end_quote`），无需人工计算行号：
     ```json
     {"id": "S004", "tags": ["地位差对白", "妥协与威胁"], "summary": "家老从碎尸木盒放置位置拆解求和与威慑双层算计", "spans": [{"source_id": "C0004", "start_quote": "书房内陷入短暂的沉默", "end_quote": "这四条处置，你亲自去办"}]}
     ```

### Step 3：提炼有原文铁证的可执行规则（`rules.jsonl`）

对照 `references/dimensions.md` 的九个细分维度（`语言与叙述`、`场景与节奏`、`人物与对白`、`博弈与关系`、`战斗与行动`、`世界与题材`、`修辞与用典`、`信息与连载`、`情绪与主题`），提炼规则写入 `rules.jsonl`。

每条规则包含 `id`（`R001`...）、`dimension`、`claim`、`when`、`do`、`effect`、`avoid`、`strength`（`repeated` / `local` / `tentative`）、`evidence` 与 `counterevidence`。
- **免数行号写法**：`evidence` 中只需提供 `source_id` 与逐字原文 `quote`（例如 `{"source_id": "C0004", "quote": "何不将这箱子放在人来人往的正门，反而放在了人迹罕至的后门？"}`），行号交给下一步 `resolve` 自动定位！

### Step 4：一键自动解析行号与哈希（`resolve`）

写完 `analysis.jsonl`、`scenes.jsonl`、`rules.jsonl` 后，立即运行：

```bash
python "<SKILL_DIR>/scripts/novel_pack.py" --json-out .work/resolve.json resolve --pack "写作包/<书名>写作包"
```

- 脚本会自动填入 `analysis.jsonl` 的精确 `sha256`，将 `rules.jsonl` 中的 `quote` 定位到精确的 `start_line ~ end_line`（自动兼容直引号与弯引号并修正为原文逐字切片），并将 `scenes.jsonl` 的 `start_quote / end_quote` 换算为标准 `start_line / end_line`。
- 若个别引文拼写有误，`resolve` 会在 `errors` 中精准报出哪条规则未命中，用 `edit` 修正该条引文后重跑一次即可。

### Step 5：编写高密度分层指纹模块与总控台（`指纹/*.md` & `PACK.md` & `coverage.json`）

1. 参考 `templates/pack-zh/` 编写 `指纹/*.md`，每个模块必须包含：**核心句法与叙事配方（含 `Rxxx` 引用） + Step 1~4 场景推进结构模板 + 原著黄金段落解剖 + Before/After 第三人称迁移改写示范**。
2. 编写 `PACK.md`（调用名、L1 量化基线、十大核心技法、按场景检索路由表、迁移红线）、`coverage.json` 与 `缺口与补充建议.md`。

### Step 6：运行校验与交付（`validate`）

1. 将 `manifest.json` 中的 `status` 设为 `"ready"`，并核对 `modules` 列表完整。
2. 运行校验命令：
   ```bash
   python "<SKILL_DIR>/scripts/novel_pack.py" --json-out .work/validate.json validate --pack "写作包/<书名>写作包"
   ```
3. 确认 `"status": "PASS"` 且 `errors` 为空后，向用户汇报写作包路径、核心文风指纹亮点及可补充的场景建议。

---

## 三、增量更新流程（`add`）

当用户后续补充新章节时：
1. 运行 `python "<SKILL_DIR>/scripts/novel_pack.py" add --pack <包路径> --input <新章节>`（保留旧分析，仅导入新文件并将状态回退为 `"draft"`，自动重算 `stats.json`）。
2. 仅通读新增的 `Cxxxx` 文件，追加 `analysis.jsonl`、`scenes.jsonl` 与新增/强化的 `rules.jsonl`，运行 `novel_pack.py resolve --pack <包路径>` 自动解析行号与哈希。
3. 同步更新对应的 `指纹/*.md`、`PACK.md`、`coverage.json` 与 `缺口与补充建议.md`，运行 `validate` 通过后将 `status` 恢复为 `"ready"`。
