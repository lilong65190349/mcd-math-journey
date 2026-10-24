"""麦麦取经录 —— 答案校验与分步讲解。

校验策略（这是作品的核心卖点）
------------------------------
1. 题目的 ``expected`` 在出题阶段用 :class:`Decimal` 精确算出；
2. 作答后用 :class:`Decimal` 比较，绝对容差 ``0.01``，兼容百分数、分数、带单位写法；
3. 如果题目声明了 ``verify.tool == "calculate-price"``，再向麦当劳 MCP 的
   ``calculate-price`` 工具发起一次真实复核；只有 MCP 真的返回了总价、
   且与预期一致时才把 ``verify_source`` 标为 ``"mcp"``，否则如实标 ``"local"``。

错因只给方向性提示，不直接给答案。
"""

from __future__ import annotations

import re
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List, Optional

if __package__ in (None, ""):  # pragma: no cover - 脚本运行路径
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import data_source  # noqa: E402

TOLERANCE: Decimal = Decimal("0.01")
"""数值比较容差。"""

CARE_NOTE: str = "适量、均衡搭配，不浪费。"

DIAGNOSIS_LABELS: Dict[str, str] = {
    "运算错误": "计算过程出错",
    "数量关系错误": "数量关系选错（该乘用了加，或该除用了减）",
    "单位换算错误": "单位进率用错",
    "漏用条件": "漏掉了题目里的某个条件",
}

_FRACTION_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[/／]\s*(\d+(?:\.\d+)?)")
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------


def parse_answer(text: Any) -> Optional[Decimal]:
    """把用户输入解析成 :class:`Decimal`。

    支持 ``"76.5"``、``"76.5元"``、``"1,020"``、``"3/4"``、``"75%"``、``"0.45千克"`` 等写法。

    Args:
        text: 用户原始输入。

    Returns:
        解析出的数值；无法解析时返回 ``None``。
    """
    if text is None:
        return None
    raw = str(text).strip()
    if not raw:
        return None
    raw = raw.replace(",", "").replace("，", "")
    raw = raw.replace("％", "%").replace("百分之", "%")
    raw = raw.replace("元", "").replace("千卡", "").replace("千米", "").replace("千克", "")
    raw = raw.replace("克", "").replace("份", "").replace("个", "").replace("块", "")

    fraction = _FRACTION_RE.search(raw)
    if fraction:
        denominator = Decimal(fraction.group(2))
        if denominator == 0:
            return None
        try:
            return Decimal(fraction.group(1)) / denominator
        except (InvalidOperation, ZeroDivisionError):
            return None

    match = _NUMBER_RE.search(raw)
    if not match:
        return None
    try:
        return Decimal(match.group(0))
    except InvalidOperation:  # pragma: no cover - 正则已保证合法
        return None


def _expected_value(problem: Dict[str, Any]) -> Optional[Decimal]:
    """从题目里取出预期答案的数值。"""
    return parse_answer(problem.get("answer"))


# ---------------------------------------------------------------------------
# MCP 复核
# ---------------------------------------------------------------------------


def _build_items(problem: Dict[str, Any]) -> List[Dict[str, Any]]:
    """从题目的数据表里构造一份可用于 ``calculate-price`` 的明细。

    菜单类表格第三列是份数（如 ``["燕麦奶铁", "9.90", "3"]``），按实际份数构造，
    否则复核的篮子与题目不是同一笔账。
    """
    table = problem.get("data_table") or {}
    columns = [str(col) for col in table.get("columns") or []]
    rows = table.get("rows") or []
    items: List[Dict[str, Any]] = []
    if len(columns) < 2:
        return items
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        name = str(row[0])
        price = data_source.to_decimal(row[1], Decimal("-1"))
        if price < 0:
            continue
        quantity = 1
        if len(row) >= 3:
            parsed = data_source.to_decimal(row[2], Decimal("0"))
            if parsed >= 1:
                quantity = int(parsed)
        items.append({"name": name, "price": str(price), "quantity": quantity})
    return items


def _mcp_recheck(problem: Dict[str, Any], expected: Decimal) -> str:
    """尝试用 MCP 的 ``calculate-price`` 复核结果。

    Returns:
        ``"mcp"``（MCP 复核通过）或 ``"local"``（降级到本地算式）。
    """
    verify = problem.get("verify") or {}
    if str(verify.get("tool")) != "calculate-price":
        return "local"
    items = _build_items(problem)
    if not items:
        return "local"
    try:
        result = data_source.calc_price(items)
    except Exception:  # pragma: no cover - 任何异常都降级
        return "local"
    total = result.get("total")
    source = str(result.get("source") or "")
    if source != data_source.SOURCE_MCP or total is None:
        return "local"
    if abs(Decimal(total) - expected) <= TOLERANCE:
        return "mcp"
    return "local"


# ---------------------------------------------------------------------------
# 错因诊断
# ---------------------------------------------------------------------------


def diagnose(problem: Dict[str, Any], user_answer: Any) -> str:
    """给出错因归类与针对性提示（**不包含最终答案**）。

    Args:
        problem: 题目字典。
        user_answer: 用户输入。

    Returns:
        形如 ``"数量关系错误：……"`` 的诊断文本。
    """
    expected = _expected_value(problem)
    actual = parse_answer(user_answer)
    if expected is None:
        return "这道题的参考答案异常，请重新出题后再试。"
    if actual is None:
        return "漏用条件：还没有输入有效的数字，请填一个数值再提交。"

    info: Dict[str, Any] = problem.get("diagnosis") or {}
    kind = str(info.get("type") or "")
    delta = actual - expected

    if actual == 0 and expected != 0:
        return "数量关系错误：结果是 0，说明运算选错或条件用错了，先确认该用哪种运算、用了哪几个数。"

    # 1) 单位换算错误：恰好差 1000 倍或 100 倍（用相对误差，避免把 0 误判进来）
    if expected != 0 and actual != 0:
        ratio = actual / expected
        for factor, label in (
            (Decimal("1000"), "把克和千克的进率弄混了（1 千克 = 1000 克）"),
            (Decimal("0.001"), "把克和千克的进率弄混了（1 千克 = 1000 克）"),
            (Decimal("100"), "百分数与小数互换时多乘或多除了 100"),
            (Decimal("0.01"), "百分数与小数互换时多乘或多除了 100"),
        ):
            if abs(ratio - factor) <= factor * Decimal("0.02"):
                return f"单位换算错误：{label}。先把单位统一，再算数量。"

    # 2) 数量关系错误
    if kind == "mul":
        a = data_source.to_decimal(info.get("a"))
        b = data_source.to_decimal(info.get("b"))
        if abs(actual - (a + b)) <= TOLERANCE:
            return (
                "数量关系错误：这里求的是几个相同的数合起来，应该用乘法，"
                "而不是把单价和份数相加。想一想「几个几」。"
            )
        if b != 0 and abs(actual - (a / b)) <= TOLERANCE:
            return "数量关系错误：题目问的是总量，不是把总量平均分，除法和乘法的用法要分清。"
    if kind == "div":
        dividend = data_source.to_decimal(info.get("dividend"))
        divisor = data_source.to_decimal(info.get("divisor"))
        if divisor != 0 and abs(actual - (dividend * divisor)) <= TOLERANCE:
            return (
                "数量关系错误：求「里面有几个单价」要用除法，用乘法会把结果放大。"
            )
        if abs(actual - dividend) <= TOLERANCE:
            return "漏用条件：你写的是总数，题目问的是能分成几份，还要再除以每份的量。"
    if kind == "add":
        terms = [data_source.to_decimal(value) for value in info.get("terms") or []]
        if len(terms) >= 2 and abs(actual - max(terms)) <= TOLERANCE:
            return "漏用条件：只算了其中一样，题目要求把几样合并起来，别漏掉其他几项。"
    if kind == "sub":
        base = data_source.to_decimal(info.get("base"))
        if abs(actual - base) <= TOLERANCE:
            return "漏用条件：只抄了预算，还没把已经用掉的部分减掉。"
    if kind == "percent":
        part = data_source.to_decimal(info.get("part"))
        whole = data_source.to_decimal(info.get("whole"))
        if whole != 0 and abs(actual - (part / whole)) <= TOLERANCE:
            return "单位换算错误：算出了比值，但百分数还要再乘 100，并带上百分号。"
    if kind == "average":
        total = data_source.to_decimal(info.get("total"))
        if abs(actual - total) <= TOLERANCE:
            return "漏用条件：你算的是总价，平均数还要再除以份数。"
    if kind == "discount":
        total = data_source.to_decimal(info.get("total"))
        if abs(actual - total) <= TOLERANCE:
            return "漏用条件：还没把券的面额减掉，实付金额要减去优惠。"
    if kind == "unit":
        grams = data_source.to_decimal(info.get("grams"))
        if abs(actual - grams) <= TOLERANCE:
            return "单位换算错误：这是总克数，题目要求用千克表示，还要除以 1000。"

    # 3) 运算错误 vs 漏用条件
    if expected != 0 and abs(delta) <= abs(expected) * Decimal("0.2"):
        return "运算错误：思路是对的，计算时某一步出了偏差，建议把每一步单独写出来再核对一遍。"
    if abs(delta) > abs(expected):
        return "数量关系错误：结果和参考答案相差较大，先确认用哪种运算、用了哪几个条件。"
    return "漏用条件：请再读一遍题干，看看有没有条件还没用上。"


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------


def check_answer(problem: Dict[str, Any], user_answer: Any) -> Dict[str, Any]:
    """校验作答。

    Args:
        problem: 题目字典（需含 ``answer`` / ``answer_type`` / ``verify``）。
        user_answer: 用户输入。

    Returns:
        ``{"correct", "expected", "actual", "delta", "verify_source", "diagnosis"}``。
        判对时 ``diagnosis`` 为空串。
    """
    expected = _expected_value(problem)
    actual = parse_answer(user_answer)
    answer_type = str(problem.get("answer_type") or "number")

    if expected is None:
        return {
            "correct": False,
            "expected": str(problem.get("answer", "")),
            "actual": "" if actual is None else str(actual),
            "delta": "",
            "verify_source": "local",
            "diagnosis": "题目数据异常，请重新出题。",
        }

    candidates: List[Decimal] = []
    if actual is not None:
        candidates.append(actual)
        if answer_type == "percent":
            # 兼容把 19.6% 写成 0.196 的写法
            candidates.append(actual * Decimal("100"))

    delta = ""
    correct = False
    matched: Optional[Decimal] = None
    for value in candidates:
        if abs(value - expected) <= TOLERANCE:
            correct = True
            matched = value
            break
    if matched is not None:
        delta = str(abs(matched - expected))

    verify_source = _mcp_recheck(problem, expected) if correct else "local"

    result: Dict[str, Any] = {
        "correct": correct,
        "expected": str(expected),
        "actual": "" if actual is None else str(actual),
        "delta": delta,
        "verify_source": verify_source,
        "diagnosis": "" if correct else diagnose(problem, user_answer),
    }
    return result


# ---------------------------------------------------------------------------
# 讲解
# ---------------------------------------------------------------------------


def explain(problem: Dict[str, Any]) -> str:
    """生成 Markdown 分步讲解，结尾统一附上轻提示。

    Args:
        problem: 题目字典。

    Returns:
        Markdown 文本。
    """
    lines: List[str] = []
    lines.append("### 分步讲解")
    lines.append("")
    table = problem.get("data_table") or {}
    if table.get("columns"):
        lines.append(f"**{table.get('title', '题目数据')}**")
        lines.append("")
        lines.append("| " + " | ".join(str(col) for col in table["columns"]) + " |")
        lines.append("|" + "|".join(["---"] * len(table["columns"])) + "|")
        for row in table.get("rows") or []:
            lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
        lines.append("")

    steps = [str(step) for step in problem.get("steps") or [] if str(step).strip() != CARE_NOTE]
    for index, step in enumerate(steps, start=1):
        lines.append(f"{index}. {step}")

    lines.append("")
    lines.append(f"**参考答案：{problem.get('answer')} {problem.get('unit', '')}**".rstrip())
    lines.append("")
    verify = problem.get("verify") or {}
    if verify.get("formula"):
        lines.append(f"> 校验算式：`{verify['formula']}`（工具：{verify.get('tool', 'local')}）")
        lines.append("")
    lines.append(f"> {CARE_NOTE}")
    return "\n".join(lines)


def verify_source_label(source: str) -> str:
    """把 ``verify_source`` 转成中文说明。"""
    if source == "mcp":
        return "麦当劳 MCP calculate-price 真实复核通过"
    return "本地 Decimal 算式复核（MCP 不可用时的降级路径）"


if __name__ == "__main__":  # pragma: no cover - 手工调试入口
    import json

    import problem_gen

    sample = problem_gen.generate(level_id="3-2", salt="demo")
    print(json.dumps(check_answer(sample, "0"), ensure_ascii=False, indent=2))
    print(explain(sample))
