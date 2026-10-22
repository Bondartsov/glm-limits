// GLM Limits — smoke-тест расширения: гоняем background.js и popup.js
// в node с моками chrome.* и DOM. Ловит ошибки инициализации, из-за которых
// попап висел на «Загрузка…», а service worker молча умирал.
//
// Запуск:  node --test tests/
const { test } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const ROOT = path.resolve(__dirname, "..");

// ── Мок chrome.* ────────────────────────────────────────────
function makeChromeMock(options = {}) {
  const local = {};
  const session = {};
  const calls = { badgeText: [], notifications: [], alarms: [], setPopup: [] };

  function area(store) {
    return {
      async get(keys) {
        if (keys === undefined || keys === null) return { ...store };
        const list = Array.isArray(keys) ? keys : [keys];
        const out = {};
        for (const k of list) if (k in store) out[k] = store[k];
        return out;
      },
      async set(obj) { Object.assign(store, obj); },
      async remove(keys) {
        for (const k of (Array.isArray(keys) ? keys : [keys])) delete store[k];
      },
    };
  }

  const chrome = {
    storage: {
      local: area(local),
      session: area(session),
      onChanged: { addListener() {} },
    },
    runtime: {
      id: "test",
      getURL: (p) => "chrome-extension://test/" + p,
      getManifest: () => ({ version: "0.6.0" }),
      sendMessage: async () => ({ ok: true, s: null, w: null }),
      onMessage: { addListener() {} },
      onInstalled: { addListener(fn) { chrome.runtime._onInstalled = fn; } },
      onStartup: { addListener() {} },
      reload: () => { calls.reloaded = true; },
    },
    alarms: {
      create: (name, opts) => calls.alarms.push({ name, opts }),
      onAlarm: { addListener() {} },
    },
    action: {
      setBadgeText: async (o) => calls.badgeText.push(o.text),
      setBadgeBackgroundColor: async () => {},
      setBadgeTextColor: async () => {},
      setPopup: async (o) => calls.setPopup.push(o.popup),
      onClicked: { addListener() {} },
    },
    notifications: {
      create: (o) => { calls.notifications.push(o); return "id"; },
    },
    windows: { create: async () => {} },
    _calls: calls,
    _local: local,
    _session: session,
  };
  return chrome;
}

// ── Мок DOM ─────────────────────────────────────────────────
function makeEl(id) {
  return {
    id,
    style: {},
    dataset: {},
    classList: { _s: new Set(), add() {}, remove() {}, toggle() {}, contains() { return false; } },
    children: [],
    firstChild: null,
    textContent: "",
    innerHTML: "",
    value: "",
    checked: false,
    title: "",
    appendChild(c) { this.children.push(c); this.firstChild = c; return c; },
    removeChild(c) { this.children = this.children.filter((x) => x !== c); return c; },
    setAttribute() {},
    getAttribute() { return null; },
    addEventListener() {},
    removeEventListener() {},
    querySelector() { return makeEl("q"); },
    querySelectorAll() { return []; },
    scrollIntoView() {},
    focus() {},
  };
}

function makeDomMock(ids) {
  const map = new Map();
  for (const id of ids) map.set(id, makeEl(id));
  return {
    getElementById: (id) => map.get(id) || null,
    querySelector: (sel) => makeEl(sel),
    querySelectorAll: () => [],
    createElementNS: () => makeEl("ns"),
    createElement: () => makeEl("el"),
    addEventListener() {},
    readyState: "complete",
    documentElement: makeEl("html"),
    body: makeEl("body"),
  };
}

const POPUP_IDS = [
  "app", "status", "loading", "fullView", "compactView", "footer", "footReset", "footSource",
  "metricSession", "sessionPct", "sessionAnno", "sessionBar", "sessionReset", "sessionSpark",
  "metricWeekly", "weeklyPct", "weeklyAnno", "weeklyBar", "weeklyReset", "weeklySpark",
  "metricMcp", "mcpPct", "mcpAnno", "mcpBar", "mcpReset",
  "planBadge", "chipSession", "chipSessionPct", "chipWeekly", "chipWeeklyPct",
  "settingsPanel", "apiKeyInput", "keyState", "keyPersistCheck", "platformSelect",
  "intervalInput", "viewModeSelect", "langSelect", "themeSelect", "showMcpCheck",
  "notifyCheck", "badgeCheck", "badgeMetricSelect", "badgeModeSelect",
  "saveSettings", "resetSettings", "refresh", "compact", "pin", "popout", "settings",
];

function runScript(ctx, file) {
  const code = fs.readFileSync(path.join(ROOT, file), "utf8");
  vm.runInContext(code, ctx, { filename: file });
}

function makeContext(chrome, dom, extra = {}) {
  const sandbox = {
    chrome, document: dom, console,
    // Таймеры не должны держать процесс node: интервалы не планируем,
    // одноразовые — unref, чтобы тест завершался.
    setInterval: () => 0,
    clearInterval: () => {},
    setTimeout: (fn, ms, ...rest) => {
      const t = setTimeout(fn, ms, ...rest);
      if (t && typeof t.unref === "function") t.unref();
      return t;
    },
    clearTimeout,
    AbortController,
    TextEncoder, TextDecoder, btoa, atob,
    crypto: require("node:crypto").webcrypto,
    URL, Date, Math, JSON, Promise, Object, Array, Number, String, Boolean, Error, Intl,
  };
  sandbox.globalThis = sandbox;
  sandbox.window = sandbox;
  Object.assign(sandbox, extra);
  return vm.createContext(sandbox);
}

// ── Тесты ───────────────────────────────────────────────────
test("background.js: инициализируется без ошибок (importScripts quota.js/keyring.js)", () => {
  const chrome = makeChromeMock();
  const dom = makeDomMock(POPUP_IDS);
  const ctx = makeContext(chrome, dom, {
    importScripts: (...files) => { for (const f of files) runScript(ctx, f); },
  });
  runScript(ctx, "background.js");
  assert.ok(true, "фоновый скрипт загрузился без исключений");
});

test("background.js: опрос квот кладёт данные в кэш и ставит бейдж", async () => {
  const chrome = makeChromeMock();
  const dom = makeDomMock(POPUP_IDS);
  const ctx = makeContext(chrome, dom, {
    importScripts: (...files) => { for (const f of files) runScript(ctx, f); },
    fetch: async (url) => ({
      ok: true,
      status: 200,
      json: async () => ({
        code: 200,
        data: {
          level: "max",
          limits: [
            { type: "CREDIT_LIMIT", unit: 3, number: 5, usage: 28000, currentValue: 1086,
              remaining: 26913, percentage: 3, nextResetTime: 1792602151287 },
            { type: "CREDIT_LIMIT", unit: 6, number: 1, usage: 140000, currentValue: 9781,
              remaining: 130218, percentage: 6, nextResetTime: 1793094553964 },
          ],
        },
      }),
    }),
  });
  runScript(ctx, "background.js");
  chrome._local.apiKey = "id.secret";   // старый формат — должна сработать миграция
  chrome._local.platform = "cn";

  await ctx.poll();

  const latest = chrome._local.latest;
  assert.ok(latest, "latest должен быть записан");
  assert.equal(latest.ok, true, "опрос должен пройти: " + JSON.stringify(latest));
  assert.equal(latest.s.utilization, 3);
  assert.equal(latest.w.utilization, 6);
  assert.ok(chrome._calls.badgeText.length > 0, "бейдж должен быть установлен");
  assert.equal(chrome._local.apiKey, undefined, "старый открытый ключ должен быть удалён");
});

test("popup.js: init() отрабатывает без исключений (иначе попап висит на «Загрузка…»)", async () => {
  const chrome = makeChromeMock();
  const dom = makeDomMock(POPUP_IDS);
  const ctx = makeContext(chrome, dom, {
    importScripts: (...files) => { for (const f of files) runScript(ctx, f); },
  });
  runScript(ctx, "quota.js");
  runScript(ctx, "keyring.js");
  runScript(ctx, "popup.js");

  // Должны быть видны и метрики, и футер с версией.
  await ctx.init();
  assert.ok(dom.getElementById("status").textContent !== undefined);
  const foot = dom.getElementById("footSource").textContent;
  assert.ok(foot.includes("v0.6.0"), "в футере должна быть версия, получено: " + foot);
  assert.equal(dom.getElementById("loading").style.display, "none",
    "состояние загрузки должно сниматься после init()");
});

test("popup.js: ключ из settings сохраняется через keyring (в память, не на диск)", async () => {
  const chrome = makeChromeMock();
  const dom = makeDomMock(POPUP_IDS);
  const ctx = makeContext(chrome, dom, {
    importScripts: (...files) => { for (const f of files) runScript(ctx, f); },
  });
  runScript(ctx, "quota.js");
  runScript(ctx, "keyring.js");
  runScript(ctx, "popup.js");

  dom.getElementById("apiKeyInput").value = "  id.secret  ";
  await ctx.saveSettings();

  assert.equal(chrome._session.apiKey, "id.secret", "ключ должен лежать в session-хранилище");
  assert.equal(chrome._local.apiKey, undefined, "открытого ключа в local быть не должно");
});
