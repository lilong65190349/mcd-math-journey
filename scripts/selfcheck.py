"""麦麦取经录 —— 文档防漂移自检（README 示例 vs 当前代码）。

为什么需要它
------------
README 第七节写的是「真实运行结果」，一旦出题模板、CLI 文案或数据快照变了，
文档就会悄悄失真——而且只有人肉复读才会发现。本脚本把这件事机器化：
用文档里记录的固定 seed 重新跑一遍，与 README 代码块逐行 diff。

除示例输出之外，还校验这些容易悄悄坏掉的事：

1. **故事线完整性**：每条 ``available`` 的线必须 9 关 × 9 难 = 81 个 level，
   ``brief`` 非空且线内不重复，``kp_id`` 必须命中 ``data/curriculum.json``；
2. **演示页题库一致性**：``demo/index.html`` 里内联的 BANK 必须与 ``demo/bank.json``
   一致，且与「当前代码重新导出的结果」一致（405 道题 / 70 名角色 / 规则参数 / 固定用例）；
3. **闯关规则**（``docs/SYSTEM_RULES.md`` 第十三节）：解锁链、故事线链、1000 个 seed 的
   随机覆盖、可复现、品阶边界、收集与介绍解锁；
4. **两端一致**：``bank.vectors`` 里 135 条固定用例，用 ``journey.draw_run`` 重算必须逐条相同；
5. **红线**：70 条角色介绍 + 50 条场景修饰语不得出现暴力 / 迷信 / 竞品 / 消费引导词；
6. **随机彩蛋**（``data/run_rules.json`` 的 ``easter_egg`` 段）：必备键齐全且类型正确、
   pool 条目合法（id 唯一 / type ∈ {A,C,D} / weight 正整数 / C 类带 mini）、文案不过红线词表，
   且配置必须经导出流入 ``demo/bank.json`` 与 ``demo/index.html``（三处一致，防止只改一边）。

比对口径（重要）
----------------
* **时间戳类字段归一化**：错题时间、周报统计区间、缓存抓取时间随机器与时刻变化，
  物理上不可能逐字稳定，比较前统一替换成 ``<时间>``。
* **终端输入回显**：README 里 ``你的答案 > 29`` 这类行是「人敲进去的字符」，
  真实管道运行时终端不会再把输入打印一遍，因此这类行整行跳过。
* **绝对路径**：示例 1 的 ``doctor`` 输出含本机路径与 Python 小版本号，
  用 ``...`` 省略，比较时按「分段顺序包含」判断，不做硬 diff。

用法::

    python scripts/selfcheck.py          # 通过时打印 DOC_SYNC: OK，退出码 0

只使用标准库；运行前后会备份 / 还原 ``runtime/progress.db``，不会破坏本机进度。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

PROJECT_ROOT: Path = Path(__file__).resolve().parents[1]
README_PATH: Path = PROJECT_ROOT / "README.md"
CLI: Path = PROJECT_ROOT / "scripts" / "cli.py"
DATA_CLI: Path = PROJECT_ROOT / "scripts" / "data_source.py"
DEMO_INDEX: Path = PROJECT_ROOT / "demo" / "index.html"
DEMO_BANK: Path = PROJECT_ROOT / "demo" / "bank.json"
EGG_ENGINE: Path = PROJECT_ROOT / "demo" / "egg-engine.js"
RUN_RULES: Path = PROJECT_ROOT / "data" / "run_rules.json"

BANK_START: str = "/* @@BANK_START@@ */"
BANK_END: str = "/* @@BANK_END@@ */"
EGG_START: str = "<!-- @@EGG_START@@ -->"
EGG_END: str = "<!-- @@EGG_END@@ -->"

EGG_MINI_KINDS = {"cloud_flip", "wukong_quiz", "peach_catch", "badge_match"}
"""C 类小游戏的四种合法取值（与 egg-engine.js 的 buildMini 一一对应）。"""

EGG_POOL_TYPES = {"A", "C", "D"}
"""彩蛋类型：A 角色探头 / C 迷你互动 / D 知识卡（B 为结算庆祝，不进 pool）。"""

EGG_TASK_KEYS = {
    "first_clear", "clean_run", "line_complete",
    "streak5", "gold_fast", "recover", "dash3",
}
"""T2 任务钩子的七个任务（与 egg-engine.js 的 onSettle/onAnswer 对应）。"""

DATETIME_RE = re.compile(r"\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}:\d{2}(?:\+08:00)?)?")
ECHO_RE = re.compile(r"^\s*你的答案 > \S{1,8}$")

# 示例 2（1 错 + 1 对）+ 示例 3（5 对）= 7 次作答、6 次答对。
# 这是「这两段示例本该产生多少次作答」的算术事实，用来交叉校验周报里的累计量。
EXPECTED_ATTEMPTS: int = 7
EXPECTED_CORRECT: int = 6
REPORT_RE = re.compile(
    r"共作答 \*\*(\d+)\*\* 题，答对 \*\*(\d+)\*\* 题，正确率 \*\*([\d.]+)%\*\*"
)

RED_LINE_RE = re.compile(
    "斩|杀|血|尸|造反|官府|招安|落草|作法|法术|神力|显灵|天意|神机妙算|借东风|"
    "长征|红军|革命|解放|军粮|粮草|肯德基|KFC|必胜客|华莱士|"
    "多吃|多买|囤券|越买越省|必买"
)
"""角色文案与场景文案的红线词表（文档第十三节第 10 条）。"""

MATH_HINT_RE = re.compile(r"[0-9０-９]|数|算|平均|倍|除|乘|加|减|比|份|斤|两|里|步|秒|格")
"""「介绍里必须含一个与数学有关的小细节」的最低可机器化标准。"""


def numeric_consistency(report_output: str) -> List[str]:
    """交叉校验周报里的累计量是否算术自洽。

    只做「字面 diff」还不够：如果哪天模板变了导致作答次数变化，而文档也跟着被改成另一组数字，
    字面 diff 会通过、但数字本身可能是错的。这里再独立验一遍算术关系：

    * ``共作答 N 题`` 必须等于这两段示例本该产生的作答次数（2 + 5 = 7）；
    * ``答对 M 题`` 必须等于 ``EXPECTED_CORRECT``；
    * ``正确率 X%`` 必须等于 ``round(M / N * 100, 1)``。

    Args:
        report_output: ``cli.py report`` 的真实输出。

    Returns:
        问题描述列表；空列表表示自洽。
    """
    match = REPORT_RE.search(report_output)
    if not match:
        return ["未在 report 输出中找到「共作答 N 题 / 答对 M 题 / 正确率 X%」"]
    total, correct, rate = int(match.group(1)), int(match.group(2)), float(match.group(3))
    issues: List[str] = []
    if total != EXPECTED_ATTEMPTS:
        issues.append(f"共作答 {total} 题，按示例 2+3 应为 {EXPECTED_ATTEMPTS} 题")
    if correct != EXPECTED_CORRECT:
        issues.append(f"答对 {correct} 题，应为 {EXPECTED_CORRECT} 题")
    if total and abs(rate - round(correct / total * 100, 1)) > 0.05:
        issues.append(f"正确率 {rate}% 与 {correct}/{total} 不一致")
    return issues


def normalize(text: str) -> str:
    """把时间戳归一化，便于跨机器比较。"""
    return DATETIME_RE.sub("<时间>", text)


def run(script: Path, args: List[str], stdin: str = "", db_path: str = "") -> str:
    """跑一条 CLI 命令并返回 stdout（stderr 丢弃，避免污染比对）。

    ``db_path`` 会通过环境变量 ``MMQJ_DB_PATH`` 传给子进程，让示例在一个**临时进度库**
    上复跑：既不污染用户自己的 ``runtime/progress.db``，也保证示例 1 永远是「首次运行」状态。
    """
    env = dict(os.environ)
    if db_path:
        env["MMQJ_DB_PATH"] = db_path
    completed = subprocess.run(
        [sys.executable, str(script)] + args,
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(PROJECT_ROOT),
        env=env,
    )
    return completed.stdout or ""


def expected_answers(count: int, grade: int, seed: str) -> str:
    """按 play 的 seed 规则预算出每题的正确答案（用于喂给交互式 play）。"""
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    import problem_gen  # noqa: E402  (延迟导入，避免影响 --help)

    answers = [problem_gen.generate(grade=grade, salt=f"{seed}-{i}")["answer"] for i in range(count)]
    return "\n".join(answers) + "\n"


def readme_blocks() -> Tuple[List[str], List[int]]:
    """取出 README 第七节的全部 console 代码块及每块起始行号（1 基）。"""
    lines = README_PATH.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("## 七、5 个使用示例"))
    end = next(i for i, line in enumerate(lines) if line.startswith("## 八、项目结构"))
    blocks: List[str] = []
    starts: List[int] = []
    index = start
    while index < end:
        if lines[index].strip() == "```console":
            begin = index + 1
            cursor = begin
            while cursor < end and lines[cursor].strip() != "```":
                cursor += 1
            blocks.append("\n".join(lines[begin:cursor]))
            starts.append(begin + 1)  # 代码块首行在文件中的行号
            index = cursor + 1
            continue
        index += 1
    return blocks, starts


def compare_block(block: str, output: str, first_line_no: int) -> List[Dict[str, Any]]:
    """逐行比较一个 README 代码块与实际输出。"""
    actual = normalize(output)
    problems: List[Dict[str, Any]] = []
    for offset, line in enumerate(block.splitlines()):
        line_no = first_line_no + offset
        if not line.strip() or line.startswith("$ "):
            continue
        if ECHO_RE.match(line):
            continue  # 终端不会回显输入
        want = normalize(line)
        if "..." in want:
            # 绝对路径 / 版本号等跨机器不稳定内容：按分段顺序包含判断
            position, matched = 0, True
            for part in [piece for piece in want.split("...") if piece]:
                found = actual.find(part, position)
                if found < 0:
                    matched = False
                    break
                position = found + len(part)
            if not matched:
                problems.append({"line_no": line_no, "expect": line, "actual": "<未匹配到分段>"})
            continue
        if want not in actual:
            problems.append({"line_no": line_no, "expect": line, "actual": "<输出中不存在该行>"})
    return problems


def collect_outputs(db_path: str) -> List[str]:
    """按 README 顺序复跑 5 段示例，返回各段真实输出。

    Args:
        db_path: 临时进度库路径，透传给子进程的 ``MMQJ_DB_PATH``。
    """
    outputs: List[str] = []
    outputs.append(run(CLI, ["doctor"], db_path=db_path))
    outputs.append(
        run(CLI, ["play", "--level", "3-2", "--seed", "demo"], "h\nh\n29\n27\n", db_path)
    )
    outputs.append(
        run(
            CLI,
            ["play", "--grade", "4", "--count", "5", "--seed", "g4"],
            expected_answers(5, 4, "g4"),
            db_path,
        )
    )
    outputs.append(run(CLI, ["wrongbook"], db_path=db_path))
    outputs.append(
        run(CLI, ["report", "--days", "7"], db_path=db_path)
        + "\n"
        + run(DATA_CLI, ["--status"], db_path=db_path)
    )
    return outputs


# ---------------------------------------------------------------------------
# 断言二：故事线数据完整性
# ---------------------------------------------------------------------------


def check_storylines() -> Tuple[List[str], int, int]:
    """校验每条 available 故事线的关卡数据。

    检查项（任何一项不满足都算 FAIL）：

    * 每条线 9 关、每关 9 难，共 81 个 level；
    * ``level_id`` 命名合规：默认线是 ``<stage>-<difficulty>``，其他线是 ``<story>-<stage>-<difficulty>``；
    * ``brief`` 非空、不超过 30 字、线内不重复；
    * ``kp_id`` 必须命中 ``data/curriculum.json``；
    * ``scene`` 非空且线内不重复（叙述模板用它当地点，关名是事件名不能当地点用）；
    * 关名非空且线内不重复；``roles`` 四类角色齐全；``era_note`` 非空。

    Returns:
        ``(问题列表, 故事线数, 关卡总数)``。
    """
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    import problem_gen  # noqa: E402  (延迟导入，避免影响 --help)

    problems: List[str] = []
    kp_ids = set(problem_gen.kp_index().keys())
    stories = problem_gen.list_storylines(only_available=True)
    total_levels = 0
    if not stories:
        return ["index.json 里没有任何 available 的故事线"], 0, 0

    default_id = problem_gen.default_story()
    for item in stories:
        sid = item["id"]
        try:
            data = problem_gen.load_storyline(sid)
        except Exception as exc:  # 文件缺失 / JSON 坏掉
            problems.append(f"{sid}：读取失败 —— {exc}")
            continue

        era_note = str(data.get("era_note") or "").strip()
        if not era_note:
            problems.append(f"{sid}：缺少 era_note（取材来源说明）")
        roles = data.get("roles") or {}
        missing_roles = [key for key in ("clever", "price", "unit", "review") if not roles.get(key)]
        if missing_roles:
            problems.append(f"{sid}：roles 缺少 {missing_roles}")

        stages = data.get("stages") or []
        if len(stages) != 9:
            problems.append(f"{sid}：关卡数 {len(stages)}，应为 9")
        seen_names: List[str] = []
        seen_briefs: List[str] = []
        seen_scenes: List[str] = []
        for order, stage in enumerate(stages, start=1):
            name = str(stage.get("name") or "").strip()
            if not name:
                problems.append(f"{sid} 第 {order} 关：关名为空")
            elif name in seen_names:
                problems.append(f"{sid} 第 {order} 关：关名重复（{name}）")
            seen_names.append(name)

            scene = str(stage.get("scene") or "").strip()
            if not scene:
                problems.append(f"{sid} 第 {order} 关：scene 为空（叙述模板需要具体地点）")
            elif scene == name:
                problems.append(f"{sid} 第 {order} 关：scene 不能等于关名（关名是事件名，不是地点）")
            elif scene in seen_scenes:
                problems.append(f"{sid} 第 {order} 关：scene 线内重复（{scene}）")
            seen_scenes.append(scene)

            stage_no = int(stage.get("stage") or order)
            levels = stage.get("levels") or []
            if len(levels) != 9:
                problems.append(f"{sid} 第 {order} 关：难度数 {len(levels)}，应为 9")
            for level in levels:
                total_levels += 1
                level_id = str(level.get("level_id") or "")
                kp_id = str(level.get("kp_id") or "")
                brief = str(level.get("brief") or "").strip()
                if kp_id not in kp_ids:
                    problems.append(f"{sid} {level_id}：kp_id {kp_id!r} 不在 curriculum.json 里")
                if not brief:
                    problems.append(f"{sid} {level_id}：brief 为空")
                elif len(brief) > 30:
                    problems.append(f"{sid} {level_id}：brief 超长（{len(brief)} 字）")
                if brief in seen_briefs:
                    problems.append(f"{sid} {level_id}：brief 线内重复（{brief}）")
                seen_briefs.append(brief)

                difficulty = str(level.get("level_id") or "").split("-")[-1]
                expect = (
                    f"{stage_no}-{difficulty}" if sid == default_id else f"{sid}-{stage_no}-{difficulty}"
                )
                if level_id != expect:
                    problems.append(f"{sid} {level_id}：level_id 不符合约定，应为 {expect}")
    return problems, len(stories), total_levels


# ---------------------------------------------------------------------------
# 断言四：角色数据与红线
# ---------------------------------------------------------------------------


def check_characters_data() -> Tuple[List[str], int, int, int]:
    """校验 ``data/characters/*.json``：数量、结构、介绍字数与数学线索、红线词。

    Returns:
        ``(问题列表, 角色总数, 最短介绍字数, 最长介绍字数)``。
    """
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    import characters  # noqa: E402
    import journey  # noqa: E402

    problems: List[str] = []
    total = 0
    shortest, longest = 10 ** 6, 0
    available = journey.storyline_order()
    on_disk = characters.story_ids()
    missing = [sid for sid in available if sid not in on_disk]
    if missing:
        problems.append(f"缺少角色库文件：{missing}")
    extra = [sid for sid in on_disk if sid not in available]
    if extra:
        problems.append(f"角色库里有未上线的故事线：{extra}")

    for sid in on_disk:
        data = characters.load(sid)
        items = characters.all_characters(sid)
        total += len(items)
        if len(items) != 14:
            problems.append(f"{sid}：角色 {len(items)} 名，应为 14（9 闯关 + 5 守关）")
        bosses = [item for item in items if item["kind"] == "boss"]
        guards = [item for item in items if item["kind"] == "guard"]
        if len(bosses) != 9:
            problems.append(f"{sid}：闯关大将 {len(bosses)} 名，应为 9")
        if len(guards) != 5:
            problems.append(f"{sid}：守关大将 {len(guards)} 名，应为 5")
        if len(characters.guard_pool_ids(sid)) != 5:
            problems.append(f"{sid}：guard_pool 不是 5 名")
        stage_bosses = data.get("stage_bosses") or {}
        if sorted(int(key) for key in stage_bosses) != list(range(1, 10)):
            problems.append(f"{sid}：stage_bosses 未覆盖 1~9 关")
        union = set(str(v) for v in stage_bosses.values()) | set(characters.guard_pool_ids(sid))
        if len(union) != 14:
            problems.append(f"{sid}：闯关大将与守关池有重叠或缺员（并集 {len(union)}）")
        if len(set(item["id"] for item in items)) != len(items):
            problems.append(f"{sid}：角色 id 有重复")
        for item in items:
            char_id = str(item["id"])
            if char_id != char_id.lower():
                problems.append(f"{sid} {char_id}：角色 id 应全小写")
            intro = str(item.get("intro") or "")
            length = len(intro)
            shortest, longest = min(shortest, length), max(longest, length)
            if not (60 <= length <= 150):
                problems.append(f"{sid} {char_id}：介绍 {length} 字，应为 60~150 字")
            if not str(item.get("title") or "").strip():
                problems.append(f"{sid} {char_id}：缺少 title")
            if not str(item.get("intro_locked_hint") or "").strip():
                problems.append(f"{sid} {char_id}：缺少 intro_locked_hint")
            if not MATH_HINT_RE.search(intro):
                problems.append(f"{sid} {char_id}：介绍里看不到与数学有关的小细节")
            for field, text in (
                ("intro", intro),
                ("title", str(item.get("title") or "")),
                ("intro_locked_hint", str(item.get("intro_locked_hint") or "")),
            ):
                hit = RED_LINE_RE.findall(text)
                if hit:
                    problems.append(f"{sid} {char_id} 的 {field} 命中红线词：{'、'.join(sorted(set(hit)))}")
    return problems, total, (0 if shortest == 10 ** 6 else shortest), longest


def check_scene_red_lines() -> Tuple[List[str], int]:
    """校验 5 条线的场景修饰语：数量、语法、红线词，以及「修饰语 × 地点」的语义冲突。

    冲突判定的做法：用关键词给地点打标签（火焰山=热、舱/房/堂=室内、码头/渡口/外海=水边……），
    再给带地点语义的修饰语写「需要 / 排斥」的标签；两者对不上就算冲突。
    纯时间词（清早/半晌/午后/黄昏/天刚亮/入夜前/归途中等）不携带地点语义，因此不产生冲突。

    Returns:
        ``(问题列表, 修饰语总数)``。
    """
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    import journey  # noqa: E402

    problems: List[str] = []
    total = 0
    for sid in journey.storyline_order():
        variants = journey.scene_variants(sid)
        total += len(variants)
        if len(variants) != 10:
            problems.append(f"{sid}：场景修饰语 {len(variants)} 条，应为 10")
        if len(set(variants)) != len(variants):
            problems.append(f"{sid}：场景修饰语有重复")
        for text in variants:
            if not text.endswith(("的", "时", "日", "前", "后")):
                problems.append(f"{sid}：修饰语「{text}」不以「的/时/日/前/后」结尾")
            hit = RED_LINE_RE.findall(text)
            if hit:
                problems.append(f"{sid}：修饰语「{text}」命中红线词：{'、'.join(sorted(set(hit)))}")
        for stage in range(1, 10):
            place = journey.scene_place(sid, stage)
            tags = _place_tags(place)
            scenes = journey.stage_scenes(sid, stage)
            for text in scenes:
                if text.endswith("的"):
                    problems.append(f"{sid} 第 {stage} 关：场景文本以「的」结尾（{text}）")
                if "的的" in text or "，，" in text or "，的" in text:
                    problems.append(f"{sid} 第 {stage} 关：场景文本有叠字/叠标点（{text}）")
            if len(set(scenes)) != 10:
                problems.append(f"{sid} 第 {stage} 关：10 条场景文本有重复")
            for variant in variants:
                reason = _scene_conflict(variant, tags)
                if reason:
                    problems.append(
                        f"{sid} 第 {stage} 关：修饰语「{variant}」× 地点「{place}」冲突（{reason}）"
                    )
    return problems, total


_PLACE_TAG_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "hot": ("火焰山",),
    "water": ("渡口", "码头", "船坞", "舱", "外海", "水泊", "风信台"),
    "indoor": ("舱", "房", "堂", "馆", "案前", "耳房", "帐中"),
    "market": ("市", "集", "秤房", "粮场", "铺"),
    "post": ("营", "校场", "点将台", "账桌", "风信台"),
    "field": ("粮场", "校场", "林"),
}
"""地点标签：靠地点名里的关键词判定，规则短到可以一眼读完。"""

_SCENE_VARIANT_RULES: Dict[str, Dict[str, Tuple[str, ...]]] = {
    "雪化后的": {"forbids": ("hot",)},
    "落雪后的": {"forbids": ("hot",)},
    "雨后": {"forbids": ("ship", "tent")},
    "雨后的": {"forbids": ("ship", "tent")},
    "雨前": {"forbids": ("ship", "tent")},
    "雨前的": {"forbids": ("ship", "tent")},
    "赶集日的": {"requires": ("market",)},
    "开市日的": {"requires": ("market",)},
    "收摊前的": {"requires": ("market",)},
    "开秤前的": {"requires": ("market",)},
    "收粮日的": {"requires": ("field",)},
    "点卯前的": {"requires": ("post",)},
    "擂鼓时的": {"requires": ("post",)},
    "传令时的": {"requires": ("post",)},
    "值更时的": {"requires": ("post",)},
    "灯下的": {"requires": ("indoor",)},
    "候潮时的": {"requires": ("water",)},
}
"""带地点语义的修饰语：一旦换回这类词，冲突矩阵会立刻报出来。"""


def _place_tags(place: str) -> set:
    """把地点名映射成标签集合。"""
    return {
        tag
        for tag, keywords in _PLACE_TAG_KEYWORDS.items()
        if any(word in str(place) for word in keywords)
    }


def _scene_conflict(variant: str, tags: set) -> str:
    """判断「修饰语 × 地点」是否有语义冲突，返回原因（无冲突返回空串）。"""
    rule = _SCENE_VARIANT_RULES.get(str(variant))
    if not rule:
        return ""
    for need in rule.get("requires", ()):  # type: ignore[union-attr]
        if need not in tags:
            return f"需要「{need}」类地点"
    for bad in rule.get("forbids", ()):  # type: ignore[union-attr]
        if bad in tags:
            return f"与「{bad}」类地点相斥"
    return ""


# ---------------------------------------------------------------------------
# 断言五：闯关规则（解锁 / 随机 / 品阶 / 收集）
# ---------------------------------------------------------------------------


def check_journey_rules() -> List[str]:
    """按文档第十三节逐条断言闯关规则。

    这些断言全部跑在**临时进度库**上，不碰用户自己的 ``runtime/progress.db``。
    """
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    import characters  # noqa: E402
    import journey  # noqa: E402
    import progress  # noqa: E402

    problems: List[str] = []
    tmpdir = Path(tempfile.mkdtemp(prefix="mmqj-journey-"))
    db_path = tmpdir / "progress.db"
    try:
        # 13.1 关卡链
        clears = progress.get_clear_map(db_path=db_path)
        if not journey.is_stage_unlocked("xiyou", 1, clears):
            problems.append("解锁链：第 1 关应默认解锁")
        if journey.is_stage_unlocked("xiyou", 2, clears):
            problems.append("解锁链：未通关第 1 关时第 2 关不应解锁")
        progress.create_run(
            run_id="run-only",
            story="xiyou",
            level_id=journey.stage_key("xiyou", 1),
            seed="only-run",
            boss_id="xiyou_tang",
            guard_id="xiyou_honghaier",
            scene_variant="清晨，五行山下的路口",
            db_path=db_path,
        )
        clears = progress.get_clear_map(db_path=db_path)
        if journey.is_stage_unlocked("xiyou", 2, clears):
            problems.append("解锁链：只有 stage_runs 记录、未写 stage_clear 时不应解锁")
        progress.clear_stage(
            journey.stage_key("xiyou", 1), 1.0, story="xiyou", db_path=db_path, tier="gold"
        )
        clears = progress.get_clear_map(db_path=db_path)
        if not journey.is_stage_unlocked("xiyou", 2, clears):
            problems.append("解锁链：通关第 1 关后第 2 关应解锁")

        # 13.2 故事线链
        if journey.is_story_unlocked("sanguo", clears):
            problems.append("故事线链：西游未全通时三国线不应解锁")
        for stage in range(1, 10):
            progress.clear_stage(
                journey.stage_key("xiyou", stage), 1.0, story="xiyou", db_path=db_path, tier="gold"
            )
        clears = progress.get_clear_map(db_path=db_path)
        if not journey.is_story_unlocked("sanguo", clears):
            problems.append("故事线链：西游 9 关全通后三国线应解锁")
        if journey.is_story_unlocked("silu", clears):
            problems.append("故事线链：三国线未全通时丝路线不应解锁")

        # 13.3 随机性：1000 个 seed
        variants_seen: Dict[int, int] = {}
        counts: Dict[int, int] = {}
        guards_seen: Dict[str, int] = {}
        dup_or_unsorted = 0
        for index in range(1000):
            plan = journey.draw_run("xiyou", 3, f"selfcheck-{index}")
            variants_seen[plan.scene_index] = variants_seen.get(plan.scene_index, 0) + 1
            counts[plan.question_count] = counts.get(plan.question_count, 0) + 1
            guards_seen[plan.guard_id] = guards_seen.get(plan.guard_id, 0) + 1
            if len(set(plan.difficulties)) != plan.question_count:
                dup_or_unsorted += 1
            if list(plan.difficulties) != sorted(plan.difficulties):
                dup_or_unsorted += 1
        if len(variants_seen) != 10:
            problems.append(f"随机性：1000 个 seed 只覆盖 {len(variants_seen)} 个场景变体，应为 10")
        for key, want in ((1, 30.0), (2, 40.0), (3, 30.0)):
            got = counts.get(key, 0) / 10.0
            if abs(got - want) > 5.0:
                problems.append(f"随机性：{key} 题占比 {got:.1f}%，应接近 {want:.0f}%±5%")
        if len(guards_seen) != 5:
            problems.append(f"随机性：1000 个 seed 只出现过 {len(guards_seen)} 名守关大将，应为 5")
        if dup_or_unsorted:
            problems.append(f"随机性：难度档出现重复或未升序（{dup_or_unsorted} 次）")

        # 13.4 可复现
        first = journey.draw_run("xiyou", 5, "replay-me")
        second = journey.draw_run("xiyou", 5, "replay-me")
        if first != second:
            problems.append("可复现：同一 seed 两次 draw_run 结果不同")

        # 13.5 品阶边界
        cases = ((0.59, "gold"), (0.60, "gold"), (0.61, "silver"), (1.00, "silver"), (1.01, "bronze"))
        for ratio, want in cases:
            got = journey.grade_tier(int(ratio * 100000), 100000)
            if got != want:
                problems.append(f"品阶：ratio {ratio} 判成 {got}，应为 {want}")
        if journey.grade_tier(30000, 100000, used_hint=True) != "silver":
            problems.append("品阶：用过提示时上限应为白银")
        if journey.grade_tier(10000, 100000, used_skip=True) is not None:
            problems.append("品阶：跳过时应返回 None")
        if journey.grade_tier(10000, 100000, retry_count=1) != "gold":
            problems.append("品阶：答错 1 次不应降品阶")
        if journey.grade_tier(10000, 100000, retry_count=3) != "silver":
            problems.append("品阶：答错 3 次应降到白银")

        # 13.6 收集 + 13.7 介绍解锁
        plan = journey.draw_run("xiyou", 1, "collect-me")
        chars = journey.run_characters(plan)
        progress.clear_stage(
            journey.stage_key("xiyou", 1), 1.0, story="xiyou", db_path=db_path, tier="gold"
        )
        journey.collect("xiyou", chars, "gold", True, db_path=db_path)
        table = progress.get_collection("xiyou", db_path=db_path)
        for char_id in chars:
            if char_id not in table:
                problems.append(f"收集：{char_id} 通关后没有进角色库")
            elif not table[char_id]["intro_unlocked"]:
                problems.append(f"收集：{char_id} 干净通关后介绍应解锁")
        journey.collect("xiyou", chars, "bronze", False, db_path=db_path)
        table = progress.get_collection("xiyou", db_path=db_path)
        for char_id in chars:
            if table[char_id]["best_tier"] != "gold":
                problems.append(f"收集：{char_id} 重玩拿到更低品阶后 best_tier 被下调了")
            if not table[char_id]["intro_unlocked"]:
                problems.append(f"收集：{char_id} 介绍被回锁了")

        other = journey.draw_run("xiyou", 2, "collect-me-2")
        dirty_chars = journey.run_characters(other)
        journey.collect("xiyou", dirty_chars, "silver", False, db_path=db_path)
        table = progress.get_collection("xiyou", db_path=db_path)
        for char_id in dirty_chars:
            if char_id in chars:
                continue
            if char_id not in table:
                problems.append(f"收集：{char_id} 通关后没有进角色库")
            elif table[char_id]["intro_unlocked"]:
                problems.append(f"收集：{char_id} 未干净通关，介绍不应解锁")
        if journey.collect("xiyou", dirty_chars, None, True, db_path=db_path):
            problems.append("收集：跳过（无品阶）时不应收集角色")

        # 角色图鉴三状态
        view = {item["id"]: item for item in characters.collection_view("xiyou", table)}
        unseen = [item for item in view.values() if item["state"] == characters.STATE_UNSEEN]
        unlocked = [item for item in view.values() if item["state"] == characters.STATE_UNLOCKED]
        locked = [item for item in view.values() if item["state"] == characters.STATE_COLLECTED]
        if not unseen or not unlocked or not locked:
            problems.append("图鉴：三种状态没有同时出现（未遇见 / 待解锁 / 已解锁）")
        for item in view.values():
            if item["state"] != characters.STATE_UNLOCKED and item["intro"]:
                problems.append(f"图鉴：{item['id']} 未解锁却泄露了介绍原文")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return problems


# ---------------------------------------------------------------------------
# 断言三：演示页内联题库与 demo/bank.json 一致
# ---------------------------------------------------------------------------


def embedded_bank() -> Any:
    """从 ``demo/index.html`` 的标记区取出内联题库对象。"""
    html = DEMO_INDEX.read_text(encoding="utf-8")
    if BANK_START not in html or BANK_END not in html:
        raise ValueError(f"demo/index.html 缺少 {BANK_START} / {BANK_END} 标记")
    middle = html.split(BANK_START, 1)[1].split(BANK_END, 1)[0].strip()
    if not middle.startswith("var BANK = "):
        raise ValueError("demo/index.html 标记区里不是 var BANK = {...};")
    text = middle[len("var BANK = "):].strip()
    if text.endswith(";"):
        text = text[:-1]
    return json.loads(text)


def reexport_bank() -> Any:
    """用当前代码 + 示例数据把演示题库重新导出一遍（不写文件）。

    这是「内联 == bank.json」之外的第三只眼：如果两边被同步改错（比如把某个答案
    一起改成错的），逐字节比对会通过，但重新计算出来的真值不会骗人。
    为了保证可复现，这里强制走离线示例快照（临时缓存目录 + 清掉令牌），
    与 ``export_demo_bank.py`` 的生成环境一致。
    """
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    import data_source  # noqa: E402
    import export_demo_bank  # noqa: E402
    import problem_gen  # noqa: E402
    from mcp_client import TOKEN_ENV  # noqa: E402

    os.environ.pop(TOKEN_ENV, None)
    tmpdir = Path(tempfile.mkdtemp(prefix="mmqj-bank-"))
    original_cache = data_source.CACHE_DIR
    data_source.CACHE_DIR = tmpdir
    data_source.reset_client()
    problem_gen._STATE = None
    try:
        return export_demo_bank.build_bank()
    finally:
        data_source.CACHE_DIR = original_cache
        problem_gen._STATE = None
        shutil.rmtree(tmpdir, ignore_errors=True)


def check_demo_bank() -> Tuple[List[str], int]:
    """校验演示页题库：内联 == bank.json == 重新导出的真值，且题目/角色/规则结构合法。

    Returns:
        ``(问题列表, 题目总数)``。
    """
    problems: List[str] = []
    if not DEMO_BANK.exists():
        return [f"缺少 {DEMO_BANK.name}，请先运行 scripts/export_demo_bank.py"], 0
    try:
        inline = embedded_bank()
    except (ValueError, json.JSONDecodeError) as exc:
        return [f"读取内联题库失败 —— {exc}"], 0
    on_disk = json.loads(DEMO_BANK.read_text(encoding="utf-8"))
    if inline != on_disk:
        problems.append("demo/index.html 内联题库与 demo/bank.json 不一致（请重跑 export_demo_bank.py）")

    try:
        fresh = reexport_bank()
    except Exception as exc:  # pragma: no cover - 导不出来就是硬故障
        problems.append(f"重新导出演示题库失败 —— {exc}")
        fresh = None
    if fresh is not None and fresh != on_disk:
        problems.append(
            "demo/bank.json 与当前代码重新导出的结果不一致（可能被手工改过，或改了出题逻辑没重跑脚本）"
        )

    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    import characters  # noqa: E402
    import journey  # noqa: E402
    import problem_gen  # noqa: E402

    available = problem_gen.story_ids(only_available=True)
    bank = on_disk.get("bank") or {}
    if sorted(bank.keys()) != sorted(available):
        problems.append(f"题库覆盖的故事线 {sorted(bank.keys())} 与可用线 {sorted(available)} 不一致")

    # 规则参数必须与 data/run_rules.json 同源：演示页的品阶判定完全靠它。
    if on_disk.get("rules") != journey.load_rules():
        problems.append("demo 题库里的 rules 与 data/run_rules.json 不一致（两端会判出不同品阶）")

    total = 0
    for sid in available:
        items = bank.get(sid) or []
        if len(items) != 81:
            problems.append(f"{sid}：演示题库 {len(items)} 题，应为 81（9 关 × 9 难）")
        seen: set = set()
        for problem in items:
            total += 1
            level_id = str(problem.get("level_id"))
            stage = int(problem.get("stage") or 0)
            difficulty = int(problem.get("difficulty") or 0)
            if not (1 <= stage <= 9 and 1 <= difficulty <= 9):
                problems.append(f"{sid} {level_id}：stage/difficulty 越界（{stage}/{difficulty}）")
            if (stage, difficulty) in seen:
                problems.append(f"{sid} {level_id}：同一关同一难度重复出现")
            seen.add((stage, difficulty))
            if level_id != problem_gen.level_id_for(sid, stage, difficulty):
                problems.append(f"{sid} {level_id}：level_id 与 stage/difficulty 不符")
            answer = problem.get("answer")
            if not isinstance(answer, (int, float)) or isinstance(answer, bool):
                problems.append(f"{sid} {level_id}：answer 不是数值（{answer!r}）")
            if len(problem.get("hints") or []) != 3:
                problems.append(f"{sid} {level_id}：提示不是 3 条")
            if len(problem.get("steps") or []) < 2:
                problems.append(f"{sid} {level_id}：讲解步骤少于 2 条")
            table = problem.get("table") or {}
            if not table.get("columns") or not table.get("rows"):
                problems.append(f"{sid} {level_id}：数据表为空")
            if not str(problem.get("question") or "").strip():
                problems.append(f"{sid} {level_id}：题干为空")

        # 角色库与关卡表
        char_payload = (on_disk.get("characters") or {}).get(sid) or {}
        if len(char_payload) != 14:
            problems.append(f"{sid}：演示页角色 {len(char_payload)} 名，应为 14")
        stages_payload = (on_disk.get("stages") or {}).get(sid) or []
        if len(stages_payload) != 9:
            problems.append(f"{sid}：演示页关卡 {len(stages_payload)} 个，应为 9")
        for entry in stages_payload:
            scenes = entry.get("scenes") or []
            if len(scenes) != 10:
                problems.append(f"{sid} 第 {entry.get('stage')} 关：场景文本 {len(scenes)} 条，应为 10")
            if len(set(scenes)) != len(scenes):
                problems.append(f"{sid} 第 {entry.get('stage')} 关：场景文本有重复")
            for text in scenes:
                if str(text).endswith("的"):
                    problems.append(f"{sid} 第 {entry.get('stage')} 关：场景文本以「的」结尾（{text}）")
            if not entry.get("bossId"):
                problems.append(f"{sid} 第 {entry.get('stage')} 关：缺少闯关大将")
        if len((on_disk.get("guards") or {}).get(sid) or []) != 5:
            problems.append(f"{sid}：守关池不是 5 名")

    # 固定用例：两端一致性的唯一机器凭证
    vectors = on_disk.get("vectors") or []
    if not vectors:
        problems.append("demo 题库缺少 vectors（两端一致性无法校验）")
    for vec in vectors:
        try:
            plan = journey.draw_run(str(vec["story"]), int(vec["stage"]), str(vec["seed"]))
        except Exception as exc:  # pragma: no cover - 数据坏了才会走到
            problems.append(f"固定用例重算失败（{vec.get('story')}/{vec.get('seed')}）—— {exc}")
            continue
        expect = {
            "sceneIndex": plan.scene_index,
            "sceneText": plan.scene_text,
            "guardId": plan.guard_id,
            "questionCount": plan.question_count,
            "difficulties": list(plan.difficulties),
        }
        actual = {
            "sceneIndex": vec.get("sceneIndex"),
            "sceneText": vec.get("sceneText"),
            "guardId": vec.get("guardId"),
            "questionCount": vec.get("questionCount"),
            "difficulties": vec.get("difficulties"),
        }
        if expect != actual:
            problems.append(
                f"固定用例 {vec.get('story')}/{vec.get('stage')}/{vec.get('seed')} 与重算结果不一致"
            )
    return problems, total


def _is_positive_int(value: Any) -> bool:
    """正整数判断（布尔不算数）。"""
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_positive_number(value: Any) -> bool:
    """正数判断（整数或小数，布尔不算数）。"""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def check_easter_egg() -> List[str]:
    """校验随机彩蛋：``run_rules.json`` 配置 → ``bank.json`` → ``index.html`` 三处一致。

    检查项（任何一项不满足都算 FAIL）：

    * ``easter_egg`` 段必备键齐全、类型正确（开关/上限/冷却/队列/去重窗口/任务权重等）；
    * ``pool`` 每条：``id`` 全局唯一、``type`` ∈ {A,C,D}、``weight`` 正整数、
      C 类必须带合法的 ``mini``；
    * ``pool`` 内所有字符串字段不过红线词表（``RED_LINE_RE``）；
    * ``demo/bank.json`` 里的 ``rules.easter_egg`` 与 ``data/run_rules.json`` 一致
      （配置经导出流入演示页，防止只改一边）；
    * ``demo/egg-engine.js`` 存在且不含 ``</script>``；``demo/index.html`` 含 EGG 标记，
      且标记之间注入的引擎与磁盘上的引擎文件一致。

    Returns:
        问题描述列表；空列表表示通过。
    """
    problems: List[str] = []
    if not RUN_RULES.exists():
        return [f"缺少 {RUN_RULES.name}（彩蛋配置的唯一真相源）"]
    try:
        rules = json.loads(RUN_RULES.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"data/run_rules.json 不是合法 JSON —— {exc}"]
    egg = rules.get("easter_egg")
    if not isinstance(egg, dict):
        return ["data/run_rules.json 缺少 easter_egg 配置段（随机彩蛋参数）"]

    if not isinstance(egg.get("enabled_default"), bool):
        problems.append(f"easter_egg.enabled_default 应为布尔值（当前 {egg.get('enabled_default')!r}）")
    for key in ("daily_hard_cap", "per_run_cap", "global_cooldown_s"):
        if not _is_positive_int(egg.get(key)):
            problems.append(f"easter_egg.{key} 应为正整数（当前 {egg.get(key)!r}）")
    cooldown = egg.get("channel_cooldown_s")
    if not isinstance(cooldown, dict) or not all(k in cooldown for k in ("T1", "T2", "T3")):
        problems.append("easter_egg.channel_cooldown_s 应含 T1/T2/T3 三个频道的冷却秒数")
    else:
        for channel, seconds in cooldown.items():
            if not (isinstance(seconds, int) and not isinstance(seconds, bool) and seconds >= 0):
                problems.append(
                    f"easter_egg.channel_cooldown_s.{channel} 应为非负整数"
                    f"（0 表示该频道不冷却，当前 {seconds!r}）"
                )
    interval = egg.get("t3_interval_range_s")
    if not (
        isinstance(interval, list)
        and len(interval) == 2
        and all(_is_positive_number(v) for v in interval)
        and interval[0] <= interval[1]
    ):
        problems.append(f"easter_egg.t3_interval_range_s 应为两元素升序区间（当前 {interval!r}）")
    for key in (
        "enter_ignore_input_ms", "minimize_auto_close_s", "queue_cap",
        "no_interact_streak_limit", "seen_window_h", "char_window_h",
        "freshness_days", "streak_target", "line_size",
        "gold_fast_ratio", "dash_streak_days",
    ):
        if not _is_positive_number(egg.get(key)):
            problems.append(f"easter_egg.{key} 应为正数（当前 {egg.get(key)!r}）")

    pool = egg.get("pool")
    if not (isinstance(pool, list) and pool):
        problems.append("easter_egg.pool 应为非空列表")
    else:
        seen_ids: set = set()
        for index, item in enumerate(pool):
            if not isinstance(item, dict):
                problems.append(f"pool[{index}] 不是对象")
                continue
            egg_id = item.get("id")
            if not (isinstance(egg_id, str) and egg_id):
                problems.append(f"pool[{index}] 缺少字符串 id")
            elif egg_id in seen_ids:
                problems.append(f"pool id 重复：{egg_id}")
            if egg_id:
                seen_ids.add(egg_id)
            if item.get("type") not in EGG_POOL_TYPES:
                problems.append(
                    f"pool[{index}]（{egg_id}）type 应为 {sorted(EGG_POOL_TYPES)} 之一（当前 {item.get('type')!r}）"
                )
            if not _is_positive_int(item.get("weight")):
                problems.append(f"pool[{index}]（{egg_id}）weight 应为正整数（当前 {item.get('weight')!r}）")
            if item.get("type") == "C" and item.get("mini") not in EGG_MINI_KINDS:
                problems.append(
                    f"pool[{index}]（{egg_id}）C 类必须带合法 mini"
                    f"（{sorted(EGG_MINI_KINDS)} 之一，当前 {item.get('mini')!r}）"
                )
            for field, text in item.items():
                if not isinstance(text, str):
                    continue
                hit = RED_LINE_RE.findall(text)
                if hit:
                    problems.append(
                        f"pool[{index}]（{egg_id}）的 {field} 命中红线词：{'、'.join(sorted(set(hit)))}"
                    )

    task_weights = egg.get("task_weights")
    if not isinstance(task_weights, dict) or set(task_weights) != EGG_TASK_KEYS:
        problems.append(f"easter_egg.task_weights 应恰好含 7 个任务：{sorted(EGG_TASK_KEYS)}")
    type_weights = egg.get("type_weights")
    if not isinstance(type_weights, dict) or not EGG_POOL_TYPES <= set(type_weights):
        problems.append("easter_egg.type_weights 应含 A/C/D 三类权重")

    # 配置必须经导出流入演示页：bank.json 的 rules.easter_egg 与 run_rules.json 逐字节一致
    if not DEMO_BANK.exists():
        problems.append("缺少 demo/bank.json，请先运行 scripts/export_demo_bank.py")
    else:
        try:
            bank = json.loads(DEMO_BANK.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            problems.append(f"demo/bank.json 不是合法 JSON —— {exc}")
        else:
            inline_egg = (bank.get("rules") or {}).get("easter_egg")
            if inline_egg != egg:
                problems.append(
                    "demo/bank.json 的 rules.easter_egg 与 data/run_rules.json 不一致"
                    "（配置没流进演示页，请重跑 scripts/export_demo_bank.py）"
                )

    # 引擎文件与注入标记
    engine_text = ""
    if not EGG_ENGINE.exists():
        problems.append("缺少 demo/egg-engine.js（彩蛋引擎）")
    else:
        engine_text = EGG_ENGINE.read_text(encoding="utf-8")
        if "</script" in engine_text.lower():
            problems.append("demo/egg-engine.js 含有 </script> 字样，不能安全内联进演示页")

    html = DEMO_INDEX.read_text(encoding="utf-8")
    if EGG_START not in html or EGG_END not in html:
        problems.append(f"demo/index.html 缺少 {EGG_START} / {EGG_END} 标记")
    else:
        middle = html.split(EGG_START, 1)[1].split(EGG_END, 1)[0]
        # 刘海屏 safe-area：齿轮 / beacon / 卡片的 bottom 必须吃进 env(safe-area-inset-bottom)，
        # 防回归——改 demo/egg-engine.js 的 CSS 后要重跑导出脚本才会生效。
        if "env(safe-area-inset-bottom)" not in middle:
            problems.append(
                "注入的彩蛋引擎 CSS 未适配 safe-area（应含 env(safe-area-inset-bottom)；"
                "改 demo/egg-engine.js 后需重跑 scripts/export_demo_bank.py）"
            )
        if engine_text:
            inner = middle.strip()
            if inner.startswith("<script>"):
                inner = inner[len("<script>"):]
            if inner.endswith("</script>"):
                inner = inner[: -len("</script>")]
            if inner.strip() != engine_text.strip():
                problems.append(
                    "demo/index.html 注入的彩蛋引擎与 demo/egg-engine.js 不一致"
                    "（请重跑 scripts/export_demo_bank.py）"
                )
    return problems


_CN_COUNT = {"两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_SAMPLE_COUNT_RE = re.compile(r"这 (\d+) 样")
_SAMPLE_COUNT_2_RE = re.compile(r"(\d+) 份一共多少")
_BRIEF_COUNT_RE = re.compile(r"(?<![前第])([两二三四五六七八九])([样份])")
"""brief 里的总数表述；`前两样` / `第三样` 这类序数说法不算总数，排除掉。"""


def check_brief_question() -> List[str]:
    """校验「brief 里写的份数」与「题面里的份数」一致。

    这是 QA 抓到的另一类隐患：brief 写「四样餐品」，题面却问「这 6 样」。
    只对句式里明确带计数的两类题面做检查（第 1/8 关的「这 N 样」、第 3/5 关的「N 份」），
    其余句式不做猜测，避免误报。
    """
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    import problem_gen  # noqa: E402

    problems: List[str] = []
    for sid in problem_gen.story_ids(only_available=True):
        for stage in problem_gen.list_stages(sid):
            for level in stage["levels"]:
                brief = str(level["brief"])
                match = _BRIEF_COUNT_RE.search(brief)
                if not match:
                    continue
                brief_count = _CN_COUNT[match.group(1)]
                question = problem_gen.generate(level_id=level["level_id"], salt="dedupe")["question"]
                found = _SAMPLE_COUNT_RE.search(question) or _SAMPLE_COUNT_2_RE.search(question)
                if not found:
                    continue
                question_count = int(found.group(1))
                if question_count != brief_count:
                    problems.append(
                        f"{level['level_id']}：brief 写 {brief_count} {match.group(2)}，"
                        f"题面是 {question_count} {match.group(2)}（{question}）"
                    )
    return problems


def distinct_questions() -> Tuple[int, int]:
    """统计默认线 81 关在固定种子下的不同题面数与关卡总数。

    固定 ``salt`` 是为了让统计结果与机器、时刻无关（随机种子每次都不同的话，
    这个数字没有意义）。返回 ``(不同题面数, 关卡总数)``。
    """
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    import problem_gen  # noqa: E402

    default_id = problem_gen.default_story()
    questions = set()
    levels = 0
    for stage in problem_gen.list_stages(default_id):
        for level in stage["levels"]:
            levels += 1
            questions.add(problem_gen.generate(level_id=level["level_id"], salt="dedupe")["question"])
    return len(questions), levels


def check_readme_numbers(story_count: int, total_levels: int) -> Tuple[List[str], int]:
    """README 里的规模数字必须与代码实际一致。

    这是 QA 指出的盲区：自检原本只验「示例输出」，README 正文里的「405 关」「81 关」
    写错了没人管。这里把三个数字都机器化：

    * ``<total_levels> 个关卡位``（5 条线合计）
    * ``<per_line> 关``（每条线的关卡数）
    * ``<distinct> 条不同题面``（默认线固定种子口径）

    Returns:
        ``(问题列表, 不同题面数)``。
    """
    distinct, _ = distinct_questions()
    text = README_PATH.read_text(encoding="utf-8")
    per_line = total_levels // max(story_count, 1)
    problems: List[str] = []
    expected = {
        f"{total_levels} 个关卡位": "可用故事线的关卡位总数",
        f"{per_line} 关": "每条线的关卡数",
        f"{distinct} 条不同题面": "默认线固定种子下的不同题面数",
    }
    for snippet, label in expected.items():
        if snippet not in text:
            problems.append(f"README 里没有出现「{snippet}」（{label}），或数字已过期")
    return problems, distinct


def main() -> int:
    """入口：返回 0 表示文档与代码一致。"""
    if not README_PATH.exists():
        print("DOC_SYNC: FAIL —— 找不到 README.md")
        return 1

    blocks, starts = readme_blocks()
    if len(blocks) < 5:
        print(f"DOC_SYNC: FAIL —— README 第七节应有 5 个示例块，实际 {len(blocks)} 个")
        return 1

    # 在临时目录建一份全新的进度库复跑，绝不触碰用户自己的 runtime/progress.db。
    # 目录特意命名为 <tmp>/runtime/progress.db：这样示例 1 里 `...\runtime\progress.db`
    # 这类带省略号的路径行，在本机与临时库两种情况下都能按「分段包含」匹配上。
    #
    # 因为每次都是全新建库，断言就与调用者的历史进度完全无关 —— 首跑、二跑、跑一百次
    # 结果都一样（这是「幂等」的根治法：不依赖历史状态，也不靠预热）。
    tmpdir = tempfile.mkdtemp(prefix="mmqj-selfcheck-")
    try:
        db_dir = Path(tmpdir) / "runtime"
        db_dir.mkdir(parents=True, exist_ok=True)
        outputs = collect_outputs(str(db_dir / "progress.db"))
    finally:
        # 清理失败（例如系统装了 safe-delete 拦截）不能影响校验结论，也不能留脏目录
        shutil.rmtree(tmpdir, ignore_errors=True)

    problems: List[Dict[str, Any]] = []
    for block, first_line, output in zip(blocks, starts, outputs):
        problems.extend(compare_block(block, output, first_line))

    # 累计量再独立验一遍算术（字面 diff 之外的第二道网）
    for issue in numeric_consistency(outputs[4]):
        problems.append({"line_no": starts[4], "expect": "周报累计量自洽", "actual": issue})

    story_issues, story_count, story_levels = check_storylines()
    for issue in story_issues:
        problems.append(
            {"line_no": 0, "expect": "故事线数据完整（9 关 × 9 难 / kp 命中 / brief 不重复）", "actual": issue}
        )

    bank_issues, bank_total = check_demo_bank()
    for issue in bank_issues:
        problems.append(
            {"line_no": 0, "expect": "demo 内联题库与 bank.json 一致", "actual": issue}
        )

    readme_issues, distinct = check_readme_numbers(story_count, story_levels)
    for issue in readme_issues:
        problems.append(
            {"line_no": 0, "expect": "README 里的规模数字与代码一致", "actual": issue}
        )

    for issue in check_brief_question():
        problems.append(
            {"line_no": 0, "expect": "brief 里的份数与题面一致", "actual": issue}
        )

    char_issues, char_total, char_short, char_long = check_characters_data()
    for issue in char_issues:
        problems.append(
            {"line_no": 0, "expect": "角色库完整（14 名/线、介绍 60~150 字、含数学细节、无红线词）", "actual": issue}
        )

    scene_issues, scene_total = check_scene_red_lines()
    for issue in scene_issues:
        problems.append(
            {"line_no": 0, "expect": "场景修饰语合规（每线 10 条、语法自洽、无红线词）", "actual": issue}
        )

    rule_issues = check_journey_rules()
    for issue in rule_issues:
        problems.append(
            {"line_no": 0, "expect": "闯关规则（解锁链 / 随机性 / 可复现 / 品阶 / 收集）", "actual": issue}
        )

    egg_issues = check_easter_egg()
    for issue in egg_issues:
        problems.append(
            {"line_no": 0, "expect": "彩蛋配置与引擎注入自洽（run_rules ↔ bank.json ↔ index.html）", "actual": issue}
        )

    if not problems:
        total_lines = sum(len(block.splitlines()) for block in blocks)
        print(
            f"DOC_SYNC: OK —— 5 段示例共 {total_lines} 行与当前代码一致；"
            f"周报累计量自洽（{EXPECTED_ATTEMPTS} 题 / 答对 {EXPECTED_CORRECT} / "
            f"{round(EXPECTED_CORRECT / EXPECTED_ATTEMPTS * 100, 1)}%）；"
            f"故事线 {story_count} 条 × {story_levels // max(story_count, 1)} 关校验通过；"
            f"demo 题库 {bank_total} 题与 bank.json / 重导出结果一致；"
            f"README 规模数字一致（{story_levels} 个关卡位，{distinct} 条不同题面）；"
            f"角色 {char_total} 名（介绍 {char_short}~{char_long} 字）/ 场景修饰语 {scene_total} 条；"
            f"彩蛋配置 / pool / 引擎注入一致；"
            f"闯关规则断言全部通过"
        )
        return 0

    print(f"DOC_SYNC: FAIL —— 发现 {len(problems)} 处文档与代码不一致：")
    for item in problems:
        if item["line_no"]:
            print(f"  README:{item['line_no']}")
        else:
            print("  [数据校验]")
        print(f"    期望: {item['expect']}")
        print(f"    实际: {item['actual']}")
    return 1


if __name__ == "__main__":  # pragma: no cover - 脚本入口
    raise SystemExit(main())
