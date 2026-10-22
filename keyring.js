// GLM Limits — хранение API-ключа.
//
// По умолчанию ключ живёт ТОЛЬКО в оперативной памяти (chrome.storage.session):
// он не пишется на диск профиля, поэтому его нельзя вытащить из файлов
// профиля или из бэкапа. В режиме «Запомнить на этом устройстве» ключ
// шифруется AES-GCM ключом устройства и лежит в chrome.storage.local —
// это защищает от случайной утечки (grep по профилю, скриншот раздела
// storage), но не от владельца профиля с доступом к DevTools.
//
// Честное ограничение: расширение обязано где-то держать ключ, чтобы слать
// Authorization — полностью спрятать его от владельца браузера нельзя.
// Поэтому режим по умолчанию — «только в памяти»: после перезапуска браузера
// ключ нужно ввести заново (он не сохранялся ни на диске, ни в RAM).
const GLMKeyring = (() => {
  "use strict";

  const SESSION_KEY = "apiKey";       // оперативная память, не пишется на диск
  const LOCAL_ENC_KEY = "apiKeyEnc";  // {iv, data} — AES-GCM, если включено запоминание
  const DEVICE_KEY = "deviceKey";     // случайный 32-байтный ключ устройства

  function bytesToB64(bytes) {
    const arr = new Uint8Array(bytes);
    let bin = "";
    for (let i = 0; i < arr.length; i++) bin += String.fromCharCode(arr[i]);
    return btoa(bin);
  }

  function b64ToBytes(b64) {
    const bin = atob(b64);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }

  async function getDeviceKey() {
    const store = await chrome.storage.local.get(DEVICE_KEY);
    if (store[DEVICE_KEY]) return b64ToBytes(store[DEVICE_KEY]);
    const raw = crypto.getRandomValues(new Uint8Array(32));
    await chrome.storage.local.set({ [DEVICE_KEY]: bytesToB64(raw) });
    return raw;
  }

  async function encryptKey(key, deviceKey) {
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const cryptoKey = await crypto.subtle.importKey(
      "raw", deviceKey, "AES-GCM", false, ["encrypt"]);
    const data = await crypto.subtle.encrypt(
      { name: "AES-GCM", iv }, cryptoKey, new TextEncoder().encode(key));
    return { iv: bytesToB64(iv), data: bytesToB64(new Uint8Array(data)) };
  }

  async function decryptKey(enc, deviceKey) {
    const cryptoKey = await crypto.subtle.importKey(
      "raw", deviceKey, "AES-GCM", false, ["decrypt"]);
    const plain = await crypto.subtle.decrypt(
      { name: "AES-GCM", iv: b64ToBytes(enc.iv) }, cryptoKey, b64ToBytes(enc.data));
    return new TextDecoder().decode(plain);
  }

  // Текущий ключ: session → расшифрованный local → null.
  async function getApiKey() {
    try {
      const session = await chrome.storage.session.get(SESSION_KEY);
      if (session[SESSION_KEY]) return session[SESSION_KEY];
    } catch (e) { /* session-хранилище может быть недоступно */ }
    const local = await chrome.storage.local.get([LOCAL_ENC_KEY, DEVICE_KEY, "apiKey"]);

    // Миграция со старого формата: ключ лежал открытым в chrome.storage.local
    // ("apiKey") — переносим его в защищённое хранилище и удаляем открытую копию.
    if (local.apiKey) {
      const legacy = String(local.apiKey).trim();
      await chrome.storage.local.remove("apiKey");
      if (legacy) {
        console.info("[GLM Limits] переносим ключ из открытого хранилища в защищённое");
        await setApiKey(legacy, true);
        return legacy;
      }
    }

    if (local[LOCAL_ENC_KEY] && local[DEVICE_KEY]) {
      try {
        return await decryptKey(local[LOCAL_ENC_KEY], b64ToBytes(local[DEVICE_KEY]));
      } catch (e) {
        console.warn("[GLM Limits] не удалось расшифровать сохранённый ключ");
        return null;
      }
    }
    return null;
  }

  async function setApiKey(key, persist) {
    const clean = (key || "").trim();
    if (!clean) { await clearApiKey(); return; }
    await chrome.storage.session.set({ [SESSION_KEY]: clean });
    if (persist) {
      const deviceKey = await getDeviceKey();
      await chrome.storage.local.set({
        [LOCAL_ENC_KEY]: await encryptKey(clean, deviceKey),
      });
    } else {
      await chrome.storage.local.remove(LOCAL_ENC_KEY);
    }
  }

  async function clearApiKey() {
    try { await chrome.storage.session.remove(SESSION_KEY); } catch (e) {}
    await chrome.storage.local.remove(LOCAL_ENC_KEY);
  }

  // Есть ли зашифрованный (переживающий перезапуск) ключ — для UI.
  async function hasPersistedKey() {
    const local = await chrome.storage.local.get(LOCAL_ENC_KEY);
    return Boolean(local[LOCAL_ENC_KEY]);
  }

  const api = {
    getApiKey, setApiKey, clearApiKey, hasPersistedKey,
    SESSION_KEY, LOCAL_ENC_KEY, DEVICE_KEY,
  };
  if (typeof globalThis !== "undefined") globalThis.GLMKeyring = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  return api;
})();
