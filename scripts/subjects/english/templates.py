"""麦麦取经录 —— 英语学科的题型模板。

本期交付范围（明确标注，不交半崩代码）
--------------------------------------
* ``spell_fill``（单词拼写）——**可用**，覆盖全部 6 个英语知识点；
* ``word_choice``（词义选择）——**仅占位**，抛 :class:`NotImplementedError` 并标注
  「赛后迭代」。它在 ``TEMPLATES`` 里存在，是为了让扩展点可见；
  **但没有任何知识点指向它**——把「点了就崩」的入口放进知识点图谱，
  等于交付一个坏入口，所以图谱里的 6 个 kp 全部挂在 ``spell_fill`` 上。

返回契约与数学 / 语文模板完全同构，见 :mod:`problem_gen._assemble_problem`。

拼写容错策略（与设计文档「决策 5」一致）
----------------------------------------
**归一化 + 显式同义表，不做编辑距离模糊匹配。**
判定走 ``verify.check_answer`` 的 ``text`` 分支：

* 归一化：NFKC（全角→半角）→ 去首尾空白 → 小写 → 连续空白折叠 → 去首尾标点引号；
* 正确性：归一化后的输入 ∈ {归一化(answer)} ∪ {归一化(accept 的每一项)}。

为什么不做编辑距离：``apple`` 与 ``apply``、``cat`` 与 ``cut`` 距离都是 1。
小学拼写题的**考点就是精确拼写**，容忍一个字母错会把「正确答错」判成对，
比判错更糟——它破坏了这道题的测量功能。变体（英式/美式、单复数）由内容侧
在 ``accept`` 里显式登记，**不做猜测**。
"""

from __future__ import annotations

from typing import Any, Dict, List

# ---------------------------------------------------------------------------
# 知识点显示名（与 data/curriculum/english.json 保持一致）
# ---------------------------------------------------------------------------

KP_NAMES: Dict[str, str] = {
    "e3_spell_fruit": "水果单词拼写",
    "e3_spell_animal": "动物单词拼写",
    "e4_spell_school": "学校用品拼写",
    "e4_spell_food": "食物单词拼写",
    "e5_spell_week": "星期与月份拼写",
    "e6_spell_nature": "自然与天气拼写",
}

# ---------------------------------------------------------------------------
# 词库：zh 是题干里的中文，en 是参考答案，accept 是显式登记的可接受变体
# ---------------------------------------------------------------------------

WORDS: Dict[str, List[Dict[str, Any]]] = {
    "e3_spell_fruit": [
        {"zh": "苹果", "en": "apple", "accept": []},
        {"zh": "香蕉", "en": "banana", "accept": []},
        {"zh": "橙子", "en": "orange", "accept": []},
        {"zh": "葡萄", "en": "grape", "accept": []},
        {"zh": "桃子", "en": "peach", "accept": []},
    ],
    "e3_spell_animal": [
        {"zh": "猫", "en": "cat", "accept": []},
        {"zh": "狗", "en": "dog", "accept": []},
        {"zh": "鸟", "en": "bird", "accept": []},
        {"zh": "老虎", "en": "tiger", "accept": []},
        {"zh": "熊猫", "en": "panda", "accept": []},
    ],
    "e4_spell_school": [
        {"zh": "铅笔", "en": "pencil", "accept": []},
        {"zh": "尺子", "en": "ruler", "accept": []},
        {"zh": "书包", "en": "schoolbag", "accept": ["school bag"]},
        {"zh": "蜡笔", "en": "crayon", "accept": []},
        {"zh": "笔记本", "en": "notebook", "accept": ["note book"]},
    ],
    "e4_spell_food": [
        {"zh": "面包", "en": "bread", "accept": []},
        {"zh": "牛奶", "en": "milk", "accept": []},
        {"zh": "果汁", "en": "juice", "accept": []},
        {"zh": "面条", "en": "noodles", "accept": ["noodle"]},
        {"zh": "糖果", "en": "candy", "accept": ["sweet", "sweets"]},
    ],
    "e5_spell_week": [
        # 刻意混入长词（Wednesday / September / December / Saturday）：
        # 只放 Monday / April 这类 5-6 字母的短词，拼写难度会一直停在低档。
        {"zh": "星期一", "en": "Monday", "accept": ["Mon"]},
        {"zh": "星期二", "en": "Tuesday", "accept": ["Tue"]},
        {"zh": "星期三", "en": "Wednesday", "accept": ["Wed"]},
        {"zh": "星期六", "en": "Saturday", "accept": ["Sat"]},
        {"zh": "四月", "en": "April", "accept": ["Apr"]},
        {"zh": "八月", "en": "August", "accept": ["Aug"]},
        {"zh": "九月", "en": "September", "accept": ["Sep", "Sept"]},
        {"zh": "十二月", "en": "December", "accept": ["Dec"]},
    ],
    "e6_spell_nature": [
        {"zh": "雨", "en": "rain", "accept": ["rainy"]},
        {"zh": "晴朗的", "en": "sunny", "accept": []},
        {"zh": "多云的", "en": "cloudy", "accept": []},
        {"zh": "风", "en": "wind", "accept": ["windy"]},
        {"zh": "雪", "en": "snow", "accept": ["snowy"]},
    ],
}

_HINT_2 = "按读音把字母分成几段，一段一段地拼，再连起来。"
_HINT_3 = "拼完核对一遍：字母顺序有没有颠倒，有没有多写或漏写一个字母。"


def _kp_of(ctx: Dict[str, Any]) -> str:
    """从上下文里取出知识点 id。"""
    level = ctx.get("level") or {}
    return str(level.get("kp_id") or "")


def _kp_label(kp_id: str) -> str:
    """知识点中文名（取不到时退化为 id）。"""
    return KP_NAMES.get(str(kp_id), str(kp_id))


def _verify_stub() -> Dict[str, Any]:
    """文本题没有可复算的算式，但仍要给同构的 ``verify`` 键。"""
    return {"formula": "", "tool": ""}


def spell_fill(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """题型一：单词拼写 —— 看中文与首尾字母提示，拼出英文单词。

    Args:
        ctx: 模板上下文（读 ``level.kp_id`` 与 ``rng``）。

    Returns:
        题目 dict（``answer_type="text"``）。

    Raises:
        KeyError: 该知识点没有配置词条。
    """
    kp_id = _kp_of(ctx)
    pool = WORDS.get(kp_id)
    if not pool:
        raise KeyError(f"英语知识点 {kp_id!r} 没有配置词条，无法出单词拼写题。")

    rng = ctx["rng"]
    item = pool[rng.randrange(len(pool))]
    word = str(item["en"])
    zh = str(item["zh"])
    first_letter = word[0].lower()
    last_letter = word[-1].lower()
    length = len(word)

    return {
        "question": (
            f"「{zh}」的英文单词怎么拼？"
            f"（共 {length} 个字母，首字母是 {first_letter}，末字母是 {last_letter}）"
        ),
        "answer": word,
        "answer_type": "text",
        "accept": [word] + [str(extra) for extra in item.get("accept") or []],
        "hints": [
            f"先读出这个词的发音，再想想每个音对应哪几个字母（一共 {length} 个字母）。",
            _HINT_2,
            _HINT_3,
        ],
        "steps": [
            f"「{zh}」的英文是 {word}。",
            f"逐字母核对：{' - '.join(word)}（共 {length} 个字母）。",
        ],
        "unit": "",
        "role": "小学者",
        "story": f"英语积累 · {_kp_label(kp_id)}：看中文，把这个英文单词完整拼出来。",
        "verify": _verify_stub(),
    }


def word_choice(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """题型二：词义选择 —— **本期未实现，赛后迭代。**

    保留这个函数（而不是删掉）是为了让「加一个题型」的扩展点可见：
    新增题型只需在本文件写一个函数并登记进 ``TEMPLATES``，
    再在 ``data/curriculum/english.json`` 里给某个 kp 写上对应的 ``skill``。

    目前**没有任何知识点指向 ``word_choice``**，所以正常路径不会走到这里；
    真被调用时抛出明确异常而不是返回半成品，避免「悄悄出一道错题」。

    Args:
        ctx: 模板上下文（本函数不使用）。

    Raises:
        NotImplementedError: 永远抛出（本期未实现）。
    """
    kp_id = _kp_of(ctx)
    raise NotImplementedError(
        f"TODO: 赛后迭代 —— 英语词义选择题（skill=word_choice，知识点 {kp_id or '（未指定）'}）尚未实现。"
        "实现步骤：1) 在本文件加 WORDS_CHOICE 题库；2) 实现返回 answer_type='enum' 的模板；"
        "3) 在 data/curriculum/english.json 里把某个 kp 的 skill 改成 word_choice。"
    )


TEMPLATES = {
    "spell_fill": spell_fill,
    "word_choice": word_choice,
}
"""``{skill: callable}`` —— 由 :func:`subjects.load_templates` 按注册表动态导入。

``word_choice`` 是明确标注的 TODO 占位（抛 ``NotImplementedError``），
当前没有任何知识点指向它。
"""


if __name__ == "__main__":  # pragma: no cover - 手工调试入口
    import random

    for kp_id in ("e3_spell_fruit", "e5_spell_week"):
        demo_ctx = {"level": {"kp_id": kp_id}, "rng": random.Random("demo"), "difficulty": 3}
        demo = TEMPLATES["spell_fill"](demo_ctx)
        print(f"[spell_fill] {demo['question']}")
        print(f"  答案：{demo['answer']}　可接受：{demo['accept']}")
        print(f"  提示1：{demo['hints'][0]}")
        print()
    try:
        TEMPLATES["word_choice"]({"level": {"kp_id": "e3_spell_fruit"}})
    except NotImplementedError as exc:
        print("word_choice 占位已就位：", exc)
