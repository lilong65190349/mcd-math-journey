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

FRACTION_TOLERANCE: Decimal = Decimal("0.000000001")
"""分数答案的比较容差：等价分数（12/38 与 6/19）必须判对，近似小数（0.32）必须判错。"""

CARE_NOTE: str = "适量、均衡搭配，不浪费。"

DIAGNOSIS_LABELS: Dict[str, str] = {
    "运算错误": "计算过程出错",
    "数量关系错误": "数量关系选错（该乘用了加，或该除用了减）",
    "单位换算错误": "单位进率用错",
    "漏用条件": "漏掉了题目里的某个条件",
}

_FRACTION_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[/／]\s*(\d+(?:\.\d+)?)")
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")

TEXT_ANSWER_TYPES: tuple = ("text", "enum")
"""走文本比对（而非数值比对）的答案类型（与 :mod:`problem_gen` 保持同一份口径）。"""


def normalize_text(value: Any) -> str:
    """把文本答案归一化成可严格比较的形式。

    **唯一真相源在 :func:`problem_gen.normalize_text`**，这里只做转发：
    判分侧与「提示防泄露」侧必须用同一套归一化，否则会出现
    「判分判对了、防泄露却说没泄露」这种自相矛盾的状态。

    Args:
        value: 原始文本。

    Returns:
        归一化后的文本（NFKC → strip → lower → 空白折叠 → 去首尾标点引号）。
    """
    import problem_gen  # 延迟导入：避免判分模块与出题模块在导入期互相牵制

    return problem_gen.normalize_text(value)


def canonical_text(value: Any) -> str:
    """文本答案的**最终比对形式**：抹掉全部标点与空白。

    转发到 :func:`problem_gen.canonical_text`（唯一真相源），理由同上：
    判分与防泄露必须同源。

    为什么判分要用它而不是 :func:`normalize_text`：``normalize_text`` 只 strip
    **首尾**标点、中间标点保留，于是「标点」与「空格」变成两类 token 互相映射，
    ``"早， 忙"``（标点 + 空格）既不是 ``"早，忙"`` 也不是 ``"早 忙"``，
    被判错——而题干明明写着「标点可以不写全」。抹掉之后只剩一条规则，不会自相矛盾。

    Args:
        value: 原始文本。

    Returns:
        只含字母、数字与汉字的 canonical 串。
    """
    import problem_gen  # 延迟导入，同上

    return problem_gen.canonical_text(value)


def _text_candidates(problem: Dict[str, Any]) -> List[str]:
    """把题目声明的「正确答案 + 同义写法」归一化成候选集合。

    ``accept`` 由模板显式给出（例如 ``{"answer": "colour", "accept": ["color"]}``）。
    **不做编辑距离模糊匹配**：``apple`` 与 ``apply`` 只差一个字符，容忍它会把
    「正确答错」判成对，破坏这道题的测量功能。

    Args:
        problem: 题目字典（读 ``answer`` / ``accept``）。

    Returns:
        去重后的 canonical 候选列表（空串不参与）。
    """
    raw: List[Any] = [problem.get("answer")]
    raw.extend(list(problem.get("accept") or []))
    candidates: List[str] = []
    for item in raw:
        normalized = canonical_text(item)
        if normalized and normalized not in candidates:
            candidates.append(normalized)
    return candidates


def _text_diagnosis(problem: Dict[str, Any], normalized: str) -> str:
    """文本题的错因提示：**只给方向，绝不出现答案本身**。

    Args:
        problem: 题目字典。
        normalized: 用户的 canonical 输入。

    Returns:
        诊断文本。
    """
    if not normalized:
        return "漏用条件：还没有输入内容，请先写一个答案再提交。"
    expected = canonical_text(problem.get("answer"))
    if expected and normalized == expected[::-1]:
        return "数量关系错误：写反了，检查一下先后顺序。"
    if expected and len(normalized) == len(expected):
        return "运算错误：字数已经对了，再核对一下每个字（或每个字母）的写法。"
    if expected and abs(len(normalized) - len(expected)) <= 2:
        return "漏用条件：很接近了，看看有没有漏字或多字。"
    return "漏用条件：答案还不完整，先回忆整句（整词）的写法，再逐字核对一遍。"


def _check_text_answer(problem: Dict[str, Any], user_answer: Any) -> Dict[str, Any]:
    """文本 / 选项型答案的判分（走 canonical 形式的集合命中）。

    Args:
        problem: 题目字典。
        user_answer: 用户输入。

    Returns:
        与数值分支同构的结果字典。``actual`` 回报的是 ``normalize_text`` 那一层
        （保留标点空格，人读得懂），比较用的是 ``canonical_text``。
    """
    candidates = _text_candidates(problem)
    canonical = canonical_text(user_answer)
    correct = bool(canonical) and canonical in candidates
    return {
        "correct": correct,
        "expected": str(problem.get("answer") if problem.get("answer") is not None else ""),
        "actual": normalize_text(user_answer),
        "delta": "",
        "verify_source": "local",
        "diagnosis": "" if correct else _text_diagnosis(problem, canonical),
    }


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
    if str(problem.get("answer_type") or "number") in TEXT_ANSWER_TYPES:
        return _text_diagnosis(problem, canonical_text(user_answer))

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
    if kind == "fraction_part":
        part = data_source.to_decimal(info.get("part"))
        whole = data_source.to_decimal(info.get("whole"))
        if part != 0 and whole != 0 and abs(actual - (whole / part)) <= TOLERANCE:
            return (
                "数量关系错误：把「部分占整体」弄反了——求几分之几要用部分除以整体，"
                "不是整体除以部分。"
            )
        if abs(actual - part) <= TOLERANCE:
            return "漏用条件：你算出的是部分本身的克数，还要除以整体（两样合起来）才是几分之几。"
        return (
            "数量关系错误：求「占几分之几」要先写成分数（部分 ÷ 整体），"
            "再约成最简分数，例如写成 12/38 的形式。"
        )
    if kind == "fraction_eat":
        protein = data_source.to_decimal(info.get("protein"))
        denom = data_source.to_decimal(info.get("denom"))
        if abs(actual - protein) <= TOLERANCE:
            return "漏用条件：还要除以平均分成的份数——每人只分到其中 1 份。"
        if denom and abs(actual - protein * denom) <= TOLERANCE:
            return "运算错误：这里应该用除法（平均分成几份取 1 份），不是用乘法。"
        return "运算错误：先把总量平均分成几份，再取其中 1 份，用除法算一遍。"

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

    文本 / 选项型答案（``answer_type`` 为 ``text`` / ``enum``）在数值分支**之前**
    分流到 :func:`_check_text_answer`——否则 ``_expected_value`` 会返回 ``None``，
    所有语文 / 英语题都会被判成「题目数据异常」。
    """
    if str(problem.get("answer_type") or "number") in TEXT_ANSWER_TYPES:
        return _check_text_answer(problem, user_answer)

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

    # 分数答案（课标整改新增的 fraction 题型）必须精确比较：
    # 6/19 ≈ 0.3158，若沿用 0.01 的容差，孩子写 0.32 会被误判为正确。
    # parse_answer 用 Decimal 除法解析，等价分数（12/38 与 6/19）会得到完全相同的值，
    # 因此这里用极小容差既能判对等价分数，又能挡住"写近似小数蒙对"。
    tolerance = FRACTION_TOLERANCE if answer_type == "fraction" else TOLERANCE

    delta = ""
    correct = False
    matched: Optional[Decimal] = None
    for value in candidates:
        if abs(value - expected) <= tolerance:
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
    unit_text = str(problem.get("unit") or "").strip()
    # 单位为空（语文 / 英语题）时不留尾随空格，否则会打印出「答案 **」这种悬空空格。
    answer_line = f"**参考答案：{problem.get('answer')}{f' {unit_text}' if unit_text else ''}**"
    lines.append(answer_line)
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
