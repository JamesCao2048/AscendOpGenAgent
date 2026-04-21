# Phase 4 Subagent Delegation — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use `subagent-driven-development` — this plan is executed in-session by dispatching parallel subagents, with main-agent review between parallelizable groups.

**Goal:** 把两个 Markdown agent 定义迁到 `.codex/agents/*.toml`，把 parent agent 的 Phase 4 从三轮自循环改为"线性 + spawn `ascendc-debug-discovery-agent`"，同时在 subagent 侧加入 handoff pre-hook，保证 standalone 与 parent-spawn 两条路径共存且行为一致。

**Architecture:** 两份 agent 定义按"TOML（Codex runtime）+ MD（人类文档 & standalone 脚本入口）"双持；Phase 4 改造与 handoff 合约落在这两份文件上。配合 `.codex/agents/default.toml`（route A）把"`codex` 启动即进入主 agent"做成确定性行为，并用 `.codex/AGENTS.md` 做项目入口说明。

**Tech Stack:** Codex CLI (v0.121.0, 本机已装), Python `tomllib`（Python 3.11+），现有 `precision_forensics.py` / `precision_gate.py` 保持不变。

**Source of truth for content:** `.planning/design.md`（Codex 定稿版），以下任务所有文案分歧均以 design.md 为准。

---

## 执行拓扑

```
┌──────────────────────────────────────────────┐
│  Group A (parallel)  ─ Phase 4 linearization │
│  ├─ Task A1: .codex/agents/ascend-kernel-     │
│  │           developer.toml                   │
│  └─ Task A2: agents/ascend-kernel-developer.md│
└──────────────────────────────────────────────┘
          │
          ▼
┌──────────────────────────────────────────────┐
│  Group B (parallel with A)  ─ Handoff hook    │
│  ├─ Task B1: .codex/agents/cann-debug-        │
│  │           agent.toml                       │
│  └─ Task B2: agents/ascendc-debug-         │
│              discovery.md                     │
└──────────────────────────────────────────────┘
          │ (A & B both done)
          ▼
┌──────────────────────────────────────────────┐
│  Group C (sequential)                         │
│  ├─ Task C1: .codex/agents/default.toml       │
│  ├─ Task C2: .codex/AGENTS.md                 │
│  ├─ Task C3: static + runtime smoke tests     │
│  └─ Task C4: local git commit (no push)       │
└──────────────────────────────────────────────┘
```

Group A 和 Group B 并行安全：两者操作的是不同文件、不同概念（Phase 4 重写 vs handoff pre-hook）。Group C 依赖 A/B 的产物存在。

---

## Task A1: 写 `.codex/agents/ascend-kernel-developer.toml`

**Files:**
- Create: `.codex/agents/ascend-kernel-developer.toml`
- Reference-read: `agents/ascend-kernel-developer.md`（原始 system prompt）

**Step 1: 构造 TOML 骨架**

字段集（按 design.md §4 最小可验证集合）：

```toml
name = "ascend-kernel-developer"
description = "Ascend 算子端到端生成 agent：PyTorch Model → TileLang 设计 → AscendC kernel 实现 → 性能分析 → trace。任何 AscendC 算子 codegen 任务应路由到此 agent。"

developer_instructions = """
<正文>
"""
```

**不写**：`model`、`model_reasoning_effort`、`sandbox_mode`、`mcp_servers`、`skills.config`、`temperature`、`tools`、`skills`、`argument-hint`。

**Step 2: 正文移植（developer_instructions）**

从 `agents/ascend-kernel-developer.md` 第 27 行（`# System Prompt`）到文件末尾**复制**为 developer_instructions，但按 Step 3 改 Phase 4。

**Step 3: 重写 Phase 4 为线性版**

删除 md 原文第 304-443 行的整块 `while ac_iteration < max_ac_iterations:` 循环及其状态变量、Conductor 修复建议格式。替换为：

```
## Phase 4: AscendC 转译与验证（线性 + 委派）

### 前置条件
- {output_dir}/design/tile_level/ TileLang 代码已存在
- {output_dir}/model_new_tilelang.py 已存在

### 4.0 AscendC 转译（一次性）

调用 `ascendc-translator` skill，读取 `@references/TileLang-AscendC-API-Mapping.md`，
将 {output_dir}/design/tile_level/ 的 TileLang kernel 转译为 AscendC kernel，
输出到 {output_dir}/kernel/。

### 4.1 生成 wrapper（一次性）

调用 `ascendc-translator` skill，基于 {output_dir}/kernel/ 生成
{output_dir}/model_new_ascendc.py。

产物：
- {output_dir}/model_new_ascendc.py

### 4.2 AST 退化预检查（一次性）

python skills/ascendc/ascendc-translator/scripts/validate_ascendc_impl.py \
    {output_dir}/model_new_ascendc.py

- exit == 0 → 进入 4.3
- exit != 0 → 标记 A-AscendCFallback-Type{N}，**跳到 Phase 7（不 spawn subagent）**
  原因：精度调优 subagent 被禁止修改 model_new_ascendc.py / model.py，
  AST 退化本质是 wrapper 退化，subagent 无法在合规前提下修复。

### 4.3 功能验证（一次性）

bash skills/ascendc/ascendc-translator/references/evaluate_ascendc.sh \
    {output_dir}

分类逻辑（保守优先）：

| 信号（在 evaluate 输出中出现） | 分类 | 路由 |
|---|---|---|
| `ModuleNotFoundError`、`ImportError`、`undefined symbol`、`cannot open shared object file`、`fatal error:`、`c++: error`、`ld:`、`collect2:`、`undefined reference`、`SyntaxError`、`NameError`、`AttributeError`、`No such file or directory` | Build/Import | Phase 7（不 spawn） |
| `Tensors are not close`、`max_abs_diff`、`mismatch_ratio`、`Numerical mismatch`、per-case diff 统计 | Numerical | 候选进入 4.4 |
| 两类都命中 | Mixed | Phase 7（不 spawn） |
| 两类都未命中 | Unknown | Phase 7（不 spawn），trace 标 A-Unknown-Phase4Fail |

当且仅当"无 Build/Import 信号且存在 Numerical 信号"时，允许进入 4.4。
即便只有部分 case 数值失败、另一些 case 通过，只要无 Build/Import 信号，仍视为可 spawn。

- all pass → 进入 Phase 5
- pure numerical fail → 进入 4.4
- 其它 → Phase 7

### 4.4 委派 ascendc-debug-discovery-agent（spawn）

**Step A: 计算 parent-side wrapper baseline**

sha256_ascendc = sha256({output_dir}/model_new_ascendc.py)
sha256_tilelang = sha256({output_dir}/model_new_tilelang.py) if exists else null

**Step B: 写 parent_handoff.json**

路径: {output_dir}/precision_tuning/parent_handoff.json

(若 {output_dir}/precision_tuning/ 不存在，先 mkdir)

schema:
{
  "source": "ascend-kernel-developer@phase4",
  "spawned_at": "<ISO8601>",
  "task_name": "<output_dir basename>",
  "task_dir": "<output_dir absolute path>",
  "op_name": "<算子名，同 task_name>",
  "npu": <NPU_ID>,
  "failure_class": "Numerical",
  "failure_policy": "pure_numerical_only",
  "evaluate_excerpt": "<evaluate_ascendc.sh 输出中失败 case 名 + 关键 diff 统计，80-120 行>",
  "phase3_design_summary": {
    "design_dir": "{output_dir}/design/tile_level",
    "key_choices": ["<block/tile 切分>", "<核心 primitives>"]
  },
  "phase4_translation_summary": {
    "kernel_files": ["<kernel/*.cpp 和 *.h 列表>"],
    "api_usage": ["<实际在 kernel 中用到的主力 AscendC API>"],
    "known_platform_limits": ["<若 Phase 3/4 已发现的 API/dtype 限制>"]
  },
  "wrapper_baseline": {
    "model_new_ascendc_sha256": "<sha256_ascendc>",
    "model_new_tilelang_sha256": "<sha256_tilelang 或 null>"
  }
}

**Step C: Spawn ascendc-debug-discovery-agent**

spawn 说明（自然语言，Codex runtime 按名字识别）：
"spawn ascendc-debug-discovery-agent subagent 执行精度调优。
 输入契约：{output_dir}/precision_tuning/parent_handoff.json。
 目标：让 evaluate_ascendc.sh 全部 case 通过。
 允许修改范围：仅 {output_dir}/kernel/。
 返回值：写入 {output_dir}/precision_tuning/subagent_result.json。"

**Step D: 等待 subagent 返回，读 subagent_result.json**

PASS → 执行 Step E parent-side anti-cheat 复核
FAIL_PRECISION → 跳到 Phase 7
CHEAT → 跳到 Phase 7，trace 标异常
ABORT → 跳到 Phase 7，trace 标 subagent_abort

**Step E: parent-side anti-cheat 复核（仅 PASS 分支）**

1. 重新 sha256 {output_dir}/model_new_ascendc.py (+ model_new_tilelang.py if exists)
2. 与 wrapper_baseline 对比；任一不一致 → 判 CHEAT，跳 Phase 7
3. 重跑 validate_ascendc_impl.py {output_dir}/model_new_ascendc.py
4. exit != 0 → 判 CHEAT，跳 Phase 7
5. 全部通过 → 进入 Phase 5

**产出**:
- {output_dir}/kernel/ — AscendC kernel 文件
- {output_dir}/model_new_ascendc.py — AscendC 优化实现（subagent 调完未改 wrapper）
- {output_dir}/precision_tuning/ — 精度调优完整历史

### 约束

| 约束 | 说明 |
|------|------|
| Phase 4 不再有 parent 自循环 | 线性执行；数值失败委派 ascendc-debug-discovery-agent subagent |
| Phase 4.4 的 subagent 最多 2 轮 | 等同 precision_gate.py 的 MAX_ATTEMPTS |
| 禁止 PyTorch 退化 | 同原文 |
| 退化检测前置 | 同原文，但只做一次 |
| 文件操作范围 | subagent 仅 {output_dir}/kernel/；parent 收尾 anti-cheat 复核 wrapper |
```

**Step 4: 同步更新"约束"、"错误处理"、"任务目录结构"等次要段**

- "约束"表：删除 `Phase 4 最大迭代 3 次`；改为 `Phase 4 subagent 最多 2 轮（由 precision_gate.py MAX_ATTEMPTS 约束）`。
- "错误处理"表：Phase 4 三行压缩为三项：AST 退化 → Phase 7；Build/Import → Phase 7；数值失败 → spawn ascendc-debug-discovery-agent。
- 保留 "AscendC 退化子类型"、"A 类错误分类（AscendC）"、"B 类错误分类" 这些表（trace/诊断文档仍需引用）。

**Step 5: 落盘**

写入 `/Users/junming/code/operator/AscendOpGenAgent/.codex/agents/ascend-kernel-developer.toml`。

**Acceptance:**
- `python3 -c "import tomllib; tomllib.load(open('.codex/agents/ascend-kernel-developer.toml','rb'))"` 返回 0
- 文件里无 `while ac_iteration` / `max_ac_iterations = 3` / `ac_history_attempts` 等原循环变量
- 文件里存在 `ascendc-debug-discovery-agent` / `parent_handoff.json` / `subagent_result.json` / `wrapper_baseline` 关键字

---

## Task A2: 同步 `agents/ascend-kernel-developer.md`（Plan Y）

**Files:**
- Modify: `agents/ascend-kernel-developer.md`（完整覆盖 Phase 4 相关章节）

**Step 1: 对齐 Task A1 的 Phase 4 内容**

把 `agents/ascend-kernel-developer.md` 的 Phase 4 章节（大致第 304-443 行，含"迭代循环"、"Conductor 修复建议格式"、"AscendC 退化子类型"等）替换为与 Task A1 `developer_instructions` 里完全一致的 Phase 4 文本。

**Step 2: 次要段同步**

约束表、错误处理表同 Task A1 Step 4。

**Acceptance:**
- `diff <(grep -A 200 'Phase 4:' .codex/agents/ascend-kernel-developer.toml) <(grep -A 200 'Phase 4:' agents/ascend-kernel-developer.md)` 语义一致（允许 TOML quoting 差异，但控制流/关键词对齐）
- 文件里无 `while ac_iteration`

---

## Task B1: 写 `.codex/agents/ascendc-debug-discovery-agent.toml`

**Files:**
- Create: `.codex/agents/ascendc-debug-discovery-agent.toml`
- Reference-read: `agents/ascendc-debug-discovery.md`

**Step 1: 构造 TOML 骨架**

```toml
name = "ascendc-debug-discovery-agent"
description = "AscendC kernel 精度调优 subagent（发现式审计）。由 ascend-kernel-developer 在 Phase 4.4 数值失败时 spawn，或通过 utils/run_ascendc_debug.sh standalone 启动。只修改 {task_dir}/kernel/。"

developer_instructions = """
<正文>
"""
```

**Step 2: 正文移植**

从 `agents/ascendc-debug-discovery.md` 第 25 行（`# System Prompt`）开始整体复制。

保留原文所有 section（Role Definition / Core Capabilities / Operational Guidelines / 反作弊约束 / Communication Style / Environment）不动。

**Step 3: 增补 handoff pre-hook 段**

在正文 "## Operational Guidelines" 之后、"## Communication Style" 之前，插入新 section：

```
## Parent-Spawn Handoff Pre-Hook

### 适用场景
本 agent 既可由 utils/run_ascendc_debug.sh 以 standalone 方式启动，
也可由 ascend-kernel-developer 在 Phase 4.4 spawn。两种场景共用同一组 Step，
仅在"首轮 Step 2.1 取证数据解读"之前多一个 pre-hook。

### Pre-hook 逻辑（attempt == 0 且本 agent 被 spawn 时）

1. 检查 {task_dir}/precision_tuning/parent_handoff.json 是否存在。

2. 存在时：
   - 读入 JSON
   - 校验 failure_class == "Numerical" 且 failure_policy == "pure_numerical_only"
   - 若校验失败，写 subagent_result.json 且 status = ABORT，立即退出
   - 将 handoff 中以下字段摘录进 [PRIOR_TRACE_CONTEXT] section：
     * phase3_design_summary.key_choices
     * phase4_translation_summary.api_usage, known_platform_limits
     * evaluate_excerpt 的 top-3 失败 case + 最大 diff
   - 记住 wrapper_baseline，在结束时写入 subagent_result.json 的 evidence

3. 不存在时（standalone 路径）：
   - 按原逻辑，attempt == 0 时可选读取 {task_dir}/trace.md

4. 无论走哪条路径，之后继续执行原有 Step 1 (precision_forensics.py) 与 Gate-F。
   Pre-hook 不改变 Gate-A 的必填 section 列表；[PRIOR_TRACE_CONTEXT] 仍是可选段，
   但 parent-spawn 场景下必须写。

### 结束时产物：subagent_result.json

在 Gate 流程全部完成（无论 PASS 还是到达 MAX_ATTEMPTS）后，
写 {task_dir}/precision_tuning/subagent_result.json：

{
  "status": "PASS" | "FAIL_PRECISION" | "CHEAT" | "ABORT",
  "attempts_used": <实际跑到的 attempt 编号 + 1，上限 2>,
  "final_max_abs_diff": <最终一轮 forensics 的 outputs[0].basic_stats.max_abs_diff>,
  "modified_files": ["kernel/xxx.cpp", ...],
  "reason": "<一句话>",
  "evidence": {
    "round_summary": "precision_tuning/round_summary_<N>.json",
    "validation": "precision_tuning/validation_result_attempt_<N>.json",
    "tuning_directions": "precision_tuning/tuning_directions.json"
  }
}

status 判别规则：
- Gate-V 返回 PASS 且全量验证通过 → PASS
- Gate-V 返回 STOP 且非 PASS，或 attempts 耗尽 → FAIL_PRECISION
- 发现本轮意外改了 wrapper 文件 → CHEAT（即使精度过了也判 CHEAT）
- 任何 Pre-hook 校验失败或取证数据不可用 → ABORT

subagent_result.json 由本 agent 自己写，不由 precision_gate.py 写。
```

**Step 4: 落盘**

写入 `/Users/junming/code/operator/AscendOpGenAgent/.codex/agents/ascendc-debug-discovery-agent.toml`。

**Acceptance:**
- `python3 -c "import tomllib; tomllib.load(open('.codex/agents/ascendc-debug-discovery-agent.toml','rb'))"` 返回 0
- 文件含 `parent_handoff.json`、`subagent_result.json`、`failure_class == "Numerical"`、`MAX_ATTEMPTS` 或 `上限 2` 等关键字
- `name = "ascendc-debug-discovery-agent"` 行存在（不是 `ascendc-debug-discovery`）

---

## Task B2: 同步 `agents/ascendc-debug-discovery.md`（Plan Y）

**Files:**
- Modify: `agents/ascendc-debug-discovery.md`（在"Operational Guidelines"之后、"Communication Style"之前插入 handoff pre-hook 段）

**Step 1: 插入 pre-hook**

与 Task B1 Step 3 的内容**逐字一致**（除 TOML triple-quoted 差异）。

**Step 2: 不改 agent 名**

保留原文 front-matter 的 `name: ascendc-debug-discovery`，不改为 `ascendc-debug-discovery-agent`。

原因：`utils/run_ascendc_debug.sh` 显式把 `agents/ascendc-debug-discovery.md` 的路径注入 prompt；改名会破坏 standalone 调度。TOML 侧叫 `ascendc-debug-discovery-agent` 只影响 Codex runtime。

**Acceptance:**
- `agents/ascendc-debug-discovery.md` 新段内容与 `.codex/agents/ascendc-debug-discovery-agent.toml` handoff pre-hook 段对应
- `run_ascendc_debug.sh` 的 `AGENT_FILE` 默认值 `agents/ascendc-debug-discovery.md` 仍有效（grep 确认）

---

## Task C1: 写 `.codex/agents/default.toml`（route A）

**Files:**
- Create: `.codex/agents/default.toml`

**Step 1: 构造 default override**

```toml
name = "default"
description = "AscendOpGenAgent 仓库默认 agent：任何 AscendC 算子生成任务，请立即 spawn ascend-kernel-developer；任何精度调优任务，请立即 spawn ascendc-debug-discovery-agent。"

developer_instructions = """
你运行在 AscendOpGenAgent 仓库中。本仓库的核心任务是"基于 PyTorch Model 生成 AscendC 算子"。

## 路由规则

1. 若用户请求"生成/编写/优化 AscendC 算子"（典型输入格式：`生成ascendC算子，npu=<NPU>, 算子描述文件为 <model.py>, 输出到 <output_dir>`）：
   - 立即 spawn `ascend-kernel-developer` subagent，把用户完整输入作为任务描述传入。
   - 不要自己做 codegen。

2. 若用户请求"精度调优"（典型输入格式：`precision tune <task_name> [npu=<NPU>]`）：
   - 立即 spawn `ascendc-debug-discovery-agent` subagent。
   - 不要自己做精度分析。

3. 其它编排型任务（查看历史、分析 trace、调度 batch）：
   - 直接在当前 session 内回答；必要时使用 Read/Bash 工具。

## 仓库入口参考

- `agents/` — 人类可读的 agent 定义（Markdown）
- `.codex/agents/` — Codex 加载的 agent 定义（TOML）
- `skills/ascendc/` — 子技能（tilelang-designer / ascendc-translator / ascendc-debug / ...）
- `utils/run_ascendc_debug.sh` — 精度调优批量调度器（standalone 路径）
- `utils/run_benchmark_ascendc_codex.sh` — benchmark 批量调度器

## 语言

思考/分析/日志使用中文；代码/路径使用英文。
"""
```

**Acceptance:**
- TOML 语法合法
- `name = "default"`（恰好是 Codex 内置 agent 名，触发 override）

---

## Task C2: 写 `.codex/AGENTS.md`

**Files:**
- Create: `.codex/AGENTS.md`

**Step 1: 写文件**

内容大纲：

```markdown
# AscendOpGenAgent — Codex Agents Overview

本仓库注册了三个 Codex custom agent，全部位于 `.codex/agents/`：

## default
`.codex/agents/default.toml`
覆盖内置 default，把所有算子生成 / 精度调优请求分别 spawn 到下面两个专用 agent。
用户在仓库根目录运行 `codex` 即自动进入该 agent。

## ascend-kernel-developer
`.codex/agents/ascend-kernel-developer.toml`
端到端 AscendC 算子生成主 agent。Phase 4 数值失败时 spawn `ascendc-debug-discovery-agent`
做精度调优。人类可读版本：`agents/ascend-kernel-developer.md`。

## ascendc-debug-discovery-agent
`.codex/agents/ascendc-debug-discovery-agent.toml`
AscendC kernel 精度调优 subagent（发现式审计）。支持两种启动方式：
- 由 ascend-kernel-developer 在 Phase 4.4 spawn（读 parent_handoff.json）
- 由 utils/run_ascendc_debug.sh standalone 启动（读 agents/ascendc-debug-discovery.md）

---

## Handoff 合约

Parent → Subagent 文件：`{task_dir}/precision_tuning/parent_handoff.json`
Subagent → Parent 文件：`{task_dir}/precision_tuning/subagent_result.json`
Schema 详见 `.planning/design.md` §6.2 与 §6.4。

## 反作弊复核

Parent 在消费 subagent `PASS` 之前必须：
1. 重新 sha256 `model_new_ascendc.py` / `model_new_tilelang.py`，与 handoff 中的 wrapper_baseline 对比
2. 重跑 `validate_ascendc_impl.py`
任一失败即判 CHEAT。

## 烟测

静态：
```bash
python3 -c "
import tomllib, pathlib
for p in pathlib.Path('.codex/agents').glob('*.toml'):
    with open(p, 'rb') as f: tomllib.load(f)
    print('OK', p)
"
```

运行时：在仓库根目录运行 `codex` 进入交互模式，输入 `spawn ascendc-debug-discovery-agent for task foo`，
观察 runtime 是否识别 custom agent（若报错 agent not found，说明 TOML 加载失败）。
```

---

## Task C3: 烟测

**Step 1: 静态 tomllib 解析**

```bash
cd /Users/junming/code/operator/AscendOpGenAgent
python3 -c "
import tomllib, pathlib, sys
ok = True
for p in sorted(pathlib.Path('.codex/agents').glob('*.toml')):
    try:
        with open(p, 'rb') as f: data = tomllib.load(f)
        name = data.get('name', '<missing>')
        print(f'  OK  {p.name:40s} name={name}')
    except Exception as e:
        print(f'  FAIL {p.name:40s} {e}')
        ok = False
sys.exit(0 if ok else 1)
"
```

预期：三个文件（`default.toml`、`ascend-kernel-developer.toml`、`ascendc-debug-discovery-agent.toml`）全部 `OK`，每个 name 字段非 `<missing>`。

**Step 2: Codex runtime loader smoke test**

```bash
codex exec --help | grep -iE "agent|profile" | head -5
# 仅验证 codex CLI 可用；不真正 spawn
```

不做真正的 `codex` 交互会话（会卡住等待输入）。运行时加载验证延后到用户 wake up 后手动执行。

**Acceptance:**
- Step 1 exit code 0
- Step 2 至少打印一行

---

## Task C4: 本地 git commit（**不 push**）

**Step 1: 观察改动**

```bash
cd /Users/junming/code/operator/AscendOpGenAgent
git status
git diff --stat
```

**Step 2: 分两个 commit**

先 commit TOML + AGENTS.md + .planning：

```bash
git add .codex/ .planning/
git commit -m "$(cat <<'EOF'
feat: add .codex TOML agents with Phase 4 subagent delegation

- .codex/agents/ascend-kernel-developer.toml: port agent with linearized Phase 4
  (removed 3-round self-loop, delegate pure-numerical failures to ascendc-debug-discovery-agent)
- .codex/agents/ascendc-debug-discovery-agent.toml: port ascendc-debug-discovery with
  handoff pre-hook reading {task_dir}/precision_tuning/parent_handoff.json
- .codex/agents/default.toml: override default agent to auto-route to the two
  above based on user request shape
- .codex/AGENTS.md: project-level overview, handoff contract, smoke test
- .planning/design.md: Codex-finalized design doc
- .planning/task_plan.md: implementation plan

Phase 4.4 protocol:
- Only "pure numerical failure" spawns ascendc-debug-discovery-agent (build/import/mixed/AST
  go to Phase 7)
- Parent writes parent_handoff.json with wrapper sha256 baseline
- Subagent writes subagent_result.json on completion (owner = subagent, not gate)
- Parent re-runs sha256 + validate_ascendc_impl.py as anti-cheat re-check
  before consuming PASS

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

再 commit .md 同步：

```bash
git add agents/ascend-kernel-developer.md agents/ascendc-debug-discovery.md
git commit -m "$(cat <<'EOF'
sync: mirror Phase 4 linearization + handoff pre-hook to agents/*.md

Keep agents/*.md as human-readable / standalone-path source of truth in sync
with .codex/agents/*.toml:
- ascend-kernel-developer.md: Phase 4 rewritten as linear + spawn (no
  parent self-loop)
- ascendc-debug-discovery.md: add Parent-Spawn Handoff Pre-Hook section
  (name field unchanged so run_ascendc_debug.sh --agent default keeps working)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

**Step 3: 不 push**

明确：`git push` 不在本批次任务内。用户 wake up 后自行评估。

**Acceptance:**
- `git log -2 --oneline` 输出两条新 commit
- `git status` 干净

---

## 运行方式

主 agent（此 session）分两波并行派发：

1. **并行派发** 两个 Agent subagent：
   - Agent α (Task A1+A2) — Phase 4 线性化 + md 同步
   - Agent β (Task B1+B2) — handoff pre-hook + md 同步
2. 等待两个 agent 完成，主 agent 自己执行：
   - Task C1 + C2 + C3 + C4

主 agent 在 C3 烟测失败时立即停止，不要强推进 C4 提交。
