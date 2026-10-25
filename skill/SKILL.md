---
name: mcd-math-journey
description: "用麦当劳 MCP 的真实菜单/营养/价格出小学数学应用题，答案由真实计价接口校验。支持按年级出题、三级提示、错因归因、错题本、家长周报、五条故事线闯关与角色图鉴。触发词：麦麦取经录、取经数学、按年级出题、麦当劳数学题、三国/水浒/丝路/郑和数学题、错题本、家长周报、数学闯关、判这道题、出几道数学题。"
description_en: "Generate primary-school math word problems from real McDonald's China MCP data (menu, nutrition, live prices); answers verified by the official pricing tool. Grade-based problem sets, 3-level hints, error-type diagnosis, wrong-answer book, parent weekly report, 5 storylines with stage progression and a character codex. Triggers: mcd-math-journey, math word problems from real menu data, grade-level math practice, wrong-answer book, parent weekly report, math stage challenge."
version: 1.0.0
author: mcd-math-journey
level: personal
---

# 麦麦取经录 · 技能操作指南

> 这份文件写给**要动手跑命令**的模型：接到用户哪句话、跑哪条命令、在哪个目录跑、结果怎么回话。
> 「有什么能力、字段规范、只读工具白名单、合规细则」在**仓库根 `SKILL.md`**（能力手册），本文件不复述，需要时引用它。

## 0. 一条铁律：命令一律走启动器

技能被加载时，命令行进程的 `cwd` **不一定是仓库根**，所以不要直接跑 `python scripts/cli.py ...`（那只在仓库根有效）。所有命令写成：

```bash
python <INVOKE> <子命令> [参数...]
```

`<INVOKE>` 是**启动器脚本的绝对路径**，常见两种位置：

| 场景 | `<INVOKE>` 实际路径 |
| --- | --- |
| 已安装到本机（对话里触发时就是这种） | `~/.workbuddy/skills/mcd-math-journey/invoke.py`（Windows：`C:\Users\<用户名>\.workbuddy\skills\mcd-math-journey\invoke.py`） |
| 直接在仓库里用 | `<仓库根>/skill/invoke.py` |

启动器只做三件事：**找到仓库根 → 把参数原样转给 `scripts/cli.py` → 透传 stdin/stdout 与退出码**。业务代码永远只有 `scripts/` 那一份，启动器里没有任何数学逻辑，所以不存在「两份代码漂移」的问题。

不确定它找到了哪个仓库时：

```bash
python <INVOKE> where      # 打印认定的项目根、命中方式、两个锚点是否齐全
```

**不知道 `<INVOKE>` 的绝对路径怎么办**（按顺序试，别猜）：

1. 先按上表拼一次：Windows 用 `$env:USERPROFILE\.workbuddy\skills\mcd-math-journey\invoke.py`，
   macOS / Linux 用 `$HOME/.workbuddy/skills/mcd-math-journey/invoke.py`；
2. 文件不存在就用 `ls ~/.workbuddy/skills/`（Windows：`dir $env:USERPROFILE\.workbuddy\skills`）看看有没有这个目录；
3. 都没有就问用户「麦麦取经录的仓库在哪 / 技能装在哪」，**不要自己 clone 或新建目录**。

---

## 一、第一步永远是环境自检

接到任何请求，**先跑**：

```bash
python <INVOKE> doctor
```

看三处就够判断现状：

1. `环境变量 MCD_MCP_TOKEN：已设置` 还是 `未设置`
2. `MCP 连通性：OK（服务 ...）` 还是 `跳过` / `失败`
3. 末尾 `数据源当前级别：` 每行的级别列（`mcp` / `cache` / `sample`）

然后**按事实决定说话方式**：

| doctor 看到的结果 | 事实 | 你必须怎么表述 |
| --- | --- | --- |
| 令牌已设置 + MCP 连通 | 数字来自实时接口 | 可以说「数字来自麦当劳实时菜单 / 营养 / 价格接口」 |
| 未配令牌，或连通失败，但缓存有数据 | 用的是本机上一次抓取结果 | 必须说「最近一次抓取的**缓存**数据」，**不许说实时** |
| 只有示例快照 | 用的是仓库内置演示数据 | 必须说「**示例数据**，仅用于演示」 |

**数据级别只有一个权威来源**：题目数据表的标题后缀 —— `（实时数据）` / `（缓存数据 2026-10-25）` / `（示例数据）`。转述时**原样保留这个后缀**，不许改写、省略，更不许把「示例数据」说成实时数据。

`doctor` 不需要令牌也能跑完（会自动降级到缓存 / 示例数据）。如果它报「找不到麦麦取经录项目根目录」，直接看**第六节**。

---

## 二、意图 → 命令映射表

> `--story` 的合法取值只有 7 个已上线 id：`xiyou`（西游记，默认）/ `sanguo`（三国演义）/ `silu`（丝路商队）/ `zhenghe`（郑和下西洋）/ `shuihu`（水浒传）/ `nuozha`（哪吒闹海）/ `fengshen`（封神演义）。写错会被参数校验直接拒掉（退出码 2）。
>
> `--subject` 的合法取值只有 3 个已上线 id：`math`（数学，默认）/ `chinese`（语文）/ `english`（英语）。**非数学学科没有故事线关卡**，必须配合 `--kp` 或 `--skill` 使用。

| 用户意图 | 命令 | 是否交互 |
| --- | --- | --- |
| 「有哪些故事线 / 哪条解锁了 / 进度到哪了」 | `python <INVOKE> stories` | 一次性输出 |
| 「七条线有哪些关」（总览） | `python <INVOKE> stages` | 一次性输出 |
| 「三国演义的 9 关是什么 / 哪关能玩」 | `python <INVOKE> stages --story sanguo` | 一次性输出 |
| 「出一道第 3 关第 2 难的题」（指定关卡-难度） | `python <INVOKE> play --level 3-2 --seed <seed>` | **需 stdin**（等答案） |
| 「出一道三国演义的题」 | `python <INVOKE> play --story sanguo --level sanguo-3-2 --seed <seed>` | **需 stdin** |
| 「四年级出 5 道」 | `python <INVOKE> play --grade 4 --count 5 --seed <seed>` | **需 stdin**（每道题各要一次输入） |
| 「出几道元角分的题」 | `python <INVOKE> play --kp g3_money --count 1 --seed <seed>` | **需 stdin** |
| 「出一道哪吒闹海的题」 | `python <INVOKE> play --story nuozha --level nuozha-3-2 --seed <seed>` | **需 stdin** |
| 「出一道封神演义的题」 | `python <INVOKE> play --story fengshen --level fengshen-9-3 --seed <seed>` | **需 stdin** |
| 「出一道语文的古诗默写题」 | `python <INVOKE> play --subject chinese --kp c3_poem_spring --seed <seed>` | **需 stdin**（答案是文字） |
| 「出一道语文的字词辨析题」 | `python <INVOKE> play --subject chinese --kp c5_word_idiom --skill word_order --seed <seed>` | **需 stdin**（答案是文字） |
| 「出一道英语单词拼写题」 | `python <INVOKE> play --subject english --kp e3_spell_fruit --seed <seed>` | **需 stdin**（答案是英文单词） |
| 「我要闯第 3 关」（计时 / 评品阶 / 收角色卡） | `python <INVOKE> play --level 3` | **强交互**：逐题计时，绝不能代跑 |
| 「三国演义第 2 关闯一下」 | `python <INVOKE> play --story sanguo --stage 2` | **强交互** |
| 「看看角色图鉴 / 收集了几个」 | `python <INVOKE> characters --story xiyou` | 一次性输出 |
| 「只看已解锁的角色介绍」 | `python <INVOKE> characters --unlocked-only` | 一次性输出 |
| 「错题本」 | `python <INVOKE> wrongbook` | 一次性输出 |
| 「只看三国演义的错题」 | `python <INVOKE> wrongbook --story sanguo` | 一次性输出 |
| 「只看元角分的错题」 | `python <INVOKE> wrongbook --kp g3_money` | 一次性输出 |
| 「家长周报 / 这周学得怎么样」 | `python <INVOKE> report --days 7` | 一次性输出 |
| 「只看西游记的周报」 | `python <INVOKE> report --days 7 --story xiyou` | 一次性输出 |
| 「环境自检 / 能连上吗」 | `python <INVOKE> doctor` | 一次性输出 |
| 「抓一下最新数据」 | `python <INVOKE> data --fetch` | 一次性输出（写缓存，可能较慢） |
| 「现在用的是实时还是缓存数据」 | `python <INVOKE> data --status` | 一次性输出 |

**语文 / 英语怎么判分**：这两科没有数值答案，`verify.check_answer` 走文本分支——
归一化（NFKC→小写→空白折叠→去首尾标点）后与「参考答案 + 显式同义表 `accept`」做集合命中，
**不做编辑距离模糊匹配**（小学拼写题的考点就是精确拼写）。所以孩子写 `Monday` / `monday` 都算对，
但 `apply` 代替 `apple` 一定判错。反馈时直接把用户输入原样喂给 `play` 的标准输入即可。

**语文 / 英语为什么没有 `--story`**：故事线是「九关八十一难」的叙事外壳，只服务数学题库；
语文 / 英语没有关卡数据，出题按「学科 + 知识点 + skill」直接派发，题型模块在
`scripts/subjects/<学科>/templates.py`。这也**证明了两件事**：加一条故事线不用改代码，
加一个学科也不用改代码。

**「需 stdin」是什么意思**：`play` 用 `input()` 逐题等作答，等你把一行文本喂给它的标准输入。不给输入，它会打印完题面就退出（这正是「只出题、不判题」的正确用法）。**「强交互」的闯关模式不要代跑**：它有 600 秒/题上限与计时评分，代跑出来的品阶是假的；应把命令原样交给用户在自己终端里跑。

---

## 三、一道题从「出」到「判」的标准流程（必须固定 seed）

`play` 是交互式的，而对话里的命令执行是「一次跑完、拿输出」。要既出题又不泄露答案、还能在用户作答后判题，用**固定 `--seed` + 管道喂 stdin**：

**第 1 步 · 出题（不判题）**

```bash
python <INVOKE> play --level 3-2 --seed 20260520-a < /dev/null
```

- `< /dev/null` 让它读完题面就没有输入可读，于是打印「已退出。」并退出 0 —— **不会打印答案**。
- PowerShell 不支持 `<` 重定向，改成：`"" | python <INVOKE> play --level 3-2 --seed 20260520-a`
- 拿到输出的题面后，把**情境 + 数据表（连标题后缀）+ 题目**转述给用户，然后停下来等用户作答。

**第 2 步 · 要提示（1 / 2 / 3 级递进）**

```bash
printf 'h\n'        | python <INVOKE> play --level 3-2 --seed 20260520-a   # 只给第 1 级
printf 'h\nh\n'     | python <INVOKE> play --level 3-2 --seed 20260520-a   # 到第 2 级
printf 'h\nh\nh\n'  | python <INVOKE> play --level 3-2 --seed 20260520-a   # 到第 3 级
```

用户要几级就给几级，**从第 1 级开始逐级递进**；三级提示都不含最终答案（这是出题引擎保证的，不要自己额外补一句「所以答案是……」）。

**第 3 步 · 判题（判完才有讲解）**

```bash
echo "27.00" | python <INVOKE> play --level 3-2 --seed 20260520-a
```

- 答案直接写用户的原话即可，`27元`、`1,020`、`3/4`、`75%` 都能解析；**不要替用户改答案**。
- 判错时输出 `第 N 次还不对。<归因>：……`，归因只会是四类之一：运算错误 / 数量关系错误 / 单位换算错误 / 漏用条件。
- 判对才会打印分步讲解（末尾固定「适量、均衡搭配，不浪费。」）与校验算式 `校验算式：37.50*3（工具：calculate-price）`。

**为什么要固定 seed**：`--seed` 决定「抽哪个关卡 / 难度 / 数据组合」。同一个 seed + 同一份数据 = 同一道题，所以第二、三步才能复现第一步那道题。每次新题换一个可读 seed（如 `20260520-a`、`20260520-b`），并在本次会话里记住它。

**三个必须注意的坑**：

1. **中途别抓数据**。跑过 `data --fetch` 或缓存过期换了数据级别，同一个 seed 出的题数字就变了，判题会对不上。出题到判题之间不要动数据。
2. **`--count N` + `< /dev/null` 只会出第一题**（读完题面就 EOF 退出了）。要连着出 N 道题又都不判，就换 N 个 seed 各跑一次 `--count 1`；要一次跑完 N 道，得按题数喂 N 行输入（`printf 'a1\na2\n'`）。
3. **重复出一模一样的题**要同时固定 `--level`/`--grade`/`--kp` 和 `--seed`；换 seed 就是换题，不要拿旧答案对新题。

---

## 四、回答呈现规范

**出题时**

1. 先一句话情境（来自命令行输出的叙述段），再贴**数据表**（表标题带数据级别后缀，原样保留），最后是题目原文。
2. **绝不主动给答案**，也不要在题面后面补「提示：用乘法」这类变相泄题的话；提示只在用户要的时候、按级给。
3. 顺手说明玩法一行：`输入答案后回车；h = 提示（最多 3 次），s = 跳过，q = 退出`。

**给提示时**

只有三级，逐级递进：第 1 级指方向（该用哪种运算）→ 第 2 级给算式框架（不填数）→ 第 3 级给关键中间结果。**任何一级都不许出现最终答案或等价的完整算式结果。**

**判题后**

1. 先明确「对了 / 还不对」。
2. 判对：给出**校验来源**（`calculate-price` 复核 / 本地 Decimal 校验），然后给分步讲解。
3. 判错：照录归因类别（运算错误 / 数量关系错误 / 单位换算错误 / 漏用条件），再鼓励他重试；不要立刻把答案说出去（命令行在答错 1~2 次时也只提示「再想一想」）。
4. 讲解要**分步**（一步一行，写清每一步在算什么），末尾保留固定句「适量、均衡搭配，不浪费。」。
5. 每份输出都带合规页脚：**「非麦当劳官方产品，输出仅供参考，餐品信息与价格以麦当劳官方渠道实时结果为准。」**（命令行自己会打，你转述时保留）。

**转述列表类输出时**：`stories` / `stages` / `characters` / `wrongbook` / `report` 是给人看的中文排版文本，**挑重点转述，不要整段复制 66 列分隔线**；数字、关名、状态（🔒 未解锁 / 已通关 ★★☆）必须与输出一致，不许美化。

---

## 五、硬边界（越界就拒，不要「灵活处理」）

- **不下单、不点餐、不代付、不抽奖、不绑券**。本项目是只读的教学工具；只读工具白名单与黑名单见仓库根 `SKILL.md` 第二节。
- **不采集任何儿童个人信息**：不追问姓名、学校、班级、生日、住址、手机号；用户主动说也不落库（进度库只记题目与对错）。
- **不给医疗 / 营养 / 膳食建议**。营养数值只用于出计算题，不解释成「该吃什么」「健不健康」。
- **不引导加量消费**：不出现「多买更划算所以多买点」「凑单」这类说法。
- **令牌只从环境变量 `MCD_MCP_TOKEN` 读取**；任何输出、日志、回复里都不得出现真实凭证，也不要让用户把令牌贴进对话。
- 故事线只作情境比喻，不涉及宗教与超自然表述；不出现其他餐饮品牌与跨品牌比较。
- 技能文件本身不含业务代码副本：**不要为了「让命令能跑」去复制 `scripts/`**，改用启动器或设置 `MMQJ_ROOT`。

---

## 六、找不到项目根怎么办

现象：任何命令都输出

```text
找不到麦麦取经录项目根目录。请先 git clone 仓库，然后设置环境变量 MMQJ_ROOT 指向仓库根（含 scripts/cli.py 的目录）。
```

（退出码 **3**，是人话报错，不是 traceback。）

启动器按这个顺序找项目根：`MMQJ_ROOT` 环境变量 → 安装时写下的 `.mmqj_root` 记录文件 → 从启动器目录逐级向上查找 → `skill/` 的上一级。所以修法只有两种：

**A. 装在本机时最省事**：重跑一次安装脚本，它会写下当前仓库的位置

```bash
python <仓库根>/skill/install.py
```

**B. 手动指定**（仓库被移动、或想临时指到另一个 clone）

```bash
# Windows PowerShell（永久）
setx MMQJ_ROOT "D:\path\to\mcd-math-journey"
# Windows 当前窗口（临时）
$env:MMQJ_ROOT = "D:\path\to\mcd-math-journey"
# macOS / Linux
export MMQJ_ROOT=/path/to/mcd-math-journey
```

**C. 从没装过**：先 clone 再装

```bash
git clone <仓库地址> mcd-math-journey
python mcd-math-journey/skill/install.py
```

设完自检一句：`python <INVOKE> where`，它会把「认定的项目根 / 命中方式 / 两个锚点是否存在」都打出来。**不要靠猜**：找不到根就问用户仓库在哪，或让用户跑一次 `where`。

---

## 附录 A · 退出码语义（判断「为什么没输出」时用）

| 退出码 | 含义 | 该怎么办 |
| --- | --- | --- |
| 0 | 正常 | 转述输出 |
| 2 | **关卡 / 故事线未解锁**，或参数错误（如 `--story` 写了不存在的 id、`--kp` 筛不到关卡、`--level` 不存在） | 照实告诉用户未解锁与前置条件，并按输出里给的「现在可以玩」命令重试；**不要**反复重试同一条命令 |
| 3 | 启动器找不到项目根 | 按第六节处理 |
| 4 | 启动器起不来子进程（Python 不可用 / `scripts/` 不完整） | 提示用户检查 Python 与仓库完整性 |

两个易错点：

- `play --level 9`（闯关）会因未解锁返回 2；`play --level 3-2`（单题）**不校验解锁**，任何关都能出题。
- `play --story sanguo --stage 3` 受**故事线解锁**限制（需先通关《西游记》9 关）→ 退出码 2 且提示「《三国演义》尚未解锁」；而 `play --story sanguo --level sanguo-3-2` 是单题模式，不受限。

## 附录 B · 知识点 id（`--kp` 用，写错就退出码 2）

`--kp` 的筛选范围是**所选故事线的关卡表**，不是课程表全量；下表是默认线（西游记）里实际用到的值，其余四条线的知识点组合略有不同，跨线用 `--kp` 前先 `stages --story <id>` 看一眼主题。

| 年级 | 可用 `--kp` |
| --- | --- |
| 3 | `g3_add_sub`（万以内加减法）、`g3_multi_sub`（多步连减）、`g3_money`（元角分）、`g3_div_basic`（除法初步与余数）、`g3_unit_measure`（质量与长度单位）、`g3_calendar`（年月日与时间）、`g3_fraction_intro`（分数初步） |
| 4 | `g4_div_2digit`（两位数除法）、`g4_decimal`（小数的意义与加减）、`g4_mixed_ops`（四则混合运算）、`g4_bar_chart`（条形统计图与平均数） |
| 5 | `g5_decimal_muldiv`（小数乘除法）、`g5_fraction_addsub`（分数认识与约分） |
| 6 | `g6_percent`（百分数与折扣） |

不在这里的 id（如 `g3_mul_basic`、`g6_circle`）虽然写在课程表 `data/curriculum.json` 里，但当前五条故事线没有对应关卡，传进去会得到 `数据错误：按当前筛选条件找不到可用关卡。`（退出码 2）。**不确定就先 `stages --story <id>` 看主题，或直接用 `--grade`。**

## 附录 C · 与仓库根 `SKILL.md` 的分工

| 想知道 | 看哪份 |
| --- | --- |
| 某句话该跑哪条命令、怎么喂输入、怎么回话、为什么退出码是 2 | **本文件** |
| 题目 / 校验结果的字段规范、三级提示规则、闯关与品阶规则、只读工具白名单、红线词与合规细则 | 仓库根 `SKILL.md` |
| 规则参数原值（品阶比例、单题上限、彩蛋池） | `data/run_rules.json` |
| 五条故事线的关名 / 场景 / 角色 | `data/storylines/*.json` |
