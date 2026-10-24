"""麦麦取经录 —— 生成离线演示页题库（demo/bank.json + 注入 index.html）。

为什么要有这个脚本
------------------
演示页是**单文件、零外部依赖、双击可开**的：它不能 ``fetch('bank.json')``，
所以题库必须内联在 HTML 里。手抄 45 道题既容易抄错，也没法和 Python 端出题逻辑保持一致。

本脚本的做法：

1. 用 ``problem_gen.generate()`` 为**每条 available 的故事线**各生成 9 道题
   （每关取第 1 难，固定 seed，可复现）；
2. 把题目投影成演示页需要的字段（``table`` / ``meta`` 等命名差异在这里抹平）；
3. 写 ``demo/bank.json``（给自检用，人可读）；
4. 把同一份数据压缩成一行注入 ``demo/index.html`` 的
   ``/* @@BANK_START@@ */`` 与 ``/* @@BANK_END@@ */`` 之间。

自检脚本会比对「注入的内容」与 ``bank.json`` 是否逐题一致，防止有人只改了其中一边。

用法::

    python scripts/export_demo_bank.py
"""

from __future__ import annotations

import json
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List

if __package__ in (None, ""):  # pragma: no cover - 脚本运行路径
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import problem_gen  # noqa: E402

PROJECT_ROOT: Path = Path(__file__).resolve().parents[1]
DEMO_DIR: Path = PROJECT_ROOT / "demo"
BANK_PATH: Path = DEMO_DIR / "bank.json"
HTML_PATH: Path = DEMO_DIR / "index.html"

BANK_START = "/* @@BANK_START@@ */"
BANK_END = "/* @@BANK_END@@ */"

CARE_NOTE: str = problem_gen.CARE_NOTE

_DIAGNOSIS_ALIAS = {"average": "avg"}


def _num(value: Any) -> Any:
    """把 ``"25.50"`` 这类字符串转成 JS 可直接参与运算的 float（转不了就原样返回）。

    演示页的错因诊断会用 ``meta`` 里的数做加减乘除，字符串会变成字符串拼接，
    所以导出时必须先数值化。
    """
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value
    text = str(value).strip()
    if not text:
        return value
    try:
        number = Decimal(text)
    except InvalidOperation:
        return value
    if number == number.to_integral_value():
        return int(number)
    return float(number)


def _meta(diagnosis: Dict[str, Any]) -> Dict[str, Any]:
    """把出题端的 ``diagnosis`` 投影成演示页的 ``meta``。"""
    payload: Dict[str, Any] = {}
    for key, value in (diagnosis or {}).items():
        if key == "type":
            kind = str(value)
            payload[key] = _DIAGNOSIS_ALIAS.get(kind, kind)
        elif key == "terms" and isinstance(value, list):
            payload[key] = [_num(item) for item in value]
        else:
            payload[key] = _num(value)
    return payload


def _answer_number(problem: Dict[str, Any]) -> float:
    """答案转成演示页比较用的数值（百分数只写数值，单位单独放 ``unit``）。"""
    text = str(problem.get("answer", "")).replace("%", "").strip()
    return float(Decimal(text))


def project(problem: Dict[str, Any]) -> Dict[str, Any]:
    """把 ``generate()`` 的题目投影成演示页字段。"""
    table = problem.get("data_table") or {}
    steps = [str(step) for step in problem.get("steps") or [] if str(step).strip() != CARE_NOTE]
    return {
        "level_id": str(problem["level_id"]),
        "stage": int(problem["stage"]),
        "stageName": str(problem["stage_name"]),
        "grade": int(problem["grade"]),
        "kp": str(problem["kp_name"]),
        "role": str(problem["role"]),
        "story": str(problem["story"]),
        "question": str(problem["question"]),
        "table": {
            "title": str(table.get("title", "")),
            "columns": [str(col) for col in table.get("columns") or []],
            "rows": [[str(cell) for cell in row] for row in table.get("rows") or []],
        },
        "answer": _answer_number(problem),
        "unit": str(problem["unit"]),
        "hints": [str(hint) for hint in problem["hints"]],
        "steps": steps,
        "meta": _meta(problem.get("diagnosis") or {}),
    }


def build_bank() -> Dict[str, Any]:
    """为每条 available 的故事线生成 9 道题（每关 1 道）。"""
    stories: List[Dict[str, Any]] = []
    bank: Dict[str, List[Dict[str, Any]]] = {}
    default_id = problem_gen.default_story()
    for item in problem_gen.list_storylines(only_available=True):
        sid = item["id"]
        problems: List[Dict[str, Any]] = []
        for stage in problem_gen.list_stages(sid):
            level_id = str(stage["levels"][0]["level_id"])
            problem = problem_gen.generate(level_id=level_id, salt=f"demo-{sid}-{stage['stage']}")
            problems.append(project(problem))
        bank[sid] = problems
        stories.append(
            {
                "id": sid,
                "name": item["name"],
                "subtitle": item["subtitle"],
                "stageNames": [str(stage["name"]) for stage in problem_gen.list_stages(sid)],
                "default": sid == default_id,
            }
        )
    return {
        "version": "1.0.0",
        "note": "由 scripts/export_demo_bank.py 生成，请勿手工编辑；改出题逻辑后重跑脚本即可。",
        "stories": stories,
        "bank": bank,
    }


def inject(html: str, payload: Dict[str, Any]) -> str:
    """把题库注入 index.html 的两个标记之间。"""
    if BANK_START not in html or BANK_END not in html:
        raise SystemExit(f"index.html 缺少 {BANK_START} / {BANK_END} 标记，无法注入题库。")
    head, rest = html.split(BANK_START, 1)
    _, tail = rest.split(BANK_END, 1)
    compact = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    middle = f"{BANK_START}\n  var BANK = {compact};\n  {BANK_END}"
    return head + middle + tail


def main() -> int:
    """入口：写 bank.json 并刷新 index.html 的内联题库。"""
    payload = build_bank()
    DEMO_DIR.mkdir(parents=True, exist_ok=True)
    BANK_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    html = HTML_PATH.read_text(encoding="utf-8")
    HTML_PATH.write_text(inject(html, payload), encoding="utf-8")
    total = sum(len(items) for items in payload["bank"].values())
    print(
        f"题库已生成：{len(payload['bank'])} 条故事线 / {total} 道题 → "
        f"{BANK_PATH.relative_to(PROJECT_ROOT)}，并注入 demo/index.html"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - 脚本入口
    raise SystemExit(main())
