---
name: mcd-math-journey
description: 用麦当劳 MCP 的真实菜单/营养/价格/优惠券数据，为小学 3-6 年级生成可被真实接口校验答案的数学应用题，以《西游记》《三国演义》《水浒传》、丝路商队、郑和下西洋五条故事线之一作为情境外壳。触发词：麦麦取经录、取经数学、三国数学题、丝路数学题、真实数据数学题、按年级出题、错题本、家长周报、数学闯关、MCP 出题。
---

# 麦麦取经录（mcd-math-journey）

**一句话**：题目里的数字都是真的，答案由麦当劳 MCP 真实计算校验。

## 一、能力边界

### 能做什么

| 能力 | 入口 | 说明 |
| --- | --- | --- |
| 列出故事线 | `python scripts/cli.py stories` | 读 `data/storylines/index.json`，含 `available` 状态 |
| 列出九关八十一难与通关状态 | `python scripts/cli.py stages`（全部线摘要）/ `stages --story sanguo`（单线 9 关） | 只读本地配置与进度 |
| 按关卡 / 年级 / 知识点出题 | `play --story sanguo --level sanguo-3-2`、`play --grade 4 --count 5`、`play --kp g3_mul_basic` | 题目绑定真实数据字段；`--story` 默认 `xiyou` |
| 三级递进提示 | 交互中按 `h` | 指方向 → 给算式框架 → 给关键中间结果；**任何一级都不给最终答案** |
| 校验答案 | `verify.check_answer(problem, answer)` | `Decimal` 容差 0.01；优先调用 MCP `calculate-price` 复核 |
| 错因诊断 | `verify.diagnose(problem, answer)` | 归到 4 类：运算错误 / 数量关系错误 / 单位换算错误 / 漏用条件 |
| 分步讲解 | `verify.explain(problem)` | Markdown，结尾附「适量、均衡搭配，不浪费。」 |
| 错题本 | `python scripts/cli.py wrongbook [--story ID]` | 本机 SQLite，按知识点聚合；不传 `--story` 则全量并标注来源线 |
| 家长周报 | `python scripts/cli.py report --days 7 [--story ID]` | Markdown，含薄弱知识点 Top3 与下周建议 |
| **闯关（v2.0）** | `play --level 3` 或 `play --story sanguo --stage 3` | 按关抽 1~3 题（场景 10 选 1、难度档不重复、守将 5 选 1）、计时、评品阶、揭守关大将、收角色卡 |
| **角色图鉴（v2.0）** | `python scripts/cli.py characters [--story ID] [--unlocked-only]` | 每线 14 名：9 闯关大将 + 5 守关池；三种状态（未遇见 / 已收集待解锁 / 已解锁） |
| **解锁进度（v2.0）** | `python scripts/cli.py stories` | 5 条线的 `已通关 N/9 关` 与锁状态（下一条线要先走完上一条线 9 关） |
| 数据抓取与状态 | `python scripts/data_source.py --fetch` / `--status` | 三级降级：MCP → 缓存 → 示例 |
| 环境自检 | `python scripts/cli.py doctor` | 令牌、连通性、缓存、示例数据、数据库、故事线注册数 |

### 闯关规则（规则全文见 `docs/SYSTEM_RULES.md`）

| 事项 | 规则 |
| --- | --- |
| 关卡解锁 | 第 1 关默认可玩；第 `k` 关要求第 `k-1` 关已通关（由 `stage_clear` 推导，不存状态位） |
| 故事线解锁 | 第 `n` 条线要求第 `n-1` 条线 9 关全通；顺序取 `index.json` 的 `storylines` 数组顺序 |
| 一次 run 抽什么 | 场景 10 选 1 / 题数 30-40-30 / 难度档 9 选 k 不重复且升序 / 守将 5 选 1，全部由 `seed` 派生四路独立子种子 |
| 种子 | `story-stage-毫秒时间戳-随机后缀`，记进 `stage_runs.seed`；`--seed` 可指定以重放 |
| 品阶 | `ratio ≤ 0.60` 黄金、`≤ 1.00` 白银、其余青铜；基准按知识点年级（3/4/5/6 年级 = 45/60/75/90 秒每题）；单题上限 600 秒 |
| 修正（只降不升） | 用过提示 → 上限白银；答错 ≥3 次 → 上限白银；跳过 → 无品阶、不收卡（但照记通关） |
| 角色收集 | 通关即收卡（闯关大将 + 本场守将各 1 张）；**干净通关**（不看提示、不答错、不跳过）才解锁介绍，解锁后不回锁 |
| 重玩 | 允许无限重玩；`best_tier` 只升不降 |
| 中断 | 中途退出 / 刷新视为作废（`stage_runs` 留 `finished_at` 为空），不计品阶、不收集 |

规则参数外置在 `data/run_rules.json`：CLI 与演示页读同一份，演示页由 `export_demo_bank.py` 原样内联，
所以两端品阶判定不会跑偏（自检里 135 条固定用例逐条比对两端抽样结果）。

### 不做什么（硬边界）

- **不点餐、不下单、不代付、不抽奖、不绑定券**。本项目是只读的教学工具。
- 不采集任何儿童个人信息（无姓名、手机号、学校、生日字段），数据不出本机。
- 不提供医疗 / 营养 / 膳食建议；营养数值仅用于计算题，不构成专业建议。
- 不诱导额外消费：题目只使用学习必需的数量，不出现鼓励加量购买或以优惠为由增加购买量的表述。
- 不模仿、不使用任何现代影视改编作品的形象；演示页只有抽象几何装饰，无人物立绘。
- **不新增近现代史题材**：故事线只从公有领域古典作品与公开历史事实（丝路商旅、郑和下西洋）取材，
  每条线的 JSON 必须写明 `era_note`；不含冲突、暴力、宗教与超自然表述。
- **角色介绍只写性格与做事方式**：70 条介绍不许出现法术 / 神力 / 显灵 / 天意 / 神机妙算、
  不许出现冲突与贬损、不做宗教宣传；每条必须含一个与数学有关的小细节（60~150 字）。
  红线词表在 `scripts/selfcheck.py::RED_LINE_RE`，命中即自检 FAIL。
  演示页随机彩蛋的内容池文案（`data/run_rules.json` 的 `easter_egg.pool`）过同一张红线词表，
  由自检的 `check_easter_egg()` 逐条扫描；彩蛋记录只存本机 `localStorage`，家长可一键关闭。
- **演示页不出人物立绘**：角色用「印章 + 名字 + 几何剪影」表示，装饰只用自绘几何 SVG。

## 二、只读工具白名单

代码常量：`scripts/mcp_client.py::READONLY_WHITELIST`

| 工具名 | 用途 | 支撑题型 |
| --- | --- | --- |
| `query-meals` | 可售餐品列表与单价 | 乘法、平均数、折扣方案 |
| `list-nutrition-foods` | 营养信息（能量 / 蛋白质 / 净含量） | 加减、连减、单位换算、百分数 |
| `query-meal-detail` | 单个餐品详情 | 单题取数校验 |
| `calculate-price` | 官方价格计算 | **答案复核**（核心） |
| `available-coupons` | 麦麦省券列表（仅券名/状态，无门槛面额） | 折扣与方案比较（实时数据缺面额时降级为示例数据并如实标注） |
| `campaign-calendar` | 活动起止时间 | 时间与行程 |
| `mall-points-products` | 积分商品所需积分 | 除法与余数 |
| `now-time-info` | 当前时间 | 时间类题的基准 |

黑名单常量：`FORBIDDEN_WRITE_TOOLS` —— `create-order`、`cancel-order`、
`delivery-create-address`、`mall-create-order`、`party-order-create`、
`draw-lottery`、`auto-bind-coupons`。调用这些名字会直接抛 `PermissionError`。

## 三、标准工作流

```
1. 识别年级        → 未指定时默认按关卡 grade_hint；显式 --grade 优先
2. 确认故事线      → 未指定时用注册表默认线 xiyou；显式 --story 优先（只列 available 的线）
3. 选关卡          → 命中 data/storylines/<story>.json 的 level_id
                      · 默认线：1-1 … 9-9（历史格式，保持不变）
                      · 其他线：<story>-<stage>-<difficulty>，如 sanguo-3-2
4. 取真实数据      → data_source 三级降级，记录 source（mcp/cache/sample）；数据与题材无关
5. 出题            → 套用该关题型模板（模板只按「第几关」索引），Decimal 精确算出 answer
6. 三级提示        → h1 指方向 / h2 算式框架 / h3 关键中间结果（均不含最终答案）
7. 学生作答 → 校验 → 有 token 时再调 calculate-price 复核，记录 verify_source
8. 讲解            → 分步 Markdown + 校验算式 + 「适量、均衡搭配，不浪费。」
9. 记错题          → SQLite attempts 表（带 story 列）；一关 9 难全对则 clear_stage 记星
10. 生成周报       → 家长视角，按线过滤或全量汇总 + 薄弱知识点 Top3 + 下周建议
```

**闯关玩法的工作流**（`play --level <关号>` / `--stage`）：解锁校验 → `journey.draw_run` 抽场景/题数/
难度/守将 → 按抽取结果出题（`problem_gen.level_id_for`）→ 逐题计时（`time.monotonic`，单题上限 600 秒）→
`journey.grade_tier` 评品阶 → `progress.finish_run` 落库 → `journey.collect` 收卡与解锁介绍 →
打印「用时 / 基准 / ratio / 品阶 / 守将揭名 / 角色卡 +N / 介绍解锁 M 张」。未解锁关卡 `exit=2` 且无 traceback。

## 四、输出格式规范

**题目对象**（`problem_gen.generate` 返回值，字段名固定）：

```json
{
  "story_id": "xiyou",
  "story_name": "西游记",
  "level_id": "3-2",
  "stage": 3,
  "stage_name": "八戒买饼",
  "kp_id": "g3_mul_basic",
  "kp_name": "两位数乘一位数",
  "grade": 3,
  "stage_grade_hint": 3,
  "role": "猪八戒",
  "brief": "三份同样的餐品，总价是多少元？",
  "story": "……",
  "question": "每份可乐(中) 9.00 元，3 份一共多少元？",
  "data_table": {"title": "今日菜单（示例数据）", "columns": ["餐品", "单价（元）", "份数"], "rows": [["可乐(中)", "9.00", "3"]]},
  "answer_type": "number",
  "answer": "27.00",
  "unit": "元",
  "hints": ["……", "……", "……"],
  "steps": ["……", "适量、均衡搭配，不浪费。"],
  "verify": {"formula": "9.00*3", "tool": "calculate-price"},
  "diagnosis": {"type": "mul", "a": "9.00", "b": 3, "expected": "27.00"},
  "data_source": "sample"
}
```

> 注 1：`story` 这个键在本项目里一直是**情境叙述文本**（不是故事线 id），故事线标识放在
> `story_id`（英文 id）与 `story_name`（中文名），这样旧调用方读 `problem["story"]` 不会突然拿到 `"sanguo"`。
>
> 注 2：`brief` 是关卡配置里那一句话的题面摘要（来自 `data/storylines/*.json`），用于列表与自检；
> `diagnosis` 是错因诊断用的结构化元信息（`type` 决定走哪条诊断分支），两者都由 `generate()` 一并返回。
>
> 注 2：`data_table.title` 的后缀（`（示例数据）` / `（实时数据）` / `（缓存数据 YYYY-MM-DD）`）
> 由 `data_source` 的实际级别决定，与题目字段严格同源——上例 `data_source` 为 `sample`，
> 所以标题是「（示例数据）」；若切到实时数据，同一道题的标题会变成「（实时数据）」。

**校验结果**（`verify.check_answer` 返回值）：

```json
{
  "correct": true,
  "expected": "27.00",
  "actual": "27",
  "delta": "0.00",
  "verify_source": "mcp",
  "diagnosis": ""
}
```

**约定**：

- `grade` 取**知识点所属年级**（`g4_*` 就是 4 年级），`stage_grade_hint` 是该关的难度定位；
  两者不一致属正常（同一关 9 难可跨年级），命令行分两段显示：
  `年级：4 年级（知识点所属）｜所属关：第 8 关 真假美猴王`。
- `--grade N` 的筛选口径同样是**知识点年级**，与展示字段完全同源；该年级没有候选 level 的关卡会被自然跳过，不会报错。
- `data_table.title` 的后缀由 `data_source` 决定，与题目字段同源：
  `（实时数据）` / `（缓存数据，若有抓取时间则附 YYYY-MM-DD）` / `（示例数据）`；无抓取时间时退化为 `（缓存数据）`。
- `data_source` 与 `verify_source` 必须如实反映实际路径，禁止把降级结果标注为 `mcp`。
- 金额统一两位小数字符串；百分数只写数值（`"19.6"`），单位单独放 `unit: "%"`。
- 讲解 Markdown 的最后一行固定为 `> 适量、均衡搭配，不浪费。`
- **故事线可插拔**：新增一条线 = `data/storylines/index.json` 加一项 + 一份同构 JSON
  （`id / name / subtitle / era_note / roles / stages`），`level_id` 用 `<story>-<stage>-<difficulty>`；
  9 个数学模板只按「第几关」索引，**不需要改任何数学代码**。`available: false` 的线不注册、不列出。
- **关名与场景分工**：每个 stage 有 `name`（事件名，如「南京启航」）与 `scene`（具体地点，如「刘家港码头」）。
  叙述模板把 `scene` 当地点、把 `name` 当事件，绝不把关名当地点用（否则会拼出「在南京启航歇脚」这种病句）；
  `scene` 缺失时模板退化为不指定地点的写法。selfcheck 会校验 `scene` 非空、线内不重复、且不等于关名。

## 五、合规约束

1. 每份输出（命令行页脚、演示页页脚、周报）必须带：
   「非麦当劳官方产品，输出仅供参考，餐品信息与价格以麦当劳官方渠道实时结果为准。」
2. 故事仅作情境比喻，用于提升学习趣味性；不得出现宗教、超自然表述。
3. 不出现其他餐饮品牌名称与跨品牌比较。
4. 令牌只从环境变量 `MCD_MCP_TOKEN` 读取；任何输出、日志、示例文件都不得包含真实凭证。
5. 不引导消费：不得出现鼓励加量购买、以优惠为由增加购买量的表述；每道题讲解固定附轻提示。
