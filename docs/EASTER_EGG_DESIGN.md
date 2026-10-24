# 麦麦取经录 · 移动端随机彩蛋惊喜功能 · 完整落地方案

> 本文档为设计稿，参数以 `data/run_rules.json` 的 `easter_egg` 段为准
> 版本：v1.0 ｜ 日期：2026-10-25 ｜ 状态：设计稿（待评审）
> 适用范围：`demo/index.html`（单文件离线演示页，零外部依赖）及后续同构移动端页面
> 关联文档：`docs/SYSTEM_RULES.md`（闯关规则唯一真相源）、`README.md` 第九节（数据与隐私）

---

## 〇、设计原则（先于一切细节）

| # | 原则 | 落地含义 |
|---|---|---|
| P1 | **惊喜优先于打扰** | 一切频率/冷却/防重复规则，服务于「每次出现都是真惊喜」，不是指标 |
| P2 | **零网络依赖** | 彩蛋全部资源内联（CSS 动画 + WebAudio 合成音 + SVG），弱网/离线 100% 可用——与项目「无令牌可离线跑」同源 |
| P3 | **零儿童数据外传** | 所有计数/去重状态只写本机 `localStorage`，不上传、不合并、不做用户画像——延续 README 第九节承诺 |
| P4 | **诚实降级** | 数据类彩蛋（D 类）无真实数据源时整体禁用，绝不用编造的数字——延续「宁可降级，不编数字」 |
| P5 | **配置外置** | 全部参数进 `data/run_rules.json` 的 `easter_egg` 段，与闯关规则同一份唯一真相源，不发散在代码里 |
| P6 | **不碰红线** | 彩蛋文案过 `selfcheck.py` 的 `RED_LINE_RE` 红线词表；不引导消费、不写法术/神力、无宗教表述；音画无频闪（防光敏） |

---

## 一、触发机制（三种触发源 + 优先级 + 互斥）

### 1.1 触发源定义

| 编号 | 名称 | 触发条件 | 设计意图 | 频次上限 |
|---|---|---|---|---|
| **T1** | 隐藏区域点击 | 关卡地图页右下角「悟空金箍棒」 beacon（半透明 12% → 2s 呼吸一次），**800ms 内点击 3 次** 或 **长按 800ms** | 奖励「爱探索」的孩子——把惊喜变成可发现的秘密，制造社群传播话题（「你找到了吗」） | 每张地图会话 1 次 |
| **T2** | 任务/操作完成 | 挂 7 个钩子（见 1.2） | 把惊喜钉在「成就时刻」上，强化正向循环 | 不冷却（任务驱动，天然低频） |
| **T3** | 随机时间间隔 | 页面可见 **且** 60s 内有交互（排除挂机）；进入答题/地图页后 `random(90, 180)s` 掷一次 | 不可预期的温柔打扰——只在孩子「正在玩」时出现，不打断离开状态 | 180s 频道冷却 |

### 1.2 T2 的 7 个任务钩子

| 钩子 | 触发时机 | 权重 |
|---|---|---|
| 首次通关某关 | `stage_runs` 该关首次出现通关记录 | 90 |
| 干净通关 | 不看提示、不答错、不跳过（沿用现有判定） | 95 |
| 集满一条线 14 名角色 | collection 该线 14/14 | 95 |
| 连对 5 题 | 单次 run 内连续 correct | 80 |
| 黄金品阶且 ratio ≤ 0.40 | 结算时 | 85 |
| 答错后首次答对 | 错题转正确的当次（鼓励瞬间） | 75 |
| 连续 3 天打开 | 本地日期差 == 3 天 | 90 |

### 1.3 优先级与互斥规则

```
优先级分值：  T1 = 100   >   T2 = 75~95   >   T3 = 50
```

| 规则 | 内容 |
|---|---|
| R1 全局互斥 | `PRESENTING` 期间**不评估**任何新触发，只入队；互斥锁保证同时最多 1 个彩蛋 |
| R2 队列容量 | 队列 ≤ 3。入队时已满：丢弃**优先级最低且最旧**的一条（T3 最先被丢） |
| R3 插队 | T1 可插到队首（用户显式意图最高）；T2 插在全部 T3 之前、T1 之后 |
| R4 入场防误触 | `ENTERING` 后 **0.8s 内忽略一切关闭类输入**，且 T1 的连点计数在 0.8s 内不累计 |
| R5 不打断互动 | `INTERACTIVE` 进行中的迷你互动，关闭请求排队到互动自然结束——**任何情况不强行中断孩子的操作** |
| R6 同源抑制 | 同一 `egg_id` 在队列中已存在时，新触发直接记 `suppressed`（不进队、不计数） |

### 1.4 触发评估伪代码

```javascript
function onTriggerEvent(evt) {              // evt: {source:'T1'|'T2'|'T3', task?:string}
  if (!cfg.enabled) return;                 // 家长总开关
  if (state !== 'IDLE') { enqueue(evt); return; }   // R1：只入队
  const score = PRIORITY[evt.source] + (evt.task ? TASK_WEIGHT[evt.task] : 0);
  if (!passCooldown(evt)) { log('suppressed', evt, 'cooldown'); return; }
  if (overDailyCap())       { log('suppressed', evt, 'daily_cap'); return; }
  enqueue(evt, score);                      // R2/R3 在 enqueue 内裁决
  pumpQueue();
}

function pumpQueue() {
  if (state !== 'IDLE' || queue.length === 0) return;   // 互斥锁
  const item = queue.sort(byScoreDesc)[0];              // R3 插队结果
  const egg  = pickContent(item);                       // 第三/四章：内容抽取 + 去重
  if (!egg) { queue.shift(); return; }                  // 池子被去重规则掏空 → 放弃
  transition('ENTERING', { egg });
}
```

---

## 二、惊喜内容设计（四类内容池 · 活泼可爱 · 真实惊喜）

### 2.1 内容池总览（共 24 个槽位，权重轮转）

| 类别 | 数量 | 呈现形式 | 时长 | 说明 |
|---|---|---|---|---|
| **A 角色探头** | 12 | 角色从屏边探出（spring 动画）→ 孩子 poke 一下 → 挤压回弹 + 一句台词 | 3~6s | 台词**复用现有 70 段角色介绍**（每段 60~150 字含数学小细节），不新造文案 |
| **B 庆祝时刻** | 4 | 金粉纸屑 + 品阶徽记「盖章」+ 筋斗云横向扫过 | 1.5~2.5s | 叠加层，**不占互斥锁**，结算页自动附加 |
| **C 迷你互动** | 4 | 见 2.2 | ≤30s | 唯一占用「互动态」的类别 |
| **D 知识卡** | 4 | 「原来如此」卡片：一条真实数据小知识 | 4~8s | 数据必须来自 MCP 缓存/示例快照；**无数据源时该类整体禁用**（P4） |

### 2.2 C 类迷你互动（只做单击 + 拖动两种手势）

| 名称 | 玩法 | 数学点 | 手势 |
|---|---|---|---|
| 悟空抢答 | 屏中弹出 1 道 10 秒口算题，两个大按钮选项 | 巩固当前关知识点 | 单击 |
| 祥云翻牌 | 3 朵云翻 1 张，翻开是角色卡碎片 + 一句台词 | 收集期待 | 单击 |
| 接仙桃计数 | 5 秒内点掉落仙桃，落地后问「一共几个」 | 数感 | 单击 |
| 拼徽记 | 拖动半枚徽记与另一半重合，合成金/银/铜 | 形状/空间 | 单指拖动 |

### 2.3 音效方案（WebAudio 实时合成，零音频文件）

| 音效 | 合成方式 | 时长 | 用途 |
|---|---|---|---|
| 「叮」 | 正弦 880→1320Hz 滑音 | 120ms | 彩蛋出现 |
| 「噗」 | 三角波 300Hz 短促 + 快速衰减 | 80ms | poke 角色 |
| 上升琶音 | C-E-G-C 四音，每音 90ms | 360ms | 迷你互动成功 |
| 轻「嘘」 | 白噪声低通滤波衰减 | 150ms | 关闭 |

约束：主增益 ≤ 0.25；**首次用户交互后才创建/恢复 AudioContext**（iOS/Android 自动播放策略）；静音状态持久化到本地存储。

### 2.4 文案红线

- 全部彩蛋文案（台词/知识卡/按钮文案）进内容池 JSON，随 `selfcheck.py` 的 `RED_LINE_RE` 逐条扫描，命中即 FAIL；
- 不出现：其他餐饮品牌、消费引导（「快去买」）、法术/神力/显灵、宗教表述；
- 统一口尾：讲解/知识卡结尾固定「适量、均衡搭配，不浪费。」（与现有题目讲解一致）；
- 统一声明：D 类知识卡页脚带「非麦当劳官方产品，信息以官方渠道为准」。

---

## 三、频率与防重复（双层机制）

### 3.1 频率上限（硬顶 + 软顶）

| 维度 | 规则 |
|---|---|
| 单次闯关 run | ≤ 2 个彩蛋（B 类庆祝叠加层不计） |
| 单日（自然日） | **硬顶 5 个**；超出后当日只保留 T1（隐藏区域是孩子主动发现的，不该被限额惩罚） |
| 全局冷却 | 任意两个彩蛋间隔 ≥ 90s |
| 频道冷却 | T1：120s；T2：无；T3：180s |
| 迷你互动 | 同一互动 24h 内 ≤ 1 次；单日内 C 类 ≤ 2 次 |

### 3.2 防重复机制（全局维度 + 单用户维度）

**全局维度（内容池运营层）**——内容池 JSON 集中管理：

```json
{
  "easter_egg": {
    "enabled_default": true,
    "daily_hard_cap": 5,
    "per_run_cap": 2,
    "global_cooldown_s": 90,
    "channel_cooldown_s": { "T1": 120, "T2": 0, "T3": 180 },
    "enter_ignore_input_ms": 800,
    "minimize_auto_close_s": 60,
    "pool": [
      { "id": "A_wukong_poke", "type": "A", "weight": 10, "char": "wukong",
        "line": "俺老孙数过，这山门石阶一共三百六十级。" },
      { "id": "C_cloud_flip", "type": "C", "weight": 8, "mini": "cloud_flip" }
    ]
  }
}
```

- `id` 全局唯一（`selfcheck` 断言）；
- 新内容只改 JSON，不动代码；支持权重下发做灰度。

**单用户维度（本地层）**——环形缓冲区 + 权重衰减：

```javascript
// 本地存储结构（单设备、不上传）
eggLog = [ { id, ts, channel, interacted } ]   // 保留最近 200 条

function pickContent(item) {
  const now = Date.now();
  const pool = cfg.pool.filter(e =>
    !seenWithin(e.id, 24 * 3600e3) &&            // 同一 egg 24h 不重复
    !charUsedWithin(e.char, 72 * 3600e3) &&      // 同一角色 72h 不做主角
    (e.type !== 'D' || dataAvailable)            // P4：无数据源禁用 D 类
  );
  if (!pool.length) return null;                 // 诚实放弃，不硬凑
  return weightedPick(pool.map(e => ({
    ...e,
    w: e.weight * freshness(e.id, now)           // 新鲜度权重
  })));
}
function freshness(id, now) {                    // 兴趣衰减保护
  const last = lastSeen(id);
  if (!last) return 1.0;
  const days = (now - last) / 86400e3;
  return days < 7 ? 0.3 : 1.0;                   // 7 天内见过的权重打三折
}
```

### 3.3 兴趣衰减保护（防「惊喜变噪音」）

| 信号 | 响应 |
|---|---|
| 连续 3 个彩蛋在 3s 内无任何互动 | T3 频道当日关闭（T1/T2 不受影响） |
| 孩子主动关闭彩蛋 ×2 | 当日 T3 关闭；记录「偏好偏低」，次日恢复 |
| 迷你互动成功率达 80%+ | C 类权重 +20%（顺手的内容自适应） |
| 同一故事线内 A 类已看完 | 自动切换到其他线的角色 |

---

## 四、交互与视觉适配（儿童友好 + 移动端）

### 4.1 儿童交互规范

| 项 | 规范 | 理由 |
|---|---|---|
| 点击目标 | ≥ 64×64dp（按钮/角色/选项） | 儿童指尖控制精度低，超出成人 48dp 指引 |
| 关闭按钮 | ≥ 56dp，四周 12dp 透明扩区 | 关得掉，但不被误关 |
| 容器外点击 | **不关闭**彩蛋（只能点「知道啦」或下滑） | 防误触流失惊喜 |
| 手势 | 仅两种：单击、单指拖动；**不做**双指/长按（T1 触发自身除外）/复杂滑动 | 认知负荷最小化 |
| 反馈延迟 | 触点到视觉反馈 ≤ 100ms（`pointerdown` 即响应） | 孩子对延迟极敏感 |
| 文字 | 正文 ≥ 18px，按钮文字 ≥ 20px；一句话 ≤ 20 字 | 独立可读，不依赖家长 |

### 4.2 视觉规范

| 项 | 规范 |
|---|---|
| 形态 | 全圆角：卡片 ≥ 16px，按钮胶囊形，角色用「印章+名字+几何剪影」（与现有演示页一致，不含人物立绘） |
| 配色 | 暖橙 `#FF8A3D` 主色 + 米白 `#FFF8F0` 底 + 朱红 `#D6452F` 点缀 + 黛蓝 `#2B4C7E` 文字（与已发布封面图同 palette） |
| 动画 | 200~400ms，`ease-out`；spring 用 CSS `cubic-bezier(.34,1.56,.64,1)`（回弹一次即收） |
| 动效降级 | `prefers-reduced-motion: reduce` → 全部降为 150ms 淡入淡出 |
| 光敏安全 | 任何元素闪烁频率 ≤ 3Hz；无高对比频闪；无全屏白光爆闪 |
| 音画同步 | 音效与动画同帧触发（WebAudio `currentTime` 对齐 rAF） |

### 4.3 移动端适配矩阵（主流机型/屏幕/系统特性）

| 场景 | 问题 | 方案 |
|---|---|---|
| iPhone 刘海/底部横条 | 彩蛋被裁 | 容器 `padding: env(safe-area-inset-*)`，关闭按钮避开底部 34pt |
| iOS Safari 100vh | 工具栏导致高度跳动 | 全链路 `dvh` 单位；彩蛋卡 `max-height: min(70dvh, 560px)`，内容超长内部滚动 |
| 小屏（≤360px 宽） | 按钮挤压 | `@media (max-width: 640px)`（复用现有断点）：全宽卡片 margin 16px，双按钮纵向堆叠 |
| 横屏 | 遮挡题干 | 卡片宽 `min(480px, 86vw)` 居中，绝不覆盖题目区域；横屏时 C 类暂停计时 |
| 触屏 hover | 桌面 hover 态「粘滞」 | 所有 hover 效果包在 `@media (hover: hover)` 内 |
| Android 返回键 | 直接退出页面 | `history.pushState` 占位 + `popstate` → 关闭彩蛋并清理占位（**不累积历史记录**） |
| iOS 橡皮筋 | 彩蛋内滚动带崩背景 | 彩蛋层 `overscroll-behavior: contain` |
| 自动播放策略 | 音效被系统静音 | AudioContext 在首次 `pointerdown` 时创建/`resume()` |
| 低端机（Android Go/旧 iPhone） | 纸屑动画掉帧 | 运行时 FPS 采样：连续 2s < 30fps → 自动降级静态卡片 + 淡入（记入本地日志，不打扰用户） |
| 系统版本底线 | — | iOS 13+ / Android 8+（`dvh`、`overscroll-behavior` 均有降级：不支持时回退 `vh`/`hidden`） |

---

## 五、完整行为逻辑与状态机

### 5.1 状态定义（对应上图）

| 状态 | 含义 | 进入动作 | 退出条件 |
|---|---|---|---|
| `IDLE` | 待机监听，全局冷却计时运行 | 挂载三类监听器 | 任意触发事件 |
| `ARMED` | 触发到达，做优先级/冷却/限额裁决 | 计分 → 通过则入队 | 通过 → `QUEUED`；否决 → 记 `suppressed` 回 `IDLE` |
| `QUEUED` | 排队等槽位（≤3，溢出丢最低最旧） | 排序；监听互斥锁 | 槽位空闲 → `ENTERING` |
| `ENTERING` | 入场动画 300ms；0.8s 防误触忽略窗 | 播「叮」；启动忽略窗 | 动画结束 → `PRESENTING` |
| `PRESENTING` | 展示中，持有互斥锁 | 分支 INTERACTIVE/PASSIVE | 关闭/返回键/下滑 → `CLOSING`；页面切换 → `MINIMIZED` |
| `INTERACTIVE` | 迷你互动进行中 | 锁定滚动、启动互动计时 | 互动自然结束 → `CLOSING`（关闭请求此时才生效，R5） |
| `PASSIVE` | 被动展示（A/B/D 类） | 自动播放计时 | 同上 |
| `MINIMIZED` | 页面切换收起为右下气泡 | 气泡呼吸动画；60s 倒计时 | 点气泡 → 回 `PRESENTING`；超时 → `CLOSING`（记「已避闭」，不计互动） |
| `CLOSING` | 退场 200ms | 播轻「嘘」；不打断进行中互动（R5） | 动画结束 → `RECORDING` |
| `RECORDING` | 写环形缓冲、计数、冷却起点 | 落盘本地存储 | 完成 → `IDLE` |

### 5.2 迁移表（事件 → 动作）

| 当前状态 | 事件 | 动作 |
|---|---|---|
| IDLE | `trigger(T*)` | → ARMED |
| ARMED | `pass` / `reject` | → QUEUED / 记 suppressed → IDLE |
| QUEUED | `slot_free` | → ENTERING |
| ENTERING | `enter_done` | → PRESENTING |
| ENTERING | `close_tap`（<0.8s） | **忽略**（R4） |
| PRESENTING | `close_tap` / `swipe_down` / `popstate` | → CLOSING |
| PRESENTING | `route_change` | → MINIMIZED |
| INTERACTIVE | `close_tap` | 入待处理队列，互动结束后才执行 |
| INTERACTIVE | `interact_done` | → CLOSING |
| MINIMIZED | `bubble_tap` | → PRESENTING（恢复现场） |
| MINIMIZED | `timeout 60s` | → CLOSING（标记 avoidClosed） |
| CLOSING | `exit_done` | → RECORDING |
| RECORDING | `write_done` | → IDLE（启动全局冷却 90s） |

### 5.3 关键节点伪代码

```javascript
// —— 页面切换：收起为气泡，不丢惊喜 ——
router.on('before:navigate', () => {
  if (state === 'PRESENTING' || state === 'INTERACTIVE') {
    pauseTimers(); renderBubble(currentEgg); transition('MINIMIZED');
  }
});

// —— Android 返回键：关彩蛋，不退出页面，不累积历史 ——
history.pushState({ eggGuard: true }, '');
window.onpopstate = () => {
  if (state === 'PRESENTING' || state === 'MINIMIZED') {
    transition('CLOSING');
    if (history.state?.eggGuard) history.back();   // 清理占位
  }
};

// —— 后台/切页：暂停随机触发，回来不补报（防爆发）——
document.onvisibilitychange = () => {
  if (document.hidden) { t3Timer.pause(); if (state==='INTERACTIVE') pauseTimers(); }
  else { t3Timer.resume(); }        // 注意：resume 不补掷，剩余时间继续走
};

// —— 记录与衰减 ——
function record(egg, meta) {
  eggLog.push({ id: egg.id, ts: Date.now(), channel: meta.channel,
                interacted: meta.interacted });
  if (eggLog.length > 200) eggLog.splice(0, eggLog.length - 200);
  persist('eggLog', eggLog);
  if (!meta.interacted) noInteractStreak++; else noInteractStreak = 0;
  if (noInteractStreak >= 3) disableChannelToday('T3');   // 3.3 衰减保护
}
```

---

## 六、边界场景处理策略

| # | 场景 | 处理策略 |
|---|---|---|
| E1 | **连续触发**（队列满 / T1 插队 / ENTERING 期间又来） | R2 丢最低最旧；R3 T1 插队首；ENTERING 0.8s 窗内 T1 连点不累计（R4）。任何情况不并行展示 |
| E2 | **页面切换**（故事线切换/答题页跳转/SPA 路由） | 收起为右下气泡，点击可回看现场；60s 未回看自动关闭并记「已避闭」——**惊喜不丢，也不强制留存** |
| E3 | **弱网/离线** | 彩蛋 100% 本地资源，零请求；D 类知识卡按 P4 三级降级：MCP 缓存 → 示例快照 → **禁用该类**（界面不出现半截卡片） |
| E4 | **切后台/锁屏** | 暂停 T3 计时与互动计时；回来**不补掷**（防 burst）；互动暂停时进度保留，超 60s 未回按放弃计（不计错） |
| E5 | **刷新/杀进程** | 会话级状态（当前彩蛋、队列）不恢复——刷新即回到 `IDLE`；已写入的记录不丢（本地存储已落盘） |
| E6 | **低端机/低电量** | FPS 采样自动降级（纸屑→静态卡）；低电量时不主动降体验（检测成本高于收益），仅保留动效降级 |
| E7 | **光敏/动效敏感儿童** | `prefers-reduced-motion` 全量降级；无频闪；音效可一键静音；家长开关可整体关闭 |
| E8 | **多设备同一孩子** | 本地状态按设备独立，不合并、不同步——隐私红线（P3）；文档如实告知「进度与彩蛋记录仅存本机」 |
| E9 | **内容池被去重规则掏空** | 诚实放弃本次触发（记 `suppressed: pool_exhausted`），**不重复播放旧内容凑数** |

---

## 七、集成方案（与现有架构的接法）

### 7.1 改动面（最小侵入）

| 文件 | 改动 | 风险 |
|---|---|---|
| `data/run_rules.json` | 新增 `easter_egg` 配置段（P5 配置外置） | 低——新增键不影响既有读取方 |
| `demo/index.html` | 新增 `<div id="egg-layer">` + 独立 IIFE 模块（`EggEngine`，不污染全局）；挂到现有结算/地图/答题三个页签的钩子上 | 中——需回归演示页既有流程 |
| `scripts/export_demo_bank.py` | 若题库导出整体注入 `run_rules.json`，`easter_egg` 段自动随行；**彩蛋是纯前端运行时逻辑，不进 bank.json 题库**，不影响 405 题三方比对 | 低 |
| `scripts/selfcheck.py` | 新增断言：① `easter_egg` 必备键完整；② 内容池 `id` 唯一、`type` 合法；③ 全部文案过 `RED_LINE_RE` | 低——纯新增断言 |
| `scripts/cli.py` | **不涉及**——彩蛋是演示页/前端特性，CLI 边界不变 | 无 |

### 7.2 数据流

```
触发源(T1/T2/T3) → EggEngine.evaluate → 裁决(优先级/冷却/限额)
   → 队列 → pickContent(全局池 × 本地去重 × 新鲜度权重)
   → 状态机(IDLE→…→RECORDING) → 渲染层(egg-layer)
   → 记录(本地环形缓冲) → 冷却计时
```

### 7.3 与大赛时间线的关系（重要）

- 本方案为**设计稿**：明晚 21:00 仓库冻结前，**不建议**动演示页代码（回归风险 > 收益）；
- 冻结期后（10-26 起）按第八章分期落地，每期独立提交 + `selfcheck` 全绿才推；
- 若评委主动问起彩蛋规划，可将本文档作为「路线图」随答——**先设计后实现**本身是工程纪律的展示。

---

## 八、落地计划（冻结期后执行）

| 期 | 范围 | 工作量 | 验收标准 |
|---|---|---|---|
| P0 | 状态机骨架 + T1/T3 + A/B 类被动内容 + 音效 | 1 天 | `selfcheck` 全绿；手动三触发各通一次；离线可用 |
| P1 | T2 七钩子 + 队列/插队 + 频率与去重 + 衰减保护 | 1 天 | 单日硬顶/全局冷却/24h·72h 去重单测通过 |
| P2 | C 类四个迷你互动 + 动效降级 + 低端机 FPS 降级 | 1.5 天 | 四种互动可玩可退；reduced-motion 降级验证 |
| P3 | 家长开关 + `MINIMIZED` 气泡 + 边界场景 E1~E9 回归 | 0.5 天 | E1~E9 全过；隐私自查（零上传）复核 |

---

## 附：一页纸摘要（给评审/家长看的版本）

- **三种触发**：找隐藏的金箍棒（T1）、达成成就（T2）、玩着玩着自己出现（T3）——优先级 T1>T2>T3，同时只出现一个，最多排 3 个队
- **四种惊喜**：角色探头 poke、庆祝纸屑、30 秒迷你互动、真实数据知识卡——音效全部现场合成，离线也能出
- **不打扰**：一次闯关最多 2 个、一天最多 5 个、两次之间至少歇 90 秒、同一惊喜 24 小时内不重复、连续 3 次不理它就安静一天
- **孩子好操作**：按钮比成人 App 大一圈、关彩蛋只能点「知道啦」或下滑（防误触）、手势只有单击和拖动
- **手机全适配**：刘海屏、横屏、Android 返回键、旧机型自动降级——全部处理在方案内
- **隐私**：彩蛋记录只存本机，不上传、不合并——和项目对儿童数据的承诺一致
