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

import pystray
import requests
from PIL import Image, ImageDraw, ImageFont

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
    """Читает tray_config.json; ключ можно задать и через переменные окружения."""
    cfg: dict[str, Any] = {}
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.exception("Не удалось прочитать конфиг %s", CONFIG_PATH)
    api_key = str(
        cfg.get("apiKey")
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
        elif itype == "TOKENS_LIMIT":
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


def read_repo_version() -> str:
    """Версия расширения из манифеста репозитория (для окна деталей)."""
    try:
        manifest = json.loads((REPO_DIR / "manifest.json").read_text(encoding="utf-8"))
        return str(manifest.get("version") or "?")
    except (OSError, json.JSONDecodeError):
        return "?"


class State:
    """Общее состояние между потоком опроса и иконкой/меню/окном."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[str, Any] = {"ok": False, "error": None}
        self.notif: dict[str, Any] = {"win": None, "s80": False, "s90": False}

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
        self.current_cfg: dict[str, Any] = load_config()

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
        s = data.get("s")
        if not cfg["notify"] or not s:
            return
        used = float(s["used"])
        win = s.get("resets_at")
        n = self.state.notif
        if n["win"] != win:
            n["win"], n["s80"], n["s90"] = win, False, False
        if used < 70:
            n["s80"] = n["s90"] = False
            return
        fired = False
        if used >= 90 and not n["s90"]:
            n["s90"] = n["s80"] = True
            fired = True
        elif used >= 80 and not n["s80"]:
            n["s80"] = True
            fired = True
        if fired and self.icon:
            msg = STRINGS[cfg["lang"]]["notify"].format(u=int(used), l=int(100 - used))
            try:
                self.icon.notify(msg, APP_NAME)
            except Exception:
                logger.exception("Не удалось показать уведомление")

    # ── поток опроса ─────────────────────────────────────────
    def poll_worker(self) -> None:
        while not self.stop_evt.is_set():
            cfg = load_config()  # перечитываем — правки конфига подхватываются сами
            self.current_cfg = cfg
            if not cfg["apiKey"]:
                self.state.set({"ok": False, "error": self.tr("no_key"), "ts": time.time()})
            else:
                try:
                    data = fetch_quota(cfg["apiKey"], cfg["platform"])
                    self.state.set(data)
                    self.check_notifications(cfg, data)
                    logger.info(
                        "Опрос ок (platform=%s): сессия %s%%, неделя %s%%",
                        data.get("platform"),
                        int(data["s"]["used"]) if data.get("s") else -1,
                        int(data["w"]["used"]) if data.get("w") else -1,
                    )
                except Exception as exc:
                    logger.exception("Опрос не удался")
                    self.state.set({"ok": False, "error": str(exc)[:200], "ts": time.time()})
            self.refresh_icon()
            self.wake.wait(cfg["intervalSec"])
            self.wake.clear()

    # ── действия меню ────────────────────────────────────────
    def on_refresh(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        self.wake.set()

    def on_exit(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
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
        """git pull репозитория; расширение само заметит новую версию и перезапустится."""
        lang = self.current_cfg["lang"]
        try:
            proc = subprocess.run(
                ["git", "-C", str(REPO_DIR), "pull", "--ff-only", "origin", "main"],
                capture_output=True, text=True, timeout=60,
                encoding="utf-8", errors="replace",
            )
            out = (proc.stdout or "") + (proc.stderr or "")
            logger.info("git pull: %s", out.strip()[:300])
            if proc.returncode != 0:
                raise RuntimeError((out.strip() or "git failed")[:120])
            changed = "fast-forward" in out.lower() or "files changed" in out.lower()
            if icon:
                icon.notify(self.tr("upd_done") if changed else self.tr("upd_none"), APP_NAME)
        except Exception as exc:
            logger.exception("Обновление расширения не удалось")
            if icon:
                try:
                    icon.notify(STRINGS[lang]["upd_fail"].format(e=str(exc)[:80]), APP_NAME)
                except Exception:
                    pass

    def build_menu(self) -> pystray.Menu:
        return pystray.Menu(
            pystray.MenuItem(self.line_session, None, enabled=False),
            pystray.MenuItem(self.line_weekly, None, enabled=False),
            pystray.MenuItem(self.line_reset, None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(lambda item: self.tr("details"), self.open_details, default=True),
            pystray.MenuItem(lambda item: self.tr("refresh"), self.on_refresh),
            pystray.MenuItem(lambda item: self.tr("upd_ext"), self.update_extension),
            pystray.MenuItem(
                lambda item: self.tr("autostart"), self.toggle_autostart,
                checked=lambda item: is_autostart_on(), radio=False,
            ),
            pystray.Menu.SEPARATOR,
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

        win = tk.Tk()
        win.title(f"{APP_NAME} v{ext_version}")
        win.geometry("330x470")
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
        win.mainloop()

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

    TrayApp().run()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
