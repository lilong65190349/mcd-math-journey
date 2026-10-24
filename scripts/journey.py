"""麦麦取经录 —— 闯关规则引擎（纯逻辑，不依赖网络、数据库与界面）。

这个模块只做四件事，全部是**可以单独断言的纯函数**：

1. 解锁判定：关卡链（线内）与故事线链（线间），见 ``docs/SYSTEM_RULES.md`` 第三节；
2. 一次挑战（run）的随机抽取：场景 / 题数 / 难度档 / 守关大将，第四节；
3. 品阶评定：用时与基准时间的比值 → 金 / 银 / 铜，第六节；
4. 介绍解锁的判定（干净通关），第七节。

设计约束
--------
* **不 import 界面**：``cli.py`` 与 ``demo/index.html`` 都按这里的规则走，规则参数统一放在
  ``data/run_rules.json``（演示页由 ``export_demo_bank.py`` 原样内联），两端判定不会跑偏。
* **不碰数据库**：写库的动作在 ``progress.py``；本模块只接收 ``clear_map``、返回决策。
* **随机可复现**：同一个 seed 必须永远抽出同一组结果，且 **Python 与 JS 抽样结果逐位一致**。
  为此本模块用一个自带的 32 位确定性随机数（``Rng``，mulberry32），种子由 seed 文本经
  FNV-1a 散列得到；``demo/index.html`` 里的 ``jsRng`` 是它的逐行镜像，两边用同一套
  ``randrange`` / ``sample``，因此 ``--seed`` 写死的用例在两端完全等价（自检里有比对）。

为什么不用标准库 :mod:`random`
------------------------------
``random.Random("abc")`` 的种子化算法是 CPython 实现细节（字符串先过 SHA-512 再铺进
Mersenne Twister 状态），用 JS 无法在几十行内逐位复刻。跨端一致性是硬要求，
所以这里换成一个两端都写得清楚的小 PRNG：算法只有五行，谁都能核对。
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

if __package__ in (None, ""):  # pragma: no cover - 脚本运行路径
    sys.path.insert(0, str(Path(__file__).resolve().parent))

PROJECT_ROOT: Path = Path(__file__).resolve().parents[1]
RULES_PATH: Path = PROJECT_ROOT / "data" / "run_rules.json"
STORYLINES_DIR: Path = PROJECT_ROOT / "data" / "storylines"

DEFAULT_STORY: str = "xiyou"
STAGE_COUNT: int = 9

_RULES: Optional[Dict[str, Any]] = None
_INDEX: Optional[Dict[str, Any]] = None
_STORY_CACHE: Dict[str, Dict[str, Any]] = {}

MASK32: int = 0xFFFFFFFF
"""32 位无符号掩码，用来模拟 JS 的 ``| 0`` / ``>>> 0``。"""


# ---------------------------------------------------------------------------
# 规则参数
# ---------------------------------------------------------------------------


def load_rules(force: bool = False) -> Dict[str, Any]:
    """读取 ``data/run_rules.json``（进程内缓存）。

    Args:
        force: 为真时重新读盘（自检改了文件后立刻生效）。

    Returns:
        规则字典；键见 ``docs/SYSTEM_RULES.md`` 第九节 9.1。
    """
    global _RULES
    if _RULES is None or force:
        with RULES_PATH.open("r", encoding="utf-8") as handle:
            _RULES = json.load(handle)
    return _RULES


def _weight_table() -> List[Tuple[int, int]]:
    """把 ``question_count_weights`` 变成 ``[(题数, 权重), …]``（按题数升序）。"""
    weights = load_rules()["question_count_weights"]
    return sorted((int(key), int(value)) for key, value in weights.items())


def base_seconds(grade: int) -> int:
    """某年级的每题基准秒数（年级缺失时按最低年级算，宁可宽松也不惩罚）。"""
    table = load_rules()["base_seconds_by_grade"]
    return int(table.get(str(int(grade)), table[min(table, key=int)]))


def per_question_cap_seconds() -> int:
    """单题计时上限（秒），防止挂机刷黄金。"""
    return int(load_rules()["per_question_cap_seconds"])


def tier_order() -> Dict[str, int]:
    """品阶高低次序，用于「取历史最优」。"""
    return {str(key): int(value) for key, value in load_rules()["tier_order"].items()}


# ---------------------------------------------------------------------------
# 故事线元信息
# ---------------------------------------------------------------------------


def load_index(force: bool = False) -> Dict[str, Any]:
    """读取 ``data/storylines/index.json``。"""
    global _INDEX
    if _INDEX is None or force:
        with (STORYLINES_DIR / "index.json").open("r", encoding="utf-8") as handle:
            _INDEX = json.load(handle)
    return _INDEX


def storyline_order() -> List[str]:
    """按注册表顺序返回**已上线**的故事线 id 列表（顺序即解锁顺序）。

    锁定故事线不进这个列表，所以「上一条线」永远指的是上一条**可用**的线。
    """
    return [
        str(item.get("id"))
        for item in load_index().get("storylines", [])
        if item.get("available")
    ]


def storyline_meta(story: str) -> Dict[str, Any]:
    """某条线的注册表条目（不存在时返回空字典）。"""
    for item in load_index().get("storylines", []):
        if str(item.get("id")) == str(story):
            return dict(item)
    return {}


def load_storyline(story: str) -> Dict[str, Any]:
    """读取某条线的全文（进程内缓存）。"""
    sid = str(story)
    if sid not in _STORY_CACHE:
        path = STORYLINES_DIR / f"{sid}.json"
        with path.open("r", encoding="utf-8") as handle:
            _STORY_CACHE[sid] = json.load(handle)
    return _STORY_CACHE[sid]


def storyline_name(story: str) -> str:
    """某条线的中文名（取不到就退化为 id）。"""
    try:
        return str(load_storyline(story).get("name") or story)
    except (OSError, json.JSONDecodeError):
        return str(story)


def stage_meta(story: str, stage: int) -> Dict[str, Any]:
    """某条线某一关的元信息（关名 / 地点 / 主题 / 年级定位）。"""
    for item in load_storyline(story).get("stages", []):
        if int(item.get("stage", 0)) == int(stage):
            return dict(item)
    raise KeyError(f"故事线「{storyline_name(story)}」没有第 {stage} 关。")


def stage_name(story: str, stage: int) -> str:
    """关名（如「八戒买饼」）。"""
    return str(stage_meta(story, stage).get("name") or f"第 {stage} 关")


def scene_place(story: str, stage: int) -> str:
    """该关的地点（如「江边船坞」），用于拼场景文本。"""
    return str(stage_meta(story, stage).get("scene") or "")


def scene_variants(story: str) -> List[str]:
    """该线的 10 条场景修饰语。"""
    variants = [str(item) for item in load_storyline(story).get("scene_variants") or []]
    if len(variants) != 10:
        raise ValueError(
            f"故事线「{storyline_name(story)}」的 scene_variants 应为 10 条，实际 {len(variants)} 条。"
        )
    return variants


def compose_scene(variant: str, place: str) -> str:
    """把「场景修饰」与「地点」拼成一句通顺的场景文本。

    修饰语一律以「的 / 时 / 日 / 前 / 后」结尾，可直接后面接地名；
    但地点本身已经带「的」时（如「五行山下的路口」）直接连写会变成
    「清晨的五行山下的路口」这种双重定语，此时改用逗号隔开：
    「清晨，五行山下的路口」。两种写法都不以「的」收尾。
    """
    variant = str(variant).strip()
    place = str(place).strip()
    if not place:
        return variant.rstrip("的")
    if "的" in place:
        return f"{variant[:-1] if variant.endswith('的') else variant}，{place}"
    return f"{variant}{place}"


def stage_scenes(story: str, stage: int) -> List[str]:
    """某关的 10 条完整场景文本（顺序与 ``scene_variants`` 一致）。"""
    place = scene_place(story, stage)
    return [compose_scene(variant, place) for variant in scene_variants(story)]


def stage_key(story: str, stage: int) -> str:
    """通关记录里「关」的键。

    与 ``cli._stage_key`` 完全一致：默认线沿用历史上的 ``"3"``（老库的进度照样算数），
    其他线用 ``"sanguo-3"``。故事线之间不会互相覆盖。
    """
    sid = str(story)
    if sid == DEFAULT_STORY:
        return str(int(stage))
    return f"{sid}-{int(stage)}"


# ---------------------------------------------------------------------------
# 解锁判定
# ---------------------------------------------------------------------------


def is_stage_unlocked(story: str, stage: int, clear_map: Dict[str, Dict[str, Any]]) -> bool:
    """关卡是否解锁：第 1 关默认解锁，第 k 关要求第 k-1 关已通关。

    Args:
        story: 故事线 id。
        stage: 关号（1~9）。
        clear_map: ``{stage_key: {"cleared": bool, ...}}``，由 ``progress`` 提供。
            只有 ``cleared`` 为真的记录才算通关——「有挑战记录但没通关」不解锁下一关。

    Returns:
        ``True`` 表示可以进入。
    """
    number = int(stage)
    if number <= 1:
        return True
    if number > STAGE_COUNT:
        return False
    record = clear_map.get(stage_key(story, number - 1)) or {}
    return bool(record.get("cleared"))


def cleared_stage_count(story: str, clear_map: Dict[str, Dict[str, Any]]) -> int:
    """某条线已通关的关卡数（只数 1~9 关内、且 cleared 为真的）。"""
    return sum(
        1
        for number in range(1, STAGE_COUNT + 1)
        if (clear_map.get(stage_key(story, number)) or {}).get("cleared")
    )


def is_story_unlocked(story: str, clear_map: Dict[str, Dict[str, Any]]) -> bool:
    """故事线是否解锁：第 1 条线默认可用，第 n 条线要求第 n-1 条线 9 关全通。"""
    order = storyline_order()
    sid = str(story)
    if sid not in order:
        return False
    index = order.index(sid)
    if index == 0:
        return True
    previous = order[index - 1]
    return cleared_stage_count(previous, clear_map) >= STAGE_COUNT


def story_progress(story: str, clear_map: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """某条线的解锁与进度摘要（CLI 的 ``stories`` 与演示页共用同一口径）。"""
    order = storyline_order()
    sid = str(story)
    index = order.index(sid) if sid in order else -1
    previous = order[index - 1] if index > 0 else ""
    return {
        "story": sid,
        "name": storyline_name(sid),
        "order": index + 1,
        "unlocked": is_story_unlocked(sid, clear_map),
        "cleared": cleared_stage_count(sid, clear_map),
        "total": STAGE_COUNT,
        "previous": previous,
        "previous_name": storyline_name(previous) if previous else "",
    }


# ---------------------------------------------------------------------------
# 确定性随机（Python ↔ JS 逐位一致）
# ---------------------------------------------------------------------------


def hash_seed(text: str) -> int:
    """FNV-1a 32 位散列（按 Unicode 码点逐个混入）。

    JS 侧用 ``for (const ch of text) ch.codePointAt(0)`` 取码点，与 Python 的 ``ord``
    在 BMP 与增补平面上都一致，因此同一个 seed 文本两端得到同一个整数种子。
    """
    value = 2166136261
    for char in str(text):
        value ^= ord(char) & MASK32
        value = (value * 16777619) & MASK32
    return value


class Rng:
    """mulberry32：32 位状态的确定性伪随机数。

    与 ``demo/index.html`` 的 ``jsRng`` 逐行对应（加法、乘法、异或都在 32 位内回绕），
    两边喂同一个整数种子，产出的序列完全相同。
    """

    __slots__ = ("_state",)

    def __init__(self, seed: Any) -> None:
        self._state = seed if isinstance(seed, int) else hash_seed(str(seed))
        self._state &= MASK32

    def next_float(self) -> float:
        """``[0, 1)`` 的浮点数（分母固定为 2**32，保证两端浮点结果一致）。"""
        self._state = (self._state + 0x6D2B79F5) & MASK32
        t = self._state
        t = ((t ^ (t >> 15)) * (t | 1)) & MASK32
        inner = ((t ^ (t >> 7)) * (t | 61)) & MASK32
        t = (((t + inner) & MASK32) ^ t) & MASK32
        t = (t ^ (t >> 14)) & MASK32
        return t / 4294967296.0

    def randrange(self, count: int) -> int:
        """``[0, count)`` 的整数（等价于 ``Math.floor(next_float() * count)``）。"""
        if count <= 0:
            raise ValueError("randrange 需要一个正整数上界。")
        index = int(self.next_float() * count)
        return count - 1 if index >= count else index

    def choice(self, items: Sequence[Any]) -> Any:
        """从序列里均匀取一个。"""
        if not items:
            raise ValueError("不能在空序列上取随机值。")
        return items[self.randrange(len(items))]

    def sample(self, items: Sequence[Any], count: int) -> List[Any]:
        """无放回抽 ``count`` 个（部分 Fisher-Yates，两端实现一致）。"""
        pool = list(items)
        if count > len(pool):
            raise ValueError("抽样数量超过池子大小。")
        for index in range(count):
            swap = index + self.randrange(len(pool) - index)
            pool[index], pool[swap] = pool[swap], pool[index]
        return pool[:count]

    def weighted(self, table: Sequence[Tuple[Any, int]]) -> Any:
        """按权重表抽一个键（权重为整数，累加后落点）。"""
        total = sum(int(weight) for _, weight in table)
        if total <= 0:
            raise ValueError("权重总和必须为正。")
        point = self.randrange(total)
        cursor = 0
        for key, weight in table:
            cursor += int(weight)
            if point < cursor:
                return key
        return table[-1][0]  # pragma: no cover - 数学上到不了


def derive(seed: str, stream: str) -> Rng:
    """由 run 的 seed 派生一路独立子随机源（四路互不干扰，见文档 8.4）。"""
    return Rng(f"{seed}#{stream}")


# ---------------------------------------------------------------------------
# 一次挑战的抽取
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RunPlan:
    """一次挑战（run）在开始前就固定下来的全部随机结果。"""

    story: str
    stage: int
    seed: str
    scene_index: int
    scene_variant: str
    scene_text: str
    guard_id: str
    question_count: int
    difficulties: Tuple[int, ...]

    def level_ids(self) -> List[str]:
        """本次抽到的题目对应的 ``level_id``（西游是 ``3-2``，其他线是 ``sanguo-3-2``）。"""
        from problem_gen import level_id_for  # 延迟导入：避免与出题模块互相牵扯

        return [level_id_for(self.story, self.stage, number) for number in self.difficulties]

    def as_dict(self) -> Dict[str, Any]:
        """给演示页 / 自检用的纯数据视图。"""
        return {
            "story": self.story,
            "stage": self.stage,
            "seed": self.seed,
            "scene_index": self.scene_index,
            "scene_variant": self.scene_variant,
            "scene_text": self.scene_text,
            "guard_id": self.guard_id,
            "question_count": self.question_count,
            "difficulties": list(self.difficulties),
        }


def draw_run(
    story: str,
    stage: int,
    seed: str,
    available_pool: Optional[Sequence[str]] = None,
) -> RunPlan:
    """抽出一次挑战的全部随机结果。

    抽取维度与文档第四节一致：场景（10 选 1）、题数（30/40/30）、难度档（9 选 k、不重复、
    升序）、守关大将（守关池均匀随机）。四路各自派生独立子种子，互不影响。

    Args:
        story: 故事线 id。
        stage: 关号 1~9。
        seed: 本次 run 的种子（会原样记进 ``stage_runs.seed``，方便重放）。
        available_pool: 守关大将候选 id 列表；``None`` 时取该线守关池。

    Returns:
        :class:`RunPlan`。
    """
    sid = str(story)
    number = int(stage)
    text = str(seed)
    if available_pool is None:
        import characters  # 延迟导入，避免循环依赖

        available_pool = characters.guard_pool_ids(sid)
    pool = [str(item) for item in available_pool]
    if not pool:
        raise ValueError(f"故事线「{storyline_name(sid)}」没有守关大将候选。")

    variants = scene_variants(sid)
    scene_index = derive(text, "scene").randrange(len(variants))

    question_count = int(derive(text, "count").weighted(_weight_table()))

    pool_diff = list(range(1, int(load_rules().get("difficulty_count", 9)) + 1))
    difficulties = sorted(derive(text, "diff").sample(pool_diff, question_count))

    guard_id = derive(text, "guard").choice(pool)

    return RunPlan(
        story=sid,
        stage=number,
        seed=text,
        scene_index=scene_index,
        scene_variant=variants[scene_index],
        scene_text=compose_scene(variants[scene_index], scene_place(sid, number)),
        guard_id=guard_id,
        question_count=question_count,
        difficulties=tuple(difficulties),
    )


def make_seed(story: str, stage: int, now_ms: Optional[int] = None, suffix: str = "") -> str:
    """按文档 4.2 的格式生成一个 run 种子：``<story>-<stage>-<毫秒时间戳>-<随机后缀>``。"""
    import time

    stamp = int(now_ms if now_ms is not None else time.time() * 1000)
    tail = suffix or f"{Rng(f'{story}-{stage}-{stamp}').randrange(10000):04d}"
    return f"{story}-{stage}-{stamp}-{tail}"


# ---------------------------------------------------------------------------
# 计时与品阶
# ---------------------------------------------------------------------------


def budget_ms(grades: Iterable[int]) -> int:
    """一次 run 的基准用时（毫秒）= 各题所属年级基准秒数之和 × 1000。"""
    total = sum(base_seconds(int(grade)) for grade in grades)
    return int(total) * 1000


def ratio_of(elapsed_ms: int, budget: int) -> float:
    """用时与基准的比值（基准为 0 时不判品阶，返回 1.0 兜底）。"""
    if budget <= 0:
        return 1.0
    return float(elapsed_ms) / float(budget)


def _cap(tier: str, cap: str) -> str:
    """把品阶压到不超过 ``cap``（只降不升）。"""
    order = tier_order()
    if tier not in order or cap not in order:
        return tier
    return tier if order[tier] <= order[cap] else cap


def grade_tier(
    elapsed_ms: int,
    budget: int,
    used_hint: bool = False,
    used_skip: bool = False,
    retry_count: int = 0,
) -> Optional[str]:
    """按用时与修正项评定品阶。

    * ``used_skip`` → 不给品阶（返回 ``None``），也不收集角色；
    * 基准比值 ``≤0.60`` 黄金、``≤1.00`` 白银、其余青铜；
    * 用过提示 → 上限白银；答错 3 次及以上 → 上限白银（1~2 次不降品阶）。

    Args:
        elapsed_ms: run 用时（已按单题上限截断）。
        budget: 基准用时（见 :func:`budget_ms`）。
        used_hint: 是否用过提示。
        used_skip: 是否跳过过某一题。
        retry_count: 答错的总次数。

    Returns:
        ``"gold"`` / ``"silver"`` / ``"bronze"`` / ``None``。
    """
    if used_skip:
        return None
    thresholds = load_rules()["tier_thresholds"]
    ratio = ratio_of(elapsed_ms, budget)
    if ratio <= float(thresholds["gold"]):
        tier = "gold"
    elif ratio <= float(thresholds["silver"]):
        tier = "silver"
    else:
        tier = "bronze"
    if used_hint:
        tier = _cap(tier, str(load_rules()["hint_caps_tier"]))
    if int(retry_count) >= int(load_rules()["retry_silver_threshold"]):
        tier = _cap(tier, "silver")
    return tier


TIER_LABELS: Dict[str, str] = {"gold": "🥇 黄金", "silver": "🥈 白银", "bronze": "🥉 青铜"}
TIER_MARKS: Dict[str, str] = {"gold": "◆◆◆", "silver": "◆◆◇", "bronze": "◆◇◇"}


def tier_label(tier: Optional[str]) -> str:
    """品阶的中文展示名（``None`` 表示「不计品阶」）。"""
    if not tier:
        return "不计品阶"
    return TIER_LABELS.get(str(tier), str(tier))


def best_tier(current: Optional[str], candidate: Optional[str]) -> Optional[str]:
    """两个品阶里取更高的那个（``None`` 视为最低，不会顶掉已有记录）。"""
    order = tier_order()
    if not candidate:
        return current
    if not current:
        return candidate
    return candidate if order.get(str(candidate), 0) > order.get(str(current), 0) else current


def clean_run(used_hint: bool, retry_count: int, used_skip: bool = False) -> bool:
    """是否「干净通关」：没过提示、没答错过、没跳过。这是介绍解锁的唯一条件。"""
    return not used_hint and int(retry_count) == 0 and not used_skip


# ---------------------------------------------------------------------------
# 收集
# ---------------------------------------------------------------------------


def run_characters(plan: RunPlan) -> List[str]:
    """本次 run 出场、通关后可收集的角色 id：闯关大将 + 本场守关大将。"""
    import characters

    return [characters.stage_boss_id(plan.story, plan.stage), str(plan.guard_id)]


def collect(
    story: str,
    char_ids: Sequence[str],
    tier: Optional[str],
    clean: bool,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """把本次出场的角色写进角色库（跳过 / 无品阶时不收集）。

    真正落库在 :func:`progress.upsert_collection`；这里负责「什么时候该收」的规则。

    Args:
        story: 故事线 id。
        char_ids: 本次出场的角色 id。
        tier: 本次品阶；``None``（跳过）表示不收集。
        clean: 是否干净通关（决定介绍能否解锁）。
        db_path: 自定义数据库路径（测试用）。

    Returns:
        本次**新收集到**的角色条目列表（用来打印「角色卡 +N」）。
    """
    if tier is None:
        return []
    import progress

    return progress.upsert_collection(
        story=str(story),
        char_ids=[str(item) for item in char_ids],
        tier=str(tier),
        clean=bool(clean),
        db_path=db_path,
    )


if __name__ == "__main__":  # pragma: no cover - 手工调试入口
    for line in storyline_order():
        print(line, storyline_name(line), len(stage_scenes(line, 1)))
    plan = draw_run("xiyou", 3, "demo-1")
    print(plan)
