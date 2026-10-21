// GLM Limits — popup.js
const VIEW_MODES = ["popup", "window", "compact"];

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
    platformCn: "China · open.bigmodel.cn",
    platformIntl: "International · api.z.ai",
    platformHint: "Choose the site that issued your key — bigmodel.cn and z.ai keys are not interchangeable.",
    interval: "Refresh interval (sec)", viewMode: "View mode",
    viewPopup: "Popup", viewWindow: "Window", viewCompact: "Compact",
    language: "Language", theme: "Theme",
    themeLight: "Light", themeDark: "Dark",
    showMcp: "Show MCP usage", save: "Save", reset: "Reset",
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
    platformCn: "Китай · open.bigmodel.cn",
    platformIntl: "Международный · api.z.ai",
    platformHint: "Площадка, на которой создан ключ, — ключи bigmodel.cn и z.ai не взаимозаменяемы.",
    interval: "Интервал обновления (сек)", viewMode: "Режим отображения",
    viewPopup: "Popup", viewWindow: "Окно", viewCompact: "Компактный",
    language: "Язык", theme: "Тема",
    themeLight: "Светлая", themeDark: "Тёмная",
    showMcp: "Показывать MCP", save: "Сохранить", reset: "Сбросить",
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

// ── Helpers ──────────────────────────────────────────────
function t(key) {
  return (I18N[currentLang] && I18N[currentLang][key]) || I18N.en[key] || key;
}

function $(id) { return document.getElementById(id); }

function normalizeViewMode(v) {
  return VIEW_MODES.includes(v) ? v : "popup";
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

// ── Rendering ────────────────────────────────────────────
function setStatus(kind, text) {
  const dot = document.querySelector("#status .dot");
  const txt = document.querySelector("#status .status-text");
  if (dot) dot.className = "dot " + kind;
  if (txt) txt.textContent = text;
}

function showMetric(elId, pctId, barId, resetId, data) {
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
    showMetric("metricSession", "sessionPct", "sessionBar", "sessionReset", data.s);
    showMetric("metricWeekly", "weeklyPct", "weeklyBar", "weeklyReset", data.w);
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
    "lang", "theme", "showMcp"
  ]);
  currentSettings = {
    apiKey: r.apiKey || "",
    platform: r.platform || "cn",
    intervalSec: r.intervalSec || 120,
    viewMode: normalizeViewMode(r.viewMode),
    lang: r.lang || "en",
    theme: normalizeTheme(r.theme),
    showMcp: r.showMcp || false
  };
  currentLang = currentSettings.lang;

  $("apiKeyInput").value = currentSettings.apiKey;
  $("platformSelect").value = currentSettings.platform;
  $("intervalInput").value = currentSettings.intervalSec;
  $("viewModeSelect").value = currentSettings.viewMode;
  $("langSelect").value = currentSettings.lang;
  $("themeSelect").value = currentSettings.theme;
  $("showMcpCheck").checked = currentSettings.showMcp;

  applyI18n();
  applyTheme(currentSettings.theme);
}

async function saveSettings() {
  currentSettings = {
    apiKey: $("apiKeyInput").value.trim(),
    platform: $("platformSelect").value,
    intervalSec: parseInt($("intervalInput").value) || 120,
    viewMode: normalizeViewMode($("viewModeSelect").value),
    lang: $("langSelect").value,
    theme: $("themeSelect").value,
    showMcp: $("showMcpCheck").checked
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
    "lang", "theme", "showMcp"
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
    if (r && r.ok) {
      render(r);
    } else {
      render({ ok: false });
    }
  } catch (e) {
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
