"""麦麦取经录 —— 统一数据获取层（MCP → 本地缓存 → 示例快照 三级降级）。

设计目标
--------
* **有令牌、有网络**：走真实 MCP 工具，返回 ``source="mcp"``。
* **没令牌 / 网络不通**：读 ``data/cache/<name>.json``（24 小时内有效），
  返回 ``source="cache"``。
* **缓存也没有**：读 ``demo/sample_data.json`` 示例快照，返回 ``source="sample"``，
  UI 上必须显式标注「示例数据」。

所有 getter 的返回值统一为::

    {"source": "mcp" | "cache" | "sample", "fetched_at": "<ISO 时间>", "data": [...]}

于是上层（出题引擎 / 校验器 / CLI）无需关心今天到底通不通网。

只使用标准库；令牌只从环境变量 ``MCD_MCP_TOKEN`` 读取。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

# 允许以 `python scripts/data_source.py` 直接运行时导入同目录模块
if __package__ in (None, ""):  # pragma: no cover - 脚本运行路径
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from mcp_client import (  # noqa: E402  (延后导入：需要上面的 sys.path 调整)
    DEFAULT_ENDPOINT,
    TOKEN_ENV,
    McpClient,
    McpConfigError,
    McpError,
    McpProtocolError,
    McpToolError,
)

PROJECT_ROOT: Path = Path(__file__).resolve().parents[1]
CACHE_DIR: Path = PROJECT_ROOT / "data" / "cache"
SAMPLE_PATH: Path = PROJECT_ROOT / "demo" / "sample_data.json"
CACHE_TTL_SECONDS: int = 24 * 3600

SOURCE_MCP: str = "mcp"
SOURCE_CACHE: str = "cache"
SOURCE_SAMPLE: str = "sample"
SOURCE_LABELS: Dict[str, str] = {
    SOURCE_MCP: "实时数据（麦当劳 MCP）",
    SOURCE_CACHE: "本地缓存（最近一次 MCP 结果）",
    SOURCE_SAMPLE: "示例数据（仅用于离线演示）",
}

_SAMPLE_CACHE: Optional[Dict[str, Any]] = None
_CLIENT: Optional[McpClient] = None


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def now_iso() -> str:
    """返回当前本地时间的 ISO 字符串（秒级）。"""
    return datetime.now().replace(microsecond=0).isoformat()


def _ensure_dir(path: Path) -> None:
    """确保目录存在。"""
    path.mkdir(parents=True, exist_ok=True)


def _cache_path(name: str) -> Path:
    """给定数据源名，返回缓存文件路径。"""
    return CACHE_DIR / f"{name}.json"


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    """安全读取 JSON 文件，失败返回 ``None``。"""
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    """原子写 JSON（先写临时文件再替换）。"""
    _ensure_dir(path.parent)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    tmp.replace(path)


def sample_payload() -> Dict[str, Any]:
    """读取示例快照（进程内只读一次）。"""
    global _SAMPLE_CACHE
    if _SAMPLE_CACHE is None:
        _SAMPLE_CACHE = _read_json(SAMPLE_PATH) or {}
    return _SAMPLE_CACHE


def get_client() -> McpClient:
    """返回进程内复用的 MCP 客户端。"""
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = McpClient(endpoint=DEFAULT_ENDPOINT, timeout=20.0)
    return _CLIENT


def reset_client() -> None:
    """丢弃当前客户端（自检 / 切换令牌时使用）。"""
    global _CLIENT
    if _CLIENT is not None:
        try:
            _CLIENT.close()
        except McpError:
            pass
    _CLIENT = None


def to_decimal(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    """把任意值稳妥地转成 :class:`Decimal`。"""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return default
    if isinstance(value, str):
        cleaned = value.strip().replace("￥", "").replace("¥", "").replace(",", "")
        cleaned = cleaned.replace("元", "").replace("%", "").replace("千卡", "")
        try:
            return Decimal(cleaned)
        except InvalidOperation:
            return default
    return default


def pick(record: Dict[str, Any], *names: str, default: Any = None) -> Any:
    """从记录里按候选字段名取第一个非空值（兼容真实接口字段命名差异）。"""
    for name in names:
        if name in record and record[name] not in (None, ""):
            return record[name]
    return default


def as_records(payload: Any) -> List[Dict[str, Any]]:
    """把工具返回的 payload 规整成记录列表。"""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("data", "list", "items", "results", "records", "foods", "meals", "coupons"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
            if isinstance(value, dict):
                nested = as_records(value)
                if nested:
                    return nested
        # 单个对象也当成一条记录
        if payload and not any(isinstance(v, str) for v in payload.values()):
            return [payload]
    return []


# ---------------------------------------------------------------------------
# 三级降级核心
# ---------------------------------------------------------------------------


def _mcp_records(tool_name: str, arguments: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """调用 MCP 工具并返回记录列表；任何异常都向上抛出。"""
    client = get_client()
    result = client.call_tool(tool_name, arguments or {})
    payload = McpClient.extract_payload(result)
    return as_records(payload)


def _from_cache(name: str, ttl: int = CACHE_TTL_SECONDS) -> Optional[Dict[str, Any]]:
    """读取缓存；超过 TTL 视为过期。"""
    cached = _read_json(_cache_path(name))
    if not cached:
        return None
    fetched_at = cached.get("fetched_at")
    if isinstance(fetched_at, str):
        try:
            stamp = datetime.fromisoformat(fetched_at).timestamp()
        except ValueError:
            stamp = None
        if stamp is not None and (time.time() - stamp) > ttl:
            return None
    data = cached.get("data")
    if not isinstance(data, list) or not data:
        return None
    return cached


def _from_sample(key: str) -> Dict[str, Any]:
    """从示例快照里取数据。"""
    payload = sample_payload()
    data = payload.get(key)
    if data is None:
        data = []
    return {
        "source": SOURCE_SAMPLE,
        "fetched_at": str(payload.get("fetched_at") or ""),
        "data": data if isinstance(data, (list, dict)) else [],
    }


def _resolve(
    name: str,
    tool_name: str,
    arguments: Optional[Dict[str, Any]] = None,
    sample_key: Optional[str] = None,
    allow_empty: bool = False,
) -> Dict[str, Any]:
    """通用三级降级：MCP → 缓存 → 示例快照。

    Args:
        name: 缓存文件名（不含扩展名）。
        tool_name: MCP 工具名。
        arguments: 工具入参。
        sample_key: 示例快照里的键名，默认与 ``name`` 相同。
        allow_empty: MCP 返回空列表时是否接受（默认不接受，继续降级）。

    Returns:
        统一结构 ``{"source", "fetched_at", "data"}``。
    """
    key = sample_key or name
    try:
        records = _mcp_records(tool_name, arguments)
        if records or allow_empty:
            entry = {"source": SOURCE_MCP, "fetched_at": now_iso(), "data": records}
            _write_json(_cache_path(name), entry)
            return entry
    except (McpConfigError, McpProtocolError, McpToolError, PermissionError, OSError):
        pass

    cached = _from_cache(name)
    if cached:
        return {
            "source": SOURCE_CACHE,
            "fetched_at": str(cached.get("fetched_at") or ""),
            "data": cached.get("data") or [],
        }

    return _from_sample(key)


# ---------------------------------------------------------------------------
# 对外 getter
# ---------------------------------------------------------------------------


def get_nutrition_foods() -> Dict[str, Any]:
    """获取餐品营养信息（热量、蛋白质、脂肪、碳水、钠、净含量）。"""
    return _resolve("nutrition", "list-nutrition-foods", {}, "nutrition")


def get_meals() -> Dict[str, Any]:
    """获取当前可售餐品列表（含价格）。"""
    return _resolve("meals", "query-meals", {}, "meals")


def get_meal_detail(meal_name: str) -> Dict[str, Any]:
    """获取单个餐品详情。"""
    entry = _resolve(
        "meal_detail",
        "query-meal-detail",
        {"mealName": meal_name},
        "meals",
    )
    data = entry.get("data")
    if isinstance(data, list):
        matched = [
            item
            for item in data
            if isinstance(item, dict)
            and meal_name in str(pick(item, "meal_name", "mealName", "name", default=""))
        ]
        entry = dict(entry)
        entry["data"] = matched
    return entry


def get_coupons() -> Dict[str, Any]:
    """获取麦麦省券列表（面额与使用门槛）。"""
    return _resolve("coupons", "available-coupons", {}, "coupons")


def get_campaign_calendar() -> Dict[str, Any]:
    """获取活动日历（起止时间），用于行程 / 时间类题型。"""
    return _resolve("calendar", "campaign-calendar", {}, "calendar")


def get_points_products() -> Dict[str, Any]:
    """获取商城积分商品（积分价），用于兑换类题型。"""
    return _resolve("points_products", "mall-points-products", {}, "points_products")


def get_now() -> Dict[str, Any]:
    """获取当前时间信息。"""
    entry = _resolve("now", "now-time-info", {}, "now")
    data = entry.get("data")
    if isinstance(data, list) and data:
        entry = dict(entry)
        entry["data"] = data[0]
    elif not isinstance(data, dict):
        entry = dict(entry)
        entry["data"] = {}
    return entry


def calc_price(items: List[Dict[str, Any]], store_code: Optional[str] = None) -> Dict[str, Any]:
    """调用 ``calculate-price`` 计算一篮子餐品的官方价格。

    Args:
        items: 形如 ``[{"name": "巨无霸", "quantity": 3, "price": 25.5}]`` 的明细。
        store_code: 门店编码，可选。

    Returns:
        ``{"source", "fetched_at", "total": Decimal | None, "currency", "raw"}``；
        降级时 ``total`` 为 ``None``，由本地算式接管。
    """
    arguments: Dict[str, Any] = {"items": items}
    if store_code:
        arguments["storeCode"] = store_code
    try:
        records = _mcp_records("calculate-price", arguments)
        total = _extract_total(records)
        if total is not None:
            return {
                "source": SOURCE_MCP,
                "fetched_at": now_iso(),
                "total": total,
                "currency": "CNY",
                "raw": records,
            }
    except (McpConfigError, McpProtocolError, McpToolError, PermissionError, OSError):
        pass

    cached = _from_cache("calculate_price", ttl=CACHE_TTL_SECONDS)
    if cached:
        total = _extract_total(cached.get("data") or [])
        if total is not None:
            return {
                "source": SOURCE_CACHE,
                "fetched_at": str(cached.get("fetched_at") or ""),
                "total": total,
                "currency": "CNY",
                "raw": cached.get("data"),
            }

    return {
        "source": SOURCE_SAMPLE,
        "fetched_at": str(sample_payload().get("fetched_at") or ""),
        "total": None,
        "currency": "CNY",
        "raw": [],
    }


_TOTAL_KEYS = ("total", "totalPrice", "totalAmount", "payPrice", "amount", "price", "sum")


def _extract_total(records: Any) -> Optional[Decimal]:
    """在任意嵌套结构里找出第一个可信的「总价」数值。"""
    queue: List[Any] = [records]
    while queue:
        node = queue.pop(0)
        if isinstance(node, list):
            queue.extend(node)
            continue
        if not isinstance(node, dict):
            continue
        for key in _TOTAL_KEYS:
            if key in node:
                value = to_decimal(node[key], Decimal("-1"))
                if value >= 0:
                    return value
        for value in node.values():
            if isinstance(value, (dict, list)):
                queue.append(value)
    return None


# ---------------------------------------------------------------------------
# 批量拉取与状态
# ---------------------------------------------------------------------------

_DATASETS: List[tuple] = [
    ("nutrition", get_nutrition_foods),
    ("meals", get_meals),
    ("coupons", get_coupons),
    ("calendar", get_campaign_calendar),
    ("points_products", get_points_products),
    ("now", get_now),
]


def fetch_all() -> Dict[str, Dict[str, Any]]:
    """一次性拉取全部数据并写入缓存，返回各数据源状态。"""
    summary: Dict[str, Dict[str, Any]] = {}
    for name, getter in _DATASETS:
        entry = getter()
        summary[name] = {
            "source": entry.get("source"),
            "fetched_at": entry.get("fetched_at"),
            "count": len(entry.get("data") or []) if isinstance(entry.get("data"), list) else 1,
        }
    return summary


def status() -> Dict[str, Dict[str, Any]]:
    """探测每个数据源当前可用的级别（mcp / cache / sample）。"""
    summary: Dict[str, Dict[str, Any]] = {}
    for name, getter in _DATASETS:
        entry = getter()
        summary[name] = {
            "source": entry.get("source"),
            "label": SOURCE_LABELS.get(str(entry.get("source")), "未知"),
            "fetched_at": entry.get("fetched_at"),
            "count": len(entry.get("data") or []) if isinstance(entry.get("data"), list) else 1,
        }
    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _format_status_table(summary: Dict[str, Dict[str, Any]]) -> str:
    """把状态字典渲染成等宽文本表格。"""
    lines = ["数据源            级别      条数  抓取时间", "-" * 56]
    for name, info in summary.items():
        lines.append(
            "{:<16}{:<10}{:<6}{}".format(
                name, str(info.get("source")), str(info.get("count")), str(info.get("fetched_at") or "-")
            )
        )
    return "\n".join(lines)


def main(argv: Optional[Iterable[str]] = None) -> int:
    """命令行入口：``--fetch`` 批量抓取，``--status`` 打印状态。"""
    parser = argparse.ArgumentParser(
        prog="data_source.py",
        description="麦麦取经录数据源工具：MCP → 缓存 → 示例快照 三级降级",
    )
    parser.add_argument("--fetch", action="store_true", help="拉取全部数据并写入 data/cache/")
    parser.add_argument("--status", action="store_true", help="打印每个数据源当前可用级别")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if not args.fetch and not args.status:
        parser.print_help()
        return 0

    if args.fetch:
        summary = fetch_all()
        print("已拉取数据：")
        print(_format_status_table(summary))
        degraded = [name for name, info in summary.items() if info.get("source") != SOURCE_MCP]
        if degraded:
            print()
            print(
                "注意：以下数据源未取到 MCP 实时结果，已按「缓存 → 示例快照」降级，本次未写入缓存："
                + "、".join(degraded)
            )
            print(f"      请先设置环境变量 {TOKEN_ENV} 后重试：export {TOKEN_ENV}=你的令牌")

    if args.status:
        summary = status()
        print(_format_status_table(summary))

    return 0


if __name__ == "__main__":  # pragma: no cover - 脚本入口
    raise SystemExit(main())
