---
name: mcd-math-journey
description: 用麦当劳 MCP 的真实菜单/营养/价格/优惠券数据，为小学 3-6 年级生成可被真实接口校验答案的数学应用题，以《西游记》取经闯关作为故事外壳。触发词：麦麦取经录、取经数学、真实数据数学题、按年级出题、错题本、家长周报、数学闯关、MCP 出题。
---

# 麦麦取经录（mcd-math-journey）

**一句话**：题目里的数字都是真的，答案由麦当劳 MCP 真实计算校验。

## 一、能力边界

### 能做什么

| 能力 | 入口 | 说明 |
| --- | --- | --- |
| 列出九关八十一难与通关状态 | `python scripts/cli.py stages` | 只读本地配置与进度 |
| 按关卡 / 年级 / 知识点出题 | `python scripts/cli.py play --level 3-2`、`--grade 4 --count 5`、`--kp g3_mul_basic` | 题目绑定真实数据字段 |
| 三级递进提示 | 交互中按 `h` | 指方向 → 给算式框架 → 给关键中间结果；**任何一级都不给最终答案** |
| 校验答案 | `verify.check_answer(problem, answer)` | `Decimal` 容差 0.01；优先调用 MCP `calculate-price` 复核 |
| 错因诊断 | `verify.diagnose(problem, answer)` | 归到 4 类：运算错误 / 数量关系错误 / 单位换算错误 / 漏用条件 |
| 分步讲解 | `verify.explain(problem)` | Markdown，结尾附「适量、均衡搭配，不浪费。」 |
| 错题本 | `python scripts/cli.py wrongbook` | 本机 SQLite，按知识点聚合 |
| 家长周报 | `python scripts/cli.py report --days 7` | Markdown，含薄弱知识点 Top3 与下周建议 |
| 数据抓取与状态 | `python scripts/data_source.py --fetch` / `--status` | 三级降级：MCP → 缓存 → 示例 |
| 环境自检 | `python scripts/cli.py doctor` | 令牌、连通性、缓存、示例数据、数据库 |

### 不做什么（硬边界）

- **不点餐、不下单、不代付、不抽奖、不绑定券**。本项目是只读的教学工具。
- 不采集任何儿童个人信息（无姓名、手机号、学校、生日字段），数据不出本机。
- 不提供医疗 / 营养 / 膳食建议；营养数值仅用于计算题，不构成专业建议。
- 不诱导额外消费：题目只使用学习必需的数量，不出现鼓励加量购买或以优惠为由增加购买量的表述。
- 不模仿、不使用任何现代影视改编作品的形象；演示页只有抽象几何装饰，无人物立绘。

## 二、只读工具白名单

代码常量：`scripts/mcp_client.py::READONLY_WHITELIST`

| 工具名 | 用途 | 支撑题型 |
| --- | --- | --- |
| `query-meals` | 可售餐品列表与单价 | 乘法、平均数、折扣方案 |
| `list-nutrition-foods` | 营养信息（能量 / 蛋白质 / 净含量） | 加减、连减、单位换算、百分数 |
| `query-meal-detail` | 单个餐品详情 | 单题取数校验 |
| `calculate-price` | 官方价格计算 | **答案复核**（核心） |
| `available-coupons` | 麦麦省券面额与门槛 | 折扣与方案比较 |
| `campaign-calendar` | 活动起止时间 | 时间与行程 |
| `mall-points-products` | 积分商品所需积分 | 除法与余数 |
| `now-time-info` | 当前时间 | 时间类题的基准 |

黑名单常量：`FORBIDDEN_WRITE_TOOLS` —— `create-order`、`cancel-order`、
`delivery-create-address`、`mall-create-order`、`party-order-create`、
`draw-lottery`、`auto-bind-coupons`。调用这些名字会直接抛 `PermissionError`。

## 三、标准工作流

```
1. 识别年级        → 未指定时默认按关卡 grade_hint；显式 --grade 优先
2. 选关卡          → 命中 data/journey.json 的 level_id（1-1 … 9-9）
3. 取真实数据      → data_source 三级降级，记录 source（mcp/cache/sample）
4. 出题            → 套用该关题型模板，Decimal 精确算出 answer
5. 三级提示        → h1 指方向 / h2 算式框架 / h3 关键中间结果（均不含最终答案）
6. 学生作答 → 校验 → 有 token 时再调 calculate-price 复核，记录 verify_source
7. 讲解            → 分步 Markdown + 校验算式 + 「适量、均衡搭配，不浪费。」
8. 记错题          → SQLite attempts 表；一关 9 难全对则 clear_stage 记星
9. 生成周报        → 家长视角，薄弱知识点 Top3 + 下周建议
```

## 四、输出格式规范

**题目对象**（`problem_gen.generate` 返回值，字段名固定）：

```json
{
  "level_id": "3-2",
  "stage": 3,
  "stage_name": "八戒买饼",
  "kp_id": "g3_mul_basic",
  "kp_name": "两位数乘一位数",
  "grade": 3,
  "stage_grade_hint": 3,
  "role": "猪八戒",
  "story": "……",
  "question": "每份可乐(中) 9.00 元，3 份一共多少元？",
  "data_table": {"title": "今日菜单（示例数据）", "columns": ["餐品", "单价（元）", "份数"], "rows": [["可乐(中)", "9.00", "3"]]},
  "answer_type": "number",
  "answer": "27.00",
  "unit": "元",
  "hints": ["……", "……", "……"],
  "steps": ["……", "适量、均衡搭配，不浪费。"],
  "verify": {"formula": "9.00*3", "tool": "calculate-price"},
  "data_source": "sample"
}
```

> 注：`data_table.title` 的后缀（`（示例数据）` / `（实时数据）` / `（缓存数据 YYYY-MM-DD）`）
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

## 五、合规约束

1. 每份输出（命令行页脚、演示页页脚、周报）必须带：
   「非麦当劳官方产品，输出仅供参考，餐品信息与价格以麦当劳官方渠道实时结果为准。」
2. 故事仅作情境比喻，用于提升学习趣味性；不得出现宗教、超自然表述。
3. 不出现其他餐饮品牌名称与跨品牌比较。
4. 令牌只从环境变量 `MCD_MCP_TOKEN` 读取；任何输出、日志、示例文件都不得包含真实凭证。
5. 不引导消费：不得出现鼓励加量购买、以优惠为由增加购买量的表述；每道题讲解固定附轻提示。
