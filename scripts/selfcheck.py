"""麦麦取经录 —— 文档防漂移自检（README 示例 vs 当前代码）。

为什么需要它
------------
README 第七节写的是「真实运行结果」，一旦出题模板、CLI 文案或数据快照变了，
文档就会悄悄失真——而且只有人肉复读才会发现。本脚本把这件事机器化：
用文档里记录的固定 seed 重新跑一遍，与 README 代码块逐行 diff。

除示例输出之外，还校验两件容易悄悄坏掉的事：

1. **故事线完整性**：每条 ``available`` 的线必须 9 关 × 9 难 = 81 个 level，
   ``brief`` 非空且线内不重复，``kp_id`` 必须命中 ``data/curriculum.json``；
2. **演示页题库一致性**：``demo/index.html`` 里内联的 BANK 必须与 ``demo/bank.json``
   逐题一致（防止有人只改了一边）。

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

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

PROJECT_ROOT: Path = Path(__file__).resolve().parents[1]
README_PATH: Path = PROJECT_ROOT / "README.md"
CLI: Path = PROJECT_ROOT / "scripts" / "cli.py"
DATA_CLI: Path = PROJECT_ROOT / "scripts" / "data_source.py"
DEMO_INDEX: Path = PROJECT_ROOT / "demo" / "index.html"
DEMO_BANK: Path = PROJECT_ROOT / "demo" / "bank.json"

BANK_START: str = "/* @@BANK_START@@ */"
BANK_END: str = "/* @@BANK_END@@ */"

DATETIME_RE = re.compile(r"\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}:\d{2}(?:\+08:00)?)?")
ECHO_RE = re.compile(r"^\s*你的答案 > \S{1,8}$")

# 示例 2（1 错 + 1 对）+ 示例 3（5 对）= 7 次作答、6 次答对。
# 这是「这两段示例本该产生多少次作答」的算术事实，用来交叉校验周报里的累计量。
EXPECTED_ATTEMPTS: int = 7
EXPECTED_CORRECT: int = 6
REPORT_RE = re.compile(
    r"共作答 \*\*(\d+)\*\* 题，答对 \*\*(\d+)\*\* 题，正确率 \*\*([\d.]+)%\*\*"
)


def numeric_consistency(report_output: str) -> List[str]:
    """交叉校验周报里的累计量是否算术自洽。

    只做「字面 diff」还不够：如果哪天模板变了导致作答次数变化，而文档也跟着被改成另一组数字，
    字面 diff 会通过、但数字本身可能是错的。这里再独立验一遍算术关系：

    * ``共作答 N 题`` 必须等于这两段示例本该产生的作答次数（2 + 5 = 7）；
    * ``答对 M 题`` 必须等于 ``EXPECTED_CORRECT``；
    * ``正确率 X%`` 必须等于 ``round(M / N * 100, 1)``。

    Args:
        report_output: ``cli.py report`` 的真实输出。

    Returns:
        问题描述列表；空列表表示自洽。
    """
    match = REPORT_RE.search(report_output)
    if not match:
        return ["未在 report 输出中找到「共作答 N 题 / 答对 M 题 / 正确率 X%」"]
    total, correct, rate = int(match.group(1)), int(match.group(2)), float(match.group(3))
    issues: List[str] = []
    if total != EXPECTED_ATTEMPTS:
        issues.append(f"共作答 {total} 题，按示例 2+3 应为 {EXPECTED_ATTEMPTS} 题")
    if correct != EXPECTED_CORRECT:
        issues.append(f"答对 {correct} 题，应为 {EXPECTED_CORRECT} 题")
    if total and abs(rate - round(correct / total * 100, 1)) > 0.05:
        issues.append(f"正确率 {rate}% 与 {correct}/{total} 不一致")
    return issues


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


# ---------------------------------------------------------------------------
# 断言二：故事线数据完整性
# ---------------------------------------------------------------------------


def check_storylines() -> Tuple[List[str], int, int]:
    """校验每条 available 故事线的关卡数据。

    检查项（任何一项不满足都算 FAIL）：

    * 每条线 9 关、每关 9 难，共 81 个 level；
    * ``level_id`` 命名合规：默认线是 ``<stage>-<difficulty>``，其他线是 ``<story>-<stage>-<difficulty>``；
    * ``brief`` 非空、不超过 30 字、线内不重复；
    * ``kp_id`` 必须命中 ``data/curriculum.json``；
    * 关名非空且线内不重复；``roles`` 四类角色齐全；``era_note`` 非空。

    Returns:
        ``(问题列表, 故事线数, 关卡总数)``。
    """
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    import problem_gen  # noqa: E402  (延迟导入，避免影响 --help)

    problems: List[str] = []
    kp_ids = set(problem_gen.kp_index().keys())
    stories = problem_gen.list_storylines(only_available=True)
    total_levels = 0
    if not stories:
        return ["index.json 里没有任何 available 的故事线"], 0, 0

    default_id = problem_gen.default_story()
    for item in stories:
        sid = item["id"]
        try:
            data = problem_gen.load_storyline(sid)
        except Exception as exc:  # 文件缺失 / JSON 坏掉
            problems.append(f"{sid}：读取失败 —— {exc}")
            continue

        era_note = str(data.get("era_note") or "").strip()
        if not era_note:
            problems.append(f"{sid}：缺少 era_note（取材来源说明）")
        roles = data.get("roles") or {}
        missing_roles = [key for key in ("clever", "price", "unit", "review") if not roles.get(key)]
        if missing_roles:
            problems.append(f"{sid}：roles 缺少 {missing_roles}")

        stages = data.get("stages") or []
        if len(stages) != 9:
            problems.append(f"{sid}：关卡数 {len(stages)}，应为 9")
        seen_names: List[str] = []
        seen_briefs: List[str] = []
        for order, stage in enumerate(stages, start=1):
            name = str(stage.get("name") or "").strip()
            if not name:
                problems.append(f"{sid} 第 {order} 关：关名为空")
            elif name in seen_names:
                problems.append(f"{sid} 第 {order} 关：关名重复（{name}）")
            seen_names.append(name)

            stage_no = int(stage.get("stage") or order)
            levels = stage.get("levels") or []
            if len(levels) != 9:
                problems.append(f"{sid} 第 {order} 关：难度数 {len(levels)}，应为 9")
            for level in levels:
                total_levels += 1
                level_id = str(level.get("level_id") or "")
                kp_id = str(level.get("kp_id") or "")
                brief = str(level.get("brief") or "").strip()
                if kp_id not in kp_ids:
                    problems.append(f"{sid} {level_id}：kp_id {kp_id!r} 不在 curriculum.json 里")
                if not brief:
                    problems.append(f"{sid} {level_id}：brief 为空")
                elif len(brief) > 30:
                    problems.append(f"{sid} {level_id}：brief 超长（{len(brief)} 字）")
                if brief in seen_briefs:
                    problems.append(f"{sid} {level_id}：brief 线内重复（{brief}）")
                seen_briefs.append(brief)

                difficulty = str(level.get("level_id") or "").split("-")[-1]
                expect = (
                    f"{stage_no}-{difficulty}" if sid == default_id else f"{sid}-{stage_no}-{difficulty}"
                )
                if level_id != expect:
                    problems.append(f"{sid} {level_id}：level_id 不符合约定，应为 {expect}")
    return problems, len(stories), total_levels


# ---------------------------------------------------------------------------
# 断言三：演示页内联题库与 demo/bank.json 一致
# ---------------------------------------------------------------------------


def embedded_bank() -> Any:
    """从 ``demo/index.html`` 的标记区取出内联题库对象。"""
    html = DEMO_INDEX.read_text(encoding="utf-8")
    if BANK_START not in html or BANK_END not in html:
        raise ValueError(f"demo/index.html 缺少 {BANK_START} / {BANK_END} 标记")
    middle = html.split(BANK_START, 1)[1].split(BANK_END, 1)[0].strip()
    if not middle.startswith("var BANK = "):
        raise ValueError("demo/index.html 标记区里不是 var BANK = {...};")
    text = middle[len("var BANK = "):].strip()
    if text.endswith(";"):
        text = text[:-1]
    return json.loads(text)


def check_demo_bank() -> Tuple[List[str], int]:
    """校验演示页题库：内联 == bank.json，且每条线的题目结构与答案合法。

    Returns:
        ``(问题列表, 题目总数)``。
    """
    problems: List[str] = []
    if not DEMO_BANK.exists():
        return [f"缺少 {DEMO_BANK.name}，请先运行 scripts/export_demo_bank.py"], 0
    try:
        inline = embedded_bank()
    except (ValueError, json.JSONDecodeError) as exc:
        return [f"读取内联题库失败 —— {exc}"], 0
    on_disk = json.loads(DEMO_BANK.read_text(encoding="utf-8"))
    if inline != on_disk:
        problems.append("demo/index.html 内联题库与 demo/bank.json 不一致（请重跑 export_demo_bank.py）")

    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    import problem_gen  # noqa: E402

    available = problem_gen.story_ids(only_available=True)
    bank = on_disk.get("bank") or {}
    if sorted(bank.keys()) != sorted(available):
        problems.append(f"题库覆盖的故事线 {sorted(bank.keys())} 与可用线 {sorted(available)} 不一致")

    total = 0
    for sid in available:
        items = bank.get(sid) or []
        if len(items) != 9:
            problems.append(f"{sid}：演示题库 {len(items)} 题，应为 9")
        stages_seen = set()
        for problem in items:
            total += 1
            level_id = str(problem.get("level_id"))
            if not level_id.startswith(f"{sid}-") and sid != problem_gen.default_story():
                problems.append(f"{sid} {level_id}：level_id 前缀与故事线不符")
            if int(problem.get("stage") or 0) in stages_seen:
                problems.append(f"{sid} {level_id}：同一关重复出现")
            stages_seen.add(int(problem.get("stage") or 0))
            answer = problem.get("answer")
            if not isinstance(answer, (int, float)) or isinstance(answer, bool):
                problems.append(f"{sid} {level_id}：answer 不是数值（{answer!r}）")
            if len(problem.get("hints") or []) != 3:
                problems.append(f"{sid} {level_id}：提示不是 3 条")
            if len(problem.get("steps") or []) < 2:
                problems.append(f"{sid} {level_id}：讲解步骤少于 2 条")
            table = problem.get("table") or {}
            if not table.get("columns") or not table.get("rows"):
                problems.append(f"{sid} {level_id}：数据表为空")
            if not str(problem.get("question") or "").strip():
                problems.append(f"{sid} {level_id}：题干为空")
    return problems, total


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
    #
    # 因为每次都是全新建库，断言就与调用者的历史进度完全无关 —— 首跑、二跑、跑一百次
    # 结果都一样（这是「幂等」的根治法：不依赖历史状态，也不靠预热）。
    tmpdir = tempfile.mkdtemp(prefix="mmqj-selfcheck-")
    try:
        db_dir = Path(tmpdir) / "runtime"
        db_dir.mkdir(parents=True, exist_ok=True)
        outputs = collect_outputs(str(db_dir / "progress.db"))
    finally:
        # 清理失败（例如系统装了 safe-delete 拦截）不能影响校验结论，也不能留脏目录
        shutil.rmtree(tmpdir, ignore_errors=True)

    problems: List[Dict[str, Any]] = []
    for block, first_line, output in zip(blocks, starts, outputs):
        problems.extend(compare_block(block, output, first_line))

    # 累计量再独立验一遍算术（字面 diff 之外的第二道网）
    for issue in numeric_consistency(outputs[4]):
        problems.append({"line_no": starts[4], "expect": "周报累计量自洽", "actual": issue})

    story_issues, story_count, story_levels = check_storylines()
    for issue in story_issues:
        problems.append(
            {"line_no": 0, "expect": "故事线数据完整（9 关 × 9 难 / kp 命中 / brief 不重复）", "actual": issue}
        )

    bank_issues, bank_total = check_demo_bank()
    for issue in bank_issues:
        problems.append(
            {"line_no": 0, "expect": "demo 内联题库与 bank.json 一致", "actual": issue}
        )

    if not problems:
        total_lines = sum(len(block.splitlines()) for block in blocks)
        print(
            f"DOC_SYNC: OK —— 5 段示例共 {total_lines} 行与当前代码一致；"
            f"周报累计量自洽（{EXPECTED_ATTEMPTS} 题 / 答对 {EXPECTED_CORRECT} / "
            f"{round(EXPECTED_CORRECT / EXPECTED_ATTEMPTS * 100, 1)}%）；"
            f"故事线 {story_count} 条 × {story_levels // max(story_count, 1)} 关校验通过；"
            f"demo 题库 {bank_total} 题与 bank.json 一致"
        )
        return 0

    print(f"DOC_SYNC: FAIL —— 发现 {len(problems)} 处文档与代码不一致：")
    for item in problems:
        if item["line_no"]:
            print(f"  README:{item['line_no']}")
        else:
            print("  [数据校验]")
        print(f"    期望: {item['expect']}")
        print(f"    实际: {item['actual']}")
    return 1


if __name__ == "__main__":  # pragma: no cover - 脚本入口
    raise SystemExit(main())
