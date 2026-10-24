/* =====================================================================
 * 麦麦取经录 · 彩蛋引擎 Node 单测（scripts/egg_engine_test.js）
 * ---------------------------------------------------------------------
 * 驱动 demo/egg-engine.js 的纯逻辑层（createEngine），覆盖：
 *   触发优先级/互斥/队列、防误触窗、冷却、日上限/单局上限、
 *   24h·72h 去重与池掏空、兴趣衰减、状态机全流程、
 *   气泡收起/回看/超时、互动中关闭延迟、T3 空闲豁免、切后台不补掷、
 *   家长总开关、跨日重置。
 * 运行：node scripts/egg_engine_test.js   （退出码非 0 即失败）
 * ===================================================================== */
"use strict";

var path = require("path");
var engineApi = require(path.join(__dirname, "..", "demo", "egg-engine.js"));

/* ---------- 测试夹具 ---------- */
function makeEnv(overrides) {
  var opts = overrides || {};
  var clock = { now: 1700000000000 };
  var store = new Map();
  var calls = [];
  var interaction = { at: clock.now };
  var active = { on: true };
  var seed = 42;
  function rng() {                       /* 确定性 LCG，保证可复现 */
    seed = (seed * 1103515245 + 12345) % 2147483648;
    return seed / 2147483648;
  }
  var env = {
    cfg: opts.cfg || {
      daily_hard_cap: 5, per_run_cap: 2, global_cooldown_s: 90,
      channel_cooldown_s: { T1: 120, T2: 0, T3: 180 },
      t3_interval_range_s: [90, 180], t3_idle_grace_s: 60,
      enter_ignore_input_ms: 800, minimize_auto_close_s: 60, queue_cap: 3,
      no_interact_streak_limit: 3, seen_window_h: 24, char_window_h: 72,
      freshness_days: 7, freshness_weight: 0.3, streak_target: 5, line_size: 14,
      gold_fast_ratio: 0.4, dash_streak_days: 3,
      task_weights: { first_clear: 90, clean_run: 95, line_complete: 95, streak5: 80, gold_fast: 85, recover: 75, dash3: 90 },
      type_weights: { A: 10, C: 10, D: 6 },
      pool: opts.noC ? [] : [
        { id: "C_cloud_flip", type: "C", weight: 10, mini: "cloud_flip", title: "祥云翻牌" },
        { id: "C_wukong_quiz", type: "C", weight: 10, mini: "wukong_quiz", title: "悟空抢答" },
        { id: "C_peach_catch", type: "C", weight: 8, mini: "peach_catch", title: "接仙桃计数" },
        { id: "C_badge_match", type: "C", weight: 8, mini: "badge_match", title: "拼徽记" }
      ]
    },
    chars: function () {
      return opts.chars || [
        { id: "xiyou:wukong", name: "孙悟空", title: "齐天大圣", intro: "俺老孙数过，这山门石阶一共三百六十级。" },
        { id: "xiyou:bajie", name: "猪八戒", title: "净坛使者", intro: "一摞十张，数摞比数张快。" },
        { id: "xiyou:shaseng", name: "沙悟净", title: "金身罗汉", intro: "担子里有几件，按件数不按重量算。" },
        { id: "xiyou:tangseng", name: "唐僧", title: "旃檀功德佛", intro: "一日走三十里，十日便是三百里。" }
      ];
    },
    now: function () { return clock.now; },
    rng: rng,
    isActive: function () { return active.on; },
    lastInteractionAt: function () { return interaction.at; },
    storage: {
      get: function (k) { return store.has(k) ? store.get(k) : null; },
      set: function (k, v) { store.set(k, String(v)); }
    },
    audio: { play: function (n) { calls.push("audio:" + n); } },
    log: function () {},
    render: {
      enter: function (egg) { calls.push("enter:" + egg.id); },
      present: function (egg) { calls.push("present:" + egg.id); },
      exit: function (egg, r) { calls.push("exit:" + r); },
      minimize: function (egg) { calls.push("minimize"); },
      restore: function (egg) { calls.push("restore:" + egg.id); },
      confetti: function (t) { calls.push("confetti:" + t); },
      deferHint: function () { calls.push("deferHint"); }
    }
  };
  return {
    env: env, calls: calls, clock: clock, store: store, active: active, interaction: interaction,
    advance: function (ms) { clock.now += ms; },
    touch: function () { interaction.at = clock.now; }
  };
}

/* ---------- 断言框架 ---------- */
var passed = 0, failed = 0, failures = [];
function check(name, cond, detail) {
  if (cond) { passed += 1; console.log("  PASS " + name); }
  else {
    failed += 1;
    failures.push(name + (detail ? " | " + detail : ""));
    console.log("  FAIL " + name + (detail ? " | " + detail : ""));
  }
}
function section(t) { console.log("\n== " + t + " =="); }

/* ---------- 场景 ---------- */
function scenario(name, fn) {
  section(name);
  try { fn(); } catch (err) {
    failed += 1;
    failures.push(name + " 抛异常: " + (err && err.message));
    console.log("  FAIL " + name + " 抛异常: " + (err && err.message));
  }
}

/* 1. T1 触发 → 完整状态机流程 */
scenario("T1 触发与状态机全流程", function () {
  var h = makeEnv();
  var e = engineApi.createEngine(h.env);
  e.start();
  check("初始为 IDLE", e.state() === "IDLE");
  var r = e.evaluate({ source: "T1" });
  check("T1 立即进入入场", e.state() === "ENTERING", "state=" + e.state() + " r=" + r);
  h.advance(300); e.tick();
  check("300ms 后进入展示", e.state() === "PRESENTING" || e.state() === "INTERACTIVE", "state=" + e.state());
  check("渲染器收到 enter", h.calls.some(function (c) { return c.indexOf("enter:") === 0; }));
  var cr = e.close("button");
  check("关闭进入退场", cr === "closing" && e.state() === "CLOSING");
  h.advance(200); e.tick();
  check("退场后回到 IDLE", e.state() === "IDLE");
  check("退出动画已调用", h.calls.some(function (c) { return c.indexOf("exit:") === 0; }));
  check("今日计数 = 1", e.todayCount() === 1);
});

/* 2. 互斥 + 队列 + 插队 + 容量 */
scenario("互斥锁、队列容量与 T1 插队", function () {
  var h = makeEnv();
  var e = engineApi.createEngine(h.env);
  e.start();
  e.evaluate({ source: "T2", task: "first_clear" });   /* 先占槽 */
  var busy = e.state();
  check("占用中状态非 IDLE", busy !== "IDLE");
  var r1 = e.evaluate({ source: "T3" });
  check("忙碌期间新触发只入队", r1 === "queued" && e.queueSize() === 1);
  var r2 = e.evaluate({ source: "T1" });
  check("T1 也入队（不并行展示）", r2 === "queued" && e.queueSize() === 2);
  e.evaluate({ source: "T2", task: "clean_run" });
  e.evaluate({ source: "T3" });
  check("队列容量 ≤ 3", e.queueSize() <= 3, "size=" + e.queueSize());
  var snap = e.snapshot();
  check("T1 排在队首", snap.queue[0].source === "T1", JSON.stringify(snap.queue.map(function (q) { return q.source; })));
});

/* 3. 防误触窗 */
scenario("入场 0.8s 防误触忽略窗", function () {
  var h = makeEnv();
  var e = engineApi.createEngine(h.env);
  e.start();
  e.evaluate({ source: "T1" });
  h.advance(100);
  var early = e.close("button");
  check("0.8s 内关闭被忽略", early === "ignored" && e.state() === "ENTERING");
  h.advance(800); e.tick();
  check("超过忽略窗后可关闭", e.close("button") === "closing");
});

/* 4. 冷却 */
scenario("全局与频道冷却", function () {
  var h = makeEnv();
  var e = engineApi.createEngine(h.env);
  e.start();
  e.evaluate({ source: "T1" });
  h.advance(300); e.tick();
  e.close("button");
  h.advance(200); e.tick();
  var again = e.evaluate({ source: "T1" });
  check("刚关闭立即再触发被抑制", again.indexOf("suppressed:") === 0, again);
  h.advance(91000);
  var mid = e.evaluate({ source: "T1" });
  check("91s 过全局冷却但未过 120s 频道冷却", mid.indexOf("suppressed:channel_cooldown") === 0, mid);
  h.advance(30000);
  var ok = e.evaluate({ source: "T1" });
  check("121s 后可再触发", ok === "queued" || e.state() === "ENTERING", ok);
});

/* 5. 频率上限：日硬顶 + 单局 */
scenario("日硬顶 5 与单局上限 2", function () {
  var h = makeEnv({ noC: true });      /* 只要 A/D 类，保证关闭立即可用 */
  var e = engineApi.createEngine(h.env);
  e.start();
  for (var i = 0; i < 2; i++) {
    e.evaluate({ source: "T2", task: "first_clear" });
    h.advance(300); e.tick();
    e.close("button");
    h.advance(200); e.tick();
    h.advance(100000);           /* 跳过全局冷却 */
  }
  check("单局第 3 个被抑制", e.evaluate({ source: "T2", task: "first_clear" }).indexOf("suppressed:run_cap") === 0);
  e.onSettle({ firstClear: false });   /* 一局结束，重置单局计数 */
  var afterSettle = e.evaluate({ source: "T2", task: "first_clear" });
  check("结算后单局计数重置", afterSettle === "queued" || afterSettle.indexOf("suppressed") !== 0, afterSettle);
});

/* 6. 池掏空（E9 诚实放弃） */
scenario("内容池掏空时诚实放弃", function () {
  var h = makeEnv();
  /* 预置本地记录：所有 C id + 两个角色的 A/D 全见过了（char 字段要真实，72h 角色去重才生效）。
     必须在 createEngine 之前写入：引擎创建时一次性加载本地记录（与生产环境一致 ——
     页面加载时读一次），建好引擎后再写存储它看不见。 */
  var ids = ["C_cloud_flip", "C_wukong_quiz", "C_peach_catch", "C_badge_match",
    "A:xiyou:wukong", "D:xiyou:wukong", "A:xiyou:bajie", "D:xiyou:bajie",
    "A:xiyou:shaseng", "D:xiyou:shaseng", "A:xiyou:tangseng", "D:xiyou:tangseng"];
  h.store.set("mmqj.egg.v1", JSON.stringify({
    log: ids.map(function (id) {
      return {
        id: id, ts: h.clock.now - 3600000, channel: "T2",
        char: id.replace(/^[AD]:/, ""), interacted: true
      };
    }),
    noInteract: 0, t3OffDate: "", lastVisit: "", enabled: true, day: "", dayCount: 0
  }));
  var e = engineApi.createEngine(h.env);
  e.start();
  var r = e.evaluate({ source: "T1" });
  check("池掏空 → 抑制且不展示", r.indexOf("suppressed") === 0 && e.state() === "IDLE", r);
});

/* 7. 兴趣衰减 */
scenario("连续无互动 → 当日关闭 T3", function () {
  var h = makeEnv({ noC: true });
  var e = engineApi.createEngine(h.env);
  e.start();
  for (var i = 0; i < 3; i++) {
    /* 三局游戏，每局出一个彩蛋都被无视（单局上限 2，局间必须结算重置） */
    e.evaluate({ source: "T2", task: "first_clear" });
    h.advance(300); e.tick();
    e.close("button");               /* 不 markInteracted */
    h.advance(200); e.tick();
    e.onSettle({ firstClear: false });
    h.advance(100000);
  }
  check("T3 被衰减规则关闭", e.evaluate({ source: "T3" }).indexOf("suppressed:decay_off") === 0);
  check("T1 不受衰减影响", e.evaluate({ source: "T1" }) === "queued" || e.state() === "ENTERING");
});

/* 8. 气泡：页面切换收起 / 点泡回看 / 超时自弃 */
scenario("页面切换 → 气泡收起与回看", function () {
  var h = makeEnv();
  var e = engineApi.createEngine(h.env);
  e.start();
  e.evaluate({ source: "T1" });
  h.advance(300); e.tick();
  var showing = e.state();
  e.onRoute();
  check("路由切换后收起为气泡", e.state() === "MINIMIZED", e.state());
  check("minimize 已通知渲染器", h.calls.indexOf("minimize") >= 0);
  var back = e.bubbleTap();
  check("点气泡恢复现场", back === "restored" && e.state() === showing, e.state());
  e.onRoute();
  h.advance(61000); e.tick();
  check("60s 未回看触发自动退场", e.state() === "CLOSING", e.state());
  h.advance(200); e.tick();
  check("退场闭环回 IDLE", e.state() === "IDLE", e.state());
});

/* 9. 互动中关闭延迟（R5） */
scenario("迷你互动进行中关闭被延迟", function () {
  var h = makeEnv({ chars: [] });      /* 无角色 → 只出 C 类 */
  var e = engineApi.createEngine(h.env);
  e.start();
  e.evaluate({ source: "T1" });
  h.advance(300); e.tick();
  check("C 类进入互动态", e.state() === "INTERACTIVE", e.state());
  var r = e.close("button");
  check("关闭请求被延迟", r === "deferred" && e.state() === "INTERACTIVE");
  check("延迟提示已弹出", h.calls.indexOf("deferHint") >= 0);
  e.finishInteractive();
  check("互动结束后执行关闭", e.state() === "CLOSING");
  h.advance(200); e.tick();
  check("完成闭环回 IDLE", e.state() === "IDLE");
});

/* 10. T3 空闲豁免 + 切后台不补掷 */
scenario("T3 空闲豁免与切后台不补掷", function () {
  var h = makeEnv();
  var e = engineApi.createEngine(h.env);
  e.start();
  h.interaction.at = h.clock.now - 120000;    /* 超过 60s 无交互 */
  h.advance(200000); e.tick();
  check("挂机时 T3 不触发", e.state() === "IDLE");
  h.touch();
  h.active.on = false;                        /* 切后台 */
  h.advance(300000); e.tick();
  check("后台期间不触发", e.state() === "IDLE");
  h.active.on = true;                         /* 回前台 */
  e.tick();
  check("回前台不补掷（仍 IDLE）", e.state() === "IDLE");
});

/* 11. T2 任务钩子（拆两个子场景，避免同一引擎队列饱和） */
scenario("任务钩子：结算类", function () {
  var h = makeEnv({ noC: true });
  var e = engineApi.createEngine(h.env);
  e.start();
  e.onSettle({ firstClear: true, clean: true, tier: "gold", ratio: 0.3, lineComplete: true });
  check("多个 T2 任务入队", e.queueSize() >= 2, "size=" + e.queueSize());
  check("队首是 T2", e.snapshot().queue[0].source === "T2");
  h.advance(300); e.tick();
  check("队列头被展示", e.state() === "PRESENTING", e.state());
});

scenario("任务钩子：连对 5 题", function () {
  var h = makeEnv({ noC: true });
  var e = engineApi.createEngine(h.env);
  e.start();
  for (var i = 0; i < 5; i++) { e.onAnswer(true, false); }
  check("连对 5 题立即触发展示", e.state() === "ENTERING", e.state());
  h.advance(300); e.tick();
  check("streak5 彩蛋进入展示", e.state() === "PRESENTING", e.state());
});

/* 12. B 类庆祝不占锁 */
scenario("结算庆祝叠加层不占互斥锁", function () {
  var h = makeEnv();
  var e = engineApi.createEngine(h.env);
  e.start();
  e.celebrate("gold");
  check("庆祝后仍 IDLE（不占锁）", e.state() === "IDLE");
  check("confetti 已调用", h.calls.indexOf("confetti:gold") >= 0);
  check("庆祝不计频率", e.todayCount() === 0);
});

/* 13. 家长总开关 */
scenario("家长总开关一键关闭", function () {
  var h = makeEnv();
  var e = engineApi.createEngine(h.env);
  e.start();
  e.evaluate({ source: "T1" });
  h.advance(300); e.tick();
  e.setEnabled(false);
  check("关闭时进行中的彩蛋退场", e.state() === "IDLE" || e.state() === "CLOSING", e.state());
  check("关闭后触发被抑制", e.evaluate({ source: "T1" }).indexOf("suppressed:disabled") === 0);
  e.setEnabled(true);
  check("重新开启后恢复", e.evaluate({ source: "T1" }) === "queued" || e.state() === "ENTERING");
});

/* 13b. 排队补位守全局冷却（O1：任意两个彩蛋间隔 ≥ 90s，补位也不例外） */
scenario("排队补位守全局冷却", function () {
  var h = makeEnv();
  var e = engineApi.createEngine(h.env);
  e.start();
  function enterCalls() {
    return h.calls.filter(function (c) { return c.indexOf("enter:") === 0; }).length;
  }
  e.evaluate({ source: "T1" });
  h.advance(300); e.tick();
  check("首个彩蛋已入场", enterCalls() === 1, "enterCalls=" + enterCalls());
  var r = e.evaluate({ source: "T2", task: "clean_run" });
  check("忙碌期新触发只入队", r === "queued" && e.queueSize() === 1);
  e.close("button");
  h.advance(200); e.tick();
  check("关闭后回 IDLE", e.state() === "IDLE", e.state());
  check("冷却未过 → 排队项不补位", e.queueSize() === 1 && e.state() === "IDLE");
  check("渲染器零新 enter", enterCalls() === 1);
  h.advance(300); e.tick();          /* 再过一个 tick 也不抢跑 */
  check("tick 驱动同样守冷却", e.state() === "IDLE" && e.queueSize() === 1);
  h.advance(91000); e.tick();        /* 拨过 90s 全局冷却 */
  check("冷却过去后 tick 自动补位", e.state() !== "IDLE", "state=" + e.state());
  check("补位后队列清空", e.queueSize() === 0);
  check("渲染器第二次 enter", enterCalls() === 2, "enterCalls=" + enterCalls());
});

/* 13c. 家长开关覆盖入场中的彩蛋（P2：ENTERING 态关开关，卡片不走完入场） */
scenario("家长开关覆盖 ENTERING 态", function () {
  var h = makeEnv();
  var e = engineApi.createEngine(h.env);
  e.start();
  e.evaluate({ source: "T1" });
  check("彩蛋刚触发（ENTERING）", e.state() === "ENTERING", e.state());
  var enterBefore = h.calls.filter(function (c) { return c.indexOf("enter:") === 0; }).length;
  var presentBefore = h.calls.filter(function (c) { return c.indexOf("present:") === 0; }).length;
  e.setEnabled(false);               /* 入场 300ms 内关开关 */
  check("ENTERING 态被立即退场", e.state() === "CLOSING", e.state());
  h.advance(200); e.tick();
  check("退场闭环回 IDLE", e.state() === "IDLE", e.state());
  check("入场后从未展示",
    h.calls.filter(function (c) { return c.indexOf("present:") === 0; }).length === presentBefore);
  check("渲染器只 enter 过一次",
    h.calls.filter(function (c) { return c.indexOf("enter:") === 0; }).length === enterBefore);
});

/* 14. 跨日重置 */
scenario("跨自然日重置计数", function () {
  var h = makeEnv();
  var e = engineApi.createEngine(h.env);
  e.start();
  e.evaluate({ source: "T1" });
  h.advance(300); e.tick();
  e.close("button");
  h.advance(200); e.tick();
  check("当天计数 1", e.todayCount() === 1);
  h.advance(26 * 3600 * 1000);
  check("次日计数归零", e.todayCount() === 0);
});

/* ---------- 汇总 ---------- */
console.log("\n========================================");
console.log("通过 " + passed + " 项，失败 " + failed + " 项");
if (failed) {
  console.log("失败项：");
  failures.forEach(function (f) { console.log("  - " + f); });
}
console.log("========================================");
process.exit(failed ? 1 : 0);
