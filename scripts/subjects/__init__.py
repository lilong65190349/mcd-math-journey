"""麦麦取经录 —— 学科注册表的读取与题型模板的动态加载。

设计要点（**这是「加学科只加文件」的实现体**）
--------------------------------------------
1. 学科的**唯一真相源**是 ``data/subjects/index.json``：它说某学科的知识点图谱在哪、
   题型模板模块在哪、允许哪些答案类型、是否上线。
2. 本模块不硬编码任何学科名。``load_templates(subject)`` 按注册表里的模块路径
   ``importlib.import_module``，再取模块里的字典属性（数学是 ``_MATH_TEMPLATES``，
   新学科是 ``TEMPLATES``）。
3. **取不到模板就抛 :class:`KeyError`，绝不回退到数学模板。** 静默回退会让
   「新学科配错 skill」变成「悄悄出数学题」，比直接失败危险得多——失败会被人看到，
   错派不会。
4. 数学的 9 个模板留在 :mod:`problem_gen` 里（历史原因，见设计文档 §一 的说明），
   通过 ``templates: "problem_gen"`` 这个注册项指路；新学科走
   ``scripts/subjects/<sid>/templates.py``。两种位置对调用方是同构的。

依赖方向：本模块**不 import** ``problem_gen``（除函数内延迟 import），
``problem_gen`` 也只在函数内延迟 import 本模块，避免循环导入。
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

if __package__ in (None, ""):  # pragma: no cover - 脚本运行路径
    sys.path.insert(0, str(Path(__file__).resolve().parent))

PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]
"""项目根目录（``scripts/subjects/__init__.py`` 往上两级）。"""

REGISTRY_PATH: Path = PROJECT_ROOT / "data" / "subjects" / "index.json"
"""学科注册表路径。"""

FALLBACK_SUBJECT: str = "math"
"""注册表损坏时的兜底学科 id（退化为改造前的单一学科行为）。"""

_ANSWER_TYPE_FALLBACK: List[str] = ["number", "percent", "fraction", "text", "enum"]
"""注册表不可用时的答案类型兜底表（历史上的 3 种 + 学科扩展新增的 2 种）。"""

_REGISTRY: Optional[Dict[str, Any]] = None
_TEMPLATE_CACHE: Dict[str, Dict[str, Callable[..., Dict[str, Any]]]] = {}


# ---------------------------------------------------------------------------
# 注册表读取
# ---------------------------------------------------------------------------


def reset_cache() -> None:
    """清空进程内缓存（自检 / 测试切换注册表时使用）。"""
    global _REGISTRY
    _REGISTRY = None
    _TEMPLATE_CACHE.clear()


def registry(force: bool = False) -> Dict[str, Any]:
    """读取 ``data/subjects/index.json``（进程内缓存）。

    Args:
        force: ``True`` 时强制重新读盘。

    Returns:
        注册表字典，含 ``default`` / ``answerTypes`` / ``subjects``。

    Raises:
        ValueError: 注册表结构异常（``subjects`` 不是非空列表）。
    """
    global _REGISTRY
    if _REGISTRY is not None and not force:
        return _REGISTRY
    with REGISTRY_PATH.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload.get("subjects"), list) or not payload["subjects"]:
        raise ValueError(f"学科注册表格式异常：{REGISTRY_PATH}")
    _REGISTRY = payload
    return payload


def all_answer_types() -> List[str]:
    """全系统已知的答案类型全集（``selfcheck`` 用它断言「未知类型不得出现」）。"""
    try:
        declared = [str(item) for item in registry().get("answerTypes") or []]
    except (OSError, ValueError, json.JSONDecodeError):  # pragma: no cover - 注册表缺失
        return list(_ANSWER_TYPE_FALLBACK)
    return declared or list(_ANSWER_TYPE_FALLBACK)


def subject_ids(only_available: bool = True) -> List[str]:
    """返回学科 id 列表。

    Args:
        only_available: 只返回 ``available: true`` 的学科（CLI 与出题口径）。
    """
    ids: List[str] = []
    for entry in registry().get("subjects", []):
        if only_available and not bool(entry.get("available")):
            continue
        ids.append(str(entry.get("id")))
    return ids


def default_subject() -> str:
    """默认学科 id；注册表里的 ``default`` 未上线时退化为第一个可用学科。"""
    ids = subject_ids(only_available=True)
    wanted = str(registry().get("default") or FALLBACK_SUBJECT)
    if wanted in ids:
        return wanted
    return ids[0] if ids else FALLBACK_SUBJECT


def subject_meta(subject_id: str) -> Dict[str, Any]:
    """取某学科的注册表条目。

    Args:
        subject_id: 学科 id，如 ``"math"`` / ``"chinese"``。

    Returns:
        注册表条目（含 ``curriculum`` / ``templates`` / ``answerTypes`` 等）。

    Raises:
        KeyError: 未注册的学科。
    """
    wanted = str(subject_id)
    for entry in registry().get("subjects", []):
        if str(entry.get("id")) == wanted:
            return dict(entry)
    raise KeyError(
        f"未注册的学科 {wanted!r}，可用：{'、'.join(subject_ids(only_available=False))}。"
    )


def is_available(subject_id: str) -> bool:
    """该学科是否已上线（未注册时返回 ``False``，不抛异常）。"""
    try:
        return bool(subject_meta(subject_id).get("available"))
    except KeyError:
        return False


def curriculum_path(subject_id: str) -> Path:
    """某学科的知识点图谱文件绝对路径。

    Args:
        subject_id: 学科 id。

    Returns:
        ``Path`` 对象；注册表里的相对路径按项目根目录解析。

    Raises:
        KeyError: 未注册的学科，或该学科没有声明 ``curriculum``。
    """
    meta = subject_meta(subject_id)
    relative = str(meta.get("curriculum") or "").strip()
    if not relative:
        raise KeyError(f"学科 {subject_id!r} 没有在注册表里声明 curriculum 文件")
    path = Path(relative)
    return path if path.is_absolute() else PROJECT_ROOT / path


def subject_answer_types(subject_id: str) -> List[str]:
    """某学科允许出现的答案类型。

    Args:
        subject_id: 学科 id。

    Returns:
        类型名列表；未声明时退化为 ``["number"]``（与改造前一致）。
    """
    try:
        declared = [str(item) for item in subject_meta(subject_id).get("answerTypes") or []]
    except KeyError:
        return ["number"]
    return declared or ["number"]


def in_demo_bank(subject_id: str) -> bool:
    """该学科的题目是否进入演示页题库（``demoBank``，默认 ``False``）。

    演示页只做数值比对（``demo/index.html`` 的 ``answerType`` 分支没有文本比较），
    所以非数学学科一律不进题库，走 CLI 交付。
    """
    try:
        return bool(subject_meta(subject_id).get("demoBank"))
    except KeyError:
        return False


def skill_label(subject_id: str, skill: str) -> str:
    """取某题型 skill 的**中文显示名**（给用户看的）。

    ``skill`` 名（``add`` / ``poem_fill`` / ``spell_fill``）是内部标识，
    不该直接印到界面上——``语文 · poem_fill`` 对小学生没有意义。
    显示名放在学科注册表的 ``skillNames`` 里，而不是硬编码在 CLI，
    这样加学科、加题型时显示名会跟着长。

    Args:
        subject_id: 学科 id。
        skill: 技能名。

    Returns:
        中文显示名；未登记时退回 skill 原名（保证不会崩，也不会印出空白）。
    """
    key = str(skill or "")
    if not key:
        return ""
    try:
        names = subject_meta(subject_id).get("skillNames") or {}
    except KeyError:
        return key
    label = str(names.get(key) or "").strip()
    return label or key


def skill_names(subject_id: str) -> Dict[str, str]:
    """某学科的 ``{skill: 中文显示名}`` 映射（自检用）。"""
    try:
        return {str(k): str(v) for k, v in (subject_meta(subject_id).get("skillNames") or {}).items()}
    except KeyError:
        return {}


# ---------------------------------------------------------------------------
# 模板动态加载
# ---------------------------------------------------------------------------


def load_templates(subject_id: str) -> Dict[str, Callable[..., Dict[str, Any]]]:
    """按注册表动态导入某学科的题型模板字典。

    注册表里的 ``templates`` 是**模块导入路径**（相对 ``scripts/`` 的顶层导入，
    例如 ``"subjects.chinese.templates"``），``templatesAttr`` 是该模块里
    承载 ``{skill: callable}`` 的属性名。

    Args:
        subject_id: 学科 id。

    Returns:
        ``{skill 名: 模板函数}``。

    Raises:
        KeyError: 未注册的学科，或模块里没有 ``templatesAttr`` 这个属性。
        ImportError: 模块路径无效（新学科忘了建 ``templates.py``）。
    """
    sid = str(subject_id)
    if sid in _TEMPLATE_CACHE:
        return _TEMPLATE_CACHE[sid]

    meta = subject_meta(sid)
    module_path = str(meta.get("templates") or "").strip()
    if not module_path:
        raise KeyError(f"学科 {sid!r} 没有在注册表里声明 templates 模块")
    attr = str(meta.get("templatesAttr") or "TEMPLATES")

    # ``scripts/`` 是模块根目录（既有惯例），所以这里按顶层模块名导入，
    # 而不是 ``scripts.subjects.xxx``。
    scripts_dir = str(PROJECT_ROOT / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    module = importlib.import_module(module_path)
    table = getattr(module, attr, None)
    if not isinstance(table, dict) or not table:
        raise KeyError(f"学科 {sid!r} 的模板模块 {module_path!r} 里没有非空的 {attr}")
    payload = {str(key): value for key, value in table.items()}
    _TEMPLATE_CACHE[sid] = payload
    return payload


def get_template(subject_id: str, skill: str) -> Callable[..., Dict[str, Any]]:
    """按 ``(学科, skill)`` 取模板函数。

    **取不到就抛 :class:`KeyError`，不做任何兜底。** 这是防止「新学科 / 新故事线
    配错 skill 却静默跑出数学题」的核心护栏。

    Args:
        subject_id: 学科 id。
        skill: 技能名（如 ``"add"`` / ``"poem_fill"``）。

    Returns:
        模板函数。

    Raises:
        KeyError: 该学科没有这个 skill 的模板。
    """
    table = load_templates(subject_id)
    key = str(skill)
    if key not in table:
        raise KeyError(
            f"学科「{subject_id}」没有 skill {key!r} 的模板，"
            f"已注册的 skill：{'、'.join(sorted(table))}。"
        )
    return table[key]


def known_skills(subject_id: str) -> List[str]:
    """某学科已注册的 skill 名列表（自检与 CLI 的 choices 用）。"""
    try:
        return sorted(load_templates(subject_id))
    except (KeyError, ImportError):  # pragma: no cover - 学科文件缺失
        return []


def template_module_path(subject_id: str) -> str:
    """某学科模板模块的导入路径（自检报错信息里用，便于定位缺失文件）。"""
    try:
        return str(subject_meta(subject_id).get("templates") or "")
    except KeyError:
        return ""


if __name__ == "__main__":  # pragma: no cover - 手工调试入口
    print("默认学科：", default_subject())
    for sid in subject_ids(only_available=False):
        meta = subject_meta(sid)
        print(
            f"  {sid:<9}{meta.get('name'):<5}available={meta.get('available')} "
            f"curriculum={meta.get('curriculum')} skills={known_skills(sid)}"
        )
