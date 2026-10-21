// GLM Limits — popup.js
const VIEW_MODES = ["popup", "window", "compact"];
const PLATFORMS = ["auto", "cn", "intl"];

const I18N = {
  en: {
    brand: "GLM Limits",
    refresh: "Refresh", compact: "Compact view", pin: "Pin on top",
    popout: "Open in window", settings: "Settings",
    statusOffline: "Offline", statusOnline: "Online",
    statusError: "Error", loading: "Loading…",
    session: "Session (5h)", weekly: "Weekly", mcp: "MCP",
    apiKey: "API Key",
    apiKeyHint: "Your GLM Coding Plan API key. Stored locally, sent only to the quota endpoint.",
    platform: "Platform",
    platformAuto: "Auto",
    platformCn: "China · open.bigmodel.cn",
    platformIntl: "International · api.z.ai",
    platformHint: "Auto detects the site from the key shape — bigmodel.cn and z.ai keys are not interchangeable.",
    interval: "Refresh interval (sec)", viewMode: "View mode",
    viewPopup: "Popup", viewWindow: "Window", viewCompact: "Compact",
    language: "Language", theme: "Theme",
    themeLight: "Light", themeDark: "Dark",
    showMcp: "Show MCP usage",
    notifyLabel: "Notify at 80/90% of session",
    badgeLabel: "Show percent on icon",
    badgeMetricLabel: "Icon shows",
    save: "Save", reset: "Reset",
    plan: "Plan",
    utilSpent: "used",
    sessionLeft: "Left · session (5h)", weeklyLeft: "Left · weekly", mcpLeft: "Left · MCP",
    footWhen: "Resets", footSource: "Source",
    today: "Today HH:MM", tomorrow: "Tomorrow HH:MM",
    resetNow: "Resets now"
  },
  ru: {
    brand: "GLM Limits",
    refresh: "Обновить", compact: "Компактный вид", pin: "Закрепить поверх окон",
    popout: "Открыть в окне", settings: "Настройки",
    statusOffline: "Нет связи", statusOnline: "Онлайн",
    statusError: "Ошибка", loading: "Загрузка…",
    session: "Сессия (5ч)", weekly: "Неделя", mcp: "MCP",
    apiKey: "API-ключ",
    apiKeyHint: "Ваш API-ключ GLM Coding Plan. Хранится локально, уходит только на endpoint квот.",
    platform: "Платформа",
    platformAuto: "Авто",
    platformCn: "Китай · open.bigmodel.cn",
    platformIntl: "Международный · api.z.ai",
    platformHint: "«Авто» определяет площадку по виду ключа — ключи bigmodel.cn и z.ai не взаимозаменяемы.",
    interval: "Интервал обновления (сек)", viewMode: "Режим отображения",
    viewPopup: "Popup", viewWindow: "Окно", viewCompact: "Компактный",
    language: "Язык", theme: "Тема",
    themeLight: "Светлая", themeDark: "Тёмная",
    showMcp: "Показывать MCP",
    notifyLabel: "Уведомлять при 80/90% сессии",
    badgeLabel: "Процент на иконке",
    badgeMetricLabel: "Что показывает иконка",
    save: "Сохранить", reset: "Сбросить",
    plan: "Тариф",
    utilSpent: "израсходовано",
    sessionLeft: "Осталось · сессия (5ч)", weeklyLeft: "Осталось · неделя", mcpLeft: "Осталось · MCP",
    footWhen: "Сброс", footSource: "Источник",
    today: "Сегодня HH:MM", tomorrow: "Завтра HH:MM",
    resetNow: "Сброс сейчас"
  }
};

let currentLang = "en";
let currentSettings = {};
let latestData = null;
let tickTimer = null;
let historyData = [];

// ── Helpers ──────────────────────────────────────────────
function t(key) {
  return (I18N[currentLang] && I18N[currentLang][key]) || I18N.en[key] || key;
}

function $(id) { return document.getElementById(id); }

function normalizeViewMode(v) {
  return VIEW_MODES.includes(v) ? v : "popup";
}

function normalizePlatform(v) {
  return PLATFORMS.includes(v) ? v : "auto";
}

// Legacy stored value "clay" maps to "light".
function normalizeTheme(v) {
  return v === "dark" ? "dark" : "light";
}

function levelColor(p) {
  if (p < 50) return "var(--green)";
  if (p < 75) return "var(--yellow)";
  if (p < 90) return "var(--amber)";
  return "var(--red)";
}

function fmtCountdown(epochMs) {
  if (!epochMs) return "—";
  const now = Date.now();
  const diff = epochMs - now;
  if (diff <= 0) return t("resetNow");
  const s = Math.floor(diff / 1000);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 24) {
    const d = Math.floor(h / 24);
    return d + "d " + (h % 24) + "h";
  }
  if (h > 0) return h + "h " + m + "m";
  if (m > 0) return m + "m " + sec + "s";
  return sec + "s";
}

function fmtAt(epochMs) {
  if (!epochMs) return "—";
  const d = new Date(epochMs);
  const now = new Date();
  const today = new Date(now.toDateString());
  const tomorrow = new Date(today);
  tomorrow.setDate(tomorrow.getDate() + 1);
  const timeStr = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (d.toDateString() === today.toDateString()) return t("today").replace("HH:MM", timeStr);
  if (d.toDateString() === tomorrow.toDateString()) return t("tomorrow").replace("HH:MM", timeStr);
  const dayName = d.toLocaleDateString([], { weekday: "short" });
  return dayName + " " + timeStr;
}

// ── Sparkline ────────────────────────────────────────────
async function loadHistory() {
  try {
    const r = await chrome.storage.local.get("history");
    historyData = Array.isArray(r.history) ? r.history : [];
  } catch (e) {
    historyData = [];
  }
}

// History points of one window ("s" or "w") since its start.
// Window length is derived from the reset time (5h session, 7d week).
function windowSeries(kind, resetsAt) {
  const len = kind === "s" ? 5 * 3600e3 : 7 * 24 * 3600e3;
  const startTs = resetsAt ? resetsAt - len : Date.now() - len;
  return historyData
    .filter(p => p && p.ts >= startTs && typeof p[kind] === "number")
    .map(p => ({ ts: p.ts, v: p[kind] }));
}

function renderSparkline(el, pts) {
  if (!el) return;
  const clean = (pts || []).filter(p => p && typeof p.v === "number");
  if (clean.length < 2) { el.style.display = "none"; return; }
  el.style.display = "block";
  const W = 328, H = 26, PAD = 2;
  const t0 = clean[0].ts, t1 = clean[clean.length - 1].ts;
  const span = Math.max(t1 - t0, 1);
  const x = (ts) => PAD + (ts - t0) / span * (W - 2 * PAD);
  const y = (v) => H - PAD - (Math.max(0, Math.min(100, v)) / 100) * (H - 2 * PAD);
  let d = "M" + x(clean[0].ts).toFixed(1) + " " + y(clean[0].v).toFixed(1);
  for (let i = 1; i < clean.length; i++) {
    d += " L" + x(clean[i].ts).toFixed(1) + " " + y(clean[i].v).toFixed(1);
  }
  const area = d + " L" + x(t1).toFixed(1) + " " + (H - PAD) + " L" + x(t0).toFixed(1) + " " + (H - PAD) + " Z";
  const color = levelColor(clean[clean.length - 1].v);
  // CSS vars in levelColor() only work via style=, not presentation attributes.
  el.innerHTML =
    '<path d="' + area + '" style="fill:' + color + ';stroke:none" opacity="0.12"></path>' +
    '<path d="' + d + '" style="fill:none;stroke:' + color + '" stroke-width="1.5" vector-effect="non-scaling-stroke" stroke-linejoin="round" stroke-linecap="round"></path>';
}

// ── Rendering ────────────────────────────────────────────
function setStatus(kind, text) {
  const dot = document.querySelector("#status .dot");
  const txt = document.querySelector("#status .status-text");
  if (dot) dot.className = "dot " + kind;
  if (txt) txt.textContent = text;
}

function showMetric(elId, pctId, barId, resetId, data, sparkId, series) {
  const el = $(elId);
  if (!data || data == null || typeof data !== "object") {
    el.style.display = "none";
    return;
  }
  el.style.display = "";
  const pct = $(pctId);
  const bar = $(barId);
  const reset = $(resetId);
  // GLM API "percentage" = utilization = how much is ALREADY spent.
  // User-facing number = remaining (what is left).
  const used = Number(data.utilization);
  const remaining = isNaN(used) ? null : Math.max(0, Math.min(100, 100 - used));
  if (remaining == null) {
    pct.textContent = "—";
    bar.style.width = "0%";
    reset.textContent = "—";
    if (sparkId) renderSparkline($(sparkId), []);
    return;
  }
  pct.textContent = remaining.toFixed(0);
  pct.style.color = levelColor(100 - remaining);
  bar.style.width = remaining + "%";
  bar.style.background = levelColor(100 - remaining);
  // Utilization annotation — how much is spent.
  const annoId = elId.replace("metric", "").toLowerCase() + "Anno";
  const anno = $(annoId);
  if (anno) anno.textContent = t("utilSpent") + " " + used.toFixed(0) + "%";
  // Usage sparkline — drawn from the local history when there are enough points.
  if (sparkId) renderSparkline($(sparkId), series || []);
  // Reset countdown — nextResetTime is epoch ms.
  const resetMs = data.resetsAt ? new Date(data.resetsAt).getTime() : null;
  if (resetMs && resetMs > 0) {
    reset.textContent = fmtCountdown(resetMs) + " · " + fmtAt(resetMs);
  } else {
    reset.textContent = "—";
  }
}

function showChip(chipId, pctId, data) {
  const chip = $(chipId);
  if (!data || data == null || typeof data !== "object") {
    chip.style.display = "none";
    return;
  }
  chip.style.display = "";
  const used = Number(data.utilization);
  const remaining = isNaN(used) ? null : Math.max(0, Math.min(100, 100 - used));
  if (remaining == null) {
    $(pctId).textContent = "—";
    return;
  }
  $(pctId).textContent = remaining.toFixed(0);
  $(pctId).style.color = levelColor(100 - remaining);
}

function renderFooter(data) {
  const f = $("footer");
  if (!f) return;
  if (!data || !data.ok) { f.style.display = "none"; return; }
  f.style.display = "";
  const s = data.s;
  $("footReset").textContent = (s && s.resetsAt)
    ? fmtCountdown(new Date(s.resetsAt).getTime()) + " · " + fmtAt(new Date(s.resetsAt).getTime())
    : "—";
  $("footSource").textContent = (data.platform === "intl" ? "api.z.ai" : "open.bigmodel.cn")
    + (data.ts ? " · " + new Date(data.ts).toLocaleTimeString() : "");
}

function render(data) {
  latestData = data;
  if (!data || !data.ok) {
    const errTxt = (data && data.error) ? String(data.error) : t("statusError");
    setStatus("error", errTxt);
    $("loading").style.display = "";
    $("fullView").style.display = "none";
    $("compactView").style.display = "none";
    return;
  }

  setStatus("online", t("statusOnline"));
  $("loading").style.display = "none";

  const viewMode = normalizeViewMode(currentSettings.viewMode);
  $("compact").classList.toggle("active", viewMode === "compact");
  if (viewMode === "compact") {
    $("fullView").style.display = "none";
    $("compactView").style.display = "";
    showChip("chipSession", "chipSessionPct", data.s);
    showChip("chipWeekly", "chipWeeklyPct", data.w);
  } else {
    $("compactView").style.display = "none";
    $("fullView").style.display = "";
    const sResetsAt = (data.s && data.s.resetsAt) ? new Date(data.s.resetsAt).getTime() : null;
    const wResetsAt = (data.w && data.w.resetsAt) ? new Date(data.w.resetsAt).getTime() : null;
    showMetric("metricSession", "sessionPct", "sessionBar", "sessionReset", data.s,
      "sessionSpark", windowSeries("s", sResetsAt));
    showMetric("metricWeekly", "weeklyPct", "weeklyBar", "weeklyReset", data.w,
      "weeklySpark", windowSeries("w", wResetsAt));
    if (currentSettings.showMcp && data.mcp) {
      showMetric("metricMcp", "mcpPct", "mcpBar", "mcpReset", data.mcp);
    } else {
      $("metricMcp").style.display = "none";
    }
    if (data.plan) {
      $("planBadge").textContent = t("plan") + ": " + data.plan;
      $("planBadge").style.display = "";
    }
  }
  renderFooter(data);
}

function applyI18n() {
  document.querySelectorAll("[data-i18n]").forEach(el => {
    const key = el.getAttribute("data-i18n");
    if (t(key)) el.textContent = t(key);
  });
  document.querySelectorAll("[data-i18n-title]").forEach(el => {
    const key = el.getAttribute("data-i18n-title");
    if (t(key)) el.title = t(key);
  });
}

function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", normalizeTheme(theme));
}

// ── Settings ──────────────────────────────────────────────
async function loadSettings() {
  const r = await chrome.storage.local.get([
    "apiKey", "platform", "intervalSec", "viewMode",
    "lang", "theme", "showMcp", "notify", "badge", "badgeMetric"
  ]);
  currentSettings = {
    apiKey: r.apiKey || "",
    platform: normalizePlatform(r.platform),
    intervalSec: r.intervalSec || 120,
    viewMode: normalizeViewMode(r.viewMode),
    lang: r.lang || "en",
    theme: normalizeTheme(r.theme),
    showMcp: r.showMcp || false,
    notify: r.notify !== false,
    badge: r.badge !== false,
    badgeMetric: r.badgeMetric === "w" ? "w" : "s"
  };
  currentLang = currentSettings.lang;

  $("apiKeyInput").value = currentSettings.apiKey;
  $("platformSelect").value = currentSettings.platform;
  $("intervalInput").value = currentSettings.intervalSec;
  $("viewModeSelect").value = currentSettings.viewMode;
  $("langSelect").value = currentSettings.lang;
  $("themeSelect").value = currentSettings.theme;
  $("showMcpCheck").checked = currentSettings.showMcp;
  $("notifyCheck").checked = currentSettings.notify;
  $("badgeCheck").checked = currentSettings.badge;
  $("badgeMetricSelect").value = currentSettings.badgeMetric;

  applyI18n();
  applyTheme(currentSettings.theme);
  updateBadgeField();
}

async function saveSettings() {
  currentSettings = {
    apiKey: $("apiKeyInput").value.trim(),
    platform: normalizePlatform($("platformSelect").value),
    intervalSec: parseInt($("intervalInput").value) || 120,
    viewMode: normalizeViewMode($("viewModeSelect").value),
    lang: $("langSelect").value,
    theme: $("themeSelect").value,
    showMcp: $("showMcpCheck").checked,
    notify: $("notifyCheck").checked,
    badge: $("badgeCheck").checked,
    badgeMetric: $("badgeMetricSelect").value
  };
  await chrome.storage.local.set(currentSettings);
  currentLang = currentSettings.lang;
  applyI18n();
  applyTheme(currentSettings.theme);

  // Notify background to apply new settings (await so storage is flushed first)
  try { await chrome.runtime.sendMessage({ type: "applySettings" }); } catch (e) {}
  await poll();
}

async function resetSettings() {
  await chrome.storage.local.remove([
    "apiKey", "platform", "intervalSec", "viewMode",
    "lang", "theme", "showMcp", "notify", "badge", "badgeMetric"
  ]);
  await loadSettings();
  // Background keeps its own alarm/popup state — re-apply defaults there too.
  try { await chrome.runtime.sendMessage({ type: "applySettings" }); } catch (e) {}
  poll();
}

// ── Polling ───────────────────────────────────────────────
async function poll() {
  try {
    const r = await chrome.runtime.sendMessage({ type: "poll" });
    await loadHistory();
    render(r && r.ok ? r : { ok: false });
  } catch (e) {
    await loadHistory();
    render({ ok: false });
  }
}

// ── View toggles ──────────────────────────────────────────
function toggleCompact() {
  const next = currentSettings.viewMode === "compact" ? "popup" : "compact";
  $("viewModeSelect").value = next;
  currentSettings.viewMode = next;
  chrome.storage.local.set({ viewMode: next });
  render(latestData);
}

function toggleSettings() {
  const panel = $("settingsPanel");
  panel.style.display = panel.style.display === "none" ? "" : "none";
}

// The "which metric" select only makes sense when the badge is on.
function updateBadgeField() {
  const on = $("badgeCheck").checked;
  const field = $("badgeMetricField");
  if (field) field.style.display = on ? "" : "none";
}

async function togglePin() {
  const btn = $("pin");
  const isPinned = btn.dataset.pinned === "1";
  if (!isPinned) {
    // No Document PiP support (or it failed) — fall back to a regular window.
    if (!("documentPictureInPicture" in window)) { popout(); return; }
    try {
      const win = await documentPictureInPicture.requestWindow({ width: 360, height: 520 });
      // Copy stylesheets so the clone keeps the active theme.
      [...document.querySelectorAll('link[rel="stylesheet"], style')].forEach((node) => {
        win.document.head.appendChild(node.cloneNode(true));
      });
      win.document.documentElement.setAttribute(
        "data-theme",
        document.documentElement.getAttribute("data-theme") || "light"
      );
      // Live copy: clone the app markup and attach popup.js to it, so the
      // PiP window keeps polling and ticking on its own.
      const pip = $("app").cloneNode(true);
      const pipPin = pip.querySelector("#pin");
      if (pipPin) pipPin.remove(); // nested PiP makes no sense
      win.document.body.appendChild(pip);
      const script = win.document.createElement("script");
      script.src = chrome.runtime.getURL("popup.js");
      win.document.body.appendChild(script);
      btn.dataset.pinned = "1";
      btn.classList.add("active");
      win.addEventListener("pagehide", () => {
        btn.dataset.pinned = "0";
        btn.classList.remove("active");
      });
    } catch (e) {
      btn.dataset.pinned = "0";
      btn.classList.remove("active");
      popout();
    }
  } else {
    try {
      if (documentPictureInPicture.window) documentPictureInPicture.window.close();
    } catch (e) {}
    btn.dataset.pinned = "0";
    btn.classList.remove("active");
  }
}

async function popout() {
  const url = chrome.runtime.getURL("popup.html");
  await chrome.windows.create({ url, type: "popup", width: 360, height: 620 });
}

// ── Tick (countdown updates) ──────────────────────────────
function startTick() {
  if (tickTimer) clearInterval(tickTimer);
  tickTimer = setInterval(() => {
    if (latestData && latestData.ok) {
      render(latestData);
    }
  }, 1000);
}

// ── Init ──────────────────────────────────────────────────
async function init() {
  await loadSettings();
  await loadHistory();
  // Fast path: show cached latest immediately (background may have polled via alarm).
  const cached = await chrome.storage.local.get("latest");
  if (cached.latest) render(cached.latest);
  await poll();
  startTick();

  $("refresh").addEventListener("click", poll);
  $("compact").addEventListener("click", toggleCompact);
  $("settings").addEventListener("click", toggleSettings);
  $("pin").addEventListener("click", togglePin);
  $("popout").addEventListener("click", popout);
  $("badgeCheck").addEventListener("change", updateBadgeField);
  $("saveSettings").addEventListener("click", saveSettings);
  $("resetSettings").addEventListener("click", resetSettings);

  $("langSelect").addEventListener("change", async (e) => {
    currentLang = e.target.value;
    applyI18n();
    render(latestData);
  });
  $("themeSelect").addEventListener("change", async (e) => {
    applyTheme(e.target.value);
  });
}

// Runs both in the popup document and in the Document PiP clone,
// where the script is injected after DOMContentLoaded has fired.
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
