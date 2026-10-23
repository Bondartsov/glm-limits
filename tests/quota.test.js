// GLM Limits — тесты чистой логики разбора квот (node --test tests/).
// Запуск:  node --test tests/
const { test } = require("node:test");
const assert = require("node:assert");
const GLMQuota = require("../quota.js");

// Реальный ответ API с подписки на bigmodel.cn (кредитные окна).
const CREDIT_RESPONSE = [
  { type: "CREDIT_LIMIT", unit: 3, number: 5, usage: 28000, currentValue: 1086,
    remaining: 26913, percentage: 3, nextResetTime: 1792602151287 },
  { type: "CREDIT_LIMIT", unit: 6, number: 1, usage: 140000, currentValue: 9781,
    remaining: 130218, percentage: 6, nextResetTime: 1793094553964 },
];

// Ответ с токенными окнами (площадка z.ai).
const TOKENS_RESPONSE = [
  { type: "TOKENS_LIMIT", unit: 3, number: 5, percentage: 62, nextResetTime: 1725000000000 },
  { type: "TOKENS_LIMIT", unit: 6, number: 1, percentage: 41, nextResetTime: 1725700000000 },
  { type: "TIME_LIMIT", unit: 3, number: 1, percentage: 10, nextResetTime: 1727700000000 },
];

test("parseLimits: CREDIT_LIMIT (bigmodel.cn) даёт сессию и неделю", () => {
  const r = GLMQuota.parseLimits(CREDIT_RESPONSE);
  assert.ok(r.session, "сессия должна распознаться");
  assert.ok(r.weekly, "неделя должна распознаться");
  assert.equal(r.session.utilization, 3);
  assert.equal(r.weekly.utilization, 6);
  assert.equal(r.mcp, null, "TIME_LIMIT отсутствует — MCP пустой");
});

test("parseLimits: CREDIT_LIMIT сохраняет абсолютные значения", () => {
  const r = GLMQuota.parseLimits(CREDIT_RESPONSE);
  assert.equal(r.session.total, 28000);
  assert.equal(r.session.spent, 1086);
  assert.equal(r.session.remaining, 26913);
});

test("parseLimits: TOKENS_LIMIT + TIME_LIMIT (z.ai)", () => {
  const r = GLMQuota.parseLimits(TOKENS_RESPONSE);
  assert.equal(r.session.utilization, 62);
  assert.equal(r.weekly.utilization, 41);
  assert.equal(r.mcp.utilization, 10);
});

test("parseLimits: прочие часовые/недельные окна идут как fallback", () => {
  const r = GLMQuota.parseLimits([
    { type: "TOKENS_LIMIT", unit: 3, number: 2, percentage: 5, nextResetTime: 1725000000000 },
    { type: "TOKENS_LIMIT", unit: 6, number: 4, percentage: 7, nextResetTime: 1725700000000 },
  ]);
  assert.equal(r.session.utilization, 5);
  assert.equal(r.weekly.utilization, 7);
});

test("parseLimits: битый вход не роняет разбор", () => {
  assert.deepEqual(GLMQuota.parseLimits(null).session, null);
  assert.deepEqual(GLMQuota.parseLimits(undefined).session, null);
  assert.deepEqual(GLMQuota.parseLimits([null, 42, "x"]).session, null);
  assert.deepEqual(GLMQuota.parseLimits([{ type: "WEIRD", unit: 3, number: 5 }]).session, null);
});

test("normalizeWindow: проценты обрезаются в 0..100, время — ISO", () => {
  const w = GLMQuota.normalizeWindow({ percentage: 140, nextResetTime: 1725000000000 });
  assert.equal(w.utilization, 100);
  assert.equal(w.resetsAt, new Date(1725000000000).toISOString());
  const noReset = GLMQuota.normalizeWindow({ percentage: 5 });
  assert.equal(noReset.resetsAt, null);
  assert.equal(noReset.utilization, 5);
});

test("detectPlatform: формат ключа определяет площадку", () => {
  assert.equal(GLMQuota.detectPlatform("id.secret.part"), "cn");
  assert.equal(GLMQuota.detectPlatform("zai_abc123"), "intl");
  assert.equal(GLMQuota.detectPlatform("ZAI-abc"), "intl");
  assert.equal(GLMQuota.detectPlatform("opaquekeywithoutdot"), "intl");
  assert.equal(GLMQuota.detectPlatform(""), "cn");
});

test("badgeText: 100 не влезает в бейдж — показываем 99+", () => {
  assert.equal(GLMQuota.badgeText(100), "99+");
  assert.equal(GLMQuota.badgeText(99.6), "99+", "округление до 100 -> 99+");
  assert.equal(GLMQuota.badgeText(62), "62");
  assert.equal(GLMQuota.badgeText(0), "0");
  assert.equal(GLMQuota.badgeText(null), "");
  assert.equal(GLMQuota.badgeText(undefined), "");
});

test("badgeColor: пороги совпадают с полосками", () => {
  assert.equal(GLMQuota.badgeColor(10), GLMQuota.BADGE_COLORS.green);
  assert.equal(GLMQuota.badgeColor(60), GLMQuota.BADGE_COLORS.yellow);
  assert.equal(GLMQuota.badgeColor(80), GLMQuota.BADGE_COLORS.amber);
  assert.equal(GLMQuota.badgeColor(95), GLMQuota.BADGE_COLORS.red);
});

test("notifyDecision: порог срабатывает один раз на окно", () => {
  let state = null;
  const winKey = "2026-10-21T17:02:31.287Z";

  let d = GLMQuota.notifyDecision(state, 80, winKey);
  assert.equal(d.threshold, 80, "первое пересечение 80% — уведомляем");
  state = d.state;

  d = GLMQuota.notifyDecision(state, 81, winKey);
  assert.equal(d.threshold, null, "81% — тишина");
  d = GLMQuota.notifyDecision(state, 85, winKey);
  assert.equal(d.threshold, null, "85% — тишина");
  state = d.state;

  d = GLMQuota.notifyDecision(state, 92, winKey);
  assert.equal(d.threshold, 90, "пересекли 90% — уведомляем");
  state = d.state;

  d = GLMQuota.notifyDecision(state, 99, winKey);
  assert.equal(d.threshold, null, "99% — тишина");
});

test("notifyDecision: скачок через 80% не даёт каскада", () => {
  const d = GLMQuota.notifyDecision(null, 95, "w1");
  assert.equal(d.threshold, 90, "одно уведомление о highest пройденном пороге");
  assert.equal(d.state.s80, true, "нижний порог тоже помечен пройденным");
  assert.equal(d.state.s90, true);
});

test("notifyDecision: новое окно сброса перезаряжает пороги", () => {
  let d = GLMQuota.notifyDecision(null, 80, "window-1");
  assert.equal(d.threshold, 80);
  d = GLMQuota.notifyDecision(d.state, 80, "window-2");
  assert.equal(d.threshold, 80, "в новом окне порог снова срабатывает");
});

test("activityWindow: скользящее окно вокруг текущего часа", () => {
  const now = new Date(2026, 9, 22, 15, 30, 0).getTime();   // 15:30
  const pts = [
    { ts: now - 3600e3, v: 10 },   // 14:30
    { ts: now - 1800e3, v: 25 },   // 15:00 → +15
  ];
  const win = GLMQuota.activityWindow(pts, now, 6, 3);
  assert.equal(win.bars.length, 6);
  assert.equal(win.bars[3].isNow, true, "четвёртый столбец — текущий час");
  assert.equal(win.bars[3].hour, 15);
  assert.equal(win.bars[0].hour, 12, "три часа назад");
  assert.equal(win.bars[5].hour, 17, "через два часа");
  assert.equal(win.bars[3].value, 15);
  assert.equal(win.bars[4].isFuture, true, "будущие часы помечены");
});

test("dailyActivity: расход раскладывается по дням недели (Пн..Вс)", () => {
  // Понедельник 2026-10-19 12:00 по местному
  const mon = new Date(2026, 9, 19, 12, 0, 0).getTime();
  const pts = [
    { ts: mon - 3600e3, v: 10 },   // 11:00 пн
    { ts: mon, v: 40 },             // 12:00 пн → +30
    { ts: mon + 86400e3, v: 55 },   // вт → +15
  ];
  const buckets = GLMQuota.dailyActivity(pts, mon + 86400e3);
  assert.equal(buckets.length, 7);
  assert.equal(buckets[0].value, 30, "понедельник: прирост 30 в 12:00");
  assert.equal(buckets[0].has, true);
  assert.equal(buckets[1].value, 15, "вторник: прирост 15");
  assert.equal(buckets[1].has, true);
  assert.equal(buckets[2].has, false, "среда без расхода");
});

test("formatAmount: разделители разрядов по языку", () => {
  const norm = (s) => s.replace(/[  ]/g, " ");
  assert.equal(norm(GLMQuota.formatAmount(26913, "ru")), "26 913");
  assert.equal(norm(GLMQuota.formatAmount(26913, "en")), "26,913");
  assert.equal(GLMQuota.formatAmount(null, "ru"), null);
});

test("hourlyActivity: расход раскладывается по часам суток (0..23)", () => {
  const now = new Date(2026, 9, 22, 15, 0, 0).getTime();   // 15:00 по местному
  const pts = [
    { ts: now - 3600e3, v: 10 },   // 14:00
    { ts: now - 1800e3, v: 25 },   // 14:30 → +15
    { ts: now - 600e3, v: 30 },    // 14:50 → +5
  ];
  const buckets = GLMQuota.hourlyActivity(pts, now);
  assert.equal(buckets.length, 24);
  assert.equal(buckets[14].value, 20, "в час 14 должно быть 15 + 5");
  assert.equal(buckets[14].has, true);
  assert.equal(buckets[3].has, false, "пустой час не отмечается");
  assert.equal(buckets[3].value, 0);
});

test("hourlyActivity: сброс окна не считается активностью", () => {
  const now = new Date(2026, 9, 22, 15, 0, 0).getTime();
  const pts = [
    { ts: now - 1800e3, v: 90 },
    { ts: now - 600e3, v: 5 },   // окно сбросилось — это не расход
  ];
  const buckets = GLMQuota.hourlyActivity(pts, now);
  assert.equal(buckets[14].value, 0, "откат (сброс) не должен попадать в активность");
  assert.equal(buckets[14].has, false);
});

test("parseModelUsage: доли по моделям, нулевые отбрасываются", () => {
  const data = {
    x_time: ["2026-10-22 00:00", "2026-10-22 01:00"],
    modelDataList: [
      { modelName: "GLM-5.3", totalTokens: 431887322 },
      { modelName: "GLM-5.3-Flash", totalTokens: 355528176 },
      { modelName: "Zero", totalTokens: 0 },
    ],
    totalUsage: { totalModelCallCount: 4305, totalTokensUsage: 787415498 },
  };
  const r = GLMQuota.parseModelUsage(data);
  assert.equal(r.models.length, 2, "модель с нулём отбрасывается");
  assert.equal(r.models[0].name, "GLM-5.3", "сортировка по убыванию токенов");
  assert.equal(r.total, 787415498);
  assert.ok(Math.abs(r.models[0].share - 431887322 / 787415498) < 1e-9);
  assert.equal(r.calls, 4305);
  assert.equal(GLMQuota.parseModelUsage(null), null);
  assert.equal(GLMQuota.parseModelUsage({}), null);
});

test("parseModelUsage: fallback на tokensUsage, если нет totalTokens", () => {
  const r = GLMQuota.parseModelUsage({ modelDataList: [
    { modelName: "A", tokensUsage: [10, 20, 30] },
    { modelName: "B", totalTokens: 60 },
  ]});
  assert.equal(r.total, 120);
  assert.equal(r.models.length, 2);
});

test("parseModelUsage: семь моделей — все попадают, сортировка по убыванию", () => {
  const list = [];
  for (let i = 1; i <= 7; i++) list.push({ modelName: "M" + i, totalTokens: i * 100 });
  const r = GLMQuota.parseModelUsage({ modelDataList: list });
  assert.equal(r.models.length, 7);
  assert.equal(r.models[0].name, "M7", "первая — самая крупная");
  assert.equal(r.models[6].name, "M1");
  assert.equal(r.total, 2800);
});

test("normalizeWindow: разбирает usageDetails MCP по инструментам", () => {
  const w = GLMQuota.normalizeWindow({
    percentage: 2,
    usageDetails: [
      { modelCode: "web-reader", usage: 11 },
      { modelCode: "search-prime", usage: 103 },
      { modelCode: "zero", usage: 0 },
    ],
  });
  assert.equal(w.details.length, 2, "нулевой инструмент отбрасывается");
  assert.equal(w.details[0].code, "search-prime", "сортировка по убыванию");
  assert.equal(w.details[0].usage, 103);
  assert.equal(w.details[1].code, "web-reader");
});

test("normalizeWindow: без usageDetails поле details = null", () => {
  assert.equal(GLMQuota.normalizeWindow({ percentage: 5 }).details, null);
});
