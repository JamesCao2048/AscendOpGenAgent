# AscendOpGenAgent — Codex Agents Overview

本仓库为 Codex runtime 注册了三个 custom agent，全部以 TOML 形式放在 `.codex/agents/` 下。Codex 在仓库根目录启动时会自动加载这些定义。

## 注册的 agent

### `default`
- 文件：`.codex/agents/default.toml`
- 覆盖 Codex 内置 default agent
- 职责：识别用户请求类型，把「算子生成」路由到 `ascend-kernel-developer`，把「精度调优」路由到 `cann-debug-agent`，其它任务在当前 session 内处理

### `ascend-kernel-developer`
- 文件：`.codex/agents/ascend-kernel-developer.toml`
- 源 Markdown 版本：`agents/ascend-kernel-developer.md`
- 职责：端到端生成 AscendC 算子（Phase 0 → Phase 7）
- Phase 4 协议：**线性**执行，4.3 判定为纯数值失败时 spawn `cann-debug-agent`；AST 退化 / Build/Import / Mixed 错误直接跳 Phase 7

### `cann-debug-agent`
- 文件：`.codex/agents/cann-debug-agent.toml`
- 源 Markdown 版本：`agents/precision-tuning-discovery.md`（**文件名保留 `precision-tuning-discovery`**，以兼容 `run_precision_tuning.sh --agent` 默认值）
- 职责：AscendC kernel 精度调优（发现式审计）
- 支持两种启动路径：
  1. **Parent-spawn**：由 `ascend-kernel-developer` 在 Phase 4.4 spawn，读 `{task_dir}/precision_tuning/parent_handoff.json` 作为首轮上下文
  2. **Standalone**：由 `utils/run_precision_tuning.sh` 以 `--agent agents/precision-tuning-discovery.md` 启动

## Handoff 合约

### Parent → Subagent
路径：`{task_dir}/precision_tuning/parent_handoff.json`

关键字段：
- `failure_class = "Numerical"` + `failure_policy = "pure_numerical_only"`（subagent 首步校验，不匹配即 ABORT）
- `task_name` + `task_dir` + `op_name` + `npu`
- `evaluate_excerpt`（80-120 行，失败 case 名 + 关键 diff）
- `phase3_design_summary` + `phase4_translation_summary`（替代尚未落盘的 trace.md）
- `wrapper_baseline.model_new_ascendc_sha256` / `model_new_tilelang_sha256`（parent 写前计算）

完整 schema：`.planning/design.md` §6.2

### Subagent → Parent
路径：`{task_dir}/precision_tuning/subagent_result.json`

status 四值：`PASS` / `FAIL_PRECISION` / `CHEAT` / `ABORT`

`attempts_used` 上限为 2（对齐 `skills/ascendc/precision-tuning/scripts/precision_gate.py` 的 `MAX_ATTEMPTS`）。

**由 subagent 自己在收尾时写**，不由 `precision_gate.py` 写。

完整 schema：`.planning/design.md` §6.4

## Parent-side Anti-Cheat 复核

`ascend-kernel-developer` 在消费 subagent `PASS` 之前必须：

1. 重新计算 `sha256(model_new_ascendc.py)` 与 `sha256(model_new_tilelang.py)`（若存在）
2. 与 `parent_handoff.json.wrapper_baseline` 对比，任一不一致 → 判 CHEAT → Phase 7
3. 重跑 `skills/ascendc/ascendc-translator/scripts/validate_ascendc_impl.py {output_dir}/model_new_ascendc.py`，exit != 0 → 判 CHEAT → Phase 7

这层兜底为 parent-spawn 路径补上 `utils/run_precision_tuning.sh` standalone 场景下由 `.bench_baseline/` 提供的保护。

## 烟测

### 静态 tomllib 解析

```bash
cd /Users/junming/code/operator/AscendOpGenAgent
python3.11 -c "
import tomllib, pathlib, sys
ok = True
for p in sorted(pathlib.Path('.codex/agents').glob('*.toml')):
    try:
        with open(p, 'rb') as f: data = tomllib.load(f)
        print(f'  OK  {p.name:40s} name={data.get(\"name\",\"<missing>\")}')
    except Exception as e:
        print(f'  FAIL {p.name:40s} {e}')
        ok = False
sys.exit(0 if ok else 1)
"
```

> 注意：本机默认 `python3` 是 3.10，`tomllib` 是 3.11+ 标准库。用 `python3.11` 跑。

### 运行时加载

在仓库根目录启动 Codex：

```bash
cd /Users/junming/code/operator/AscendOpGenAgent
codex
```

预期：进入交互 session 后，敲测试请求「生成ascendC算子，npu=0，算子描述文件为 xxx.py，输出到 /tmp/test/」，观察 default agent 是否按路由规则 spawn `ascend-kernel-developer`。若出现 `agent not found` 之类报错，说明 TOML 未被加载（很可能是 Codex 版本不支持 custom agent 或 `.codex/agents/` 路径需要额外配置）。

## 与 standalone 路径的关系

`utils/run_precision_tuning.sh` **不解析 TOML**，只把 `agents/precision-tuning-discovery.md` 的文件路径注入 prompt template（见脚本第 27 行 `AGENT_FILE` 默认值 + 第 34-52 行 `PROMPT_TEMPLATE`）。因此：

- TOML 侧的改名 (`precision-tuning-discovery` → `cann-debug-agent`) **只影响 Codex runtime**，不影响 standalone 调度。
- `.md` 文件名保持 `precision-tuning-discovery.md`，standalone 路径继续工作。
- `.md` 文件里的 `Parent-Spawn Handoff Pre-Hook` section 对 standalone 无副作用（handoff 文件不存在时走原逻辑）。

## 相关文档

- `.planning/design.md` — Phase 4 subagent delegation 定稿设计（Codex review 版）
- `.planning/design_draft.md` — 设计草案（Claude Code 版，保留对照）
- `.planning/task_plan.md` — 实现方案（bite-sized tasks）
- `CLAUDE.md` — 项目根 CLAUDE Code 指南（device env / file transfer / git sync）
