"""麦麦取经录 —— 命令行入口。

用法::

    python scripts/cli.py stories                # 列出全部故事线（可插拔注册表）
    python scripts/cli.py stages                 # 列出所有线的关名摘要
    python scripts/cli.py stages --story sanguo  # 列出某条线的 9 关与通关状态
    python scripts/cli.py play --level 3-2       # 玩一关（默认西游记）
    python scripts/cli.py play --story sanguo --level sanguo-3-2
    python scripts/cli.py play --grade 4 --count 5
    python scripts/cli.py wrongbook              # 错题本（全量，标注来源线）
    python scripts/cli.py report                 # 家长周报
    python scripts/cli.py doctor                 # 环境自检

``--story`` 省略时一律使用注册表里的默认线（``xiyou``），行为与改造前完全一致。
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


def _stage_key(story_id: str, stage: int) -> str:
    """通关记录的键：默认线沿用 ``"3"``（兼容老库），其他线用 ``"sanguo-3"``。

    ``stage_clear`` 的主键是 ``level_id``，如果五条线都用 ``"3"`` 会互相覆盖，
    所以非默认线必须带线前缀。
    """
    if str(story_id) == problem_gen.default_story():
        return str(int(stage))
    return f"{story_id}-{int(stage)}"


def _render_stage_map(story: Optional[str] = None) -> str:
    """渲染某条故事线的九关地图（含通关状态）。"""
    sid = str(story or problem_gen.default_story())
    data = problem_gen.load_storyline(sid)
    clears = progress.get_stage_clears(sid)
    cleared_count = len(clears)
    lines: List[str] = []
    lines.append(_rule("═"))
    lines.append(f"  {data.get('name', sid)} · 九关八十一难")
    if data.get("subtitle"):
        lines.append(f"  {data['subtitle']}　｜　故事线 id：{sid}")
    lines.append("  题目里的数字都是真的，答案由麦当劳 MCP 真实计算校验")
    lines.append(_rule("═"))
    for stage in problem_gen.list_stages(sid):
        key = _stage_key(sid, stage["stage"])
        cleared = clears.get(key)
        if cleared:
            mark = "★" * int(cleared["stars"]) + "☆" * (3 - int(cleared["stars"]))
            status = f"已通关 {mark}"
        else:
            stats = progress.stage_accuracy(stage["stage"], sid)
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
    lines.append(f"  已通关 {cleared_count}/9 关　｜　故事线：{data.get('name', sid)}（{sid}）")
    if sid == problem_gen.default_story():
        lines.append("  用法：python scripts/cli.py play --level 3-2")
    else:
        lines.append(f"  用法：python scripts/cli.py play --story {sid} --level {sid}-3-2")
    lines.append(f"  {DISCLAIMER}")
    return "\n".join(lines)


def _render_story_overview() -> str:
    """渲染所有已上线故事线的摘要（每条线一行 + 关名一行）。"""
    items = problem_gen.list_storylines(only_available=True)
    default_id = problem_gen.default_story()
    lines: List[str] = []
    lines.append(_rule("═"))
    lines.append("  麦麦取经录 · 故事线总览")
    lines.append("  题目里的数字都是真的，答案由麦当劳 MCP 真实计算校验")
    lines.append(_rule("═"))
    names = {item["id"]: item["name"] for item in items}
    lines.append(
        f"  可用故事线 {len(items)} 条（默认 {names.get(default_id, default_id)} / {default_id}）"
    )
    lines.append("  所有线共用同一套 9 个题型模板，只换关名、角色与题面文案；数学逻辑完全一致。")
    lines.append("")
    for item in items:
        stages = problem_gen.list_stages(item["id"])
        level_count = sum(int(stage["level_count"]) for stage in stages)
        cleared = len(progress.get_stage_clears(item["id"]))
        lines.append(
            f"  {_pad(item['name'], 12)}{_pad(item['id'], 10)}"
            f"{len(stages)} 关 / {level_count} 难　已通关 {cleared}/{len(stages)}"
        )
        lines.append("      " + " · ".join(str(stage["name"]) for stage in stages))
    lines.append(_rule("═"))
    lines.append("  用法：python scripts/cli.py stages --story sanguo")
    lines.append("        python scripts/cli.py play --story sanguo --level sanguo-3-2")
    lines.append(f"  {DISCLAIMER}")
    return "\n".join(lines)


def _print_problem(problem: Dict[str, Any]) -> None:
    """打印题目（含数据表与元信息）。

    年级与所属关分开写清楚：年级取「知识点所属年级」（也是 --grade 的筛选口径），
    所属关只是它在取经路线上的位置，两者可以不一致（同一关的 9 难允许跨年级）。
    """
    print()
    print(_rule("═"))
    difficulty = str(problem["level_id"]).split("-")[-1]
    print(f"  第 {problem['stage']} 关 · {problem['stage_name']}　第 {difficulty} 难")
    print(
        f"  年级：{problem['grade']} 年级（知识点所属）"
        f"｜所属关：第 {problem['stage']} 关 {problem['stage_name']}"
    )
    if problem.get("story_id") and problem["story_id"] != problem_gen.default_story():
        print(f"  故事线：{problem.get('story_name', '')}（{problem['story_id']}）")
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


def cmd_stages(args: argparse.Namespace) -> int:
    """列出关卡：不传 --story 时给出所有线的摘要，传了则给该线 9 关详情。"""
    story = getattr(args, "story", None)
    print(_render_stage_map(story) if story else _render_story_overview())
    return 0


def cmd_stories(_: argparse.Namespace) -> int:
    """列出故事线注册表（含未上线的线）。"""
    print()
    print(_rule("═"))
    print("  故事线列表（data/storylines/index.json）")
    print(_rule("═"))
    for item in problem_gen.list_storylines(only_available=False):
        stages = problem_gen.list_stages(item["id"]) if item["available"] else []
        level_count = sum(int(stage["level_count"]) for stage in stages)
        status = "已上线" if item["available"] else "未上线（不注册）"
        print(
            f"  {_pad(item['id'], 10)}{_pad(item['name'], 12)}"
            f"{_pad(f'{len(stages)} 关 / {level_count} 难', 14)}{status}"
        )
        if item["subtitle"]:
            print(f"      {item['subtitle']}")
    print(_rule("═"))
    default_id = problem_gen.default_story()
    print(f"  默认故事线：{problem_gen.storyline_name(default_id)}（{default_id}）")
    lines_available = "、".join(problem_gen.story_ids(only_available=True))
    print(f"  可用：{lines_available}")
    print("  说明：available=false 的线不会出现在 stages / demo 里，也不会被出题注册。")
    print(f"  {DISCLAIMER}")
    return 0


def cmd_play(args: argparse.Namespace) -> int:
    """交互式答题。"""
    count = max(1, int(args.count))
    level_id: Optional[str] = args.level
    story = getattr(args, "story", None)
    sid = str(story or problem_gen.default_story())
    problems: List[Dict[str, Any]] = []

    if level_id:
        problems.append(problem_gen.generate(level_id=level_id, salt=args.seed, story=story))
    else:
        for index in range(count):
            problems.append(
                problem_gen.generate(
                    grade=args.grade,
                    kp_id=args.kp,
                    salt=None if args.seed is None else f"{args.seed}-{index}",
                    story=sid,
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
                story=str(problem.get("story_id") or problem_gen.default_story()),
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

        stats = progress.stage_accuracy(
            problem["stage"], str(problem.get("story_id") or problem_gen.default_story())
        )
        if answered_correctly and stats["correct_levels"] >= 9:
            story_id = str(problem.get("story_id") or problem_gen.default_story())
            stars = progress.clear_stage(
                _stage_key(story_id, problem["stage"]),
                stats["accuracy"],
                story=story_id,
            )
            print(f"  第 {problem['stage']} 关通关！获得 {'★' * stars}{'☆' * (3 - stars)}")
        print(_rule("═"))

    print(f"  本次完成 {len(problems)} 题，答对 {solved} 题。")
    print(f"  {DISCLAIMER}")
    return 0


def cmd_wrongbook(args: argparse.Namespace) -> int:
    """打印错题本。"""
    story = getattr(args, "story", None)
    entries = progress.get_wrong_book(args.kp, story=story)
    names = {key: value["name"] for key, value in problem_gen.kp_index().items()}
    print()
    print(_rule("═"))
    print("  错题本")
    print(_rule("═"))
    if story:
        print(f"  故事线：{problem_gen.storyline_name(story)}（{story}）")
    else:
        counts: Dict[str, int] = {}
        for entry in entries:
            counts[entry["story"]] = counts.get(entry["story"], 0) + 1
        summary = "、".join(
            f"{problem_gen.storyline_name(sid)} {count} 条"
            for sid, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        )
        print(f"  故事线：全部{('（' + summary + '）') if summary else ''}")
    if not entries:
        print("  还没有错题记录。先玩一关吧：python scripts/cli.py play --level 1-1")
    else:
        default_id = problem_gen.default_story()
        for entry in entries:
            # 已按 --story 过滤时不再重复标注来源线；全量汇总时才加括号说明。
            show_marker = not story and entry["story"] != default_id
            marker = f"　（{problem_gen.storyline_name(entry['story'])}）" if show_marker else ""
            print(
                f"  第 {entry['level_id']} 难　{names.get(entry['kp_id'], entry['kp_id'])}"
                f"　错过 {entry['wrong_count']} 次　最近 {entry['last_time']}{marker}"
            )
            print(f"      上次作答：{entry['last_answer'] or '（空）'}　参考答案：{entry['expected']}")
    print(_rule("═"))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    """打印家长周报。"""
    print()
    print(progress.weekly_report(days=args.days, story=getattr(args, "story", None)))
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

    available = problem_gen.list_storylines(only_available=True)
    all_lines = problem_gen.list_storylines(only_available=False)
    print(
        f"  故事线：已注册 {len(all_lines)} 条，可用 {len(available)} 条"
        f"（{'、'.join(item['id'] for item in available)}）"
    )
    print(f"    默认故事线：{problem_gen.storyline_name()}（{problem_gen.default_story()}）")

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
    """构造参数解析器。

    ``--story`` 的可选值从 ``data/storylines/index.json`` 动态读取（只列已上线的线），
    默认 ``None`` 表示「没指定」：``stages`` 用它区分「总览」与「单线详情」，
    其余子命令回落到注册表里的默认线（西游记），保证与改造前行为一致。
    """
    parser = argparse.ArgumentParser(
        prog="cli.py",
        description="麦麦取经录：用麦当劳 MCP 真实数据出题、校验、讲解的小学数学闯关工具。",
    )
    sub = parser.add_subparsers(dest="command")

    stories_choices = problem_gen.story_ids(only_available=True)

    def add_story(p: argparse.ArgumentParser, help_text: str) -> None:
        p.add_argument("--story", default=None, choices=stories_choices, help=help_text)

    stages = sub.add_parser("stages", help="列出关卡：不带 --story 给出所有线摘要，带则给单线 9 关")
    add_story(stages, "只看某条故事线，如 sanguo；不填则汇总所有已上线线")
    stages.set_defaults(func=cmd_stages)

    stories = sub.add_parser("stories", help="列出故事线注册表（含未上线）")
    stories.set_defaults(func=cmd_stories)

    play = sub.add_parser("play", help="玩一关（出题 → 提示 → 作答 → 校验 → 讲解）")
    add_story(play, f"选择故事线，默认 {problem_gen.default_story()}")
    play.add_argument("--level", default=None, help="关卡号，如 3-2 或 sanguo-3-2；不填则随机")
    play.add_argument("--grade", type=int, default=None, choices=[3, 4, 5, 6], help="按年级随机出题")
    play.add_argument("--kp", default=None, help="按知识点出题，如 g3_mul_basic")
    play.add_argument("--count", type=int, default=1, help="连续出几题，默认 1")
    play.add_argument("--seed", default=None, help="随机种子，便于复现同一道题")
    play.set_defaults(func=cmd_play)

    wrongbook = sub.add_parser("wrongbook", help="查看错题本")
    add_story(wrongbook, "只看某条故事线的错题；不填则全量汇总")
    wrongbook.add_argument("--kp", default=None, help="只看某个知识点")
    wrongbook.set_defaults(func=cmd_wrongbook)

    report = sub.add_parser("report", help="生成家长周报")
    add_story(report, "只统计某条故事线；不填则全量汇总")
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
