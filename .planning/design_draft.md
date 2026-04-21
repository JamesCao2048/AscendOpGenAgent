# Phase 4 Subagent Delegation — Design Draft

> **Status:** Draft produced by ascend-kernel-developer (Claude Code session). Will be reviewed by Codex and finalized as `design.md` before task planning.

## 1. 目标

1. 把两个 Markdown 形式的 agent 定义 (`agents/ascend-kernel-developer.md` & `agents/ascendc-debug-discovery.md`) 迁移到 Codex 原生 TOML 形式 (`.codex/agents/`)，让 Codex 在本仓库启动时自动注册这两个 agent。
2. 重构 `ascend-kernel-developer` 的 Phase 4：**取消"转译→退化预检查→功能验证→Conductor"的三轮内循环**；Phase 4 变成一次性的线性流程，当 4.3 功能验证失败时，4.4 直接 `spawn` 新注册的 `ascendc-debug-discovery-agent` subagent。
3. 把精度修复循环 (取证→分析→修复→Gate 验证) 全部内聚到 `ascendc-debug-discovery-agent` 内部。
4. 为 `ascendc-debug-discovery-agent` 设计清晰的"首次输入契约"，确保它从 **parent-spawn** 被调起时拿到的上下文与它从 **standalone 启动**（`run_ascendc_debug.sh`）时等效。

## 2. 非目标（YAGNI）

- 不改 `utils/run_ascendc_debug.sh` 的行为；standalone 路径继续工作。
- 不改 `skills/ascendc/ascendc-debug/` 下的脚本契约（forensics/gate）。
- 不改 Phase 3 (TileLang) 的迭代逻辑。
- 不引入 `codex-subagents-mcp` 等第三方 MCP；只用 Codex 原生 TOML + 自然语言 delegate。

---

## 3. 目录与文件

```
AscendOpGenAgent/
├── .codex/
│   ├── AGENTS.md                              # 项目级 Codex 入口说明（见 §7）
│   └── agents/
│       ├── ascend-kernel-developer.toml       # 来自 agents/ascend-kernel-developer.md
│       └── ascendc-debug-discovery-agent.toml              # 来自 agents/ascendc-debug-discovery.md（重命名）
├── agents/
│   ├── ascend-kernel-developer.md             # 保留作人类可读文档（不再被 Codex 直接吃）
│   ├── ascendc-debug-discovery.md          # 保留作历史/standalone 路径
│   └── ... (其它不动)
└── ...
```

**设计决策**：
- **双持方案**：`.codex/agents/*.toml` 是 Codex 的 source-of-truth；`agents/*.md` 保留给人看、也保留给 `run_ascendc_debug.sh` 的 `--agent` 继续引用。两者内容在本次修改前后**一次性同步**，此后除非显式需要，不强行绑定双向同步。
- **subagent 改名**：`ascendc-debug-discovery` → `ascendc-debug-discovery-agent`。`ascendc-debug-discovery-agent.toml` 的 `name` 字段是 `ascendc-debug-discovery-agent`，这是 Codex 运行时识别/`spawn` 用的唯一标识。

---

## 4. TOML Schema（两个 agent 通用写法）

按照 OpenAI Codex 官方 schema，只用三个必填字段 + 受控的可选字段，避免因字段名不兼容导致 Codex 拒绝加载。

```toml
name = "ascend-kernel-developer"
description = "Ascend 算子端到端生成 agent：PyTorch Model → TileLang 设计 → AscendC kernel 实现 → 性能分析 → trace 记录。用于所有 AscendC 算子 codegen 任务。"

# model / model_reasoning_effort / sandbox_mode / mcp_servers / skills.config 全部省略
# → 由启动 session 继承（保持当前 codex 默认行为，不绑死模型）

developer_instructions = """
<原 md 文件 front-matter 以下的全部 System Prompt 内容，按 §5 的改造落地>
"""
```

**字段对齐说明**：

| 原 md front-matter 字段 | 去向 |
|---|---|
| `name` | TOML `name` |
| `description` | TOML `description`（微调，突出"何时该挑这个 agent"） |
| `temperature: 0.1` | 丢弃 — Codex TOML 无此字段，让 session 继承 |
| `tools: {write/edit/bash/skill/read: true}` | 丢弃 — Codex 通过 sandbox + 内建工具统一管理，无此字段 |
| `skills: [...]` | 丢弃 — md-era 的 "skill 清单" 概念，Codex 没有；skill 路径直接写在 `developer_instructions` 里 |
| `argument-hint` | 融入 `description` 或 `developer_instructions` 开头 |

---

## 5. Phase 4 线性化改造（ascend-kernel-developer）

### 5.1 当前 Phase 4（循环版）

```
Phase 4.0 (首轮): ascendc-translator 转译 → kernel/
while ac_iteration < 3:
    4.1 generate model_new_ascendc.py
    4.2 validate_ascendc_impl.py  (AST 退化检查)
        fail → goto 4.4
    4.3 evaluate_ascendc.sh       (功能验证)
        pass → break into Phase 5
        fail → goto 4.4
    4.4 Conductor (A/B/C 分类)
        A → 改 kernel/ 或 model_new_ascendc.py，iter++
        B → terminate
        C → terminate
```

### 5.2 新版 Phase 4（线性版 + spawn）

```
Phase 4.0 (一次性): ascendc-translator 转译  →  {task_dir}/kernel/
Phase 4.1 (一次性): 生成 model_new_ascendc.py
Phase 4.2 (一次性): validate_ascendc_impl.py
    exit != 0 → 记录 AST 退化失败，跳到 Phase 7（不 spawn debug agent）
Phase 4.3 (一次性): evaluate_ascendc.sh
    exit == 0 (全部 case 通过)     → 直接进入 Phase 5
    exit != 0 && 属 Build/Import  → 记录失败，跳到 Phase 7（不 spawn）
    exit != 0 && 属 Numerical     → 进入 Phase 4.4
Phase 4.4 (spawn): 写 handoff artifact，spawn ascendc-debug-discovery-agent
    subagent 返回 status ∈ {PASS, FAIL_PRECISION, CHEAT, ABORT}
    PASS               → 进入 Phase 5
    FAIL_PRECISION     → 跳到 Phase 7，记录最终失败
    CHEAT / ABORT      → 跳到 Phase 7，标记异常
```

### 5.3 失败类型判别（4.3 之后）

`evaluate_ascendc.sh` 退出码不足以区分 Build vs Numerical，因此 parent agent 需扫描其 stdout/stderr：

| 关键字（stderr/stdout 中出现）| 分类 | 路由 |
|---|---|---|
| `error: ... compilation`、`c++: error`、`nvcc error`、`ModuleNotFoundError: No module named '_xxx_ext'`、`undefined symbol` | Build/Import | Phase 7（parent 不尝试修） |
| `AssertionError: Tensors are not close`、`max_abs_diff`、`mismatch ratio`、`Numerical mismatch` | Numerical | Phase 4.4 spawn |
| 两者皆不匹配 | Unknown | Phase 7 + trace.md 标 `A-Unknown-Phase4Fail` |

> **为什么不让 parent 尝试修 AST/Build 失败？** 因为 parent 原本靠 3 轮 Conductor 才能修；改线性后没有迭代配额，继续让 parent 单轮修复正确率不够，不如记失败由人看 trace。Phase 4.4 仅接数值类失败——这也正是 `ascendc-debug-discovery-agent` 的 prerequisite。

---

## 6. `ascendc-debug-discovery-agent` 的首次输入契约（**核心改动**）

### 6.1 背景

原 `ascendc-debug-discovery` agent 设定 prerequisite：
> task 目录下已有 model.py、model_new_ascendc.py、kernel/ 目录，
> 且 evaluate_ascendc.sh 已报告 Numerical 失败（非 Build/Import 失败）。

在 standalone 启动（`run_ascendc_debug.sh`）场景，这些条件是"历史遗留状态"——某次跑完 ascend-kernel-developer 留下的文件 + 人为判断"数值失败"。

在 **parent-spawn** 场景，这些条件变成"刚刚产生的状态"——parent 的 Phase 4.1/4.2/4.3 刚写完 kernel/ + model_new_ascendc.py，刚跑完 evaluate_ascendc.sh 拿到失败 log。

两者对 subagent 而言都是"有效前提"，但 parent-spawn 场景**多出一份即时上下文**（parent 的 Phase 3 设计思路、Phase 4 转译细节、evaluate 报错原文），不用则浪费。

### 6.2 Handoff Artifact

Parent 在 4.4 spawn 前**写一个结构化文件**：

**路径**：`{task_dir}/precision_tuning/parent_handoff.json`（子目录 `precision_tuning/` 在 subagent Step 0 之前即已存在，只是 subagent 会补上 `history/` 等子目录）

**Schema**：

```json
{
  "source": "ascend-kernel-developer@phase4",
  "spawned_at": "<ISO8601 时间戳>",
  "task_name": "<op_name 或 task 目录 basename，与 run_ascendc_debug.sh 对齐>",
  "task_dir": "<绝对路径>",
  "op_name": "<算子名>",
  "npu": 0,
  "failure_class": "Numerical",
  "evaluate_excerpt": "<evaluate_ascendc.sh 输出的关键 tail，不超过 80 行>",
  "phase3_design_summary": {
    "design_dir": "{task_dir}/design/tile_level",
    "key_choices": [
      "<一句话：block/tile 切分策略>",
      "<一句话：核心 primitives 使用>"
    ]
  },
  "phase4_translation_summary": {
    "kernel_files": ["<kernel/*.cpp / *.h 列表>"],
    "api_usage": ["<DataCopy/DataCopyPad/ReduceMax/SyncAll 等在 kernel/ 中实际用到的主力 API>"],
    "known_platform_limits": ["<当前平台 API/dtype 限制，若 parent 在生成阶段已发现>"]
  },
  "anti_cheat_baseline": {
    "model_new_ascendc_sha256": "<parent 生成结束时的 hash>",
    "model_new_tilelang_sha256": "<若存在>"
  }
}
```

**字段用途**：
- `task_name` / `task_dir` / `op_name` / `npu` — 等价 `run_ascendc_debug.sh` 的 `--task-dirs` + `--npus`。
- `failure_class` — 固定 `Numerical`（parent 已过滤），subagent 无需再判别。
- `evaluate_excerpt` — 取代原 standalone 场景下 subagent 要去跑 forensics 前依赖的"上次 evaluate 报错"；subagent 的 `[PRIOR_TRACE_CONTEXT]` section 可直接引用。
- `phase3_design_summary` / `phase4_translation_summary` — 等价原 standalone 路径的 `trace.md` 作用（但 trace.md 是 Phase 7 才写，parent-spawn 时还没写，所以用 handoff 代替）。
- `anti_cheat_baseline` — 让 subagent 在结束时把自己的改动和 baseline 对比，提前自检是否违反反作弊。

### 6.3 Subagent 的"首次输入产物"变更

在 `ascendc-debug-discovery-agent.toml` 的 `developer_instructions` 里，Step 2.1 的流程增加一个**前置 hook**：

```
Step 2.1 (修订):
  1. 先检查 {task_dir}/precision_tuning/parent_handoff.json 是否存在
     - 存在 → 读入，作为 [PRIOR_TRACE_CONTEXT] section 的主要内容源；
              同时校验 failure_class == "Numerical"，否则 abort。
     - 不存在 → 走原 standalone 路径：可选读取 {task_dir}/trace.md（若存在）。
  2. 跑 precision_forensics.py（不变）
  3. 跑 precision_gate.py --step forensics（不变）
  4. 生成 [FORENSICS_SUMMARY] + [PRIOR_TRACE_CONTEXT] section（不变）
```

**Gate-A 契约不变**：`[REFERENCE_IMPL_SPEC]`、`[FORENSICS_SUMMARY]`、`[COMPUTATION_DECOMPOSITION]`、`[KERNEL_STEP_TRACE]` 仍必填。`[PRIOR_TRACE_CONTEXT]` 仍是可选段，但 parent-spawn 场景下强烈建议写入（handoff 信息不应丢）。

### 6.4 Subagent 返回值约定

Subagent 结束时在 parent 视图里留一条**结构化 summary**（通过 stdout 最后一段 + `{task_dir}/precision_tuning/subagent_result.json`）：

```json
{
  "status": "PASS | FAIL_PRECISION | CHEAT | ABORT",
  "final_max_abs_diff": 0.0,
  "attempts_used": 3,
  "modified_files": ["kernel/xxx.cpp"],
  "reason": "<一句话>"
}
```

Parent 在 4.4 结束后读 `subagent_result.json` 决定去 Phase 5 还是 Phase 7。

---

## 7. Codex 项目启动默认行为

Codex 不支持"启动自动激活某个 custom agent"的硬配置（只有 `default`/`worker`/`explorer` 是内置可被覆盖的名字）。可用的三条路线：

| 路线 | 做法 | 取舍 |
|---|---|---|
| **A** 覆盖内置 `default` | `.codex/agents/default.toml` 的 `name = "default"`，描述里说 "本仓库专属：任何算子生成任务转交 `ascend-kernel-developer` / 任何精度调优任务转交 `ascendc-debug-discovery-agent`" | 用户敲 `codex "生成..."` 立即生效；但 `default` 名字被覆盖后其它非业务任务也会被这段描述影响 |
| **B** 文档化 `--agent` 调用 | 只写 `ascend-kernel-developer.toml` + `ascendc-debug-discovery-agent.toml`，在 `.codex/AGENTS.md` 告诉人类/自动化"用 `codex --agent ascend-kernel-developer "..."` 启动" | 干净、不污染 default；代价是每次多敲一个 flag |
| **C** AGENTS.md 引导式 | 只写两个 agent TOML，`.codex/AGENTS.md` 写明"本仓库主任务 = AscendC 算子生成；遇到此类任务请立即 spawn `ascend-kernel-developer`" | Codex 的 default agent 会读 AGENTS.md，它会按语义路由；不用记 CLI flag，但有一步间接 |

**推荐 C**：和 Codex "AGENTS.md 是主 prompt 补丁"的设计对齐，零副作用，和 default 解耦；同时文档里附 A 的 fallback（给真的很在意每次少一跳 delegate 的用户）。

---

## 8. Phase 4 删除/替换片段（给实现方参考的 diff 草图）

- **删除**：4.1-4.4 的 `while ac_iteration < max_ac_iterations:` 伪代码块、`max_ac_iterations = 3`、`ac_history_attempts`、`ac_verifier_error`、`ac_conductor_suggestion` 等变量、"Conductor 修复建议格式" section 中 Phase 4 专属部分、"AscendC 退化子类型"/"A 类错误详细分类（AscendC）"/"B 类错误详细分类" 若仅用于循环的 conductor 可整段压缩（但保留给 trace 分类用）。
- **新增**：`Phase 4.4 - Spawn ascendc-debug-discovery-agent` 小节，含 handoff 写入规范（引用 §6.2 schema）、spawn 口令的自然语言模板（见 §9）、返回值消费逻辑。
- **修改**："约束" 表里 "Phase 4 最大迭代 3 次" 一行删除，替换为 "Phase 4 线性执行 + 数值失败委派 ascendc-debug-discovery-agent subagent"。
- **修改**："错误处理" 表里 Phase 4 的三行压成两行（AST 退化 → Phase 7；数值失败 → spawn；Build/Import → Phase 7）。

## 9. Parent Spawn 口令模板

写在 `ascend-kernel-developer.toml` 的 `developer_instructions` Phase 4.4 段：

```
Phase 4.4: 委派精度调优到 ascendc-debug-discovery-agent

当且仅当 Phase 4.3 判定为 Numerical 失败时执行本步骤。

1. 写 handoff 文件到 {task_dir}/precision_tuning/parent_handoff.json，字段见 §规范。
2. Spawn 子任务：
   - subagent 名: ascendc-debug-discovery-agent
   - 任务描述: "按 ascendc-debug-discovery-agent 规范对 {task_name} 做精度调优。
                输入契约: {task_dir}/precision_tuning/parent_handoff.json。
                目标: 让 evaluate_ascendc.sh 全部 case 通过。"
3. 等待 subagent 结束。
4. 读取 {task_dir}/precision_tuning/subagent_result.json，按 status 路由。
```

Codex 原生规则：父 agent 在 `developer_instructions` 里**自然语言点名** `ascendc-debug-discovery-agent`，runtime 识别后自动 spawn。无额外 CLI 语法。

---

## 10. 风险与未决问题

| 风险 | 说明 | 缓解 |
|---|---|---|
| `agents.max_depth` 默认 1 | parent 里 spawn 一次 OK；若 subagent 自己再 spawn 会被拦 | 保持默认；`ascendc-debug-discovery-agent` 不应再嵌套 spawn |
| handoff JSON 漂移 | 如 subagent 改 schema 导致 parent 写错 | 在 design.md 定稿后把 schema 冻结到 `.codex/agents/ascendc-debug-discovery-agent.toml` 的 `developer_instructions` 里 |
| evaluate 输出分类靠关键字匹配不稳 | Build vs Numerical 的判别可能漏 | 在 parent agent 里先判最保守的 Numerical signal（出现 "max_abs_diff" 或 "Tensors are not close"），否则一律 Phase 7 |
| `default` agent 是否要改 | 取决于 §7 选择的路线 | 默认路线 C，不改 default |
| Standalone 路径回归 | `run_ascendc_debug.sh` 仍指向 `agents/ascendc-debug-discovery.md` | 保留 .md 文件；一次性修正 .md 让它等价于新 TOML 的 behavior（或者不动，只在 TOML 里做 handoff 支持），下节决定 |

### 10.1 Standalone 路径兼容策略

两个方案：

- **方案 X**（最小改动）：只写 TOML，不动 `agents/ascendc-debug-discovery.md`。handoff 逻辑只进 TOML。
  - 优点：向后兼容 standalone。
  - 缺点：两份文档漂移。
- **方案 Y**（同步）：TOML 和 md 同步增加 handoff 前置 hook。md 增加的 hook 对 standalone 场景无副作用（handoff 文件不存在时走原路径）。
  - 优点：单一 source-of-truth 逻辑，md 和 TOML 行为一致。
  - 缺点：改动面稍大。

**推荐 Y**：handoff hook 纯 additive，无副作用，成本小。

---

## 11. 验收标准

1. `.codex/agents/ascend-kernel-developer.toml` 与 `.codex/agents/ascendc-debug-discovery-agent.toml` 语法合法（`python -c "import tomllib; tomllib.load(...)"` 通过）。
2. `.codex/AGENTS.md` 描述项目默认任务 + 两个 agent。
3. `ascend-kernel-developer.toml` 的 Phase 4 内容严格线性，无 `while ac_iteration` 字样。
4. `ascendc-debug-discovery-agent.toml` 含 §6.3 的 Step 2.1 前置 hook 与 §6.4 的返回值规范。
5. Parent spawn 模板（§9）出现在 Phase 4.4。
6. `agents/ascendc-debug-discovery.md` 按方案 Y 同步补 handoff hook；`agents/ascend-kernel-developer.md` 同步线性化（保证 md/TOML 一致）。
7. 本地 `git commit`，**不 push**、**不远端同步**。
8. 新建 `.planning/design.md` / `.planning/task_plan.md` 两份文档。

## 12. 下一步

Codex review → 定稿 design.md → writing-plans 出 bite-sized task_plan.md → 并行实现。
