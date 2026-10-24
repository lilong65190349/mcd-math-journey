# 麦麦取经录 · 闯关系统规则与实现方案

> 版本 2.0 ｜ 2026-10-24
> 本文是**规则的唯一真相源**。代码、README、演示页的文案口径都以此为准；规则变更必须先改本文。

---

## 一、设计目标

| 目标 | 对应机制 |
|---|---|
| 让每个孩子看到的内容不一样 | 场景 10 选 1 + 每关抽 1~3 题 + 守关大将 5 选 1 |
| 让「做完才有下一步」成立 | 关卡顺序解锁 + 故事线顺序解锁 |
| 让做完有奖励、且值得重做 | 角色收集 + 品阶评定（重玩取最优） |
| 让「读介绍」成为做题的回报 | 角色介绍需干净通关才解锁 |

三条不可让渡的原则：
1. **题目数据必须真实**：所有数值只能来自 MCP 只读工具返回的字段（`energy_kcal` / `protein_g` / `weight_g` / 价格 / 券门槛与面额），**不得为凑玩法编造**。
2. **不惩罚、只奖励**：答错可以重试；提示与跳过不扣分，只影响品阶上限。
3. **不制造焦虑**：品阶只有三档且最低一档是「青铜」而非「失败」；无倒计时强迫、无排行榜羞辱。

---

## 二、全局结构

```
故事线（storyline） → 关卡（stage 1..9） → 单次挑战（run） → 题目（1~3 题）
```

- 5 条故事线：`xiyou`（西游记，默认） → `sanguo` → `silu` → `zhenghe` → `shuihu`
- 每条线 9 关，每关 9 个难度档（difficulty 1..9），共 405 个「关卡位」
- 一次「挑战 run」= 抽取若干题目并全部答对的过程，是计时、品阶、收集的最小单位

---

## 三、解锁规则

### 3.1 关卡链（线内）
- 每条线的**第 1 关默认解锁**，其余 8 关锁定。
- 第 `k` 关解锁的充要条件：第 `k-1` 关已通关（`stage_clear` 中存在该关记录）。
- 解锁状态由 `stage_clear` **推导**，不额外存状态位，避免状态不一致。

### 3.2 故事线链（线间）
- 只有第 1 条线（`xiyou`）默认可用。
- 第 `n` 条线解锁的充要条件：第 `n-1` 条线的**全部 9 关**均已通关。
- 顺序取自 `data/storylines/index.json` 的 `storylines` 数组顺序（该顺序即设计上的推荐难度递增顺序）。
- `available: false` 的线永远不可解锁（用于紧急下线）。

### 3.3 呈现口径
- 锁定关卡：显示关名与「🔒 需先通关第 k-1 关」，不可进入。
- 锁定故事线：名为「？？？」或显示线名 + 「需先完成《上一线》全部 9 关」，不可切换。
- **不隐藏未解锁内容**：让孩子知道后面有什么，才有动力（但守关大将身份除外，见 5.2）。

---

## 四、随机机制

一次 run 开始时一次性抽取并**记录种子**，整个 run 内不再变化（可复现、可审计）。

| 维度 | 规则 | 抽法 |
|---|---|---|
| **场景** | 每关 10 个备选，抽 1 个 | 见 4.1 |
| **题目数** | 1~3 题 | 权重：1 题 30%、2 题 40%、3 题 30% |
| **题目难度档** | 从该关 9 个难度档中抽 k 个**互不重复**的档位 | 无放回抽样，按难度升序排列 |
| **守关大将** | 5 名守关池中抽 1 名 | 均匀随机 |

### 4.1 场景变体的构造
不为 405 个关卡位各写 10 条文案（那是 4050 条，写不完也写不好）。采用**地点 × 场景修饰**的组合：

```
场景文本 = 场景修饰 + 该关的 scene（地点）
data/storylines/<story>.json:
  "scene_variants": ["清早的","半晌的","午后的","黄昏的","天刚亮的","歇脚时的","动身前的","半路上的","入夜前的","归途中的"]
每个 stage 保留现有 "scene"（地点）
```
- 每条线一份 10 个修饰语（5 线 × 10 = 50 条），与 9 个地点组合 → **每关实际有 10 个可选场景文本**，满足需求且文案量可控。
- 组合后需语法自洽：修饰语一律用「的/时/日/前/后」结尾可后接地点的形式。
- 程序侧断言（`selfcheck.py` 实际实现）：① 每条线 10 条修饰语互不重复；② 组合结果不以「的」结尾、不含「的的」或连续标点；③ **修饰语 × 地点冲突矩阵全部为 0**（冲突词表如「雪化后/雨后」不得与「火焰山」类地点组合）。

### 4.2 种子的记录与复现
- `run.seed = f"{story}-{stage}-{timestamp_ms}-{random_suffix}"`
- 抽场景/抽题数/抽难度/抽守将**全部由该 seed 派生**（`Random(seed)` 派生四路独立子种子，避免互相干扰）。
- CLI 提供 `--seed` 覆盖，用于复现与测试；默认自动生成。
- 记录下来的 seed 使「同一 run」可被精确重放——这既是测试手段，也是家长质疑「这题怎么来的」时的凭证。

---

## 五、角色系统

### 5.1 角色分两类

| 类型 | 数量 | 出现方式 | 是否收集 |
|---|---|---|---|
| **闯关大将** | 每条线 9 名（1 关 1 名，固定） | 该关固定出场 | 通关即收集 |
| **守关大将** | 每条线 5 名（线性守关池） | 每次进入该关时从 5 名中随机 1 名 | 通关即收集（本场出场的那位） |

> **设计取舍说明**：需求原文「每关另设 5 名守关大将」若按字面实现，是 9 关 × 5 = 45 名/线、全项目 225 名角色，每人配介绍会让文案质量崩掉。因此改为**每条线一个 5 人守关池**——每关进入时仍是「5 选 1 随机出现一位」，随机性与收集乐趣不变，角色总量降到 **5 线 × 14 = 70 名**，介绍文案得以逐个写好。此取舍需在 README 中写明。

### 5.2 守关大将的「神秘」呈现
- 进入关卡时守关大将**不显示名字**，显示为「？？？·守关者」+ 剪影。
- 通关后才揭名并收卡——制造「这次遇到谁」的悬念，也是重玩动机。
- 闯关大将则明示姓名（他是这一关的引导者）。

### 5.3 角色库
- 按故事线分组呈现，每位角色一张卡：`名字 / 身份 / 已获最高品阶 / 介绍（锁定或已解锁）`。
- 未收集：灰卡 + 「尚未遇见」。
- 已收集但介绍未解锁：显示名字与品阶，介绍区显示「待解锁：干净通关第 k 关（不看提示、不答错）」。
- 已解锁：完整介绍文本。

---

## 六、品阶评定

### 6.1 计时
- 从**第一题题干渲染完成**开始计时，到**最后一题判定为正确**结束。
- **实现口径**：`stage_runs` 只记录 run 级总用时 `elapsed_ms`（= 各题用时之和，不含结算动画）；**不单独存单题用时字段**。单题用时只用于 600 秒截断判定，不落库。
- **单题上限 600 秒**，超过按 600 秒计（防止挂机刷黄金）。
- 中途退出/关闭页面：该 run 作废，不记录品阶。

### 6.2 基准时间
每题基准秒数按**该题知识点所属年级**取：

| 年级 | 基准秒/题 |
|---|---|
| 3 | 45 |
| 4 | 60 |
| 5 | 75 |
| 6 | 90 |

```
budget_ms = Σ(该 run 每题的基准秒数) × 1000
ratio = elapsed_ms / budget_ms
```

### 6.3 三档判定

| 品阶 | 条件 | 含义 |
|---|---|---|
| 🥇 黄金 | `ratio ≤ 0.60` | 又快又准 |
| 🥈 白银 | `0.60 < ratio ≤ 1.00` | 稳 |
| 🥉 青铜 | `ratio > 1.00` | 完成即值得记录 |

### 6.4 修正项（只降不升）
| 情况 | 影响 |
|---|---|
| 使用过提示（`h`） | 品阶**上限为白银** |
| 使用过跳过（`s`） | **不给品阶、不收集角色**，仅记录「已看过本题」 |
| 答错过 1 次 | 品阶上限仍为黄金（不惩罚），但 `clean = false`（影响介绍解锁） |
| 答错 ≥3 次 | 品阶上限为白银 |

### 6.5 重玩与取优
- 同一关可无限重玩；角色卡上记录**历史最高品阶**（黄金 > 白银 > 青铜）。
- `stage_clear` 只记录「是否通关 + 最高品阶」，不因重玩变差而下降。

---

## 七、角色介绍解锁逻辑

```
收集条件：该角色出场的关卡 → 通关（所有抽到的题最终答对）
介绍解锁条件：该角色出场的关卡 → 干净通关（本次 run 中「未使用提示」且「一次答对」）
```

- 两个条件是**递进**的：先收卡，再解锁介绍。
- 未达干净通关时，卡片已入角色库、品阶可见，但介绍区锁定，并明确告知解锁条件。
- 解锁后介绍永久有效，不会因为后来重玩变差而回锁。
- 设计意图：把「读一段有趣的介绍」变成一次**专注作答**的奖励，而不是随手可得的文本。

---

## 八、数据模型

### 8.1 故事线 JSON 增量字段
```jsonc
{
  "id": "xiyou", "name": "西游记", "order": 1,
  "roles": { "clever": "孙悟空", "price": "猪八戒", "unit": "沙悟净", "review": "唐僧" },
  "scene_variants": ["清早的","半晌的","午后的","黄昏的","天刚亮的","歇脚时的","动身前的","半路上的","入夜前的","归途中的"],
  "stages": [ /* 现有结构不变，仍含 scene 与 9 个 levels */ ]
}
```

### 8.2 角色定义 `data/characters/<story>.json`（新增）
```jsonc
{
  "story": "xiyou",
  "stage_bosses": { "1":"xiyou_tang", "2":"xiyou_wukong" /* …9 个 */ },
  "guard_pool": ["xiyou_bajie","xiyou_shasheng","xiyou_bailongma","xiyou_taishang","xiyou_guanyin"],
  "characters": [
    {
      "id": "xiyou_tang",
      "name": "唐僧",
      "kind": "boss",              // boss=闯关大将 / guard=守关大将
      "stage": 1,                   // boss 才有；guard 为 null
      "title": "取经路上的定盘星",
      "intro": "……60~150 字，生动有趣，必须包含一个与数学有关的小细节……",
      "intro_locked_hint": "干净通关第 1 关，就能听师父讲讲他为什么从不迷路。"
    }
  ]
}
```

**介绍文案要求**：60~150 字；口吻对孩子说话；必须含一个与数学相关的小细节（如「他数过九九八十一难，其实是 9×9」）；不得含迷信、暴力、贬损；不得与其他品牌比较。

### 8.3 SQLite 新增表
```sql
-- 单次挑战记录
CREATE TABLE IF NOT EXISTS stage_runs (
  run_id      TEXT PRIMARY KEY,
  story       TEXT NOT NULL,
  level_id    TEXT NOT NULL,          -- 关（默认线 "3"，其他线 "sanguo-3"，与 stage_clear 的键一致）
  seed        TEXT NOT NULL,
  started_at  TEXT NOT NULL,
  finished_at TEXT,
  elapsed_ms  INTEGER NOT NULL DEFAULT 0,
  budget_ms   INTEGER NOT NULL DEFAULT 0,
  question_count INTEGER NOT NULL DEFAULT 0,
  correct_count  INTEGER NOT NULL DEFAULT 0,
  used_hint   INTEGER NOT NULL DEFAULT 0,
  used_skip   INTEGER NOT NULL DEFAULT 0,
  retry_count INTEGER NOT NULL DEFAULT 0,
  tier        TEXT,                   -- gold|silver|bronze|NULL
  clean       INTEGER NOT NULL DEFAULT 0,
  boss_id     TEXT,
  guard_id    TEXT,
  scene_variant TEXT                 -- 本场场景**完整文本**（如「晌午，山道旁的歇脚棚」），不是单独修饰语
);

-- 角色收集
CREATE TABLE IF NOT EXISTS collection (
  story        TEXT NOT NULL,
  char_id      TEXT NOT NULL,
  first_seen_at TEXT NOT NULL,
  best_tier    TEXT,                  -- gold|silver|bronze，取最优
  intro_unlocked INTEGER NOT NULL DEFAULT 0,
  intro_unlocked_at TEXT,
  PRIMARY KEY (story, char_id)
);
```
迁移沿用现有模式：`PRAGMA table_info` 检测 → 有数据先备份 `progress.db.bak` → `CREATE TABLE IF NOT EXISTS`（新表无迁移风险）。

`stage_clear` 另补一列 `tier TEXT`（该关历史最高品阶，重玩取优、只升不降），同样按「缺了才 ALTER」处理，
老库补完默认 `NULL`（表示「通关过但没有闯关品阶」），不影响任何既有进度。

### 8.4 派生子种子（必须四路独立）
```python
rng_scene = Random(f"{seed}#scene")
rng_count = Random(f"{seed}#count")
rng_diff  = Random(f"{seed}#diff")
rng_guard = Random(f"{seed}#guard")
```

> **实现备注（2026-10-24，已落地）**：四路派生结构不变（仍然是 `f"{seed}#scene"` 这四条），
> 但随机数发生器换成了项目自带的 32 位确定性 `journey.Rng`（mulberry32，种子 = 对 seed 文本做
> FNV-1a 散列），不是标准库的 `random.Random`。原因：`Random("abc")` 的种子化是 CPython 实现细节，
> JS 无法在几十行内逐位复刻，而「演示页与 CLI 用同一个 seed 抽出同一场」是验收第 8 条的硬要求。
> 换来的是两端**逐位一致**：同样的 `seed` → 同样的场景 / 题数 / 难度 / 守将，`selfcheck.py` 用
> `demo/bank.json` 里 135 条固定用例守住这条性质。
>
> **另一个实现备注**：`stage_runs.level_id` 存的是「关的键」，与既有 `stage_clear.level_id` 保持同一口径
> ——默认线是 `"3"`，其他线是 `"sanguo-3"`（而不是 `xiyou-3`）。这样老库里已有的西游进度照样参与解锁推导，
> 一个关也不丢。

---

## 九、核心逻辑模块

新增 `scripts/journey.py`（纯逻辑，不依赖网络与 UI，便于单测）：
```python
def storyline_order() -> list[str]                     # index.json 顺序中 available 的线
def is_story_unlocked(story, clear_map) -> bool
def is_stage_unlocked(story, stage, clear_map) -> bool
def draw_run(story, stage, seed, available_pool) -> RunPlan
    # RunPlan: scene_text, guard_id, difficulties[sorted], question_count, seed
def grade_tier(elapsed_ms, budget_ms, used_hint, used_skip, retry_count) -> str|None
def collect(story, char_ids, tier, clean) -> None      # 写 collection
```
- `clear_map`: `{(story, stage): {"cleared": bool, "best_tier": str|None}}`，由 `progress` 提供。
- **所有随机必须经 `draw_run`**，CLI 与 demo 共用同一份规则参数（见 9.1），避免两端各写一套。

### 9.1 规则参数外置 `data/run_rules.json`
```jsonc
{
  "question_count_weights": {"1": 30, "2": 40, "3": 30},
  "base_seconds_by_grade": {"3": 45, "4": 60, "5": 75, "6": 90},
  "per_question_cap_seconds": 600,
  "tier_thresholds": {"gold": 0.60, "silver": 1.00},
  "hint_caps_tier": "silver",
  "retry_silver_threshold": 3
}
```
CLI 与 demo **都读这个文件**（demo 由 `export_demo_bank.py` 把该 JSON 内联进去），保证两端判定完全一致。

---

## 十、CLI 交互流程

```
$ python scripts/cli.py stories                 # 显示 5 条线及解锁进度
$ python scripts/cli.py stages                  # 显示解锁/锁定/最高品阶
$ python scripts/cli.py play --level 3          # 进入第 3 关（关卡号，非 level_id）
   🔒 第 4 关尚未解锁（需先通关第 3 关）        # 未解锁时
   ── 第 3 关 · 八戒买饼 ──
   闯关大将：猪八戒（闯关引导）
   守关大将：？？？·守关者
   场景：清早，山脚下的饼铺
   本次抽到 2 道题，开始计时。
   [1/2] 题目……（输入答案，h 提示，s 跳过）
   ……
   结算：用时 96 秒 / 基准 120 秒（ratio 0.80）
   品阶：🥈 白银
   守关大将揭晓：红孩儿《手脚最快的小跑腿》
   角色卡 +2（猪八戒、红孩儿）｜介绍解锁 1 张（红孩儿）
$ python scripts/cli.py characters              # 角色图鉴
$ python scripts/cli.py characters --story xiyou --unlocked-only
```

退出/中断：计时作废，不记录，不收集。

---

## 十一、演示页交互

1. **故事线切换**：5 个印章按钮；未解锁显示锁与「需先完成《上一线》9 关」。
2. **关卡地图**：已通关（显示最高品阶徽记）/ 当前可玩（高亮）/ 未解锁（灰 + 锁）。
3. **关卡面板**：闯关大将（有名）+ 守关大将（？？？剪影）+ 场景文本 + 「本次 N 道题」+ 计时开始。
4. **答题**：逐题作答；提示、跳过按钮；答错可重试（计时继续）。
5. **结算**：用时对比基准 → 品阶徽记（金/银/铜）动画 → 守关大将揭名 → 角色卡入手动画 → 介绍解锁提示。
6. **角色图鉴**：按线分组；三种状态（未遇见 / 已收集待解锁 / 已解锁）；点击卡片看介绍。
7. **进度持久化**：`localStorage`，按 `story/level` 分桶，与 CLI 的 SQLite 逻辑一致（规则参数同源）。

---

## 十二、边界与异常

| 场景 | 处理 |
|---|---|
| 进入未解锁关卡 | CLI 报错 exit=2；demo 按钮禁用 |
| 切换未解锁故事线 | 同样拦截，并提示还差几关 |
| **旧接口 `play --level <关>-<难度>`** | **有意放行，不受解锁链约束**。它是 v1 的单题接口，为保持 README 示例与历史用法可用而保留；新玩法（`--level <关号>` / `--story + --stage`）才走解锁校验。这是兼容性取舍，非漏洞 |
| 一关反复重玩 | 允许；品阶取历史最优；介绍解锁后不回锁 |
| 中途退出 | run 作废，不计时不计收集 |
| 单题超 600 秒 | 按 600 秒计入 budget 比 |
| 跳过某题 | 该 run 无品阶、不收集；但 `stage_clear` 仍记录通关（否则会卡死孩子） |
| 全部通关后 | 显示「全部取经完成」总结页：总角色数、各品阶数量、建议重玩的高价值关卡 |
| 数据源缺失 | 沿用三级降级（mcp → cache → sample），不影响玩法 |

---

## 十三、验收标准（机器可断言）

1. **解锁链**：`is_stage_unlocked` 对「未通关前序」返回 False；通关 k-1 后 k 为 True；`stage_runs` 有记录但 `stage_clear` 无记录时仍为 False。
2. **故事线链**：`xiyou` 全 9 关通关前，`sanguo` 为 False；全通关后为 True。
3. **随机性**：同一 `(story, stage)` 用 1000 个不同 seed，场景覆盖全部 10 个变体；题数分布接近 30/40/30（±5%）；难度档组合无重复档位；守将 5 名均出现过。
4. **可复现**：同一 seed 两次 `draw_run` 结果完全一致（场景、题数、难度、守将）。
5. **品阶**：构造 `ratio` 边界 0.59/0.60/0.61/1.00/1.01 五例，判定分别 gold/gold/silver/silver/bronze；`used_hint` 使 gold 降为 silver；`used_skip` 返回 None。
6. **收集**：通关后 `collection` 出现闯关大将与本场守将各 1 条；重玩品阶更低时 `best_tier` 不下降。
7. **介绍解锁**：干净通关 → `intro_unlocked=1`；有提示或有重试 → 为 0 且 hint 正确；之后干净重玩 → 变 1 且不回退。
8. **两端一致**：CLI 与 demo 用同一 `run_rules.json`；同一 seed 下两端抽到的场景/题数/难度/守将完全一致（由 `export_demo_bank` 导出固定用例比对）。
9. **不阻断旧功能**：`play --level 3-2 --seed demo` 仍出 `27.00`；`selfcheck.py` 仍 exit=0；README 示例同步更新后由 selfcheck 兜住。
10. **红线**：70 个角色介绍与 50 条场景修饰语，grep 无暴力/迷信/政治敏感/竞品/消费引导词；介绍须含数学细节。

---

## 十四、对现有代码的改动清单

| 文件 | 改动 |
|---|---|
| `data/storylines/*.json` | 每线加 `order` 与 `scene_variants`（10 条） |
| `data/characters/*.json` | 新增 5 个文件，共 70 名角色 |
| `data/run_rules.json` | 新增，规则参数外置 |
| `scripts/journey.py` | 新增，解锁/抽取/品阶/收集纯逻辑 |
| `scripts/characters.py` | 新增，角色加载与解锁判定 |
| `scripts/progress.py` | 新增 `stage_runs`、`collection` 两表与读写函数 |
| `scripts/cli.py` | `stories` / `characters` 子命令；`play` 改为按关号进入、计时、结算、收集 |
| `scripts/problem_gen.py` | 支持按指定 difficulty 生成（现有已支持，确认接口） |
| `scripts/export_demo_bank.py` | 导出增加：角色库、场景变体、`run_rules`、固定 seed 用例 |
| `scripts/selfcheck.py` | 增加第 13 节的 1/2/4/5/6/7/8 条断言 |
| `demo/index.html` | 解锁态、计时、结算、角色图鉴、品阶徽记 |
| `README.md` / `SKILL.md` | 玩法升级说明；`405 个关卡位` 口径保持；补解锁与收集规则 |
| `docs/SYSTEM_RULES.md` | 本文件 |

---

## 十五、实施优先级与止损

| 级 | 内容 | 未完成的后果 |
|---|---|---|
| **P0** | `journey.py` 规则引擎 + `run_rules.json` + 角色数据（70 名）+ `scene_variants` + DB 两表 | 无法交付新玩法 |
| **P1** | demo 解锁态 + 计时 + 结算 + 角色图鉴 | 演示页看不到新玩法（**门面损失最大**） |
| **P2** | CLI 集成（`stories` / `characters` / `play` 改造） | 命令行仍是旧流程 |
| **P3** | selfcheck 新断言 + 文档更新 | 质量防线变弱 |

**止损点：2026-10-24 24:00。** 到点未完成的部分按上表顺序保留已完成项：若 P1 未完成，demo 回到 `e45358e` 版本（新玩法只在 CLI 生效），并在 README 注明「演示页版本可能滞后」——不允许出现 demo 半坏的状态。
