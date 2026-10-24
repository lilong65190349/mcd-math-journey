"""麦麦取经录 —— 角色库的加载与状态判定。

角色分两类（见 ``docs/SYSTEM_RULES.md`` 第五节）：

* **闯关大将**（``kind="boss"``）：每条线 9 名，一关 1 名，固定出场，明示姓名；
* **守关大将**（``kind="guard"``）：每条线 5 名组成线性守关池，每次进入该关随机 1 名，
  进入时只显示「？？？·守关者」，通关后才揭名。

数据全部放在 ``data/characters/<story>.json``，代码里不硬编码任何人物名。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

if __package__ in (None, ""):  # pragma: no cover - 脚本运行路径
    sys.path.insert(0, str(Path(__file__).resolve().parent))

PROJECT_ROOT: Path = Path(__file__).resolve().parents[1]
CHARACTERS_DIR: Path = PROJECT_ROOT / "data" / "characters"

GUARD_PLACEHOLDER: str = "？？？·守关者"
"""守关大将未揭名时的占位文案（CLI 与演示页共用同一句）。"""

INTRO_LOCKED_TAIL: str = "干净通关这一关（不看提示、不答错），就能读到这段。"
"""介绍未解锁时给孩子的提示。"""

_CACHE: Dict[str, Dict[str, Any]] = {}

STATE_UNSEEN: str = "unseen"
STATE_COLLECTED: str = "collected"
STATE_UNLOCKED: str = "unlocked"


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------


def load(story: str) -> Dict[str, Any]:
    """读取某条线的角色库（进程内缓存）。"""
    sid = str(story)
    if sid not in _CACHE:
        path = CHARACTERS_DIR / f"{sid}.json"
        with path.open("r", encoding="utf-8") as handle:
            _CACHE[sid] = json.load(handle)
    return _CACHE[sid]


def all_characters(story: str) -> List[Dict[str, Any]]:
    """该线全部角色（闯关大将按关号在前，守关大将按守关池顺序在后）。"""
    data = load(story)
    index = {str(item["id"]): item for item in data.get("characters", [])}
    ordered: List[Dict[str, Any]] = []
    for stage in sorted(data.get("stage_bosses", {}), key=lambda key: int(key)):
        char_id = str(data["stage_bosses"][stage])
        if char_id in index:
            ordered.append(index[char_id])
    for char_id in data.get("guard_pool", []):
        if str(char_id) in index:
            ordered.append(index[str(char_id)])
    return ordered


def by_id(story: str, char_id: str) -> Optional[Dict[str, Any]]:
    """按 id 取角色（找不到返回 ``None``）。"""
    for item in load(story).get("characters", []):
        if str(item.get("id")) == str(char_id):
            return dict(item)
    return None


def stage_boss_id(story: str, stage: int) -> str:
    """某关的闯关大将 id。"""
    mapping = load(story).get("stage_bosses") or {}
    key = str(int(stage))
    if key not in mapping:
        raise KeyError(f"故事线「{story}」第 {stage} 关没有配置闯关大将。")
    return str(mapping[key])


def stage_boss(story: str, stage: int) -> Dict[str, Any]:
    """某关的闯关大将（必定存在，配置缺失时抛 KeyError）。"""
    char = by_id(story, stage_boss_id(story, stage))
    if char is None:
        raise KeyError(f"角色数据缺失：{story} 第 {stage} 关的闯关大将未定义。")
    return char


def guard_pool_ids(story: str) -> List[str]:
    """该线守关池的 5 个角色 id（顺序稳定，抽样结果因此可复现）。"""
    pool = [str(item) for item in load(story).get("guard_pool") or []]
    if not pool:
        raise ValueError(f"故事线「{story}」没有配置守关池。")
    return pool


def guard_pool(story: str) -> List[Dict[str, Any]]:
    """该线守关池的角色详情列表。"""
    items: List[Dict[str, Any]] = []
    for char_id in guard_pool_ids(story):
        char = by_id(story, char_id)
        if char is not None:
            items.append(char)
    return items


def guard_by_id(story: str, char_id: str) -> Dict[str, Any]:
    """按 id 取守关大将；不存在时返回一个「未命名」占位角色，避免界面炸掉。"""
    char = by_id(story, char_id)
    if char is None:
        return {
            "id": str(char_id),
            "name": GUARD_PLACEHOLDER,
            "title": "",
            "intro": "",
            "intro_locked_hint": INTRO_LOCKED_TAIL,
            "kind": "guard",
            "stage": None,
        }
    return char


def count(story: str) -> Dict[str, int]:
    """该线角色数量统计：``{"boss": 9, "guard": 5, "total": 14}``。"""
    items = all_characters(story)
    boss = sum(1 for item in items if str(item.get("kind")) == "boss")
    return {"boss": boss, "guard": len(items) - boss, "total": len(items)}


# ---------------------------------------------------------------------------
# 状态判定
# ---------------------------------------------------------------------------


def char_state(record: Optional[Dict[str, Any]]) -> str:
    """把收集记录映射成三种展示状态。

    * ``unseen``：还没遇上过；
    * ``collected``：已收卡、介绍还锁着；
    * ``unlocked``：介绍已解锁（永久有效，不会回锁）。
    """
    if not record:
        return STATE_UNSEEN
    return STATE_UNLOCKED if int(record.get("intro_unlocked") or 0) else STATE_COLLECTED


def state_label(state: str) -> str:
    """三种状态的中文标签。"""
    return {
        STATE_UNSEEN: "尚未遇见",
        STATE_COLLECTED: "已收集 · 待解锁",
        STATE_UNLOCKED: "已解锁",
    }.get(str(state), "尚未遇见")


def collection_view(
    story: str, collection: Optional[Dict[str, Dict[str, Any]]] = None
) -> List[Dict[str, Any]]:
    """角色图鉴视图：每位角色带上状态与品阶，未遇见的介绍不外传。

    Args:
        story: 故事线 id。
        collection: ``{char_id: {"best_tier", "intro_unlocked", ...}}``，
            由 ``progress.get_collection`` 提供。

    Returns:
        条目列表，字段：``id / name / kind / stage / title / state / state_label /
        best_tier / intro``。未收集时 ``intro`` 为空字符串（介绍不能提前泄露）。
    """
    records = collection or {}
    view: List[Dict[str, Any]] = []
    for char in all_characters(story):
        char_id = str(char.get("id"))
        state = char_state(records.get(char_id))
        unlocked = state == STATE_UNLOCKED
        view.append(
            {
                "id": char_id,
                "name": str(char.get("name")),
                "kind": str(char.get("kind")),
                "stage": char.get("stage"),
                "title": str(char.get("title")),
                "state": state,
                "state_label": state_label(state),
                "best_tier": (records.get(char_id) or {}).get("best_tier"),
                "intro": str(char.get("intro")) if unlocked else "",
                "intro_locked_hint": (
                    "" if unlocked else str(char.get("intro_locked_hint") or INTRO_LOCKED_TAIL)
                ),
            }
        )
    return view


def summary(story: str, collection: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, int]:
    """图鉴统计：``{"total", "collected", "unlocked"}``。"""
    view = collection_view(story, collection)
    collected = sum(1 for item in view if item["state"] != STATE_UNSEEN)
    unlocked = sum(1 for item in view if item["state"] == STATE_UNLOCKED)
    return {"total": len(view), "collected": collected, "unlocked": unlocked}


def hint_for(char: Dict[str, Any]) -> str:
    """介绍锁着时给孩子的解锁提示。"""
    return str(char.get("intro_locked_hint") or INTRO_LOCKED_TAIL)


def all_ids(story: str) -> List[str]:
    """该线全部角色 id（自检用）。"""
    return [str(item.get("id")) for item in all_characters(story)]


def story_ids() -> List[str]:
    """有角色数据的故事线 id（按文件名排序，自检用）。"""
    return sorted(path.stem for path in CHARACTERS_DIR.glob("*.json"))


if __name__ == "__main__":  # pragma: no cover - 手工调试入口
    for sid in story_ids():
        print(sid, count(sid), [item["name"] for item in guard_pool(sid)])
