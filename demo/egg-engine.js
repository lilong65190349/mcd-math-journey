/* =====================================================================
 * 麦麦取经录 · 随机彩蛋惊喜引擎（EggEngine）
 * ---------------------------------------------------------------------
 * 设计稿：推送包/彩蛋惊喜功能-移动端落地方案.md（随仓库分发时可移入 docs/）
 * 配置：data/run_rules.json 的 easter_egg 段（经 export_demo_bank.py 注入 BANK.rules）
 *
 * 架构：纯逻辑与渲染分离——
 *   createEngine(env)  不碰 DOM，可在 Node 下驱动完整状态机做单测；
 *   init()             浏览器端装配真实 env + 渲染器，并把钩子挂到 window.MMQJ。
 *
 * 本文件由 scripts/export_demo_bank.py 注入 demo/index.html 的
 * EGG_START / EGG_END 标记之间，请勿手工编辑 index.html 里的注入结果；
 * 要改行为，改本文件 + 重跑导出脚本。
 * ===================================================================== */
(function (root, factory) {
  var api = factory(root);
  if (typeof module === "object" && module.exports) {
    module.exports = api;                 /* Node 单测入口 */
  } else {
    root.MMQJ_EGG = api;                  /* 浏览器入口 */
    /* 自引导：本文件由导出脚本注入到主脚本之后，此时 window.MMQJ 已就绪；
     * 与主脚本同一模式——文档解析中则等 DOMContentLoaded，否则立即装配。 */
    if (typeof document !== "undefined") {
      if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", function () { api.init(); });
      } else {
        api.init();
      }
    }
  }
})(typeof self !== "undefined" ? self : this, function (root) {
  "use strict";

  var VERSION = "1.0.0";

  /* 触发源优先级（设计稿 1.3） */
  var PRIORITY = { T1: 100, T2: 80, T3: 50 };
  var ENTER_MS = 300;                     /* 入场动画时长 */
  var EXIT_MS = 200;                      /* 退场动画时长 */
  var INTERACTIVE_HARD_CAP_MS = 30000;    /* 迷你互动安全阀：任何情况 30s 内放行 */
  var LOG_CAP = 200;                      /* 环形缓冲区长度 */

  var DEFAULTS = {
    enabled_default: true,
    daily_hard_cap: 5,
    per_run_cap: 2,
    global_cooldown_s: 90,
    channel_cooldown_s: { T1: 120, T2: 0, T3: 180 },
    t3_interval_range_s: [90, 180],
    t3_idle_grace_s: 60,
    enter_ignore_input_ms: 800,
    minimize_auto_close_s: 60,
    queue_cap: 3,
    no_interact_streak_limit: 3,
    seen_window_h: 24,
    char_window_h: 72,
    freshness_days: 7,
    freshness_weight: 0.3,
    streak_target: 5,
    line_size: 14,
    gold_fast_ratio: 0.4,
    dash_streak_days: 3,
    task_weights: {},
    type_weights: { A: 10, C: 10, D: 6 },
    pool: []
  };

  function mergeCfg(cfg) {
    var out = {};
    var k;
    for (k in DEFAULTS) {
      if (Object.prototype.hasOwnProperty.call(DEFAULTS, k)) { out[k] = DEFAULTS[k]; }
    }
    for (k in (cfg || {})) {
      if (Object.prototype.hasOwnProperty.call(cfg || {}, k)) { out[k] = cfg[k]; }
    }
    return out;
  }

  function dayKey(ms) {
    var d = new Date(ms);
    var m = String(d.getMonth() + 1);
    var day = String(d.getDate());
    if (m.length < 2) { m = "0" + m; }
    if (day.length < 2) { day = "0" + day; }
    return d.getFullYear() + "-" + m + "-" + day;
  }

  /* ===================================================================
   * 纯逻辑引擎（无 DOM）
   * =================================================================== */
  function createEngine(env) {
    var cfg = mergeCfg(env.cfg);
    var store = env.storage;
    var render = env.render;

    var state = "IDLE";
    var queue = [];
    var current = null;
    var enteredAt = 0;
    var minimizeAt = 0;
    var pendingClose = false;
    var interacted = false;
    var closingAt = 0;

    var lastPresentedAt = 0;
    var lastChannelAt = { T1: 0, T2: 0, T3: 0 };
    var nextT3At = 0;
    var wasActive = true;
    var runEggCount = 0;
    var streak = 0;
    var dashChecked = false;

    /* ---- 持久化（单设备、不上传） ---- */
    var blob = loadBlob();

    function loadBlob() {
      var base = {
        log: [],
        noInteract: 0,
        t3OffDate: "",
        lastVisit: "",
        enabled: cfg.enabled_default,
        day: "",
        dayCount: 0
      };
      var raw = store.get("mmqj.egg.v1");
      if (!raw) { return base; }
      try {
        var parsed = JSON.parse(raw);
        if (parsed && typeof parsed === "object") {
          base.log = Array.isArray(parsed.log) ? parsed.log.slice(-LOG_CAP) : [];
          base.noInteract = Number(parsed.noInteract) || 0;
          base.t3OffDate = String(parsed.t3OffDate || "");
          base.lastVisit = String(parsed.lastVisit || "");
          base.enabled = parsed.enabled !== false;
          base.day = String(parsed.day || "");
          base.dayCount = Number(parsed.dayCount) || 0;
        }
      } catch (err) { /* 损坏的本地数据按全新开始，不打扰用户 */ }
      return base;
    }

    function saveBlob() {
      try {
        store.set("mmqj.egg.v1", JSON.stringify({
          log: blob.log.slice(-LOG_CAP),
          noInteract: blob.noInteract,
          t3OffDate: blob.t3OffDate,
          lastVisit: blob.lastVisit,
          enabled: blob.enabled,
          day: blob.day,
          dayCount: blob.dayCount
        }));
      } catch (err) { /* 隐私模式写入失败：本会话仍可玩，只是记忆不落盘 */ }
    }

    function rollDay() {
      var today = dayKey(env.now());
      if (blob.day !== today) {
        blob.day = today;
        blob.dayCount = 0;
      }
    }

    function enabled() { return blob.enabled !== false; }

    /* ---- 去重 / 新鲜度（设计稿 3.2） ---- */
    function seenWithin(id, windowMs) {
      var cutoff = env.now() - windowMs;
      for (var i = blob.log.length - 1; i >= 0; i--) {
        if (blob.log[i].id === id && blob.log[i].ts >= cutoff) { return true; }
      }
      return false;
    }

    function charUsedWithin(charId, windowMs) {
      var cutoff = env.now() - windowMs;
      for (var i = blob.log.length - 1; i >= 0; i--) {
        var entry = blob.log[i];
        if (entry.char === charId && entry.ts >= cutoff) { return true; }
      }
      return false;
    }

    function freshnessFactor(id) {
      for (var i = blob.log.length - 1; i >= 0; i--) {
        if (blob.log[i].id === id) {
          var days = (env.now() - blob.log[i].ts) / 86400000;
          return days < cfg.freshness_days ? cfg.freshness_weight : 1;
        }
      }
      return 1;
    }

    /* ---- 频率门（设计稿 3.1） ---- */
    function passGlobalCooldown() {
      return env.now() - lastPresentedAt >= cfg.global_cooldown_s * 1000;
    }

    function passChannelCooldown(source) {
      var need = (cfg.channel_cooldown_s && cfg.channel_cooldown_s[source]) || 0;
      return env.now() - (lastChannelAt[source] || 0) >= need * 1000;
    }

    function overDailyCap() {
      rollDay();
      return blob.dayCount >= cfg.daily_hard_cap;
    }

    function t3OffToday() {
      return blob.t3OffDate === dayKey(env.now());
    }

    /* ---- 队列（设计稿 1.3：R2 容量 / R3 插队 / R6 同源抑制） ---- */
    function scoreOf(evt) {
      /* T1 是用户显式意图，无条件压队首（R3）；其余按优先级 + 任务权重竞争 */
      return (evt.source === "T1" ? 1000 : 0)
        + PRIORITY[evt.source]
        + (evt.task ? (cfg.task_weights[evt.task] || 0) : 0);
    }

    function enqueue(evt) {
      for (var i = 0; i < queue.length; i++) {
        if (queue[i].eggId && queue[i].eggId === evt.eggId) { return "suppressed:duplicate"; }
      }
      queue.push({ source: evt.source, task: evt.task || "", score: scoreOf(evt), ts: env.now() });
      queue.sort(function (a, b) { return b.score - a.score || a.ts - b.ts; });
      while (queue.length > cfg.queue_cap) {
        var worst = queue.length - 1;   /* 已按分数降序：末位即最低最旧 */
        queue.splice(worst, 1);
      }
      return "queued";
    }

    /* ---- 内容抽取（设计稿第二章 + 3.2） ---- */
    function pickFrom(arr) { return arr[Math.floor(env.rng() * arr.length) % arr.length]; }

    function pickContent() {
      var seenMs = cfg.seen_window_h * 3600000;
      var charMs = cfg.char_window_h * 3600000;
      var options = [];

      cfg.pool.forEach(function (entry) {
        if (entry.type !== "C") { return; }
        if (seenWithin(entry.id, seenMs)) { return; }
        options.push({
          id: entry.id, type: "C", mini: entry.mini, title: entry.title,
          weight: (entry.weight || 1) * freshnessFactor(entry.id)
        });
      });

      var chars = (env.chars && env.chars()) || [];
      var fresh = chars.filter(function (c) { return !charUsedWithin(c.id, charMs); });
      if (fresh.length) {
        var ca = pickFrom(fresh);
        options.push({
          id: "A:" + ca.id, type: "A", char: ca,
          weight: (cfg.type_weights.A || 1) * freshnessFactor(ca.id)
        });
        var cd = pickFrom(fresh);
        options.push({
          id: "D:" + cd.id, type: "D", char: cd,
          weight: (cfg.type_weights.D || 1) * freshnessFactor(cd.id)
        });
      }

      if (!options.length) { return null; }   /* E9：池子掏空，诚实放弃 */
      var total = options.reduce(function (sum, o) { return sum + o.weight; }, 0);
      var roll = env.rng() * total;
      for (var i = 0; i < options.length; i++) {
        roll -= options[i].weight;
        if (roll <= 0) { return options[i]; }
      }
      return options[options.length - 1];
    }

    /* ---- 状态迁移 ---- */
    function beginPresentation(egg) {
      current = egg;
      interacted = false;
      pendingClose = false;
      enteredAt = env.now();
      state = "ENTERING";
      runEggCount += 1;
      render.enter(egg);
    }

    function presentNow() {
      if (!enabled()) { closeNow("disabled"); return; }   /* 家长开关护栏：入场途中被关也不展示 */
      if (current && current.type === "C") {
        state = "INTERACTIVE";
        render.present(current);
      } else {
        state = "PRESENTING";
        render.present(current);
      }
    }

    function recordAndIdle() {
      var today = dayKey(env.now());
      rollDay();
      blob.log.push({
        id: current.id, ts: env.now(), channel: current.channel,
        char: current.char ? current.char.id : "", interacted: interacted
      });
      if (blob.log.length > LOG_CAP) { blob.log.splice(0, blob.log.length - LOG_CAP); }
      blob.dayCount += 1;

      if (interacted) {
        blob.noInteract = 0;
      } else {
        blob.noInteract += 1;
        if (blob.noInteract >= cfg.no_interact_streak_limit) {
          blob.t3OffDate = today;      /* 兴趣衰减保护：当日关闭 T3 */
          blob.noInteract = 0;
        }
      }
      saveBlob();

      lastPresentedAt = env.now();
      lastChannelAt[current.channel] = env.now();
      current = null;
      state = "IDLE";
      scheduleT3();
      /* 不在此处立即补位：队列头的展示同样要守全局冷却，交给 tick 到点驱动（O1） */
    }

    function closeNow(reason) {
      state = "CLOSING";
      closingAt = env.now();
      render.exit(current, reason);
    }

    /* ---- 对外：触发评估 ---- */
    function evaluate(evt) {
      if (!enabled()) { return "suppressed:disabled"; }
      if (state !== "IDLE") { return enqueue(evt); }
      if (!passGlobalCooldown()) { return "suppressed:global_cooldown"; }
      if (!passChannelCooldown(evt.source)) { return "suppressed:channel_cooldown"; }
      if (evt.source !== "T1" && overDailyCap()) { return "suppressed:daily_cap"; }
      if (evt.source === "T3" && t3OffToday()) { return "suppressed:decay_off"; }
      if (runEggCount >= cfg.per_run_cap) { return "suppressed:run_cap"; }
      var result = enqueue(evt);
      var outcome = pump();
      if (outcome === "exhausted") { return "suppressed:pool_exhausted"; }
      /* presented / cooldown / empty（忙碌入队）均维持「已受理」语义 */
      return result;
    }

    /* 补位展示。返回："presented"（已入场）/ "cooldown"（队列非空但全局冷却未到）/
     * "exhausted"（池掏空）/ "empty"（队列空或不处于 IDLE）。
     * 设计稿 3.1：任意两个彩蛋间隔 ≥ global_cooldown_s，排队补位同样守这门。 */
    function pump() {
      if (state !== "IDLE" || !queue.length) { return "empty"; }
      if (!passGlobalCooldown()) { return "cooldown"; }
      var item = queue[0];
      var egg = pickContent();
      if (!egg) {
        queue.shift();                                  /* E9：池掏空，丢弃本条 */
        if (env.log) { env.log("egg suppressed: pool_exhausted"); }
        return "exhausted";
      }
      queue.shift();
      egg.channel = item.source;
      beginPresentation(egg);
      return "presented";
    }

    /* ---- 对外：任务/作答钩子 ---- */
    function onAnswer(correct, hadRetry) {
      if (!enabled()) { return; }
      if (correct) {
        streak += 1;
        if (hadRetry) { evaluate({ source: "T2", task: "recover" }); }
        if (streak >= cfg.streak_target) {
          evaluate({ source: "T2", task: "streak5" });
          streak = 0;
        }
      } else {
        streak = 0;
      }
    }

    function onSettle(info) {
      runEggCount = 0;
      streak = 0;
      if (!enabled()) { return; }
      if (info.firstClear) { evaluate({ source: "T2", task: "first_clear" }); }
      if (info.clean) { evaluate({ source: "T2", task: "clean_run" }); }
      if (info.lineComplete) { evaluate({ source: "T2", task: "line_complete" }); }
      if (info.tier === "gold" && info.ratio <= cfg.gold_fast_ratio) {
        evaluate({ source: "T2", task: "gold_fast" });
      }
    }

    /* ---- 对外：关闭 / 气泡 / 路由 ---- */
    function close(reason, meta) {
      if (state === "ENTERING" && env.now() - enteredAt < cfg.enter_ignore_input_ms) {
        return "ignored";                                   /* R4 防误触 */
      }
      if (state === "INTERACTIVE" && reason !== "auto" && reason !== "finish") {
        pendingClose = true;                                /* R5 不打断互动 */
        render.deferHint();
        return "deferred";
      }
      if (state !== "PRESENTING" && state !== "INTERACTIVE" && state !== "MINIMIZED") {
        return "ignored";
      }
      if (meta && meta.interacted) { interacted = true; }
      closeNow(reason || "button");
      return "closing";
    }

    function bubbleTap() {
      if (state !== "MINIMIZED") { return "ignored"; }
      state = current && current.type === "C" ? "INTERACTIVE" : "PRESENTING";
      render.restore(current);
      return "restored";
    }

    function onRoute() {
      if (state === "PRESENTING" || state === "INTERACTIVE") {
        state = "MINIMIZED";
        minimizeAt = env.now();
        render.minimize(current);
      }
    }

    function finishInteractive() {
      if (state !== "INTERACTIVE") { return "ignored"; }
      if (pendingClose) { closeNow("button"); return "closing"; }
      return "ok";
    }

    /* ---- B 类庆祝叠加层（不占互斥锁、不计频率） ---- */
    function celebrate(tier) {
      if (!enabled()) { return; }
      render.confetti(tier);
    }

    /* ---- T3 随机间隔 ---- */
    function scheduleT3() {
      var lo = (cfg.t3_interval_range_s && cfg.t3_interval_range_s[0]) || 90;
      var hi = (cfg.t3_interval_range_s && cfg.t3_interval_range_s[1]) || 180;
      nextT3At = env.now() + (lo + env.rng() * (hi - lo)) * 1000;
    }

    function tick() {
      var now = env.now();

      if (state === "ENTERING" && now - enteredAt >= ENTER_MS) { presentNow(); }
      if (state === "CLOSING" && now - closingAt >= EXIT_MS) { recordAndIdle(); }
      if (state === "MINIMIZED" && now - minimizeAt >= cfg.minimize_auto_close_s * 1000) {
        closeNow("timeout");
      }
      if (state === "INTERACTIVE" && now - enteredAt >= INTERACTIVE_HARD_CAP_MS) {
        finishInteractive();                                /* 安全阀 */
      }

      var active = env.isActive();
      if (active && !wasActive) {
        scheduleT3();                                       /* 回来不补掷 */
      }
      wasActive = active;

      if (!enabled() || !active || state !== "IDLE") { return; }
      /* 排队补位（O1）：关闭一个彩蛋后队列里排队的项，等全局冷却到位再由 tick 展示，
       * 避免一波任务触发连珠炮；冷却门与 evaluate 同源（passGlobalCooldown）。 */
      if (queue.length && passGlobalCooldown()) { pump(); }
      var idleFor = now - (env.lastInteractionAt() || 0);
      if (idleFor < (cfg.t3_idle_grace_s || 60) * 1000 && now >= nextT3At) {
        evaluate({ source: "T3" });
        scheduleT3();
      }
    }

    /* ---- 连续签到（T2 · dash3） ---- */
    function checkDash() {
      if (dashChecked || !enabled()) { return; }
      dashChecked = true;
      var today = dayKey(env.now());
      var last = blob.lastVisit;
      blob.lastVisit = today;
      saveBlob();
      if (!last) { return; }
      var prev = new Date(last + "T00:00:00").getTime();
      var days = Math.round((new Date(today + "T00:00:00").getTime() - prev) / 86400000);
      if (days === cfg.dash_streak_days) { evaluate({ source: "T2", task: "dash3" }); }
    }

    function setEnabled(on) {
      blob.enabled = on !== false;
      saveBlob();
      if (!blob.enabled) {
        queue = [];
        /* ENTERING 也要覆盖：家长在彩蛋刚触发的 ~300ms 内关开关，卡片不能走完入场 */
        if (state === "PRESENTING" || state === "INTERACTIVE" || state === "MINIMIZED" || state === "ENTERING") {
          closeNow("disabled");
        }
      }
      return blob.enabled;
    }

    function start() {
      if (nextT3At === 0) { scheduleT3(); }
      checkDash();
    }

    return {
      evaluate: evaluate,
      tick: tick,
      onAnswer: onAnswer,
      onSettle: onSettle,
      celebrate: celebrate,
      onRoute: onRoute,
      close: close,
      bubbleTap: bubbleTap,
      finishInteractive: finishInteractive,
      markInteracted: function () { interacted = true; },
      setEnabled: setEnabled,
      enabled: enabled,
      start: start,
      state: function () { return state; },
      queueSize: function () { return queue.length; },
      todayCount: function () { rollDay(); return blob.dayCount; },
      runCount: function () { return runEggCount; },
      snapshot: function () {
        return {
          state: state, queue: queue.slice(), current: current,
          nextT3At: nextT3At, dayCount: blob.dayCount, noInteract: blob.noInteract,
          t3OffDate: blob.t3OffDate, enabled: blob.enabled
        };
      }
    };
  }

  /* ===================================================================
   * 浏览器端：装配 + 渲染器
   * =================================================================== */
  var CSS_TEXT = [
    ".egg-layer{position:fixed;inset:0;z-index:80;pointer-events:none;",
    "font-family:\"PingFang SC\",\"Microsoft YaHei\",sans-serif;}",
    ".egg-host{position:relative;}",
    ".egg-layer .egg-card{pointer-events:auto;position:absolute;left:50%;bottom:calc(18% + env(safe-area-inset-bottom));",
    "transform:translateX(-50%);width:min(340px,86vw);max-height:min(70dvh,560px);",
    "overflow:auto;overscroll-behavior:contain;background:#FFFFFF;border-radius:20px;",
    "border:2px solid #DA291C;box-shadow:0 8px 28px rgba(43,43,43,.18);",
    "padding:20px 20px 16px;color:#2b2b2b;}",
    ".egg-layer.egg-enter .egg-card{animation:egg-pop .3s cubic-bezier(.34,1.56,.64,1);}",
    ".egg-layer.egg-exit .egg-card{animation:egg-fade .2s ease-out forwards;}",
    "@keyframes egg-pop{from{transform:translateX(-50%) scale(.6);opacity:0;}",
    "to{transform:translateX(-50%) scale(1);opacity:1;}}",
    "@keyframes egg-fade{to{transform:translateX(-50%) scale(.85);opacity:0;}}",
    ".egg-title{margin:0 0 6px;font-size:19px;font-weight:600;color:#DA291C;}",
    ".egg-sub{margin:0 0 10px;font-size:14px;color:#8A7563;}",
    ".egg-line{margin:0 0 12px;font-size:17px;line-height:1.7;}",
    ".egg-note{margin:0 0 10px;font-size:12px;color:#8a8177;}",
    ".egg-close{display:block;width:100%;min-height:56px;border:none;border-radius:28px;",
    "background:#FFC72C;color:#A81E14;font-size:19px;font-weight:600;}",
    ".egg-close:active{background:#F0B000;}",
    ".egg-role{display:flex;align-items:center;gap:14px;margin-bottom:12px;}",
    ".egg-badge{flex:0 0 72px;width:72px;height:72px;border-radius:20px;",
    "background:#FFF9EF;border:2px solid #DA291C;display:flex;align-items:center;",
    "justify-content:center;font-size:30px;font-weight:600;color:#DA291C;",
    "transition:transform .18s cubic-bezier(.34,1.56,.64,1);}",
    ".egg-badge.egg-poke{transform:scale(.82) rotate(-6deg);}",
    ".egg-role-name{font-size:19px;font-weight:600;}",
    ".egg-role-title{font-size:13px;color:#8a8177;}",
    ".egg-beacon{position:absolute;right:14px;bottom:calc(12px + env(safe-area-inset-bottom));width:64px;height:64px;",
    "border:none;background:transparent;padding:0;cursor:pointer;}",
    ".egg-beacon .egg-stick{display:block;width:10px;height:44px;margin:0 auto;",
    "border-radius:6px;background:#c9a06a;animation:egg-breathe 2.4s ease-in-out infinite;}",
    "@keyframes egg-breathe{0%,100%{opacity:.14;}50%{opacity:.34;}}",
    ".egg-confetti{position:absolute;top:-12px;width:9px;height:14px;border-radius:3px;",
    "animation:egg-fall linear forwards;}",
    "@keyframes egg-fall{to{transform:translateY(105vh) rotate(540deg);}}",
    ".egg-bubble{pointer-events:auto;position:absolute;right:16px;bottom:88px;",
    "width:60px;height:60px;border-radius:50%;border:2px solid #DA291C;",
    "background:#FFFFFF;color:#DA291C;font-size:22px;font-weight:600;",
    "display:flex;align-items:center;justify-content:center;",
    "animation:egg-breathe 1.8s ease-in-out infinite;}",
    ".egg-gear{position:fixed;left:12px;bottom:calc(12px + env(safe-area-inset-bottom));z-index:81;min-height:44px;",
    "padding:0 14px;border-radius:22px;border:1px solid #E7DFD4;background:#fff;",
    "color:#8A7563;font-size:13px;}",
    ".egg-panel{position:fixed;left:12px;bottom:64px;z-index:81;width:min(280px,80vw);",
    "background:#fff;border:1px solid #E7DFD4;border-radius:16px;padding:14px 16px;",
    "font-size:13px;color:#4a443c;line-height:1.7;}",
    ".egg-panel button{min-height:44px;padding:0 16px;border-radius:22px;",
    "border:1px solid #DA291C;background:#fff;color:#DA291C;font-size:14px;}",
    ".egg-panel button[aria-pressed=\"true\"]{background:#FFC72C;color:#A81E14;}",
    ".egg-mini{min-height:150px;display:flex;flex-wrap:wrap;gap:12px;",
    "align-items:center;justify-content:center;margin-bottom:12px;}",
    ".egg-opt{min-width:120px;min-height:64px;border-radius:18px;border:2px solid #DA291C;",
    "background:#fff;color:#2b2b2b;font-size:22px;font-weight:600;}",
    ".egg-opt:active{background:#ffe9d6;}",
    ".egg-cloud{width:88px;height:64px;border-radius:32px;background:#e6f1fb;",
    "border:2px solid #85B7EB;font-size:26px;color:#185fa5;}",
    ".egg-peach{width:46px;height:46px;border-radius:50%;background:#f0997b;",
    "border:none;position:absolute;top:-40px;}",
    ".egg-half{width:56px;height:56px;border-radius:50%;border:2px solid #DA291C;}",
    ".egg-half.left{background:#ffd9b0;}",
    ".egg-half.right{background:#F7B7A3;touch-action:none;cursor:grab;}",
    ".egg-timer{height:8px;border-radius:4px;background:#f0e6d8;overflow:hidden;margin-bottom:10px;}",
    ".egg-timer>i{display:block;height:100%;background:#DA291C;}",
    ".egg-lite .egg-confetti{display:none;}",
    "@media (prefers-reduced-motion:reduce){",
    ".egg-layer.egg-enter .egg-card{animation:egg-fade .15s ease-out;}",
    ".egg-beacon .egg-stick,.egg-bubble{animation:none;opacity:.3;}}",
    "@media (max-width:640px){.egg-layer .egg-card{bottom:calc(12% + env(safe-area-inset-bottom));width:92vw;}}"
  ].join("\n");

  var SOUNDS = {
    ding: { type: "sine", from: 880, to: 1320, ms: 120 },
    poke: { type: "triangle", from: 300, to: 160, ms: 80 },
    arp: { type: "sine", notes: [523, 659, 784, 1047], ms: 90 },
    shh: { type: "noise", ms: 150 }
  };

  /* DOM 助手：渲染器与家长开关面板共用（工厂作用域，init 也能用） */
  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) { node.className = cls; }
    if (text !== undefined && text !== null) { node.textContent = text; }
    return node;
  }

  function buildEnv(MMQJ) {
    var audioCtx = null;
    var lastInteraction = Date.now();

    function ensureCtx() {
      if (audioCtx) { return audioCtx; }
      var AC = root.AudioContext || root.webkitAudioContext;
      if (!AC) { return null; }
      try { audioCtx = new AC(); } catch (err) { audioCtx = null; }
      return audioCtx;
    }

    document.addEventListener("pointerdown", function () {
      var ctx = ensureCtx();
      if (ctx && ctx.state === "suspended" && ctx.resume) { ctx.resume(); }
      lastInteraction = Date.now();
    }, { capture: true, once: false });

    function play(name) {
      var spec = SOUNDS[name];
      var ctx = ensureCtx();
      if (!spec || !ctx) { return; }
      try {
        var t0 = ctx.currentTime;
        var gain = ctx.createGain();
        gain.gain.value = 0.22;
        gain.connect(ctx.destination);
        if (spec.type === "noise") {
          var buf = ctx.createBuffer(1, ctx.sampleRate * 0.2, ctx.sampleRate);
          var data = buf.getChannelData(0);
          for (var i = 0; i < data.length; i++) { data[i] = (Math.random() * 2 - 1) * (1 - i / data.length); }
          var src = ctx.createBufferSource();
          src.buffer = buf;
          var lp = ctx.createBiquadFilter();
          lp.type = "lowpass";
          lp.frequency.value = 900;
          src.connect(lp); lp.connect(gain);
          src.start();
          return;
        }
        var notes = spec.notes || [{ from: spec.from, to: spec.to }];
        notes.forEach(function (n, idx) {
          var osc = ctx.createOscillator();
          osc.type = spec.type;
          var start = t0 + idx * (spec.ms / 1000);
          osc.frequency.setValueAtTime(n.from, start);
          if (n.to) { osc.frequency.exponentialRampToValueAtTime(n.to, start + spec.ms / 1000); }
          var g = ctx.createGain();
          g.gain.setValueAtTime(0.0001, start);
          g.gain.exponentialRampToValueAtTime(0.22, start + 0.01);
          g.gain.exponentialRampToValueAtTime(0.0001, start + spec.ms / 1000);
          osc.connect(g); g.connect(ctx.destination);
          osc.start(start);
          osc.stop(start + spec.ms / 1000 + 0.02);
        });
      } catch (err) { /* 音频失败不影响彩蛋 */ }
    }

    return {
      cfg: (MMQJ.RULES && MMQJ.RULES.easter_egg) || {},
      chars: function () {
        var out = [];
        var byStory = MMQJ.CHARS || {};
        Object.keys(byStory).forEach(function (sid) {
          var table = byStory[sid] || {};
          Object.keys(table).forEach(function (cid) {
            var c = table[cid];
            out.push({ id: sid + ":" + cid, name: c.name, title: c.title, intro: c.intro });
          });
        });
        return out;
      },
      now: Date.now,
      rng: Math.random,
      isActive: function () { return !document.hidden; },
      lastInteractionAt: function () { return lastInteraction; },
      storage: {
        get: function (k) { try { return window.localStorage.getItem(k); } catch (e) { return null; } },
        set: function (k, v) { try { window.localStorage.setItem(k, v); } catch (e) { /* 隐私模式 */ } }
      },
      audio: { play: play },
      log: function () { if (root.console && console.debug) { console.debug.apply(console, arguments); } }
    };
  }

  /* ---------------- 渲染器 ---------------- */
  function createRenderer(engine, opts) {
    var lite = opts.lite;
    var layer = null;
    var card = null;
    var bubble = null;
    var audio = opts.audio;
    var miniCleanup = null;

    function ensureStyle() {
      if (document.getElementById("egg-style")) { return; }
      var st = document.createElement("style");
      st.id = "egg-style";
      st.textContent = CSS_TEXT;
      document.head.appendChild(st);
    }

    function ensureLayer() {
      if (layer) { return layer; }
      layer = el("div", "egg-layer");
      if (lite) { layer.classList.add("egg-lite"); }
      document.body.appendChild(layer);
      return layer;
    }

    function clearCard() {
      if (miniCleanup) { miniCleanup(); miniCleanup = null; }
      if (card && card.parentNode) { card.parentNode.removeChild(card); }
      card = null;
    }

    function buildCard(egg, onClose) {
      var box = el("div", "egg-card");
      if (egg.type === "A" || egg.type === "D") {
        var c = egg.char;
        box.appendChild(el("p", "egg-title", egg.type === "A" ? "嘿嘿，被发现啦！" : "原来如此"));
        var row = el("div", "egg-role");
        var badge = el("div", "egg-badge", c.name ? c.name.charAt(0) : "？");
        badge.setAttribute("role", "button");
        badge.setAttribute("tabindex", "0");
        badge.setAttribute("aria-label", "戳一下 " + c.name);
        var meta = el("div");
        meta.appendChild(el("div", "egg-role-name", c.name));
        meta.appendChild(el("div", "egg-role-title", c.title || ""));
        row.appendChild(badge);
        row.appendChild(meta);
        box.appendChild(row);
        badge.addEventListener("click", function () {
          badge.classList.add("egg-poke");
          audio.play("poke");
          engine.markInteracted();
          setTimeout(function () { badge.classList.remove("egg-poke"); }, 180);
        });
        box.appendChild(el("p", "egg-line", c.intro || ""));
        if (egg.type === "D") {
          box.appendChild(el("p", "egg-note", "取自角色资料（演示数据）· 非麦当劳官方产品"));
        }
      } else if (egg.type === "C") {
        box.appendChild(el("p", "egg-title", egg.title || "小游戏"));
        box.appendChild(el("p", "egg-sub", "三十秒内完成，不想玩随时关掉"));
        var host = el("div", "egg-mini");
        box.appendChild(host);
        miniCleanup = buildMini(host, egg.mini, onClose);
      }
      var closeBtn = el("button", "egg-close", "知道啦");
      closeBtn.addEventListener("click", function () { onClose("button"); });
      box.appendChild(closeBtn);
      bindSwipe(box, function () { onClose("swipe"); });
      return box;
    }

    function bindSwipe(node, onSwipe) {
      var startY = null;
      node.addEventListener("touchstart", function (e) {
        if (e.touches && e.touches.length) { startY = e.touches[0].clientY; }
      }, { passive: true });
      node.addEventListener("touchmove", function (e) {
        if (startY === null || !e.touches || !e.touches.length) { return; }
        var dy = e.touches[0].clientY - startY;
        if (dy > 0) { node.style.transform = "translateX(-50%) translateY(" + dy + "px)"; }
      }, { passive: true });
      node.addEventListener("touchend", function (e) {
        if (startY === null) { return; }
        var dy = 0;
        if (e.changedTouches && e.changedTouches.length) { dy = e.changedTouches[0].clientY - startY; }
        node.style.transform = "";
        startY = null;
        if (dy > 80) { onSwipe(); }
      });
    }

    /* ---- 四个迷你互动 ---- */
    function buildMini(host, kind, onClose) {
      var timers = [];
      var done = false;
      function after(ms, fn) { var t = setTimeout(fn, ms); timers.push(t); }
      function finish(ok) {
        if (done) { return; }
        done = true;
        engine.markInteracted();
        if (ok) { audio.play("arp"); }
        after(900, function () { onClose("auto"); });
      }
      function cleanup() { timers.forEach(clearTimeout); }

      if (kind === "cloud_flip") {
        var cardIdx = Math.floor(Math.random() * 3);
        for (var i = 0; i < 3; i++) {
          (function (idx) {
            var b = el("button", "egg-opt egg-cloud", "云");
            b.addEventListener("click", function () {
              if (done || b.disabled) { return; }
              b.disabled = true;
              if (idx === cardIdx) {
                b.textContent = "卡";
                b.style.background = "#ffe9d6";
                finish(true);
              } else {
                b.textContent = "雾";
                b.style.opacity = ".45";
              }
            });
            host.appendChild(b);
          })(i);
        }
        after(20000, function () { finish(false); });
        return cleanup;
      }

      if (kind === "wukong_quiz") {
        var mul = Math.random() < 0.5;
        var a = mul ? 2 + Math.floor(Math.random() * 8) : 11 + Math.floor(Math.random() * 38);
        var b = 2 + Math.floor(Math.random() * (mul ? 8 : 8));
        var ans = mul ? a * b : a + b;
        var wrong = ans + (Math.random() < 0.5 ? 1 : -1) * (1 + Math.floor(Math.random() * 3));
        host.appendChild(el("p", "egg-line", "悟空问：" + a + (mul ? " × " : " + ") + b + " = ？"));
        var bar = el("div", "egg-timer");
        var fill = el("i");
        fill.style.width = "100%";
        bar.appendChild(fill);
        host.appendChild(bar);
        var t0 = Date.now();
        var iv = setInterval(function () {
          var left = 1 - (Date.now() - t0) / 10000;
          fill.style.width = Math.max(0, left * 100) + "%";
          if (left <= 0) { clearInterval(iv); finish(false); }
        }, 100);
        timers.push(iv);
        [ans, wrong].sort(function () { return Math.random() - 0.5; }).forEach(function (v) {
          var btn = el("button", "egg-opt", String(v));
          btn.addEventListener("click", function () {
            if (done) { return; }
            if (v === ans) { btn.style.background = "#eaf3de"; finish(true); }
            else { btn.style.background = "#fcebeb"; btn.disabled = true; }
          });
          host.appendChild(btn);
        });
        return cleanup;
      }

      if (kind === "peach_catch") {
        var field = el("div");
        field.style.position = "relative";
        field.style.width = "100%";
        field.style.height = "150px";
        field.style.overflow = "hidden";
        field.style.background = "#FFF9EF";
        field.style.borderRadius = "12px";
        host.appendChild(field);
        var caught = 0;
        var total = 6;
        for (var p = 0; p < total; p++) {
          (function (idx) {
            var peach = el("button", "egg-peach", "");
            peach.setAttribute("aria-label", "接仙桃");
            peach.style.left = (8 + Math.random() * 78) + "%";
            peach.style.animation = "egg-fall 4.5s linear forwards";
            peach.style.animationDelay = (idx * 0.55) + "s";
            peach.addEventListener("click", function () {
              if (peach.dataset.got) { return; }
              peach.dataset.got = "1";
              caught += 1;
              peach.style.opacity = "0";
              audio.play("poke");
              engine.markInteracted();
            });
            field.appendChild(peach);
          })(p);
        }
        after(5200, function () {
          var opts = [caught, caught + 1, caught - 1].filter(function (v, i, arr) {
            return v >= 0 && arr.indexOf(v) === i;
          });
          field.innerHTML = "";
          host.appendChild(el("p", "egg-line", "一共接住几个仙桃？"));
          opts.sort(function () { return Math.random() - 0.5; }).forEach(function (v) {
            var btn = el("button", "egg-opt", String(v));
            btn.addEventListener("click", function () {
              if (done) { return; }
              if (v === caught) { finish(true); } else { btn.style.background = "#fcebeb"; btn.disabled = true; }
            });
            host.appendChild(btn);
          });
        });
        return cleanup;
      }

      if (kind === "badge_match") {
        var zone = el("div");
        zone.style.position = "relative";
        zone.style.width = "100%";
        zone.style.height = "140px";
        zone.style.background = "#FFF9EF";
        zone.style.borderRadius = "12px";
        zone.style.display = "flex";
        zone.style.alignItems = "center";
        zone.style.justifyContent = "center";
        host.appendChild(zone);
        var left = el("div", "egg-half left");
        left.style.position = "absolute";
        left.style.left = "34%";
        var right = el("div", "egg-half right");
        right.style.position = "absolute";
        right.style.left = "56%";
        right.setAttribute("role", "button");
        right.setAttribute("tabindex", "0");
        right.setAttribute("aria-label", "拖动右半枚徽记");
        zone.appendChild(left);
        zone.appendChild(right);
        var dragging = false, sx = 0, sy = 0, ox = 0, oy = 0;
        right.addEventListener("pointerdown", function (e) {
          dragging = true;
          sx = e.clientX; sy = e.clientY;
          right.setPointerCapture(e.pointerId);
        });
        right.addEventListener("pointermove", function (e) {
          if (!dragging) { return; }
          ox = e.clientX - sx; oy = e.clientY - sy;
          right.style.left = (56 + ox / zone.clientWidth * 100) + "%";
          right.style.top = oy + "px";
        });
        right.addEventListener("pointerup", function () {
          dragging = false;
          var lr = left.getBoundingClientRect(), rr = right.getBoundingClientRect();
          var dx = (lr.left + lr.width / 2) - (rr.left + rr.width / 2);
          var dy = (lr.top + lr.height / 2) - (rr.top + rr.height / 2);
          if (Math.abs(dx) < 48 && Math.abs(dy) < 48) {
            var tiers = ["黄金品阶", "白银品阶", "青铜品阶"];
            var roll = Math.random();
            var tier = roll < 0.2 ? tiers[0] : (roll < 0.7 ? tiers[1] : tiers[2]);
            zone.innerHTML = "";
            var badge = el("div", "egg-badge", "徽");
            badge.style.borderColor = "#DA291C";
            zone.appendChild(badge);
            host.appendChild(el("p", "egg-line", "合成成功 · " + tier));
            finish(true);
          }
        });
        after(25000, function () { finish(false); });
        return cleanup;
      }

      host.appendChild(el("p", "egg-line", "这个小游戏还没准备好，先看看别的吧。"));
      after(1500, function () { finish(false); });
      return cleanup;
    }

    /* ---- 引擎回调 ---- */
    function enter(egg) {
      ensureLayer();
      layer.classList.remove("egg-exit", "egg-enter");
      void layer.offsetWidth;
      layer.classList.add("egg-enter");
      clearCard();
      audio.play("ding");
      card = buildCard(egg, function (reason) {
        engine.close(reason);
      });
      layer.appendChild(card);
    }

    function present() { /* 入场动画由 CSS 完成；C 类在 buildCard 时已启动 */ }

    function exit() {
      if (!layer || !card) { return; }
      layer.classList.remove("egg-enter");
      layer.classList.add("egg-exit");
      audio.play("shh");
      var old = card;
      card = null;
      setTimeout(function () {
        if (old && old.parentNode) { old.parentNode.removeChild(old); }
        layer.classList.remove("egg-exit");
      }, EXIT_MS);
    }

    function minimize(egg) {
      clearCard();
      if (!bubble) {
        bubble = el("button", "egg-bubble", egg.char ? egg.char.name.charAt(0) : "宝");
        bubble.setAttribute("aria-label", "打开刚才的惊喜");
        bubble.addEventListener("click", function () { engine.bubbleTap(); });
      }
      ensureLayer().appendChild(bubble);
    }

    function restore(egg) {
      if (bubble && bubble.parentNode) { bubble.parentNode.removeChild(bubble); }
      layer.classList.remove("egg-exit", "egg-enter");
      void layer.offsetWidth;
      layer.classList.add("egg-enter");
      card = buildCard(egg, function (reason) {
        engine.close(reason);
      });
      layer.appendChild(card);
    }

    function confetti(tier) {
      if (lite) {
        ensureLayer();
        var mark = el("div", "egg-badge", tier === "gold" ? "金" : (tier === "silver" ? "银" : "铜"));
        mark.style.position = "absolute";
        mark.style.left = "50%";
        mark.style.top = "30%";
        mark.style.transform = "translateX(-50%)";
        layer.appendChild(mark);
        setTimeout(function () { if (mark.parentNode) { mark.parentNode.removeChild(mark); } }, 1200);
        return;
      }
      ensureLayer();
      var colors = ["#DA291C", "#DA291C", "#F7B7A3", "#85B7EB"];
      for (var i = 0; i < 24; i++) {
        (function (idx) {
          var p = el("div", "egg-confetti");
          p.style.left = (Math.random() * 96) + "%";
          p.style.background = colors[idx % colors.length];
          p.style.animationDuration = (1.4 + Math.random() * 0.9) + "s";
          p.style.animationDelay = (Math.random() * 0.35) + "s";
          layer.appendChild(p);
          setTimeout(function () { if (p.parentNode) { p.parentNode.removeChild(p); } }, 2600);
        })(i);
      }
    }

    function deferHint() {
      ensureLayer();
      var tip = el("p", "egg-sub", "这局结束就能退出了，稍等一下下");
      tip.style.position = "absolute";
      tip.style.left = "50%";
      tip.style.bottom = "8%";
      tip.style.transform = "translateX(-50%)";
      tip.style.background = "#fff";
      tip.style.padding = "6px 14px";
      tip.style.borderRadius = "14px";
      tip.style.pointerEvents = "none";
      layer.appendChild(tip);
      setTimeout(function () { if (tip.parentNode) { tip.parentNode.removeChild(tip); } }, 1600);
    }

    return {
      enter: enter, present: present, exit: exit,
      minimize: minimize, restore: restore,
      confetti: confetti, deferHint: deferHint
    };
  }

  /* ---------------- 浏览器装配 ---------------- */
  function init() {
    var MMQJ = root.MMQJ;
    if (!MMQJ || !MMQJ.RULES || root.MMQJ_EGG_BOOTED) { return null; }
    root.MMQJ_EGG_BOOTED = true;

    var env = buildEnv(MMQJ);

    var renderBox = {
      enter: function () {}, present: function () {}, exit: function () {},
      minimize: function () {}, restore: function () {},
      confetti: function () {}, deferHint: function () {}
    };

    /* 低端机 / 动效敏感：FPS 采样 + reduced-motion 二选一即降级 */
    var lite = false;
    try {
      lite = root.matchMedia && root.matchMedia("(prefers-reduced-motion: reduce)").matches;
    } catch (err) { lite = false; }
    if (!lite) {
      var frames = 0, t0 = Date.now();
      (function sample() {
        frames += 1;
        if (Date.now() - t0 < 2000) { root.requestAnimationFrame(sample); }
        else if (frames / ((Date.now() - t0) / 1000) < 30) { lite = true; }
      })();
    }

    var engine = createEngine({
      cfg: env.cfg, chars: env.chars, now: env.now, rng: env.rng,
      isActive: env.isActive, lastInteractionAt: env.lastInteractionAt,
      storage: env.storage, audio: env.audio, log: env.log,
      render: renderBox
    });

    var renderer = createRenderer(engine, { lite: lite, audio: env.audio });
    var keys = ["enter", "present", "exit", "minimize", "restore", "confetti", "deferHint"];
    keys.forEach(function (k) { renderBox[k] = renderer[k]; });

    /* 挂到主脚本暴露的 MMQJ 上（主脚本里是防御式调用，不存在也不报错） */
    MMQJ.onAnswer = engine.onAnswer;
    MMQJ.onSettle = engine.onSettle;
    MMQJ.celebrate = engine.celebrate;
    MMQJ.onRoute = engine.onRoute;

    /* 隐藏区域 beacon：挂在已有地图面板里 */
    function bindBeacon() {
      var beacon = document.getElementById("eggBeacon");
      if (!beacon) { return; }
      var taps = 0, lastTap = 0, pressTimer = null;
      function fire() {
        var r = engine.evaluate({ source: "T1" });
        if (String(r).indexOf("queued") === 0 || String(r).indexOf("presented") === 0) {
          beacon.classList.add("hidden");
        }
      }
      beacon.addEventListener("click", function () {
        var now = Date.now();
        taps = now - lastTap < 800 ? taps + 1 : 1;
        lastTap = now;
        if (taps >= 3) { taps = 0; fire(); }
      });
      beacon.addEventListener("pointerdown", function () {
        pressTimer = setTimeout(fire, 800);
      });
      ["pointerup", "pointerleave", "pointercancel"].forEach(function (evt) {
        beacon.addEventListener(evt, function () { clearTimeout(pressTimer); });
      });
      engine.__showBeacon = function () {
        if (engine.enabled()) { beacon.classList.remove("hidden"); }
      };
      engine.__hideBeacon = function () { beacon.classList.add("hidden"); };
    }

    /* 家长开关（齿轮） */
    function bindGear() {
      if (document.getElementById("eggGear")) { return; }
      var gearPanel = null;   /* 设置面板实例：开合都收敛在本作用域 */
      var btn = el("button", "egg-gear", "彩蛋设置");
      btn.id = "eggGear";
      btn.setAttribute("aria-expanded", "false");
      document.body.appendChild(btn);
      btn.addEventListener("click", function () {
        var open = btn.getAttribute("aria-expanded") === "true";
        btn.setAttribute("aria-expanded", open ? "false" : "true");
        if (open) {
          if (gearPanel && gearPanel.parentNode) { gearPanel.parentNode.removeChild(gearPanel); }
          gearPanel = null;
          return;
        }
        gearPanel = el("div", "egg-panel");
        var on = engine.enabled();
        gearPanel.appendChild(el("div", null, "随机小惊喜（角色探头、小游戏、知识卡）"));
        gearPanel.appendChild(el("div", "egg-note", "今天已出现 " + engine.todayCount() + " 次 · 记录只存本机，不上传"));
        var toggle = el("button", null, on ? "开启中 · 点此关闭" : "已关闭 · 点此开启");
        toggle.setAttribute("aria-pressed", on ? "true" : "false");
        toggle.addEventListener("click", function () {
          var now = engine.setEnabled(!engine.enabled());
          toggle.textContent = now ? "开启中 · 点此关闭" : "已关闭 · 点此开启";
          toggle.setAttribute("aria-pressed", now ? "true" : "false");
        });
        gearPanel.appendChild(toggle);
        gearPanel.appendChild(el("div", "egg-note", "非麦当劳官方产品；彩蛋文案与数据均为演示用途"));
        document.body.appendChild(gearPanel);
      });
    }

    /* 路由感知：进地图显示 beacon，离开收起 */
    var lastRoute = "";
    setInterval(function () {
      var panels = document.querySelectorAll("section.panel");
      var route = "";
      for (var i = 0; i < panels.length; i++) {
        if (!panels[i].classList.contains("hidden")) { route = panels[i].id || "mapPanel"; }
      }
      if (route !== lastRoute) {
        lastRoute = route;
        engine.onRoute();
        if (engine.__showBeacon) {
          if (route === "mapPanel") { engine.__showBeacon(); } else { engine.__hideBeacon(); }
        }
      }
    }, 300);

    ensureStyleOnce();
    bindBeacon();
    bindGear();
    engine.start();
    setInterval(function () { engine.tick(); }, 250);

    function ensureStyleOnce() {
      if (document.getElementById("egg-style")) { return; }
      var st = document.createElement("style");
      st.id = "egg-style";
      st.textContent = CSS_TEXT;
      document.head.appendChild(st);
    }

    return engine;
  }

  return {
    VERSION: VERSION,
    createEngine: createEngine,
    init: init
  };
});
