// GLM Limits — background service worker.
// Polls your GLM Coding Plan quota (raw API key, no Bearer), caches it for the popup,
// keeps a usage history for the sparkline and fires one-shot 80/90% session notifications.
// Settings: platform (auto-detected from the key), poll interval, view mode, notifications.

// Чистая логика разбора квот вынесена в quota.js — её же тестируют node-тесты.
importScripts("quota.js");
importScripts("keyring.js");

const DEFAULTS = { apiKey: "", intervalSec: 120, viewMode: "popup", platform: "auto", notify: true };
const VIEW_MODES = ["popup", "window", "compact"];
const PLATFORMS = ["auto", "cn", "intl"];

// Host per platform. cn = open.bigmodel.cn, intl = api.z.ai.
const HOSTS = { cn: "https://open.bigmodel.cn", intl: "https://api.z.ai" };
const QUOTA_PATH = "/api/monitor/usage/quota/limit";

// Upstream manifest for "new version available" checks (branch main).
const RAW_MANIFEST_URL = "https://raw.githubusercontent.com/Bondartsov/glm-limits/main/manifest.json";

// Sparkline history: one point per 5 minutes is enough, capped to stay small.
const HISTORY_STEP_MS = 5 * 60 * 1000;
const HISTORY_MAX_POINTS = 800;

// How often we may ask GitHub for the upstream version (network call).
const UPDATE_CHECK_INTERVAL_MS = 30 * 60 * 1000;

// Timeout for the quota request — a hanging fetch would keep the SW alive
// until Chrome kills it, and the popup would wait forever.
const API_TIMEOUT_MS = 15000;

// Protects against overlapping polls (alarm + manual "poll" message +
// onInstalled/onStartup all can fire at once).
let pollInFlight = false;

// Backoff: при серии ошибок API опрос отдаляется (до 30 минут), чтобы не
// долбить сервер и не жечь батарею.
let pollBaseSec = 120;
let pollEffectiveSec = 120;
let failStreak = 0;
const BACKOFF_MAX_SEC = 1800;

function applyAlarm() {
  chrome.alarms.create("poll", { periodInMinutes: Math.max(0.5, pollEffectiveSec / 60) });
}

function noteFailure() {
  failStreak = Math.min(failStreak + 1, 6);
  pollEffectiveSec = Math.min(pollBaseSec * Math.pow(2, failStreak - 1), BACKOFF_MAX_SEC);
  console.info("[GLM Limits] API failed x" + failStreak + " — следующий опрос через " + pollEffectiveSec + "с");
  applyAlarm();
}

function noteSuccess() {
  if (failStreak > 0) {
    failStreak = 0;
    pollEffectiveSec = pollBaseSec;
    applyAlarm();
  }
}

// Пороги уведомлений и разбор окон живут в quota.js (покрыты тестами).

async function getSettings() {
  // Flat keys — must match popup.js loadSettings/saveSettings.
  // Ключа здесь нет: он живёт в keyring.js (session/зашифрованный).
  const r = await chrome.storage.local.get([
    "platform", "intervalSec", "viewMode", "lang", "notify",
    "badge", "badgeMetric", "badgeMode"
  ]);
  return {
    platform: PLATFORMS.includes(r.platform) ? r.platform : DEFAULTS.platform,
    intervalSec: r.intervalSec || DEFAULTS.intervalSec,
    viewMode: VIEW_MODES.includes(r.viewMode) ? r.viewMode : DEFAULTS.viewMode,
    lang: r.lang || "en",
    notify: r.notify !== false,
    badge: r.badge !== false,
    badgeMetric: r.badgeMetric === "w" ? "w" : "s",
    badgeMode: r.badgeMode === "used" ? "used" : "remaining",
  };
}

async function applySettings() {
  const s = await getSettings();
  pollBaseSec = s.intervalSec || 120;
  if (failStreak === 0) pollEffectiveSec = pollBaseSec;
  applyAlarm();
  const mode = VIEW_MODES.includes(s.viewMode) ? s.viewMode : "popup";
  try { await chrome.action.setPopup({ popup: mode === "window" ? "" : "popup.html" }); } catch (e) {}
}

// Same size as popout() in popup.js so both entry points look identical.
function openWindow() {
  chrome.windows.create({ url: "popup.html?win=1", type: "popup", width: 360, height: 620 });
}

// Guess the platform from the key shape:
//  - bigmodel.cn keys use the "id.secret" form and contain a dot;
//  - z.ai keys are opaque strings (may carry a "zai" prefix).
//  Unknown shape → international. Логика живёт в quota.js (покрыта тестами).
function detectPlatform(key) {
  return GLMQuota.detectPlatform(key);
}

async function getJSON(url, apiKey) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), API_TIMEOUT_MS);
  try {
    const r = await fetch(url, {
      headers: {
        // GLM monitor API expects the raw key — NO "Bearer" prefix.
        "Authorization": apiKey,
        "Accept": "application/json",
        "Content-Type": "application/json",
      },
      signal: controller.signal,
    });
    if (!r.ok) throw new Error(url + " -> " + r.status);
    return await r.json();
  } finally {
    clearTimeout(timer);
  }
}

// Parse one limit entry into { utilization (0-100), resetsAt (ISO) }.
// GLM reports percentage as "used" (e.g. 16.0 = 16% used).
// Логика живёт в quota.js (покрыта тестами).
function normalizeWindow(item) {
  return GLMQuota.normalizeWindow(item);
}

// Self-update for the unpacked flow, in two steps:
//  1) disk vs loaded manifest — after `git pull` the file on disk already has
//     the new version (unpacked files are served live): record lastUpdate,
//     fire the "updated" notification and reload ourselves (no-op for packed);
//  2) upstream check on GitHub — if a newer version exists but is not pulled
//     yet, notify once per version: update via the tray menu.
async function checkForUpdate(st) {
  const ru = st.lang === "ru";
  try {
    const r = await fetch(chrome.runtime.getURL("manifest.json"), { cache: "no-store" });
    const disk = await r.json();
    const loaded = chrome.runtime.getManifest();
    if (disk && disk.version && loaded && disk.version !== loaded.version) {
      await chrome.storage.local.set({ lastUpdate: { version: disk.version, at: Date.now() } });
      try {
        chrome.notifications.create({
          type: "basic",
          iconUrl: "icons/icon128.png",
          title: "GLM Limits " + disk.version,
          message: ru ? "Расширение успешно обновлено." : "Extension successfully updated.",
        });
      } catch (e) { /* уведомления недоступны — не критично */ }
      console.info("[GLM Limits] new version on disk:", disk.version, "- reloading");
      chrome.runtime.reload();
      return;
    }
  } catch (e) { /* best-effort */ }

  // Сетевая проверка GitHub — не чаще раза в 30 минут, иначе это ~720
  // запросов в сутки на пустом месте.
  try {
    const store = await chrome.storage.local.get(["updNotified", "lastUpdateCheck"]);
    if (Date.now() - (store.lastUpdateCheck || 0) < UPDATE_CHECK_INTERVAL_MS) return;
    await chrome.storage.local.set({ lastUpdateCheck: Date.now() });
    const r = await fetch(RAW_MANIFEST_URL, { cache: "no-store" });
    if (!r.ok) return;
    const remote = await r.json();
    const loaded = chrome.runtime.getManifest();
    if (remote && remote.version && loaded &&
        remote.version !== loaded.version && store.updNotified !== remote.version) {
      await chrome.storage.local.set({ updNotified: remote.version });
      chrome.notifications.create({
        type: "basic",
        iconUrl: "icons/icon128.png",
        title: "GLM Limits — " + (ru ? "доступна " : "available ") + remote.version,
        message: ru
          ? "Новая версия " + remote.version + ". Обнови: иконка в трее → «Обновить расширение»."
          : "New version " + remote.version + ". Update via the tray menu → \"Update extension\".",
      });
    }
  } catch (e) { /* нет сети или GitHub недоступен — молча */ }
}

async function poll() {
  // Параллельные опросы (alarm + ручное сообщение) не нужны и могут
  // портить историю/флаги уведомлений из-за read-modify-write.
  if (pollInFlight) {
    const cached = await chrome.storage.local.get("latest");
    return cached.latest || null;   // отдаём кэш, а не ошибку
  }
  pollInFlight = true;
  try {
    const st = await getSettings();
    await checkForUpdate(st);
    const apiKey = await GLMKeyring.getApiKey();
    if (!apiKey) {
      // Нет ключа — это не ошибка API, backoff не включаем.
      await chrome.storage.local.set({ latest: { ok: false, error: "No API key. Set it in settings.", ts: Date.now() } });
      noteSuccess();
      return null;
    }
    // Resolve "auto" (and any stale stored value) into a concrete platform.
    const platform = st.platform === "auto" || !HOSTS[st.platform]
      ? detectPlatform(apiKey)
      : st.platform;
    const body = await getJSON(HOSTS[platform] + QUOTA_PATH, apiKey);

    const data = body && body.data;
    const limits = (data && Array.isArray(data.limits)) ? data.limits : [];

    // Разбор окон вынесен в quota.js: TOKENS_LIMIT / CREDIT_LIMIT (сессия и
    // неделя) и TIME_LIMIT (месячный MCP).
    const windows = GLMQuota.parseLimits(limits);
    const fiveHour = windows.session;
    const sevenDay = windows.weekly;
    const mcp = windows.mcp;

    // Побочные эффекты изолируем: сбой истории/уведомления/бейджа не должен
    // мешать обновить основной кэш latest.
    try {
      await appendHistory(
        fiveHour ? fiveHour.utilization : null,
        sevenDay ? sevenDay.utilization : null
      );
    } catch (e) {
      console.warn("[GLM Limits] history update failed:", e);
    }
    try {
      await maybeNotify(st, fiveHour);
    } catch (e) {
      console.warn("[GLM Limits] notification failed:", e);
    }
    try {
      await updateBadge(st, fiveHour, sevenDay);
    } catch (e) {
      console.warn("[GLM Limits] badge update failed:", e);
    }

    // Store full window objects — popup reads { utilization, resetsAt }.
    const latest = {
      ok: true,
      s: fiveHour || null,
      w: sevenDay || null,
      mcp: mcp || null,
      plan: (data && data.level) || null,
      platform,   // resolved platform — footer shows the host actually used
      ts: Date.now(),
      // Raw response for debugging (field names / values as the API returns them).
      raw: (data && Array.isArray(data.limits)) ? data.limits : null,
    };
    await chrome.storage.local.set({ latest });
    noteSuccess();
    return latest;
  } catch (e) {
    await chrome.storage.local.set({ latest: { ok: false, error: String(e), ts: Date.now() } });
    noteFailure();
    throw e;
  } finally {
    pollInFlight = false;
  }
}

// Sparkline history points { ts, s, w } — utilization values in %.
// Newest point is refreshed in place until it is HISTORY_STEP_MS old,
// so a 2-minute poll interval does not inflate storage.
async function appendHistory(sUtil, wUtil) {
  try {
    const stg = await chrome.storage.local.get("history");
    const hist = Array.isArray(stg.history) ? stg.history : [];
    const now = Date.now();
    const last = hist[hist.length - 1];
    if (last && now - last.ts < HISTORY_STEP_MS) {
      last.s = sUtil;
      last.w = wUtil;
    } else {
      hist.push({ ts: now, s: sUtil, w: wUtil });
    }
    while (hist.length > HISTORY_MAX_POINTS) hist.shift();
    await chrome.storage.local.set({ history: hist });
  } catch (e) {}
}

// One-shot notifications when session usage crosses 80% / 90% / 100%.
// Flags are scoped to the reset window (resetsAt): внутри одного окна каждый
// порог срабатывает ровно один раз, поэтому 81%, 82%, 83%... не спамят.
async function maybeNotify(st, fiveHour) {
  if (!st.notify || !fiveHour || typeof fiveHour.utilization !== "number") return;
  const used = fiveHour.utilization;
  // Ключ окна: время сброса, а при его отсутствии — пятичасовой слот,
  // иначе флаги залипнут навсегда на одном "unknown".
  const winKey = fiveHour.resetsAt ||
    ("slot:" + Math.floor(Date.now() / (5 * 3600 * 1000)));
  const stg = await chrome.storage.local.get("notifState");
  // Решение «звучать ли и о каком пороге» — чистая функция в quota.js.
  const decision = GLMQuota.notifyDecision(stg.notifState, used, winKey);
  if (decision.threshold != null) {
    fireNotification(st.lang, used, decision.threshold);
  }
  await chrome.storage.local.set({ notifState: decision.state });
}

function fireNotification(lang, used, threshold) {
  const ru = lang === "ru";
  const remaining = Math.max(0, Math.round(100 - used));
  const message = ru
    ? "Сессия израсходована на " + Math.round(used) + "%. До сброса осталось " + remaining + "%."
    : "Session is " + Math.round(used) + "% used. " + remaining + "% left before reset.";
  try {
    chrome.notifications.create({
      type: "basic",
      iconUrl: "icons/icon128.png",
      title: "GLM Limits — " + threshold + "%",
      message,
    });
  } catch (e) {}
}

// Toolbar badge: percent used of the chosen window, background colored by
// the same thresholds as the bars (50/75/90). Hex literals — no DOM/CSS vars
// inside the service worker.
function badgeColor(used) {
  return GLMQuota.badgeColor(used);
}

async function updateBadge(st, fiveHour, sevenDay) {
  try {
    const win = st.badgeMetric === "w" ? sevenDay : fiveHour;
    if (!st.badge || !win || typeof win.utilization !== "number") {
      await chrome.action.setBadgeText({ text: "" });
      return;
    }
    const used = Math.max(0, Math.min(100, Math.round(win.utilization)));
    // badgeMode: "remaining" (default) shows what is left, "used" shows spend.
    // Color always follows the spend level — danger does not depend on wording.
    const shown = st.badgeMode === "used" ? used : 100 - used;
    await chrome.action.setBadgeBackgroundColor({ color: badgeColor(used) });
    try { await chrome.action.setBadgeTextColor({ color: "#ffffff" }); } catch (e) {}
    // "100" в бейдже Chrome обрезается — показываем "99+".
    await chrome.action.setBadgeText({ text: GLMQuota.badgeText(shown) });
  } catch (e) {}
}

chrome.runtime.onInstalled.addListener((details) => {
  applySettings();
  poll().catch(() => {});
  // Точка отсчёта "даты обновления" — момент установки (перезаписывается
  // при каждом обновлении через checkForUpdate).
  chrome.storage.local.get("lastUpdate").then((r) => {
    if (!r.lastUpdate) {
      chrome.storage.local.set({
        lastUpdate: { version: chrome.runtime.getManifest().version, at: Date.now() },
      });
    }
  });
});
chrome.runtime.onStartup.addListener(() => { applySettings(); poll().catch(() => {}); });
chrome.alarms.onAlarm.addListener((a) => { if (a.name === "poll") poll().catch(() => {}); });

chrome.action.onClicked.addListener(() => openWindow());

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg) return;
  if (msg.type === "poll") {
    poll().then((r) => sendResponse(r))
          .catch((e) => sendResponse({ ok: false, error: String(e) }));
    return true;
  }
  if (msg.type === "applySettings") { applySettings().then(() => sendResponse({ ok: true })); return true; }
  if (msg.type === "popout") { openWindow(); sendResponse({ ok: true }); return true; }
});
