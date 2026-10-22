// GLM Limits — popup.js
const VIEW_MODES = ["popup", "window", "compact"];
const PLATFORMS = ["auto", "cn", "intl"];

const I18N = {
  en: {
    brand: "GLM Limits",
    refresh: "Refresh", compact: "Compact view", pin: "Pin on top",
    popout: "Open in window", settings: "Settings", close: "Close",
    statusOffline: "Offline", statusOnline: "Online",
    statusError: "Error", statusNoKey: "Not set up", loading: "Loading…",
    swUnreachable: "Extension is not responding — press Reload in chrome://extensions",
    updateAvailable: "version {v} available",
    emptyTitle: "Enter your API key",
    emptyText: "The key is stored only on your machine and is used to request your GLM Coding Plan quota.",
    emptyAction: "Enter key",
    session: "Session (5h)", weekly: "Weekly", mcp: "MCP",
    apiKey: "API Key",
    apiKeyHint: "Your GLM Coding Plan API key. Never shared.",
    keyPersist: "Remember on this device (encrypted)",
    keyStateNone: "No key set",
    keyStateSession: "Key is in memory only — it disappears when the browser closes",
    keyStatePersisted: "Key is saved encrypted (still readable via this extension's DevTools)",
    platform: "Platform",
    platformAuto: "Auto",
    platformCn: "China · open.bigmodel.cn",
    platformIntl: "International · api.z.ai",
    platformHint: "Auto detects the site from your key.",
    interval: "Refresh interval (sec)", viewMode: "View mode",
    viewPopup: "Popup", viewWindow: "Window", viewCompact: "Compact",
    language: "Language", theme: "Theme",
    themeLight: "Light", themeDark: "Dark",
    showMcp: "Show MCP usage",
    notifyLabel: "Notify at 80/90% of session",
    badgeLabel: "Show percent on icon",
    badgeMetricLabel: "Icon shows",
    valueModeLabel: "Show numbers as",
    valueModeRemaining: "Remaining",
    valueModeUsed: "Used",
    save: "Save", reset: "Reset",
    saved: "Saved", resetDone: "Settings reset",
    plan: "Plan",
    utilSpent: "used", utilLeft: "left",
    sessionLeft: "Left · session (5h)", weeklyLeft: "Left · weekly", mcpLeft: "Left · MCP",
    chartHours: "activity by hour",
    chartDays: "spend by day of week",
    legendNow: "now",
    legendReset: "reset",
    footWhen: "Resets", footSource: "Source",
    today: "Today HH:MM", tomorrow: "Tomorrow HH:MM",
    resetNow: "Resets now",
    weekdays: ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"],
  },
  ru: {
    brand: "GLM Limits",
    refresh: "Обновить", compact: "Компактный вид", pin: "Закрепить поверх окон",
    popout: "Открыть в окне", settings: "Настройки", close: "Закрыть",
    statusOffline: "Нет связи", statusOnline: "Онлайн",
    statusError: "Ошибка", statusNoKey: "Не настроено", loading: "Загрузка…",
    swUnreachable: "Расширение не отвечает — нажми Reload в chrome://extensions",
    updateAvailable: "доступна версия {v}",
    emptyTitle: "Введите API-ключ",
    emptyText: "Ключ хранится только у вас и нужен для запроса квот GLM Coding Plan.",
    emptyAction: "Ввести ключ",
    session: "Сессия (5ч)", weekly: "Неделя", mcp: "MCP",
    apiKey: "API-ключ",
    apiKeyHint: "Ваш API-ключ GLM Coding Plan. Никому не передавайте.",
    keyPersist: "Запомнить на этом устройстве (шифровать)",
    keyStateNone: "Ключ не задан",
    keyStateSession: "Ключ только в памяти — исчезнет после закрытия браузера",
    keyStatePersisted: "Ключ сохранён в зашифрованном виде (но доступен через DevTools расширения)",
    platform: "Платформа",
    platformAuto: "Авто",
    platformCn: "Китай · open.bigmodel.cn",
    platformIntl: "Международный · api.z.ai",
    platformHint: "«Авто» определяет площадку по виду ключа.",
    interval: "Интервал обновления (сек)", viewMode: "Режим отображения",
    viewPopup: "Popup", viewWindow: "Окно", viewCompact: "Компактный",
    language: "Язык", theme: "Тема",
    themeLight: "Светлая", themeDark: "Тёмная",
    showMcp: "Показывать MCP",
    notifyLabel: "Уведомлять при 80/90% сессии",
    badgeLabel: "Процент на иконке",
    badgeMetricLabel: "Что показывает иконка",
    valueModeLabel: "Показывать числа как",
    valueModeRemaining: "Остаток",
    valueModeUsed: "Израсходовано",
    save: "Сохранить", reset: "Сбросить",
    saved: "Сохранено", resetDone: "Настройки сброшены",
    plan: "Тариф",
    utilSpent: "израсходовано", utilLeft: "осталось",
    sessionLeft: "Осталось · сессия (5ч)", weeklyLeft: "Осталось · неделя", mcpLeft: "Осталось · MCP",
    chartHours: "активность по часам",
    chartDays: "расход по дням недели",
    legendNow: "сейчас",
    legendReset: "сброс",
    footWhen: "Сброс", footSource: "Источник",
    today: "Сегодня HH:MM", tomorrow: "Завтра HH:MM",
    resetNow: "Сброс сейчас",
    weekdays: ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"],
  }
};

let currentLang = "en";
let currentSettings = {};
let latestData = null;
let tickTimer = null;
let toastTimer = null;
let historyData = [];
let lastUpdateInfo = null;      // кэш lastUpdate — читаем из storage не каждый кадр
let updateAvailableInfo = null; // метка «доступна новая версия» (постоянная)
let lastChartHour = null;       // час, для которого нарисована скользящая шкала

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

// Режим показа чисел: остаток (по умолчанию) или израсходовано.
// Ключ хранилища — valueMode (старый badgeMode поддерживается для миграции).
function valueMode() {
  return currentSettings.valueMode === "used" ? "used" : "remaining";
}

function levelColor(p) {
  if (p < 50) return "var(--ok)";
  if (p < 75) return "var(--warn)";
  if (p < 90) return "var(--hot)";
  return "var(--risk)";
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

// ── Данные ────────────────────────────────────────────────
async function loadHistory() {
  try {
    const r = await chrome.storage.local.get("history");
    historyData = Array.isArray(r.history) ? r.history : [];
  } catch (e) {
    historyData = [];
  }
}

async function loadLastUpdate() {
  try {
    const r = await chrome.storage.local.get("lastUpdate");
    lastUpdateInfo = r.lastUpdate || null;
  } catch (e) {
    lastUpdateInfo = null;
  }
}

// Постоянная метка «доступно обновление» — видна всегда, а не только во
// всплывающем уведомлении.
async function loadUpdateInfo() {
  try {
    const r = await chrome.storage.local.get("updateAvailable");
    updateAvailableInfo = r.updateAvailable || null;
  } catch (e) {
    updateAvailableInfo = null;
  }
}
// Окно часов вокруг «сейчас» + отметки границ сброса окна.
function sessionWindow() {
  const now = Date.now();
  const win = GLMQuota.activityWindow(
    historyData.map((p) => ({ ts: p.ts, v: p.s })), now, 6, 3);
  const s = latestData && latestData.s;
  const marks = [];
  if (s && s.resetsAt) {
    const nextMs = new Date(s.resetsAt).getTime();
    // Границы окна: прошлый сброс (≈5 ч назад) и следующий.
    for (const ms of [nextMs, nextMs - 5 * 3600e3]) {
      const idx = win.bars.findIndex(
        (b) => ms >= b.startMs && ms < b.startMs + 3600e3);
      if (idx >= 0 && !marks.includes(idx)) marks.push(idx);
    }
  }
  win.resets = marks;
  return win;
}

// Недельное окно: сегодня и день сброса недельного окна.
function weeklyWindow() {
  const buckets = GLMQuota.dailyActivity(
    historyData.map((p) => ({ ts: p.ts, v: p.w })), Date.now());
  const todayIdx = (new Date().getDay() + 6) % 7;
  const resets = [];
  const w = latestData && latestData.w;
  if (w && w.resetsAt) {
    const idx = (new Date(w.resetsAt).getDay() + 6) % 7;
    if (buckets[idx]) resets.push(idx);
  }
  return {
    bars: buckets.map((b) => ({ ...b, isNow: b.index === todayIdx })),
    labels: t("weekdays"),
    nowIndex: todayIdx,
    resets,
  };
}

// График активности: столбцы, подписи и отметки «сейчас» (сплошная) и
// «сброс» (пунктирная).
function renderTimeChart(el, bars, labels, opts) {
  if (!el) return;
  const list = Array.isArray(bars) ? bars : [];
  if (!list.some((b) => b && b.has)) { el.style.display = "none"; return; }
  el.style.display = "block";

  const W = 328;
  const laneH = 9;                       // полка для отметок — над столбцами
  const barH = (opts && opts.barH) || 30;
  const H = laneH + barH;                // базовая линия столбцов
  const max = Math.max(1, ...list.map((b) => b.value));
  const slot = W / list.length;
  const NS = "http://www.w3.org/2000/svg";

  while (el.firstChild) el.removeChild(el.firstChild);

  list.forEach((b, i) => {
    const x = i * slot + 1;
    const w = Math.max(2, slot - 2);
    const rect = document.createElementNS(NS, "rect");
    if (!b.has) {
      rect.setAttribute("x", String(x));
      rect.setAttribute("y", String(H - 2));
      rect.setAttribute("width", String(w));
      rect.setAttribute("height", "2");
      rect.setAttribute("style", b.isFuture
        ? "fill:var(--track);opacity:.35" : "fill:var(--track)");
    } else {
      const h = Math.max(2, (b.value / max) * (barH - 2));
      rect.setAttribute("x", String(x));
      rect.setAttribute("y", String(H - h));
      rect.setAttribute("width", String(w));
      rect.setAttribute("height", String(h));
      rect.setAttribute("rx", "1.5");
      rect.setAttribute("style", b.isNow
        ? "fill:var(--accent)"
        : "fill:var(--accent);opacity:" + (0.35 + 0.5 * (b.value / max)).toFixed(2));
    }
    el.appendChild(rect);
  });

  // Отметки живут на верхней полке и не наезжают на столбцы:
  // «сейчас» — контрастная сплошная (цвет текста), «сброс» — красная пунктирная.
  const nowIndex = opts && opts.nowIndex;
  if (nowIndex != null && nowIndex >= 0) {
    const x = nowIndex * slot + slot / 2;
    const line = document.createElementNS(NS, "line");
    line.setAttribute("x1", String(x));
    line.setAttribute("x2", String(x));
    line.setAttribute("y1", "0");
    line.setAttribute("y2", String(laneH - 2));
    line.setAttribute("style", "stroke:var(--text);stroke-width:2");
    el.appendChild(line);
    const dot = document.createElementNS(NS, "circle");
    dot.setAttribute("cx", String(x));
    dot.setAttribute("cy", String(laneH - 1.5));
    dot.setAttribute("r", "1.8");
    dot.setAttribute("style", "fill:var(--text)");
    el.appendChild(dot);
  }

  ((opts && opts.resets) || []).forEach((idx) => {
    const x = idx * slot + slot / 2;
    const line = document.createElementNS(NS, "line");
    line.setAttribute("x1", String(x));
    line.setAttribute("x2", String(x));
    line.setAttribute("y1", "0");
    line.setAttribute("y2", String(laneH - 2));
    line.setAttribute("style", "stroke:var(--risk);stroke-width:2;stroke-dasharray:2.5 1.5");
    el.appendChild(line);
    const dot = document.createElementNS(NS, "circle");
    dot.setAttribute("cx", String(x));
    dot.setAttribute("cy", String(laneH - 1.5));
    dot.setAttribute("r", "1.8");
    dot.setAttribute("style", "fill:var(--risk)");
    el.appendChild(dot);
  });

  // Подписи.
  (labels || []).forEach((label, i) => {
    const node = document.createElementNS(NS, "text");
    node.setAttribute("x", String(i * slot + slot / 2));
    node.setAttribute("y", String(H + 8));
    node.setAttribute("text-anchor", "middle");
    node.setAttribute("style", "fill:" + (i === nowIndex ? "var(--text)" : "var(--muted)") +
      ";font-size:" + (labels.length > 8 ? "7px" : "9px"));
    node.textContent = label;
    el.appendChild(node);
  });
}

// Маленькая расшифровка под графиком: что означают линии.
function renderLegend(el, items) {
  if (!el) return;
  while (el.firstChild) el.removeChild(el.firstChild);
  for (const item of items) {
    const wrap = document.createElement("span");
    wrap.className = "legend-item";
    const sw = document.createElement("i");
    sw.className = "legend-swatch" + (item.dashed ? " dashed" : "");
    sw.style.setProperty("--sw", item.color);
    const label = document.createElement("b");
    label.textContent = item.label;
    wrap.appendChild(sw);
    wrap.appendChild(label);
    el.appendChild(wrap);
  }
}

function updateChartLegends() {
  const items = [
    { color: "var(--text)", label: t("legendNow") },
    { color: "var(--risk)", dashed: true, label: t("legendReset") },
  ];
  renderLegend($("sessionLegend"), items);
  renderLegend($("weeklyLegend"), items);
}

// ── Рендеринг ─────────────────────────────────────────────
function setStatus(kind, text, extra) {
  const dot = document.querySelector("#status .dot");
  const txt = document.querySelector("#status .status-text");
  if (dot) dot.className = "dot " + kind;
  if (txt) txt.textContent = text;
  const ex = $("statusExtra");
  if (ex) ex.textContent = extra || "";
}

// Подпись под числом: в зависимости от режима — «израсходовано» или «осталось».
function metricNote(data) {
  const used = Number(data.utilization);
  const remaining = 100 - used;
  const total = data.total != null ? GLMQuota.formatAmount(data.total, currentLang) : null;
  if (valueMode() === "used") {
    const remTxt = data.remaining != null ? GLMQuota.formatAmount(data.remaining, currentLang) : null;
    const abs = (remTxt && total) ? " · " + remTxt + " / " + total : "";
    return t("utilLeft") + " " + remaining.toFixed(0) + "%" + abs;
  }
  const spentTxt = data.spent != null ? GLMQuota.formatAmount(data.spent, currentLang) : null;
  const abs = (spentTxt && total) ? " " + spentTxt + " / " + total : "";
  return t("utilSpent") + " " + used.toFixed(0) + "%" + abs;
}

// Число, которое показываем (остаток или израсходовано).
function displayValue(used) {
  return valueMode() === "used" ? used : 100 - used;
}

// Главная карточка (сессия): крупное число + полоса.
function showHero(elId, pctId, barId, annoId, data) {
  const el = $(elId);
  if (!data || typeof data !== "object") { el.style.display = "none"; return; }
  el.style.display = "";
  const used = Number(data.utilization);
  if (isNaN(used)) {
    $(pctId).textContent = "—";
    $(barId).style.width = "0%";
    $(annoId).textContent = "";
    return;
  }
  const shown = displayValue(used);
  $(pctId).textContent = shown.toFixed(0);
  $(pctId).style.color = levelColor(used);
  $(barId).style.width = shown + "%";
  $(barId).style.background = levelColor(used);
  $(annoId).textContent = metricNote(data);
}

// Компактная строка (неделя, MCP): значение + мини-полоса + время сброса.
function showLine(elId, valueId, barId, whenId, annoId, data) {
  const el = $(elId);
  if (!data || typeof data !== "object") { el.style.display = "none"; return; }
  el.style.display = "";
  const used = Number(data.utilization);
  if (isNaN(used)) {
    $(valueId).textContent = "—";
    $(barId).style.width = "0%";
    return;
  }
  const shown = displayValue(used);
  $(valueId).textContent = shown.toFixed(0) + "%";
  $(valueId).style.color = levelColor(used);
  $(barId).style.width = shown + "%";
  $(barId).style.background = levelColor(used);
  if (annoId) $(annoId).textContent = metricNote(data);
  if (whenId) {
    const ms = data.resetsAt ? new Date(data.resetsAt).getTime() : null;
    $(whenId).textContent = ms ? fmtCountdown(ms) : "—";
  }
}

function showChip(chipId, pctId, data) {
  const chip = $(chipId);
  if (!data || typeof data !== "object") { chip.style.display = "none"; return; }
  chip.style.display = "";
  const used = Number(data.utilization);
  if (isNaN(used)) { $(pctId).textContent = "—"; return; }
  const shown = displayValue(used);
  $(pctId).textContent = shown.toFixed(0);
  $(pctId).style.color = levelColor(used);
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
  let parts = ["v" + chrome.runtime.getManifest().version];
  parts.push(data.platform === "intl" ? "api.z.ai" : "open.bigmodel.cn");
  if (lastUpdateInfo && lastUpdateInfo.at) {
    const d = new Date(lastUpdateInfo.at);
    parts.push((currentLang === "ru" ? "обновлено " : "updated ") +
      d.toLocaleDateString() + " " +
      d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }));
  }
  $("footSource").textContent = parts.join(" · ");
}

function showEmptyState() {
  $("emptyState").style.display = "";
  $("loading").style.display = "none";
  $("metricSession").style.display = "none";
  $("metricWeekly").style.display = "none";
  $("metricMcp").style.display = "none";
  $("planBadge").style.display = "none";
  $("footer").style.display = "none";
  setStatus("offline", t("statusNoKey"));
}

function render(data) {
  latestData = data;

  if (data && data.noKey) {
    showEmptyState();
    return;
  }
  $("emptyState").style.display = "none";

  if (!data || !data.ok) {
    const errTxt = (data && data.error) ? String(data.error) : t("statusError");
    setStatus("error", t("statusError"));
    $("loading").textContent = errTxt;
    $("loading").style.display = "";
    $("metricSession").style.display = "none";
    $("metricWeekly").style.display = "none";
    $("metricMcp").style.display = "none";
    $("planBadge").style.display = "none";
    $("footer").style.display = "none";
    return;
  }

  setStatus("online", t("statusOnline"), (updateAvailableInfo
    ? "↑ " + t("updateAvailable").replace("{v}", updateAvailableInfo.version)
    : ""));
  $("loading").style.display = "none";

  const viewMode = normalizeViewMode(currentSettings.viewMode);
  $("compact").classList.toggle("active", viewMode === "compact");
  if (viewMode === "compact") {
    $("metricSession").style.display = "none";
    $("metricWeekly").style.display = "none";
    $("metricMcp").style.display = "none";
    $("planBadge").style.display = "none";
    $("compactView").style.display = "";
    showChip("chipSession", "chipSessionPct", data.s);
    showChip("chipWeekly", "chipWeeklyPct", data.w);
    renderFooter(data);
    return;
  }
  $("compactView").style.display = "none";

  showHero("metricSession", "sessionPct", "sessionBar", "sessionAnno", data.s);
  showLine("metricWeekly", "weeklyPct", "weeklyBar", "weeklyReset", "weeklyAnno", data.w);
  if (currentSettings.showMcp && data.mcp) {
    showLine("metricMcp", "mcpPct", "mcpBar", "mcpReset", "mcpAnno", data.mcp);
  } else {
    $("metricMcp").style.display = "none";
  }
  if (data.plan) {
    $("planBadge").textContent = t("plan") + ": " + data.plan;
    $("planBadge").style.display = "";
  } else {
    $("planBadge").style.display = "none";
  }

  // Активность сессии — скользящее окно часов вокруг «сейчас» (3 ч назад
  // и 3 ч вперёд), с отметками «сейчас» и границ сброса окна.
  const sWin = sessionWindow();
  renderTimeChart($("sessionChart"), sWin.bars,
    sWin.bars.map((b) => String(b.hour).padStart(2, "0")),
    { barH: 30, nowIndex: sWin.before, resets: sWin.resets });

  // Расход недельного окна по дням недели (Пн..Вс): отметки «сегодня»
  // и дня сброса недели.
  const wWin = weeklyWindow();
  renderTimeChart($("weeklyChart"), wWin.bars, wWin.labels,
    { barH: 18, nowIndex: wWin.nowIndex, resets: wWin.resets });

  updateChartLegends();

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

// ── Всплывающее подтверждение ─────────────────────────────
function showToast(text) {
  const el = $("toast");
  if (!el) return;
  el.textContent = text;
  el.style.display = "block";
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.style.display = "none"; }, 2200);
}

// ── Настройки ──────────────────────────────────────────────
async function loadSettings() {
  const r = await chrome.storage.local.get([
    "platform", "intervalSec", "viewMode", "lang", "theme",
    "showMcp", "notify", "badge", "badgeMetric", "valueMode", "badgeMode", "keyPersist"
  ]);
  const storedPersist = await chrome.storage.local.get("keyPersist");
  // По умолчанию ключ запоминается — но в зашифрованном виде.
  const persist = storedPersist.keyPersist !== false;
  const hasKey = Boolean(await GLMKeyring.getApiKey());
  currentSettings = {
    platform: normalizePlatform(r.platform),
    intervalSec: r.intervalSec || 120,
    viewMode: normalizeViewMode(r.viewMode),
    lang: r.lang || "en",
    theme: normalizeTheme(r.theme),
    showMcp: r.showMcp || false,
    notify: r.notify !== false,
    badge: r.badge !== false,
    badgeMetric: r.badgeMetric === "w" ? "w" : "s",
    valueMode: (r.valueMode || r.badgeMode) === "used" ? "used" : "remaining",
    keyPersist: persist
  };
  currentLang = currentSettings.lang;

  $("platformSelect").value = currentSettings.platform;
  $("intervalInput").value = currentSettings.intervalSec;
  $("viewModeSelect").value = currentSettings.viewMode;
  $("langSelect").value = currentSettings.lang;
  $("themeSelect").value = currentSettings.theme;
  $("showMcpCheck").checked = currentSettings.showMcp;
  $("notifyCheck").checked = currentSettings.notify;
  $("badgeCheck").checked = currentSettings.badge;
  $("badgeMetricSelect").value = currentSettings.badgeMetric;
  $("badgeModeSelect").value = currentSettings.valueMode;
  $("keyPersistCheck").checked = persist;

  // Сам ключ никогда не показываем и не подставляем в поле — только статус.
  $("apiKeyInput").value = "";
  $("apiKeyInput").placeholder = hasKey ? "••••••••" : "glm_… / zai_…";
  $("keyState").textContent = !hasKey
    ? t("keyStateNone")
    : (persist ? t("keyStatePersisted") : t("keyStateSession"));

  applyI18n();
  applyTheme(currentSettings.theme);
  updateBadgeField();
}

async function saveSettings() {
  // Ключ: пустое поле = оставить текущий, не удалять.
  const keyValue = $("apiKeyInput").value.trim();
  const persist = $("keyPersistCheck").checked;
  if (keyValue) {
    await GLMKeyring.setApiKey(keyValue, persist);
  } else {
    const currentKey = await GLMKeyring.getApiKey();
    if (currentKey) await GLMKeyring.setApiKey(currentKey, persist);
  }
  currentSettings = {
    platform: normalizePlatform($("platformSelect").value),
    intervalSec: parseInt($("intervalInput").value) || 120,
    viewMode: normalizeViewMode($("viewModeSelect").value),
    lang: $("langSelect").value,
    theme: $("themeSelect").value,
    showMcp: $("showMcpCheck").checked,
    notify: $("notifyCheck").checked,
    badge: $("badgeCheck").checked,
    badgeMetric: $("badgeMetricSelect").value,
    valueMode: $("badgeModeSelect").value,
    keyPersist: persist
  };
  await chrome.storage.local.set(currentSettings);
  currentLang = currentSettings.lang;
  applyI18n();
  applyTheme(currentSettings.theme);

  // Notify background to apply new settings (await so storage is flushed first)
  try { await chrome.runtime.sendMessage({ type: "applySettings" }); } catch (e) {}
  showToast(t("saved"));
  await poll();
  await loadSettings();   // обновить статус ключа и плейсхолдер
}

async function resetSettings() {
  await GLMKeyring.clearApiKey();
  await chrome.storage.local.remove([
    "platform", "intervalSec", "viewMode", "lang", "theme",
    "showMcp", "notify", "badge", "badgeMetric", "valueMode", "keyPersist"
  ]);
  await loadSettings();
  // Background keeps its own alarm/popup state — re-apply defaults there too.
  try { await chrome.runtime.sendMessage({ type: "applySettings" }); } catch (e) {}
  showToast(t("resetDone"));
  poll();
}

// ── Polling ───────────────────────────────────────────────
// Таймаут запроса к service worker: если он мёртв, попап не должен висеть
// на «Загрузка…» бесконечно.
const POLL_TIMEOUT_MS = 6000;

function withTimeout(promise, ms) {
  return Promise.race([
    promise,
    new Promise((_, reject) => setTimeout(() => reject(new Error("timeout")), ms)),
  ]);
}

async function poll() {
  try {
    const r = await withTimeout(chrome.runtime.sendMessage({ type: "poll" }), POLL_TIMEOUT_MS);
    await loadHistory();
    await loadLastUpdate();
    await loadUpdateInfo();
    render(r && r.ok ? r : (r && r.noKey ? r : { ok: false }));
  } catch (e) {
    await loadHistory();
    render({ ok: false, error: t("swUnreachable") });
  }
}

// ── Переключатели ─────────────────────────────────────────
function toggleCompact() {
  const next = currentSettings.viewMode === "compact" ? "popup" : "compact";
  $("viewModeSelect").value = next;
  currentSettings.viewMode = next;
  chrome.storage.local.set({ viewMode: next });
  render(latestData);
}

function toggleSettings(forceOpen) {
  const panel = $("settingsPanel");
  const open = forceOpen === true ? true : (forceOpen === false ? false : panel.style.display === "none");
  panel.style.display = open ? "" : "none";
}

// Селекторы «что показывать» скрыты, когда бейдж выключен.
function updateBadgeField() {
  const on = $("badgeCheck").checked;
  const metric = $("badgeMetricField");
  const mode = $("badgeModeField");
  if (metric) metric.style.display = on ? "" : "none";
  if (mode) mode.style.display = on ? "" : "none";
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

// ── Tick (обратный отсчёт) ────────────────────────────────
function tickUpdate() {
  if (!latestData || !latestData.ok) return;
  const pairs = [
    ["metricSession", "sessionReset", "s"],
    ["metricWeekly", "weeklyReset", "w"],
    ["metricMcp", "mcpReset", "mcp"],
  ];
  for (const [elId, resetId, key] of pairs) {
    const el = $(elId);
    if (!el || el.style.display === "none") continue;
    const w = latestData[key];
    if (!w || !w.resetsAt) continue;
    const ms = new Date(w.resetsAt).getTime();
    $(resetId).textContent = fmtCountdown(ms) + " · " + fmtAt(ms);
  }
  const s = latestData.s;
  if (s && s.resetsAt) {
    const ms = new Date(s.resetsAt).getTime();
    $("footReset").textContent = fmtCountdown(ms) + " · " + fmtAt(ms);
  }
  // Скользящая шкала переезжает вместе с текущим часом — перерисовываем
  // только когда час реально сменился.
  const hour = new Date().getHours();
  if (hour !== lastChartHour) {
    lastChartHour = hour;
    const sWin = sessionWindow();
    renderTimeChart($("sessionChart"), sWin.bars,
      sWin.bars.map((b) => String(b.hour).padStart(2, "0")),
      { barH: 30, nowIndex: sWin.before, resets: sWin.resets });
  }
}

function startTick() {
  if (tickTimer) clearInterval(tickTimer);
  tickTimer = setInterval(tickUpdate, 1000);
}

// ── Init ──────────────────────────────────────────────────
async function init() {
  await loadSettings();
  await loadHistory();
  await loadLastUpdate();
  await loadUpdateInfo();
  const cached = await chrome.storage.local.get("latest");
  if (cached.latest) render(cached.latest);
  if (!IS_PIP_CLONE) await poll();
  startTick();

  $("refresh").addEventListener("click", poll);
  $("compact").addEventListener("click", toggleCompact);
  $("settings").addEventListener("click", () => toggleSettings());
  $("closeSettings").addEventListener("click", () => toggleSettings(false));
  $("emptySettings").addEventListener("click", () => toggleSettings(true));
  $("pin").addEventListener("click", togglePin);
  $("popout").addEventListener("click", popout);
  $("saveSettings").addEventListener("click", saveSettings);
  $("resetSettings").addEventListener("click", resetSettings);
  $("badgeCheck").addEventListener("change", updateBadgeField);

  $("langSelect").addEventListener("change", async (e) => {
    currentLang = e.target.value;
    currentSettings.lang = currentLang;
    // Язык и тему сохраняем сразу: иначе предпросмотр терялся бы при
    // закрытии попапа без нажатия Save.
    await chrome.storage.local.set({ lang: currentLang });
    applyI18n();
    render(latestData);
  });
  $("themeSelect").addEventListener("change", async (e) => {
    currentSettings.theme = e.target.value;
    await chrome.storage.local.set({ theme: currentSettings.theme });
    applyTheme(currentSettings.theme);
  });
}

// Клон Document PiP не опрашивает API сам — обновления приходят через
// chrome.storage.onChanged, иначе два экземпляра шлют запросы наперегонки.
const IS_PIP_CLONE = (typeof documentPictureInPicture !== "undefined" &&
  documentPictureInPicture.window === window);

// Настройки могли измениться в другом окне (PiP, второе окно, трей) —
// подхватываем их без перезагрузки попапа.
chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== "local") return;
  if (changes.latest) {
    const d = changes.latest.newValue;
    if (d) { latestData = d; render(d); }
  }
  if (changes.history) {
    loadHistory().then(() => render(latestData));
  }
  if (changes.lastUpdate) {
    loadLastUpdate().then(() => render(latestData));
  }
  if (changes.updateAvailable) {
    loadUpdateInfo().then(() => render(latestData));
  }
  if (changes.lang) {
    currentSettings.lang = changes.lang.newValue || "en";
    currentLang = currentSettings.lang;
    applyI18n();
    $("langSelect").value = currentLang;
    render(latestData);
  }
  if (changes.theme) {
    currentSettings.theme = changes.theme.newValue || "light";
    applyTheme(currentSettings.theme);
    $("themeSelect").value = currentSettings.theme;
  }
  if (changes.viewMode) {
    currentSettings.viewMode = normalizeViewMode(changes.viewMode.newValue);
    $("viewModeSelect").value = currentSettings.viewMode;
    render(latestData);
  }
  if (changes.valueMode || changes.badgeMode) {
    currentSettings.valueMode = (changes.valueMode && changes.valueMode.newValue) ||
      (changes.badgeMode && changes.badgeMode.newValue) || "remaining";
    $("badgeModeSelect").value = currentSettings.valueMode;
    render(latestData);
  }
  if (changes.showMcp || changes.notify || changes.badge ||
      changes.badgeMetric || changes.platform) {
    render(latestData);
  }
});

// Runs both in the popup document and in the Document PiP clone,
// where the script is injected after DOMContentLoaded has fired.
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
