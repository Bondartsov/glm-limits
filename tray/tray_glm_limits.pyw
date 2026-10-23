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
import faulthandler
import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

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
STATE_PATH = BASE_DIR / "tray_state.json"
CRASH_PATH = BASE_DIR / "tray_crash.log"   # флаги уведомлений (в git не попадает)

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
_CRASH_FILE = None

STRINGS = {
    "ru": {
        "session": "Сессия (5ч)", "weekly": "Неделя", "mcp": "MCP",
        "left": "осталось", "used": "израсходовано",
        "value_mode_remaining": "Показывать: остаток",
        "value_mode_used": "Показывать: израсходовано",
        "no_data": "Нет данных", "error": "Ошибка",
        "reset": "сброс через", "refresh": "Обновить данные",
        "details": "Показать детали", "exit": "Выход",
        "upd_ext": "Обновить расширение",
        "auto_update": "Автообновление",
        "upd_available": "Доступна версия {v}",
        "upd_no_available": "Обновлений нет",
        "upd_done_ver": "Обновлено до версии {v}",
        "upd_incomplete": "Обновление неполное — файлы расширения повреждены, перезапуск отменён",
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
        "session": "Session (5h)", "weekly": "Weekly", "mcp": "MCP",
        "left": "left", "used": "used",
        "value_mode_remaining": "Show: remaining",
        "value_mode_used": "Show: used",
        "no_data": "No data", "error": "Error",
        "reset": "resets in", "refresh": "Refresh data",
        "details": "Show details", "exit": "Exit",
        "upd_ext": "Update extension",
        "auto_update": "Auto-update",
        "upd_available": "Version {v} available",
        "upd_no_available": "Up to date",
        "upd_done_ver": "Updated to version {v}",
        "upd_incomplete": "Update incomplete — extension files are damaged, restart skipped",
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
    # Жёсткие падения (access violation и т.п.) — тоже в файл, иначе процесс
    # исчезает без следа.
    try:
        global _CRASH_FILE
        _CRASH_FILE = open(CRASH_PATH, "a", encoding="utf-8")  # noqa: SIM115
        faulthandler.enable(_CRASH_FILE, all_threads=True)
    except Exception:
        pass


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


def enable_dpi_awareness() -> None:
    """Делает процесс DPI-aware, иначе Windows растягивает окно как картинку
    (шрифты и отступы «плывут» на мониторах с масштабированием 125–150%)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)   # SYSTEM_DPI_AWARE
        logger.info("DPI awareness включён")
    except Exception:
        logger.info("DPI awareness недоступен (старая Windows)")


def set_value_mode(mode: str) -> bool:
    """Сохраняет режим показа чисел в конфиг (ключ остаётся зашифрованным)."""
    data = _read_config_raw()
    data["valueMode"] = "used" if mode == "used" else "remaining"
    try:
        CONFIG_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return True
    except OSError:
        logger.exception("Не удалось записать режим показа")
        return False


def parse_model_usage(data: dict[str, Any] | None) -> dict[str, Any] | None:
    """Разбор ответа /api/monitor/usage/model-usage: доли по моделям."""
    if not isinstance(data, dict):
        return None
    models: list[dict[str, Any]] = []
    total = 0
    for item in data.get("modelDataList") or []:
        if not isinstance(item, dict):
            continue
        try:
            tokens = int(item.get("totalTokens") or 0)
        except (TypeError, ValueError):
            continue
        if tokens <= 0:
            continue
        models.append({"name": str(item.get("modelName") or "?"), "tokens": tokens})
        total += tokens
    if not models:
        return None
    models.sort(key=lambda m: -m["tokens"])
    for m in models:
        m["share"] = (m["tokens"] / total) if total else 0.0
    return {"models": models, "total": total}


def set_auto_update(enabled: bool) -> bool:
    """Сохраняет настройку автообновления (ключ API не трогается)."""
    data = _read_config_raw()
    data["autoUpdate"] = bool(enabled)
    try:
        CONFIG_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return True
    except OSError:
        logger.exception("Не удалось записать настройку автообновления")
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
        "valueMode": "used" if str(cfg.get("valueMode") or "").lower() == "used" else "remaining",
        "autoUpdate": cfg.get("autoUpdate") is True,
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


def fmt_reset_moment(iso: str | None, lang: str) -> str:
    """«сегодня в 16:56» / «завтра в 09:00» / «27.10 в 12:56» — понятная
    привязка сброса, а не голое время."""
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso).astimezone()
    except ValueError:
        return ""
    now = datetime.now()
    clock = dt.strftime("%H:%M")
    if dt.date() == now.date():
        return f"сегодня в {clock}" if lang == "ru" else f"today at {clock}"
    if dt.date() == (now + timedelta(days=1)).date():
        return f"завтра в {clock}" if lang == "ru" else f"tomorrow at {clock}"
    if lang == "ru":
        return f"{dt:%d.%m} в {clock}"
    return f"{dt:%d.%m}, {clock}"


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
            # Новый формат: {"s": {...}, "w": {...}}. Старый (плоский) отбрасываем.
            if isinstance(data, dict) and all(isinstance(v, dict) for v in data.values()):
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
        self.notif: dict[str, Any] = {"s": {}, "w": {}}

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
        self._remote_version: str | None = None
        self._history: list[tuple[float, float]] = []   # (ts, израсходовано %)
        self._model_usage: dict[str, Any] | None = None
        self._updating = False
        self._ui_queue: queue.Queue = queue.Queue()     # действия в потоке Tk
        self._root: Any = None
        self.current_cfg: dict[str, Any] = load_config()
        # Флаги уведомлений живут на диске: перезапуск трея не должен приводить
        # к повторному «80%» в том же окне сброса.
        self.state.notif.update(load_notif_state())

    def tr(self, key: str) -> str:
        # Отсутствующий ключ не должен ронять UI — показываем сам ключ.
        return STRINGS.get(self.current_cfg["lang"], {}).get(key)             or STRINGS.get("en", {}).get(key) or key

    # ── строки меню ──────────────────────────────────────────
    def _window_line(self, kind: str) -> str:
        d = self.state.snapshot()
        label = self.tr("session") if kind == "s" else self.tr("weekly")
        win = d.get(kind) if d.get("ok") else None
        if not win:
            return f"{label}: —"
        used = int(win["used"])
        return f"{label}: {int(round(self._display(used)))}% {self._display_word()}"

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

    def _display(self, used: float) -> float:
        """Число для показа: остаток или израсходовано — по настройке."""
        return used if self.current_cfg.get("valueMode") == "used" else 100 - used

    def _display_word(self) -> str:
        return self.tr("used") if self.current_cfg.get("valueMode") == "used" else self.tr("left")

    def on_toggle_auto_update(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        enabled = not bool(self.current_cfg.get("autoUpdate", False))
        if not set_auto_update(enabled):
            self._notify_tray(self.tr("error"))
            return
        self.current_cfg["autoUpdate"] = enabled
        logger.info("Автообновление: %s", "вкл" if enabled else "выкл")
        self._notify_tray(self.tr("auto_update") + ": " + ("вкл" if enabled else "выкл")
                          if self.current_cfg["lang"] == "ru"
                          else self.tr("auto_update") + ": " + ("on" if enabled else "off"))

    def on_toggle_value_mode(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        current = self.current_cfg.get("valueMode", "remaining")
        new_mode = "remaining" if current == "used" else "used"
        if not set_value_mode(new_mode):
            self._notify_tray(self.tr("error"))
            return
        self.current_cfg["valueMode"] = new_mode
        logger.info("Режим показа чисел: %s", new_mode)
        self.refresh_icon()
        self._notify_tray(self.tr("value_mode_" + new_mode))

    def refresh_icon(self) -> None:
        if not self.icon:
            return
        d = self.state.snapshot()
        win = d.get("s") or d.get("w") if d.get("ok") else None
        if win:
            used = float(win["used"])
            self.icon.icon = render_icon(int(round(self._display(used))), color_for(used))
        else:
            self.icon.icon = render_icon(None, COLOR_GRAY)
        self.icon.title = self.tooltip()
        self.icon.update_menu()

    # ── уведомления 80/90% ───────────────────────────────────
    def check_notifications(self, cfg: dict[str, Any], data: dict[str, Any]) -> None:
        """По одному уведомлению на порог (80/90/100) в пределах окна сброса —
        отдельно для сессии и для недели."""
        if not cfg["notify"]:
            return
        for kind, name_key in (("s", "session"), ("w", "weekly")):
            win_data = data.get(kind)
            if win_data:
                self._check_one(cfg, kind, name_key, win_data)
                self._check_reset(cfg, kind, name_key, win_data)

    def _check_one(self, cfg: dict[str, Any], kind: str, name_key: str,
                   win_data: dict[str, Any]) -> None:
        used = float(win_data["used"])
        win = win_data.get("resets_at")
        state = self.state.notif.setdefault(kind, {})
        if state.get("win") != win:
            state["win"] = win
            for th in NOTIFY_THRESHOLDS:
                state[f"t{th}"] = False
        for th in NOTIFY_THRESHOLDS:
            if used >= th and not state.get(f"t{th}"):
                # Порог пройден — нижние считаем пройденными: одно уведомление.
                for lower in NOTIFY_THRESHOLDS:
                    if lower <= th:
                        state[f"t{lower}"] = True
                if self.icon:
                    try:
                        self.icon.notify(
                            self._notify_text(cfg["lang"], name_key, used, win),
                            f"{APP_NAME} — {self.tr(name_key)} {th}%",
                        )
                    except Exception:
                        logger.exception("Не удалось показать уведомление")
                save_notif_state(self.state.notif)
                break

    def _check_reset(self, cfg: dict[str, Any], kind: str, name_key: str,
                     win_data: dict[str, Any]) -> None:
        """Уведомление о СБРОСЕ окна: старый resetsAt истёк, появился новый."""
        resets = self.state.notif.setdefault("reset", {})
        win = win_data.get("resets_at")
        if not win:
            return
        prev = resets.get(kind)
        if not prev:
            resets[kind] = {"win": win}
            save_notif_state(self.state.notif)
            return
        if prev.get("win") == win:
            return

        try:
            prev_ms = datetime.fromisoformat(prev["win"]).timestamp()
        except (ValueError, TypeError):
            prev_ms = 0
        resets[kind] = {"win": win}
        save_notif_state(self.state.notif)

        if not (prev_ms and prev_ms <= time.time()):
            return   # старое окно ещё не истекало
        if float(win_data.get("used") or 0) >= 50:
            return   # новое окно уже не свежее — это не сброс

        name = self.tr(name_key)
        cd = fmt_countdown(win, cfg["lang"])
        when = fmt_reset_moment(win, cfg["lang"])
        if cfg["lang"] == "ru":
            msg = f"{name} сброшена — лимит снова 100%. Следующий сброс через {cd} ({when})."
            title = f"{APP_NAME} — {self.tr(name_key)} сброшена"
        else:
            msg = f"{name} reset — limit is 100% again. Next reset in {cd} ({when})."
            title = f"{APP_NAME} — {self.tr(name_key)} reset"
        if self.icon:
            try:
                self.icon.notify(msg, title)
            except Exception:
                logger.exception("Не удалось показать уведомление о сбросе")

    def _notify_text(self, lang: str, name_key: str, used: float,
                     resets_at: str | None) -> str:
        """Текст уведомления: сколько израсходовано, сколько ОСТАЛОСЬ ЛИМИТА
        и когда сброс (время, для недели — дата)."""
        name = self.tr(name_key)
        left = max(0, int(round(100 - used)))
        cd = fmt_countdown(resets_at, lang)
        when = fmt_reset_moment(resets_at, lang)
        if lang == "ru":
            tail = f" Сброс через {cd} ({when})." if resets_at else ""
            return f"{name}: израсходовано {int(used)}%. Осталось {left}% лимита.{tail}"
        tail = f" Resets in {cd} ({when})." if resets_at else ""
        return f"{name}: {int(used)}% used. {left}% of the limit left.{tail}"

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
                        self._remember_usage(data)
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
        self._post_ui(self._shutdown_ui)

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
            # После pull проверяем целостность: если файлы дописались не все,
            # расширение нельзя перезапускать — оно станет «сломанным».
            if not self._validate_repo():
                logger.error("Обновление получилось неполным — перезапуск отменён")
                self._notify_tray(STRINGS[lang]["upd_incomplete"])
                return
            if self._git_touched_tray(before, after):
                # Обновились файлы самого трея — текущий процесс со старым
                # кодом: перезапускаем себя, чтобы новый код заработал сразу.
                self.restart_self(self.tr("tray_restarting"))
            else:
                self._notify_tray(STRINGS[lang]["upd_done_ver"].format(v=read_repo_version()))
        except Exception as exc:
            logger.exception("Обновление расширения не удалось")
            self._notify_tray(STRINGS[lang]["upd_fail"].format(e=str(exc)[:80]))

    @staticmethod
    def _validate_repo() -> bool:
        """После git pull проверяем, что расширение на диске целое: manifest
        читается и все ключевые файлы на месте. Иначе перезапуск сделает
        расширение сломанным (Chrome такие удаляет при следующем старте)."""
        try:
            manifest = json.loads((REPO_DIR / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.error("manifest.json не читается после обновления")
            return False
        if not manifest.get("version"):
            logger.error("В manifest.json нет версии")
            return False
        for f in ("background.js", "popup.js", "popup.html", "popup.css",
                  "quota.js", "keyring.js"):
            if not (REPO_DIR / f).exists():
                logger.error("После обновления отсутствует файл %s", f)
                return False
        return True

    def _start_model_watch(self) -> None:
        threading.Thread(target=self._model_worker, name="models", daemon=True).start()

    def _model_worker(self) -> None:
        """Разбивка по моделям меняется медленно — обновляем раз в 15 минут."""
        while not self.stop_evt.is_set():
            try:
                cfg = load_config()
                if cfg["apiKey"]:
                    self._model_usage = self._fetch_model_usage(cfg)
            except Exception:
                logger.exception("Не удалось получить расход по моделям")
            self.stop_evt.wait(900)

    @staticmethod
    def _fetch_model_usage(cfg: dict[str, Any]) -> dict[str, Any] | None:
        platform = cfg["platform"]
        if platform == "auto":
            platform = detect_platform(cfg["apiKey"])
        host = HOSTS.get(platform, HOSTS["cn"])
        today = datetime.now().strftime("%Y-%m-%d")
        url = (host + "/api/monitor/usage/model-usage"
               + "?startTime=" + quote(today + " 00:00:00")
               + "&endTime=" + quote(today + " 23:59:59"))
        resp = requests.get(url, headers={"Authorization": cfg["apiKey"],
                                          "Accept": "application/json"}, timeout=15)
        if resp.status_code != 200:
            logger.warning("model-usage: HTTP %s", resp.status_code)
            return None
        return parse_model_usage((resp.json() or {}).get("data"))

    def _start_update_watch(self) -> None:
        """Фоновый следящий: проверяет origin/main и держит метку
        «доступна версия X» — её видно в меню трея постоянно."""
        threading.Thread(target=self._update_watch_loop, name="update-watch",
                         daemon=True).start()

    def _update_watch_loop(self) -> None:
        while not self.stop_evt.is_set():
            try:
                self._remote_version = self._fetch_remote_version()
                if self.icon:
                    self.icon.update_menu()
                # Автообновление: новая версия в origin/main — тянем и
                # перезапускаемся сами (расширение подхватит само).
                if (self._remote_version and self.current_cfg.get("autoUpdate")
                        and not self._updating):
                    logger.info("Автообновление: найдена версия %s — обновляюсь",
                                self._remote_version)
                    self._updating = True
                    try:
                        self._update_extension_worker()
                    finally:
                        self._updating = False
            except Exception:
                logger.exception("Проверка обновлений не удалась")
            self.stop_evt.wait(600)

    @staticmethod
    def _fetch_remote_version() -> str | None:
        """Версия в origin/main, если она новее локальной."""
        try:
            subprocess.run(
                ["git", "-C", str(REPO_DIR), "fetch", "--quiet", "origin", "main"],
                capture_output=True, text=True, timeout=60,
                encoding="utf-8", errors="replace",
            )
            proc = subprocess.run(
                ["git", "-C", str(REPO_DIR), "show", "origin/main:manifest.json"],
                capture_output=True, text=True, timeout=15,
                encoding="utf-8", errors="replace",
            )
            if proc.returncode != 0:
                return None
            data = json.loads(proc.stdout)
            remote = str(data.get("version") or "")
            local = read_repo_version()
            return remote if remote and remote != local else None
        except Exception:
            logger.exception("Не удалось получить версию из репозитория")
            return None

    def line_update(self, *args: Any) -> str:
        v = getattr(self, "_remote_version", None)
        return self.tr("upd_available").format(v=v) if v else self.tr("upd_no_available")

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
        self._post_ui(self._key_dialog_main)

    def _key_dialog_main(self) -> None:
        import tkinter as tk

        lang = self.current_cfg["lang"]
        s = STRINGS[lang]
        current = load_config()["apiKey"]

        win = tk.Toplevel(self._root)
        win.title(APP_NAME)
        win.geometry("340x200")
        win.grab_set()
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
            pystray.MenuItem(self.line_update, None, enabled=False),
            pystray.MenuItem(self.line_session, None, enabled=False),
            pystray.MenuItem(self.line_weekly, None, enabled=False),
            pystray.MenuItem(self.line_reset, None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(lambda item: self.tr("details"), self.open_details, default=True),
            pystray.MenuItem(lambda item: self.tr("refresh"), self.on_refresh),
            pystray.MenuItem(
                lambda item: self.tr("value_mode_" + self.current_cfg.get("valueMode", "remaining")),
                self.on_toggle_value_mode),
            pystray.MenuItem(
                lambda item: self.tr("auto_update"),
                self.on_toggle_auto_update,
                checked=lambda item: bool(self.current_cfg.get("autoUpdate", False)),
                radio=False),
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
        """Показать окно деталей. Создаётся в главном потоке Tk — иначе
        на Windows возможен жёсткий краш без traceback."""
        self._post_ui(self._show_details)

    def _post_ui(self, fn: Any) -> None:
        self._ui_queue.put(fn)

    def _pump_ui(self) -> None:
        """Выполняет UI-действия в главном потоке."""
        try:
            while True:
                fn = self._ui_queue.get_nowait()
                try:
                    fn()
                except Exception:
                    logger.exception("Ошибка UI-действия")
        except queue.Empty:
            pass
        finally:
            if self._root is not None:
                try:
                    self._root.after(120, self._pump_ui)
                except Exception:
                    pass

    def _show_details(self) -> None:
        win = self._detail_win
        if win is not None:
            try:
                win.deiconify()
                win.lift()
                win.focus_force()
                return
            except Exception:
                self._detail_win = None
        self._details_main()

    def _remember_usage(self, data: dict[str, Any]) -> None:
        """Помнит расход сессии по точкам — из них строится мини-график."""
        s = data.get("s")
        if not s:
            return
        now = time.time()
        self._history.append((now, float(s["used"])))
        cutoff = now - 24 * 3600
        while self._history and self._history[0][0] < cutoff:
            self._history.pop(0)

    def _hourly_buckets(self) -> dict[int, float]:
        """Сколько окна сожгли в каждый час (часы активности)."""
        buckets: dict[int, float] = {}
        hist = self._history
        for i in range(1, len(hist)):
            v0 = hist[i - 1][1]
            v1 = hist[i][1]
            if v1 - v0 <= 0:
                continue
            hour = datetime.fromtimestamp(hist[i][0]).hour
            buckets[hour] = buckets.get(hour, 0.0) + (v1 - v0)
        return buckets

    def _details_main(self) -> None:
        import tkinter as tk
        import tkinter.font as tkfont

        c = {"bg": "#17191C", "card": "#1F2124", "text": "#ECEEF1",
             "muted": "#8B9098", "line": "#24272B", "accent": "#6E9BC4",
             "ok": "#6FA983", "warn": "#D3B05E", "hot": "#CE9260", "risk": "#CC6C64"}
        lang = self.current_cfg["lang"]
        ext_version = read_repo_version()
        upd_time = repo_update_time()

        win = tk.Toplevel(self._root)
        win.title(APP_NAME)
        win.configure(bg=c["bg"])
        win.columnconfigure(0, weight=1)
        self._detail_win = win

        # Масштаб DPI: 1.0 при 100%, 1.25 / 1.5 при масштабировании Windows.
        try:
            S = win.winfo_fpixels("1i") / 96.0
        except Exception:
            S = 1.0

        def px(value: float) -> int:
            return max(1, round(value * S))

        view = {"used": None, "week": None, "mcp": None, "ts": None, "host": ""}

        def on_close() -> None:
            self._detail_win = None
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", on_close)

        def color_for_used(used: float) -> str:
            if used < 50:
                return c["ok"]
            if used < 75:
                return c["warn"]
            if used < 90:
                return c["hot"]
            return c["risk"]

        # ── Шапка ──
        head = tk.Frame(win, bg=c["bg"])
        head.grid(row=0, column=0, sticky="ew", padx=px(16), pady=(px(12), 0))
        tk.Label(head, text=APP_NAME, font=("Segoe UI", 12, "bold"),
                 fg=c["text"], bg=c["bg"]).pack(side="left")
        tk.Label(head, text=f"v{ext_version}", font=("Segoe UI", 9),
                 fg=c["muted"], bg=c["bg"]).pack(side="right")
        tk.Label(win, text=f"{self.tr('updated_at')}: {upd_time}" if upd_time else "",
                 font=("Segoe UI", 9), fg=c["muted"], bg=c["bg"], anchor="w").grid(
            row=1, column=0, sticky="ew", padx=px(16), pady=(px(2), 0))

        # ── Кольцо ──
        ring = tk.Canvas(win, bg=c["bg"], width=px(220), height=px(150),
                         highlightthickness=0)
        ring.grid(row=2, column=0, sticky="nsew", padx=px(16), pady=(px(8), 0))
        win.rowconfigure(2, weight=1)

        def draw_ring() -> None:
            ring.delete("all")
            W = max(ring.winfo_width(), 1)
            H = max(ring.winfo_height(), 1)
            margin = px(10)
            diameter = min(min(W, H) - 2 * margin, px(220))
            diameter = max(diameter, px(70))
            thick = min(max(round(diameter / 10), px(8)), px(16))
            x0 = (W - diameter) / 2
            y0 = (H - diameter) / 2
            ring.create_oval(x0, y0, x0 + diameter, y0 + diameter,
                             outline=c["line"], width=thick)
            used = view["used"]
            shown = self._display(used) if used is not None else None
            if used is None:
                f_dash = tkfont.Font(family="Segoe UI", weight="bold",
                                     size=min(max(round(diameter / (5.5 * S)), 16), 40))
                ring.create_text(W / 2, H / 2, text="—", font=f_dash, fill=c["text"])
                return
            ring.create_arc(x0, y0, x0 + diameter, y0 + diameter, start=90,
                            extent=-359.9 * (shown / 100.0), style="arc",
                            width=thick, outline=color_for_used(used))

            # Текст позиционируем по реальным метрикам шрифта, а не по долям
            # диаметра — иначе процент и подпись слипаются.
            pct_pt = min(max(round(diameter / (5.5 * S)), 16), 40)
            sub_pt = min(max(round(diameter / (16 * S)), 9), 14)
            f_pct = tkfont.Font(family="Segoe UI", size=pct_pt, weight="bold")
            f_sub = tkfont.Font(family="Segoe UI", size=sub_pt)
            h_pct = f_pct.metrics("linespace")
            h_sub = f_sub.metrics("linespace")
            gap = max(px(4), round(h_pct * 0.10))
            inner = diameter - thick
            # Если пара не влезает внутрь кольца — уменьшаем процент.
            while h_pct + gap + h_sub > inner - px(8) and pct_pt > 16:
                pct_pt -= 1
                f_pct = tkfont.Font(family="Segoe UI", size=pct_pt, weight="bold")
                h_pct = f_pct.metrics("linespace")
            block = h_pct + gap + h_sub
            y_top = (H - block) / 2
            ring.create_text(W / 2, y_top + h_pct / 2,
                             text=f"{int(round(shown))}%", font=f_pct, fill=c["text"])
            ring.create_text(W / 2, y_top + h_pct + gap + h_sub / 2,
                             text=self._display_word(), font=f_sub, fill=c["muted"])

        ring.bind("<Configure>", lambda event: draw_ring())

        # ── Подпись ──
        cap = tk.Label(win, text="", font=("Segoe UI", 10), fg=c["muted"], bg=c["bg"])
        cap.grid(row=3, column=0, sticky="ew", padx=px(16), pady=(px(6), 0))

        # ── Мини-график: высота считается из метрик шрифта подписей ──
        f_lbl = tkfont.Font(family="Segoe UI", size=8)
        h_lbl = f_lbl.metrics("linespace")
        top_pad = px(6)
        bar_max = px(34)
        lbl_gap = px(3)
        chart_h = top_pad + bar_max + lbl_gap + h_lbl + px(2)

        chart = tk.Canvas(win, bg=c["bg"], width=px(220), height=chart_h,
                          highlightthickness=0)
        chart.grid(row=4, column=0, sticky="ew", padx=px(16), pady=(px(8), 0))

        def draw_chart() -> None:
            chart.delete("all")
            W = max(chart.winfo_width(), 1)
            base = top_pad + bar_max
            y_lbl = base + lbl_gap
            buckets = self._hourly_buckets()
            now = datetime.now()
            hours = [(now.hour - 3 + i) % 24 for i in range(6)]
            values = [buckets.get(hour, 0.0) for hour in hours]
            peak = max(values) if values else 0.0
            slot = W / 6
            bar_w = min(max(round(slot * 0.6), px(8)), px(48))
            # Если подписи не помещаются — рисуем через одну (текущий час всегда).
            show_labels = slot >= 2.4 * max(1, f_lbl.measure("00"))
            for i, (hour, value) in enumerate(zip(hours, values)):
                center = i * slot + slot / 2
                x = center - bar_w / 2
                if peak > 0 and value > 0:
                    draw_h = max(round(value / peak * bar_max), px(2))
                    color = c["accent"] if i == 3 else c["ok"]
                else:
                    draw_h = px(2)
                    color = c["line"]
                chart.create_rectangle(round(x), base - draw_h, round(x + bar_w), base,
                                       fill=color, outline="")
                if show_labels or i == 3:
                    chart.create_text(round(center), y_lbl, text=f"{hour:02d}",
                                      anchor="n", font=f_lbl,
                                      fill=c["accent"] if i == 3 else c["muted"])
            # Отметка «сейчас» — на полке над столбцами.
            chart.create_line(round(3 * slot + slot / 2), 0,
                              round(3 * slot + slot / 2), top_pad,
                              fill=c["text"], width=2)

        chart.bind("<Configure>", lambda event: draw_chart())

        # ── Строки метрик ──
        rows: dict[str, tk.Label] = {}

        def make_row(row: int, key: str, title: str) -> None:
            wrap = tk.Frame(win, bg=c["bg"])
            wrap.grid(row=row, column=0, sticky="ew", padx=px(16), pady=(px(2), 0))
            tk.Label(wrap, text=title, font=("Segoe UI", 10), fg=c["muted"],
                     bg=c["bg"]).pack(side="left")
            val = tk.Label(wrap, text="—", font=("Segoe UI", 12, "bold"), fg=c["text"],
                           bg=c["bg"])
            val.pack(side="right")
            rows[key] = val

        tk.Frame(win, height=1, bg=c["line"]).grid(row=5, column=0, sticky="ew",
                                                   padx=px(16), pady=(px(12), px(4)))
        make_row(6, "w", self.tr("weekly"))
        make_row(7, "mcp", self.tr("mcp"))

        models_lbl = tk.Label(win, text="", font=("Segoe UI", 9), fg=c["muted"],
                              bg=c["bg"], anchor="w")
        models_lbl.grid(row=8, column=0, sticky="ew", padx=px(16), pady=(px(6), 0))

        # ── Подвал ──
        foot = tk.Frame(win, bg=c["bg"])
        foot.grid(row=9, column=0, sticky="ew", padx=px(16), pady=(px(10), px(12)))
        tk.Button(foot, text="⟳", command=self.wake.set, font=("Segoe UI", 10),
                  bg=c["card"], fg=c["text"], activebackground=c["line"],
                  activeforeground=c["text"], relief="flat",
                  padx=px(10), pady=px(4), cursor="hand2").pack(side="right")
        foot_lbl = tk.Label(foot, text="", font=("Segoe UI", 9), fg=c["muted"],
                            bg=c["bg"], anchor="w")
        foot_lbl.pack(side="left", fill="x", expand=True)

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
            s = d.get("s") if ok else None

            used = float(s["used"]) if s else None
            if used != view["used"]:
                view["used"] = used
                draw_ring()
            if s:
                cap.config(text=f"{self.tr('session')} · {self.tr('reset')} "
                                f"{fmt_countdown(s['resets_at'], lang)}")
            else:
                cap.config(text=self.tr("no_data") if not ok else "")

            for key, source in (("w", "w"), ("mcp", "mcp")):
                win_data = d.get(source) if ok else None
                if win_data:
                    used_w = float(win_data["used"])
                    rows[key].config(text=f"{int(round(self._display(used_w)))}%",
                                     fg=color_for_used(used_w))
                else:
                    rows[key].config(text="—", fg=c["muted"])

            draw_chart()
            mu = self._model_usage
            if mu and mu.get("models"):
                parts = [f"{m['name']} {round(m['share'] * 100)}%"
                         for m in mu["models"][:3]]
                models_lbl.config(text="Модели: " + " · ".join(parts)
                                  if lang == "ru" else "Models: " + " · ".join(parts))
            else:
                models_lbl.config(text="")
            ts = d.get("ts")
            host = HOSTS.get(d.get("platform"), "").replace("https://", "").replace("http://", "")
            label = f"{time.strftime('%H:%M', time.localtime(ts))} · {host}" if ts else ""
            foot_lbl.config(text=label[:48])
            win.after(1000, tick)

        win.update_idletasks()
        win.geometry(f"{max(px(340), win.winfo_reqwidth())}x{max(px(430), win.winfo_reqheight())}")
        win.minsize(px(320), px(400))

        tick()
        win.after(60, draw_ring)
        win.after(60, draw_chart)

    def run(self) -> None:
        """pystray — в фоновом потоке, Tk — в главном: Tk из чужого потока
        на Windows может уронить процесс без traceback."""
        try:
            import tkinter as tk
        except Exception:
            logger.exception("Tkinter недоступен")
            return

        threading.Thread(target=self.poll_worker, name="poll", daemon=True).start()
        threading.Thread(target=self._icon_worker, name="icon", daemon=True).start()

        self._root = tk.Tk()
        self._root.withdraw()
        self._root.report_callback_exception = self._on_tk_error
        self._root.after(120, self._pump_ui)
        try:
            self._root.mainloop()
        except Exception:
            logger.exception("Главный цикл Tk завершился с ошибкой")
        logger.info("Приложение остановлено")

    def _icon_worker(self) -> None:
        try:
            self.icon = pystray.Icon(
                APP_NAME, render_icon(None, COLOR_GRAY), APP_NAME, menu=self.build_menu()
            )
            logger.info("Трей-иконка запущена")
            self._start_update_watch()
            self._start_model_watch()
            self.icon.run()
        except Exception:
            logger.exception("Трей-иконка завершилась с ошибкой")

    def _on_tk_error(self, exc: Any, val: Any, tb: Any) -> None:
        logger.error("Ошибка в Tk-колбэке: %s", val)
        traceback.print_exception(exc, val, tb)

    def _shutdown_ui(self) -> None:
        try:
            if self._root is not None:
                self._root.quit()
                self._root.destroy()
                self._root = None
        except Exception:
            logger.exception("Не удалось закрыть Tk")


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
    enable_dpi_awareness()
    if _IMPORT_ERROR is not None:
        msg = (f"Не хватает зависимостей: {_IMPORT_ERROR}. "
               "Установи их: pip install -r tray/requirements.txt (или запусти tray_install.bat)")
        logger.error(msg)
        print(msg)
        return 1
    if "--selftest" in argv:
        return selftest()
    if "--enable-autostart" in argv or "--disable-autostart" in argv:
        enable = "--enable-autostart" in argv
        try:
            set_autostart(enable)
            msg = "Автозапуск включён" if enable else "Автозапуск выключен"
            logger.info(msg)
            print(msg)
            return 0
        except Exception as exc:
            logger.exception("Не удалось изменить автозапуск")
            print(f"Не удалось изменить автозапуск: {exc}")
            return 1

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
