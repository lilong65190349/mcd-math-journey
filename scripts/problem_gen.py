"""麦麦取经录 —— 出题引擎。

职责
----
1. 读取 ``data/curriculum.json``（知识点图谱）与 ``data/journey.json``（九关八十一难）。
2. 通过 :mod:`data_source` 取真实餐品数据（MCP → 缓存 → 示例快照）。
3. 用 9 个题型模板把真实数据变成小学 3-6 年级的应用题。
4. 每题给三级递进提示（**任何一级都不直接给出最终答案**）。

题目结构见 :func:`generate` 的返回注释，字段名在整个项目里保持一致：
``level_id`` / ``kp_id`` / ``stage`` / ``answer`` / ``verify`` / ``data_source``。
"""

from __future__ import annotations

import json
import random
import re
import sys
import time
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

if __package__ in (None, ""):  # pragma: no cover - 脚本运行路径
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import data_source  # noqa: E402

PROJECT_ROOT: Path = Path(__file__).resolve().parents[1]
CURRICULUM_PATH: Path = PROJECT_ROOT / "data" / "curriculum.json"
JOURNEY_PATH: Path = PROJECT_ROOT / "data" / "journey.json"

CARE_NOTE: str = "适量、均衡搭配，不浪费。"
"""每道题讲解结尾的统一轻提示（合规要求）。"""


# ---------------------------------------------------------------------------
# 兜底常量：任何数据源都不可用时，出题流程仍不能崩
# ---------------------------------------------------------------------------

_FALLBACK_FOODS: List[Dict[str, Any]] = [
    {"name": "巨无霸", "kcal": 530, "protein": Decimal("26.0"), "weight": Decimal("219")},
    {"name": "中薯条", "kcal": 340, "protein": Decimal("4.0"), "weight": Decimal("114")},
    {"name": "可乐(中)", "kcal": 150, "protein": Decimal("0.0"), "weight": Decimal("400")},
    {"name": "麦乐鸡(5块)", "kcal": 230, "protein": Decimal("12.0"), "weight": Decimal("90")},
    {"name": "玉米杯", "kcal": 90, "protein": Decimal("2.0"), "weight": Decimal("105")},
    {"name": "香芋派", "kcal": 250, "protein": Decimal("3.0"), "weight": Decimal("75")},
]

_FALLBACK_MEALS: List[Dict[str, Any]] = [
    {"name": "巨无霸", "price": Decimal("25.50"), "kcal": 530},
    {"name": "麦辣鸡腿堡", "price": Decimal("24.00"), "kcal": 570},
    {"name": "中薯条", "price": Decimal("12.00"), "kcal": 340},
    {"name": "可乐(中)", "price": Decimal("9.00"), "kcal": 150},
    {"name": "麦乐鸡(5块)", "price": Decimal("13.50"), "kcal": 230},
]

_FALLBACK_COUPONS: List[Dict[str, Any]] = [
    {"name": "满30元减5元", "threshold": Decimal("30"), "discount": Decimal("5")},
    {"name": "满50元减10元", "threshold": Decimal("50"), "discount": Decimal("10")},
    {"name": "满20元减3元", "threshold": Decimal("20"), "discount": Decimal("3")},
]

_FALLBACK_POINTS: List[Dict[str, Any]] = [
    {"name": "巨无霸兑换券", "points": Decimal("1200")},
    {"name": "中薯条兑换券", "points": Decimal("600")},
    {"name": "麦咖啡拿铁券", "points": Decimal("800")},
]

_STATE: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# 数值格式化
# ---------------------------------------------------------------------------


def fmt_money(value: Decimal) -> str:
    """金额格式化：保留两位小数。"""
    return str(Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def fmt_num(value: Decimal, places: str = "0.01") -> str:
    """通用数值格式化：按给定量级四舍五入，并去掉多余的 0。"""
    quantized = Decimal(value).quantize(Decimal(places), rounding=ROUND_HALF_UP)
    text = format(quantized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _table(title: str, columns: List[str], rows: List[List[str]]) -> Dict[str, Any]:
    """构造 data_table 结构。"""
    return {"title": title, "columns": list(columns), "rows": [list(row) for row in rows]}


# ---------------------------------------------------------------------------
# 数据装载与规范化
# ---------------------------------------------------------------------------


def _norm_food(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """把一条营养记录规范化成内部结构。"""
    name = str(
        data_source.pick(record, "food_name", "foodName", "meal_name", "mealName", "name", default="")
    ).strip()
    kcal = int(data_source.to_decimal(data_source.pick(record, "energy_kcal", "energyKcal", "energy", "calorie", "kcal", "热量", default=0)))
    protein = data_source.to_decimal(data_source.pick(record, "protein_g", "protein", "蛋白质", default=0))
    weight = data_source.to_decimal(data_source.pick(record, "weight_g", "weight", "net_weight", "净含量", default=0))
    if not name or kcal <= 0:
        return None
    return {"name": name, "kcal": kcal, "protein": protein, "weight": weight}


def _norm_meal(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """把一条餐品记录规范化成内部结构。"""
    name = str(
        data_source.pick(record, "meal_name", "mealName", "name", "title", default="")
    ).strip()
    price = data_source.to_decimal(
        data_source.pick(record, "price", "sale_price", "salePrice", "current_price", "amount", default=0)
    )
    kcal = int(data_source.to_decimal(data_source.pick(record, "energy_kcal", "energyKcal", "kcal", default=0)))
    if not name or price <= 0:
        return None
    return {"name": name, "price": price, "kcal": kcal}


def _norm_coupon(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """把一条优惠券记录规范化成内部结构。"""
    name = str(
        data_source.pick(record, "coupon_name", "couponName", "name", "title", default="")
    ).strip()
    threshold = data_source.to_decimal(
        data_source.pick(record, "threshold", "min_amount", "minAmount", "limit", "condition", default=0)
    )
    discount = data_source.to_decimal(
        data_source.pick(record, "discount", "amount", "face_value", "faceValue", "reduce", default=0)
    )
    if not name or discount <= 0:
        return None
    if threshold <= 0:
        threshold = Decimal("0")
    return {"name": name, "threshold": threshold, "discount": discount}


def _norm_points(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """把一条积分商品记录规范化成内部结构。"""
    name = str(
        data_source.pick(record, "product_name", "productName", "name", "title", default="")
    ).strip()
    points = data_source.to_decimal(
        data_source.pick(record, "points", "point", "need_points", "needPoints", "integral", default=0)
    )
    if not name or points <= 0:
        return None
    return {"name": name, "points": points}


def _parse_datetime(text: Any) -> Optional[datetime]:
    """尽力解析日期时间字符串。"""
    if isinstance(text, datetime):
        return text
    raw = str(text or "").strip()
    if not raw:
        return None
    raw = raw.replace("/", "-").replace("年", "-").replace("月", "-").replace("日", "")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%m-%d %H:%M"):
        try:
            parsed = datetime.strptime(raw.strip(), fmt)
        except ValueError:
            continue
        if fmt == "%m-%d %H:%M":  # pragma: no cover - 极少出现
            parsed = parsed.replace(year=datetime.now().year)
        return parsed
    return None


def _norm_calendar(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """把一条活动记录规范化成内部结构。"""
    name = str(
        data_source.pick(record, "campaign_name", "campaignName", "name", "title", default="活动期")
    ).strip()
    start = _parse_datetime(data_source.pick(record, "start_time", "startTime", "begin", "start", "start_date", default=""))
    end = _parse_datetime(data_source.pick(record, "end_time", "endTime", "finish", "end", "end_date", default=""))
    if not start or not end or end < start:
        return None
    return {"name": name or "活动期", "start": start, "end": end}


def load_state(force: bool = False) -> Dict[str, Any]:
    """装载并缓存全部数据（进程内只取一次，避免重复网络请求）。"""
    global _STATE
    if _STATE is not None and not force:
        return _STATE

    nutrition = data_source.get_nutrition_foods()
    meals = data_source.get_meals()
    coupons = data_source.get_coupons()
    calendar = data_source.get_campaign_calendar()
    points = data_source.get_points_products()
    now_info = data_source.get_now()

    foods = [item for item in (_norm_food(r) for r in nutrition.get("data") or []) if item]
    meal_list = [item for item in (_norm_meal(r) for r in meals.get("data") or []) if item]
    coupon_list = [item for item in (_norm_coupon(r) for r in coupons.get("data") or []) if item]
    point_list = [item for item in (_norm_points(r) for r in points.get("data") or []) if item]
    calendar_list = [item for item in (_norm_calendar(r) for r in calendar.get("data") or []) if item]

    now_payload = now_info.get("data") or {}
    now_dt = _parse_datetime(data_source.pick(now_payload, "datetime", "now", "time", "currentTime", default=""))
    if now_dt is None:
        now_dt = datetime.now().replace(microsecond=0)

    if not foods:
        foods = [dict(item) for item in _FALLBACK_FOODS]
    if not meal_list:
        meal_list = [dict(item) for item in _FALLBACK_MEALS]
    if not coupon_list:
        coupon_list = [dict(item) for item in _FALLBACK_COUPONS]
    if not point_list:
        point_list = [dict(item) for item in _FALLBACK_POINTS]
    if not calendar_list:
        calendar_list = [
            {
                "name": "默认活动期",
                "start": now_dt.replace(hour=0, minute=0, second=0),
                "end": now_dt.replace(hour=23, minute=59, second=0) + timedelta(days=27),
            }
        ]

    _STATE = {
        "foods": foods,
        "meals": meal_list,
        "coupons": coupon_list,
        "points": point_list,
        "calendar": calendar_list,
        "now": now_dt,
        "sources": {
            "nutrition": nutrition.get("source"),
            "menu": meals.get("source"),
            "coupons": coupons.get("source"),
            "calendar": calendar.get("source"),
            "points": points.get("source"),
            "now": now_info.get("source"),
        },
        # 数据级别 + 抓取时间：表格标题要用它标注「（实时数据）/（缓存数据 YYYY-MM-DD）/（示例数据）」，
        # 必须与题目 dict 里的 data_source 同源，否则会出现「表头写实时、字段写示例」的矛盾。
        "source_meta": {
            "nutrition": {"source": nutrition.get("source"), "fetched_at": nutrition.get("fetched_at")},
            "menu": {"source": meals.get("source"), "fetched_at": meals.get("fetched_at")},
            "coupons": {"source": coupons.get("source"), "fetched_at": coupons.get("fetched_at")},
            "calendar": {"source": calendar.get("source"), "fetched_at": calendar.get("fetched_at")},
            "points": {"source": points.get("source"), "fetched_at": points.get("fetched_at")},
            "now": {"source": now_info.get("source"), "fetched_at": now_info.get("fetched_at")},
        },
    }
    return _STATE


def reset_state() -> None:
    """清空进程内缓存（测试 / 切换数据源时使用）。"""
    global _STATE
    _STATE = None


def load_curriculum() -> Dict[str, Any]:
    """读取知识点图谱。"""
    with CURRICULUM_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_journey() -> Dict[str, Any]:
    """读取关卡配置。"""
    with JOURNEY_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def kp_index() -> Dict[str, Dict[str, Any]]:
    """返回 ``{kp_id: {"id","name","desc","grade"}}`` 索引。"""
    index: Dict[str, Dict[str, Any]] = {}
    for grade_block in load_curriculum().get("grades", []):
        grade = int(grade_block.get("grade", 0))
        for kp in grade_block.get("kps", []):
            index[str(kp.get("id"))] = {
                "id": str(kp.get("id")),
                "name": str(kp.get("name")),
                "desc": str(kp.get("desc")),
                "grade": grade,
            }
    return index


def list_stages() -> List[Dict[str, Any]]:
    """列出九个关卡（含每一难的元数据）。"""
    stages: List[Dict[str, Any]] = []
    for stage in load_journey().get("stages", []):
        stages.append(
            {
                "stage": int(stage.get("stage", 0)),
                "name": str(stage.get("name", "")),
                "theme": str(stage.get("theme", "")),
                "role_focus": str(stage.get("role_focus", "")),
                "grade_hint": int(stage.get("grade_hint", 0)),
                "op_family": str(stage.get("op_family", "")),
                "level_count": len(stage.get("levels", [])),
                "levels": [
                    {
                        "level_id": str(level.get("level_id")),
                        "kp_id": str(level.get("kp_id")),
                        "brief": str(level.get("brief")),
                        "data_hint": str(level.get("data_hint")),
                    }
                    for level in stage.get("levels", [])
                ],
            }
        )
    return stages


def get_level(level_id: str) -> Dict[str, Any]:
    """按 ``level_id`` 取出「关卡 + 这一难」的合并信息。

    Raises:
        KeyError: 关卡号不存在。
    """
    for stage in load_journey().get("stages", []):
        for level in stage.get("levels", []):
            if str(level.get("level_id")) == str(level_id):
                merged = dict(level)
                merged.update(
                    {
                        "stage": int(stage.get("stage", 0)),
                        "stage_name": str(stage.get("name", "")),
                        "theme": str(stage.get("theme", "")),
                        "role_focus": str(stage.get("role_focus", "")),
                        "grade_hint": int(stage.get("grade_hint", 0)),
                    }
                )
                return merged
    raise KeyError(f"未找到关卡 {level_id!r}，可用关卡形如 1-1 … 9-9。")


# ---------------------------------------------------------------------------
# 九个题型模板
# ---------------------------------------------------------------------------


def _ctx(level: Dict[str, Any], rng: random.Random) -> Dict[str, Any]:
    """组装模板上下文。"""
    state = load_state()
    level_id = str(level.get("level_id", "1-1"))
    parts = level_id.split("-")
    difficulty = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
    return {
        "state": state,
        "foods": state["foods"],
        "meals": state["meals"],
        "coupons": state["coupons"],
        "points": state["points"],
        "calendar": state["calendar"],
        "now": state["now"],
        "sources": state["sources"],
        "source_meta": state.get("source_meta", {}),
        "level": level,
        "rng": rng,
        "difficulty": max(1, min(9, difficulty)),
        "role": str(level.get("role_focus", "唐僧")),
        "stage_name": str(level.get("stage_name", "取经路上")),
    }


def _pick_many(rng: random.Random, pool: List[Dict[str, Any]], count: int) -> List[Dict[str, Any]]:
    """从池子里取不重复的若干项；池子不足时允许重复补齐。"""
    if not pool:
        raise ValueError("数据池为空，无法出题。")
    count = max(1, count)
    if count <= len(pool):
        return rng.sample(pool, count)
    picked = list(pool)
    while len(picked) < count:
        picked.append(rng.choice(pool))
    return picked


def _stage_1_add(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """题型一：万以内加减法 —— 合并几样餐品的热量。"""
    n = 2 + (ctx["difficulty"] - 1) // 3
    picks = _pick_many(ctx["rng"], ctx["foods"], n)
    kcal = [int(item["kcal"]) for item in picks]
    total = sum(kcal)
    names = "、".join(item["name"] for item in picks)

    rows = [[item["name"], str(item["kcal"]), str(item["weight"])] for item in picks]
    if n >= 3:
        hint3 = f"先算前两样：{kcal[0]} + {kcal[1]} = {kcal[0] + kcal[1]} 千卡。"
    else:
        low = 2 * min(kcal)
        high = 2 * max(kcal)
        hint3 = f"先估一估：两样都在 {min(kcal)}～{max(kcal)} 千卡之间，所以答案一定在 {low}～{high} 千卡之间。"

    return {
        "role": ctx["role"],
        "story": f"{ctx['role']}在{ctx['stage_name']}歇脚，把今天的餐品摆开：{names}。"
                 f"他想先弄清楚这一份加起来一共有多少能量。",
        "question": f"这 {n} 样餐品一共多少千卡？",
        "data_table": _table("今日餐品营养（真实数据）", ["餐品", "能量（千卡）", "净含量（克）"], rows),
        "answer_type": "number",
        "answer": str(total),
        "unit": "千卡",
        "hints": [
            "求「一共多少」，就是要把几个数合并起来，用加法。",
            "把每个千卡数排成一列逐个相加："
            + " + ".join(["□"] * n)
            + " = □。",
            hint3,
        ],
        "steps": [
            "把题目给出的每个数写下来：" + "、".join(f"{v} 千卡" for v in kcal) + "。",
            "求合计用加法，按从左到右的顺序相加："
            + " + ".join(str(v) for v in kcal)
            + f" = {total}（千卡）。",
            f"所以这 {n} 样餐品一共 {total} 千卡。",
            CARE_NOTE,
        ],
        "verify": {"formula": "+".join(str(v) for v in kcal), "tool": "calculate-price"},
        "diagnosis": {"type": "add", "terms": kcal, "expected": total},
        "primary_source": "nutrition",
    }


def _stage_2_sub(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """题型二：多步连减 —— 从热量预算里扣掉已选的餐品。"""
    n = 2 + (ctx["difficulty"] - 1) // 3
    picks = _pick_many(ctx["rng"], ctx["foods"], n)
    kcal = [int(item["kcal"]) for item in picks]
    used = sum(kcal)
    budget = used + 150 + 50 * ctx["difficulty"]
    remain = budget - used
    names = "、".join(item["name"] for item in picks)

    rows = [[item["name"], str(item["kcal"])] for item in picks]
    rows.append(["本次预算", str(budget)])

    return {
        "role": ctx["role"],
        "story": f"{ctx['role']}的行程按 {budget} 千卡来安排，路上挑了{names}。"
                 f"他想知道扣掉这些之后还剩多少额度。",
        "question": f"预算 {budget} 千卡，减掉这 {n} 样餐品的热量后，还剩多少千卡？",
        "data_table": _table("本次餐品与预算（真实数据）", ["项目", "能量（千卡）"], rows),
        "answer_type": "number",
        "answer": str(remain),
        "unit": "千卡",
        "hints": [
            "先从预算里减还是先合并？两种顺序都可以，但要先弄清「减掉的总共是多少」。",
            "两步走：第一步把 □ 个千卡数相加得 □；第二步用 "
            + str(budget)
            + " 减去这个和。",
            # 当「已用合计」恰好等于「剩余额度」时，报出合计就等于报出答案，
            # 因此改用不含数值的递进提示。
            (
                f"这 {n} 样餐品合起来是 {used} 千卡。"
                if used != remain
                else f"两步走：先把这 {n} 样餐品的热量加起来，再用预算减掉这个和。"
            ),
        ],
        "steps": [
            "第一步：求出要减掉的总热量，"
            + " + ".join(str(v) for v in kcal)
            + f" = {used}（千卡）。",
            f"第二步：用预算减去已用，{budget} − {used} = {remain}（千卡）。",
            "也可以一步步减："
            + str(budget)
            + "".join(f" − {v}" for v in kcal)
            + f" = {remain}（千卡），结果一致。",
            CARE_NOTE,
        ],
        "verify": {"formula": f"{budget}-({'+'.join(str(v) for v in kcal)})", "tool": "calculate-price"},
        "diagnosis": {"type": "sub", "terms": kcal, "base": budget, "expected": remain},
        "primary_source": "nutrition",
    }


def _stage_3_mul(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """题型三：乘法与倍数 —— 单价 × 份数。"""
    meal = ctx["rng"].choice(ctx["meals"])
    price: Decimal = meal["price"]
    qty = min(9, 2 + (ctx["difficulty"] - 1))
    total = price * qty
    int_part = int(price)
    frac_part = price - Decimal(int_part)
    if frac_part == 0:
        upper = (Decimal(int_part) + Decimal("1")) * qty
        hint3 = (
            f"先估一估范围：把单价往大看成 {int_part + 1} 元，{qty} 份也不会超过 {fmt_money(upper)} 元，"
            f"所以答案应当略小于这个数。"
        )
    else:
        hint3 = (
            f"拆开算：整数部分 {int_part} × {qty} = {int_part * qty}；"
            f"小数部分 {fmt_num(frac_part)} × {qty} = {fmt_num(frac_part * qty)}。"
        )

    return {
        "role": ctx["role"],
        "story": f"{ctx['role']}在{ctx['stage_name']}的点单台前，看到{meal['name']}每份 {fmt_money(price)} 元。"
                 f"今天的用餐计划需要 {qty} 份，他先在心里把金额算了一遍。",
        "question": f"每份{meal['name']} {fmt_money(price)} 元，{qty} 份一共多少元？",
        "data_table": _table(
            "今日菜单（真实数据）",
            ["餐品", "单价（元）", "份数"],
            [[meal["name"], fmt_money(price), str(qty)]],
        ),
        "answer_type": "number",
        "answer": fmt_money(total),
        "unit": "元",
        "hints": [
            "求几个相同单价的合计，用乘法比连加快。",
            f"{fmt_money(price)} × {qty} = □，注意结果要带两位小数。",
            hint3,
        ],
        "steps": [
            f"一份 {fmt_money(price)} 元，要买 {qty} 份，就是求 {qty} 个 {fmt_money(price)} 相加。",
            f"列式：{fmt_money(price)} × {qty} = {fmt_money(total)}（元）。",
            f"所以一共 {fmt_money(total)} 元。",
            CARE_NOTE,
        ],
        "verify": {"formula": f"{fmt_money(price)}*{qty}", "tool": "calculate-price"},
        "diagnosis": {"type": "mul", "a": str(price), "b": qty, "expected": str(total)},
        "primary_source": "menu",
    }


def _stage_4_div(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """题型四：除法与余数 —— 积分能兑换几份。"""
    product = ctx["rng"].choice(ctx["points"])
    unit_points = int(product["points"])
    quotient = 2 + (ctx["difficulty"] - 1) // 2
    remainder = min(unit_points - 1, 20 + 15 * ctx["difficulty"])
    total_points = unit_points * quotient + remainder

    return {
        "role": ctx["role"],
        "story": f"{ctx['role']}在{ctx['stage_name']}的积分台前，手里有 {total_points} 积分。"
                 f"他想换{product['name']}，每份需要 {unit_points} 积分。",
        "question": f"用 {total_points} 积分兑换需要 {unit_points} 积分的{product['name']}，最多能兑换几份？",
        "data_table": _table(
            "积分商品（真实数据）",
            ["积分商品", "所需积分", "持有积分"],
            [[product["name"], str(unit_points), str(total_points)]],
        ),
        "answer_type": "number",
        "answer": str(quotient),
        "unit": "份",
        "hints": [
            "问「最多能换几份」，就是问总量里面包含几个单价，用除法。",
            f"{total_points} ÷ {unit_points} = □（份）…… □（积分），商就是答案，余数是剩下的积分。",
            f"先试商：{unit_points} × {max(quotient - 1, 1)} = {unit_points * max(quotient - 1, 1)}，"
            f"比 {total_points} 小；再往上试一个数，看会不会超过。",
        ],
        "steps": [
            f"把 {total_points} 积分按每份 {unit_points} 积分去分，列式：{total_points} ÷ {unit_points}。",
            f"商是 {quotient}，余数是 {remainder}（{unit_points} × {quotient} = {unit_points * quotient}，"
            f"{total_points} − {unit_points * quotient} = {remainder}）。",
            f"余数 {remainder} 比每份所需 {unit_points} 积分小，说明不能再换一份，所以最多 {quotient} 份。",
            CARE_NOTE,
        ],
        "verify": {"formula": f"{total_points}/{unit_points}", "tool": "calculate-price"},
        "diagnosis": {
            "type": "div",
            "dividend": total_points,
            "divisor": unit_points,
            "quotient": quotient,
            "remainder": remainder,
            "expected": quotient,
        },
        "primary_source": "points",
    }


def _stage_5_unit(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """题型五：小数与单位换算 —— 克换算成千克。"""
    candidates = [item for item in ctx["foods"] if item["weight"] > 0] or ctx["foods"]
    food = ctx["rng"].choice(candidates)
    weight = Decimal(food["weight"])
    qty = min(6, 2 + (ctx["difficulty"] - 1))
    total_g = weight * qty
    total_kg = (total_g / Decimal("1000")).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)

    return {
        "role": ctx["role"],
        "story": f"{ctx['role']}要把{ctx['stage_name']}的物资称一称。{food['name']}每份净含量 {fmt_num(weight)} 克，"
                 f"一共准备了 {qty} 份，需要按千克登记。",
        "question": f"每份{food['name']}净含量 {fmt_num(weight)} 克，{qty} 份一共多少千克？",
        "data_table": _table(
            "净含量（真实数据）",
            ["餐品", "每份净含量（克）", "份数", "进率"],
            [[food["name"], fmt_num(weight), str(qty), "1 千克 = 1000 克"]],
        ),
        "answer_type": "number",
        "answer": fmt_num(total_kg, "0.001"),
        "unit": "千克",
        "hints": [
            "先求总克数（乘法），再想克和千克之间的进率。",
            f"{fmt_num(weight)} × {qty} = □ 克；把克换成千克要除以 1000，即 □ ÷ 1000 = □ 千克。",
            f"先算第一步：{fmt_num(weight)} × {qty} = {fmt_num(total_g)} 克。",
        ],
        "steps": [
            f"先求总克数：{fmt_num(weight)} × {qty} = {fmt_num(total_g)}（克）。",
            f"克换算成千克要除以进率 1000：{fmt_num(total_g)} ÷ 1000 = {fmt_num(total_kg, '0.001')}（千克）。",
            f"所以一共 {fmt_num(total_kg, '0.001')} 千克。",
            CARE_NOTE,
        ],
        "verify": {"formula": f"{fmt_num(weight)}*{qty}/1000", "tool": "calculate-price"},
        "diagnosis": {
            "type": "unit",
            "grams": str(total_g),
            "kg": str(total_kg),
            "expected": str(total_kg),
        },
        "primary_source": "nutrition",
    }


def _stage_6_time(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """题型六：时间与行程 —— 先求天数，再求总路程。"""
    # 优先挑持续 2 天以上的活动：只有 1 天时「天数 × 每天路程」会退化成一步题。
    multi_day = [
        item
        for item in ctx["calendar"]
        if (item["end"].date() - item["start"].date()).days + 1 >= 2
    ]
    campaign = ctx["rng"].choice(multi_day or ctx["calendar"])
    start: datetime = campaign["start"]
    end: datetime = campaign["end"]
    # 两头都算：day_diff + 1。真实数据可能出现「起止同一天」，此时天数是 1 天，
    # 不能再强行抬成 2 天，否则答案与分步讲解会自相矛盾。
    days = max(1, (end.date() - start.date()).days + 1)
    daily_km = 6 + ctx["difficulty"]
    total_km = days * daily_km

    return {
        "role": ctx["role"],
        "story": f"{ctx['role']}要去{ctx['stage_name']}对岸。活动期「{campaign['name']}」从 "
                 f"{start.strftime('%Y-%m-%d')} 持续到 {end.strftime('%Y-%m-%d')}，两头都算。"
                 f"队伍计划每天走 {daily_km} 千米。",
        "question": f"活动期一共多少天？按每天走 {daily_km} 千米计算，这些天一共走多少千米？",
        "data_table": _table(
            "活动日历（真实数据）",
            ["活动名称", "开始日期", "结束日期", "每日路程（千米）"],
            [[campaign["name"], start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"), str(daily_km)]],
        ),
        "answer_type": "number",
        "answer": str(total_km),
        "unit": "千米",
        "hints": [
            "这是两步题：先求天数，再用天数乘每天的路程。",
            "天数 = 结束日期 − 开始日期 + 1（两头都算）；再算 天数 × 每天千米数 = □。",
            f"先算第一步：从 {start.strftime('%Y-%m-%d')} 到 {end.strftime('%Y-%m-%d')}，"
            f"两头都算，一共 {days} 天。",
        ],
        "steps": [
            f"第一步求天数：{end.strftime('%Y-%m-%d')} − {start.strftime('%Y-%m-%d')} = "
            f"{(end.date() - start.date()).days} 天，两头都算再加 1，所以是 {days} 天。",
            f"第二步求路程：{days} × {daily_km} = {total_km}（千米）。",
            f"所以这些天一共走 {total_km} 千米。",
            CARE_NOTE,
        ],
        "verify": {"formula": f"{days}*{daily_km}", "tool": "calculate-price"},
        "diagnosis": {"type": "mul", "a": str(days), "b": daily_km, "expected": str(total_km)},
        "primary_source": "calendar",
    }


def _stage_7_percent(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """题型七：分数与百分数 —— 蛋白质供能占比。"""
    candidates = [item for item in ctx["foods"] if item["protein"] > 0 and item["kcal"] > 0]
    if not candidates:
        candidates = ctx["foods"]
    food = ctx["rng"].choice(candidates)
    protein = Decimal(food["protein"])
    energy = Decimal(food["kcal"])
    part = protein * Decimal("4")
    ratio = (part / energy * Decimal("100")).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    if abs(part - ratio) <= Decimal("0.05"):
        # 总能量恰好 100 千卡时，「部分千卡」与答案数值相同，不能再当作提示报出。
        hint3 = "先算第一步：把蛋白质克数乘 4 得到它提供的能量；再除以整份总能量，最后乘 100 写成百分数。"
    else:
        hint3 = f"先算第一步：蛋白质提供 {fmt_num(part)} 千卡，整份总能量是 {int(energy)} 千卡。"

    return {
        "role": ctx["role"],
        "story": f"{ctx['role']}在看{ctx['stage_name']}的营养标签。{food['name']}含蛋白质 "
                 f"{fmt_num(protein)} 克，整份能量 {int(energy)} 千卡。每克蛋白质大约提供 4 千卡能量。",
        "question": f"{food['name']}中，蛋白质提供的能量约占总能量的百分之几？（百分号前保留一位小数）",
        "data_table": _table(
            "营养标签（真实数据）",
            ["餐品", "蛋白质（克）", "总能量（千卡）", "每克蛋白质供能"],
            [[food["name"], fmt_num(protein), str(int(energy)), "4 千卡"]],
        ),
        "answer_type": "percent",
        "answer": fmt_num(ratio, "0.1"),
        "unit": "%",
        "hints": [
            "先求「部分」有多少千卡，再和「整体」比，最后写成百分数。",
            f"（{fmt_num(protein)} × 4）÷ {int(energy)} × 100% = □%，保留一位小数。",
            hint3,
        ],
        "steps": [
            f"第一步求部分能量：{fmt_num(protein)} × 4 = {fmt_num(part)}（千卡）。",
            f"第二步求占比：{fmt_num(part)} ÷ {int(energy)} × 100% ≈ {fmt_num(ratio, '0.1')}%。",
            f"所以蛋白质提供的能量约占总能量的 {fmt_num(ratio, '0.1')}%。",
            CARE_NOTE,
        ],
        "verify": {"formula": f"{fmt_num(protein)}*4/{int(energy)}*100", "tool": "calculate-price"},
        "diagnosis": {"type": "percent", "part": str(part), "whole": str(energy), "expected": str(ratio)},
        "primary_source": "nutrition",
    }


def _stage_8_average(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """题型八：统计与平均数 —— 平均价格。"""
    n = 3 + (ctx["difficulty"] - 1) // 3
    picks = _pick_many(ctx["rng"], ctx["meals"], n)
    total = sum((item["price"] for item in picks), Decimal("0"))
    average = (total / Decimal(n)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    names = "、".join(item["name"] for item in picks)

    rows = [[item["name"], fmt_money(item["price"])] for item in picks]

    return {
        "role": ctx["role"],
        "story": f"{ctx['role']}在{ctx['stage_name']}核对账目：{names} 的价格各不相同，"
                 f"他想用一个数概括这几样的价格水平。",
        "question": f"这 {n} 样餐品的平均价格是多少元？（保留两位小数）",
        "data_table": _table("今日菜单（真实数据）", ["餐品", "单价（元）"], rows),
        "answer_type": "number",
        "answer": fmt_money(average),
        "unit": "元",
        "hints": [
            "平均数的意思：把总量平均分成几份，求每份是多少。",
            "两步走：先求 总价 = □ + □ + …；再算 总价 ÷ 份数（" + str(n) + "）= □。",
            f"先算第一步：这 {n} 样餐品的总价是 {fmt_money(total)} 元。",
        ],
        "steps": [
            "第一步求总价：" + " + ".join(fmt_money(item["price"]) for item in picks) + f" = {fmt_money(total)}（元）。",
            f"第二步求平均数：{fmt_money(total)} ÷ {n} ≈ {fmt_money(average)}（元）。",
            f"平均数 {fmt_money(average)} 元落在最低价与最高价之间，说明结果合理。",
            CARE_NOTE,
        ],
        "verify": {"formula": f"({'+'.join(fmt_money(item['price']) for item in picks)})/{n}", "tool": "calculate-price"},
        "diagnosis": {"type": "average", "total": str(total), "n": n, "expected": str(average)},
        "primary_source": "menu",
    }


def _stage_9_discount(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """题型九：折扣与方案比较 —— 用最少的实付金额。"""
    coupons = ctx["coupons"]
    max_threshold = max(coupon["threshold"] for coupon in coupons)
    picks: List[Dict[str, Any]] = []
    basket = Decimal("0")
    pool = sorted(ctx["meals"], key=lambda item: item["price"], reverse=True)
    # 篮子最多 4 样：既够达到常见门槛，又不会把题目变成十几项连加。
    # 门槛极高（真实接口出现「满 999 减 X」之类）时，取前 4 样走「无券达标」分支。
    max_items = min(4, len(pool))
    for meal in pool[:max_items]:
        picks.append(meal)
        basket += meal["price"]
        if len(picks) >= 3 and basket >= max_threshold:
            break
    if len(picks) < 3:  # 菜单条数不足时的兜底
        for meal in pool:
            if meal not in picks:
                picks.append(meal)
                basket += meal["price"]
            if len(picks) >= 3:
                break

    applicable = [coupon for coupon in coupons if basket >= coupon["threshold"]]
    no_coupon = not applicable
    if no_coupon:
        best = {"name": "无可用券", "threshold": Decimal("0"), "discount": Decimal("0")}
    else:
        best = max(applicable, key=lambda coupon: coupon["discount"])
    pay = basket - best["discount"]
    names = "、".join(item["name"] for item in picks)
    coupon_text = "；".join(
        f"{coupon['name']}（满 {fmt_num(coupon['threshold'])} 元减 {fmt_num(coupon['discount'])} 元）"
        for coupon in coupons
    )

    rows = [[item["name"], fmt_money(item["price"])] for item in picks]
    if no_coupon:
        # 一张券也没达标时，答案就是原价合计，因此数据表里不能再给出「原价合计」，
        # 否则题目本身就把答案写出来了。
        rows.append(["券的情况", "没有一张达到门槛，按原价付款"])
        question = "这几样餐品逐张核对券的门槛，没有一张达到门槛，需要按原价付款。一共要付多少元？"
        hint2 = "两步走：先求原价合计；再逐张看门槛，都不达标时实付就等于原价。"
        hint3 = "先把这几样的价格加起来求出原价；再逐张看门槛，没有券达标时实付就等于原价。"
    else:
        rows.append(["原价合计", fmt_money(basket)])
        rows.append(["适用券", f"{best['name']} 减 {fmt_num(best['discount'])} 元"])
        question = f"这几样餐品原价合计 {fmt_money(basket)} 元，用其中实付金额最少的一张券，需要付多少元？"
        hint2 = "实付 = 原价合计 − 券的面额；把每张能用的券都试一遍，取实付金额最少的那一个。"
        hint3 = (
            f"原价合计 {fmt_money(basket)} 元，达到门槛且面额最大的是"
            f"「{best['name']}」，可减 {fmt_num(best['discount'])} 元。"
        )

    return {
        "role": ctx["role"],
        "story": f"{ctx['role']}在{ctx['stage_name']}结算，篮子里是{names}，按需购买不多拿。"
                 f"手上有这些券：{coupon_text}。",
        "question": question,
        "data_table": _table("菜单与优惠券（真实数据）", ["项目", "金额（元）"], rows),
        "answer_type": "number",
        "answer": fmt_money(pay),
        "unit": "元",
        "hints": [
            "先看每张券的门槛有没有达到，只有达到门槛的券才能用。",
            hint2,
            hint3,
        ],
        "steps": [
            "第一步求原价合计：" + " + ".join(fmt_money(item["price"]) for item in picks) + f" = {fmt_money(basket)}（元）。",
            "第二步判断门槛："
            + "；".join(
                f"{coupon['name']} 需要满 {fmt_num(coupon['threshold'])} 元，"
                + ("已达到" if basket >= coupon["threshold"] else "未达到")
                for coupon in coupons
            )
            + "。",
            f"第三步求实付：{fmt_money(basket)} − {fmt_num(best['discount'])} = {fmt_money(pay)}（元）。",
            CARE_NOTE,
        ],
        "verify": {"formula": f"{fmt_money(basket)}-{fmt_num(best['discount'])}", "tool": "calculate-price"},
        "diagnosis": {
            "type": "discount",
            "total": str(basket),
            "discount": str(best["discount"]),
            "expected": str(pay),
        },
        "primary_source": "coupons",
    }


_TEMPLATES = {
    1: _stage_1_add,
    2: _stage_2_sub,
    3: _stage_3_mul,
    4: _stage_4_div,
    5: _stage_5_unit,
    6: _stage_6_time,
    7: _stage_7_percent,
    8: _stage_8_average,
    9: _stage_9_discount,
}


# ---------------------------------------------------------------------------
# 提示泄露答案的通用兜底
# ---------------------------------------------------------------------------

_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")

SOURCE_TABLE_LABELS: Dict[str, str] = {
    "mcp": "实时数据",
    "cache": "缓存数据",
    "sample": "示例数据",
}
"""数据表标题里用来标注当前数据级别的文案。"""

# 用于剥掉模板里写死的「（真实数据）」等旧后缀，保证重复调用时不会叠加。
_TITLE_SUFFIX_RE = re.compile(r"（[^（）]*(?:数据|缓存)[^（）]*）")


def _table_title(base: str, source: str, fetched_at: Any = "") -> str:
    """按实际数据级别生成数据表标题。

    规则（与题目 ``data_source`` 字段同源，避免「表头写实时、字段写示例」）：

    * ``mcp``    → ``（实时数据）``
    * ``cache``  → ``（缓存数据 YYYY-MM-DD）``，无日期时退化为 ``（缓存数据）``
    * 其它       → ``（示例数据）``

    Args:
        base: 原始标题，例如 ``"今日菜单（真实数据）"``。
        source: 数据级别，取 ``data_source`` 的实际值。
        fetched_at: 抓取时间（仅 cache 分支用于拼日期）。

    Returns:
        带统一后缀的标题。
    """
    text = _TITLE_SUFFIX_RE.sub("", str(base)).strip() or str(base).strip()
    if str(source) == "mcp":
        return f"{text}（实时数据）"
    if str(source) == "cache":
        date_text = str(fetched_at or "").strip()[:10]
        return f"{text}（缓存数据 {date_text}）" if date_text else f"{text}（缓存数据）"
    return f"{text}（示例数据）"


def _answer_value(answer: Any) -> Optional[Decimal]:
    """把题目答案转成可比较的 :class:`Decimal`（兼容百分数写法）。"""
    text = str(answer or "").strip().replace("%", "")
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


_THOUSAND_SEP_RE = re.compile(r"(?<=\d)[,，\u00a0 ](?=\d{3}(?!\d))")
"""千分位分隔符（半角逗号 / 全角逗号 / 不换行空格 / 普通空格）。"""


def _normalize_separators(text: Any) -> str:
    """去掉数字里的千分位分隔符，让 ``1,234.5``、``1 234.5`` 都能被正确识别。"""
    return _THOUSAND_SEP_RE.sub("", str(text))


def _numbers_in(text: Any) -> List[Decimal]:
    """提取文本里的全部数值（先归一化千分位）。"""
    values: List[Decimal] = []
    for chunk in _NUMBER_RE.findall(_normalize_separators(text)):
        try:
            values.append(Decimal(chunk))
        except InvalidOperation:  # pragma: no cover - 正则已保证合法
            continue
    return values


def _answer_targets(answer: Any, answer_type: str = "number") -> List[Decimal]:
    """把答案展开成「所有等价写法」的数值集合。

    百分数题同时接受 ``19.6`` 与 ``0.196`` 两种写法（与 :func:`verify.check_answer`
    的判定口径保持一致），否则 ``_hint_leaks("0.196", "19.6")`` 会漏判。

    Args:
        answer: 题目答案。
        answer_type: ``"number"`` 或 ``"percent"``。

    Returns:
        需要比对的目标数值列表。
    """
    expected = _answer_value(answer)
    if expected is None:
        return []
    targets = [expected]
    if str(answer_type) == "percent" and expected != 0:
        targets.append(expected / Decimal("100"))
    return targets


def _hint_leaks(hint: str, answer: Any, answer_type: str = "number") -> bool:
    """判断一条提示是否直接报出了答案。

    只要提示里出现与答案数值相同的数字（容差 0.01），就认为这条提示把答案说破了：
    孩子完全可以照抄那个数字。百分数题会把 ``19.6`` 与 ``0.196`` 两种写法都视为等同，
    千分位写法（``1,234.5`` / ``1 234.5``）也会被归一化后再比对。

    Args:
        hint: 提示文本。
        answer: 题目答案。
        answer_type: ``"number"`` 或 ``"percent"``。

    Returns:
        ``True`` 表示泄露。
    """
    targets = _answer_targets(answer, answer_type)
    if not targets:
        return False
    for value in _numbers_in(hint):
        for target in targets:
            if abs(value - target) <= Decimal("0.01"):
                return True
    return False


def _blank_numbers(hint: str) -> str:
    """把提示里的**所有**数字替换成空格框（仅在需要彻底去数值时使用）。"""
    return _NUMBER_RE.sub("□", str(hint))


def _blank_leaked_numbers(hint: str, answer: Any, answer_type: str = "number") -> str:
    """只把「等于答案」的那个数字换成空格框，保留其余信息。

    比 :func:`_blank_numbers` 精准：例如 ``（5 × 4）÷ 500 × 100% = □%`` 在答案为 4 时
    只会变成 ``（5 × □）÷ 500 × 100% = □%``，5 / 500 / 100 这些有用条件不会被抹掉；
    券名 ``满20元减10元`` 在答案为 10 时只变成 ``满20元减□元``，不会出现「整条提示全是 □」的观感。

    Args:
        hint: 提示文本。
        answer: 题目答案。
        answer_type: ``"number"`` 或 ``"percent"``。

    Returns:
        只抹掉泄露数值的提示文本。
    """
    targets = _answer_targets(answer, answer_type)
    if not targets:
        return str(hint)
    text = _normalize_separators(hint)

    def _replace(match: "re.Match[str]") -> str:
        try:
            value = Decimal(match.group(0))
        except InvalidOperation:  # pragma: no cover - 正则已保证合法
            return match.group(0)
        for target in targets:
            if abs(value - target) <= Decimal("0.01"):
                return "□"
        return match.group(0)

    return _NUMBER_RE.sub(_replace, text)


def _sanitize_hints(hints: List[str], answer: Any, answer_type: str = "number") -> List[str]:
    """把泄露答案的提示改写为「只抹命中数字」的版本（尽量保留提示信息量）。"""
    result: List[str] = []
    for hint in hints:
        if _hint_leaks(hint, answer, answer_type):
            result.append(_blank_leaked_numbers(hint, answer, answer_type))
        else:
            result.append(hint)
    return result


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------


def generate(
    level_id: Optional[str] = None,
    kp_id: Optional[str] = None,
    grade: Optional[int] = None,
    salt: Any = None,
) -> Dict[str, Any]:
    """生成一道题。

    Args:
        level_id: 指定关卡（如 ``"3-2"``）；不指定则随机。
        kp_id: 按知识点筛选。
        grade: 按年级筛选（3-6）；口径是**知识点所属年级**，与返回值的 ``grade`` 字段同源。
        salt: 随机种子；给定相同 ``salt`` 与相同参数可复现同一道题。

    Returns:
        题目字典::

            {
              "level_id": "3-2", "stage": 3, "stage_name": "八戒买饼",
              "kp_id": "g3_mul_basic", "kp_name": "两位数乘一位数",
              "grade": 3, "stage_grade_hint": 3, "role": "猪八戒",
              "story": "...", "question": "...",
              "data_table": {"title": "今日菜单（实时数据）", "columns": [...], "rows": [...]},
              "answer_type": "number", "answer": "76.50", "unit": "元",
              "hints": ["...", "...", "..."], "steps": ["...", "..."],
              "verify": {"formula": "25.50*3", "tool": "calculate-price"},
              "data_source": "mcp"
            }

        ``grade`` 是知识点所属年级（权威展示口径）；``stage_grade_hint`` 是该关的难度定位，
        两者可能不同（同一关的 9 难可以跨年级）。``data_table.title`` 的后缀由
        ``data_source`` 决定：实时数据 / 缓存数据 YYYY-MM-DD / 示例数据。

    Raises:
        ValueError: 筛选条件下没有任何关卡。
        KeyError: 指定的 ``level_id`` 不存在。
    """
    candidates: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    for stage in load_journey().get("stages", []):
        for level in stage.get("levels", []):
            if level_id is not None and str(level.get("level_id")) != str(level_id):
                continue
            if kp_id is not None and str(level.get("kp_id")) != str(kp_id):
                continue
            candidates.append((stage, level))

    kp_map = kp_index()
    if grade is not None:
        # 年级口径：一律以「知识点所属年级」为准，筛选口径与展示字段完全同源，
        # 保证 play --grade 4 输出的每一题都真实标注「4 年级」。
        # 该年级没有匹配 level 的关卡会被自然跳过（不报错），因为它没有任何候选。
        candidates = [
            (stage, level)
            for stage, level in candidates
            if int(kp_map.get(str(level.get("kp_id")), {}).get("grade") or 0) == int(grade)
        ]
        if not candidates:
            raise ValueError(f"该年级（{grade} 年级）暂无可用题目，可选 3-6 年级。")

    if not candidates:
        if level_id is not None:
            raise KeyError(f"未找到关卡 {level_id!r}。")
        raise ValueError("按当前筛选条件找不到可用关卡。")

    base_seed = str(salt) if salt is not None else str(time.time_ns())
    rng = random.Random(base_seed)
    stage, level = rng.choice(candidates)

    merged = dict(level)
    merged.update(
        {
            "stage": int(stage.get("stage", 0)),
            "stage_name": str(stage.get("name", "")),
            "theme": str(stage.get("theme", "")),
            "role_focus": str(stage.get("role_focus", "唐僧")),
            "grade_hint": int(stage.get("grade_hint", 0)),
        }
    )

    template = _TEMPLATES.get(int(stage.get("stage", 0)))
    if template is None:  # pragma: no cover - 配置异常兜底
        template = _stage_1_add

    # 同一关卡换一组数据重抽，直到三级提示都不泄露答案；
    # 仍不行时把泄露那条提示里的数值改成空格框（结构保留、答案不泄露）。
    #
    # 种子稳定性（重要）：attempt 0 必须复用已经抽过关卡的同一个 rng 对象，
    # 保证「同一个 salt 永远出同一道题」在加入重抽机制前后完全一致——
    # 这样 README / 演示 / 评委复现里写死的 --seed 不会因为代码演进失效。
    # 只有确实发生泄露、需要换一组数据时才派生 f"{base_seed}#{attempt}"。
    ctx = _ctx(merged, rng)
    body: Dict[str, Any] = {}
    for attempt in range(6):
        attempt_rng = rng if attempt == 0 else random.Random(f"{base_seed}#{attempt}")
        ctx = _ctx(merged, attempt_rng)
        body = template(ctx)
        hints = list(body.get("hints") or [])
        answer_type = str(body.get("answer_type") or "number")
        if not any(_hint_leaks(hint, body.get("answer"), answer_type) for hint in hints):
            break
    else:
        body["hints"] = _sanitize_hints(
            list(body.get("hints") or []),
            body.get("answer"),
            str(body.get("answer_type") or "number"),
        )

    kp = kp_map.get(
        str(merged.get("kp_id")),
        {"id": str(merged.get("kp_id")), "name": "综合应用", "grade": int(stage.get("grade_hint", 0))},
    )
    source_key = str(body.pop("primary_source", "nutrition"))
    # 数据级别与抓取时间都取自已加载的数据集元信息，和题目 dict 的 data_source 严格同源。
    source_meta = ctx.get("source_meta", {}).get(source_key) or {}
    source_value = str(source_meta.get("source") or ctx["sources"].get(source_key) or "sample")
    source_fetched = source_meta.get("fetched_at") or ""

    data_table = dict(body["data_table"])
    data_table["title"] = _table_title(str(data_table.get("title", "")), source_value, source_fetched)

    problem: Dict[str, Any] = {
        "level_id": str(merged.get("level_id")),
        "stage": int(stage.get("stage", 0)),
        "stage_name": str(stage.get("name", "")),
        "kp_id": str(kp["id"]),
        "kp_name": str(kp["name"]),
        "grade": int(kp.get("grade") or stage.get("grade_hint", 3)),
        "stage_grade_hint": int(stage.get("grade_hint", 0)),
        "role": body["role"],
        "brief": str(merged.get("brief", "")),
        "story": body["story"],
        "question": body["question"],
        "data_table": data_table,
        "answer_type": body["answer_type"],
        "answer": body["answer"],
        "unit": body["unit"],
        "hints": list(body["hints"]),
        "steps": list(body["steps"]),
        "verify": dict(body["verify"]),
        "diagnosis": dict(body.get("diagnosis") or {}),
        "data_source": source_value,
    }
    return problem


def render_text(problem: Dict[str, Any]) -> str:
    """把题目渲染成等宽文本（CLI 使用）。"""
    lines: List[str] = []
    lines.append(f"【第 {problem['level_id']} 难】{problem['stage_name']} · "
                 f"第 {problem['stage']} 关")
    lines.append(f"年级：{problem['grade']} 年级　知识点：{problem['kp_name']}（{problem['kp_id']}）")
    lines.append("-" * 60)
    lines.append(problem["story"])
    table = problem["data_table"]
    lines.append("")
    lines.append(f"{table['title']}：")
    header = "  ".join(f"{col}" for col in table["columns"])
    lines.append("  " + header)
    for row in table["rows"]:
        lines.append("  " + "  ".join(str(cell) for cell in row))
    lines.append("")
    lines.append("题目：" + problem["question"])
    lines.append("-" * 60)
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover - 手工调试入口
    demo = generate(salt="demo")
    print(render_text(demo))
    print("参考答案：", demo["answer"], demo["unit"])
