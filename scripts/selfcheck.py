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
   一致，且与「当前代码重新导出的结果」一致（567 道题 / 98 名角色 / 规则参数 / 固定用例）；
3. **闯关规则**（``docs/SYSTEM_RULES.md`` 第十三节）：解锁链、故事线链、1000 个 seed 的
   随机覆盖、可复现、品阶边界、收集与介绍解锁；
4. **两端一致**：``bank.vectors`` 里 135 条固定用例，用 ``journey.draw_run`` 重算必须逐条相同；
5. **红线**：98 条角色介绍 + 70 条场景修饰语 + 567 个关名/主题/brief 不得出现暴力 / 迷信 / 竞品 / 消费引导词；
6. **随机彩蛋**（``data/run_rules.json`` 的 ``easter_egg`` 段）：必备键齐全且类型正确、
   pool 条目合法（id 唯一 / type ∈ {A,C,D} / weight 正整数 / C 类带 mini）、文案不过红线词表，
   且配置必须经导出流入 ``demo/bank.json`` 与 ``demo/index.html``（三处一致，防止只改一边）。
7. **背景位图内联**（``demo/assets/bg-*.jpg`` → ``demo/index.html`` 的 base64 data URI）：
   5 张全部内联、负载与源文件重新编码后逐字节对应、5 个消费点都接上了、
   页面仍是零外部请求的单文件、且背景令牌**没有覆盖全局 ``--ink`` / ``--card``**
   （覆盖了会让全站正文变白字，页面直接作废）。

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

**与本机状态隔离**（这一点很关键）：README 第七节记录的是「**示例数据快照**」口径的输出，
而本机 ``data/cache/`` 里若残留着 24 小时内的 MCP 缓存，CLI 就会改用**缓存口径**——
餐品名和营养数字都变成实时值，示例与文档自然对不上。所以自检跑之前会：

* 在临时目录里建一个**空的** ``<tmp>/data/cache``，通过环境变量 ``MMQJ_CACHE_DIR``
  指给子进程（``cli.py`` / ``data_source.py`` 都认这个变量）；
* 本进程内也把 ``data_source.CACHE_DIR`` 指向同一个空目录并清掉出题缓存，
  否则自检自己算「示例 2/3 的参考答案」时会读到实时数字，和子进程那份不是同一份。

这样自检的结果**与调用者的本机状态无关**：有缓存、没缓存、有进度库、没进度库，
首跑、二跑、跑一百次结果都一样。
"""

from __future__ import annotations

import base64
import json
import math
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
CURRICULUM: Path = PROJECT_ROOT / "data" / "curriculum.json"

BANK_START: str = "/* @@BANK_START@@ */"
BANK_END: str = "/* @@BANK_END@@ */"
EGG_START: str = "<!-- @@EGG_START@@ -->"
EGG_END: str = "<!-- @@EGG_END@@ -->"

BG_START: str = "/* @@BG_START@@ */"
BG_END: str = "/* @@BG_END@@ */"
BG_ASSETS_DIR: Path = PROJECT_ROOT / "demo" / "assets"
BG_ASSETS: List[str] = ["bg-hero", "bg-pattern", "bg-map", "bg-codex", "bg-result"]
"""5 张背景氛围位图（横幅 / 全站底纹 / 路线图 / 角色图鉴 / 结算页）。

演示页必须**全部内联**成 base64 data URI：只内联一部分，页面就既不是「单文件」
也不是「同一套素材」，两处会悄悄漂移。
"""

BG_TOKEN_NAMES: List[str] = [
    "--hero-ink", "--hero-soft", "--hero-accent", "--hero-chip", "--hero-chip-ink",
    "--hero-scrim", "--hero-scrim-soft",
    "--result-ink", "--result-soft", "--result-accent", "--result-card",
    "--result-scrim", "--result-scrim-deep",
    "--map-scrim", "--map-scrim-soft",
]
"""背景配套的文字色令牌（**必须带命名空间前缀**）。

裸键 ``ink`` / ``card`` 会覆盖全局 ``--ink`` / ``--card`` → 全站正文变白字、页面作废。
"""

def global_css_vars() -> Dict[str, str]:
    """全局主题变量的期望取值（**单一真相源 = ``export_demo_bank.GLOBAL_CSS_VARS``**）。

    导出脚本与自检脚本必须认同一组变量名与期望值——两边各写一份，
    迟早会漂移（比如导出脚本挡了 ``--line``、自检却没挡）。
    所以这里**直接 import 导出脚本的常量**，不再重复抄一遍。

    Returns:
        ``{变量名: 期望取值}``。
    """
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    import export_demo_bank  # noqa: E402

    return dict(export_demo_bank.GLOBAL_CSS_VARS)


BG_CONSUMERS: Dict[str, str] = {
    "--bg-pattern": "body（全站底纹）",
    "--bg-hero": ".scroll-head（横幅）",
    "--bg-map": "#mapPanel（路线图）",
    "--bg-codex": "#codexPanel（角色图鉴）",
    "--bg-result": "#resultPanel（结算页）",
}
"""5 个背景变量的消费处——**注入了但没接上**等于白做，必须逐个核对。"""

BG_LAYER_RULES: Dict[str, List[str]] = {
    "body": ["background-repeat", "background-size", "background-attachment"],
    ".scroll-head": ["background-size", "background-position", "background-repeat"],
    "#mapPanel": ["background-size", "background-position", "background-repeat"],
    "#codexPanel": ["background-size", "background-repeat"],
    "#resultPanel": ["background-size", "background-position", "background-repeat"],
}
"""每个消费点的背景层数校验表。

CSS 里 ``background`` 简写有几层，``background-repeat`` / ``background-size`` /
``background-position`` / ``background-attachment`` 就必须各给几个值——
**值数对不上，整条声明直接作废**（不是「部分生效」）。所以这层断言能抓住
「加了第 4 层罩纱却忘了补 repeat 的第 4 个值」这类低级但致命的错。
"""

DEMO_INDEX_MIN_BYTES: int = 1_200_000
DEMO_INDEX_MAX_BYTES: int = 2_000_000
"""demo/index.html 的体积预期区间（5 张位图 base64 内联后的真实体量）。"""

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


def run(script: Path, args: List[str], stdin: str = "", db_path: str = "", cache_dir: str = "") -> str:
    """跑一条 CLI 命令并返回 stdout（stderr 丢弃，避免污染比对）。

    ``db_path`` 会通过环境变量 ``MMQJ_DB_PATH`` 传给子进程，让示例在一个**临时进度库**
    上复跑：既不污染用户自己的 ``runtime/progress.db``，也保证示例 1 永远是「首次运行」状态。

    ``cache_dir`` 通过 ``MMQJ_CACHE_DIR`` 传：把子进程的 MCP 缓存目录指向一个**空目录**，
    让示例走「示例数据快照」口径。README 第七节记录的示例就是示例数据口径；
    若本机 ``data/cache/`` 里残留着 24 小时内的 MCP 缓存，``doctor`` 会打印
    「本地缓存：N 个文件」、数据源级别变成 cache，示例就与文档对不上了。
    """
    env = dict(os.environ)
    if db_path:
        env["MMQJ_DB_PATH"] = db_path
    if cache_dir:
        env["MMQJ_CACHE_DIR"] = cache_dir
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


def problem_gen_state_reset() -> None:
    """清掉出题模块的进程内缓存（换数据源后必须清，否则还在用旧数字）。"""
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    import problem_gen  # noqa: E402

    problem_gen._STATE = None


def collect_outputs(db_path: str, cache_dir: str = "") -> List[str]:
    """按 README 顺序复跑 5 段示例，返回各段真实输出。

    Args:
        db_path: 临时进度库路径，透传给子进程的 ``MMQJ_DB_PATH``。
        cache_dir: 空的临时 MCP 缓存目录，透传给子进程的 ``MMQJ_CACHE_DIR``。
    """
    outputs: List[str] = []
    outputs.append(run(CLI, ["doctor"], db_path=db_path, cache_dir=cache_dir))
    outputs.append(
        run(
            CLI,
            ["play", "--level", "3-2", "--seed", "demo"],
            "h\nh\n29\n27\n",
            db_path,
            cache_dir,
        )
    )
    outputs.append(
        run(
            CLI,
            ["play", "--grade", "4", "--count", "5", "--seed", "g4"],
            expected_answers(5, 4, "g4"),
            db_path,
            cache_dir,
        )
    )
    outputs.append(run(CLI, ["wrongbook"], db_path=db_path, cache_dir=cache_dir))
    outputs.append(
        run(CLI, ["report", "--days", "7"], db_path=db_path, cache_dir=cache_dir)
        + "\n"
        + run(DATA_CLI, ["--status"], db_path=db_path, cache_dir=cache_dir)
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

                # 红线词覆盖「关名 / 主题 / brief」三处（补既有缺口）。
                # 补之前这三处**没有任何机器断言**——把「斩杀」直接写进关名，
                # 原来自检会放行。对既有 5 条线的 495 个字段实测命中 0 处，
                # 所以补这条断言零回归风险。
                for field, text in (
                    ("name", name),
                    ("theme", str(stage.get("theme") or "")),
                    ("brief", brief),
                ):
                    hit = RED_LINE_RE.findall(text)
                    if hit:
                        problems.append(
                            f"{sid} {level_id} 的{field}命中红线词：{'、'.join(sorted(set(hit)))}"
                        )

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
    """校验各条线的场景修饰语：数量、语法、红线词，以及「修饰语 × 地点」的语义冲突。

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


def _split_top_level(value: str) -> List[str]:
    """按**顶层**逗号切分 CSS 值（括号里的逗号不算）。"""
    parts: List[str] = []
    depth = 0
    current: List[str] = []
    for char in value:
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        if char == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return parts


def _rule_body(css: str, selector: str) -> str:
    """取 CSS 里某选择器**最后一次**出现的规则体（找不到返回空串）。"""
    body = ""
    for match in re.finditer(rf"(?<![\w.#-]){re.escape(selector)}\s*\{{([^}}]*)\}}", css):
        body = match.group(1)
    return body


def _background_layer_count(rule_body: str) -> int:
    """数一条规则里的背景图层数。

    先看 ``background`` 简写；没有简写就看 ``background-image``
    （``body`` 用的是 ``background-color`` + ``background-image`` 的写法）。
    """
    match = re.search(r"(?<![\w-])background\s*:\s*([^;]+);", rule_body)
    if not match:
        match = re.search(r"(?<![\w-])background-image\s*:\s*([^;]+);", rule_body)
    if not match:
        return 0
    return len(_split_top_level(match.group(1)))


def check_background_layers(html: str) -> List[str]:
    """校验每个背景消费点的「图层数」与 repeat / size / position / attachment 的值数一致。

    CSS 规则：``background`` 简写有几层，后面的 ``background-repeat`` / ``background-size`` /
    ``background-position`` / ``background-attachment`` 就必须各给几个值。
    值数不够时**整条声明作废**（不是只丢掉多出来的那层），所以这里按「必须完全相等」判。

    Args:
        html: ``demo/index.html`` 的全文。

    Returns:
        问题描述列表；空列表表示通过。
    """
    problems: List[str] = []
    for selector, properties in BG_LAYER_RULES.items():
        rule = _rule_body(html, selector)
        if not rule:
            problems.append(f"演示页里找不到 {selector} 的样式规则")
            continue
        layers = _background_layer_count(rule)
        if layers == 0:
            problems.append(f"{selector} 没有 background 声明")
            continue
        for prop in properties:
            match = re.search(rf"(?<![\w-]){re.escape(prop)}\s*:\s*([^;]+);", rule)
            if not match:
                problems.append(f"{selector} 有 {layers} 层背景，却没写 {prop}")
                continue
            values = len(_split_top_level(match.group(1)))
            if values != layers:
                problems.append(
                    f"{selector} 的 {prop} 给了 {values} 个值，但背景有 {layers} 层"
                    "（值数对不上，整条声明会作废）"
                )
    return problems


def check_background_assets() -> List[str]:
    """校验背景位图：5 张全部内联、负载与源文件逐字节对应、页面仍是零外部请求的单文件。

    检查项（任何一项不满足都算 FAIL）：

    1. ``demo/assets/`` 下 5 张 ``bg-*.jpg`` 都在；
    2. ``demo/index.html`` 有 BG 标记，且 **5 张全部**内联成 base64 data URI；
    3. 每个内联负载与源 jpg **重新 base64 编码后逐字节相同**（防「改了图忘了重跑脚本」）；
    4. 背景令牌**不得覆盖全局主题变量** ``--ink`` / ``--card`` 等
       （裸键 ``ink`` / ``card`` 写进 ``:root`` → 全站正文变白字，页面直接作废）；
    5. 配套文字色令牌齐全（``--hero-ink`` / ``--result-ink`` …）；
    6. 5 个消费点都真的用上了 ``var(--bg-*)``（注入了却没接上等于白做）；
    7. 页面仍是**零外部资源**：HTML 里不得出现指向 ``.jpg`` / ``.png`` / ``http(s)`` 的
       ``url(...)`` / ``src=`` / ``href=``（base64 data URI 与 SVG 片段引用除外）；
    8. ``demo/index.html`` 体积落在预期区间（1.2MB ~ 2MB）。

    Returns:
        问题描述列表；空列表表示通过。
    """
    problems: List[str] = []
    if not DEMO_INDEX.exists():
        return ["缺少 demo/index.html"]
    html = DEMO_INDEX.read_text(encoding="utf-8")
    raw = DEMO_INDEX.read_bytes()

    if BG_START not in html or BG_END not in html:
        return [f"demo/index.html 缺少 {BG_START} / {BG_END} 标记，背景位图没有注入"]
    block = html.split(BG_START, 1)[1].split(BG_END, 1)[0]

    # ① 5 张全部内联，且负载与源文件逐字节对应
    inlined = 0
    for name in BG_ASSETS:
        source = BG_ASSETS_DIR / f"{name}.jpg"
        if not source.exists():
            problems.append(f"缺少背景素材 demo/assets/{name}.jpg")
            continue
        payload = base64.b64encode(source.read_bytes()).decode("ascii")
        needle = f'--{name}: url("data:image/jpeg;base64,{payload}")'
        if needle in block:
            inlined += 1
        elif f"--{name}:" in block:
            problems.append(
                f"--{name} 的 base64 负载与 demo/assets/{name}.jpg 不一致"
                "（素材被改过？请重跑 scripts/export_demo_bank.py）"
            )
        else:
            problems.append(f"--{name} 没有内联进 demo/index.html")
    if inlined != len(BG_ASSETS):
        problems.append(f"只有 {inlined}/{len(BG_ASSETS)} 张背景位图被内联（应全部内联）")

    # ② 背景令牌不得覆盖全局主题变量
    #    （变量名与期望值取自 export_demo_bank.GLOBAL_CSS_VARS —— 单一真相源）
    globals_expected = global_css_vars()
    written = set(re.findall(r"^\s*(--[A-Za-z0-9-]+)\s*:", block, re.M))
    for var in globals_expected:
        if var in written:
            problems.append(f"背景令牌覆盖了全局变量 {var}（会把全站配色打穿）")
    for var, expected in globals_expected.items():
        values = re.findall(rf"^\s*{re.escape(var)}\s*:\s*([^;]+);", html, re.M)
        if not values:
            problems.append(f"demo/index.html 里找不到全局变量 {var}")
        elif values[-1].strip().lower() != expected.lower():
            problems.append(
                f"全局 {var} 的最终取值是 {values[-1].strip()}，应为 {expected}"
                "（背景令牌把它打穿了 → 全站正文会变白字）"
            )

    # ③ 配套文字色令牌齐全
    for token in BG_TOKEN_NAMES:
        if token not in written:
            problems.append(f"背景令牌 {token} 缺失（文字色令牌必须与图片同生共死）")

    # ④ 5 个消费点都接上了
    for var, where in BG_CONSUMERS.items():
        if not re.search(rf"var\(\s*{re.escape(var)}\s*,", html):
            problems.append(f"演示页没有消费 {var}（{where} 应接上背景位图）")

    # ④b 图层数与 repeat / size / position / attachment 的值数必须一一对应
    problems.extend(check_background_layers(html))

    # ⑤ 零外部资源：双击可开、离线可用
    for match in re.finditer(r"url\(([^)]*)\)", html):
        target = match.group(1).strip().strip("\"'")
        if target.startswith("data:") or target.startswith("#"):
            continue
        problems.append(f"demo/index.html 里有指向外部资源的 url({target[:60]})，破坏「单文件零外部请求」")
    for match in re.finditer(r"\b(?:src|href)\s*=\s*\"([^\"]*)\"", html):
        value = match.group(1)
        if value.startswith("data:") or value.startswith("#"):
            continue
        problems.append(f"demo/index.html 里有外部资源引用 {value[:60]}，破坏「单文件零外部请求」")

    # ⑥ 体积区间
    size = len(raw)
    if not (DEMO_INDEX_MIN_BYTES <= size <= DEMO_INDEX_MAX_BYTES):
        problems.append(
            f"demo/index.html 体积 {size} 字节，超出预期区间 "
            f"{DEMO_INDEX_MIN_BYTES}~{DEMO_INDEX_MAX_BYTES}"
        )
    return problems


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

    这是 QA 指出的盲区：自检原本只验「示例输出」，README 正文里的「567 关」「81 关」
    写错了没人管。这里把三个数字都机器化：

    * ``<total_levels> 个关卡位``（全部可用线合计）
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


def check_curriculum_alignment() -> List[str]:
    """课标对齐的机器断言（2026-10-25 考据整改后新增）。

    把这次整改的结论钉成自动检查，防止以后改题时悄悄回退：

    * 第 4 关：1-5 难只能出现**一位数**除数，6-9 难只能出现**两位数**除数
      （小学除法笔算上限是「除数是两位数」，新人教四下）；
    * 第 5 关：1-4 难答案单位是「克」（整数），5-9 难才是「千克」；
    * 第 7 关：1-3 难是分数初步（整数克），4-6 难是分数（答案须为最简分数），
      7-9 难才是百分数——这一条同时守住「3/5 年级不出现百分数题」；
    * 第 9 关：8-9 难是百分数（折扣应用）；
    * 分数答案必须精确判分：等价分数判对、近似小数判错；
    * 知识点图谱：每个 kp 都要带 stage / textbook_ref / note，且不得再挂着
      「简易方程」「负数」这类已移出小学的知识点名。

    Returns:
        问题描述列表；为空表示全部通过。
    """
    issues: List[str] = []
    import problem_gen  # noqa: E402
    import verify  # noqa: E402

    def gen(level_id: str) -> Dict[str, Any]:
        return problem_gen.generate(level_id=level_id, salt="align")

    # 1) 第 4 关除数位数
    for difficulty in range(1, 10):
        problem = gen(f"4-{difficulty}")
        divisor = int((problem.get("diagnosis") or {}).get("divisor") or 0)
        if difficulty <= 5:
            if not 2 <= divisor <= 9:
                issues.append(
                    f"4-{difficulty}：除数是 {divisor}，应为一位数（2-9）"
                    "——小学除法笔算上限是「除数是两位数」，低难档不能出两位及以上除数"
                )
        else:
            if not 10 <= divisor <= 99:
                issues.append(
                    f"4-{difficulty}：除数是 {divisor}，应为两位数（10-99）"
                    "——与关卡标签「除数是两位数的除法」必须一致"
                )

    # 2) 第 5 关单位分层
    for difficulty in range(1, 10):
        problem = gen(f"5-{difficulty}")
        unit = str(problem.get("unit") or "")
        expected = "克" if difficulty <= 4 else "千克"
        if unit != expected:
            issues.append(f"5-{difficulty}：答案单位是「{unit}」，应为「{expected}」")
        if expected == "克" and "." in str(problem.get("answer") or ""):
            issues.append(f"5-{difficulty}：低难档答案应取整数克，实际 {problem.get('answer')}")

    # 3) 第 7 关三段式 + 最简分数
    for difficulty in range(1, 10):
        problem = gen(f"7-{difficulty}")
        answer_type = str(problem.get("answer_type") or "")
        if difficulty <= 3:
            if answer_type == "percent":
                issues.append("7-1~7-3 不得出百分数题（百分数属第三学段，六年级）")
            if "." in str(problem.get("answer") or ""):
                issues.append(f"7-{difficulty}：分数初步题的答案应是整数克，实际 {problem.get('answer')}")
        elif difficulty <= 6:
            if answer_type != "fraction":
                issues.append(f"7-{difficulty}：应为分数题（answer_type=fraction），实际 {answer_type}")
            else:
                text = str(problem.get("answer") or "")
                parts = text.split("/")
                if len(parts) != 2 or not all(part.strip().isdigit() for part in parts):
                    issues.append(f"7-{difficulty}：分数答案格式应为 a/b，实际 {text!r}")
                else:
                    numerator, denominator = int(parts[0]), int(parts[1])
                    if numerator <= 0 or denominator <= 0:
                        issues.append(f"7-{difficulty}：分数答案的分子分母必须为正，实际 {text!r}")
                    elif math.gcd(numerator, denominator) != 1:
                        issues.append(f"7-{difficulty}：分数答案 {text!r} 不是最简分数")
                    elif denominator > 30:
                        issues.append(f"7-{difficulty}：分数分母 {denominator} 超出五下练习范围（应 ≤ 30）")
        else:
            if answer_type != "percent":
                issues.append(f"7-{difficulty}：应为百分数题（answer_type=percent），实际 {answer_type}")

    # 4) 第 9 关高难档是百分数（若该题篮子没够到券的门槛，则按原价付款是设计内的分支）
    for difficulty in (8, 9):
        problem = gen(f"9-{difficulty}")
        rows = (problem.get("data_table") or {}).get("rows") or []
        no_coupon = any("券的情况" in str(row[0]) for row in rows if row)
        if not no_coupon and str(problem.get("answer_type") or "") != "percent":
            issues.append(f"9-{difficulty}：应为百分数题（折扣应用），实际 {problem.get('answer_type')}")
        if no_coupon and difficulty >= 8:
            issues.append(
                f"9-{difficulty}：高档位落在「没有券达标」分支上，"
                "应保证篮子够到门槛，才能教到折扣百分数"
            )

    # 5) 分数答案的判分必须精确
    fraction_problem = gen("7-4")
    answer = str(fraction_problem.get("answer") or "")
    if "/" in answer:
        numerator, denominator = (int(part) for part in answer.split("/"))
        equivalent = verify.check_answer(fraction_problem, f"{numerator * 3}/{denominator * 3}")
        if not equivalent.get("correct"):
            issues.append("分数判分：等价分数（3 倍分子分母）应判对")
        approx = f"{numerator / denominator:.2f}"
        if verify.check_answer(fraction_problem, approx).get("correct"):
            issues.append(f"分数判分：近似小数 {approx} 不应判对（容差必须比数值容差严）")

    # 6) 知识点图谱字段与命名
    curriculum = json.loads(CURRICULUM.read_text(encoding="utf-8"))
    for grade in curriculum.get("grades") or []:
        for kp in grade.get("kps") or []:
            kp_id = str(kp.get("id") or "")
            for field in ("stage", "textbook_ref", "note"):
                if not str(kp.get(field) or "").strip():
                    issues.append(f"知识点 {kp_id} 缺少 {field} 字段")
            name = str(kp.get("name") or "")
            if name in ("简易方程", "负数"):
                issues.append(
                    f"知识点 {kp_id} 仍叫「{name}」：该内容已随 2022 年版课标移出小学，"
                    "应改为小学仍然存在的表述"
                )
    return issues


def check_subjects() -> Tuple[List[str], List[str]]:
    """学科维度断言：**注册表 ↔ 知识点图谱 ↔ 题型模板** 三方一致。

    这一节是新加的护栏，堵的是「学科扩展后悄悄失效却不报错」的四类缺口：

    1. **注册表说有、文件却没有**：``available: true`` 的学科，其 ``curriculum``
       文件必须存在且可解析，``templates`` 模块必须能导入且给出一张非空的
       ``{skill: callable}`` 表；每个 kp 都要带 ``subject``（必须等于所在学科）
       与 ``skill``（必须非空）。
    2. **图谱被静默跳过**：``problem_gen.kp_index()`` 合并多学科时若某个图谱坏掉，
       会降级跳过并记进 ``subject_errors()``。这里断言它**必须为空**——
       降级可以发生，但不许无声无息。
    3. **``answer_type`` 静默失效**：``problem_gen._hint_leaks`` /
       ``verify.check_answer`` / 学科声明的 ``answerTypes`` / 已知类型表，
       这四处缺任意一处，「提示不泄露答案」就会变成装饰。
       所以这里逐对 ``(subject, skill)`` 真出一道题，断言它的 ``answer_type``
       既在全局已知集合里，也在该学科声明的集合里。
    4. **``role_focus`` 指向不存在的角色**（多故事线时代的既有缺口）：
       断言每关的 ``role_focus`` 能在该线的角色库里按 ``name`` 找到，
       或者等于该线 ``roles`` 四类角色之一（如「正使」「粮官」这类身份定位）。

    Returns:
        ``(问题列表, 各学科规模摘要列表)``。
    """
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    import characters  # noqa: E402
    import problem_gen  # noqa: E402
    import subjects  # noqa: E402

    problems: List[str] = []
    summary: List[str] = []

    known_types = set(subjects.all_answer_types())
    available_subjects = subjects.subject_ids(only_available=True)
    if problem_gen.MATH_SUBJECT not in available_subjects:
        problems.append(f"学科注册表里没有上线数学（{problem_gen.MATH_SUBJECT}），历史数据将无法派发")

    # --- 1) 注册表 ↔ 图谱 ↔ 模板 三方一致 ---------------------------------
    for sid in available_subjects:
        try:
            path = subjects.curriculum_path(sid)
        except KeyError as exc:
            problems.append(f"学科 {sid}：注册表条目缺 curriculum —— {exc}")
            continue
        if not path.exists():
            problems.append(f"学科 {sid}：curriculum 文件不存在（{path}）")
            continue
        try:
            payload = problem_gen.load_curriculum(sid)
        except (OSError, ValueError) as exc:
            problems.append(f"学科 {sid}：curriculum 解析失败 —— {exc}")
            continue

        kp_count = 0
        for grade_block in payload.get("grades") or []:
            for kp in grade_block.get("kps") or []:
                kp_count += 1
                kp_id = str(kp.get("id") or "")
                declared = str(kp.get("subject") or "")
                if declared != sid:
                    problems.append(
                        f"学科 {sid} 的知识点 {kp_id}：subject 字段是 {declared!r}，应与所在学科一致"
                    )
                if not str(kp.get("skill") or "").strip():
                    problems.append(f"学科 {sid} 的知识点 {kp_id}：缺少 skill（派发第三级来源）")
        if kp_count == 0:
            problems.append(f"学科 {sid}：知识点图谱里一个 kp 都没有")

        module_path = subjects.template_module_path(sid)
        try:
            table = subjects.load_templates(sid)
        except (KeyError, ImportError, AttributeError) as exc:
            problems.append(f"学科 {sid}：模板模块 {module_path!r} 加载失败 —— {exc}")
            table = {}
        if not table:
            problems.append(f"学科 {sid}：模板表为空（注册表声明了 templates={module_path!r}）")
        skill_count = len(table)
        summary.append(f"{sid} {kp_count} kp / {skill_count} skill")

    # --- 2) 图谱合并不得静默跳过任何学科 ----------------------------------
    problem_gen.kp_index()
    for error in problem_gen.subject_errors():
        problems.append(f"知识点图谱被静默跳过：{error}")

    kp_map = problem_gen.kp_index()

    # --- 3) 每条 level 的 (subject, skill) 必须有模板 ----------------------
    #    并且逐对真出一道题，断言 answer_type 在「全局已知」与「本学科声明」两个集合里。
    #
    #    覆盖口径：
    #    * 挂在故事线上的学科（数学）：取「关卡实际用到的 (subject, skill)」——
    #      知识点图谱里有几个「暂未出题」的 kp（如 g4_angle / g6_ratio），
    #      它们没有模板也不该报错，因为它们没有被任何关卡引用。
    #    * 没有故事线的学科（语文 / 英语）：没有关卡，改用「该学科全部 kp 的 skill」，
    #      这样任何一个「声明了 skill 却没有模板」的 kp 都会被抓住。
    pairs: Dict[Tuple[str, str], str] = {}
    subjects_with_levels = set()
    for item in problem_gen.list_storylines(only_available=True):
        sid = item["id"]
        for stage in problem_gen.load_storyline(sid).get("stages") or []:
            for level in stage.get("levels") or []:
                subject, skill = problem_gen.resolve_dispatch(level, stage, kp_map)
                if not skill:
                    problems.append(
                        f"{sid} {level.get('level_id')}：三级派发（level→stage→kp）全空，无法确定题型"
                    )
                    continue
                subjects_with_levels.add(str(subject))
                pairs.setdefault((subject, skill), str(level.get("level_id")))

    for sid in available_subjects:
        if str(sid) in subjects_with_levels:
            continue
        for kp in kp_map.values():
            if str(kp.get("subject")) != str(sid):
                continue
            skill = str(kp.get("skill") or "")
            if not skill:
                continue
            pairs.setdefault((str(sid), skill), "")

    for (subject, skill), sample_level in sorted(pairs.items()):
        try:
            problem_gen.get_template(subject, skill)
        except KeyError as exc:
            problems.append(f"派发 {subject}/{skill}：没有模板 —— {exc}")
            continue
        # 非数学学科没有故事线关卡，用无故事线路径出一道题来验证答案类型。
        if sample_level:
            problem = problem_gen.generate(level_id=sample_level, salt="subject-check")
        else:
            kp_ids = [
                str(kp.get("id"))
                for kp in kp_map.values()
                if str(kp.get("subject")) == subject and str(kp.get("skill")) == skill
            ]
            if not kp_ids:
                problems.append(f"派发 {subject}/{skill}：图谱里找不到声明该 skill 的知识点")
                continue
            problem = problem_gen.generate(
                kp_id=kp_ids[0], subject=subject, salt="subject-check"
            )
        answer_type = str(problem.get("answer_type") or "")
        if answer_type not in known_types:
            problems.append(
                f"{subject}/{skill}：answer_type {answer_type!r} 不在全局已知集合 {sorted(known_types)} 里"
            )
        allowed = set(subjects.subject_answer_types(subject))
        if answer_type not in allowed:
            problems.append(
                f"{subject}/{skill}：answer_type {answer_type!r} 不在该学科声明的 answerTypes {sorted(allowed)} 里"
            )
        if str(problem.get("subject")) != subject or str(problem.get("skill")) != skill:
            problems.append(
                f"{subject}/{skill}：题目里的派发元信息是 "
                f"({problem.get('subject')}, {problem.get('skill')})，与实际派发不一致"
            )

    # 不进演示题库的学科必须真的不进（否则前端只有数值比较，会「永远判错」）。
    for sid in available_subjects:
        if subjects.in_demo_bank(sid):
            continue
        if str(sid) == problem_gen.MATH_SUBJECT:
            problems.append("数学学科必须 demoBank: true（演示页是按数学题设计题库的）")

    # --- 4) role_focus 必须能在角色库或 roles 里找到 ------------------------
    for item in problem_gen.list_storylines(only_available=True):
        sid = item["id"]
        try:
            role_values = set()
            for value in (problem_gen.load_storyline(sid).get("roles") or {}).values():
                role_values.add(str(value))
            char_names = {str(char.get("name")) for char in characters.all_characters(sid)}
        except (KeyError, OSError, ValueError) as exc:
            problems.append(f"{sid}：角色库读取失败 —— {exc}")
            continue
        known = char_names | role_values
        for stage in problem_gen.load_storyline(sid).get("stages") or []:
            focus = str(stage.get("role_focus") or "").strip()
            if not focus:
                problems.append(f"{sid} 第 {stage.get('stage')} 关：role_focus 为空")
            elif focus not in known:
                problems.append(
                    f"{sid} 第 {stage.get('stage')} 关：role_focus {focus!r} "
                    "既不在角色库里、也不是该线的 roles 之一（角色名会显示成不存在的人）"
                )

    # --- 5) 显式 skill 出题不得崩在模板里 -----------------------------------
    # 这是一个真实漏网过的缺陷：``_generate_from_subject`` 早期只按
    # subject / kp_id / grade 筛候选 kp，**漏了 skill**。于是
    # ``generate(subject="chinese", skill="word_order")`` 有近一半概率先随机抽到一个
    # skill=poem_fill 的知识点，再用显式 word_order 去取模板 → KeyError 崩在模板函数里。
    # 因为 CLI 把 ``--skill`` 暴露给了用户，这条路径用户真的会走到。
    #
    # 断言口径：每个已注册的 (subject, skill) 组合，「模板内部崩」（KeyError /
    # NotImplementedError）一律 FAIL；只有「该 skill 没有 kp 声明」时才允许
    # 以清晰的 ValueError 报错（如 english 的 word_choice 占位）。
    for sid in available_subjects:
        declared_skills = {
            str(kp.get("skill"))
            for kp in kp_map.values()
            if str(kp.get("subject")) == str(sid) and str(kp.get("skill") or "")
        }
        for skill in subjects.known_skills(sid):
            try:
                problem_gen.generate(subject=str(sid), skill=str(skill), salt="skill-check")
                usable = True
            except ValueError:
                usable = False  # 允许：没有 kp 声明这个 skill
            except (KeyError, NotImplementedError) as exc:
                problems.append(
                    f"{sid}/{skill}：用显式 skill 出题时崩在模板里 —— "
                    f"{type(exc).__name__}: {exc}"
                    "（候选知识点没有按 skill 过滤，模板与 kp 不匹配）"
                )
                continue
            if str(skill) in declared_skills and not usable:
                problems.append(
                    f"{sid}/{skill}：有知识点声明了这个 skill，却取不到可用知识点"
                )
            # 显示名：界面上不能出现 poem_fill / add 这类内部标识
            label = subjects.skill_label(str(sid), str(skill))
            if not label or label == str(skill):
                problems.append(
                    f"{sid}/{skill}：学科注册表 skillNames 里没有这个 skill 的中文显示名"
                    "（界面上会直接印出内部标识，小学生看不懂）"
                )
    return problems, summary


def export_kp_badge() -> str:
    """取「导出脚本算出来的徽章文案」，供自检的成功摘要打印。

    单独包一层是为了让摘要文案与实际断言**同源**：摘要里印的数字，
    就是刚刚被断言比对过的那个数字，不是另抄一遍。
    """
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    import export_demo_bank  # noqa: E402

    return export_demo_bank.kp_badge_text()


def check_demo_kp_badge() -> List[str]:
    """断言演示页首屏徽章里的「N 个知识点」== 数学知识点实际个数。

    这一项堵的是一个**真实漏网过的问题**：徽章文案曾是手写死在 ``demo/index.html`` 里的
    「3-6 年级 · 25 个知识点」，新增 ``g6_discount`` 后实际变成 26 个，
    但这行手写文案不会跟着变——它成了全站唯一一处会自己漂的统计数字。

    当时的排查之所以漏掉它，是因为只 grep 了「405」这个数字，没搜「个知识点」这种说法。
    所以这里除了比对数字，还额外断言**全文只有一处「个知识点」**且必须落在标记段内，
    这样「换个地方再写死一份」也会被抓到。

    标记与文案的唯一真相源都在 :mod:`export_demo_bank`，这里直接 import 复用，
    避免自检与导出脚本各写一份正则、迟早漂开。

    Returns:
        问题描述列表；为空表示通过。
    """
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    import export_demo_bank  # noqa: E402
    import problem_gen  # noqa: E402

    problems: List[str] = []
    if not DEMO_INDEX.exists():
        return [f"找不到 {DEMO_INDEX}"]
    html = DEMO_INDEX.read_text(encoding="utf-8")

    start = export_demo_bank.KP_BADGE_START
    end = export_demo_bank.KP_BADGE_END
    if start not in html or end not in html:
        return [f"index.html 缺少知识点徽章标记（{start} / {end}）—— 手改 HTML 时把标记删了"]

    inner = html.split(start, 1)[1].split(end, 1)[0]
    expected = export_demo_bank.kp_badge_text()
    if inner != expected:
        problems.append(f"徽章文案已过期：页面写「{inner}」，应为「{expected}」")

    # 数字再独立对一遍（不信 kp_badge_text 自己，直接数数学 kp）
    match = re.search(r"(\d+)\s*个知识点", inner)
    if not match:
        problems.append(f"徽章文案里找不到「N 个知识点」：{inner!r}")
    else:
        actual = int(match.group(1))
        math_kp = sum(
            1
            for kp in problem_gen.kp_index().values()
            if str(kp.get("subject")) == problem_gen.MATH_SUBJECT
        )
        if actual != math_kp:
            problems.append(f"徽章写 {actual} 个知识点，数学图谱实际 {math_kp} 个")

    # 全文只能有一处「个知识点」，且必须在标记段内
    total_marks = html.count("个知识点")
    outside = html.replace(start + inner + end, "")
    stray = outside.count("个知识点")
    if total_marks != 1 or stray != 0:
        problems.append(
            f"「个知识点」在页面里出现 {total_marks} 处（标记段外还有 {stray} 处）——"
            "统计数字必须只有一处真相源，不允许再手写一份"
        )
    return problems


_CJK_PUNCT: str = "，,。.、；;：:！!？?…·「」『』（）()《》【】 　"
"""中文标点与空白集合（用于「省略标点是否仍判对」的断言）。"""

_CN_NUMERALS: Tuple[str, ...] = ("", "一", "二", "三", "四", "五", "六", "七", "八", "九", "十")
"""序号的中文写法（1 基，索引 0 占位）。中文数字本身是语言事实，不是应用策略，
所以在这里列一份只为「检测 accept 里有没有对应中文写法」，不复制模板里的映射逻辑。"""

_OPTION_SPLIT: str = "（选项："
"""语文 / 英语选择题题面里选项段的起始标记（与 chinese/templates.py 保持一致）。"""


def _parse_options(question: str) -> List[str]:
    """从「（选项：1. 甲　2. 乙　3. 丙）」里解析出选项原文列表。

    Args:
        question: 题面。

    Returns:
        按题面顺序排列的选项原文；不是选择题时返回空列表。
    """
    if _OPTION_SPLIT not in question:
        return []
    body = question.split(_OPTION_SPLIT, 1)[1].rstrip("）")
    options: List[str] = []
    for chunk in body.split("　"):
        if ". " in chunk:
            options.append(chunk.split(". ", 1)[1])
    return options


def check_subject_templates() -> List[str]:
    """语文 / 英语题型模板的**行为**断言（QA 第二轮抓到的三类缺陷的永久回归门）。

    这三条都是「答案看起来对、但题本身没用」的缺陷，靠人眼看题面看不出来：

    1. **选项未洗牌**：语料里 ``options`` 按「正确答案打头」写便于人工核对，
       若模板原样输出，答案会 100% 固定在第 1 项——学生不读题、永远填第一个就全对，
       QA 实测 20/20 命中位置 0。这里换 12 个 ``salt`` 出题，断言答案位置**至少覆盖 2 个**。
    2. **序号作答**：题面用「1. / 2. / 3.」编号呈现选项，等于暗示可以用序号回答。
       断言「正确序号判对」且「错误序号一律判错」——只认一个位置，
       不能把所有序号都塞进 ``accept``（那会让写 1、2、3 全判对，比不洗牌更糟）。
    3. **题干承诺与判分口径矛盾**：古诗题题干写「标点与空格可以省略」，
       但 ``normalize_text`` 只 strip 首尾标点、中间逗号会保留。
       断言「去掉全部标点空白的写法必须判对」，否则孩子照题干照做反被判错，
       诊断还会说「看看有没有漏字」，完全误导。

    断言只按 ``skill`` 名与 ``answer_type`` 判别，**不硬编码任何 kp_id 或词条内容**，
    所以换语料 / 加词条都不会误报。

    Returns:
        问题描述列表；为空表示通过。
    """
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    import problem_gen  # noqa: E402
    import subjects  # noqa: E402
    import verify  # noqa: E402

    issues: List[str] = []
    kp_map = problem_gen.kp_index()
    salts = [f"qa{i}" for i in range(12)]

    for sid in subjects.subject_ids(only_available=True):
        if str(sid) == problem_gen.MATH_SUBJECT:
            continue  # 数学题的答案位置由真实数据决定，不适用本节
        for kp in kp_map.values():
            if str(kp.get("subject")) != str(sid):
                continue
            kp_id = str(kp.get("id"))
            samples = []
            for salt in salts:
                try:
                    samples.append(problem_gen.generate(kp_id=kp_id, subject=sid, salt=salt))
                except (KeyError, ValueError) as exc:
                    issues.append(f"{kp_id}：出题失败 —— {exc}")
                    break
            if not samples:
                continue

            # --- 1) 选择题：答案位置必须有变化 ---
            enum_samples = [item for item in samples if str(item.get("answer_type")) == "enum"]
            positions = set()
            for item in enum_samples:
                options = _parse_options(str(item.get("question") or ""))
                if not options:
                    continue
                if str(item.get("answer")) not in options:
                    issues.append(
                        f"{kp_id}：答案「{item.get('answer')}」不在自己的选项 {options} 里"
                    )
                    continue
                positions.add(options.index(str(item.get("answer"))))
            if len(enum_samples) >= 3 and len(positions) == 1:
                issues.append(
                    f"{kp_id}：{len(enum_samples)} 道选择题的答案全部固定在第 {positions.pop() + 1} 项"
                    "——选项没有洗牌，学生永远填第一个就全对，题目失去测量功能"
                )

            # --- 2) 选择题：序号作答只认正确位置（阿拉伯数字与中文数字都要认）---
            for item in enum_samples[:3]:
                options = _parse_options(str(item.get("question") or ""))
                if not options or str(item.get("answer")) not in options:
                    continue
                right = options.index(str(item.get("answer"))) + 1
                if not verify.check_answer(item, str(right))["correct"]:
                    issues.append(f"{kp_id}：正确序号 {right} 被判错（题面用序号编号就等于允许序号作答）")
                if right < len(_CN_NUMERALS) and _CN_NUMERALS[right]:
                    if not verify.check_answer(item, _CN_NUMERALS[right])["correct"]:
                        issues.append(
                            f"{kp_id}：中文序号「{_CN_NUMERALS[right]}」被判错"
                            "——孩子写「3」和写「三」都很常见，题面既然用序号编号就该两种都认"
                        )
                for other in range(1, len(options) + 1):
                    if other == right:
                        continue
                    if verify.check_answer(item, str(other))["correct"]:
                        issues.append(
                            f"{kp_id}：错误序号 {other} 被判对（正确是 {right}）"
                            "——accept 里只能登记正确位置，不能把所有序号都塞进去"
                        )
                    if other < len(_CN_NUMERALS) and _CN_NUMERALS[other]:
                        if verify.check_answer(item, _CN_NUMERALS[other])["correct"]:
                            issues.append(
                                f"{kp_id}：错误的中文序号「{_CN_NUMERALS[other]}」被判对（正确是 {right}）"
                            )

            # --- 3a) 默写题：标点与空白的全部常见写法都必须判对 ---
            # 这一节盯的是一个真实漏网过的缺陷：题干写「标点与空格可以省略」，
            # 但归一化把「标点」与「空白」当两类 token 分别处理，于是
            # 「保留标点 + 后面多打一个空格」（"早， 忙"）既不是 "早，忙" 也不是 "早 忙"，
            # 被判错。规则不一致本身就说明归一化没做到底。
            # 现在 canonical_text 比对前把标点与空白一起抹掉，这一节就是它的回归门。
            poem_samples = [
                item
                for item in samples
                if str(item.get("answer_type")) == "text"
                and any(ch in str(item.get("answer")) for ch in _CJK_PUNCT)
            ]
            for item in poem_samples:
                answer = str(item.get("answer"))
                bare = "".join(ch for ch in answer if ch not in _CJK_PUNCT)
                # 把句中逗号换成各种「分隔写法」，覆盖学生真实会打出的形态
                variants = {
                    "原样": answer,
                    "无标点": bare,
                    "标点→单空格": answer.replace("，", " "),
                    "标点→双空格": answer.replace("，", "  "),
                    "标点+单空格": answer.replace("，", "， "),
                    "标点+双空格": answer.replace("，", "，  "),
                    "标点+三空格": answer.replace("，", "，   "),
                    "首空格": " " + answer,
                    "尾空格": answer + " ",
                    "全角空格": answer.replace("，", "\u3000"),
                    "半角标点": answer.replace("，", ","),
                }
                for label, text in variants.items():
                    if not verify.check_answer(item, text)["correct"]:
                        issues.append(
                            f"{kp_id}：默写答案的「{label}」写法被判错（{text!r}）——"
                            "题干承诺「标点与空格可以省略」，判分就必须真的做到，"
                            "且不能出现「省略可以、多一个空格不行」这种自相矛盾的规则"
                        )
                        break
                # 反向：改了内容必须判错（防「抹标点」被写成「抹得太多」）
                if len(bare) > 3:
                    wrong = bare[:-1] + "错"
                    if verify.check_answer(item, wrong)["correct"]:
                        issues.append(
                            f"{kp_id}：把内容改成「{wrong}」也被判对——归一化放宽过头了"
                        )

            # --- 3b) 所有文本题：大小写 / 首尾空格宽容，改内容必须判错 ---
            for item in samples:
                if str(item.get("answer_type")) not in ("text", "enum"):
                    continue
                answer = str(item.get("answer") or "")
                if not answer:
                    continue
                if not verify.check_answer(item, " " + answer + " ")["correct"]:
                    issues.append(f"{kp_id}：答案首尾带空格被判错（{answer!r}）")
                if answer.isascii() and answer.isalpha():
                    if not verify.check_answer(item, answer.upper())["correct"]:
                        issues.append(f"{kp_id}：英文答案大写形式被判错（{answer.upper()!r}）")
                    if verify.check_answer(item, answer + "x")["correct"]:
                        issues.append(f"{kp_id}：英文答案多一个字母也被判对（{answer + 'x'!r}）")
                    break
    return issues


def check_answer_semantics() -> List[str]:
    """判分与「提示不泄露答案」的语义断言（正例 + **负例**）。

    这一节堵的是「检查存在但永远返回 False」这类**静默失效**：

    * 加了文本答案类型之后，``_answer_targets("白日依山尽", "text")`` 曾返回 ``[]``，
      于是即使提示明写答案，``_hint_leaks`` 也返回 ``False`` ——
      「提示不泄露答案」变成一个永远通过的空断言，而且不会报错。
    * 同理，``verify.check_answer`` 若不认识 ``text`` / ``enum``，
      所有语文 / 英语题都会被判成「题目数据异常」。

    所以这里**用负例反向验证**：故意造一条明写答案的提示，断言它必须被判为泄露；
    故意造一个只差一个字母的拼写，断言它必须判错（不能靠编辑距离蒙对）。

    Returns:
        问题描述列表；为空表示全部通过。
    """
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    import problem_gen  # noqa: E402
    import verify  # noqa: E402

    issues: List[str] = []
    poem = "白日依山尽"

    # 1) 文本答案的防泄露必须真的生效（负例转正）
    if not problem_gen._hint_leaks(f"提示：答案是{poem}", poem, "text"):
        issues.append("文本答案防泄露失效：提示里明写答案却未被判为泄露")
    if problem_gen._hint_leaks("提示：想想黄河入海的样子", poem, "text"):
        issues.append("文本答案防泄露误报：提示只给方向却被判为泄露")
    if not problem_gen._hint_leaks("提示：答案是 200", "200", "number"):
        issues.append("数值答案防泄露失效：提示里明写答案却未被判为泄露")
    if not problem_gen._hint_leaks("答案是 ＡＰＰＬＥ", "apple", "text"):
        issues.append("文本答案防泄露未做全角/大小写归一：全角大写写法未被识别")

    sanitized = problem_gen._sanitize_hints([f"答案是{poem}"], poem, "text")[0]
    if poem in sanitized:
        issues.append("文本提示净化失效：抹除后答案仍在提示里")

    # 2) 文本 / 选项判分
    text_problem = {"answer": poem, "answer_type": "text", "accept": [], "verify": {}}
    if not verify.check_answer(text_problem, f" {poem} ")["correct"]:
        issues.append("文本判分失效：首尾空白不该影响判定")
    if verify.check_answer(text_problem, poem[:3])["correct"]:
        issues.append("文本判分过宽：答案不完整却被判对")
    if poem in verify.check_answer(text_problem, poem[:3])["diagnosis"]:
        issues.append("文本题错因泄露答案：诊断文本里出现了参考答案")

    spell_problem = {
        "answer": "apple",
        "answer_type": "text",
        "accept": ["apple"],
        "verify": {},
    }
    if not verify.check_answer(spell_problem, "Apple")["correct"]:
        issues.append("拼写判分未忽略大小写：Apple 应判对")
    if verify.check_answer(spell_problem, "apply")["correct"]:
        issues.append("拼写判分过宽：只差一个字母的 apply 被误判为对（不应做编辑距离模糊匹配）")

    enum_problem = {"answer": "B", "answer_type": "enum", "accept": ["B"], "verify": {}}
    if not verify.check_answer(enum_problem, "b")["correct"]:
        issues.append("选项型判分失效：b 与 B 应视为同一项")

    # 3) 非数值答案不能让「提示净化」抛异常（回归：Early 实现直接 raise）
    try:
        problem_gen._sanitize_hints(["随便一条提示"], "苹果", "text")
    except Exception as exc:  # pragma: no cover - 仅在回归时触发
        issues.append(f"文本提示净化抛异常：{type(exc).__name__} {exc}")
    return issues


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
    # 缓存目录同理：<tmp>/data/cache —— 既让示例走「示例数据快照」口径（README 第七节
    # 记录的就是这个口径），又保留 `data\cache` 这个路径后缀，
    # 所以 `本地缓存：0 个文件（目录 ...\data\cache）` 那行仍能按「分段包含」匹配。
    # 目录名带 data\cache 是有意为之，README 里那行示例才对得上。
    #
    # 因为每次都是全新建库 + 全新空缓存目录，断言就与调用者的历史进度、本机是否残留
    # MCP 缓存完全无关 —— 首跑、二跑、跑一百次结果都一样（这是「幂等」的根治法：
    # 不依赖历史状态，也不靠预热）。
    tmpdir = tempfile.mkdtemp(prefix="mmqj-selfcheck-")
    original_cache = None
    try:
        db_dir = Path(tmpdir) / "runtime"
        db_dir.mkdir(parents=True, exist_ok=True)
        cache_dir = Path(tmpdir) / "data" / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        # 本进程内也要隔离：自检自己要算「示例 2/3 的参考答案」，
        # 若这一步读到的是本机 MCP 缓存里的实时数字，算出来的答案和子进程
        # （示例快照口径）不是同一份，断言就会自己打自己。
        sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
        import data_source  # noqa: E402

        original_cache = data_source.CACHE_DIR
        data_source.CACHE_DIR = cache_dir
        data_source.reset_client()
        problem_gen_state_reset()
        outputs = collect_outputs(str(db_dir / "progress.db"), str(cache_dir))
    finally:
        # 恢复本进程的缓存目录：自检不该改调用者的运行环境
        if original_cache is not None:
            try:
                data_source.CACHE_DIR = original_cache
                data_source.reset_client()
                problem_gen_state_reset()
            except Exception:  # pragma: no cover - 恢复失败不影响校验结论
                pass
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

    for issue in check_curriculum_alignment():
        problems.append(
            {"line_no": 0, "expect": "课标对齐（第 4 关除数位数 / 第 5、7、9 关分层 / 分数精确判分 / 知识点图谱字段）", "actual": issue}
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

    bg_issues = check_background_assets()
    for issue in bg_issues:
        problems.append(
            {
                "line_no": 0,
                "expect": "背景位图内联一致（5 张全内联 / 负载与源文件对应 / 零外部请求 / 不覆盖全局变量）",
                "actual": issue,
            }
        )

    subject_issues, subject_summary = check_subjects()
    for issue in subject_issues:
        problems.append(
            {
                "line_no": 0,
                "expect": "学科三方一致（注册表 ↔ 知识点图谱 ↔ 题型模板 / answer_type 已知 / role_focus 可解析）",
                "actual": issue,
            }
        )

    for issue in check_answer_semantics():
        problems.append(
            {
                "line_no": 0,
                "expect": "判分语义（文本防泄露真的生效 / 拼写不做编辑距离容错 / 错因不泄露答案）",
                "actual": issue,
            }
        )

    for issue in check_demo_kp_badge():
        problems.append(
            {
                "line_no": 0,
                "expect": "演示页统计数字不硬编码（徽章「N 个知识点」== 数学图谱实际个数，且只有一处真相源）",
                "actual": issue,
            }
        )

    for issue in check_subject_templates():
        problems.append(
            {
                "line_no": 0,
                "expect": "学科题型行为（选项已洗牌 / 序号作答只认正确位置 / 省略标点仍判对）",
                "actual": issue,
            }
        )

    if not problems:
        total_lines = sum(len(block.splitlines()) for block in blocks)
        print(
            f"DOC_SYNC: OK —— 5 段示例共 {total_lines} 行与当前代码一致；"
            f"周报累计量自洽（{EXPECTED_ATTEMPTS} 题 / 答对 {EXPECTED_CORRECT} / "
            f"{round(EXPECTED_CORRECT / EXPECTED_ATTEMPTS * 100, 1)}%）；"
            f"故事线 {story_count} 条 × {story_levels // max(story_count, 1)} 关校验通过；"
            f"demo 题库 {bank_total} 题与 bank.json / 重导出结果一致；"
            f"演示页知识点徽章与数学图谱一致（{export_kp_badge()}）；"
            f"README 规模数字一致（{story_levels} 个关卡位，{distinct} 条不同题面）；"
            f"学科 {len(subject_summary)} 个（{' / '.join(subject_summary)}）；"
            f"角色 {char_total} 名（介绍 {char_short}~{char_long} 字）/ 场景修饰语 {scene_total} 条；"
            f"彩蛋配置 / pool / 引擎注入一致；"
            f"背景位图 {len(BG_ASSETS)} 张全部内联且与 demo/assets/ 逐字节对应；"
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
