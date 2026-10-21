// GLM Limits — background service worker.
// Polls your GLM Coding Plan quota (raw API key, no Bearer), caches it for the popup,
// keeps a usage history for the sparkline and fires one-shot 80/90% session notifications.
// Settings: platform (auto-detected from the key), poll interval, view mode, notifications.

const DEFAULTS = { intervalSec: 120, viewMode: "popup", platform: "auto", notify: true };
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

// Session usage thresholds for one-shot notifications, highest first.
const NOTIFY_THRESHOLDS = [
  { at: 90, flag: "s90" },
  { at: 80, flag: "s80" },
];

async function getSettings() {
  // Flat keys — must match popup.js loadSettings/saveSettings.
  const r = await chrome.storage.local.get([
    "apiKey", "platform", "intervalSec", "viewMode", "lang", "notify",
    "badge", "badgeMetric", "badgeMode"
  ]);
  return {
    apiKey: r.apiKey || DEFAULTS.apiKey,
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
  const minutes = Math.max(0.5, (s.intervalSec || 120) / 60);
  chrome.alarms.create("poll", { periodInMinutes: minutes });
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
//  Unknown shape → international.
function detectPlatform(key) {
  const k = (key || "").trim();
  if (!k) return "cn";
  if (/^zai[_-]/i.test(k)) return "intl";
  if (k.includes(".")) return "cn";
  return "intl";
}

async function getJSON(url, apiKey) {
  const r = await fetch(url, {
    headers: {
      // GLM monitor API expects the raw key — NO "Bearer" prefix.
      "Authorization": apiKey,
      "Accept": "application/json",
      "Content-Type": "application/json",
    },
  });
  if (!r.ok) throw new Error(url + " -> " + r.status);
  return r.json();
}

// Parse one limit entry into { utilization (0-100), resetsAt (ISO) }.
// GLM reports percentage as "used" (e.g. 16.0 = 16% used).
function normalizeWindow(item) {
  const pct = Number(item && item.percentage) || 0;
  const resetMs = item && item.nextResetTime;
  let resetsAt = null;
  if (resetMs && Number(resetMs) > 0) {
    resetsAt = new Date(Number(resetMs)).toISOString();
  }
  return { utilization: Math.min(Math.max(pct, 0), 100), resetsAt };
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

  try {
    const store = await chrome.storage.local.get("updNotified");
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
  try {
    const st = await getSettings();
    await checkForUpdate(st);
    if (!st.apiKey || !st.apiKey.trim()) {
      await chrome.storage.local.set({ latest: { ok: false, error: "No API key. Set it in settings.", ts: Date.now() } });
      throw new Error("no api key");
    }
    // Resolve "auto" (and any stale stored value) into a concrete platform.
    const platform = st.platform === "auto" || !HOSTS[st.platform]
      ? detectPlatform(st.apiKey)
      : st.platform;
    const body = await getJSON(HOSTS[platform] + QUOTA_PATH, st.apiKey.trim());

    const data = body && body.data;
    const limits = (data && Array.isArray(data.limits)) ? data.limits : [];

    let fiveHour = null;
    let sevenDay = null;
    let mcp = null;

    for (const item of limits) {
      if (!item || typeof item !== "object") continue;

      // TIME_LIMIT = monthly prompt/request window (optional meter).
      if (item.type === "TIME_LIMIT") {
        mcp = normalizeWindow(item);
        continue;
      }
      // Окна тарифа приходят и как TOKENS_LIMIT, и как CREDIT_LIMIT
      // (кредитные окна bigmodel.cn: usage = лимит, currentValue = потрачено) —
      // семантика percentage/unit/number та же.
      if (item.type !== "TOKENS_LIMIT" && item.type !== "CREDIT_LIMIT") continue;

      const unit = Number(item.unit);
      const number = Number(item.number);
      const norm = normalizeWindow(item);

      if (unit === 3 && number === 5) {
        fiveHour = norm;          // session / 5h token window
      } else if (unit === 6 && number === 1) {
        sevenDay = norm;          // weekly token window
      } else if (!fiveHour && unit === 3) {
        fiveHour = norm;          // fallback: any hourly window
      } else if (!sevenDay && unit === 6) {
        sevenDay = norm;          // fallback: any weekly window
      }
    }

    await appendHistory(
      fiveHour ? fiveHour.utilization : null,
      sevenDay ? sevenDay.utilization : null
    );
    await maybeNotify(st, fiveHour);
    await updateBadge(st, fiveHour, sevenDay);

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
    return latest;
  } catch (e) {
    await chrome.storage.local.set({ latest: { ok: false, error: String(e), ts: Date.now() } });
    throw e;
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

// One-shot notifications when session usage crosses 80% / 90%.
// Flags are scoped to the window (resetsAt) and re-armed only below 70%,
// so hovering around a threshold does not re-notify.
async function maybeNotify(st, fiveHour) {
  if (!st.notify || !fiveHour || typeof fiveHour.utilization !== "number") return;
  const used = fiveHour.utilization;
  const winKey = fiveHour.resetsAt || "unknown";
  const stg = await chrome.storage.local.get("notifState");
  let ns = stg.notifState;
  if (!ns || ns.winKey !== winKey) ns = { winKey, s80: false, s90: false };
  if (used < 70) { ns.s80 = false; ns.s90 = false; }
  for (const th of NOTIFY_THRESHOLDS) {
    if (used >= th.at && !ns[th.flag]) {
      ns[th.flag] = true;
      if (th.flag === "s90") ns.s80 = true; // jumping straight past 80% covers both
      fireNotification(st.lang, used, th.at);
      break; // at most one notification per poll
    }
  }
  await chrome.storage.local.set({ notifState: ns });
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
  if (used < 50) return "#5a9367";
  if (used < 75) return "#c2a14a";
  if (used < 90) return "#bf7a40";
  return "#b5524c";
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
    await chrome.action.setBadgeText({ text: String(shown) });
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
