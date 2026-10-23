// GLM Limits — чистая логика разбора квот GLM и порогов.
//
// Вынесена отдельно от chrome-API, чтобы её можно было тестировать без
// браузера:  node --test tests/
//
// Формат ответа API (data.limits[]):
//   type: TOKENS_LIMIT | CREDIT_LIMIT | TIME_LIMIT
//   unit/number: 3/5 — окно 5 часов, 6/1 — неделя
//   percentage:  СКОЛЬКО ИЗРАСХОДОВАНО (не сколько осталось)
//   usage/currentValue/remaining: абсолютные значения (если API их отдаёт)
const GLMQuota = (() => {
  "use strict";

  const TOKEN_TYPES = ["TOKENS_LIMIT", "CREDIT_LIMIT"];
  const MONTHLY_TYPE = "TIME_LIMIT";
  const SESSION_WINDOW = { unit: 3, number: 5 };
  const WEEKLY_WINDOW = { unit: 6, number: 1 };
  // Пороги уведомлений о расходе сессии (по возрастанию).
  const NOTIFY_THRESHOLDS = [80, 90, 100];
  const BADGE_COLORS = {
    green: "#5a9367", yellow: "#c2a14a", amber: "#bf7a40", red: "#b5524c",
  };

  function toNumber(value) {
    if (value === null || value === undefined || value === "") return null;
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  }

  // Нормализация одного окна квоты.
  function normalizeWindow(item) {
    if (!item || typeof item !== "object") return null;
    const used = toNumber(item.percentage);
    const resetMs = toNumber(item.nextResetTime);
    return {
      utilization: used == null ? 0 : Math.min(Math.max(used, 0), 100),
      resetsAt: resetMs && resetMs > 0 ? new Date(resetMs).toISOString() : null,
      total: toNumber(item.usage),
      spent: toNumber(item.currentValue),
      remaining: toNumber(item.remaining),
    };
  }

  // Разбор массива limits -> { session, weekly, mcp }.
  function parseLimits(limits) {
    const out = { session: null, weekly: null, mcp: null };
    for (const item of Array.isArray(limits) ? limits : []) {
      if (!item || typeof item !== "object") continue;

      if (item.type === MONTHLY_TYPE) {
        out.mcp = normalizeWindow(item);
        continue;
      }
      if (!TOKEN_TYPES.includes(item.type)) continue;

      const unit = toNumber(item.unit);
      const number = toNumber(item.number);
      const norm = normalizeWindow(item);

      if (unit === SESSION_WINDOW.unit && number === SESSION_WINDOW.number) {
        out.session = norm;
      } else if (unit === WEEKLY_WINDOW.unit && number === WEEKLY_WINDOW.number) {
        out.weekly = norm;
      } else if (!out.session && unit === SESSION_WINDOW.unit) {
        out.session = norm;   // fallback: любое часовое окно
      } else if (!out.weekly && unit === WEEKLY_WINDOW.unit) {
        out.weekly = norm;     // fallback: любое недельное окно
      }
    }
    return out;
  }

  // Площадка по виду ключа: id.secret (с точкой) -> bigmodel.cn, иначе z.ai.
  function detectPlatform(key) {
    const k = (key || "").trim();
    if (!k) return "cn";
    if (/^zai[_-]/i.test(k)) return "intl";
    if (k.includes(".")) return "cn";
    return "intl";
  }

  // Цвет бейджа по уровню расхода (те же пороги, что у полосок).
  function badgeColor(used) {
    if (used < 50) return BADGE_COLORS.green;
    if (used < 75) return BADGE_COLORS.yellow;
    if (used < 90) return BADGE_COLORS.amber;
    return BADGE_COLORS.red;
  }

  // Текст бейджа: "100" в Chrome обрезается, поэтому "99+".
  function badgeText(value) {
    const n = toNumber(value);
    if (n == null) return "";
    const rounded = Math.round(n);
    if (rounded <= 0) return "0";
    if (rounded >= 100) return "99+";
    return String(rounded);
  }

  // Решение об уведомлении: по одному уведомлению на порог в пределах
  // окна сброса. Возвращает новый state (чистая функция).
  // Пороги перебираются от большего к меньшему: при скачке 75% -> 95%
  // сообщаем о 90%, а не о 80% (и 80% помечаем пройденным).
  function notifyDecision(prev, used, winKey) {
    const usedNum = toNumber(used);
    const state = (prev && prev.winKey === winKey)
      ? Object.assign({}, prev)
      : { winKey, s80: false, s90: false, s100: false };
    if (usedNum == null) return { threshold: null, state };
    for (const th of NOTIFY_THRESHOLDS.slice().reverse()) {
      if (usedNum >= th && !state["s" + th]) {
        // Порог пройден — нижние считаем пройденными: одно уведомление.
        for (const lower of NOTIFY_THRESHOLDS) {
          if (lower <= th) state["s" + lower] = true;
        }
        return { threshold: th, state };
      }
    }
    return { threshold: null, state };
  }

  // Число с разделителями thousands (ru: пробел, en: запятая).
  function formatAmount(value, lang) {
    const n = toNumber(value);
    if (n == null) return null;
    try {
      return new Intl.NumberFormat(lang === "ru" ? "ru-RU" : "en-US").format(Math.round(n));
    } catch (e) {
      return String(Math.round(n));
    }
  }

  // Активность по часам суток: 24 столбца (часы 00..23), значение — сколько
  // окна сожгли за этот час (сумма положительных приростов расхода).
  // Так график показывает именно активность, а не «уровень окна» с его
  // пилами на каждом сбросе.
  function hourlyActivity(points, nowMs, windowHours) {
    const hours = windowHours || 24;
    const now = nowMs || Date.now();
    const start = now - hours * 3600e3;
    const buckets = new Array(24).fill(0);
    const seen = new Array(24).fill(false);
    const sorted = (Array.isArray(points) ? points : [])
      .filter(p => p && typeof p.ts === "number" && typeof p.v === "number")
      .slice()
      .sort((a, b) => a.ts - b.ts);
    for (let i = 1; i < sorted.length; i++) {
      const prev = sorted[i - 1];
      const cur = sorted[i];
      if (cur.ts < start || cur.ts > now) continue;
      const delta = cur.v - prev.v;
      if (delta <= 0) continue;   // сброс окна или откат — не активность
      const hour = new Date(cur.ts).getHours();
      buckets[hour] += delta;
      seen[hour] = true;
    }
    return buckets.map((value, hour) => ({ hour, value, has: seen[hour] }));
  }

  // Активность по дням ТЕКУЩЕЙ недели: 7 столбцов (0 = понедельник …
  // 6 = воскресенье). Будущие дни пустые — данные прошлой недели в них
  // не подмешиваются.
  function dailyActivity(points, nowMs) {
    const now = nowMs || Date.now();
    const d = new Date(now);
    const dow = (d.getDay() + 6) % 7;   // 0 = понедельник
    const weekStart = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    weekStart.setDate(weekStart.getDate() - dow);
    const start = weekStart.getTime();

    const buckets = new Array(7).fill(0);
    const seen = new Array(7).fill(false);
    const sorted = (Array.isArray(points) ? points : [])
      .filter(p => p && typeof p.ts === "number" && typeof p.v === "number")
      .slice()
      .sort((a, b) => a.ts - b.ts);
    for (let i = 1; i < sorted.length; i++) {
      const prev = sorted[i - 1];
      const cur = sorted[i];
      if (cur.ts < start || cur.ts > now) continue;
      const delta = cur.v - prev.v;
      if (delta <= 0) continue;
      const day = Math.min(6, Math.max(0, Math.floor((cur.ts - start) / 86400e3)));
      buckets[day] += delta;
      seen[day] = true;
    }
    return buckets.map((value, index) => ({ index, value, has: seen[index] }));
  }

  // Скользящее окно активности: spanHours столбцов вокруг текущего часа
  // (beforeHours слева, остальное справа). Бакеты — по РЕАЛЬНЫМ часовым
  // слотам окна, поэтому будущие часы всегда пустые (вчерашняя активность
  // в тот же час суток туда не подмешивается).
  function activityWindow(points, nowMs, spanHours, beforeHours) {
    const span = spanHours || 6;
    const before = beforeHours == null ? Math.floor(span / 2) : beforeHours;
    const now = nowMs || Date.now();

    const nowHourStart = new Date(now);
    nowHourStart.setMinutes(0, 0, 0);
    const nowHourMs = nowHourStart.getTime();
    const start = nowHourMs - before * 3600e3;

    const bars = [];
    for (let i = 0; i < span; i++) {
      const slotStart = start + i * 3600e3;
      bars.push({
        hour: new Date(slotStart).getHours(),
        startMs: slotStart,
        value: 0,
        has: false,
        isNow: i === before,
        isFuture: slotStart > nowHourMs,
      });
    }

    const sorted = (Array.isArray(points) ? points : [])
      .filter(p => p && typeof p.ts === "number" && typeof p.v === "number")
      .slice()
      .sort((a, b) => a.ts - b.ts);
    for (let i = 1; i < sorted.length; i++) {
      const prev = sorted[i - 1];
      const cur = sorted[i];
      const delta = cur.v - prev.v;
      if (delta <= 0) continue;   // сброс окна или откат
      if (cur.ts < start || cur.ts >= start + span * 3600e3) continue;
      const idx = Math.floor((cur.ts - start) / 3600e3);
      if (idx < 0 || idx >= span) continue;
      bars[idx].value += delta;
      bars[idx].has = true;
    }

    return { bars, span, before, nowHourMs };
  }


  // Разбор ответа /api/monitor/usage/model-usage:
  // data.modelDataList = [{modelName, tokensUsage[], totalTokens}]
  function parseModelUsage(data) {
    if (!data || typeof data !== "object") return null;
    const list = Array.isArray(data.modelDataList) ? data.modelDataList : [];
    const models = [];
    let total = 0;
    for (const item of list) {
      if (!item || typeof item !== "object") continue;
      // totalTokens есть в текущем API; если вдруг пропадёт — суммируем ряд.
      let tokens = toNumber(item.totalTokens) || 0;
      if (!tokens && Array.isArray(item.tokensUsage)) {
        tokens = item.tokensUsage.reduce((s, v) => s + (toNumber(v) || 0), 0);
      }
      if (tokens <= 0) continue;
      models.push({ name: String(item.modelName || "?"), tokens });
      total += tokens;
    }
    if (!models.length) return null;
    models.sort((a, b) => b.tokens - a.tokens);
    for (const m of models) m.share = total > 0 ? m.tokens / total : 0;
    return {
      models,
      total,
      hours: Array.isArray(data.x_time) ? data.x_time.length : 0,
      calls: toNumber((data.totalUsage || {}).totalModelCallCount),
    };
  }

  const api = {
    TOKEN_TYPES, MONTHLY_TYPE, NOTIFY_THRESHOLDS, BADGE_COLORS,
    toNumber, normalizeWindow, parseLimits, detectPlatform,
    badgeColor, badgeText, notifyDecision, formatAmount,
    hourlyActivity, dailyActivity, activityWindow, parseModelUsage,
  };

  if (typeof globalThis !== "undefined") globalThis.GLMQuota = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  return api;
})();
