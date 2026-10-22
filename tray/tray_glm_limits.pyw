"""
GLM Limits — трей-компаньон (Windows).

Показывает остаток квот GLM Coding Plan в системном трее:
цветная иконка с процентом, tooltip с деталями и временем сброса,
окно с подробной информацией по левому клику, меню с данными,
уведомления при 80/90% сессии. Общается с тем же API, что и
браузерное расширение (raw API-ключ, без Bearer).

Меню трея:
  - «Показать детали» (или левый клик по иконке) — окно с метриками;
  - «Обновить данные» — внеочередной опрос;
  - «Обновить расширение» — git pull репозитория; расширение заметит
    новую версию манифеста на диске и перезапустится само;
  - «Автозапуск при входе в Windows» — тумблер (ветка Run в HKCU).

Конфиг: tray_config.json рядом со скриптом (создаётся при первом
запуске; ключ можно задать и через переменные окружения
GLM_API_KEY / ZAI_API_KEY / BIGMODEL_API_KEY / ZHIPUAI_API_KEY).
Конфиг перечитывается на каждом опросе — правки подхватываются сами.

Запуск:
    python tray_glm_limits.pyw             # трей-иконка, без консоли
    python tray_glm_limits.pyw --selftest  # проверка без запуска трея
"""
from __future__ import annotations

import base64
import json
import logging
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

APP_NAME = "GLM Limits"
HOSTS = {"cn": "https://open.bigmodel.cn", "intl": "https://api.z.ai"}
QUOTA_PATH = "/api/monitor/usage/quota/limit"
PLATFORMS = ("auto", "cn", "intl")
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

# Пороги расхода — те же, что в расширении.
COLOR_GREEN, COLOR_YELLOW, COLOR_AMBER, COLOR_RED, COLOR_GRAY = (
    "#5a9367", "#c2a14a", "#bf7a40", "#b5524c", "#8b8a84",
)

BASE_DIR = Path(__file__).resolve().parent
REPO_DIR = BASE_DIR.parent          # корень репозитория (для git pull)
CONFIG_PATH = BASE_DIR / "tray_config.json"
LOG_PATH = BASE_DIR / "tray_app.log"
STATE_PATH = BASE_DIR / "tray_state.json"   # флаги уведомлений (в git не попадает)

# Третьесторонние зависимости импортируются под защитой: .pyw работает без
# консоли, и падение на импорте выглядело бы как «не запускается» — молча.
# Причина пишется в лог, чтобы её можно было прочитать.
_IMPORT_ERROR: Exception | None = None
try:
    import pystray
    import requests
    from PIL import Image, ImageDraw, ImageFont
except Exception as exc:  # noqa: BLE001 — ловим всё, чтобы записать в лог
    _IMPORT_ERROR = exc
    pystray = None  # type: ignore[assignment]
    requests = None  # type: ignore[assignment]
    Image = ImageDraw = ImageFont = None  # type: ignore[assignment]
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now().isoformat(timespec='seconds')} "
                     f"CRITICAL ошибка импорта зависимостей: {exc}\n")
    except OSError:
        pass

# Пороги уведомлений о расходе сессии (по возрастанию) — по одному уведомлению
# на порог в пределах одного окна сброса.
NOTIFY_THRESHOLDS = (80, 90, 100)

# Backoff при ошибках API: максимальный интервал между опросами.
BACKOFF_MAX_SEC = 1800

logger = logging.getLogger("glm-limits.tray")

STRINGS = {
    "ru": {
        "session": "Сессия (5ч)", "weekly": "Неделя",
        "left": "осталось", "used": "израсходовано",
        "no_data": "Нет данных", "error": "Ошибка",
        "reset": "сброс через", "refresh": "Обновить данные",
        "details": "Показать детали", "exit": "Выход",
        "upd_ext": "Обновить расширение",
        "autostart": "Автозапуск при входе в Windows",
        "upd_done": "Расширение обновлено. Само перезапустится за пару минут — или кликни по его иконке.",
        "upd_none": "Уже последняя версия.",
        "upd_fail": "Ошибка обновления: {e}",
        "auto_on": "Автозапуск включён", "auto_off": "Автозапуск выключен",
        "restart": "Перезапустить", "restarting": "Перезапускаюсь…",
        "tray_restarting": "Обновлено — перезапускаю трей…",
        "restart_fail": "Не удалось перезапуститься — сделай это вручную",
        "key_menu": "API-ключ…",
        "key_prompt": "Новый API-ключ (ввод скрыт):",
        "key_current_set": "Сейчас: задан (••••{tail})",
        "key_current_none": "Сейчас: не задан",
        "key_saved": "Ключ сохранён — опрашиваю с новым ключом",
        "key_empty": "Поле пустое — ключ остался прежним",
        "save_btn": "Сохранить", "cancel_btn": "Отмена",
        "notify": "Сессия израсходована на {u}%. Осталось {l}%.",
        "no_key": "Ключ не задан (tray_config.json)",
        "updated_at": "Обновлено",
    },
    "en": {
        "session": "Session (5h)", "weekly": "Weekly",
        "left": "left", "used": "used",
        "no_data": "No data", "error": "Error",
        "reset": "resets in", "refresh": "Refresh data",
        "details": "Show details", "exit": "Exit",
        "upd_ext": "Update extension",
        "autostart": "Start with Windows",
        "upd_done": "Extension updated. It will reload itself within a couple of minutes — or click its icon.",
        "upd_none": "Already up to date.",
        "upd_fail": "Update failed: {e}",
        "auto_on": "Autostart enabled", "auto_off": "Autostart disabled",
        "restart": "Restart", "restarting": "Restarting…",
        "tray_restarting": "Updated — restarting the tray…",
        "restart_fail": "Couldn't restart itself — please do it manually",
        "key_menu": "API key…",
        "key_prompt": "New API key (input hidden):",
        "key_current_set": "Current: set (••••{tail})",
        "key_current_none": "Current: not set",
        "key_saved": "Key saved — polling with the new key",
        "key_empty": "Empty field — key unchanged",
        "save_btn": "Save", "cancel_btn": "Cancel",
        "notify": "Session is {u}% used. {l}% left.",
        "no_key": "API key is not set (tray_config.json)",
        "updated_at": "Updated",
    },
}


def setup_logging() -> None:
    """Файл + stderr; секреты (ключ) в логи не пишутся никогда."""
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    try:
        fh = logging.FileHandler(LOG_PATH, encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except OSError:
        pass  # лог-файл недоступен — работаем только с stderr
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    logger.addHandler(sh)


def protect_secret(plain: bytes) -> bytes | None:
    """Шифрует секрет средствами Windows (DPAPI): расшифровать сможет только
    текущий пользователь этой машины. None — если DPAPI недоступен."""
    if not plain:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD),
                        ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        crypt32.CryptProtectData.argtypes = [
            ctypes.POINTER(DATA_BLOB), wintypes.LPCWSTR, ctypes.POINTER(DATA_BLOB),
            ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DATA_BLOB)]
        crypt32.CryptProtectData.restype = wintypes.BOOL
        kernel32.LocalFree.argtypes = [wintypes.HLOCAL]

        buf = ctypes.create_string_buffer(plain, len(plain))
        in_blob = DATA_BLOB(len(plain), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
        out_blob = DATA_BLOB()
        if not crypt32.CryptProtectData(ctypes.byref(in_blob), "GLM Limits", None, None,
                                        None, 0x01, ctypes.byref(out_blob)):
            logger.error("CryptProtectData: ошибка %s", ctypes.get_last_error())
            return None
        try:
            return ctypes.string_at(out_blob.pbData, out_blob.cbData)
        finally:
            kernel32.LocalFree(out_blob.pbData)
    except Exception:
        logger.exception("DPAPI: не удалось зашифровать")
        return None


def unprotect_secret(blob: bytes) -> bytes | None:
    """Расшифровка секрета, защищённого DPAPI (тот же пользователь Windows)."""
    if not blob:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD),
                        ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        crypt32.CryptUnprotectData.argtypes = [
            ctypes.POINTER(DATA_BLOB), ctypes.POINTER(wintypes.LPWSTR),
            ctypes.POINTER(DATA_BLOB), ctypes.c_void_p, ctypes.c_void_p,
            wintypes.DWORD, ctypes.POINTER(DATA_BLOB)]
        crypt32.CryptUnprotectData.restype = wintypes.BOOL
        kernel32.LocalFree.argtypes = [wintypes.HLOCAL]

        buf = ctypes.create_string_buffer(blob, len(blob))
        in_blob = DATA_BLOB(len(blob), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
        out_blob = DATA_BLOB()
        if not crypt32.CryptUnprotectData(ctypes.byref(in_blob), None, None, None, None,
                                          0x01, ctypes.byref(out_blob)):
            logger.error("CryptUnprotectData: ошибка %s", ctypes.get_last_error())
            return None
        try:
            return ctypes.string_at(out_blob.pbData, out_blob.cbData)
        finally:
            kernel32.LocalFree(out_blob.pbData)
    except Exception:
        logger.exception("DPAPI: не удалось расшифровать")
        return None


def _read_config_raw() -> dict[str, Any]:
    """Читает конфиг как есть (без разбора ключа)."""
    if not CONFIG_PATH.exists():
        return {}
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        logger.exception("Не удалось прочитать конфиг %s", CONFIG_PATH)
        return {}


def write_protected_key(key: str) -> bool:
    """Сохраняет ключ в конфиг в зашифрованном (DPAPI) виде."""
    blob = protect_secret(key.encode("utf-8"))
    if not blob:
        return False
    data = _read_config_raw()
    data.pop("apiKey", None)
    data["apiKeyProtected"] = base64.b64encode(blob).decode("ascii")
    try:
        CONFIG_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return True
    except OSError:
        logger.exception("Не удалось записать конфиг")
        return False


def detect_platform(key: str) -> str:
    """Эвристика платформы — та же, что в background.js расширения."""
    k = (key or "").strip()
    if not k:
        return "cn"
    if k.lower().startswith(("zai_", "zai-")):
        return "intl"
    if "." in k:
        return "cn"  # формат id.secret у bigmodel.cn
    return "intl"


def load_config() -> dict[str, Any]:
    """Читает tray_config.json. Ключ хранится зашифрованным (Windows DPAPI) —
    прочитать его может только текущий пользователь этой машины. Fallback:
    переменные окружения."""
    cfg = _read_config_raw()

    stored_key = ""
    if cfg.get("apiKeyProtected"):
        try:
            blob = base64.b64decode(cfg["apiKeyProtected"])
        except (ValueError, TypeError):
            logger.exception("Повреждён зашифрованный ключ в конфиге")
            blob = b""
        plain = unprotect_secret(blob)
        if plain:
            stored_key = plain.decode("utf-8", "replace")
        else:
            logger.error("Ключ не расшифровывается (нужен тот же пользователь Windows) — введите его заново")
    elif cfg.get("apiKey"):
        # Миграция со старого формата: сразу перешифровываем.
        stored_key = str(cfg["apiKey"]).strip()
        if stored_key and write_protected_key(stored_key):
            logger.info("Ключ мигрирован в зашифрованный формат (DPAPI)")

    api_key = str(
        stored_key
        or os.environ.get("GLM_API_KEY")
        or os.environ.get("ZAI_API_KEY")
        or os.environ.get("BIGMODEL_API_KEY")
        or os.environ.get("ZHIPUAI_API_KEY")
        or ""
    ).strip()
    platform = cfg.get("platform")
    try:
        interval = max(30, int(cfg.get("intervalSec") or 120))
    except (TypeError, ValueError):
        interval = 120
    return {
        "apiKey": api_key,
        "platform": platform if platform in PLATFORMS else "auto",
        "intervalSec": interval,
        "lang": "en" if str(cfg.get("lang") or "ru").lower() == "en" else "ru",
        "notify": cfg.get("notify") is not False,
    }


def fetch_quota(api_key: str, platform: str) -> dict[str, Any]:
    """Опрашивает endpoint квот; возвращает нормализованные окна s/w/mcp."""
    if platform == "auto":
        platform = detect_platform(api_key)
    url = HOSTS[platform] + QUOTA_PATH
    resp = requests.get(
        url,
        headers={"Authorization": api_key, "Accept": "application/json"},
        timeout=15,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"{url} -> HTTP {resp.status_code}")
    data = (resp.json() or {}).get("data") or {}
    limits = data.get("limits") or []

    def norm(item: dict[str, Any]) -> dict[str, Any]:
        used = min(max(float(item.get("percentage") or 0), 0.0), 100.0)
        reset_ms = int(item.get("nextResetTime") or 0)
        resets_at = (
            datetime.fromtimestamp(reset_ms / 1000, tz=timezone.utc).isoformat()
            if reset_ms > 0
            else None
        )
        return {"used": used, "resets_at": resets_at}

    s = w = mcp = None
    for item in limits:
        if not isinstance(item, dict):
            continue
        itype, unit, number = item.get("type"), item.get("unit"), item.get("number")
        if itype == "TIME_LIMIT":
            mcp = norm(item)
        elif itype in ("TOKENS_LIMIT", "CREDIT_LIMIT"):
            # CREDIT_LIMIT — кредитные окна bigmodel.cn, семантика та же.
            if unit == 3 and number == 5:
                s = norm(item)
            elif unit == 6 and number == 1:
                w = norm(item)
            elif s is None and unit == 3:
                s = norm(item)
            elif w is None and unit == 6:
                w = norm(item)
    return {"ok": True, "error": None, "platform": platform, "plan": data.get("level"),
            "s": s, "w": w, "mcp": mcp, "ts": time.time()}


def color_for(used: float) -> str:
    if used < 50:
        return COLOR_GREEN
    if used < 75:
        return COLOR_YELLOW
    if used < 90:
        return COLOR_AMBER
    return COLOR_RED


def render_icon(remaining: int | None, color: str) -> Image.Image:
    """64x64 RGBA: скруглённый квадрат с процентом остатка (масштабируется треем)."""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((2, 2, size - 2, size - 2), radius=16, fill=color)
    text = "—" if remaining is None else str(remaining)
    font_size = 32 if len(text) <= 2 else 22
    try:
        font: Any = ImageFont.truetype("seguisb.ttf", font_size)  # Segoe UI Semibold
    except OSError:
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), text, font=font)
    draw.text(
        ((size - (bbox[2] - bbox[0])) / 2 - bbox[0], (size - (bbox[3] - bbox[1])) / 2 - bbox[1]),
        text, font=font, fill="#ffffff",
    )
    return img


def fmt_countdown(iso: str | None, lang: str) -> str:
    if not iso:
        return "—"
    try:
        diff = datetime.fromisoformat(iso).timestamp() - time.time()
    except ValueError:
        return "—"
    if diff <= 0:
        return "0"
    total_min = int(diff) // 60
    h, m = total_min // 60, total_min % 60
    if lang == "ru":
        if h >= 24:
            return f"{h // 24}д {h % 24}ч"
        if h:
            return f"{h}ч {m}м"
        return f"{m}м"
    if h >= 24:
        return f"{h // 24}d {h % 24}h"
    if h:
        return f"{h}h {m}m"
    return f"{m}m"


def fmt_at_time(iso: str | None) -> str:
    """Локальное время сброса в формате HH:MM."""
    if not iso:
        return "—"
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%H:%M")
    except ValueError:
        return "—"


# ── Автозапуск (ветка Run в HKCU) ────────────────────────────
def _pythonw_path() -> str:
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    return str(pythonw) if pythonw.exists() else str(exe)


def autostart_command() -> str:
    return f'"{_pythonw_path()}" "{Path(__file__).resolve()}"'


def is_autostart_on() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, APP_NAME)
            return True
    except OSError:
        return False


def set_autostart(enable: bool) -> None:
    import winreg
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
        if enable:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, autostart_command())
            logger.info("Автозапуск включён: %s", autostart_command())
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
                logger.info("Автозапуск выключен")
            except FileNotFoundError:
                pass


def acquire_single_instance() -> bool:
    """True, если этот процесс — единственный экземпляр трея (Windows mutex).

    Без этого двойной клик по .pyw даёт две иконки и два параллельных опроса.
    """
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        handle = kernel32.CreateMutexW(None, False, "Global\\GLMLimitsTray")
        if not handle:
            return True
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            kernel32.CloseHandle(handle)
            return False
        # Хэндл намеренно не закрываем — он держится до конца процесса.
        return True
    except Exception:
        logger.exception("Не удалось проверить единственный экземпляр")
        return True


def load_notif_state() -> dict[str, Any]:
    """Флаги уведомлений переживают перезапуск трея (иначе 80% прилетит дважды)."""
    try:
        if STATE_PATH.exists():
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except (OSError, json.JSONDecodeError):
        logger.exception("Не удалось прочитать состояние уведомлений")
    return {}


def save_notif_state(state: dict[str, Any]) -> None:
    try:
        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except OSError:
        logger.exception("Не удалось сохранить состояние уведомлений")


def read_repo_version() -> str:
    """Версия расширения из манифеста репозитория (для окна деталей)."""
    try:
        manifest = json.loads((REPO_DIR / "manifest.json").read_text(encoding="utf-8"))
        return str(manifest.get("version") or "?")
    except (OSError, json.JSONDecodeError):
        return "?"


def repo_update_time() -> str:
    """Дата/время последнего обновления кода — mtime манифеста на диске."""
    try:
        mtime = (REPO_DIR / "manifest.json").stat().st_mtime
        return datetime.fromtimestamp(mtime).strftime("%d.%m.%Y %H:%M")
    except OSError:
        return ""


class State:
    """Общее состояние между потоком опроса и иконкой/меню/окном."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[str, Any] = {"ok": False, "error": None}
        self.notif: dict[str, Any] = {"win": None, "s80": False, "s90": False, "s100": False}

    def set(self, data: dict[str, Any]) -> None:
        with self._lock:
            self._data = data

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._data)


class TrayApp:
    def __init__(self) -> None:
        self.state = State()
        self.wake = threading.Event()
        self.stop_evt = threading.Event()
        self.icon: pystray.Icon | None = None
        self._detail_win: Any = None
        self._key_win: Any = None
        self.current_cfg: dict[str, Any] = load_config()
        # Флаги уведомлений живут на диске: перезапуск трея не должен приводить
        # к повторному «80%» в том же окне сброса.
        self.state.notif.update(load_notif_state())

    def tr(self, key: str) -> str:
        return STRINGS[self.current_cfg["lang"]][key]

    # ── строки меню ──────────────────────────────────────────
    def _window_line(self, kind: str) -> str:
        d = self.state.snapshot()
        label = self.tr("session") if kind == "s" else self.tr("weekly")
        win = d.get(kind) if d.get("ok") else None
        if not win:
            return f"{label}: —"
        return f"{label}: {100 - int(win['used'])}% {self.tr('left')}"

    def line_session(self, *args: Any) -> str:
        return self._window_line("s")

    def line_weekly(self, *args: Any) -> str:
        return self._window_line("w")

    def line_reset(self, *args: Any) -> str:
        d = self.state.snapshot()
        s = d.get("s") if d.get("ok") else None
        if not s:
            return f"{self.tr('reset')}: —"
        return f"{self.tr('reset')} {fmt_countdown(s['resets_at'], self.current_cfg['lang'])}"

    def line_version(self, *args: Any) -> str:
        ver = read_repo_version()
        upd = repo_update_time()
        return f"{APP_NAME} v{ver}" + (f" · {self.tr('updated_at')} {upd}" if upd else "")

    # ── tooltip / иконка ─────────────────────────────────────
    def tooltip(self) -> str:
        d = self.state.snapshot()
        lang = self.current_cfg["lang"]
        if not d.get("ok"):
            err = str(d.get("error") or self.tr("no_data"))
            return f"{APP_NAME} — {self.tr('error')}: {err}"[:127]
        parts = []
        s, w = d.get("s"), d.get("w")
        if s:
            parts.append(f"{100 - int(s['used'])}% {self.tr('left')}, "
                         f"{self.tr('reset')} {fmt_countdown(s['resets_at'], lang)}")
        if w:
            parts.append(f"{self.tr('weekly')}: {100 - int(w['used'])}%")
        if d.get("plan"):
            parts.append(str(d["plan"]))
        return (APP_NAME + " · " + " · ".join(parts))[:127]

    def refresh_icon(self) -> None:
        if not self.icon:
            return
        d = self.state.snapshot()
        win = d.get("s") or d.get("w") if d.get("ok") else None
        if win:
            used = float(win["used"])
            self.icon.icon = render_icon(100 - int(used), color_for(used))
        else:
            self.icon.icon = render_icon(None, COLOR_GRAY)
        self.icon.title = self.tooltip()
        self.icon.update_menu()

    # ── уведомления 80/90% ───────────────────────────────────
    def check_notifications(self, cfg: dict[str, Any], data: dict[str, Any]) -> None:
        """По одному уведомлению на порог (80/90/100) в пределах окна сброса."""
        s = data.get("s")
        if not cfg["notify"] or not s:
            return
        used = float(s["used"])
        win = s.get("resets_at")
        n = self.state.notif
        if n["win"] != win:
            n["win"] = win
            for th in NOTIFY_THRESHOLDS:
                n[f"s{th}"] = False
        for th in NOTIFY_THRESHOLDS:
            if used >= th and not n.get(f"s{th}"):
                # Порог пройден — нижние считаем пройденными тоже: одно
                # уведомление вместо каскада, без дублей.
                for lower in NOTIFY_THRESHOLDS:
                    if lower <= th:
                        n[f"s{lower}"] = True
                if self.icon:
                    msg = STRINGS[cfg["lang"]]["notify"].format(u=int(used), l=int(100 - used))
                    try:
                        self.icon.notify(msg, APP_NAME)
                    except Exception:
                        logger.exception("Не удалось показать уведомление")
                save_notif_state(n)
                break

    # ── поток опроса ─────────────────────────────────────────
    def poll_worker(self) -> None:
        streak = 0
        while not self.stop_evt.is_set():
            interval = 120
            try:
                cfg = load_config()  # перечитываем — правки конфига подхватываются сами
                interval = cfg["intervalSec"]
                self.current_cfg = cfg
                if not cfg["apiKey"]:
                    self.state.set({"ok": False, "error": self.tr("no_key"), "ts": time.time()})
                else:
                    try:
                        data = fetch_quota(cfg["apiKey"], cfg["platform"])
                        self.state.set(data)
                        self.check_notifications(cfg, data)
                        streak = 0
                        interval = cfg["intervalSec"]
                        logger.info(
                            "Опрос ок (platform=%s): сессия %s%%, неделя %s%%",
                            data.get("platform"),
                            int(data["s"]["used"]) if data.get("s") else -1,
                            int(data["w"]["used"]) if data.get("w") else -1,
                        )
                    except Exception as exc:
                        # Backoff: при серии ошибок отдаляем опрос (до 30 минут).
                        streak += 1
                        interval = min(
                            cfg["intervalSec"] * (2 ** min(streak - 1, 4)),
                            BACKOFF_MAX_SEC,
                        )
                        logger.warning("Ошибка опроса (подряд %s), следующий через %sс: %s",
                                       streak, interval, exc)
                        self.state.set({"ok": False, "error": str(exc)[:200], "ts": time.time()})
                self.refresh_icon()
            except Exception:
                # Поток опроса не должен умирать: иначе иконка застынет навсегда.
                logger.exception("Неожиданная ошибка в цикле опроса — продолжаем")
            self.wake.wait(interval)
            self.wake.clear()

    # ── действия меню ────────────────────────────────────────
    def on_refresh(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        self.wake.set()

    def on_exit(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        logger.info("Выход по команде пользователя")
        self.stop_evt.set()
        self.wake.set()
        icon.stop()

    def toggle_autostart(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        enable = not is_autostart_on()
        try:
            set_autostart(enable)
            if icon:
                icon.notify(self.tr("auto_on") if enable else self.tr("auto_off"), APP_NAME)
        except Exception as exc:
            logger.exception("Не удалось изменить автозапуск")
            if icon:
                try:
                    icon.notify(f"{self.tr('error')}: {exc}", APP_NAME)
                except Exception:
                    pass

    def update_extension(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        """git pull в фоновом потоке: блокировка на минуту недопустима —
        pystray крутит меню в этом же потоке."""
        threading.Thread(target=self._update_extension_worker, name="git-pull",
                         daemon=True).start()

    def _update_extension_worker(self) -> None:
        lang = self.current_cfg["lang"]
        try:
            before = self._git_head()
            proc = subprocess.run(
                ["git", "-C", str(REPO_DIR), "pull", "--ff-only", "origin", "main"],
                capture_output=True, text=True, timeout=120,
                encoding="utf-8", errors="replace",
            )
            out = (proc.stdout or "") + (proc.stderr or "")
            logger.info("git pull: %s", out.strip()[:300])
            if proc.returncode != 0:
                raise RuntimeError((out.strip() or "git failed")[:120])
            after = self._git_head()
            # Сравниваем SHA, а не текст вывода: текст git локализован.
            if not after or before == after:
                self._notify_tray(self.tr("upd_none"))
                return
            if self._git_touched_tray(before, after):
                # Обновились файлы самого трея — текущий процесс со старым
                # кодом: перезапускаем себя, чтобы новый код заработал сразу.
                self.restart_self(self.tr("tray_restarting"))
            else:
                self._notify_tray(self.tr("upd_done"))
        except Exception as exc:
            logger.exception("Обновление расширения не удалось")
            self._notify_tray(STRINGS[lang]["upd_fail"].format(e=str(exc)[:80]))

    @staticmethod
    def _git_head() -> str | None:
        try:
            proc = subprocess.run(
                ["git", "-C", str(REPO_DIR), "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=15,
                encoding="utf-8", errors="replace",
            )
            return proc.stdout.strip() if proc.returncode == 0 else None
        except Exception:
            logger.exception("git rev-parse не удался")
            return None

    @staticmethod
    def _git_touched_tray(before: str | None, after: str | None) -> bool:
        """Изменились ли файлы трея между двумя коммитами."""
        if not before or not after:
            return False
        try:
            proc = subprocess.run(
                ["git", "-C", str(REPO_DIR), "diff", "--name-only", before, after],
                capture_output=True, text=True, timeout=15,
                encoding="utf-8", errors="replace",
            )
            if proc.returncode != 0:
                return False
            return any(line.strip().startswith("tray/")
                       for line in proc.stdout.splitlines())
        except Exception:
            logger.exception("git diff не удался")
            return False

    def _notify_tray(self, text: str) -> None:
        if self.icon:
            try:
                self.icon.notify(text, APP_NAME)
            except Exception:
                logger.exception("Не удалось показать уведомление")

    def open_key_dialog(self, *args: Any) -> None:
        """Ввод API-ключа мышкой: tkinter-диалог с маскировкой звёздочками."""
        threading.Thread(target=self._key_dialog_main, name="key-dialog", daemon=True).start()

    def _key_dialog_main(self) -> None:
        import tkinter as tk

        lang = self.current_cfg["lang"]
        s = STRINGS[lang]
        current = load_config()["apiKey"]

        win = tk.Tk()
        win.title(APP_NAME)
        win.geometry("340x200")
        win.resizable(False, False)
        win.attributes("-topmost", True)
        win.configure(bg="#17181a")
        self._key_win = win

        def on_close() -> None:
            self._key_win = None
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", on_close)

        tk.Label(win, text=s["key_prompt"], font=("Segoe UI", 10),
                 fg="#e8e7e3", bg="#17181a").pack(anchor="w", padx=16, pady=(14, 4))
        entry = tk.Entry(win, show="*", font=("Segoe UI", 11), bg="#1f2124",
                         fg="#e8e7e3", insertbackground="#e8e7e3", relief="flat")
        entry.pack(fill="x", padx=16, ipady=6)
        entry.focus_set()

        if current:
            tail = current[-4:] if len(current) >= 4 else current
            status_text = s["key_current_set"].format(tail=tail)
        else:
            status_text = s["key_current_none"]
        tk.Label(win, text=status_text, font=("Segoe UI", 9),
                 fg="#8f9094", bg="#17181a").pack(anchor="w", padx=16, pady=(5, 0))

        def save() -> None:
            new_key = entry.get().strip()
            if not new_key:
                self._notify_tray(s["key_empty"])
                on_close()
                return
            if not write_protected_key(new_key):
                logger.error("Не удалось сохранить ключ в зашифрованном виде")
                self._notify_tray(f"{s['error']}: {CONFIG_PATH.name}")
                on_close()
                return
            logger.info("API-ключ обновлён через диалог трея (зашифрован, DPAPI)")
            self._notify_tray(s["key_saved"])
            self.wake.set()  # сразу опросить с новым ключом
            on_close()

        row = tk.Frame(win, bg="#17181a")
        row.pack(pady=(14, 8))
        tk.Button(row, text=s["save_btn"], command=save, bg="#1f2124", fg="#e8e7e3",
                  activebackground="#2c2e32", activeforeground="#e8e7e3",
                  relief="flat", padx=14, pady=3, cursor="hand2").pack(side="left", padx=6)
        tk.Button(row, text=s["cancel_btn"], command=on_close, bg="#1f2124", fg="#8f9094",
                  activebackground="#2c2e32", activeforeground="#e8e7e3",
                  relief="flat", padx=14, pady=3, cursor="hand2").pack(side="left", padx=6)
        win.bind("<Return>", lambda event: save())
        win.bind("<Escape>", lambda event: on_close())
        win.mainloop()

    def restart_self(self, notify_text: str | None = None) -> None:
        """Запускает новую копию трей-приложения и завершает текущую."""
        def worker() -> None:
            if notify_text:
                self._notify_tray(notify_text)
                time.sleep(2.0)  # дать уведомлению показаться
            try:
                flags = (getattr(subprocess, "DETACHED_PROCESS", 0)
                         | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = 0  # SW_HIDE — без мигающего окна консоли
                # Запускаем через pythonw: даже если трей был стартован из
                # консоли, новая копия не должна открывать окно консоли.
                subprocess.Popen(
                    [_pythonw_path(), str(Path(__file__).resolve())],
                    creationflags=flags, startupinfo=startupinfo, close_fds=True,
                )
                logger.info("Новый процесс трея запущен, текущий завершается")
            except Exception:
                logger.exception("Не удалось запустить новую копию")
                self._notify_tray(self.tr("restart_fail"))
                return
            self.stop_evt.set()
            self.wake.set()
            if self.icon:
                self.icon.stop()

        threading.Thread(target=worker, name="restart", daemon=True).start()

    def on_restart(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        self.restart_self(self.tr("restarting"))

    def build_menu(self) -> pystray.Menu:
        return pystray.Menu(
            pystray.MenuItem(self.line_version, None, enabled=False),
            pystray.MenuItem(self.line_session, None, enabled=False),
            pystray.MenuItem(self.line_weekly, None, enabled=False),
            pystray.MenuItem(self.line_reset, None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(lambda item: self.tr("details"), self.open_details, default=True),
            pystray.MenuItem(lambda item: self.tr("refresh"), self.on_refresh),
            pystray.MenuItem(lambda item: self.tr("upd_ext"), self.update_extension),
            pystray.MenuItem(lambda item: self.tr("key_menu"), self.open_key_dialog),
            pystray.MenuItem(
                lambda item: self.tr("autostart"), self.toggle_autostart,
                checked=lambda item: is_autostart_on(), radio=False,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(lambda item: self.tr("restart"), self.on_restart),
            pystray.MenuItem(lambda item: self.tr("exit"), self.on_exit),
        )

    # ── окно деталей (tkinter, свой поток) ───────────────────
    def open_details(self, *args: Any) -> None:
        win = self._detail_win
        if win is not None:
            try:
                win.deiconify()
                win.lift()
                win.focus_force()
                return
            except Exception:
                self._detail_win = None
        threading.Thread(target=self._details_main, name="details", daemon=True).start()

    def _details_main(self) -> None:
        import tkinter as tk

        c = {"bg": "#17181a", "card": "#1f2124", "text": "#e8e7e3",
             "muted": "#8f9094", "border": "#2c2e32", "accent": "#7ea6c7"}
        lang = self.current_cfg["lang"]
        ext_version = read_repo_version()
        upd_time = repo_update_time()

        win = tk.Tk()
        win.title(f"{APP_NAME} v{ext_version}")
        win.geometry("330x490")
        win.resizable(False, False)
        win.attributes("-topmost", True)
        win.configure(bg=c["bg"])
        self._detail_win = win

        def on_close() -> None:
            self._detail_win = None
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", on_close)

        tk.Label(win, text=f"{APP_NAME}  v{ext_version}", font=("Segoe UI", 13, "bold"),
                 fg=c["text"], bg=c["bg"]).pack(anchor="w", padx=16, pady=(12, 0))
        upd_lbl = tk.Label(win, text="", font=("Segoe UI", 8), fg=c["muted"], bg=c["bg"])
        upd_lbl.pack(anchor="w", padx=16)
        if upd_time:
            upd_lbl.config(text=f"{self.tr('updated_at')}: {upd_time}")
        plan_lbl = tk.Label(win, text="", font=("Segoe UI", 9), fg=c["accent"], bg=c["bg"])
        plan_lbl.pack(anchor="w", padx=16, pady=(0, 4))

        blocks: dict[str, dict[str, Any]] = {}

        def make_block(key: str, title: str) -> None:
            wrap = tk.Frame(win, bg=c["card"], highlightthickness=1,
                            highlightbackground=c["border"])
            wrap.pack(fill="x", padx=16, pady=6)
            tk.Label(wrap, text=title.upper(), font=("Segoe UI", 8),
                     fg=c["muted"], bg=c["card"]).pack(anchor="w", padx=12, pady=(8, 0))
            val = tk.Label(wrap, text="—", font=("Segoe UI", 24, "bold"),
                           fg=c["text"], bg=c["card"])
            val.pack(anchor="w", padx=12)
            canvas = tk.Canvas(wrap, height=8, bg=c["card"], highlightthickness=0)
            canvas.pack(fill="x", padx=12, pady=(4, 0))
            meta = tk.Label(wrap, text="", font=("Segoe UI", 9),
                            fg=c["muted"], bg=c["card"], wraplength=270, justify="left")
            meta.pack(anchor="w", padx=12, pady=(2, 8))
            blocks[key] = {"wrap": wrap, "val": val, "canvas": canvas, "meta": meta}

        make_block("s", self.tr("session"))
        make_block("w", self.tr("weekly"))

        foot = tk.Label(win, text="", font=("Segoe UI", 9), fg=c["muted"],
                        bg=c["bg"], wraplength=290, justify="left")
        foot.pack(anchor="w", padx=16, pady=(4, 0))

        tk.Button(win, text=self.tr("refresh"), command=self.wake.set,
                  bg=c["card"], fg=c["text"], activebackground=c["border"],
                  activeforeground=c["text"], relief="flat", padx=14, pady=4,
                  cursor="hand2").pack(pady=(6, 12))

        def draw_bar(canvas: Any, used: float, color: str) -> None:
            canvas.delete("all")
            width = max(canvas.winfo_width(), 100)
            canvas.create_rectangle(0, 0, width, 8, fill=c["border"], width=0)
            canvas.create_rectangle(0, 0, int(width * used / 100), 8, fill=color, width=0)

        def tick() -> None:
            if self._detail_win is not win:
                return
            try:
                if not win.winfo_exists():
                    return
            except Exception:
                return
            d = self.state.snapshot()
            ok = bool(d.get("ok"))
            plan_text = str(d.get("plan") or "")
            plan_lbl.config(text=f"{'Тариф' if lang == 'ru' else 'Plan'}: {plan_text}"
                            if plan_text else "")
            for key in ("s", "w"):
                b = blocks[key]
                win_d = d.get(key) if ok else None
                if not win_d:
                    b["val"].config(text="—", fg=c["muted"])
                    b["meta"].config(text=str(d.get("error") or self.tr("no_data"))[:80])
                    b["canvas"].delete("all")
                    continue
                used = float(win_d["used"])
                b["val"].config(text=f"{100 - int(used)}%", fg=color_for(used))
                b["meta"].config(text=(
                    f"{self.tr('used')}: {int(used)}% · "
                    f"{self.tr('reset')} {fmt_countdown(win_d['resets_at'], lang)} "
                    f"({fmt_at_time(win_d['resets_at'])})"
                ))
                draw_bar(b["canvas"], used, color_for(used))
            if ok:
                mcp = d.get("mcp")
                extra = f" · MCP: {100 - int(mcp['used'])}% {self.tr('left')}" if mcp else ""
                ts = d.get("ts") or time.time()
                host = HOSTS.get(d.get("platform"), "")
                foot.config(text=f"{self.tr('updated_at')}: "
                                 f"{time.strftime('%H:%M:%S', time.localtime(ts))} · {host}{extra}")
            else:
                foot.config(text=str(d.get("error") or "")[:90])
            win.after(1000, tick)

        tick()
        try:
            win.mainloop()
        except Exception:
            logger.exception("Окно деталей завершилось с ошибкой")
        finally:
            self._detail_win = None

    def run(self) -> None:
        worker = threading.Thread(target=self.poll_worker, name="poll", daemon=True)
        worker.start()
        self.icon = pystray.Icon(
            APP_NAME, render_icon(None, COLOR_GRAY), APP_NAME, menu=self.build_menu()
        )
        logger.info("Трей-иконка запущена")
        self.icon.run()


def selftest() -> int:
    """Проверка окружения без запуска трея: рендер, конфиг, (если ключ есть) API."""
    if _IMPORT_ERROR is not None:
        print(f"IMPORT ERROR: {_IMPORT_ERROR}")
        return 1
    for args in ((None, COLOR_GRAY), (62, COLOR_GREEN), (5, COLOR_YELLOW), (100, COLOR_RED)):
        img = render_icon(*args)
        assert img.size == (64, 64), f"icon size {img.size}"
    cfg = load_config()
    logger.info("Selftest: конфиг — ключ %s, платформа %s, интервал %sс",
                "задан" if cfg["apiKey"] else "НЕ задан", cfg["platform"], cfg["intervalSec"])
    logger.info("Selftest: автозапуск %s, версия расширения в репо %s",
                "вкл" if is_autostart_on() else "выкл", read_repo_version())
    if cfg["apiKey"]:
        data = fetch_quota(cfg["apiKey"], cfg["platform"])
        windows = [k for k in ("s", "w", "mcp") if data.get(k)]
        logger.info("Selftest: API ок — план %s, окон %s", data.get("plan"), windows)
    print("Selftest OK")
    return 0


def main(argv: list[str]) -> int:
    setup_logging()
    if _IMPORT_ERROR is not None:
        msg = (f"Не хватает зависимостей: {_IMPORT_ERROR}. "
               "Установи их: pip install -r tray/requirements.txt (или запусти tray_install.bat)")
        logger.error(msg)
        print(msg)
        return 1
    if "--selftest" in argv:
        return selftest()

    cfg = load_config()
    if not cfg["apiKey"] and not CONFIG_PATH.exists():
        template = {"apiKey": "", "platform": "auto", "intervalSec": 120, "lang": "ru", "notify": True}
        CONFIG_PATH.write_text(
            json.dumps(template, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        msg = f"Создан шаблон конфига: {CONFIG_PATH}. Впишите apiKey и запустите снова."
        logger.warning(msg)
        print(msg)
        return 1
    if not cfg["apiKey"]:
        msg = "apiKey не задан: впишите в tray_config.json или задайте GLM_API_KEY."
        logger.error(msg)
        print(msg)
        return 1

    if not acquire_single_instance():
        msg = "GLM Limits уже запущен — второй экземпляр закрыт."
        logger.info(msg)
        print(msg)
        return 0

    TrayApp().run()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
