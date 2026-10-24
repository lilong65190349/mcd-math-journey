"""麦麦取经录 —— 文档防漂移自检（README 示例 vs 当前代码）。

为什么需要它
------------
README 第七节写的是「真实运行结果」，一旦出题模板、CLI 文案或数据快照变了，
文档就会悄悄失真——而且只有人肉复读才会发现。本脚本把这件事机器化：
用文档里记录的固定 seed 重新跑一遍，与 README 代码块逐行 diff。

比对口径（重要）
----------------
* **时间戳类字段归一化**：错题时间、周报统计区间、缓存抓取时间随机器与时刻变化，
  物理上不可能逐字稳定，比较前统一替换成 ``<时间>``。
* **终端输入回显**：README 里 ``你的答案 > 29`` 这类行是「人敲进去的字符」，
  真实管道运行时终端不会再把输入打印一遍，因此这类行整行跳过。
* **绝对路径**：示例 1 的 ``doctor`` 输出含本机路径与 Python 小版本号，
  用 ``...`` 省略，比较时按「分段顺序包含」判断，不做硬 diff。

用法::

    python scripts/selfcheck.py          # 通过时打印 DOC_SYNC: OK，退出码 0

只使用标准库；运行前后会备份 / 还原 ``runtime/progress.db``，不会破坏本机进度。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

PROJECT_ROOT: Path = Path(__file__).resolve().parents[1]
README_PATH: Path = PROJECT_ROOT / "README.md"
CLI: Path = PROJECT_ROOT / "scripts" / "cli.py"
DATA_CLI: Path = PROJECT_ROOT / "scripts" / "data_source.py"

DATETIME_RE = re.compile(r"\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}:\d{2}(?:\+08:00)?)?")
ECHO_RE = re.compile(r"^\s*你的答案 > \S{1,8}$")


def normalize(text: str) -> str:
    """把时间戳归一化，便于跨机器比较。"""
    return DATETIME_RE.sub("<时间>", text)


def run(script: Path, args: List[str], stdin: str = "", db_path: str = "") -> str:
    """跑一条 CLI 命令并返回 stdout（stderr 丢弃，避免污染比对）。

    ``db_path`` 会通过环境变量 ``MMQJ_DB_PATH`` 传给子进程，让示例在一个**临时进度库**
    上复跑：既不污染用户自己的 ``runtime/progress.db``，也保证示例 1 永远是「首次运行」状态。
    """
    env = dict(os.environ)
    if db_path:
        env["MMQJ_DB_PATH"] = db_path
    completed = subprocess.run(
        [sys.executable, str(script)] + args,
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(PROJECT_ROOT),
        env=env,
    )
    return completed.stdout or ""


def expected_answers(count: int, grade: int, seed: str) -> str:
    """按 play 的 seed 规则预算出每题的正确答案（用于喂给交互式 play）。"""
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    import problem_gen  # noqa: E402  (延迟导入，避免影响 --help)

    answers = [problem_gen.generate(grade=grade, salt=f"{seed}-{i}")["answer"] for i in range(count)]
    return "\n".join(answers) + "\n"


def readme_blocks() -> Tuple[List[str], List[int]]:
    """取出 README 第七节的全部 console 代码块及每块起始行号（1 基）。"""
    lines = README_PATH.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("## 七、5 个使用示例"))
    end = next(i for i, line in enumerate(lines) if line.startswith("## 八、项目结构"))
    blocks: List[str] = []
    starts: List[int] = []
    index = start
    while index < end:
        if lines[index].strip() == "```console":
            begin = index + 1
            cursor = begin
            while cursor < end and lines[cursor].strip() != "```":
                cursor += 1
            blocks.append("\n".join(lines[begin:cursor]))
            starts.append(begin + 1)  # 代码块首行在文件中的行号
            index = cursor + 1
            continue
        index += 1
    return blocks, starts


def compare_block(block: str, output: str, first_line_no: int) -> List[Dict[str, Any]]:
    """逐行比较一个 README 代码块与实际输出。"""
    actual = normalize(output)
    problems: List[Dict[str, Any]] = []
    for offset, line in enumerate(block.splitlines()):
        line_no = first_line_no + offset
        if not line.strip() or line.startswith("$ "):
            continue
        if ECHO_RE.match(line):
            continue  # 终端不会回显输入
        want = normalize(line)
        if "..." in want:
            # 绝对路径 / 版本号等跨机器不稳定内容：按分段顺序包含判断
            position, matched = 0, True
            for part in [piece for piece in want.split("...") if piece]:
                found = actual.find(part, position)
                if found < 0:
                    matched = False
                    break
                position = found + len(part)
            if not matched:
                problems.append({"line_no": line_no, "expect": line, "actual": "<未匹配到分段>"})
            continue
        if want not in actual:
            problems.append({"line_no": line_no, "expect": line, "actual": "<输出中不存在该行>"})
    return problems


def collect_outputs(db_path: str) -> List[str]:
    """按 README 顺序复跑 5 段示例，返回各段真实输出。

    Args:
        db_path: 临时进度库路径，透传给子进程的 ``MMQJ_DB_PATH``。
    """
    outputs: List[str] = []
    outputs.append(run(CLI, ["doctor"], db_path=db_path))
    outputs.append(
        run(CLI, ["play", "--level", "3-2", "--seed", "demo"], "h\nh\n29\n27\n", db_path)
    )
    outputs.append(
        run(
            CLI,
            ["play", "--grade", "4", "--count", "5", "--seed", "g4"],
            expected_answers(5, 4, "g4"),
            db_path,
        )
    )
    outputs.append(run(CLI, ["wrongbook"], db_path=db_path))
    outputs.append(
        run(CLI, ["report", "--days", "7"], db_path=db_path)
        + "\n"
        + run(DATA_CLI, ["--status"], db_path=db_path)
    )
    return outputs


def main() -> int:
    """入口：返回 0 表示文档与代码一致。"""
    if not README_PATH.exists():
        print("DOC_SYNC: FAIL —— 找不到 README.md")
        return 1

    blocks, starts = readme_blocks()
    if len(blocks) < 5:
        print(f"DOC_SYNC: FAIL —— README 第七节应有 5 个示例块，实际 {len(blocks)} 个")
        return 1

    # 在临时目录建一份全新的进度库复跑，绝不触碰用户自己的 runtime/progress.db。
    # 目录特意命名为 <tmp>/runtime/progress.db：这样示例 1 里 `...\runtime\progress.db`
    # 这类带省略号的路径行，在本机与临时库两种情况下都能按「分段包含」匹配上。
    with tempfile.TemporaryDirectory(prefix="mmqj-selfcheck-") as tmpdir:
        db_dir = Path(tmpdir) / "runtime"
        db_dir.mkdir(parents=True, exist_ok=True)
        outputs = collect_outputs(str(db_dir / "progress.db"))

    problems: List[Dict[str, Any]] = []
    for block, first_line, output in zip(blocks, starts, outputs):
        problems.extend(compare_block(block, output, first_line))

    if not problems:
        total_lines = sum(len(block.splitlines()) for block in blocks)
        print(f"DOC_SYNC: OK —— 5 段示例共 {total_lines} 行，全部与当前代码一致")
        return 0

    print(f"DOC_SYNC: FAIL —— 发现 {len(problems)} 处文档与代码不一致：")
    for item in problems:
        print(f"  README:{item['line_no']}")
        print(f"    期望: {item['expect']}")
        print(f"    实际: {item['actual']}")
    return 1


if __name__ == "__main__":  # pragma: no cover - 脚本入口
    raise SystemExit(main())
