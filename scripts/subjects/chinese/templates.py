"""麦麦取经录 —— 语文学科的题型模板。

设计约束（与数学模板同构，缺任何一个键都会在上层崩）
----------------------------------------------------
每个模板函数接收 ``ctx``（:func:`problem_gen._ctx` 的产物），返回一个 dict：

``question`` / ``answer`` / ``answer_type`` / ``hints`` / ``steps`` /
``unit`` / ``role`` / ``story`` / ``verify`` / ``accept``

其中：

* ``answer_type`` 只能是 ``"text"`` 或 ``"enum"``（语文不产生数值答案）；
* ``hints`` 是三级递进提示，**任何一级都不得包含答案本身**——
  这条由 ``problem_gen._hint_leaks`` 在出题时逐条检查（文本答案走归一化后的
  子串包含判断）。所以本文件里的提示文案刻意只给方向、不给结论；
* ``accept`` 是显式同义表，判分时与 ``answer`` 一起归一化后做集合命中，
  **不做编辑距离模糊匹配**（容忍一字之差会把「正确答错」判成对）。

答案长度约束
------------
所有答案都刻意做到 **≥ 2 个字符**。原因是防泄露检查对单字答案无法可靠判定
（子串包含会把任何含该字的正常提示都判成泄露），而放弃这项检查等于放弃闸门。
所以本学科的内容侧主动避开单字答案——**这是内容纪律，不是代码兜底**。

选文来源
--------
全部为公有领域古典诗文（唐宋名家，逝世逾千年），只考名句补全，不考作者生平，
不涉及宗教与暴力表述。
"""

from __future__ import annotations

from typing import Any, Dict, List

# ---------------------------------------------------------------------------
# 知识点显示名（题目与讲解里用；与 data/curriculum/chinese.json 保持一致）
# ---------------------------------------------------------------------------

KP_NAMES: Dict[str, str] = {
    "c3_poem_spring": "春天的名句",
    "c3_poem_number": "数字入诗",
    "c3_word_homophone": "同音字辨析",
    "c3_word_measure": "量词搭配",
    "c4_poem_scenery": "写景名句",
    "c4_word_synonym": "近义词辨析",
    "c5_poem_homesick": "思乡名句",
    "c5_word_idiom": "成语辨析",
    "c6_poem_philosophy": "哲理名句",
    "c6_word_antonym": "反义词辨析",
}

# ---------------------------------------------------------------------------
# poem_fill 题库：每首给「上半」与「下半」，题面问下半
# ---------------------------------------------------------------------------

POEMS: Dict[str, List[Dict[str, str]]] = {
    "c3_poem_spring": [
        {
            "title": "咏柳",
            "author": "贺知章",
            "first": "碧玉妆成一树高，万条垂下绿丝绦。",
            "second": "不知细叶谁裁出，二月春风似剪刀。",
            "tip": "这首诗把柳树的枝条比作绿色的丝带，后半在问「这么细的叶子是谁剪出来的」。",
        },
        {
            "title": "春晓",
            "author": "孟浩然",
            "first": "春眠不觉晓，处处闻啼鸟。",
            "second": "夜来风雨声，花落知多少。",
            "tip": "诗人是被鸟叫声唤醒的，后半接着写夜里听到的动静。",
        },
        {
            "title": "村居",
            "author": "高鼎",
            "first": "草长莺飞二月天，拂堤杨柳醉春烟。",
            "second": "儿童散学归来早，忙趁东风放纸鸢。",
            "tip": "前半写草长莺飞的早春，后半写孩子们放学后做的一件事。",
        },
        {
            "title": "绝句",
            "author": "杜甫",
            "first": "迟日江山丽，春风花草香。",
            "second": "泥融飞燕子，沙暖睡鸳鸯。",
            "tip": "前半写风和日暖、花草飘香，后半写两种动物的动作。",
        },
    ],
    "c3_poem_number": [
        {
            "title": "山村咏怀",
            "author": "邵雍",
            "first": "一去二三里，烟村四五家。",
            "second": "亭台六七座，八九十枝花。",
            "tip": "这首诗最特别的地方是每一句都嵌着数字，后半的数字接着前半往后数。",
        },
        {
            "title": "望庐山瀑布",
            "author": "李白",
            "first": "日照香炉生紫烟，遥看瀑布挂前川。",
            "second": "飞流直下三千尺，疑是银河落九天。",
            "tip": "前半写远看瀑布的样子，后半用夸张的数字写水流的气势。",
        },
        {
            "title": "绝句",
            "author": "杜甫",
            "first": "两个黄鹂鸣翠柳，一行白鹭上青天。",
            "second": "窗含西岭千秋雪，门泊东吴万里船。",
            "tip": "前半用数字写鸟的数量，后半用数字写时间之久与路途之远。",
        },
        {
            "title": "早发白帝城",
            "author": "李白",
            "first": "朝辞白帝彩云间，千里江陵一日还。",
            "second": "两岸猿声啼不住，轻舟已过万重山。",
            "tip": "前半写出发与一天的路程，后半写船行得快、山一座连着一座。",
        },
    ],
    "c4_poem_scenery": [
        {
            "title": "望天门山",
            "author": "李白",
            "first": "天门中断楚江开，碧水东流至此回。",
            "second": "两岸青山相对出，孤帆一片日边来。",
            "tip": "前半写江水把山门冲开，后半写船从远处驶来的样子。",
        },
        {
            "title": "饮湖上初晴后雨",
            "author": "苏轼",
            "first": "水光潋滟晴方好，山色空蒙雨亦奇。",
            "second": "欲把西湖比西子，淡妆浓抹总相宜。",
            "tip": "前半分别写晴天与雨天的西湖，后半把西湖比作一位古代美人。",
        },
        {
            "title": "题西林壁",
            "author": "苏轼",
            "first": "横看成岭侧成峰，远近高低各不同。",
            "second": "不识庐山真面目，只缘身在此山中。",
            "tip": "前半写从不同角度看山各不相同，后半在说为什么看不清全貌。",
        },
        {
            "title": "山行",
            "author": "杜牧",
            "first": "远上寒山石径斜，白云生处有人家。",
            "second": "停车坐爱枫林晚，霜叶红于二月花。",
            "tip": "前半写山路与白云深处的人家，后半写诗人为什么停下车。",
        },
    ],
    "c5_poem_homesick": [
        {
            "title": "静夜思",
            "author": "李白",
            "first": "床前明月光，疑是地上霜。",
            "second": "举头望明月，低头思故乡。",
            "tip": "前半写月光像霜，后半由抬头看到的月亮转到心里的念头。",
        },
        {
            "title": "九月九日忆山东兄弟",
            "author": "王维",
            "first": "独在异乡为异客，每逢佳节倍思亲。",
            "second": "遥知兄弟登高处，遍插茱萸少一人。",
            "tip": "前半写节日里格外想家，后半写想到兄弟们在做什么、少了谁。",
        },
        {
            "title": "泊船瓜洲",
            "author": "王安石",
            "first": "京口瓜洲一水间，钟山只隔数重山。",
            "second": "春风又绿江南岸，明月何时照我还。",
            "tip": "前半写离家并不算远，后半借春风与月亮问归期。",
        },
        {
            "title": "枫桥夜泊",
            "author": "张继",
            "first": "月落乌啼霜满天，江枫渔火对愁眠。",
            "second": "姑苏城外寒山寺，夜半钟声到客船。",
            "tip": "前半写夜里看到的景象，后半写半夜听到的钟声传到了船上。",
        },
    ],
    "c6_poem_philosophy": [
        {
            "title": "登鹳雀楼",
            "author": "王之涣",
            "first": "白日依山尽，黄河入海流。",
            "second": "欲穷千里目，更上一层楼。",
            "tip": "前半写眼前的山与河，后半讲一个「想看得更远」的道理。",
        },
        {
            "title": "观书有感",
            "author": "朱熹",
            "first": "半亩方塘一鉴开，天光云影共徘徊。",
            "second": "问渠那得清如许，为有源头活水来。",
            "tip": "前半写池塘像一面镜子，后半自问自答：水为什么这样清。",
        },
        {
            "title": "游山西村",
            "author": "陆游",
            "first": "莫笑农家腊酒浑，丰年留客足鸡豚。",
            "second": "山重水复疑无路，柳暗花明又一村。",
            "tip": "前半写农家待客的热情，后半讲走到看似没有路时又见到村子的道理。",
        },
        {
            "title": "冬夜读书示子聿",
            "author": "陆游",
            "first": "古人学问无遗力，少壮工夫老始成。",
            "second": "纸上得来终觉浅，绝知此事要躬行。",
            "tip": "前半讲做学问要下苦功，后半讲光从书本上得来还不够。",
        },
    ],
}

# ---------------------------------------------------------------------------
# word_order 题库：选词填空（答案唯一，全部 ≥ 2 个字符）
# ---------------------------------------------------------------------------

WORDS: Dict[str, List[Dict[str, Any]]] = {
    "c3_word_homophone": [
        {
            "stem": "公园里，孩子们在草地上（　）地奔跑。",
            "options": ["尽情", "尽兴", "进情"],
            "answer": "尽情",
            "tip": "这里要说的是放得开、不受到拘束，重点在「不克制」。",
            "explain": "「尽情」表示不受拘束地放开；「尽兴」强调兴致得到了满足；「进情」不是词。",
        },
        {
            "stem": "这件事跟他一点关系也没有，他是（　）的。",
            "options": ["无辜", "无故", "无姑"],
            "answer": "无辜",
            "tip": "要选表示「没有过错」的那一个。",
            "explain": "「无辜」是没有罪错；「无故」是没有原因；「无姑」不是词。",
        },
        {
            "stem": "老师把作业（　）了一遍，才发下来。",
            "options": ["检查", "检察", "捡查"],
            "answer": "检查",
            "tip": "要选表示「查看有没有错」的那一个。",
            "explain": "「检查」是查看有无问题；「检察」是司法机关的专门用词；「捡查」不是词。",
        },
        {
            "stem": "山上的空气很（　），吸一口就让人觉得清爽。",
            "options": ["清新", "清心", "轻新"],
            "answer": "清新",
            "tip": "要选专门形容空气干净好闻的那一个。",
            "explain": "「清新」形容空气新鲜清爽；「清心」指使心境清净；「轻新」不是词。",
        },
    ],
    "c3_word_measure": [
        {
            "stem": "草原上，一（　）骏马跑了过去。下面哪一项搭配规范？",
            "options": ["一匹马", "一只马", "一头马"],
            "answer": "一匹马",
            "tip": "马有专门的量词，要选规范搭配的那一项。",
            "explain": "马用「匹」计量，这是固定搭配；「只」「头」都不用于马。",
        },
        {
            "stem": "桌上放着一（　）书。下面哪一项搭配规范？",
            "options": ["一本书", "一只书", "一头书"],
            "answer": "一本书",
            "tip": "书本有专门的量词，要选规范搭配的那一项。",
            "explain": "书用「本」计量；「只」「头」都不用于书。",
        },
        {
            "stem": "院子里种着一（　）大树。下面哪一项搭配规范？",
            "options": ["一棵大树", "一只大树", "一头大树"],
            "answer": "一棵大树",
            "tip": "树木有专门的量词，要选规范搭配的那一项。",
            "explain": "树用「棵」计量；「只」「头」都不用于树。",
        },
        {
            "stem": "他买了一（　）香蕉。下面哪一项搭配规范？",
            "options": ["一串香蕉", "一只香蕉", "一头香蕉"],
            "answer": "一串香蕉",
            "tip": "成串的水果要用表示「连在一起」的量词。",
            "explain": "成串的东西用「串」；「只」和「头」都不用于成串的香蕉。",
        },
    ],
    "c4_word_synonym": [
        {
            "stem": "秋天的天空（　）得让人想多看一眼。",
            "options": ["明朗", "明亮", "明白"],
            "answer": "明朗",
            "tip": "要选专门形容天空清朗开阔的那一个。",
            "explain": "「明朗」多形容天空、光线清朗；「明亮」形容光亮；「明白」指懂得道理。",
        },
        {
            "stem": "他做事一向（　），从不拖泥带水。",
            "options": ["干脆", "干净", "干旱"],
            "answer": "干脆",
            "tip": "要选形容说话做事利落、不拖延的那一个。",
            "explain": "「干脆」形容直截了当；「干净」形容没有脏东西；「干旱」形容缺少雨水。",
        },
        {
            "stem": "洪水过后，村民们（　）重建家园。",
            "options": ["齐心协力", "齐头并进", "并驾齐驱"],
            "answer": "齐心协力",
            "tip": "要选表示「大家一起用力」的那一个。",
            "explain": "「齐心协力」指认识一致、共同努力；「齐头并进」指同时进行；「并驾齐驱」指不分先后。",
        },
        {
            "stem": "这道题他（　）了很久，终于想通了。",
            "options": ["琢磨", "捉摸", "啄磨"],
            "answer": "琢磨",
            "tip": "要选表示反复想、慢慢推敲的那一个。",
            "explain": "「琢磨」指反复思考推敲；「捉摸」多用于「难以捉摸」；「啄磨」不是词。",
        },
    ],
    "c5_word_idiom": [
        {
            "stem": "他画的这幅画（　），一看就是下了功夫的。",
            "options": ["栩栩如生", "欣欣向荣", "生生不息"],
            "answer": "栩栩如生",
            "tip": "要选形容画得像真的一样的那个成语。",
            "explain": "「栩栩如生」形容形象逼真；「欣欣向荣」形容事业兴旺；「生生不息」指不断生长繁衍。",
        },
        {
            "stem": "遇到难题，他总能（　），想出好办法。",
            "options": ["举一反三", "一鼓作气", "一帆风顺"],
            "answer": "举一反三",
            "tip": "要选表示「由一个例子想到很多个」的那个成语。",
            "explain": "「举一反三」指从一件事类推许多事；「一鼓作气」指趁劲头大时一口气做完；「一帆风顺」指顺利。",
        },
        {
            "stem": "读书要（　），不能只图快。",
            "options": ["循序渐进", "络绎不绝", "川流不息"],
            "answer": "循序渐进",
            "tip": "要选表示「按步骤慢慢来」的那个成语。",
            "explain": "「循序渐进」指按一定次序逐步深入；「络绎不绝」「川流不息」都形容来往不断。",
        },
        {
            "stem": "比赛前，队员们（　），都想拿第一。",
            "options": ["摩拳擦掌", "张灯结彩", "车水马龙"],
            "answer": "摩拳擦掌",
            "tip": "要选形容准备出力、跃跃欲试的那个成语。",
            "explain": "「摩拳擦掌」形容跃跃欲试；「张灯结彩」形容节日景象；「车水马龙」形容热闹。",
        },
    ],
    "c6_word_antonym": [
        {
            "stem": "「节约」的反义词是（　）。",
            "options": ["浪费", "节省", "省略"],
            "answer": "浪费",
            "tip": "要选表示「不加节制地用掉」的那一个。",
            "explain": "「浪费」与「节约」意义相反；「节省」是近义词；「省略」指省去不说。",
        },
        {
            "stem": "「崎岖」的反义词是（　）。",
            "options": ["平坦", "平常", "平稳"],
            "answer": "平坦",
            "tip": "要选专门形容路面没有高低起伏的那一个。",
            "explain": "「平坦」与「崎岖」意义相反；「平常」指普通；「平稳」指没有波动。",
        },
        {
            "stem": "「虚心」的反义词是（　）。",
            "options": ["自满", "自信", "自立"],
            "answer": "自满",
            "tip": "要选表示「自以为已经足够好」的那一个。",
            "explain": "「自满」与「虚心」意义相反；「自信」「自立」都不是它的反义词。",
        },
        {
            "stem": "「模糊」的反义词是（　）。",
            "options": ["清楚", "清新", "清淡"],
            "answer": "清楚",
            "tip": "要选表示「看得明白、分得清」的那一个。",
            "explain": "「清楚」与「模糊」意义相反；「清新」形容空气；「清淡」形容味道。",
        },
    ],
}

# ---------------------------------------------------------------------------
# 通用提示 2 / 3（按知识点区分，避免「万金油提示」）
# ---------------------------------------------------------------------------

_HINT2: Dict[str, str] = {
    "c3_poem_spring": "先想想诗里写的是哪个季节、哪几种景物。",
    "c3_poem_number": "注意这一首里的数字是怎么往后接的。",
    "c4_poem_scenery": "先把前半句的画面在脑子里描一遍，再想后半句接着写什么。",
    "c5_poem_homesick": "想一想诗人此刻在哪里、心里在惦记谁。",
    "c6_poem_philosophy": "这首后半句往往在讲一个道理，先想道理，再想字句。",
    "c3_word_homophone": "先把每个选项代进句子里读一遍，看哪个顺口。",
    "c3_word_measure": "想想这个东西平时是用什么量词来数的。",
    "c4_word_synonym": "先把每个选项代进句子里读一遍，看哪个最贴合语境。",
    "c5_word_idiom": "先弄清每个成语的意思，再看它和句子的对象合不合。",
    "c6_word_antonym": "先弄清这个词的意思，再想与它方向相反的那个词。",
}

_HINT3: Dict[str, str] = {
    "c3_poem_spring": "默写时注意字数要和上句相配，别写错形近字。",
    "c3_poem_number": "数字写错一个整句就不对了，逐字核对一遍。",
    "c4_poem_scenery": "注意下句要押韵，字数也要和上句一致。",
    "c5_poem_homesick": "这一句常常押「an」或「ang」的韵，顺着韵脚想一想。",
    "c6_poem_philosophy": "先默出前半句，再顺着意思接后半句。",
    "c3_word_homophone": "读音相同、字形不同，要看清偏旁表示什么意思。",
    "c3_word_measure": "量词和事物的形状、数量有关系，别只看读音。",
    "c4_word_synonym": "近义词之间常常差在「适用范围」和「程度」上。",
    "c5_word_idiom": "成语有固定的搭配对象，用错了会闹笑话。",
    "c6_word_antonym": "反义词要词性相同、意思相反，近义词不算。",
}


def _kp_of(ctx: Dict[str, Any]) -> str:
    """从上下文里取出知识点 id。"""
    level = ctx.get("level") or {}
    return str(level.get("kp_id") or "")


def _kp_label(kp_id: str) -> str:
    """知识点中文名（取不到时退化为 id）。"""
    return KP_NAMES.get(str(kp_id), str(kp_id))


_CN_ORDINAL: Dict[int, str] = {
    1: "一",
    2: "二",
    3: "三",
    4: "四",
    5: "五",
    6: "六",
    7: "七",
    8: "八",
    9: "九",
    10: "十",
}
"""阿拉伯序号 → 中文序号。

小学生回答「选第几个」时，写 ``3`` 和写 ``三`` 都很常见，两种都必须判对——
题面本身就是用序号编号呈现选项的，没理由只认阿拉伯数字。
"""


def _ordinal_forms(position: int) -> List[str]:
    """某个选项序号的全部可接受写法。

    Args:
        position: 1 基序号。

    Returns:
        ``["3", "三"]`` 这样的写法列表；超出映射表时只给阿拉伯数字。
    """
    forms = [str(int(position))]
    cn = _CN_ORDINAL.get(int(position))
    if cn and cn not in forms:
        forms.append(cn)
    return forms


def _verify_stub() -> Dict[str, Any]:
    """非数值题没有可复算的算式，但仍要给同构的 ``verify`` 键。

    上层 ``explain()`` 只在 ``verify.formula`` 非空时才打印「校验算式」一行，
    所以这里给空串既保持了字段同构，也不会在讲解里多出一行空话。
    """
    return {"formula": "", "tool": ""}


def poem_fill(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """题型一：古诗补全 —— 给出上半句，默写下半句。

    Args:
        ctx: 模板上下文（读 ``level.kp_id`` 与 ``rng``）。

    Returns:
        题目 dict（``answer_type="text"``）。

    Raises:
        KeyError: 该知识点没有配置诗文。
    """
    kp_id = _kp_of(ctx)
    pool = POEMS.get(kp_id)
    if not pool:
        raise KeyError(f"语文知识点 {kp_id!r} 没有配置诗文，无法出古诗补全题。")

    rng = ctx["rng"]
    poem = pool[rng.randrange(len(pool))]
    first = poem["first"]
    second = poem["second"]
    title = poem["title"]
    author = poem["author"]

    return {
        "question": f"《{title}》里，「{first}」的下一句是什么？（照原文默写，标点与空格可以省略）",
        "answer": second,
        "answer_type": "text",
        # 题干承诺「标点与空格可以省略」，判分就必须真的做到：
        # normalize_text 只 strip **首尾**标点，中间的全角逗号会保留，
        # 题干承诺「标点与空格可以省略」，判分就必须真的做到——但**不在这一层做**。
        # 那件事由 problem_gen.canonical_text 统一负责：比对前把全部标点与空白一起抹掉。
        # 早期这里是「往 accept 里塞三种写法」的权宜之计，结果漏掉了
        # 「保留标点 + 后面多打一个空格」（``"早， 忙"``）这种写法——
        # 因为标点与空格是两类 token，互相映射永远拼不出全部组合。
        # 现在归一化一条规则到底，accept 只需要原句本身。
        "accept": [second],
        "hints": [
            poem["tip"],
            _HINT2.get(kp_id, "先回忆一下这首诗写的是什么。"),
            _HINT3.get(kp_id, "逐字默写，别写错形近字。"),
        ],
        "steps": [
            f"《{title}》是{author}的作品。",
            f"上半句是「{first}」，照原文接下来是「{second}」。",
        ],
        "unit": "",
        "role": "小学者",
        "story": f"语文积累 · {_kp_label(kp_id)}：先把《{title}》读一遍，再把缺的那一句默出来。",
        "verify": _verify_stub(),
    }


def word_order(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """题型二：字词辨析 —— 在选项里选出与语境最贴合的一项。

    **选项必须洗牌**：语料里的 ``options`` 是按「正确答案打头」写的（便于人工核对），
    若原样输出，答案会 100% 固定在第 1 个位置，学生不读题、永远填第一个就全对，
    这道题就失去测量功能了（QA 实测 20/20 命中位置 0）。
    洗牌用 ``ctx["rng"]``，与出题种子同源，所以「同一个 seed 出同一道题」依然成立。

    **序号作答**：题面用「1. / 2. / 3.」编号呈现选项，等于暗示可以用序号回答。
    因此 ``accept`` 里要登记**正确答案洗牌后的那个序号**——只登记它一个，
    不能把所有序号都登记进去（那样写 1、2、3 都算对，比不洗牌更糟）。

    Args:
        ctx: 模板上下文（读 ``level.kp_id`` 与 ``rng``）。

    Returns:
        题目 dict（``answer_type="enum"``）。

    Raises:
        KeyError: 该知识点没有配置词条。
    """
    kp_id = _kp_of(ctx)
    pool = WORDS.get(kp_id)
    if not pool:
        raise KeyError(f"语文知识点 {kp_id!r} 没有配置词条，无法出字词辨析题。")

    rng = ctx["rng"]
    item = pool[rng.randrange(len(pool))]
    answer = str(item["answer"])
    options: List[str] = list(item["options"])
    rng.shuffle(options)
    labels = "　".join(f"{index}. {option}" for index, option in enumerate(options, start=1))

    # 答案在洗牌后的落点（1 基）。语料配置错误时 answer 不在 options 里 → 直接炸，
    # 不静默降级（静默会产出一道「没有任何选项正确」的题）。
    try:
        answer_position = options.index(answer) + 1
    except ValueError as exc:  # pragma: no cover - 语料错误才会走到
        raise ValueError(
            f"语文词条配置错误：kp={kp_id!r} 的 answer {answer!r} 不在 options {options} 里。"
        ) from exc

    return {
        "question": f"{item['stem']}\n（选项：{labels}）",
        "answer": answer,
        "answer_type": "enum",
        # 只登记「正确答案所在的那个序号」——阿拉伯数字与中文数字都要，
        # 但**不能**把所有序号都登记进去（那样写 1、2、3 全判对）。
        "accept": [answer] + _ordinal_forms(answer_position),
        "hints": [
            item["tip"],
            _HINT2.get(kp_id, "把每个选项代进句子里读一遍。"),
            _HINT3.get(kp_id, "注意词语的适用对象与感情色彩。"),
        ],
        "steps": [
            f"正确答案是「{answer}」（洗牌后是第 {answer_position} 项）。",
            item["explain"],
        ],
        "unit": "",
        "role": "小学者",
        "story": f"语文积累 · {_kp_label(kp_id)}：读句子，把括号里该填的那一个挑出来。",
        "verify": _verify_stub(),
    }


TEMPLATES = {
    "poem_fill": poem_fill,
    "word_order": word_order,
}
"""``{skill: callable}`` —— 由 :func:`subjects.load_templates` 按注册表动态导入。"""


if __name__ == "__main__":  # pragma: no cover - 手工调试入口
    import random

    for skill, kp_id in (
        ("poem_fill", "c3_poem_spring"),
        ("poem_fill", "c6_poem_philosophy"),
        ("word_order", "c3_word_measure"),
        ("word_order", "c5_word_idiom"),
    ):
        demo_ctx = {"level": {"kp_id": kp_id}, "rng": random.Random("demo"), "difficulty": 3}
        demo = TEMPLATES[skill](demo_ctx)
        print(f"[{skill}] {demo['question']}")
        print(f"  答案：{demo['answer']}（{demo['answer_type']}）")
        print(f"  提示1：{demo['hints'][0]}")
        print()
