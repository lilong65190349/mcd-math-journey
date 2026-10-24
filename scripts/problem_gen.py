"""麦麦取经录 —— 出题引擎。

职责
----
1. 读取 ``data/curriculum.json``（知识点图谱）与 ``data/storylines/``（故事线注册表 + 每条线的九关八十一难）。
2. 通过 :mod:`data_source` 取真实餐品数据（MCP → 缓存 → 示例快照）。
3. 用 9 个题型模板把真实数据变成小学 3-6 年级的应用题。
4. 每题给三级递进提示（**任何一级都不直接给出最终答案**）。

多故事线设计（重要）
--------------------
9 个数学模板**只按「第几关」索引**，故事线只提供关名 / 主题 / 角色 / brief 文案，
数学逻辑与故事题材完全解耦：新增一条线只需在 ``data/storylines/index.json`` 注册并给一份
同构 JSON，出题、校验、讲解、错题本全部自动复用。

``level_id`` 的格式约定：
* 默认线（西游记）保持历史格式 ``1-1`` … ``9-9``，向后兼容所有文档示例；
* 其他线用 ``<story>-<stage>-<difficulty>``，如 ``sanguo-3-2``。

题目结构见 :func:`generate` 的返回注释，字段名在整个项目里保持一致：
``story`` / ``level_id`` / ``kp_id`` / ``stage`` / ``answer`` / ``verify`` / ``data_source``。
"""

from __future__ import annotations

import json
import random
import re
import sys
import time
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from math import gcd
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

if __package__ in (None, ""):  # pragma: no cover - 脚本运行路径
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import data_source  # noqa: E402

PROJECT_ROOT: Path = Path(__file__).resolve().parents[1]
CURRICULUM_PATH: Path = PROJECT_ROOT / "data" / "curriculum.json"
STORYLINES_DIR: Path = PROJECT_ROOT / "data" / "storylines"
REGISTRY_PATH: Path = STORYLINES_DIR / "index.json"

DEFAULT_STORY: str = "xiyou"
"""未指定 ``--story`` 时使用的故事线（与改造前行为一致）。"""

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
    # 课标对齐（2026-10-25 考据整改）：小学除法笔算的上限是「除数是两位数」
    # （新人教四下；课标第二学段学业要求只到「能计算两位数乘除三位数」）。
    # 真实积分商品点数普遍偏大（2026-10-24 缓存实测 50 条中仅 1 条为两位数，
    # 一位除数商品为零），因此内置示例商品按除数位数分两组：
    #   一位除数（4-1～4-5，三下「除数是一位数的除法」）
    #   两位除数（4-6～4-9，四下「除数是两位数的除法」）
    # 仅在真实池缺位时启用，题面会经 primary_source="fallback" 如实标注「（示例数据）」。
    {"name": "苹果片兑换券", "points": Decimal("4")},
    {"name": "小薯兑换券", "points": Decimal("6")},
    {"name": "可乐(中)兑换券", "points": Decimal("8")},
    {"name": "圆筒冰淇淋兑换券", "points": Decimal("9")},
    {"name": "中薯条兑换券", "points": Decimal("12")},
    {"name": "麦乐鸡(5块)兑换券", "points": Decimal("15")},
    {"name": "巨无霸兑换券", "points": Decimal("25")},
    {"name": "麦辣鸡腿堡兑换券", "points": Decimal("32")},
]

_STATE: Optional[Dict[str, Any]] = None
_REGISTRY: Optional[Dict[str, Any]] = None
_STORY_CACHE: Dict[str, Dict[str, Any]] = {}


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


def load_state(force: bool = False, story: str = DEFAULT_STORY) -> Dict[str, Any]:
    """装载并缓存全部数据（进程内只取一次，避免重复网络请求）。

    Args:
        force: 强制重新抓取（忽略进程内缓存）。
        story: 故事线 id。真实餐品数据与故事线无关，这里接受该参数只为接口对称，
            调用方不必为了换故事线而重新取数。
    """
    del story  # 数据与题材解耦，取数逻辑不随故事线变化
    global _STATE
    if _STATE is not None and not force:
        return _STATE

    nutrition = data_source.get_nutrition_foods()
    meals = data_source.get_meals()
    coupons = data_source.get_coupons()
    calendar = data_source.get_campaign_calendar()
    points = data_source.get_points_products()
    now_info = data_source.get_now()

    real_foods = [item for item in (_norm_food(r) for r in nutrition.get("data") or []) if item]
    real_meals = [item for item in (_norm_meal(r) for r in meals.get("data") or []) if item]
    real_coupons = [item for item in (_norm_coupon(r) for r in coupons.get("data") or []) if item]
    real_points = [item for item in (_norm_points(r) for r in points.get("data") or []) if item]
    real_calendar = [item for item in (_norm_calendar(r) for r in calendar.get("data") or []) if item]

    now_payload = now_info.get("data") or {}
    now_dt = _parse_datetime(data_source.pick(now_payload, "datetime", "now", "time", "currentTime", default=""))
    if now_dt is None:
        now_dt = datetime.now().replace(microsecond=0)

    foods = real_foods or [dict(item) for item in _FALLBACK_FOODS]
    meal_list = real_meals or [dict(item) for item in _FALLBACK_MEALS]
    coupon_list = real_coupons or [dict(item) for item in _FALLBACK_COUPONS]
    point_list = real_points or [dict(item) for item in _FALLBACK_POINTS]
    if not real_calendar:
        calendar_list = [
            {
                "name": "默认活动期",
                "start": now_dt.replace(hour=0, minute=0, second=0),
                "end": now_dt.replace(hour=23, minute=59, second=0) + timedelta(days=27),
            }
        ]
    else:
        calendar_list = real_calendar

    # 有效数据级别：真实/缓存数据一条都规范化不出来时（字段缺失或结构不兼容），
    # 题目实际使用的是内置示例数据——级别必须改标注为「示例」，
    # 宁可降级，也不能出现「表头写实时、数据其实是示例」的失信标注。
    # 典型场景：available-coupons 只返回券名与状态，没有门槛/面额，
    # 第 9 关（折扣比较）因此自动降级并标注（示例数据）。
    raw_sources = {
        "nutrition": nutrition.get("source"),
        "menu": meals.get("source"),
        "coupons": coupons.get("source"),
        "calendar": calendar.get("source"),
        "points": points.get("source"),
        "now": now_info.get("source"),
    }
    used_fallback = {
        "nutrition": not real_foods,
        "menu": not real_meals,
        "coupons": not real_coupons,
        "calendar": not real_calendar,
        "points": not real_points,
        "now": False,
    }

    def _effective_source(key: str) -> str:
        raw = str(raw_sources.get(key) or "sample")
        if used_fallback.get(key) and raw != "sample":
            return "sample"
        return raw

    def _source_meta(key: str) -> Dict[str, Any]:
        if used_fallback.get(key):
            return {"source": "sample", "fetched_at": ""}
        return {
            "source": raw_sources.get(key),
            "fetched_at": {
                "nutrition": nutrition,
                "menu": meals,
                "coupons": coupons,
                "calendar": calendar,
                "points": points,
                "now": now_info,
            }[key].get("fetched_at"),
        }

    _STATE = {
        "foods": foods,
        "meals": meal_list,
        "coupons": coupon_list,
        "points": point_list,
        "calendar": calendar_list,
        "now": now_dt,
        "sources": {key: _effective_source(key) for key in raw_sources},
        # 数据级别 + 抓取时间：表格标题要用它标注「（实时数据）/（缓存数据 YYYY-MM-DD）/（示例数据）」，
        # 必须与题目 dict 里的 data_source 同源，否则会出现「表头写实时、字段写示例」的矛盾。
        # 「fallback」是第 5 关专用级别：净含量（克）在当前 MCP 只读工具里不提供，
        # 该关在没有真实净含量时改用内置示例餐品，并以（示例数据）如实标注。
        "source_meta": {key: _source_meta(key) for key in raw_sources}
        | {"fallback": {"source": "sample", "fetched_at": ""}},
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


# ---------------------------------------------------------------------------
# 故事线注册表
# ---------------------------------------------------------------------------


def load_registry(force: bool = False) -> Dict[str, Any]:
    """读取 ``data/storylines/index.json``（故事线注册表）。"""
    global _REGISTRY
    if _REGISTRY is not None and not force:
        return _REGISTRY
    with REGISTRY_PATH.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload.get("storylines"), list) or not payload["storylines"]:
        raise ValueError(f"故事线注册表格式异常：{REGISTRY_PATH}")
    _REGISTRY = payload
    return payload


def list_storylines(only_available: bool = True) -> List[Dict[str, Any]]:
    """列出故事线。

    Args:
        only_available: 只返回 ``available: true`` 的线（上线口径，CLI 与 demo 都用这个）。

    Returns:
        每项含 ``id`` / ``name`` / ``subtitle`` / ``available`` / ``file`` / ``era_note``。
    """
    items: List[Dict[str, Any]] = []
    for entry in load_registry().get("storylines", []):
        available = bool(entry.get("available"))
        if only_available and not available:
            continue
        items.append(
            {
                "id": str(entry.get("id")),
                "name": str(entry.get("name") or entry.get("id")),
                "subtitle": str(entry.get("subtitle") or ""),
                "available": available,
                "file": str(entry.get("file") or f"{entry.get('id')}.json"),
                "era_note": str(entry.get("era_note") or ""),
            }
        )
    return items


def story_ids(only_available: bool = True) -> List[str]:
    """返回故事线 id 列表（默认只含已上线的线）。"""
    return [item["id"] for item in list_storylines(only_available=only_available)]


def default_story() -> str:
    """返回默认故事线 id；注册表里的 default 若未上线，则退化为第一条可用线。"""
    ids = story_ids(only_available=True)
    wanted = str(load_registry().get("default") or DEFAULT_STORY)
    if wanted in ids:
        return wanted
    return ids[0] if ids else DEFAULT_STORY


def _registry_entry(story_id: str) -> Dict[str, Any]:
    """按 id 取注册表条目；未注册则抛 :class:`KeyError`。"""
    for entry in load_registry().get("storylines", []):
        if str(entry.get("id")) == str(story_id):
            return entry
    raise KeyError(
        f"未注册的故事线 {story_id!r}，可用：{'、'.join(story_ids(only_available=False))}。"
    )


def is_available(story_id: str) -> bool:
    """该故事线是否已上线（``available: true``）。"""
    try:
        return bool(_registry_entry(story_id).get("available"))
    except KeyError:
        return False


def load_storyline(story_id: Optional[str] = None) -> Dict[str, Any]:
    """读取某条故事线的九关配置（进程内缓存）。

    Args:
        story_id: 故事线 id；``None`` 表示默认线。

    Returns:
        故事线 JSON（含 ``id`` / ``name`` / ``subtitle`` / ``era_note`` / ``roles`` / ``stages``）。

    Raises:
        KeyError: 未注册的故事线。
    """
    sid = str(story_id or default_story())
    if sid in _STORY_CACHE:
        return _STORY_CACHE[sid]
    entry = _registry_entry(sid)
    path = STORYLINES_DIR / str(entry.get("file") or f"{sid}.json")
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    payload.setdefault("id", sid)
    payload.setdefault("name", entry.get("name") or sid)
    payload.setdefault("subtitle", entry.get("subtitle") or "")
    payload.setdefault("era_note", entry.get("era_note") or "")
    payload.setdefault("roles", {})
    _STORY_CACHE[sid] = payload
    return payload


def storyline_name(story_id: Optional[str] = None) -> str:
    """返回故事线中文名（取不到时退化为 id）。"""
    sid = str(story_id or default_story())
    try:
        return str(load_storyline(sid).get("name") or sid)
    except (KeyError, OSError, json.JSONDecodeError):
        return sid


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


def list_stages(story: Optional[str] = None) -> List[Dict[str, Any]]:
    """列出某条故事线的九个关卡（含每一难的元数据）。

    Args:
        story: 故事线 id；``None`` 表示默认线。
    """
    data = load_storyline(story)
    sid = str(data.get("id"))
    stages: List[Dict[str, Any]] = []
    for stage in data.get("stages", []):
        stages.append(
            {
                "story": sid,
                "story_name": str(data.get("name") or sid),
                "stage": int(stage.get("stage", 0)),
                "name": str(stage.get("name", "")),
                "scene": str(stage.get("scene", "")),
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


def _find_level(level_id: str, story: str) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """在指定故事线里按 ``level_id`` 找「关卡 + 这一难」。"""
    for stage in load_storyline(story).get("stages", []):
        for level in stage.get("levels", []):
            if str(level.get("level_id")) == str(level_id):
                return stage, level
    return None


def resolve_level(
    level_id: str, story: Optional[str] = None
) -> Tuple[str, Tuple[Dict[str, Any], Dict[str, Any]]]:
    """跨故事线定位关卡。

    规则：
    * 显式给了 ``story``：先在该线里找 ``level_id``；找不到时再试 ``<story>-<level_id>``
      （这样 ``--story sanguo --level 3-2`` 这种简写也能用）。
    * 没给 ``story``：先在默认线（西游记）里找，再按注册顺序扫其他已上线的线，
      因此 ``3-2`` 仍指向西游记第 3 关第 2 难（完全向后兼容），
      而 ``sanguo-3-2`` 能自动定位到三国线。

    Returns:
        ``(story_id, (stage, level))``。

    Raises:
        KeyError: 关卡不存在。
    """
    text = str(level_id)
    if story:
        sid = str(story)
        found = _find_level(text, sid)
        if found:
            return sid, found
        found = _find_level(f"{sid}-{text}", sid)
        if found:
            return sid, found
        raise KeyError(
            f"在故事线「{storyline_name(sid)}」里未找到关卡 {level_id!r}，"
            f"可用关卡形如 {sid}-1-1 … {sid}-9-9。"
        )

    order = [default_story()] + [sid for sid in story_ids(True) if sid != default_story()]
    for sid in order:
        found = _find_level(text, sid)
        if found:
            return sid, found
    raise KeyError(
        f"未找到关卡 {level_id!r}，可用关卡形如 1-1 … 9-9（西游记）或 sanguo-3-2（其他故事线）。"
    )


def level_id_for(story: Optional[str], stage: int, difficulty: int) -> str:
    """按约定拼出 ``level_id``：默认线是 ``3-2``，其他线是 ``sanguo-3-2``。

    闯关系统按「关号 + 难度档」出题时用它落成具体的 ``level_id``，
    命名规则与 ``data/storylines/*.json`` 里的既有写法完全一致（自检里有断言）。
    """
    sid = str(story or default_story())
    if sid == default_story():
        return f"{int(stage)}-{int(difficulty)}"
    return f"{sid}-{int(stage)}-{int(difficulty)}"


def get_level(level_id: str, story: Optional[str] = None) -> Dict[str, Any]:
    """按 ``level_id`` 取出「关卡 + 这一难」的合并信息（支持跨故事线）。

    Args:
        level_id: ``1-1`` 这类默认线关卡号，或 ``sanguo-3-2`` 这类带线前缀的关卡号。
        story: 限定在某条线里查；``None`` 时按 :func:`resolve_level` 的规则自动定位。

    Raises:
        KeyError: 关卡号不存在。
    """
    sid, (stage, level) = resolve_level(level_id, story)
    data = load_storyline(sid)
    merged = dict(level)
    merged.update(
        {
            "story": sid,
            "story_name": str(data.get("name") or sid),
            "stage": int(stage.get("stage", 0)),
            "stage_name": str(stage.get("name", "")),
            "scene": str(stage.get("scene", "")),
            "theme": str(stage.get("theme", "")),
            "role_focus": str(stage.get("role_focus", "")),
            "grade_hint": int(stage.get("grade_hint", 0)),
        }
    )
    return merged


# ---------------------------------------------------------------------------
# 九个题型模板
# ---------------------------------------------------------------------------


def _ctx(level: Dict[str, Any], rng: random.Random) -> Dict[str, Any]:
    """组装模板上下文。

    难度取 ``level_id`` 的**最后一段**：``3-2`` 与 ``sanguo-3-2`` 都解析成 2，
    这样同一套数学模板对任何故事线都成立。
    """
    state = load_state()
    level_id = str(level.get("level_id", "1-1"))
    tail = level_id.split("-")[-1]
    difficulty = int(tail) if tail.isdigit() else 1
    story_id = str(level.get("story") or default_story())
    roles = load_storyline(story_id).get("roles") or {}
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
        # 角色一律从故事线 JSON 读，代码里不再硬编码任何人物名。
        "role": str(level.get("role_focus") or roles.get("review") or "领队"),
        "stage_name": str(level.get("stage_name") or "路上"),
        # scene 是「这条线这一关发生在哪儿」的具体地点（如「桃园林间」「淡水舱」）。
        # 关名（如「南京启航」）是事件名，不能当地点用，否则会拼出「在南京启航歇脚」这种病句。
        "scene": str(level.get("scene") or ""),
        "story": story_id,
        "story_name": str(level.get("story_name") or storyline_name(story_id)),
    }


def _place(ctx: Dict[str, Any]) -> str:
    """把 scene 渲染成状语；没有 scene 时返回空串（退化成不指定地点的写法）。"""
    scene = str(ctx.get("scene") or "").strip()
    return f"在{scene}" if scene else ""


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
    # 样数按难度逐级递增 2→10（每档唯一），9 难因此各有不同题面；上限取数据池大小。
    n = min(len(ctx["foods"]), 2 + (ctx["difficulty"] - 1))
    picks = _pick_many(ctx["rng"], ctx["foods"], n)
    kcal = [int(item["kcal"]) for item in picks]
    total = sum(kcal)
    names = "、".join(item["name"] for item in picks)

    # 净含量列仅在数据里真的有净含量时出现：MCP 营养接口不提供克数，
    # 此时不能拿 0 充数——宁可少一列，也不能显示假数据。
    has_weight = all(item["weight"] > 0 for item in picks)
    rows = [[item["name"], str(item["kcal"])] for item in picks]
    columns = ["餐品", "能量（千卡）"]
    if has_weight:
        columns.append("净含量（克）")
        rows = [row + [str(item["weight"])] for row, item in zip(rows, picks)]
    if n >= 3:
        hint3 = f"先算前两样：{kcal[0]} + {kcal[1]} = {kcal[0] + kcal[1]} 千卡。"
    else:
        low = 2 * min(kcal)
        high = 2 * max(kcal)
        hint3 = f"先估一估：两样都在 {min(kcal)}～{max(kcal)} 千卡之间，所以答案一定在 {low}～{high} 千卡之间。"

    return {
        "role": ctx["role"],
        # 「这几样」而不是「这一份」：前面列了 2~10 样，用单数会自相矛盾。
        "story": f"{ctx['role']}{_place(ctx)}清点今天的餐品：{names}。"
                 f"他想先弄清楚这几样加起来一共有多少能量。",
        "question": f"这 {n} 样餐品一共多少千卡？",
        "data_table": _table("今日餐品营养（真实数据）", columns, rows),
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
        # 千卡是能量单位，不能说「行程按 X 千卡安排」；场景词也一并落进叙述里。
        "story": f"{ctx['role']}{_place(ctx)}把这一程的餐食额度定在 {budget} 千卡，挑了{names}。"
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
    # 份数按难度 2→10 递增（原上限 9 让 8、9 难撞成同一题）。
    qty = 2 + (ctx["difficulty"] - 1)
    total = price * qty
    int_part = int(price)
    frac_part = price - Decimal(int_part)
    # 教学口径按难度分两段（2026-10-25 考据整改 P1-2）：
    #   1-4 难 → g3_money「元角分」：先算元、再算角，最后合并；
    #   5-9 难 → g5_decimal_muldiv「小数乘法」：先按整数算，再点小数点。
    if frac_part == 0:
        upper = (Decimal(int_part) + Decimal("1")) * qty
        range_hint = (
            f"先估一估范围：把单价往大看成 {int_part + 1} 元，{qty} 份也不会超过 {fmt_money(upper)} 元，"
            f"所以答案应当略小于这个数。"
        )
    elif ctx["difficulty"] <= 4:
        jiao = int(frac_part * 100)
        range_hint = (
            f"拆开算：{int_part} 元 × {qty} = {int_part * qty} 元；"
            f"{jiao} 角 × {qty} = {jiao * qty} 角，"
            f"其中 {jiao * qty // 10} 元 {(jiao * qty) % 10} 角；两部分合起来就是答案。"
        )
    else:
        scaled = int(price * 100) * qty
        range_hint = (
            f"小数乘法先按整数算：把 {fmt_money(price)} 去掉小数点看成 {int(price * 100)}，"
            f"{int(price * 100)} × {qty} = {scaled}，再把小数点点回两位，得 {fmt_money(total)}。"
        )
    if ctx["difficulty"] <= 4:
        hint2 = f"{fmt_money(price)} × {qty} = □，注意结果要带两位小数。"
        step2 = f"第二步算角：{int(frac_part * 100)} 角 × {qty} = {int(frac_part * 100) * qty} 角。"
        step3 = (
            f"第三步合并：{int_part * qty} 元 ＋ {int(frac_part * 100) * qty // 10} 元 "
            f"{(int(frac_part * 100) * qty) % 10} 角 = {fmt_money(total)} 元。"
        )
    else:
        hint2 = f"{fmt_money(price)} × {qty} = □，结果要保留两位小数（先按整数乘，再点小数点）。"
        step2 = f"第二步去掉小数点按整数算：{int(price * 100)} × {qty} = {int(price * 100) * qty}。"
        step3 = f"第三步把小数点点回两位：{int(price * 100) * qty} → {fmt_money(total)} 元。"

    return {
        "role": ctx["role"],
        "story": f"{ctx['role']}{_place(ctx)}看到{meal['name']}每份 {fmt_money(price)} 元。"
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
            hint2,
            range_hint,
        ],
        "steps": [
            f"一份 {fmt_money(price)} 元，要买 {qty} 份，就是求 {qty} 个 {fmt_money(price)} 相加。",
            step2,
            step3,
            f"所以一共 {fmt_money(total)} 元。",
            CARE_NOTE,
        ],
        "verify": {"formula": f"{fmt_money(price)}*{qty}", "tool": "calculate-price"},
        "diagnosis": {"type": "mul", "a": str(price), "b": qty, "expected": str(total)},
        "primary_source": "menu",
    }


def _stage_4_div(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """题型四：除法与余数 —— 积分能兑换几份。

    课标对齐（2026-10-25 考据整改 P0-3）：小学除法笔算的上限是「除数是两位数」
    （新人教四下；课标第二学段学业要求只到「能计算两位数乘除三位数」）。
    旧实现直接 ``rng.choice(ctx["points"])``，真实数据下除数达 3-4 位（实测
    「26774 ÷ 6666」），超出小学任何年级的除法笔算要求，且与
    「除数是两位数的除法」的关卡标签自相矛盾。

    现在按关卡期望的除数位数选池（关卡绑定：4-1～4-5 → g3_div_basic 除数一位数，
    4-6～4-9 → g4_div_2digit 除数为两位数）：
      难度 1-5 → 真实池中点数 2-9 的商品；池空时用内置一位除数示例商品；
      难度 6-9 → 真实池中点数 10-99 的商品；不足时用内置两位除数示例商品补足。
    选中内置示例时 ``primary_source="fallback"``，题面表格标题经 _table_title
    统一改标注「（示例数据）」——宁可降级，不编数字、不超纲。
    """
    difficulty = ctx["difficulty"]
    want_digits = 1 if difficulty <= 5 else 2
    lo, hi = (2, 9) if want_digits == 1 else (10, 99)

    real_pool = [item for item in ctx["points"] if lo <= int(item["points"]) <= hi]
    real_values = {int(item["points"]) for item in real_pool}
    builtin = [
        dict(item)
        for item in _FALLBACK_POINTS
        if lo <= int(item["points"]) <= hi and int(item["points"]) not in real_values
    ]
    # 真实商品优先；同位数内置示例只补真实池没有的除数，保证 9 难的除数各有变化。
    pool: List[tuple] = [(item, True) for item in real_pool] + [(item, False) for item in builtin]
    # 按「难度轮换 + 种子偏移」取：相邻难度拿到不同除数（否则同一除数会连着出现，
    # 演示页里九道题全是同一个商品名），同时换一次随机种子仍会换一批餐品。
    ordered = sorted(pool, key=lambda pair: (int(pair[0]["points"]), str(pair[0]["name"])))
    offset = int(ctx["rng"].random() * len(ordered))
    product, is_real = ordered[(offset + difficulty - 1) % len(ordered)]
    unit_points = int(product["points"])
    # 商随难度严格递增（2→10）：除数被压到一位/两位后，余数会被「余数 < 除数」卡住，
    # 若商再按两难一档递进，相邻两难会得到同一组（商，余数）→ 题面撞车
    # （selfcheck 的「81 条不同题面」会因此掉到 78）。商严格递增后每难都不同。
    quotient = 1 + difficulty
    remainder = min(unit_points - 1, 5 + 15 * (difficulty - 1) // 2)
    total_points = unit_points * quotient + remainder
    source_key = "points" if is_real else "fallback"

    # 试商提示按除数位数给不同的方法（一位除数口算试商，两位除数用整十数估）。
    if want_digits == 1:
        trial = (
            f"先想乘法口诀：{unit_points} × {max(quotient - 1, 1)} = "
            f"{unit_points * max(quotient - 1, 1)}，比 {total_points} 小；"
            f"再想 {unit_points} × {quotient} = {unit_points * quotient}，"
            f"加上余数正好是 {total_points}。"
        )
    else:
        round_to = (unit_points // 10) * 10 or 10
        trial = (
            f"把除数 {unit_points} 看作接近的整十数 {round_to} 来试商："
            f"{round_to} × {max(quotient - 1, 1)} = {round_to * max(quotient - 1, 1)}，"
            f"再慢慢往上加，试到 {unit_points} × {quotient} = {unit_points * quotient} 不超过 {total_points}。"
        )

    return {
        "role": ctx["role"],
        "story": f"{ctx['role']}{_place(ctx)}清点手里的 {total_points} 积分。"
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
            "问「最多能换几份」，就是问总量里面包含几个单价，用带余数的除法。",
            f"{total_points} ÷ {unit_points} = □（份）…… □（积分），商就是份数，余数是剩下的积分。",
            trial,
        ],
        "steps": [
            f"把 {total_points} 积分按每份 {unit_points} 积分去分，列式：{total_points} ÷ {unit_points}。",
            f"商是 {quotient}，余数是 {remainder}（{unit_points} × {quotient} = {unit_points * quotient}，"
            f"{total_points} − {unit_points * quotient} = {remainder}）。",
            f"余数 {remainder} 比每份所需 {unit_points} 积分小，说明不能再换一份，所以最多 {quotient} 份，余 {remainder} 积分。",
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
        "primary_source": source_key,
    }


def _stage_5_unit(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """题型五：小数与单位换算 —— 克换算成千克。"""
    # 净含量（克）在当前 MCP 只读工具里不提供（营养接口只有能量/蛋白质等）。
    # 有真实净含量就用真实的；一条都没有时改用内置示例餐品，
    # 并把 primary_source 标成 fallback——题面会如实显示「（示例数据）」。
    weighted = [item for item in ctx["foods"] if item["weight"] > 0]
    use_fallback = not weighted
    candidates = weighted if weighted else [dict(item) for item in _FALLBACK_FOODS]
    food = ctx["rng"].choice(candidates)
    weight = Decimal(food["weight"])
    # 份数按难度 2→10 递增（原上限 9 让 8、9 难撞成同一题）。
    qty = 2 + (ctx["difficulty"] - 1)
    total_g = weight * qty
    total_kg = (total_g / Decimal("1000")).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)

    # 课标对齐（2026-10-25 考据整改 P1-3）：低难档只问「一共多少克」（整数答案，
    # 对应三上质量单位 + 多位数乘一位数）；高难档才问「多少千克」（换算 + 小数表示，
    # 对应四下「小数的认识」）。旧实现 9 难全问千克，三位小数答案对低年级超纲。
    ask_kg = ctx["difficulty"] >= 5
    if ask_kg:
        question = f"每份{food['name']}净含量 {fmt_num(weight)} 克，{qty} 份一共多少千克？"
        answer = fmt_num(total_kg, "0.001")
        unit = "千克"
        hints = [
            "先求总克数（乘法），再想克和千克之间的进率。",
            f"{fmt_num(weight)} × {qty} = □ 克；把克换成千克要除以 1000，即 □ ÷ 1000 = □ 千克。",
            f"先算第一步：{fmt_num(weight)} × {qty} = {fmt_num(total_g)} 克。",
        ]
        steps = [
            f"先求总克数：{fmt_num(weight)} × {qty} = {fmt_num(total_g)}（克）。",
            f"克换算成千克要除以进率 1000：{fmt_num(total_g)} ÷ 1000 = {fmt_num(total_kg, '0.001')}（千克）。",
            f"所以一共 {fmt_num(total_kg, '0.001')} 千克。",
            CARE_NOTE,
        ]
        diagnosis = {
            "type": "unit",
            "grams": str(total_g),
            "kg": str(total_kg),
            "expected": str(total_kg),
        }
    else:
        question = f"每份{food['name']}净含量 {fmt_num(weight)} 克，{qty} 份一共多少克？"
        answer = fmt_num(total_g)
        unit = "克"
        hints = [
            "求几个相同净含量合起来，用乘法比连加快。",
            f"{fmt_num(weight)} × {qty} = □ 克。",
            f"先估一估：{fmt_num(weight)} 往大看取整，{qty} 份也不会超过那个数乘 {qty}。",
        ]
        steps = [
            f"每份 {fmt_num(weight)} 克，一共 {qty} 份，就是求 {qty} 个 {fmt_num(weight)} 相加。",
            f"列式：{fmt_num(weight)} × {qty} = {fmt_num(total_g)}（克）。",
            f"所以一共 {fmt_num(total_g)} 克。",
            CARE_NOTE,
        ]
        diagnosis = {"type": "mul", "a": str(weight), "b": qty, "expected": str(total_g)}

    return {
        "role": ctx["role"],
        "story": f"{ctx['role']}{_place(ctx)}把东西过了一遍秤：{food['name']}每份净含量 {fmt_num(weight)} 克，"
                 f"一共准备了 {qty} 份，需要按{'千克' if ask_kg else '克'}登记。",
        "question": question,
        "data_table": _table(
            "净含量（真实数据）",
            ["餐品", "每份净含量（克）", "份数", "进率"],
            [[food["name"], fmt_num(weight), str(qty), "1 千克 = 1000 克"]],
        ),
        "answer_type": "number",
        "answer": answer,
        "unit": unit,
        "hints": hints,
        "steps": steps,
        "verify": {"formula": f"{fmt_num(weight)}*{qty}" + ("/1000" if ask_kg else ""), "tool": "calculate-price"},
        "diagnosis": diagnosis,
        "primary_source": "fallback" if use_fallback else "nutrition",
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
        "story": f"{ctx['role']}{_place(ctx)}准备出发走一程。活动期「{campaign['name']}」从 "
                 f"{start.strftime('%Y-%m-%d')} 持续到 {end.strftime('%Y-%m-%d')}"
                 f"（以活动日历窗口内显示的起止为准），两头都算。"
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
            "小提醒：活动日历只返回当前窗口内的日期，题面里的起止就是窗口内看到的起止。",
            CARE_NOTE,
        ],
        "verify": {"formula": f"{days}*{daily_km}", "tool": "calculate-price"},
        "diagnosis": {"type": "mul", "a": str(days), "b": daily_km, "expected": str(total_km)},
        "primary_source": "calendar",
    }


def _stage_7_fraction_percent(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """题型七：分数与百分数 —— 按难度三段式。

    课标对齐（2026-10-25 考据整改 P0-1）：旧实现 9 难全是百分数题，但 7-1～7-6 绑的是
    「分数初步（3 年级）/分数加减法（5 年级）」——``play --grade 3/5`` 会输出孩子
    没学过的百分数题（百分数在 2022 版课标属第三学段、人教版六上）。

    现在按课标把第 7 关拆成三段，数据源不变（仍是营养表）：
      难度 1-3（g3_fraction_intro，三上「分数的初步认识」）：
          整数 × 几分之一 —— 「一份含蛋白质 P 克，吃掉了它的几分之一，吃掉多少克？」
      难度 4-6（g5_fraction_addsub，五下「分数的认识与约分」）：
          部分占整体的几分之几 —— 「一餐蛋白质共 A 克，其中一样占 B 克，占几分之几？」
          （答案要约成最简分数；等价分数判对，讲解里展示约分过程）
      难度 7-9（g6_percent，六上「百分数」）：保持原百分数题不变。
    """
    candidates = [item for item in ctx["foods"] if item["protein"] > 0 and item["kcal"] > 0]
    if not candidates:
        candidates = ctx["foods"]
    difficulty = ctx["difficulty"]

    # ---- 分支一：难度 1-3，整数 × 几分之一（分数初步，三上） ----
    if difficulty <= 3:
        denom = {1: 2, 2: 3, 3: 4}[difficulty]
        denom_word = {2: "一半", 3: "三分之一", 4: "四分之一"}[denom]
        # 挑蛋白质能被分母整除的餐品，保证答案是整数克（三上不学小数除法）。
        divisible = [
            item for item in candidates
            if (Decimal(str(item["protein"])) % denom) == 0 and Decimal(str(item["protein"])) > 0
        ]
        use_fallback = not divisible
        pool = divisible if divisible else [
            item for item in _FALLBACK_FOODS
            if (item["protein"] % denom) == 0 and item["protein"] > 0
        ]
        if not pool:  # 理论上不会发生（内置示例含偶数蛋白质）；兜底用巨无霸改问一半
            pool, denom, denom_word = [{"name": "巨无霸", "protein": Decimal("26")}], 2, "一半"
        food = ctx["rng"].choice(pool)
        protein = Decimal(str(food["protein"]))
        eaten = protein / denom
        return {
            "role": ctx["role"],
            "story": f"{ctx['role']}{_place(ctx)}翻看营养标签：{food['name']}含蛋白质 "
                     f"{fmt_num(protein)} 克。这一份被平均分给 {denom} 个人，每人分到同样多。",
            "question": f"{food['name']}含蛋白质 {fmt_num(protein)} 克，每人分到其中{denom_word}，"
                        f"每人分到多少克蛋白质？",
            "data_table": _table(
                "营养标签（真实数据）",
                ["餐品", "蛋白质（克）", "平均分成", "每人取几份"],
                [[food["name"], fmt_num(protein), f"{denom} 份", "1 份"]],
            ),
            "answer_type": "number",
            "answer": fmt_num(eaten),
            "unit": "克",
            "hints": [
                f"「其中{denom_word}」就是把 {fmt_num(protein)} 克平均分成 {denom} 份，取其中 1 份，用除法。",
                f"{fmt_num(protein)} ÷ {denom} = □（克）。",
                f"想乘法口诀倒推：{denom} × □ = {fmt_num(protein)}，这个 □ 就是每人的克数。",
            ],
            "steps": [
                f"把 {fmt_num(protein)} 克平均分成 {denom} 份，列式：{fmt_num(protein)} ÷ {denom}。",
                f"算一算：{fmt_num(protein)} ÷ {denom} = {fmt_num(eaten)}（克）。",
                f"所以每人分到 {fmt_num(eaten)} 克蛋白质，也就是这{denom}份里的 1 份，即 1/{denom}。",
                CARE_NOTE,
            ],
            "verify": {"formula": f"{fmt_num(protein)}/{denom}", "tool": "calculate-price"},
            "diagnosis": {
                "type": "fraction_eat",
                "protein": str(protein),
                "denom": denom,
                "expected": str(eaten),
            },
            "primary_source": "fallback" if use_fallback else "nutrition",
        }

    # ---- 分支二：难度 4-6，部分占整体的几分之几（分数的认识与约分，五下） ----
    if difficulty <= 6:
        pool = [item for item in candidates if item["protein"] > 0]
        if not pool:
            pool = _FALLBACK_FOODS
        # 按难度控制约分工作量：低难档给「好约」的分数（分母小），高难档给需要真约分的。
        target_gcd = {4: "large", 5: "medium", 6: "small"}[difficulty]
        best = None
        for _ in range(24):
            pair = ctx["rng"].sample(pool, 2) if len(pool) >= 2 else [pool[0], pool[0]]
            a = Decimal(str(pair[0]["protein"]))
            b = Decimal(str(pair[1]["protein"]))
            if a <= 0 or b <= 0 or a + b > 200:
                continue
            num, den = int(a), int(a + b)
            g = gcd(num, den)
            red_num, red_den = num // g, den // g
            if red_den > 30:  # 分母太大超出五下练习范围
                continue
            score = {"large": -red_den, "medium": -abs(red_den - 8), "small": red_den}[target_gcd]
            if best is None or score > best[0]:
                best = (score, pair, a, b, num, den, g, red_num, red_den)
            if target_gcd == "large" and red_den <= 4:
                break
        if best is None:  # 数据异常时的兜底
            pair = ctx["rng"].sample(pool, 2) if len(pool) >= 2 else [pool[0], pool[0]]
            a, b = Decimal(str(pair[0]["protein"])), Decimal(str(pair[1]["protein"]))
            num, den = int(a), int(a + b)
            g = gcd(num, den)
            best = (0, pair, a, b, num, den, g, num // g, den // g)
        _, pair, a, b, num, den, g, red_num, red_den = best
        item_a, item_b = pair[0], pair[1]
        return {
            "role": ctx["role"],
            "story": f"{ctx['role']}{_place(ctx)}搭配一餐：{item_a['name']}含蛋白质 {fmt_num(a)} 克，"
                     f"{item_b['name']}含蛋白质 {fmt_num(b)} 克。他想知道第一样占这两样蛋白质总量的几分之几。",
            "question": f"把{item_a['name']}和{item_b['name']}的蛋白质合起来算一份，"
                        f"{item_a['name']}的蛋白质占这份的几分之几？（写成最简分数）",
            "data_table": _table(
                "营养标签（真实数据）",
                ["餐品", "蛋白质（克）", "占蛋白质总量"],
                [
                    [item_a["name"], fmt_num(a), f"?/{den}"],
                    [item_b["name"], fmt_num(b), "——"],
                    ["合计", fmt_num(a + b), f"{den} 份为整体"],
                ],
            ),
            "answer_type": "fraction",
            "answer": f"{red_num}/{red_den}",
            "unit": "",
            "hints": [
                "求「一个数占另一个数的几分之几」：部分 ÷ 整体，写成分数。",
                f"部分是 {fmt_num(a)} 克，整体是 {fmt_num(a)} + {fmt_num(b)} = {num} 克，"
                f"先写成分数 {num}/{den}。",
                f"再看看能不能约分：{num} 和 {den} 的最大公因数是 {g}，"
                f"分子分母同时除以 {g}，得到 {red_num}/{red_den}。",
            ],
            "steps": [
                f"先求整体：{fmt_num(a)} + {fmt_num(b)} = {num}（克）。",
                f"再求部分占整体：{num}/{den}。",
                f"约分：{num} 和 {den} 的最大公因数是 {g}，{num} ÷ {g} = {red_num}，"
                f"{den} ÷ {g} = {red_den}，所以是 {red_num}/{red_den}。",
                f"验算：{red_num}/{red_den} 就是 {num} 份里面的 {red_num} 份，和 {num}/{den} 一样大。",
                CARE_NOTE,
            ],
            "verify": {"formula": f"{num}/{den}", "tool": "calculate-price"},
            "diagnosis": {
                "type": "fraction_part",
                "part": str(a),
                "whole": str(a + b),
                "expected": f"{red_num}/{red_den}",
            },
            "primary_source": "nutrition",
        }

    # ---- 分支三：难度 7-9，百分数（六上，保持原实现） ----
    sorted_candidates = sorted(candidates, key=lambda item: (int(item["kcal"]), str(item["name"])))
    span = len(sorted_candidates)
    index = (difficulty - 1) * (span - 1) // 8 if span > 1 else 0
    food = sorted_candidates[max(0, min(span - 1, index))]
    protein = Decimal(str(food["protein"]))
    energy = Decimal(str(food["kcal"]))
    part = protein * Decimal("4")
    ratio = (part / energy * Decimal("100")).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    if abs(part - ratio) <= Decimal("0.05"):
        # 总能量恰好 100 千卡时，「部分千卡」与答案数值相同，不能再当作提示报出。
        hint3 = "先算第一步：把蛋白质克数乘 4 得到它提供的能量；再除以整份总能量，最后乘 100 写成百分数。"
    else:
        hint3 = f"先算第一步：蛋白质提供 {fmt_num(part)} 千卡，整份总能量是 {int(energy)} 千卡。"

    return {
        "role": ctx["role"],
        "story": f"{ctx['role']}{_place(ctx)}翻看营养标签：{food['name']}含蛋白质 "
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
    # 样数按难度 2→10 递增（每档唯一；原 (d-1)//2 让 8-1/8-2 这类题目完全重复）。
    n = min(len(ctx["meals"]), 2 + (ctx["difficulty"] - 1))
    picks = _pick_many(ctx["rng"], ctx["meals"], n)
    total = sum((item["price"] for item in picks), Decimal("0"))
    average = (total / Decimal(n)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    names = "、".join(item["name"] for item in picks)

    rows = [[item["name"], fmt_money(item["price"])] for item in picks]

    return {
        "role": ctx["role"],
        "story": f"{ctx['role']}{_place(ctx)}核对账目：{names}的价格各不相同，"
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
    """题型九：折扣与方案比较 —— 用最少的实付金额。

    券的数据只有「满减券」一种（没有折扣券、也没有叠加规则），所以不做券种轮换，
    改为**按难度轮换购物篮**：样数在 2～5 之间循环、起点逐级错开，
    使 9 难的篮子金额互不相同 —— 由此 9 道题面互不相同，且能自然出现
    「一张券都没达标、只能按原价付」的情形（最容易被忽略的一种判断）。
    """
    coupons = ctx["coupons"]
    difficulty = ctx["difficulty"]
    pool = sorted(ctx["meals"], key=lambda item: item["price"], reverse=True)
    if difficulty >= 8:
        # 8、9 难要教「相当于便宜了百分之几」（六上百分数的应用），
        # 篮子必须够到券的门槛才有的可算——固定取价格最高的几样，
        # 免得高档位反而落在「没有券达标、按原价付款」的分支上。
        # 8 难取三样、9 难取四样：两难篮子不同，题面才不会撞车。
        count = min(3 if difficulty == 8 else 4, len(pool))
        start = 0
    else:
        count = min(2 + (difficulty - 1) % 4, len(pool))
        start = (difficulty - 1) % max(1, len(pool) - count + 1)
    picks: List[Dict[str, Any]] = list(pool[start:start + count])
    while len(picks) < 2:  # 菜单条数不足时的兜底
        picks.append(pool[len(picks) % len(pool)])
    basket = sum((item["price"] for item in picks), Decimal("0"))

    applicable = [coupon for coupon in coupons if basket >= coupon["threshold"]]
    no_coupon = not applicable
    if no_coupon:
        best = {"name": "无可用券", "threshold": Decimal("0"), "discount": Decimal("0")}
    else:
        best = max(applicable, key=lambda coupon: coupon["discount"])
    pay = basket - best["discount"]
    names = "、".join(item["name"] for item in picks)

    def _coupon_label(coupon: Dict[str, Any]) -> str:
        """券名里若已经写明门槛与面额，就不要再重复括号说明。"""
        name = str(coupon["name"])
        threshold_text = fmt_num(coupon["threshold"])
        discount_text = fmt_num(coupon["discount"])
        if threshold_text in name and discount_text in name:
            return name
        return f"{name}（满 {threshold_text} 元减 {discount_text} 元）"

    coupon_text = "；".join(_coupon_label(coupon) for coupon in coupons)

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
        rows.append(["适用券", _coupon_label(best)])
        question = f"这几样餐品原价合计 {fmt_money(basket)} 元，用其中实付金额最少的一张券，需要付多少元？"
        hint2 = "实付 = 原价合计 − 券的面额；把每张能用的券都试一遍，取实付金额最少的那一个。"
        hint3 = (
            f"原价合计 {fmt_money(basket)} 元，达到门槛且面额最大的是"
            f"「{best['name']}」，可减 {fmt_num(best['discount'])} 元。"
        )

    # 课标对齐（2026-10-25 考据整改 P2-1）：8、9 难改问「相当于便宜了百分之几」，
    # 直接对标新人教六上「百分数的应用（折扣）」——数据是真实菜单价，算式现成。
    answer_type = "number"
    answer = fmt_money(pay)
    answer_unit = "元"
    hints = [
        "先看每张券的门槛有没有达到，只有达到门槛的券才能用。",
        hint2,
        hint3,
    ]
    steps = [
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
    ]
    diagnosis = {
        "type": "discount",
        "total": str(basket),
        "discount": str(best["discount"]),
        "expected": str(pay),
    }
    if difficulty >= 8 and not no_coupon and basket > 0:
        saved_pct = (best["discount"] / basket * Decimal("100")).quantize(
            Decimal("0.1"), rounding=ROUND_HALF_UP
        )
        # 注意：数据表里不能出现「便宜了百分之几」这一行——那等于把答案写在题面上。
        answer_type = "percent"
        answer = fmt_num(saved_pct, "0.1")
        answer_unit = "%"
        question = (
            f"这几样餐品原价合计 {fmt_money(basket)} 元，用「{best['name']}」省下 "
            f"{fmt_num(best['discount'])} 元。这样买相当于便宜了百分之几？（百分号前保留一位小数）"
        )
        hints = [
            "求「便宜了百分之几」，就是用省下的钱数除以原价合计，再乘 100。",
            f"省下 {fmt_num(best['discount'])} 元，原价合计 {fmt_money(basket)} 元，"
            f"列式：{fmt_num(best['discount'])} ÷ {fmt_money(basket)} × 100% = □%。",
            f"先估一估：{fmt_num(best['discount'])} ÷ {fmt_money(basket)} 不到十分之一，"
            f"所以答案比 10% 小。",
        ]
        steps = [
            f"第一步求原价合计：{' + '.join(fmt_money(item['price']) for item in picks)} = {fmt_money(basket)}（元）。",
            f"第二步求省下的钱：{fmt_num(best['discount'])} 元（用「{best['name']}」）。",
            f"第三步求便宜了百分之几：{fmt_num(best['discount'])} ÷ {fmt_money(basket)} × 100% ≈ "
            f"{fmt_num(saved_pct, '0.1')}%。",
            CARE_NOTE,
        ]
        diagnosis = {
            "type": "percent",
            "part": str(best["discount"]),
            "whole": str(basket),
            "expected": str(saved_pct),
        }

    return {
        "role": ctx["role"],
        "story": f"{ctx['role']}{_place(ctx)}结算，篮子里是{names}，按需购买不多拿。"
                 f"手上有这些券：{coupon_text}。",
        "question": question,
        "data_table": _table("菜单与优惠券（真实数据）", ["项目", "金额（元）"], rows),
        "answer_type": answer_type,
        "answer": answer,
        "unit": answer_unit,
        "hints": hints,
        "steps": steps,
        "verify": {"formula": f"{fmt_money(basket)}-{fmt_num(best['discount'])}", "tool": "calculate-price"},
        "diagnosis": diagnosis,
        "primary_source": "coupons",
    }


_TEMPLATES = {
    1: _stage_1_add,
    2: _stage_2_sub,
    3: _stage_3_mul,
    4: _stage_4_div,
    5: _stage_5_unit,
    6: _stage_6_time,
    7: _stage_7_fraction_percent,
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
    """把题目答案转成可比较的 :class:`Decimal`（兼容百分数写法与分数写法）。"""
    text = str(answer or "").strip().replace("%", "")
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        # 分数答案（课标整改新增的 fraction 题型，如 "6/19"）：按分数除法解析。
        match = re.match(r"^\s*(-?\d+(?:\.\d+)?)\s*/\s*(-?\d+(?:\.\d+)?)\s*$", text)
        if match:
            try:
                denominator = Decimal(match.group(2))
                if denominator != 0:
                    return Decimal(match.group(1)) / denominator
            except InvalidOperation:
                return None
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
    story: Optional[str] = None,
) -> Dict[str, Any]:
    """生成一道题。

    Args:
        level_id: 指定关卡（如 ``"3-2"``，或跨线写法 ``"sanguo-3-2"``）；不指定则在该线内随机。
        kp_id: 按知识点筛选。
        grade: 按年级筛选（3-6）；口径是**知识点所属年级**，与返回值的 ``grade`` 字段同源。
        salt: 随机种子；给定相同 ``salt`` 与相同参数可复现同一道题。
        story: 故事线 id，默认 ``xiyou``。只影响关名 / 主题 / 角色 / brief 文案，
            9 个数学模板完全复用，数字与算式与故事线无关。

    Returns:
        题目字典::

            {
              "story_id": "xiyou", "story_name": "西游记",
              "level_id": "3-2", "stage": 3, "stage_name": "八戒买饼",
              "kp_id": "g3_mul_basic", "kp_name": "两位数乘一位数",
              "grade": 3, "stage_grade_hint": 3, "role": "猪八戒",
              "story": "（情境叙述文本）", "question": "...",
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
        ValueError: 筛选条件下没有任何关卡，或指定的故事线尚未上线。
        KeyError: 指定的 ``level_id`` 不存在。
    """
    if story is not None and not is_available(story):
        raise ValueError(f"故事线「{storyline_name(story)}」（{story}）尚未上线，请换一条线。")

    candidates: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    if level_id is not None:
        sid, resolved = resolve_level(level_id, story)
        candidates = [resolved]
    else:
        sid = str(story or default_story())
        for stage in load_storyline(sid).get("stages", []):
            for level in stage.get("levels", []):
                candidates.append((stage, level))

    if kp_id is not None:
        candidates = [
            (stage, level)
            for stage, level in candidates
            if str(level.get("kp_id")) == str(kp_id)
        ]

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

    story_data = load_storyline(sid)
    merged = dict(level)
    merged.update(
        {
            "story": sid,
            "story_name": str(story_data.get("name") or sid),
            "stage": int(stage.get("stage", 0)),
            "stage_name": str(stage.get("name", "")),
            "scene": str(stage.get("scene", "")),
            "theme": str(stage.get("theme", "")),
            "role_focus": str(stage.get("role_focus", "")),
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
        # 题材元信息：``story_id`` 是故事线 id，``story_name`` 是中文名。
        # 注意 ``story`` 这个键在本项目里一直是「题目情境叙述文本」（见 SKILL.md 字段表），
        # 为保持向后兼容不改变其含义，故事线标识改用 story_id。
        "story_id": sid,
        "story_name": str(story_data.get("name") or sid),
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
    if problem.get("story_name"):
        lines.append(f"故事线：{problem['story_name']}")
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
