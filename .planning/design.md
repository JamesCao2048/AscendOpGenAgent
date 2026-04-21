# Phase 4 Subagent Delegation — Finalized Design

> **Status:** Reviewed and corrected by Codex. This version supersedes `.planning/design_draft.md`.

## 1. 目标

1. 把两个 Markdown 形式的 agent 定义迁移为 Codex 可加载的 `.codex/agents/*.toml`，同时保留 `agents/*.md` 作为人类文档与 standalone 调度入口。
2. 将 `ascend-kernel-developer` 的 Phase 4 从三轮自循环改为一次性线性流程；数值失败时委派给 `cann-debug-agent`。
3. 将精度修复循环继续保留在精度调优子 agent 内部，沿用既有 `precision_forensics.py` / `precision_gate.py` 工作流。
4. 定义 parent→subagent 的首次输入契约，使 parent-spawn 与 standalone 两条路径共享同一组先决条件和同一套 Gate 语义。

> **Codex review note:** 保留了 draft 的总体目标，但把“自动注册后即可无歧义启动”改为“可加载 + 可验证”。原因是当前仓库证据只覆盖已有 Markdown agent/skill 工作流与 Phase 4/precision 流程本身，并未在参考文件里给出 Codex custom-agent 的正式 loader 约束；因此目标应落在可验证行为而不是未验证的 runtime 假设上。证据：`agents/ascend-kernel-developer.md:304-392`、`agents/precision-tuning-discovery.md:26-39`、`skills/ascendc/precision-tuning/SKILL.md:31-41`、`utils/run_precision_tuning.sh:34-52`。

## 2. 非目标（YAGNI）

- 不改 `utils/run_precision_tuning.sh` 的调用方式、任务分发方式或 prompt 模板变量接口。
- 不改 `precision_forensics.py` / `precision_gate.py` 的既有输入输出契约。
- 不恢复 Phase 4 的 parent 自修复多轮循环。
- 不引入第三方 subagent/MCP 机制。

> **Codex review note:** 将“不改脚本契约”保留为硬边界，因为 Gate 已经固化了 `forensics_report_{attempt}.json`、`precision_audit_{attempt}.md`、`validation_result_attempt_{attempt}.json`、`round_summary_{attempt}.json`、`tuning_directions.json` 等链式产物；改这些脚本会扩大设计面。证据：`skills/ascendc/precision-tuning/scripts/precision_gate.py:9-22, 51-84, 134-188, 237-283, 423-573`。

## 3. 目录与文件

```text
AscendOpGenAgent/
├── .codex/
│   ├── AGENTS.md
│   └── agents/
│       ├── ascend-kernel-developer.toml
│       └── cann-debug-agent.toml
├── agents/
│   ├── ascend-kernel-developer.md
│   ├── precision-tuning-discovery.md
│   └── ...
└── ...
```

设计决策：
- `.codex/agents/*.toml` 作为 Codex runtime 配置载体。
- `agents/*.md` 继续保留，因为 `run_precision_tuning.sh` 目前显式把 `agents/precision-tuning-discovery.md` 作为 `AGENT_FILE` 注入 prompt，而不是解析 TOML。 
- `precision-tuning-discovery` 在 TOML 中重命名为 `cann-debug-agent`；`.md` 文件名保持不变，以避免打破 standalone 默认路径。

> **Codex review note:** 将“`.codex/agents/*.toml` 是唯一 source-of-truth”改为“双载体，TOML 供 Codex，Markdown 供 standalone”。`run_precision_tuning.sh` 直接把 `AGENT_FILE="agents/precision-tuning-discovery.md"` 和该文件路径注入 `PROMPT_TEMPLATE`，说明 `.md` 不是可删的历史包袱，而是当前 standalone 输入契约的一部分。证据：`utils/run_precision_tuning.sh:27, 34-52`。

## 4. TOML Schema（两个 agent 通用写法）

采用“最小可验证字段集”：

```toml
name = "ascend-kernel-developer"
description = "Ascend 算子端到端生成 agent。"
developer_instructions = """
<系统提示正文>
"""
```

可选字段如 `model`、`model_reasoning_effort`、`sandbox_mode` 仅在确有必要时再写入；本设计默认省略它们，并把“省略后继承当前 session 配置”视为需要烟测确认的运行时假设，而不是文档中的无条件真理。

字段对齐建议：

| 原 md front-matter 字段 | TOML 去向 |
|---|---|
| `name` | `name` |
| `description` | `description` |
| `temperature` | 删除，不迁移 |
| `tools` | 删除，不迁移 |
| `skills` | 不单独声明，直接写入 `developer_instructions` |
| `argument-hint` | 融入 `description` 或正文开头 |

> **Codex review note:** 将 draft 中“官方 schema 只有三个必填字段”修正为“最小可验证字段集”。参考文件没有给出 custom-agent TOML 正式 schema；能直接观察到的是会话级 `model` / `model_reasoning_effort` / sandbox 都是 CLI 或全局配置层面的能力，而不是这些 agent 文档本身的既有字段。与此同时，仓库内现有 agent/skill 文档都是 Markdown front-matter 形式。为避免把未验证事实写成规范，这里把继承语义降级为待烟测假设。证据：`agents/precision-tuning-discovery.md:1-15`、`skills/ascendc/precision-tuning/SKILL.md:1-14`、`utils/run_precision_tuning.sh:34-52`。

> **Codex review note:** `model` / `model_reasoning_effort` / `sandbox_mode` 可以先省略，但“省略等于完全继承 session defaults”必须通过实际加载验证后才能作为验收结论，因为参考文件里没有任何一处消费这些 TOML 字段。证据：参考文件中唯一真实被消费的启动接口是 `run_precision_tuning.sh` 的 prompt 注入，而不是 TOML 解析。见 `utils/run_precision_tuning.sh:34-52`。

## 5. Phase 4 线性化改造（ascend-kernel-developer）

### 5.1 当前 Phase 4（循环版）

当前实现是 parent 自己维护 `ac_iteration`、`ac_verifier_error`、`ac_conductor_suggestion`，在 4.1→4.4 闭环最多重试 3 次。

> **Codex review note:** 这一节保留为现状基线，直接对应现有 Phase 4 的状态变量与 `while ac_iteration < max_ac_iterations` 伪代码。证据：`agents/ascend-kernel-developer.md:313-392`。

### 5.2 新版 Phase 4（线性版 + spawn）

```text
Phase 4.0: AscendC 转译，产出 kernel/
Phase 4.1: 生成 model_new_ascendc.py
Phase 4.2: validate_ascendc_impl.py
    fail  -> Phase 7（不 spawn）
Phase 4.3: evaluate_ascendc.sh
    all pass                      -> Phase 5
    pure numerical failure        -> Phase 4.4
    any build/import/mixed fail   -> Phase 7
Phase 4.4: 写 parent_handoff.json，spawn cann-debug-agent
    PASS            -> Phase 5
    FAIL_PRECISION  -> Phase 7
    CHEAT / ABORT   -> Phase 7
```

> **Codex review note:** “mixed fail” 被单独加进边界条件。原因是精度调优 agent 的前提是“编译通过、运行但精度不通过”，若同一轮输出同时含 Build/Import 和 Numerical 迹象，则不满足 clean numerical prerequisite，不能安全下放。证据：`agents/precision-tuning-discovery.md:26-39`、`skills/ascendc/precision-tuning/SKILL.md:26-41`。

### 5.3 失败类型判别（4.3 之后）

父 agent 必须按“先排除 build/import，再判 numerical”的顺序分类：

| 信号 | 分类 | 路由 |
|---|---|---|
| `ModuleNotFoundError`、`ImportError`、`undefined symbol`、`cannot open shared object file`、`No such file or directory`、`fatal error:`、`error:`、`c++:`、`ld:`、`collect2:`、`undefined reference`、`SyntaxError`、`NameError`、`AttributeError` | Build/Import | Phase 7 |
| `Tensors are not close`、`max_abs_diff`、`mismatch_ratio`、`Numerical mismatch`、存在 per-case diff 统计 | Numerical | 候选进入 Phase 4.4 |
| 两类都命中 | Mixed | Phase 7 |
| 两类都未命中 | Unknown | Phase 7 |

规则：
- 只有“无 Build/Import 信号且存在 Numerical 信号”时才允许 spawn。
- “部分 case 通过、部分 case 数值失败”仍应 spawn，因为 forensics/gate 本来就基于 mismatch 统计、worst elements、history trend 处理部分失败，而不是要求全 case 一致失败。
- “部分 case 数值失败，但另一些 case 编译/导入失败”不能 spawn，按 Mixed 处理。

> **Codex review note:** draft 的 Build/Import 关键词集不完整，至少遗漏了 `ImportError`、动态库加载失败、链接器报错和 Python wrapper 级符号错误。现有 Gate-X 明确检查 `pybind11.cpp`、`PYBIND11_MODULE`、非 pybind `.cpp` 以及 import 名一致性，说明 import/build 边界并不只是一两种字符串。证据：`skills/ascendc/precision-tuning/scripts/precision_gate.py:194-231`。

> **Codex review note:** “AST 退化失败不 spawn”维持不变，但这里补上了理由。精度调优 agent 的可修改范围被限制为 `{task_dir}/kernel/`，显式禁止改 `model_new_ascendc.py` / `model_new_tilelang.py` / `model.py`；而 AST 退化本质上针对 wrapper 退化，子 agent 无法在不越界的前提下修复它。证据：`agents/precision-tuning-discovery.md:26-39, 100-136`、`skills/ascendc/precision-tuning/SKILL.md:31-41, 51-84`。

> **Codex review note:** “部分 case 失败是否 spawn”在 draft 中缺位，这里明确为“只要失败纯数值，就 spawn”。`precision_forensics.py` 输出按 case 聚合 diff、worst elements、tail/body mismatch 和 history trend，本来就是为部分失败设计的；不需要等到全部 case 都数值失败。证据：`skills/ascendc/precision-tuning/scripts/precision_forensics.py:10-21`、`skills/ascendc/precision-tuning/SKILL.md:150-243`。

## 6. `cann-debug-agent` 的首次输入契约

### 6.1 背景

subagent 的前提仍是：
- `model.py`、`model_new_ascendc.py`、`kernel/` 已存在；
- evaluate 已经暴露“纯 Numerical 失败”；
- parent 额外掌握一份生成阶段上下文，可作为首轮分析的加速信息。

> **Codex review note:** 这里把 prerequisite 收窄为“纯 Numerical 失败”，与 discovery/skill 文档保持一致，避免 mixed/build 情况混入。证据：`agents/precision-tuning-discovery.md:26-39`、`skills/ascendc/precision-tuning/SKILL.md:26-41`。

### 6.2 Handoff Artifact

Parent 在 spawn 前写入：

路径：`{task_dir}/precision_tuning/parent_handoff.json`

建议 schema：

```json
{
  "source": "ascend-kernel-developer@phase4",
  "spawned_at": "<ISO8601>",
  "task_name": "<task basename>",
  "task_dir": "<absolute path>",
  "op_name": "<operator name>",
  "npu": 0,
  "failure_class": "Numerical",
  "failure_policy": "pure_numerical_only",
  "evaluate_excerpt": "<关键失败片段，建议 80-120 行，优先保留失败 case 与 diff 统计>",
  "phase3_design_summary": {
    "design_dir": "{task_dir}/design/tile_level",
    "key_choices": ["..."]
  },
  "phase4_translation_summary": {
    "kernel_files": ["..."],
    "api_usage": ["..."],
    "known_platform_limits": ["..."]
  },
  "wrapper_baseline": {
    "model_new_ascendc_sha256": "<optional>",
    "model_new_tilelang_sha256": "<optional>"
  }
}
```

字段约定：
- `task_name` 与 `npu` 保留，因为 standalone prompt 模板就是这样传参。
- `op_name` 也保留，不从 `task_name` 推导；现有 Gate 调用同时需要 `--op-name` 与 `--task-name`。
- `evaluate_excerpt` 不作为 Gate 输入，只作为首轮上下文加速；若日志较长，应优先保留失败 case、diff 统计和最终报错，而不是机械 tail 80 行。
- `wrapper_baseline` 为可选字段；若保留，应由 parent 在 spawn 前计算，不依赖 `run_precision_tuning.sh` 的 bench 机制。

> **Codex review note:** `task_name` 不是冗余字段，`run_precision_tuning.sh` 的 prompt 模板显式传 `task_name`、`task_dir`、`npu`，而 Step 1 / Gate 命令也显式区分 `--task-name` 和 `--op-name`。因此这里保留双字段最稳妥。证据：`utils/run_precision_tuning.sh:36-52`、`skills/ascendc/precision-tuning/SKILL.md:96-105`。

> **Codex review note:** `evaluate_excerpt` 从“固定 80 行”放宽为“80-120 行或等价关键片段”。因为 subagent 的真实确定性输入是重新跑 `precision_forensics.py`，日志摘录只是补充上下文；如果机械只留 tail 80 行，容易丢掉失败 case 名和首个关键 diff。证据：`skills/ascendc/precision-tuning/SKILL.md:150-243`、`skills/ascendc/precision-tuning/scripts/precision_forensics.py:17-21`。

> **Codex review note:** draft 把 `anti_cheat_baseline` 写成像是 bench 自带字段，这不准确。现有 bench 哈希/AST 检测只在 standalone prompt 中被说明，并不由 `precision_gate.py` 自动生成；parent-spawn 若想带 baseline，只能由 parent 自己在 spawn 前计算，不能指望 `.bench_baseline/` 机制自然出现。证据：`utils/run_precision_tuning.sh:44-47`、`skills/ascendc/precision-tuning/scripts/precision_gate.py:51-127`。

### 6.3 Subagent 的首次输入产物变更

在 `cann-debug-agent` 的 Step 2.1 前增加一个纯增量 pre-hook：

```text
Step 2.1 pre-hook:
1. 若 {task_dir}/precision_tuning/parent_handoff.json 存在：
   - 读取并校验 failure_class == "Numerical"
   - 将其中的 design/translation/evaluate 摘要写入 [PRIOR_TRACE_CONTEXT]
2. 若不存在：
   - 保持原逻辑；attempt == 0 时可选读取 {task_dir}/trace.md
3. 然后再执行原有 Step 1 forensics 与 Gate-F
```

该 hook 不改变 Gate-A 必填 section，`[PRIOR_TRACE_CONTEXT]` 仍为可选补充段。

> **Codex review note:** 这里把 hook 明确为“读取额外上下文，不替代 forensics”。Step 2.1 现有硬要求仍是先读 `forensics_report_{attempt}.json`，再产出 `[FORENSICS_SUMMARY]`，Gate-A 也只校验这些核心 sections；因此 handoff 只能是 additive，不能越过现有 Gate 链。证据：`skills/ascendc/precision-tuning/SKILL.md:150-243`、`skills/ascendc/precision-tuning/scripts/precision_gate.py:134-188`。

### 6.4 Subagent 返回值约定

推荐保留一个 adapter 文件：

路径：`{task_dir}/precision_tuning/subagent_result.json`

```json
{
  "status": "PASS | FAIL_PRECISION | CHEAT | ABORT",
  "attempts_used": 0,
  "final_max_abs_diff": 0.0,
  "modified_files": ["kernel/xxx.cpp"],
  "reason": "<一句话>",
  "evidence": {
    "round_summary": "precision_tuning/round_summary_0.json",
    "validation": "precision_tuning/validation_result_attempt_0.json",
    "tuning_directions": "precision_tuning/tuning_directions.json"
  }
}
```

约束：
- `subagent_result.json` 由 subagent 在最终收尾时写入。
- `precision_gate.py` 不负责写这个文件；Gate 继续只写既有的 `baseline_state.json`、`round_summary_{N}.json`、`tuning_directions.json` 等产物。
- `attempts_used` 必须与 Gate 的真实上限一致，不得写死为 3；当前脚本上限是 2。

> **Codex review note:** draft 没有厘清 `subagent_result.json` 的作者。现有 Gate 只会写 `baseline_state.json`、`round_summary_{attempt}.json`、`tuning_directions.json` 等文件，仓库内不存在任何会生成 `subagent_result.json` 的脚本，所以这个文件若保留，只能由 subagent 自己在末尾基于 Gate 结果写一个 adapter。证据：`skills/ascendc/precision-tuning/scripts/precision_gate.py:80-127, 423-573`。

> **Codex review note:** `attempts_used: 3` 被修正，因为 `precision_gate.py` 当前 `MAX_ATTEMPTS = 2`。设计文档必须与实际 Gate 上限一致，否则 parent 的状态判断会漂移。证据：`skills/ascendc/precision-tuning/scripts/precision_gate.py:35-36`。

## 7. Codex 项目启动默认行为

若目标是“用户在仓库根目录直接输入 `codex`，并尽可能无缝进入 `ascend-kernel-developer`”，三种路线中以 **A: 覆盖 `default`** 最符合这个目标。

结论：
- **首选 A**：若你追求确定性自动进入仓库主 agent，就覆盖 `.codex/agents/default.toml`，在默认 agent 中直接承载 `ascend-kernel-developer` 的行为或立即委派给它。
- **备选 C**：若你更重视不污染默认 agent，则用 `.codex/AGENTS.md` 做语义引导，但这不是“seamlessly enters”的最强保证。
- **不推荐 B 作为主路径**：当前本地 `codex --help` 未暴露 `--agent` 选项，因此把 B 写成默认启动方案风险过高。

> **Codex review note:** draft 推荐 C，但题目要求是“user types `codex` in repo root and seamlessly enters ascend-kernel-developer`”。在这个目标下，只有 A 是确定性最强的方案；C 依赖默认 agent 读懂 AGENTS.md 并再做语义路由，存在一步间接。证据：draft 自身对 A/B/C 的定义见 `.planning/design_draft.md:218-226`；参考文件里也没有任何 `--agent` 调用痕迹，现有 standalone 方案完全通过 prompt 注入实现。见 `utils/run_precision_tuning.sh:27, 34-52`。

## 8. Phase 4 删除/替换片段（给实现方参考的 diff 草图）

- 删除 `ac_iteration` / `max_ac_iterations` / `ac_history_attempts` / `ac_conductor_suggestion` 等 Phase 4 自循环状态。
- 删除 Phase 4 内部 Conductor 修复建议格式。
- 保留错误分类词汇，但仅作为 parent 路由和 trace 记录，不再驱动 parent 自修复。
- 新增 `Phase 4.4 - Spawn cann-debug-agent`，写入 `parent_handoff.json`，等待 `subagent_result.json`。
- 新增 mixed-failure 路由与 parent-side anti-cheat 校验要求。

> **Codex review note:** 这里补入了 parent-side anti-cheat 校验，因为 draft 漏掉了 parent-spawn 路径没有 standalone bench 兜底这一点。standalone prompt 明确说 bench 会做 wrapper 哈希 + AST 退化检测，但 parent-spawn 并没有自然继承这层外部检查；若 parent 想消费 `PASS`，就应在收尾时补做同等检查。证据：`utils/run_precision_tuning.sh:44-47`、`agents/precision-tuning-discovery.md:119-136`。

## 9. Parent Spawn 口令模板

写在 `ascend-kernel-developer.toml` 的 Phase 4.4 中：

```text
当且仅当 Phase 4.3 被判定为 pure Numerical failure 时：
1. 写 {task_dir}/precision_tuning/parent_handoff.json
2. spawn cann-debug-agent，明确输入契约为该 handoff 文件
3. 等待子 agent 结束
4. 读取 {task_dir}/precision_tuning/subagent_result.json
5. 若 status == PASS，再执行一次 parent-side wrapper hash/AST anti-cheat 复核；通过后进入 Phase 5
6. 否则进入 Phase 7
```

> **Codex review note:** 新增了“PASS 后 parent 复核 anti-cheat”这一步。否则 parent-spawn 路径里 `CHEAT` 只能靠子 agent 自觉声明，而没有 standalone bench 那样的客观兜底。证据：`utils/run_precision_tuning.sh:44-47`、`agents/precision-tuning-discovery.md:119-136`。

## 10. 风险与未决问题

| 风险 | 说明 | 缓解 |
|---|---|---|
| custom-agent TOML 字段假设与实际 loader 不一致 | 参考文件未给正式 schema | 增加 Codex 运行时烟测，必要时退回最小字段集 |
| evaluate 分类误判 | 仅凭关键字容易把 mixed fail 当 numerical | 采用“先排除 build/import，再判 numerical”的保守策略 |
| parent-spawn 缺少 bench 级 anti-cheat | standalone 有外部 hash/AST 检测，parent-spawn 没有 | parent 在消费 `PASS` 前自行复核 wrapper hash + AST |
| `.md` 与 `.toml` 语义漂移 | standalone 仍读 `.md` | 采用方案 Y，同步补 pre-hook |
| 子 agent 返回值与 Gate 真实状态漂移 | 手写 `subagent_result.json` 可能撒谎 | 返回值必须引用 `round_summary` / `validation_result` / `tuning_directions` |

> **Codex review note:** 风险表新增了“parent-spawn 缺少 bench 级 anti-cheat”，这是 draft 完全遗漏的维度。standalone 路径的反作弊说明只出现在调度脚本 prompt 里，不在 Gate 脚本里。证据：`utils/run_precision_tuning.sh:44-47`、`skills/ascendc/precision-tuning/scripts/precision_gate.py:51-127`。

### 10.1 Standalone 路径兼容策略

选择 **方案 Y**：同步更新 `.toml` 和 `agents/precision-tuning-discovery.md`，把 handoff pre-hook 写成“存在即读，不存在即跳过”的纯增量逻辑。

理由：
- `run_precision_tuning.sh` 不解析 agent 内容，只把 `.md` 路径注入 prompt；因此只要 `.md` 中新增的是可选 hook，就不会对脚本层产生副作用。
- prompt 模板只要求“先 Read agent 规范文件并按规范执行”，不会与 `.md` 具体章节结构耦合。

> **Codex review note:** 这里把“`.md` sync 零副作用”说清楚了。脚本只消费文件路径和自然语言规范，不读取具体段落字段，因此同步修改 `.md` 的风险是语义层面的，不是脚本接口层面的。证据：`utils/run_precision_tuning.sh:34-52`。

## 11. 验收标准

1. `.codex/agents/*.toml` 能被 `tomllib` 解析。
2. 运行时烟测至少包含一项 Codex loader 检查，而不只做静态 TOML parse。
3. `ascend-kernel-developer` 的 Phase 4 已无 `while ac_iteration` 闭环。
4. `cann-debug-agent` 的 Step 2.1 增加了 optional handoff pre-hook，且 forensics/Gate 主链不变。
5. mixed-failure、AST fail、pure numerical fail 三种边界条件在父 agent 文档中都有明确路由。
6. `.md` 与 `.toml` 都同步了 handoff pre-hook。
7. `subagent_result.json` 的设计与 Gate 真正产物对齐，并将 `attempts_used` 约束为实际脚本上限。
8. parent-spawn 路径在消费 `PASS` 前有 anti-cheat 复核。

建议烟测：
- 静态：`python -c "import tomllib, pathlib; [tomllib.loads(path.read_text()) for path in pathlib.Path('.codex/agents').glob('*.toml')]"`。
- 运行时：优先使用目标版本 Codex 提供的 agent/loader 检查命令；若无专门命令，则至少执行一次 repo-root 下的 no-op 启动烟测，确认 `.codex` 配置不会触发 loader 解析错误。

> **Codex review note:** draft 的验收缺少运行时加载烟测，而且还写了 `git commit` / 新建 `task_plan.md`，这超出了本次任务范围。这里补上 loader smoke test，同时去掉与本次 review 输出无关的提交和后续计划文件要求。证据：`.planning/design_draft.md:285-298`。

## 12. 下一步

1. 先落文档与 agent 配置，不改脚本。
2. 做静态 + 运行时烟测，确认 custom-agent TOML 能被目标 Codex 版本接受。
3. 再进入实现计划，把 parent Phase 4、subagent pre-hook、parent-side anti-cheat 复核拆成独立任务。

> **Codex review note:** 将“直接 writing-plans + 并行实现”改成“先做 loader 烟测再拆任务”，因为 §4 的 schema 仍有运行时不确定性，先验证再实现更稳妥。证据：参考文件均未提供 custom-agent TOML 的正式 schema，而现有可执行路径全部仍是 Markdown/prompt 驱动。见 `utils/run_precision_tuning.sh:27, 34-52`。

## 13. Codex Review Summary

- 将 §4 从“官方 schema 结论”改为“最小可验证字段集 + 运行时烟测”，避免把未验证的 TOML 继承语义写成事实。
- 收紧了 Phase 4 路由：只有 pure numerical failure 才 spawn；mixed/build/import 一律不下放。
- 明确 AST 退化失败不应 spawn，因为精度调优 agent 被禁止修改 wrapper，只能改 `kernel/`。
- 扩充了 Build/Import 关键词集合，并补上“部分数值失败可 spawn、混合失败不可 spawn”的政策。
- 调整了 handoff schema：`task_name` / `op_name` 双字段保留，`wrapper_baseline` 改为 parent 选填而非 bench 自动产物。
- 认定 `subagent_result.json` 必须由 subagent 自己写，Gate 只继续写既有 `round_summary` / `tuning_directions` 等文件；同时把 `attempts_used` 修正为与 `MAX_ATTEMPTS = 2` 一致。
- 将默认启动建议从 C 改为 A，因为目标是“用户只输入 `codex` 即无缝进入主 agent”，而这比 AGENTS.md 语义引导更确定。
- 补入 parent-spawn 路径缺失的 anti-cheat 复核与 Codex loader smoke test，这两项是 draft 漏掉的关键验收维度。
