#!/usr/bin/env python3
"""Anti-cheat detector for precision tuning.

精度调优期间禁止改动 Python wrapper（model_new_ascendc.py / model_new_tilelang.py）——
所有精度问题必须通过修复 kernel/ 内的 AscendC 源码解决。本脚本提供三个子命令：

  snapshot  — 调优开始前保存 wrapper 基线（副本 + sha256）
  verify    — 调优结束后做 hash 对比 + AST 退化检测
  restore   — 检测到作弊时从基线恢复 wrapper

检测规则：
  - hash 不一致 → 作弊（HASH_DIFF / DELETED）
  - validate_ascendc_impl.py 命中 4 类 PyTorch 退化 → 作弊（AST_FAIL:type{1-4}）

退出码：verify 0=CLEAN, 1=CHEAT；snapshot/restore 始终 0（除非 IO 错误）。

典型用法：
  # 交互式
  python3 anticheat.py snapshot /path/to/task_dir
  <...手动或通过 agent 运行精度调优...>
  python3 anticheat.py verify /path/to/task_dir
  python3 anticheat.py restore /path/to/task_dir  # 如果作弊

  # Bench 集成（JSON 机读）
  python3 anticheat.py verify /path/to/task_dir --json
"""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path


WRAPPER_FILES = ["model_new_ascendc.py", "model_new_tilelang.py"]
DEFAULT_BASELINE_DIRNAME = ".bench_baseline"


def repo_root() -> Path:
    # scripts/ -> precision-tuning/ -> ascendc/ -> skills/ -> <repo_root>
    return Path(__file__).resolve().parents[4]


def default_validator() -> Path:
    return repo_root() / "skills/ascendc/ascendc-translator/scripts/validate_ascendc_impl.py"


def sha256sum(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def cmd_snapshot(args) -> int:
    task_dir = Path(args.task_dir).resolve()
    baseline_dir = task_dir / args.baseline_name
    baseline_dir.mkdir(parents=True, exist_ok=True)

    saved = []
    for fname in WRAPPER_FILES:
        src = task_dir / fname
        if not src.exists():
            continue
        shutil.copy2(src, baseline_dir / fname)
        (baseline_dir / f"{fname}.sha256").write_text(sha256sum(src) + "\n")
        saved.append(fname)

    result = {"baseline_dir": str(baseline_dir), "saved": saved}
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"[SNAPSHOT] baseline → {baseline_dir}")
        for f in saved:
            print(f"  - {f}")
        if not saved:
            print("  (no wrapper files found — nothing to snapshot)")
    return 0


def _check_ast(wrapper: Path, validator: Path) -> dict:
    if not validator.exists():
        return {"status": "validator_missing", "path": str(validator)}
    proc = subprocess.run(
        [sys.executable, str(validator), str(wrapper), "--json"],
        capture_output=True,
        text=True,
    )
    try:
        data = json.loads(proc.stdout) if proc.stdout.strip() else {}
    except json.JSONDecodeError:
        return {"status": "parse_error", "stdout": proc.stdout[:500], "stderr": proc.stderr[:500]}

    if data.get("valid"):
        return {"status": "pass"}
    return {
        "status": "fail",
        "regression_type": data.get("regression_type"),
        "suggestion": data.get("suggestion", ""),
    }


def cmd_verify(args) -> int:
    task_dir = Path(args.task_dir).resolve()
    baseline_dir = task_dir / args.baseline_name
    reasons = []
    details = {"hash": {}, "ast": {}}

    # 1. hash 对比
    for fname in WRAPPER_FILES:
        hash_file = baseline_dir / f"{fname}.sha256"
        if not hash_file.exists():
            continue
        base_hash = hash_file.read_text().strip()
        cur = task_dir / fname
        if not cur.exists():
            reasons.append(f"DELETED:{fname}")
            details["hash"][fname] = "deleted"
            continue
        cur_hash = sha256sum(cur)
        if cur_hash != base_hash:
            reasons.append(f"HASH_DIFF:{fname}")
            details["hash"][fname] = {"baseline": base_hash, "current": cur_hash}
        else:
            details["hash"][fname] = "unchanged"

    # 2. AST 退化检测（model_new_ascendc.py）
    wrapper = task_dir / "model_new_ascendc.py"
    if wrapper.exists():
        validator = Path(args.validator) if args.validator else default_validator()
        ast = _check_ast(wrapper, validator)
        details["ast"] = ast
        if ast["status"] == "fail":
            reasons.append(f"AST_FAIL:type{ast.get('regression_type')}")
        elif ast["status"] == "validator_missing":
            reasons.append(f"VALIDATOR_MISSING:{ast.get('path')}")
        elif ast["status"] in ("parse_error",):
            reasons.append(f"AST_ERROR:{ast['status']}")
    else:
        details["ast"] = {"status": "wrapper_missing"}

    verdict = "CHEAT" if reasons else "CLEAN"
    result = {"verdict": verdict, "reasons": reasons, "details": details}

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        icon = "🚨" if verdict == "CHEAT" else "✅"
        print(f"[VERIFY] {icon} {verdict}")
        if reasons:
            for r in reasons:
                print(f"  - {r}")
            if details.get("ast", {}).get("status") == "fail":
                print(f"  AST suggestion: {details['ast'].get('suggestion', '')}")
        else:
            print("  wrapper hash 未变 + AST 退化检测通过")

    return 1 if verdict == "CHEAT" else 0


def cmd_restore(args) -> int:
    task_dir = Path(args.task_dir).resolve()
    baseline_dir = task_dir / args.baseline_name
    restored = []
    for fname in WRAPPER_FILES:
        src = baseline_dir / fname
        if not src.exists():
            continue
        shutil.copy2(src, task_dir / fname)
        restored.append(fname)

    result = {"restored": restored}
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"[RESTORE] ← {baseline_dir}")
        for f in restored:
            print(f"  - {f}")
        if not restored:
            print("  (no baseline files — nothing to restore)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("task_dir", help="absolute path to task directory")
    common.add_argument("--baseline-name", default=DEFAULT_BASELINE_DIRNAME,
                        help=f"baseline subdirectory under task_dir (default: {DEFAULT_BASELINE_DIRNAME})")
    common.add_argument("--json", action="store_true", help="machine-readable JSON output")

    p = argparse.ArgumentParser(
        description="Anti-cheat detector for precision tuning "
                    "(wrapper hash + AST regression check)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("snapshot", parents=[common],
                   help="save baseline copies + sha256 before tuning starts")
    sp_ver = sub.add_parser("verify", parents=[common],
                            help="hash compare + AST regression check after tuning")
    sp_ver.add_argument("--validator",
                        help="path to validate_ascendc_impl.py (auto-detected by default)")
    sub.add_parser("restore", parents=[common],
                   help="restore wrappers from baseline (on cheat detection)")
    return p


def main() -> None:
    args = build_parser().parse_args()
    dispatch = {"snapshot": cmd_snapshot, "verify": cmd_verify, "restore": cmd_restore}
    sys.exit(dispatch[args.cmd](args))


if __name__ == "__main__":
    main()
