"""麦麦取经录 —— 生成离线演示页题库（demo/bank.json + 注入 index.html）。

为什么要有这个脚本
------------------
演示页是**单文件、零外部依赖、双击可开**的：它不能 ``fetch('bank.json')``，
所以题库必须内联在 HTML 里。手抄题目既容易抄错，也没法和 Python 端出题逻辑保持一致。

本脚本的做法：

1. 用 ``problem_gen.generate()`` 为**每条 available 的故事线**生成 81 道题
   （9 关 × 9 个难度档，固定 seed，可复现）——闯关系统一次要抽 1~3 题、且要求难度档
   互不重复，所以演示页必须备齐每个难度档的题，不能只留第 1 难；
2. 导出规则参数（``data/run_rules.json``）与角色库（``data/characters/*.json``），
   让演示页与 CLI 用**同一份规则**判定品阶、同一份数据揭名；
3. 导出每关的 10 条场景文本与**固定 seed 用例**（``vectors``）：演示页的抽样由 JS 独立
   实现，自检用这批用例逐条比对「JS 抽到的」与「Python 抽到的」是否一致；
4. 写 ``demo/bank.json``（给自检用，人可读）；
5. 把同一份数据压缩成一行注入 ``demo/index.html`` 的
   ``/* @@BANK_START@@ */`` 与 ``/* @@BANK_END@@ */`` 之间；
6. 把 ``demo/egg-engine.js``（随机彩蛋引擎，UMD 单文件、零外部依赖）原样注入
   ``demo/index.html`` 的 ``<!-- @@EGG_START@@ -->`` 与 ``<!-- @@EGG_END@@ -->``
   之间（外包一层 ``<script>``，与题库注入各占一段标记、互不干扰）。

自检脚本会比对「注入的内容」与 ``bank.json`` / ``egg-engine.js`` 是否一致，防止有人只改了其中一边。

用法::

    python scripts/export_demo_bank.py
"""

from __future__ import annotations

import json
import re
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List

if __package__ in (None, ""):  # pragma: no cover - 脚本运行路径
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import characters  # noqa: E402
import journey  # noqa: E402
import problem_gen  # noqa: E402

PROJECT_ROOT: Path = Path(__file__).resolve().parents[1]
DEMO_DIR: Path = PROJECT_ROOT / "demo"
BANK_PATH: Path = DEMO_DIR / "bank.json"
HTML_PATH: Path = DEMO_DIR / "index.html"

BANK_START = "/* @@BANK_START@@ */"
BANK_END = "/* @@BANK_END@@ */"
EGG_START = "<!-- @@EGG_START@@ -->"
EGG_END = "<!-- @@EGG_END@@ -->"
EGG_ENGINE_PATH: Path = DEMO_DIR / "egg-engine.js"

CARE_NOTE: str = problem_gen.CARE_NOTE

_DIAGNOSIS_ALIAS = {"average": "avg"}

VECTOR_SEEDS: List[str] = ["vector-a", "vector-b", "vector-c"]
"""固定用例的种子。两端一致性就靠这三条 × 5 线 × 9 关 = 135 条用例守住。"""


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
    """答案转成演示页比较用的数值（百分数只写数值，单位单独放 ``unit``）。

    分数答案（课标整改新增的 fraction 题型，如 ``6/19``）转成数值比较用；
    原始文本另存在 ``answerText``，演示页按 ``answerType`` 走精确比较。
    """
    text = str(problem.get("answer", "")).replace("%", "").strip()
    try:
        return float(Decimal(text))
    except InvalidOperation:
        match = re.match(r"^\s*(-?\d+(?:\.\d+)?)\s*/\s*(-?\d+(?:\.\d+)?)\s*$", text)
        if not match:
            raise
        denominator = Decimal(match.group(2))
        if denominator == 0:
            raise ValueError("分数答案的分母不能为 0")
        return float(Decimal(match.group(1)) / denominator)


def project(problem: Dict[str, Any]) -> Dict[str, Any]:
    """把 ``generate()`` 的题目投影成演示页字段。"""
    table = problem.get("data_table") or {}
    steps = [str(step) for step in problem.get("steps") or [] if str(step).strip() != CARE_NOTE]
    return {
        "level_id": str(problem["level_id"]),
        "stage": int(problem["stage"]),
        "difficulty": int(str(problem["level_id"]).split("-")[-1]),
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
        "answerText": str(problem.get("answer", "")),
        "answerType": str(problem.get("answer_type") or "number"),
        "unit": str(problem["unit"]),
        "hints": [str(hint) for hint in problem["hints"]],
        "steps": steps,
        "meta": _meta(problem.get("diagnosis") or {}),
    }


def _character_bank(sid: str) -> Dict[str, Dict[str, Any]]:
    """导出某条线的角色表（只用 id 索引，方便演示页按 id 取）。"""
    payload: Dict[str, Dict[str, Any]] = {}
    for char in characters.all_characters(sid):
        payload[str(char["id"])] = {
            "id": str(char["id"]),
            "name": str(char["name"]),
            "kind": str(char["kind"]),
            "stage": char.get("stage"),
            "title": str(char.get("title")),
            "intro": str(char.get("intro")),
            "introHint": str(char.get("intro_locked_hint")),
        }
    return payload


def _stage_bank(sid: str) -> List[Dict[str, Any]]:
    """导出某条线的 9 关：关名、地点、10 条场景文本、闯关大将、守关池。"""
    stages: List[Dict[str, Any]] = []
    for stage in problem_gen.list_stages(sid):
        number = int(stage["stage"])
        stages.append(
            {
                "stage": number,
                "name": str(stage["name"]),
                "theme": str(stage["theme"]),
                "scene": str(stage["scene"]),
                "scenes": journey.stage_scenes(sid, number),
                "gradeHint": int(stage["grade_hint"]),
                "bossId": characters.stage_boss_id(sid, number),
            }
        )
    return stages


def build_bank() -> Dict[str, Any]:
    """为每条 available 的故事线生成 9 关 × 9 难 = 81 道题，并附带规则、角色与固定用例。"""
    stories: List[Dict[str, Any]] = []
    bank: Dict[str, List[Dict[str, Any]]] = {}
    stage_bank: Dict[str, List[Dict[str, Any]]] = {}
    char_bank: Dict[str, Dict[str, Dict[str, Any]]] = {}
    guard_bank: Dict[str, List[str]] = {}
    default_id = problem_gen.default_story()
    order = journey.storyline_order()

    for item in problem_gen.list_storylines(only_available=True):
        sid = item["id"]
        problems: List[Dict[str, Any]] = []
        for stage in problem_gen.list_stages(sid):
            for level in stage["levels"]:
                level_id = str(level["level_id"])
                problem = problem_gen.generate(
                    level_id=level_id, salt=f"demo-{sid}-{level_id}"
                )
                problems.append(project(problem))
        bank[sid] = problems
        stage_bank[sid] = _stage_bank(sid)
        char_bank[sid] = _character_bank(sid)
        guard_bank[sid] = characters.guard_pool_ids(sid)
        stories.append(
            {
                "id": sid,
                "name": item["name"],
                "subtitle": item["subtitle"],
                "order": order.index(sid) + 1 if sid in order else 0,
                "default": sid == default_id,
            }
        )

    vectors: List[Dict[str, Any]] = []
    for sid in [item["id"] for item in stories]:
        for number in range(1, journey.STAGE_COUNT + 1):
            for seed in VECTOR_SEEDS:
                plan = journey.draw_run(sid, number, seed)
                vectors.append(
                    {
                        "story": sid,
                        "stage": number,
                        "seed": seed,
                        "sceneIndex": plan.scene_index,
                        "sceneText": plan.scene_text,
                        "guardId": plan.guard_id,
                        "questionCount": plan.question_count,
                        "difficulties": list(plan.difficulties),
                    }
                )

    return {
        "version": "2.0.0",
        "note": "由 scripts/export_demo_bank.py 生成，请勿手工编辑；改出题逻辑后重跑脚本即可。",
        "rules": journey.load_rules(),
        "stories": stories,
        "stages": stage_bank,
        "characters": char_bank,
        "guards": guard_bank,
        "vectors": vectors,
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


def inject_egg(html: str) -> str:
    """把彩蛋引擎（``demo/egg-engine.js``）注入 index.html 的 EGG 标记之间。

    引擎是 UMD 单文件（Node 下 ``module.exports``，浏览器下挂 ``window.MMQJ_EGG``），
    零外部依赖；注入时外包一层 ``<script>``，与题库注入各占一段标记，互不干扰。
    引擎文件里不能出现 ``</script>`` 字样——否则会提前闭合标签、把页面搅碎。
    """
    if EGG_START not in html or EGG_END not in html:
        raise SystemExit(f"index.html 缺少 {EGG_START} / {EGG_END} 标记，无法注入彩蛋引擎。")
    if not EGG_ENGINE_PATH.exists():
        raise SystemExit(f"缺少 {EGG_ENGINE_PATH.name}，无法注入彩蛋引擎。")
    engine = EGG_ENGINE_PATH.read_text(encoding="utf-8").strip()
    if "</script" in engine.lower():
        raise SystemExit("demo/egg-engine.js 内含有 </script> 字样，不能直接内联进 HTML。")
    head, rest = html.split(EGG_START, 1)
    _, tail = rest.split(EGG_END, 1)
    middle = f"{EGG_START}\n<script>\n{engine}\n</script>\n{EGG_END}"
    return head + middle + tail


def main() -> int:
    """入口：写 bank.json 并刷新 index.html 的内联题库与彩蛋引擎。"""
    payload = build_bank()
    DEMO_DIR.mkdir(parents=True, exist_ok=True)
    BANK_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    html = HTML_PATH.read_text(encoding="utf-8")
    html = inject(html, payload)
    html = inject_egg(html)
    HTML_PATH.write_text(html, encoding="utf-8")
    total = sum(len(items) for items in payload["bank"].values())
    chars = sum(len(items) for items in payload["characters"].values())
    print(
        f"题库已生成：{len(payload['bank'])} 条故事线 / {total} 道题 / "
        f"{chars} 名角色 / {len(payload['vectors'])} 条固定用例 → "
        f"{BANK_PATH.relative_to(PROJECT_ROOT)}，并注入 demo/index.html（题库 + 彩蛋引擎）"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - 脚本入口
    raise SystemExit(main())
