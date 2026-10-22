// GLM Limits — тест манифеста: JSON должен парситься, а обязательные поля
// быть на месте. Именно битый manifest.json однажды сделал расширение
// «сломанным» (Chrome удаляет такое при следующем старте).
//
// Запуск:  node --test tests/
const { test } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const ROOT = path.resolve(__dirname, "..");
const manifest = JSON.parse(fs.readFileSync(path.join(ROOT, "manifest.json"), "utf8"));

test("manifest.json валиден и имеет нужные поля", () => {
  assert.equal(manifest.manifest_version, 3);
  assert.ok(manifest.name && manifest.name.length > 0, "должно быть имя");
  assert.match(manifest.version, /^\d+\.\d+\.\d+$/, "версия в формате X.Y.Z");
});

test("manifest: ключевые файлы расширения существуют", () => {
  for (const f of ["background.js", "popup.js", "popup.html", "popup.css",
                   "quota.js", "keyring.js"]) {
    assert.ok(fs.existsSync(path.join(ROOT, f)), "нет файла " + f);
  }
});

test("manifest: иконки на месте", () => {
  for (const size of ["16", "48", "128"]) {
    const rel = manifest.icons[size];
    assert.ok(rel, "нет иконки " + size);
    assert.ok(fs.existsSync(path.join(ROOT, rel)), "нет файла иконки " + rel);
  }
});

test("manifest: разрешения и хосты на месте", () => {
  assert.ok(manifest.permissions.includes("storage"), "нужно storage");
  assert.ok(manifest.permissions.includes("alarms"), "нужен alarms");
  assert.ok(manifest.permissions.includes("notifications"), "нужны notifications");
  assert.ok(manifest.host_permissions.includes("https://open.bigmodel.cn/*"));
  assert.ok(manifest.host_permissions.includes("https://api.z.ai/*"));
  assert.ok(manifest.host_permissions.includes("https://raw.githubusercontent.com/*"),
    "нужен доступ к raw.githubusercontent.com для проверки обновлений");
});
