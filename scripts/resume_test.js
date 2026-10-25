"use strict";
/* 断点续答（demo/index.html 的 RUN_KEY 存档）回归测试
 *
 * 运行：NODE_PATH=<jsdom 所在 node_modules> node scripts/resume_test.js
 * 依赖 jsdom（仅测试用，不进入页面运行时）；**任何断言失败 / 未捕获异常 / 断言数为 0
 * 都以非 0 退出**——门禁必须会红，否则等于没有门禁。
 *
 * 三条设计原则（都是踩过的坑）：
 *  1) 本关题数按 30/40/30 的权重随机抽，N **可以等于 1**。所以绝不断言「N > 1」；
 *     需要确定性的场景，一律先用 MMQJ.drawRun(story, stage, seed) 筛出「N=1」「N≥2」
 *     的种子再推进，不赌随机。随机那一局则读实际 N、按 N 走对应分支。
 *  2) 题面用题库（MMQJ.BANK.bank）原文做独立 oracle 比对，而不是「和上一次渲染比」——
 *     后者只能证明两次渲染一致，证明不了「种子确实重建出了同一道题」。
 *  3) 每条用例一个独立 window（jsdom 实例），存档通过 localStorage 预置，
 *     这样每条用例的前置状态都显式可见，不依赖前面用例的执行痕迹。
 */

var fs = require("fs");
var path = require("path");

var jsdomPkg;
try {
  jsdomPkg = require("jsdom");
} catch (err) {
  console.error("找不到 jsdom，无法运行本测试。请先安装并设置 NODE_PATH：");
  console.error("  NODE_PATH=<node_modules 路径> node scripts/resume_test.js");
  process.exit(1);
}
var JSDOM = jsdomPkg.JSDOM;
var VirtualConsole = jsdomPkg.VirtualConsole;

var ROOT = path.join(__dirname, "..");
var HTML = fs.readFileSync(path.join(ROOT, "demo", "index.html"), "utf8");
var RULES_JSON = JSON.parse(fs.readFileSync(path.join(ROOT, "data", "run_rules.json"), "utf8"));

var RUN_KEY = "mmqj.run.v1";
var STORY_KEY = "mmqj.story.v1";
var STAGE_KEY_PREFIX = "mmqj.stage.v2.";
var CHAR_KEY_PREFIX = "mmqj.chars.v2.";

var failures = [];
var checks = 0;
var scriptErrors = [];

/* 看门狗：jsdom 的定时器会让进程不退出，卡住时也要给出非 0 退出而不是挂死 */
var watchdog = setTimeout(function () {
  console.error("\n[超时] 180 秒内没有跑完，判定失败");
  process.exit(1);
}, 180000);

process.on("uncaughtException", function (err) {
  console.error("[未捕获异常]", err && err.stack ? err.stack : err);
  process.exit(1);
});
process.on("unhandledRejection", function (err) {
  console.error("[未处理的 Promise 拒绝]", err && err.stack ? err.stack : err);
  process.exit(1);
});

function section(title) {
  console.log("\n== " + title + " ==");
}

function ok(cond, label, extra) {
  checks += 1;
  console.log((cond ? "  PASS " : "  FAIL ") + label + (extra !== undefined && extra !== "" ? "  -> " + extra : ""));
  if (!cond) {
    failures.push(label);
  }
  return Boolean(cond);
}

function info(label, value) {
  console.log("  ·· " + label + ": " + value);
}

/* ---------------------------------------------------------------------------
 * 页面装载
 * ------------------------------------------------------------------------- */

function boot(prefill) {
  var vc = new VirtualConsole();
  vc.on("jsdomError", function (err) {
    var message = String((err && err.message) || err);
    /* jsdom 对「未实现的标准 API」「整段解析失败」也会走这里，区分对待：
       只有真正的脚本异常算失败，环境能力缺失只提示。 */
    var fatal = /Uncaught|SyntaxError|TypeError|ReferenceError|is not a function|Cannot read/i.test(message);
    console.log("  " + (fatal ? "[页面异常] " : "[jsdom 提示] ") + message.slice(0, 200));
    if (fatal) {
      scriptErrors.push(message);
    }
  });
  return new JSDOM(HTML, {
    url: "http://localhost/demo/index.html",
    runScripts: "dangerously",
    pretendToBeVisual: true,
    virtualConsole: vc,
    beforeParse: function (w) {
      Object.keys(prefill || {}).forEach(function (key) {
        w.localStorage.setItem(key, prefill[key]);
      });
    }
  });
}

function ready(dom) {
  return new Promise(function (resolve) {
    if (dom.window.document.readyState === "complete") {
      return setTimeout(resolve, 60);
    }
    dom.window.addEventListener("load", function () { setTimeout(resolve, 60); });
  });
}

function wait(ms) {
  return new Promise(function (resolve) { setTimeout(resolve, ms); });
}

/* 统一的生命周期管理：无论用例成功与否都关掉 window，避免定时器把进程吊住 */
async function withDom(prefill, body) {
  var dom = boot(prefill);
  try {
    await ready(dom);
    return await body(dom);
  } finally {
    try { dom.window.close(); } catch (err) { /* ignore */ }
  }
}

/* ---------------------------------------------------------------------------
 * DOM 小工具
 * ------------------------------------------------------------------------- */

function doc(dom) {
  return dom.window.document;
}

function byText(dom, selector, text) {
  var list = Array.prototype.slice.call(doc(dom).querySelectorAll(selector));
  for (var i = 0; i < list.length; i += 1) {
    if (list[i].textContent.indexOf(text) >= 0) { return list[i]; }
  }
  return null;
}

function click(dom, node) {
  if (!node) { throw new Error("click 收到了 null 节点（按钮没找到）"); }
  node.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true, cancelable: true }));
}

function text(dom, id) {
  var node = doc(dom).getElementById(id);
  return node ? node.textContent : "";
}

function isHidden(dom, id) {
  var node = doc(dom).getElementById(id);
  return !node || node.classList.contains("hidden");
}

function questionText(dom) {
  var node = doc(dom).querySelector("#problemBody .question");
  return node ? node.textContent : "";
}

function snapshot(dom) {
  var raw = dom.window.localStorage.getItem(RUN_KEY);
  if (!raw) { return null; }
  try { return JSON.parse(raw); } catch (err) { return { __unparsable: raw }; }
}

function stageBucketOf(dom, story) {
  var raw = dom.window.localStorage.getItem(STAGE_KEY_PREFIX + story);
  return raw ? JSON.parse(raw) : {};
}

function charBucketOf(dom, story) {
  var raw = dom.window.localStorage.getItem(CHAR_KEY_PREFIX + story);
  return raw ? JSON.parse(raw) : {};
}

function bankItem(dom, story, stage, difficulty) {
  var list = ((dom.window.MMQJ.BANK || {}).bank || {})[story] || [];
  for (var i = 0; i < list.length; i += 1) {
    if (Number(list[i].stage) === Number(stage) && Number(list[i].difficulty) === Number(difficulty)) {
      return list[i];
    }
  }
  return null;
}

function sameArray(a, b) {
  if (!a || !b || a.length !== b.length) { return false; }
  for (var i = 0; i < a.length; i += 1) {
    if (a[i] !== b[i]) { return false; }
  }
  return true;
}

/* 找一个满足条件的确定性种子（不赌随机） */
function findSeed(dom, story, stage, predicate) {
  for (var i = 0; i < 600; i += 1) {
    var seed = "resume-seed-" + i;
    var plan = dom.window.MMQJ.drawRun(story, stage, seed);
    if (predicate(plan)) { return seed; }
  }
  return "";
}

/* 存档构造器：只写我们关心的字段，其余走页面默认值 */
function makeSnap(over) {
  var base = {
    story: "xiyou",
    stage: 1,
    seed: "seed-placeholder",
    index: 0,
    elapsed: 0,
    usedHint: false,
    usedSkip: false,
    retry: 0,
    savedAt: new Date().toISOString()
  };
  Object.keys(over || {}).forEach(function (key) { base[key] = over[key]; });
  return base;
}

function prefillWith(snapObj, storyId, extra) {
  var prefill = {};
  prefill[RUN_KEY] = typeof snapObj === "string" ? snapObj : JSON.stringify(snapObj);
  prefill[STORY_KEY] = storyId || "xiyou";
  Object.keys(extra || {}).forEach(function (key) { prefill[key] = extra[key]; });
  return prefill;
}

/* 把当前这一关做完：能跳就跳，跳过之后点「下一题 / 看结算」，直到结算面板出现 */
async function finishRun(dom, stepLimit) {
  var limit = stepLimit || 40;
  for (var i = 0; i < limit; i += 1) {
    if (!isHidden(dom, "resultPanel")) { return true; }
    var skip = byText(dom, "#problemBody button", "跳过本题");
    if (skip && !skip.disabled) {
      click(dom, skip);
      await wait(20);
      continue;
    }
    var next = byText(dom, "#problemBody button", "下一题") || byText(dom, "#problemBody button", "看结算");
    if (!next) { return !isHidden(dom, "resultPanel"); }
    click(dom, next);
    await wait(25);
  }
  return !isHidden(dom, "resultPanel");
}

/* 答当前题：right=按题库答案填；wrong=故意填错 */
async function answerCurrent(dom, item, mode) {
  var input = doc(dom).getElementById("answerInput");
  if (!input) { return false; }
  if (mode === "right") {
    input.value = String(item.answer);
  } else {
    input.value = item.answerType === "number" || item.answerType === "percent"
      ? String(Number(item.answer) + 1000)
      : "999999";
  }
  click(dom, byText(dom, "#problemBody button", "提交答案"));
  await wait(25);
  return true;
}

/* 进入第 1 关并点「开始答题」（真实点击流程） */
async function enterStageOne(dom) {
  var node = doc(dom).querySelector("#nodes g.node.open");
  if (!node) { return false; }
  click(dom, node);
  await wait(50);
  var start = byText(dom, "#stageBody button", "开始答题");
  if (!start) { return false; }
  click(dom, start);
  await wait(70);
  return true;
}

/* 接管页面的 performance.now：让「经过 N 毫秒」不必真等 N 毫秒。
   页面所有计时都走 nowMs() → performance.now()，所以这样能精确复现
   「答了 2 分钟然后刷新」这类攻击，而不用让测试真的跑两分钟。 */
function installClock(dom) {
  var perf = dom.window.performance;
  var base = perf.now();
  var state = { offset: 0 };
  perf.now = function () { return base + state.offset; };
  return {
    ok: typeof perf.now === "function",
    advance: function (ms) { state.offset += ms; }
  };
}

/* 模拟浏览器刷新：pagehide 是浏览器在刷新 / 关标签页 / 跳转时必然派发的事件。
   这条用例的正是不点任何按钮、只靠这个事件落盘。 */
function firePagehide(dom) {
  dom.window.dispatchEvent(new dom.window.Event("pagehide"));
}

/* 反复刷新：每轮推进 perCycleMs 毫秒 → 派发 pagehide → 用存档开新窗口接回。
   这正是 QA 的 A2/A4 攻击手法（每 4 秒左右刷一次，避开 5 秒周期落盘）。 */
async function repeatRefresh(cycles, perCycleMs) {
  var dom = boot({});
  await ready(dom);
  var clock = installClock(dom);
  var moved = await enterStageOne(dom);
  if (!moved) {
    dom.window.close();
    return null;
  }
  var restored = 0;
  for (var i = 0; i < cycles; i += 1) {
    clock.advance(perCycleMs);
    firePagehide(dom);
    await wait(30);
    var raw = dom.window.localStorage.getItem(RUN_KEY);
    var next = boot(prefillWith(raw, "xiyou"));
    dom.window.close();
    dom = next;
    await ready(dom);
    clock = installClock(dom);
  }
  var run = dom.window.MMQJ.runInfo();
  restored = run ? run.elapsed : 0;
  dom.window.close();
  return { real: cycles * perCycleMs, restored: restored, cycles: cycles };
}

/* ---------------------------------------------------------------------------
 * 用例：全部返回 Promise，按顺序执行
 * ------------------------------------------------------------------------- */

var ctx = {};

async function partRules() {
  section("A · 规则口径自证（不赌随机：先证明 30/40/30 真的会抽到 1 题）");
  await withDom({}, async function (dom) {
    var MM = dom.window.MMQJ;
    var weights = MM.RULES.question_count_weights || {};
    ok(
      Number(weights["1"]) === 30 && Number(weights["2"]) === 40 && Number(weights["3"]) === 30,
      "题数权重仍是 30/40/30（所以 N=1 完全合法）",
      JSON.stringify(weights)
    );

    ctx.seedSingle = findSeed(dom, "xiyou", 1, function (plan) {
      return plan.questionCount === 1;
    });
    ctx.seedMulti = findSeed(dom, "xiyou", 1, function (plan) {
      return plan.questionCount >= 2;
    });
    ok(Boolean(ctx.seedSingle), "能筛到「本关只有 1 题」的种子（原测试误报的根源）", ctx.seedSingle || "600 次未命中");
    ok(Boolean(ctx.seedMulti), "能筛到「本关 ≥2 题」的种子（续答场景需要它）", ctx.seedMulti || "600 次未命中");

    /* 干净通关用例要避开分数答案（分数必须精确相等，不适合用「填题库答案」的方式驱动） */
    ctx.seedClean = findSeed(dom, "xiyou", 1, function (plan) {
      return plan.questionCount === 1 && plan.difficulties.every(function (difficulty) {
        var item = bankItem(dom, "xiyou", 1, difficulty);
        return Boolean(item) && item.answerType === "number";
      });
    });
    ok(Boolean(ctx.seedClean), "能筛到「单题 + 纯数值答案」的干净通关种子", ctx.seedClean || "600 次未命中");

    /* drawRun 的结构性口径 */
    var plan = MM.drawRun("xiyou", 1, ctx.seedMulti || "resume-seed-0");
    ok(plan.questionCount >= 1 && plan.questionCount <= 3, "题数落在 1~3", "N=" + plan.questionCount);
    ok(plan.difficulties.length === plan.questionCount, "抽到的难度档个数等于题数");
    var ascending = plan.difficulties.every(function (value, index, list) {
      return index === 0 || list[index - 1] < value;
    });
    ok(ascending, "难度档严格升序且不重复", plan.difficulties.join(","));
    var allInBank = plan.difficulties.every(function (difficulty) {
      return Boolean(bankItem(dom, "xiyou", 1, difficulty));
    });
    ok(allInBank, "抽到的每一档在题库里都有对应题目（续答能重建的前提）");
    ok(Boolean(plan.seed) && plan.seed === (ctx.seedMulti || "resume-seed-0"), "drawRun 原样回显种子", plan.seed);

    /* 演示页内联的规则 == data/run_rules.json（防止页面私改阈值绕过规则）。
       逐字段比数值，不比整段 JSON 字符串——键序不同不代表规则不同。 */
    var mineCount = MM.RULES.question_count_weights || {};
    var srcCount = RULES_JSON.question_count_weights || {};
    var countMatch = ["1", "2", "3"].every(function (key) {
      return Number(mineCount[key]) === Number(srcCount[key]);
    });
    var mineTier = MM.RULES.tier_thresholds || {};
    var srcTier = RULES_JSON.tier_thresholds || {};
    var tierMatch = ["gold", "silver"].every(function (key) {
      return Number(mineTier[key]) === Number(srcTier[key]);
    });
    ok(countMatch, "题数权重与 data/run_rules.json 一致", JSON.stringify(mineCount));
    ok(tierMatch, "品阶阈值与 data/run_rules.json 一致", JSON.stringify(mineTier));
    ok(
      MM.RULES.hint_caps_tier === RULES_JSON.hint_caps_tier
        && Number(MM.RULES.retry_silver_threshold) === Number(RULES_JSON.retry_silver_threshold)
        && Number(MM.RULES.per_question_cap_seconds) === Number(RULES_JSON.per_question_cap_seconds),
      "提示/重试上限与单题上限与 data/run_rules.json 一致"
    );
  });
}

async function partLiveFlow() {
  section("B · 真实点击流程：开始答题 → 提示 → 答错 → 刷新接回 → 做完结算");
  await withDom({}, async function (dom) {
    var moved = await enterStageOne(dom);
    ok(moved, "地图节点 →「开始答题」可进入答题页");
    if (!moved) { return; }

    var MM = dom.window.MMQJ;
    var run = MM.runInfo();
    ok(Boolean(run), "runInfo 可读");
    if (!run) { return; }

    ctx.live = { story: run.story, stage: run.stage, seed: run.seed, n: run.questionCount, difficulties: run.difficulties.slice(0) };
    ok(
      new RegExp("^第 1/" + run.questionCount + " 题").test(text(dom, "panelTitle")),
      "首题题号为 1/N（N 读实际值，不假设 >1）",
      text(dom, "panelTitle")
    );
    info("本局实际题数", "N=" + run.questionCount + "（随机抽到，1 也合法）");
    ok(snapshot(dom) !== null, "开始答题后立即写入断点存档");

    var snap0 = snapshot(dom);
    var required = ["story", "stage", "seed", "index", "elapsed", "usedHint", "usedSkip", "retry", "savedAt"];
    var missing = required.filter(function (key) { return snap0[key] === undefined; });
    ok(missing.length === 0, "存档字段齐全（story/stage/seed/index/elapsed/usedHint/usedSkip/retry/savedAt）", missing.join(",") || "无缺失");
    ok(snap0.seed === run.seed && snap0.index === 0, "存档 seed / index 与内存一致", "seed=" + snap0.seed + " index=" + snap0.index);

    /* 提示：usedHint 落盘，且不会让已用时间倒退 */
    var hintBtn = byText(dom, "#problemBody button", "求提示");
    ok(Boolean(hintBtn), "答题页有「求提示」按钮");
    click(dom, hintBtn);
    await wait(40);
    var snapHint = snapshot(dom);
    ok(snapHint.usedHint === true, "点提示后存档 usedHint=true");
    ok(snapHint.elapsed >= snap0.elapsed, "点提示不会让已用时间倒退", snapHint.elapsed + " >= " + snap0.elapsed);

    /* 答错：retry 落盘 */
    var item = bankItem(dom, run.story, run.stage, run.difficulties[0]);
    ok(Boolean(item), "题库里能找到当前题（用作判题 oracle）");
    await answerCurrent(dom, item, "wrong");
    var afterWrong = MM.runInfo();
    ok(afterWrong.retry >= 1, "答错一次后 run.retry 递增", "retry=" + afterWrong.retry);
    ok(snapshot(dom).retry === afterWrong.retry, "存档 retry 与内存一致");
    ok(dom.window.localStorage.getItem(RUN_KEY) !== null, "答错后存档仍在（不会因为答错被清）");

    ctx.live.qText = questionText(dom);
    ctx.live.snap = dom.window.localStorage.getItem(RUN_KEY);
    ctx.live.elapsed = snapshot(dom).elapsed;
  });

  /* 刷新接回：新 window + 同一份 localStorage */
  await withDom(prefillWith(ctx.live.snap, ctx.live.story), async function (dom) {
    ok(!isHidden(dom, "problemPanel"), "刷新后直接停在答题页（而不是地图/关卡面板）");
    ok(isHidden(dom, "stagePanel"), "关卡面板没有挡住答题页");

    var after = dom.window.MMQJ.runInfo();
    ok(Boolean(after), "接回后 runInfo 可读");
    if (!after) { return; }

    ok(after.seed === ctx.live.seed, "接回后种子没变（同一场，不会换题）", after.seed);
    ok(after.index === 0, "接回后题号一致", "index=" + after.index);
    ok(sameArray(after.difficulties, ctx.live.difficulties), "接回后难度档序列一致", after.difficulties.join(","));
    ok(questionText(dom) === ctx.live.qText, "接回后题面文本与刷新前完全一致", questionText(dom) === ctx.live.qText ? "" : "不一致");
    ok(after.usedHint === true, "接回后 usedHint 保留（品阶上限不会被刷回来）");
    ok(after.retry >= 1, "接回后 retry 保留", "retry=" + after.retry);
    ok(after.elapsed >= ctx.live.elapsed - 5, "接回后已用时间没有被清零", "接回 " + Math.round(after.elapsed) + " vs 存档 " + Math.round(ctx.live.elapsed));
    ok(after.elapsed <= ctx.live.elapsed + 3000, "接回后已用时间没有凭空膨胀", Math.round(after.elapsed) + " ms");

    var oracle = bankItem(dom, after.story, after.stage, after.difficulties[after.index]);
    ok(Boolean(oracle) && oracle.question === questionText(dom), "题面与题库原文一致（种子确定性重建成立）");

    /* 做完本关：跳过 → 结算 → 存档必须清掉 */
    var settled = await finishRun(dom);
    ok(settled, "接力把本关做完并进入结算面板");
    ok(dom.window.localStorage.getItem(RUN_KEY) === null, "结算后断点存档已清除（下次不会误跳答题页）");
    var bucket = stageBucketOf(dom, ctx.live.story);
    ok(Boolean(bucket[String(ctx.live.stage)] && bucket[String(ctx.live.stage)].cleared), "通关记录仍正常写入（原有落盘逻辑未受影响）");
    ok(bucket[String(ctx.live.stage)].tier === null, "本场用过跳过 → 不计品阶（规则没被续答绕过）", String(bucket[String(ctx.live.stage)].tier));
  });
}

async function partDeterministic() {
  section("C1 · 用固定种子验证「刷新接回」的完整状态（index / 题面 / 计时 / 提示 / 重试）");
  var planMulti = null;
  await withDom(prefillWith(makeSnap({ seed: ctx.seedMulti, index: 1, elapsed: 12345, usedHint: true, retry: 2 })), async function (dom) {
    ok(!isHidden(dom, "problemPanel"), "存档 index=1 → 自动接回答题页");
    var run = dom.window.MMQJ.runInfo();
    planMulti = dom.window.MMQJ.drawRun("xiyou", 1, ctx.seedMulti);
    ok(Boolean(run), "runInfo 可读");
    if (!run) { return; }
    ok(run.index === 1, "题号接回 index=1", "index=" + run.index);
    ok(run.questionCount === planMulti.questionCount, "题数与 drawRun 一致", run.questionCount + " vs " + planMulti.questionCount);
    ok(sameArray(run.difficulties, planMulti.difficulties), "难度档序列与 drawRun 一致", run.difficulties.join(","));
    ok(
      text(dom, "panelTitle") === "第 2/" + planMulti.questionCount + " 题 · 第 " + planMulti.difficulties[1] + " 难",
      "标题按第 2 题渲染",
      text(dom, "panelTitle")
    );
    ok(run.usedHint === true && run.retry === 2, "usedHint / retry 原样还原", "usedHint=" + run.usedHint + " retry=" + run.retry);
    ok(run.usedSkip === false, "usedSkip 原样还原");
    ok(Math.abs(run.elapsed - 12345) <= 300, "已用时间原样还原（不清零、不重算）", Math.round(run.elapsed) + " ms");
    var oracle = bankItem(dom, "xiyou", 1, planMulti.difficulties[1]);
    ok(Boolean(oracle) && oracle.question === questionText(dom), "题面与题库原文一致");

    /* C9b：接回后继续答题，当前这半题的时间必须算进存档（回归「刷新白送时间」） */
    await wait(420);
    click(dom, byText(dom, "#problemBody button", "求提示"));
    await wait(60);
    var saved = snapshot(dom);
    var grew = saved.elapsed - 12345;
    ok(grew >= 250, "接回后再答题，这段时间会计入存档（不会白送）", "+" + Math.round(grew) + " ms");
    ok(grew <= 3000, "计入的增量符合实际等待时间（没有重复累加）", "+" + Math.round(grew) + " ms");
  });

  section("C1b · usedSkip=true 的存档接回后，仍然不计品阶（跳过的代价不能被刷新抹掉）");
  await withDom(prefillWith(makeSnap({ seed: ctx.seedSingle, index: 0, elapsed: 6000, usedSkip: true })), async function (dom) {
    var run = dom.window.MMQJ.runInfo();
    ok(Boolean(run) && run.usedSkip === true, "接回后 usedSkip 保留", run ? String(run.usedSkip) : "?");
    var item = bankItem(dom, "xiyou", 1, run.difficulties[0]);
    await answerCurrent(dom, item, "right");
    var next = byText(dom, "#problemBody button", "下一题") || byText(dom, "#problemBody button", "看结算");
    if (next) { click(dom, next); await wait(60); }
    ok(!isHidden(dom, "resultPanel"), "进入结算面板");
    var bucket = stageBucketOf(dom, "xiyou");
    ok(bucket["1"] && bucket["1"].tier === null, "曾经跳过 → 本场仍然不计品阶", bucket["1"] ? String(bucket["1"].tier) : "无记录");
  });

  section("C2 · 同一份存档开两次：题目必须完全一致（确定性重建）");
  var renders = [];
  for (var i = 0; i < 2; i += 1) {
    /* 两个 window 各自独立启动，只共享同一条存档 */
    await withDom(prefillWith(makeSnap({ seed: ctx.seedMulti, index: 1, elapsed: 1000 })), async function (dom) {
      var run = dom.window.MMQJ.runInfo();
      renders.push({
        seed: run.seed,
        difficulties: run.difficulties.join(","),
        question: questionText(dom),
        scene: run.sceneText
      });
    });
  }
  ok(renders.length === 2, "两次装载都成功");
  ok(renders[0].question === renders[1].question && renders[0].question !== "", "两次装载的题面完全一致", renders[0].question.slice(0, 24));
  ok(renders[0].difficulties === renders[1].difficulties, "两次装载的难度档一致", renders[0].difficulties);
  ok(renders[0].seed === renders[1].seed && renders[0].seed === ctx.seedMulti, "两次装载的种子一致");
  ok(renders[0].scene === renders[1].scene, "两次装载的场景文案一致（同一种子 → 同一场景）", renders[0].scene);

  section("C3 · 只有 1 题的关卡：接回 → 跳过 → 结算 → 清档（原测试在这里崩的）");
  await withDom(prefillWith(makeSnap({ seed: ctx.seedSingle, index: 0, elapsed: 8000 })), async function (dom) {
    var run = dom.window.MMQJ.runInfo();
    ok(Boolean(run) && run.questionCount === 1, "N=1 的关卡能正常接回", "N=" + (run ? run.questionCount : "?"));
    ok(text(dom, "panelTitle").indexOf("第 1/1 题") === 0, "标题渲染为第 1/1 题", text(dom, "panelTitle"));
    var skip = byText(dom, "#problemBody button", "跳过本题");
    ok(Boolean(skip), "N=1 时「跳过本题」照常存在");
    click(dom, skip);
    await wait(40);
    var next = byText(dom, "#problemBody button", "下一题") || byText(dom, "#problemBody button", "看结算");
    ok(Boolean(next), "跳过后出现「下一题 / 看结算」（原测试硬断言「下一题」才崩）", next ? next.textContent : "没找到");
    if (next) {
      click(dom, next);
      await wait(60);
    }
    ok(!isHidden(dom, "resultPanel"), "N=1 的关卡能走到结算面板");
    ok(dom.window.localStorage.getItem(RUN_KEY) === null, "结算后存档已清除");
  });

  section("C4 · 已通关关卡重玩：品阶只升不降，跳过不会把黄金压回青铜");
  var goldStage = {};
  goldStage["1"] = { cleared: true, tier: "gold", at: new Date().toISOString(), scene: "旧场景", seed: "old-seed" };
  await withDom(prefillWith(makeSnap({ seed: ctx.seedSingle, index: 0 }), "xiyou", {
    [STAGE_KEY_PREFIX + "xiyou"]: JSON.stringify(goldStage),
    [CHAR_KEY_PREFIX + "xiyou"]: JSON.stringify({})
  }), async function (dom) {
    ok(!isHidden(dom, "problemPanel"), "重玩已通关关卡时也能接回存档");
    await finishRun(dom);
    var bucket = stageBucketOf(dom, "xiyou");
    ok(bucket["1"] && bucket["1"].tier === "gold", "重玩跳过（tier=null）后 best_tier 仍为 gold", bucket["1"] ? String(bucket["1"].tier) : "无记录");
    ok(Object.keys(charBucketOf(dom, "xiyou")).length === 0, "跳过不计品阶 → 不新增角色卡");
  });

  section("C5 · 干净通关：应拿黄金 + 收卡 + 解锁介绍（规则没被续答绕过）");
  await withDom(prefillWith(makeSnap({ seed: ctx.seedClean, index: 0 })), async function (dom) {
    var run = dom.window.MMQJ.runInfo();
    ok(Boolean(run) && run.questionCount === 1, "干净通关用例抽到单题", "N=" + (run ? run.questionCount : "?"));
    var item = bankItem(dom, "xiyou", 1, run.difficulties[0]);
    await answerCurrent(dom, item, "right");
    var next = byText(dom, "#problemBody button", "下一题") || byText(dom, "#problemBody button", "看结算");
    ok(Boolean(next), "答对后出现推进按钮");
    if (next) { click(dom, next); await wait(60); }
    ok(!isHidden(dom, "resultPanel"), "进入结算面板");
    var bucket = stageBucketOf(dom, "xiyou");
    ok(bucket["1"] && bucket["1"].tier === "gold", "快速全对 → 黄金", bucket["1"] ? String(bucket["1"].tier) : "无记录");
    var chars = charBucketOf(dom, "xiyou");
    var names = Object.keys(chars);
    ok(names.length === 2, "通关收下闯关大将 + 守关大将两张卡", names.join(","));
    ok(names.every(function (key) { return chars[key].intro === true; }), "干净通关才解锁介绍，本次全部解锁");
    ok(dom.window.localStorage.getItem(RUN_KEY) === null, "结算后存档已清除");
  });
}

async function partEdges() {
  section("C6 · 存档指向未解锁关卡：应清档退回，不能把人塞进锁着的关");
  var lockedSnap = makeSnap({ stage: 2, index: 0, seed: ctx.seedMulti });
  await withDom(prefillWith(lockedSnap), async function (dom) {
    ok(isHidden(dom, "problemPanel"), "未解锁关卡的存档不会把页面顶到答题页");
    ok(dom.window.localStorage.getItem(RUN_KEY) === null, "无效解锁状态的存档被清除");
    var unlocked = dom.window.MMQJ.isStageUnlocked("xiyou", 2);
    ok(unlocked === false, "自证第 2 关在无通关记录时确实未解锁", "isStageUnlocked=" + unlocked);
  });

  section("C7 · 换故事线：A 线存档不会错接到 B 线（也不能误清）");
  await withDom(prefillWith(makeSnap({ seed: ctx.seedMulti, index: 1, elapsed: 4321 }), "sanguo"), async function (dom) {
    ok(isHidden(dom, "problemPanel"), "当前线是三国时，不会接回西游记的存档");
    ok(doc(dom).querySelectorAll("#nodes g.node").length > 0, "地图仍正常渲染（没有白屏）");
    ok(dom.window.localStorage.getItem(RUN_KEY) !== null, "别的线的存档保留着，不该被误清");
  });
  await withDom(prefillWith(makeSnap({ seed: ctx.seedMulti, index: 1, elapsed: 4321 }), "xiyou"), async function (dom) {
    ok(!isHidden(dom, "problemPanel"), "切回西游记后仍能接回原进度（只是延后，不是丢弃）");
    var run = dom.window.MMQJ.runInfo();
    ok(Boolean(run) && run.index === 1, "接回的题号仍是第 2 题");
  });

  section("C8 · 存档损坏 / 越界：一律安全降级，不白屏、不崩");
  var badCases = [
    { label: "非法 JSON", raw: "{not json at all" },
    { label: "空对象", raw: "{}" },
    { label: "story 指向不存在的线", raw: JSON.stringify(makeSnap({ story: "nope", seed: ctx.seedMulti })), expectCleared: true },
    { label: "stage 越界（999）", raw: JSON.stringify(makeSnap({ stage: 999, seed: ctx.seedMulti })), expectCleared: true },
    { label: "index 越界（99）", raw: JSON.stringify(makeSnap({ index: 99, seed: ctx.seedMulti })), expectCleared: true },
    { label: "seed 缺失", raw: JSON.stringify(makeSnap({ seed: "" })) }
  ];
  for (var i = 0; i < badCases.length; i += 1) {
    var bad = badCases[i];
    var before = scriptErrors.length;
    await withDom(prefillWith(bad.raw, "xiyou"), async function (dom) {
      ok(isHidden(dom, "problemPanel"), "[" + bad.label + "] 不会被当成可接回的存档");
      ok(doc(dom).querySelectorAll("#nodes g.node").length > 0, "[" + bad.label + "] 地图照常渲染（没有白屏）");
      ok(scriptErrors.length === before, "[" + bad.label + "] 页面没有抛出脚本异常");
      if (bad.expectCleared) {
        ok(dom.window.localStorage.getItem(RUN_KEY) === null, "[" + bad.label + "] 这条接不回来的存档被清掉（不留僵尸）");
      }
    });
  }

  /* 自愈：坏存档之后正常开局，应该把它覆盖成合法存档 */
  await withDom(prefillWith(badCases[0].raw, "xiyou"), async function (dom) {
    var moved = await enterStageOne(dom);
    ok(moved, "[非法 JSON] 之后仍能正常进关开始答题（自愈）");
    var healed = snapshot(dom);
    ok(Boolean(healed) && typeof healed === "object" && Boolean(healed.seed), "开局后存档被覆盖成合法结构", healed ? String(healed.seed) : "无");
  });

  section("C9 · 陈旧存档（savedAt 一周前）：不崩，且不会把离线时间算进用时");
  var weekAgo = new Date(Date.now() - 7 * 24 * 3600 * 1000).toISOString();
  await withDom(prefillWith(makeSnap({ seed: ctx.seedMulti, index: 1, elapsed: 12345, savedAt: weekAgo })), async function (dom) {
    ok(!isHidden(dom, "problemPanel"), "一周前的存档仍能接回（设计上不做时效淘汰，见报告）");
    var run = dom.window.MMQJ.runInfo();
    ok(Boolean(run) && Math.abs(run.elapsed - 12345) <= 300, "离线一周不会把 7 天算进已用时间", Math.round(run ? run.elapsed : -1) + " ms");
    await wait(120);
    click(dom, byText(dom, "#problemBody button", "求提示"));
    await wait(60);
    var saved = snapshot(dom);
    ok(saved.elapsed < 12345 + 3000, "落盘的仍是「本局已用时间」，不是「存档以来经过的时间」", Math.round(saved.elapsed) + " ms");
  });

  section("C10 · 计时口径回归：接回不清零、已用时间会继续累计");
  /* 注意：这里**主动点了「求提示」**触发落盘，测的是「live elapsed 是否算进当前题」。
     「完全不点按钮、直接刷新」的场景由 C10b / C10c 覆盖——
     之前正是只在 C10 里靠点提示落盘，才漏掉了「刷新那一刻没落盘」的缺口。 */
  await withDom(prefillWith(makeSnap({ seed: ctx.seedMulti, index: 0, elapsed: 10000 })), async function (dom) {
    var run = dom.window.MMQJ.runInfo();
    ok(Math.abs(run.elapsed - 10000) <= 300, "接回时已用时间保持不变", Math.round(run.elapsed) + " ms");
    await wait(500);
    var live = dom.window.MMQJ.runInfo();
    ok(live.elapsed - 10000 >= 400, "停留的这 500ms 会计入运行中的已用时间（不能白送）", "+" + Math.round(live.elapsed - 10000) + " ms");
    click(dom, byText(dom, "#problemBody button", "求提示"));
    await wait(60);
    var saved = snapshot(dom);
    ok(saved.elapsed - 10000 >= 400, "落盘的 elapsed 包含这半题（刷新不会抹掉它）", "+" + Math.round(saved.elapsed - 10000) + " ms");
  });

  section("C10b · 攻击复现 A1：虚拟时钟走 120 秒后直接刷新，期间不点任何按钮");
  var a1Raw = null;
  await withDom({}, async function (dom) {
    var clock = installClock(dom);
    ok(clock.ok, "能接管页面虚拟时钟（复现 QA 的攻击手法）");
    var moved = await enterStageOne(dom);
    ok(moved, "进入第 1 关并开始答题");
    if (!moved) { return; }
    clock.advance(120000);
    firePagehide(dom);            // 只派发「刷新」事件，绝不点提示 / 下一题
    await wait(50);
    var saved = snapshot(dom);
    ok(Boolean(saved), "刷新这一刻写出了存档");
    ok(
      Boolean(saved) && saved.elapsed >= 120000 * 0.85,
      "A1：刷新时已用的 120 秒被落盘（修复前这里落的是 0）",
      saved ? Math.round(saved.elapsed) + "ms" : "无存档"
    );
    a1Raw = dom.window.localStorage.getItem(RUN_KEY);
  });

  await withDom(prefillWith(a1Raw, "xiyou"), async function (dom) {
    var run = dom.window.MMQJ.runInfo();
    ok(Boolean(run) && run.elapsed >= 120000 * 0.85, "A1：接回后 120 秒没有归零", run ? Math.round(run.elapsed) + "ms" : "无法接回");
    ok(Boolean(run) && run.elapsed <= 125000, "A1：接回后也没有多算（不按 wall-clock 补离线时长）", run ? Math.round(run.elapsed) + "ms" : "?");
  });

  section("C10c · 攻击复现 A2 / A4：反复刷新不能把用时无限抹掉");
  var a2 = await repeatRefresh(4, 4000);
  ok(Boolean(a2), "A2：4 轮「4 秒 → 刷新 → 接回」流程跑通");
  if (a2) {
    ok(
      a2.restored >= a2.real * 0.85,
      "A2（4 次 ×4s）：累计用时 ≥ 真实流逝的 85%（修复前只剩 0.4%）",
      Math.round(a2.restored) + "/" + a2.real + "ms"
    );
    ok(a2.restored <= a2.real * 1.25, "A2：累计用时没有虚增", Math.round(a2.restored) + "ms");
  }
  var a4 = await repeatRefresh(8, 4200);
  ok(Boolean(a4), "A4：8 轮「4.2 秒 → 刷新 → 接回」流程跑通");
  if (a4) {
    ok(
      a4.restored >= a4.real * 0.85,
      "A4（8 次 ×4.2s）：累计用时 ≥ 真实流逝的 85%（修复前 33.6 秒 → 0.11 秒）",
      Math.round(a4.restored) + "/" + a4.real + "ms"
    );
    ok(a4.restored <= a4.real * 1.25, "A4：累计用时没有虚增", Math.round(a4.restored) + "ms");
  }

  section("C11 · 答错次数必须落盘：刷新不能把错题记录洗掉（否则白拿黄金 + 解锁介绍）");
  var retrySnap = null;
  await withDom(prefillWith(makeSnap({ seed: ctx.seedClean, index: 0 })), async function (dom) {
    var run = dom.window.MMQJ.runInfo();
    var item = bankItem(dom, "xiyou", 1, run.difficulties[0]);
    for (var i = 0; i < 3; i += 1) {
      await answerCurrent(dom, item, "wrong");
    }
    var live = dom.window.MMQJ.runInfo();
    ok(live.retry === 3, "连错 3 次，run.retry = 3", "retry=" + live.retry);
    var saved = snapshot(dom);
    ok(saved.retry === 3, "连错 3 次后存档里的 retry 也是 3（答错即刻落盘）", "retry=" + saved.retry);
    retrySnap = dom.window.localStorage.getItem(RUN_KEY);
  });

  await withDom(prefillWith(retrySnap, "xiyou"), async function (dom) {
    var run = dom.window.MMQJ.runInfo();
    ok(Boolean(run) && run.retry === 3, "刷新后 retry=3 仍在（错题记录没被洗掉）", "retry=" + (run ? run.retry : "?"));
    var item = bankItem(dom, "xiyou", 1, run.difficulties[run.index]);
    await answerCurrent(dom, item, "right");
    var next = byText(dom, "#problemBody button", "下一题") || byText(dom, "#problemBody button", "看结算");
    if (next) { click(dom, next); await wait(60); }
    ok(!isHidden(dom, "resultPanel"), "进入结算面板");
    var bucket = stageBucketOf(dom, "xiyou");
    ok(bucket["1"] && bucket["1"].tier === "silver", "错 3 次后封顶白银（规则没被刷新绕过）", bucket["1"] ? String(bucket["1"].tier) : "无记录");
    var chars = charBucketOf(dom, "xiyou");
    var introOn = Object.keys(chars).filter(function (key) { return chars[key].intro === true; });
    ok(introOn.length === 0, "非干净通关（有错题记录）→ 角色介绍不解锁", "已解锁 " + introOn.length + " 张");
  });
}

async function partRuleIntegrity() {
  section("D · 品阶判定口径（与 CLI 一致的那套规则没被改动）");
  await withDom({}, async function (dom) {
    var MM = dom.window.MMQJ;
    var budget = MM.budgetFor([{ grade: 3 }, { grade: 3 }]);   // 45s + 45s = 90s
    ok(budget === 90000, "三年级两题的基准是 90 秒", budget + " ms");
    ok(MM.gradeTier(0, budget, false, false, 0) === "gold", "零用时 + 无提示无错 → 黄金");
    ok(MM.gradeTier(budget * 0.8, budget, false, false, 0) === "silver", "ratio 0.8 → 白银");
    ok(MM.gradeTier(budget * 2, budget, false, false, 0) === "bronze", "ratio 2.0 → 青铜");
    ok(MM.gradeTier(0, budget, true, false, 0) === "silver", "用过提示 → 上限白银");
    ok(MM.gradeTier(0, budget, false, false, 3) === "silver", "答错 ≥3 次 → 上限白银");
    ok(MM.gradeTier(0, budget, false, true, 0) === null, "用过跳过 → 不计品阶");
  });
}

/* ---------------------------------------------------------------------------
 * 汇总
 * ------------------------------------------------------------------------- */

(async function main() {
  console.log("断点续答回归测试（demo/index.html · RUN_KEY=" + RUN_KEY + "）");
  await partRules();
  await partLiveFlow();
  await partDeterministic();
  await partEdges();
  await partRuleIntegrity();

  section("结果");
  info("断言总数", String(checks));
  info("失败断言", String(failures.length));
  info("页面脚本异常", String(scriptErrors.length));

  var bad = failures.length > 0 || scriptErrors.length > 0 || checks === 0;
  if (checks === 0) {
    console.error("[门禁] 一条断言都没执行，判定失败");
  }
  if (failures.length) {
    console.error("失败项：" + failures.join(" / "));
  }
  if (scriptErrors.length) {
    console.error("页面脚本异常：" + scriptErrors.join(" / "));
  }
  clearTimeout(watchdog);
  if (bad) {
    console.error("结果：失败");
    process.exit(1);
  }
  console.log("结果：全部通过");
  process.exit(0);
})().catch(function (err) {
  console.error("[异常]", err && err.stack ? err.stack : err);
  clearTimeout(watchdog);
  process.exit(1);
});
