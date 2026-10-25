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
   之间（外包一层 ``<script>``，与题库注入各占一段标记、互不干扰）；
7. 把 ``demo/assets/bg-*.jpg``（背景氛围位图）转成 base64 注入
   ``demo/index.html`` 的 ``/* @@BG_START@@ */`` 与 ``/* @@BG_END@@ */`` 之间——
   **内联而不是相对路径引用**，这样演示页仍然「单文件、零外部请求、双击可开」。

自检脚本会比对「注入的内容」与 ``bank.json`` / ``egg-engine.js`` 是否一致，防止有人只改了其中一边。

用法::

    python scripts/export_demo_bank.py
"""

from __future__ import annotations

import base64
import json
import re
import sys
import tempfile
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

BG_START = "/* @@BG_START@@ */"
BG_END = "/* @@BG_END@@ */"
BG_ASSETS_DIR: Path = DEMO_DIR / "assets"

BG_ASSETS: List[str] = ["bg-hero", "bg-pattern", "bg-map", "bg-codex", "bg-result"]
"""需要内联进演示页的背景位图（文件名不含扩展名；只取 .jpg，png 太大不划算）。

顺序即 CSS 自定义属性的书写顺序：横幅 / 全站底纹 / 路线图 / 角色图鉴 / 结算页。
素材全部是 AI 生成的风格化示意图形，不是官方品牌资产。
"""

BG_TOKEN_PREFIX: Dict[str, str] = {name: name.removeprefix("bg-") for name in BG_ASSETS}
"""每张位图的令牌命名空间前缀（**由素材名推导**，不会与素材列表脱节）。

映射：``bg-hero`` → ``hero``、``bg-result`` → ``result`` ……
于是 ``("bg-hero", "ink")`` → ``--hero-ink``、``("bg-result", "card")`` → ``--result-card``。

**为什么必须命名空间化**：这些令牌最终写进 ``:root``，而 ``:root`` 里已经有全局主题变量
``--ink``（#3B2A20）、``--card``（#FFFFFF）、``--paper``…如果背景令牌用 ``ink`` / ``card``
这种**裸键**写进去，就会直接覆盖全局值——``--ink`` 被刷成 ``#ffffff`` 后，
**全站正文变白字**，落在浅色面板上完全看不见，整个页面就废了。

所以统一写成 ``--<命名空间>-<键>``，消费处写 ``var(--hero-ink, var(--ink))``：
既不会撞全局变量，素材缺失时又能优雅回落到原来的深色文字。
"""

GLOBAL_CSS_VARS: frozenset = frozenset(
    {"--paper", "--ink", "--seal", "--muted", "--card", "--mcd-yellow", "--line"}
)
"""全局主题变量名。背景令牌若与它们同名 → 直接把全站配色打穿，必须在写入前拦下。"""

BG_TOKENS: Dict[str, Dict[str, str]] = {
    "bg-hero": {
        "ink": "#ffffff",
        "soft": "rgba(255, 255, 255, 0.86)",
        "accent": "#FFC72C",
        "chip": "rgba(255, 255, 255, 0.92)",
        "chip-ink": "#A81E14",
        "scrim": "rgba(122, 20, 12, 0.82)",
        "scrim-soft": "rgba(122, 20, 12, 0.42)",
    },
    "bg-result": {
        "ink": "#ffffff",
        "soft": "rgba(255, 255, 255, 0.86)",
        "accent": "#FFC72C",
        "card": "rgba(255, 255, 255, 0.92)",
        "scrim": "rgba(122, 20, 12, 0.56)",
        "scrim-deep": "rgba(122, 20, 12, 0.68)",
    },
    "bg-map": {
        "scrim": "rgba(255, 255, 255, 0.62)",
        "scrim-soft": "rgba(255, 255, 255, 0.12)",
    },
    "bg-codex": {},
    "bg-pattern": {},
}
"""每张位图配套的「压底 / 文字色」令牌（**键是语义名，注入时加命名空间前缀**）。

红色底（横幅、结算页）必须配白字与黄字；浅色底（地图、图鉴）沿用暖深棕字。
这些令牌与图片**同生共死**：素材缺失 → 令牌不写入 → 消费处回落到原来的 CSS 渐变与深色文字。

键名通过 :func:`bg_token_name` 加上命名空间前缀后写入，例如 ``("bg-hero", "ink")``
→ ``--hero-ink``；``("bg-result", "card")`` → ``--result-card``。
"""


def bg_token_name(asset: str, key: str) -> str:
    """把 ``(素材名, 令牌键)`` 映射成带命名空间的 CSS 自定义属性名。

    Args:
        asset: 素材名，例如 ``"bg-hero"``。
        key: 语义令牌键，例如 ``"ink"``、``"chip-ink"``。

    Returns:
        形如 ``"--hero-ink"`` 的属性名。

    Raises:
        SystemExit: 素材没有登记命名空间，或算出来的名字撞上全局主题变量。
    """
    namespace = BG_TOKEN_PREFIX.get(asset)
    if not namespace:
        raise SystemExit(f"背景素材 {asset} 没有登记令牌命名空间，无法生成令牌名。")
    if namespace != asset.removeprefix("bg-"):
        raise SystemExit(f"背景素材 {asset} 的命名空间 {namespace} 与素材名对不上。")
    name = f"--{namespace}-{key}"
    if name in GLOBAL_CSS_VARS:
        raise SystemExit(f"背景令牌 {name} 与全局主题变量同名，会覆盖全站配色，已中止。")
    return name

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


def _bg_data_uri(path: Path) -> str:
    """把一张位图读成 base64 data URI（保持「单文件、零外部请求、双击可开」）。

    base64 字符集（``A-Z a-z 0-9 + / =``）本身不含 ``<``，但仍然断言一次
    ``</script``——与 ``inject_egg()`` 同一风格：万一将来换了编码方式，
    不能把 ``<script>`` 标签提前闭合、把页面搅碎。
    """
    payload = base64.b64encode(path.read_bytes()).decode("ascii")
    if "</script" in payload.lower():
        raise SystemExit(f"{path.name} 的 base64 负载里出现了 </script> 字样，不能内联进 HTML。")
    return f"data:image/jpeg;base64,{payload}"


def inject_backgrounds(html: str) -> str:
    """把 ``demo/assets/`` 下的背景位图内联进 index.html 的 BG 标记之间。

    做法：每张位图转 base64 data URI，写成一条 ``--bg-<名字>`` 自定义属性；
    同时写入与之配套的「压底 / 文字色」令牌（见 ``BG_TOKENS``）——
    令牌名一律**加命名空间前缀**（``--hero-ink`` / ``--result-ink``），
    绝不能写裸键 ``ink`` / ``card``：那样会覆盖全局 ``--ink`` / ``--card``，
    全站正文变白字、页面直接作废。

    页面里统一用 ``var(--bg-hero, none)`` / ``var(--hero-ink, var(--ink))`` 这种带兜底值的
    写法消费，所以**素材缺失时优雅降级**：变量不存在 → 兜底值生效 → 页面回落到纯 CSS 渐变，
    不报错、不崩。
    """
    if BG_START not in html or BG_END not in html:
        raise SystemExit(f"index.html 缺少 {BG_START} / {BG_END} 标记，无法注入背景位图。")
    head, rest = html.split(BG_START, 1)
    _, tail = rest.split(BG_END, 1)

    lines: List[str] = []
    for name in BG_ASSETS:
        path = BG_ASSETS_DIR / f"{name}.jpg"
        if not path.exists():
            continue  # 缺哪张就少写哪个变量 → 该处 var(--bg-*, none) 兜底
        # 变量名直接用素材名：bg-hero → --bg-hero（与页面里的 var(--bg-hero, none) 对齐）
        lines.append(f'    --{name}: url("{_bg_data_uri(path)}");')
        for key, value in BG_TOKENS.get(name, {}).items():
            lines.append(f"    {bg_token_name(name, key)}: {value};")

    if not lines:
        middle = (
            f"{BG_START}\n"
            "  /* 背景素材缺失：保持纯 CSS 渐变，页面照常可读。 */\n"
            f"{BG_END}"
        )
    else:
        middle = (
            f"{BG_START}\n"
            "  /* 背景氛围位图：base64 内联，保持单文件零外部依赖。\n"
            "     由 scripts/export_demo_bank.py 从 demo/assets/ 生成，请勿手工编辑。\n"
            "     配套文字色令牌一律带命名空间前缀（--hero-ink / --result-ink …），\n"
            "     避免覆盖全局 --ink / --card。 */\n"
            "  :root {\n"
            + "\n".join(lines)
            + "\n  }\n"
            f"{BG_END}"
        )
    return head + middle + tail


def main() -> int:
    """入口：写 bank.json 并刷新 index.html 的内联题库、彩蛋引擎与背景位图。

    支持 ``--offline``：强制走「示例数据快照」口径（把 ``data_source`` 的缓存目录
    指向一个空临时目录）。**默认行为不变**（有本机 MCP 缓存就用缓存），但会打印
    一行醒目提示——因为缓存口径的题库和仓库基线（sample 口径）**不是同一份数字**，
    混用会让 README 示例、自检断言和演示内容全部对不上。
    """
    offline = "--offline" in sys.argv[1:]
    cache_files = sorted((PROJECT_ROOT / "data" / "cache").glob("*.json"))
    if offline:
        import data_source  # noqa: E402

        data_source.CACHE_DIR = Path(tempfile.mkdtemp(prefix="mmqj-offline-"))
        data_source.reset_client()
        print("已按 --offline 强制使用示例数据快照（忽略本机 MCP 缓存）")
    elif cache_files:
        print(
            f"⚠ 检测到本机 MCP 缓存 {len(cache_files)} 个文件（data/cache/*.json）——"
            "本次导出走的是**缓存口径**，题库数字会与仓库基线（sample 口径）不同。"
            "若要保持仓库基线一致：先移走 data/cache/ 再导出，或加 --offline 参数。"
        )

    payload = build_bank()
    DEMO_DIR.mkdir(parents=True, exist_ok=True)
    BANK_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    html = HTML_PATH.read_text(encoding="utf-8")
    html = inject(html, payload)
    html = inject_egg(html)
    html = inject_backgrounds(html)
    HTML_PATH.write_text(html, encoding="utf-8")
    total = sum(len(items) for items in payload["bank"].values())
    chars = sum(len(items) for items in payload["characters"].values())
    inlined = sum(1 for name in BG_ASSETS if (BG_ASSETS_DIR / f"{name}.jpg").exists())
    print(
        f"题库已生成：{len(payload['bank'])} 条故事线 / {total} 道题 / "
        f"{chars} 名角色 / {len(payload['vectors'])} 条固定用例 → "
        f"{BANK_PATH.relative_to(PROJECT_ROOT)}，并注入 demo/index.html"
        f"（题库 + 彩蛋引擎 + {inlined}/{len(BG_ASSETS)} 张背景位图）"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - 脚本入口
    raise SystemExit(main())
