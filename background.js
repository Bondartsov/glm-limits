// GLM Limits — background service worker.
// Polls your GLM Coding Plan quota (raw API key, no Bearer), caches it for the popup.
// Settings: poll interval + view mode (toolbar popup vs separate window).

// Must match popup.js defaults (intervalSec) and VIEW_MODES whitelist.
const DEFAULTS = { intervalSec: 120, viewMode: "popup", platform: "cn", apiKey: "" };
const VIEW_MODES = ["popup", "window", "compact"];

async function getSettings() {
  // Flat keys — must match popup.js loadSettings/saveSettings.
  const r = await chrome.storage.local.get([
    "apiKey", "platform", "intervalSec", "viewMode"
  ]);
  return {
    apiKey: r.apiKey || DEFAULTS.apiKey,
    platform: r.platform || DEFAULTS.platform,
    intervalSec: r.intervalSec || DEFAULTS.intervalSec,
    viewMode: r.viewMode || DEFAULTS.viewMode,
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

// Host per platform. cn = open.bigmodel.cn, intl = api.z.ai.
const HOSTS = { cn: "https://open.bigmodel.cn", intl: "https://api.z.ai" };
const QUOTA_PATH = "/api/monitor/usage/quota/limit";

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

// Convert epoch ms (Singapore GMT+8 reset clocks) to a local ISO string.
function msToIso(ms) {
  if (!ms || Number(ms) <= 0) return null;
  return new Date(Number(ms)).toISOString();
}

async function poll() {
  try {
    const st = await getSettings();
    if (!st.apiKey || !st.apiKey.trim()) {
      await chrome.storage.local.set({ latest: { ok: false, error: "No API key. Set it in settings.", ts: Date.now() } });
      throw new Error("no api key");
    }
    const host = HOSTS[st.platform] || HOSTS.cn;
    const body = await getJSON(host + QUOTA_PATH, st.apiKey.trim());

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
      if (item.type !== "TOKENS_LIMIT") continue;

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

    // Store full window objects — popup reads { utilization, resetsAt }.
    const latest = {
      ok: true,
      s: fiveHour || null,
      w: sevenDay || null,
      mcp: mcp || null,
      plan: (data && data.level) || null,
      platform: st.platform,
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

chrome.runtime.onInstalled.addListener(() => { applySettings(); poll().catch(() => {}); });
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