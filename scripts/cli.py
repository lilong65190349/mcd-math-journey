"""麦麦取经录 —— 命令行入口。

用法::

    python scripts/cli.py stages                 # 列出 9 关与通关状态
    python scripts/cli.py play --level 3-2       # 玩一关
    python scripts/cli.py play --grade 4 --count 5
    python scripts/cli.py wrongbook              # 错题本
    python scripts/cli.py report                 # 家长周报
    python scripts/cli.py doctor                 # 环境自检

没有 ``MCD_MCP_TOKEN`` 时也能完整跑通：数据会自动降级到本地缓存或示例快照，
并在开头明确告知当前数据级别。
"""

from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional

if __package__ in (None, ""):  # pragma: no cover - 脚本运行路径
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import data_source  # noqa: E402
import problem_gen  # noqa: E402
import progress  # noqa: E402
import verify  # noqa: E402
from mcp_client import READONLY_WHITELIST, TOKEN_ENV  # noqa: E402

PROJECT_ROOT: Path = Path(__file__).resolve().parents[1]
SAMPLE_PATH: Path = PROJECT_ROOT / "demo" / "sample_data.json"
CACHE_DIR: Path = PROJECT_ROOT / "data" / "cache"

MAX_HINTS: int = 3
MAX_TRIES: int = 3

DISCLAIMER: str = (
    "非麦当劳官方产品，输出仅供参考，餐品信息与价格以麦当劳官方渠道实时结果为准。"
)


# ---------------------------------------------------------------------------
# 展示辅助
# ---------------------------------------------------------------------------


def _rule(char: str = "─", width: int = 66) -> str:
    """输出一条分隔线。"""
    return char * width


def _display_width(text: Any) -> int:
    """计算字符串在等宽终端里的显示宽度（中日韩字符按 2 计）。"""
    width = 0
    for char in str(text):
        width += 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
    return width


def _pad(text: Any, width: int) -> str:
    """按显示宽度左对齐补空格。"""
    return str(text) + " " * max(0, width - _display_width(text))


def _source_banner(source: str) -> str:
    """把数据级别渲染成一句醒目提示。"""
    label = data_source.SOURCE_LABELS.get(source, "未知数据级别")
    if source == data_source.SOURCE_MCP:
        return f"数据级别：{label} —— 题目里的数字来自实时接口。"
    if source == data_source.SOURCE_CACHE:
        return f"数据级别：{label} —— 未连上 MCP，使用最近一次缓存结果。"
    return f"数据级别：{label} —— 未配置 MCP 令牌或网络不通，实际运行会替换为实时数据。"


def _render_stage_map() -> str:
    """渲染九关地图（含通关状态）。"""
    clears = progress.get_stage_clears()
    lines: List[str] = []
    lines.append(_rule("═"))
    lines.append("  麦麦取经录 · 九关八十一难")
    lines.append("  题目里的数字都是真的，答案由麦当劳 MCP 真实计算校验")
    lines.append(_rule("═"))
    for stage in problem_gen.list_stages():
        key = str(stage["stage"])
        cleared = clears.get(key)
        if cleared:
            mark = "★" * int(cleared["stars"]) + "☆" * (3 - int(cleared["stars"]))
            status = f"已通关 {mark}"
        else:
            stats = progress.stage_accuracy(stage["stage"])
            if stats["attempts"]:
                status = f"进行中（{stats['correct_levels']}/{stage['level_count']} 难已答对）"
            else:
                status = "未开始"
        lines.append(
            f"  第 {stage['stage']} 关  {_pad(stage['name'], 12)}"
            f"（{stage['grade_hint']} 年级 / {stage['level_count']} 难）  {status}"
        )
        lines.append(f"          主题：{stage['theme']}")
    lines.append(_rule("═"))
    lines.append("  用法：python scripts/cli.py play --level 3-2")
    lines.append(f"  {DISCLAIMER}")
    return "\n".join(lines)


def _print_problem(problem: Dict[str, Any]) -> None:
    """打印题目（含数据表与元信息）。

    年级与所属关分开写清楚：年级取「知识点所属年级」（也是 --grade 的筛选口径），
    所属关只是它在取经路线上的位置，两者可以不一致（同一关的 9 难允许跨年级）。
    """
    print()
    print(_rule("═"))
    print(f"  第 {problem['stage']} 关 · {problem['stage_name']}　第 {problem['level_id'].split('-')[1]} 难")
    print(
        f"  年级：{problem['grade']} 年级（知识点所属）"
        f"｜所属关：第 {problem['stage']} 关 {problem['stage_name']}"
    )
    print(f"  知识点：{problem['kp_name']}")
    print(f"  {_source_banner(problem['data_source'])}")
    print(_rule("─"))
    print(f"  {problem['story']}")
    print()
    table = problem["data_table"]
    print(f"  {table['title']}：")
    all_rows = [list(table["columns"])] + [list(row) for row in table["rows"]]
    widths = [
        max(_display_width(row[index]) for row in all_rows) + 2
        for index in range(len(table["columns"]))
    ]
    print("    " + "".join(_pad(col, widths[index]) for index, col in enumerate(table["columns"])).rstrip())
    print("    " + "-" * (sum(widths) - 2))
    for row in table["rows"]:
        print("    " + "".join(_pad(cell, widths[index]) for index, cell in enumerate(row)).rstrip())
    print()
    print(f"  题目：{problem['question']}")
    print(_rule("─"))
    print("  输入答案后回车；h = 求提示（最多 3 次），s = 跳过，q = 退出")
    print(_rule("═"))


def _print_result(result: Dict[str, Any]) -> None:
    """打印校验结果。"""
    if result["correct"]:
        print()
        print(f"  正确！参考答案 {result['expected']}，你的答案 {result['actual']}。")
        print(f"  校验来源：{verify.verify_source_label(result['verify_source'])}")
    else:
        print()
        print(f"  还不对。{result['diagnosis']}")
        print(f"  参考答案：{result['expected']}")


# ---------------------------------------------------------------------------
# 子命令
# ---------------------------------------------------------------------------


def cmd_stages(_: argparse.Namespace) -> int:
    """列出九关与通关状态。"""
    print(_render_stage_map())
    return 0


def cmd_play(args: argparse.Namespace) -> int:
    """交互式答题。"""
    count = max(1, int(args.count))
    level_id: Optional[str] = args.level
    problems: List[Dict[str, Any]] = []

    if level_id:
        problems.append(problem_gen.generate(level_id=level_id, salt=args.seed))
    else:
        for index in range(count):
            problems.append(
                problem_gen.generate(
                    grade=args.grade,
                    kp_id=args.kp,
                    salt=None if args.seed is None else f"{args.seed}-{index}",
                )
            )

    first = problems[0]
    print()
    print(f"  数据级别总览：")
    for name, info in data_source.status().items():
        print(f"    {name:<16}{info['label']}（{info['count']} 条）")
    print()

    solved = 0
    for problem in problems:
        _print_problem(problem)
        hint_index = 0
        tries = 0
        answered_correctly = False
        while tries < MAX_TRIES:
            try:
                raw = input("  你的答案 > ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n  已退出。")
                return 0
            if raw.lower() in ("q", "quit", "exit"):
                print("  已退出。")
                return 0
            if raw.lower() in ("s", "skip"):
                print("  已跳过。")
                break
            if raw.lower() in ("h", "hint", "提示"):
                if hint_index < min(MAX_HINTS, len(problem["hints"])):
                    print(f"  提示 {hint_index + 1}：{problem['hints'][hint_index]}")
                    hint_index += 1
                else:
                    print("  三级提示已经全部给出，试着自己写一写。")
                continue
            if not raw:
                print("  请输入一个数值，或输入 h 求提示。")
                continue

            tries += 1
            result = verify.check_answer(problem, raw)
            progress.record_attempt(
                level_id=problem["level_id"],
                kp_id=problem["kp_id"],
                user_answer=raw,
                expected=result["expected"],
                correct=bool(result["correct"]),
                verify_source=str(result["verify_source"]),
            )
            if result["correct"]:
                print()
                print(f"  正确！参考答案 {result['expected']} {problem['unit']}。")
                print(f"  校验来源：{verify.verify_source_label(result['verify_source'])}")
                answered_correctly = True
                solved += 1
                break
            print()
            print(f"  第 {tries} 次还不对。{result['diagnosis']}")
            if tries < MAX_TRIES:
                print("  再想一想，也可以输入 h 求提示。")

        print()
        print(verify.explain(problem))
        print()

        stats = progress.stage_accuracy(problem["stage"])
        if answered_correctly and stats["correct_levels"] >= 9:
            stars = progress.clear_stage(str(problem["stage"]), stats["accuracy"])
            print(f"  第 {problem['stage']} 关通关！获得 {'★' * stars}{'☆' * (3 - stars)}")
        print(_rule("═"))

    print(f"  本次完成 {len(problems)} 题，答对 {solved} 题。")
    print(f"  {DISCLAIMER}")
    return 0


def cmd_wrongbook(args: argparse.Namespace) -> int:
    """打印错题本。"""
    entries = progress.get_wrong_book(args.kp)
    names = {key: value["name"] for key, value in problem_gen.kp_index().items()}
    print()
    print(_rule("═"))
    print("  错题本")
    print(_rule("═"))
    if not entries:
        print("  还没有错题记录。先玩一关吧：python scripts/cli.py play --level 1-1")
    else:
        for entry in entries:
            print(
                f"  第 {entry['level_id']} 难　{names.get(entry['kp_id'], entry['kp_id'])}"
                f"　错过 {entry['wrong_count']} 次　最近 {entry['last_time']}"
            )
            print(f"      上次作答：{entry['last_answer'] or '（空）'}　参考答案：{entry['expected']}")
    print(_rule("═"))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    """打印家长周报。"""
    print()
    print(progress.weekly_report(days=args.days))
    return 0


def cmd_doctor(_: argparse.Namespace) -> int:
    """环境自检：Python 版本、令牌、MCP 连通性、缓存与示例数据。"""
    print()
    print(_rule("═"))
    print("  环境自检 doctor")
    print(_rule("═"))

    version_ok = sys.version_info >= (3, 10)
    print(f"  Python 版本：{sys.version.split()[0]} {'OK' if version_ok else '需要 3.10 及以上'}")
    print(f"  标准库依赖：无第三方包（requests / mcp SDK 均未使用）")

    client = data_source.get_client()
    token_ok = client.has_token()
    print(f"  环境变量 {TOKEN_ENV}：{'已设置' if token_ok else '未设置'}")

    if token_ok:
        try:
            info = client.initialize()
            server = info.get("serverInfo") or {}
            name = server.get("name", "未知服务") if isinstance(server, dict) else "未知服务"
            print(f"  MCP 连通性：OK（服务 {name}，协议 {info.get('protocolVersion', '未知')}）")
            tools = client.list_tools()
            allowed = [tool for tool in tools if tool in READONLY_WHITELIST]
            print(f"  可用工具：共 {len(tools)} 个，其中命中只读白名单 {len(allowed)} 个")
            for tool in allowed:
                print(f"    - {tool}")
        except Exception as exc:  # 自检要吞掉异常，只报告
            print(f"  MCP 连通性：失败 —— {exc}")
    else:
        print("  MCP 连通性：跳过（未设置令牌，将自动降级到缓存 / 示例数据）")

    cache_files = sorted(CACHE_DIR.glob("*.json")) if CACHE_DIR.exists() else []
    print(f"  本地缓存：{len(cache_files)} 个文件（目录 {CACHE_DIR}）")
    for path in cache_files:
        print(f"    - {path.name}")

    if SAMPLE_PATH.exists():
        try:
            with SAMPLE_PATH.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
            keys = ", ".join(sorted(payload.keys()))
            print(f"  示例数据：OK（{SAMPLE_PATH.name}，字段 {keys}）")
        except json.JSONDecodeError as exc:
            print(f"  示例数据：解析失败 —— {exc}")
    else:
        print(f"  示例数据：缺失（{SAMPLE_PATH}）")

    try:
        summary = progress.get_summary()
        print(f"  进度数据库：OK（{progress.DB_PATH}，累计 {summary['total_attempts']} 条记录）")
    except Exception as exc:  # pragma: no cover - 自检要吞掉异常
        print(f"  进度数据库：不可写 —— {exc}")

    print()
    print("  数据源当前级别：")
    for name, info in data_source.status().items():
        print(f"    {name:<16}{info['source']:<8}{info['count']} 条")
    print()
    print(f"  {DISCLAIMER}")
    print(_rule("═"))
    return 0


# ---------------------------------------------------------------------------
# 参数解析
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """构造参数解析器。"""
    parser = argparse.ArgumentParser(
        prog="cli.py",
        description="麦麦取经录：用麦当劳 MCP 真实数据出题、校验、讲解的小学数学闯关工具。",
    )
    sub = parser.add_subparsers(dest="command")

    stages = sub.add_parser("stages", help="列出 9 关与通关状态")
    stages.set_defaults(func=cmd_stages)

    play = sub.add_parser("play", help="玩一关（出题 → 提示 → 作答 → 校验 → 讲解）")
    play.add_argument("--level", default=None, help="关卡号，如 3-2；不填则随机")
    play.add_argument("--grade", type=int, default=None, choices=[3, 4, 5, 6], help="按年级随机出题")
    play.add_argument("--kp", default=None, help="按知识点出题，如 g3_mul_basic")
    play.add_argument("--count", type=int, default=1, help="连续出几题，默认 1")
    play.add_argument("--seed", default=None, help="随机种子，便于复现同一道题")
    play.set_defaults(func=cmd_play)

    wrongbook = sub.add_parser("wrongbook", help="查看错题本")
    wrongbook.add_argument("--kp", default=None, help="只看某个知识点")
    wrongbook.set_defaults(func=cmd_wrongbook)

    report = sub.add_parser("report", help="生成家长周报")
    report.add_argument("--days", type=int, default=7, help="统计最近多少天，默认 7")
    report.set_defaults(func=cmd_report)

    doctor = sub.add_parser("doctor", help="环境自检")
    doctor.set_defaults(func=cmd_doctor)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """命令行主入口。"""
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    try:
        return int(args.func(args))
    except KeyError as exc:
        # KeyError 的 str() 会给消息再套一层引号，这里取原始消息，避免出现
        # 「参数错误："未找到关卡 '99-9'。"」这种双层引号。
        message = exc.args[0] if exc.args else exc
        print(f"参数错误：{message}")
        return 2
    except ValueError as exc:
        print(f"数据错误：{exc}")
        return 2


if __name__ == "__main__":  # pragma: no cover - 脚本入口
    raise SystemExit(main())
