"""GLM Limits — тесты трей-компаньона.

Запуск:  python -m unittest discover -s tests -p "test_*.py" -v

Требуется только `requests` (для мока API). pystray/Pillow на CI могут быть
недоступны — модуль трея импортирует их под защитой, тесты это учитывают.
"""
import importlib.util
import os
import sys
from datetime import datetime, timedelta, timezone
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRAY_FILE = ROOT / "tray" / "tray_glm_limits.pyw"

ENV_KEY_VARS = ("GLM_API_KEY", "ZAI_API_KEY", "BIGMODEL_API_KEY", "ZHIPUAI_API_KEY")


def load_tray_module():
    spec = importlib.util.spec_from_file_location("tray_under_test", TRAY_FILE)
    module = importlib.util.module_from_spec(spec)
    sys.modules["tray_under_test"] = module
    spec.loader.exec_module(module)
    return module


tray = load_tray_module()


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class FakeIcon:
    """Заглушка pystray.Icon — собирает уведомления."""

    def __init__(self):
        self.messages = []

    def notify(self, message, title=None):
        self.messages.append((title, message))

    def update_menu(self):
        pass


CREDIT_RESPONSE = {
    "code": 200,
    "success": True,
    "data": {
        "level": "max",
        "limits": [
            {"type": "CREDIT_LIMIT", "unit": 3, "number": 5, "usage": 28000,
             "currentValue": 1086, "remaining": 26913, "percentage": 3,
             "nextResetTime": 1792602151287},
            {"type": "CREDIT_LIMIT", "unit": 6, "number": 1, "usage": 140000,
             "currentValue": 9781, "remaining": 130218, "percentage": 6,
             "nextResetTime": 1793094553964},
        ],
    },
}


class TestDetectPlatform(unittest.TestCase):
    def test_dot_means_bigmodel(self):
        self.assertEqual(tray.detect_platform("id.secret"), "cn")

    def test_zai_prefix_means_international(self):
        self.assertEqual(tray.detect_platform("zai_abc"), "intl")
        self.assertEqual(tray.detect_platform("ZAI-abc"), "intl")

    def test_opaque_key_defaults_to_international(self):
        self.assertEqual(tray.detect_platform("opaquekey"), "intl")

    def test_empty_key(self):
        self.assertEqual(tray.detect_platform(""), "cn")


class TestFetchQuota(unittest.TestCase):
    def setUp(self):
        self._orig_requests = tray.requests
        self.calls = []

    def tearDown(self):
        tray.requests = self._orig_requests

    def _mock(self, payload, status=200):
        def fake_get(url, headers=None, timeout=None):
            self.calls.append((url, headers, timeout))
            return FakeResponse(payload, status)
        tray.requests = type("R", (), {"get": staticmethod(fake_get)})

    def test_parses_credit_limit_windows(self):
        self._mock(CREDIT_RESPONSE)
        data = tray.fetch_quota("id.secret", "auto")
        self.assertIsNotNone(data["s"], "сессия должна распознаться")
        self.assertIsNotNone(data["w"], "неделя должна распознаться")
        self.assertEqual(data["s"]["used"], 3)
        self.assertEqual(data["w"]["used"], 6)
        self.assertEqual(data["plan"], "max")

    def test_auto_platform_detection(self):
        self._mock(CREDIT_RESPONSE)
        data = tray.fetch_quota("id.secret", "auto")
        self.assertEqual(data["platform"], "cn")
        self.assertIn("open.bigmodel.cn", self.calls[0][0])

    def test_raw_key_without_bearer(self):
        self._mock(CREDIT_RESPONSE)
        tray.fetch_quota("my-key", "intl")
        headers = self.calls[0][1]
        self.assertEqual(headers["Authorization"], "my-key")
        self.assertNotIn("Bearer", headers["Authorization"])

    def test_http_error_raises(self):
        self._mock({"error": "nope"}, status=401)
        with self.assertRaises(RuntimeError):
            tray.fetch_quota("my-key", "intl")

    def test_broken_payload_does_not_crash(self):
        self._mock({"code": 200, "data": None})
        data = tray.fetch_quota("my-key", "intl")
        self.assertIsNone(data["s"])
        self.assertIsNone(data["w"])


class TestNotifications(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self._orig_config = tray.CONFIG_PATH
        self._orig_state = tray.STATE_PATH
        tray.CONFIG_PATH = tmp / "tray_config.json"
        tray.STATE_PATH = tmp / "tray_state.json"

    def tearDown(self):
        tray.CONFIG_PATH = self._orig_config
        tray.STATE_PATH = self._orig_state
        self._tmp.cleanup()

    def test_fires_once_per_threshold(self):
        app = tray.TrayApp()
        app.icon = FakeIcon()
        cfg = {"notify": True, "lang": "ru"}

        app.check_notifications(cfg, {"s": {"used": 80, "resets_at": "w1"}})
        self.assertEqual(len(app.icon.messages), 1, "на 80% — одно уведомление")

        app.check_notifications(cfg, {"s": {"used": 85, "resets_at": "w1"}})
        self.assertEqual(len(app.icon.messages), 1, "81..85% — тишина")

        app.check_notifications(cfg, {"s": {"used": 92, "resets_at": "w1"}})
        self.assertEqual(len(app.icon.messages), 2, "на 90% — ещё одно")

        app.check_notifications(cfg, {"s": {"used": 99, "resets_at": "w1"}})
        self.assertEqual(len(app.icon.messages), 2, "99% — тишина")

    def test_new_window_rearms_thresholds(self):
        app = tray.TrayApp()
        app.icon = FakeIcon()
        cfg = {"notify": True, "lang": "ru"}
        app.check_notifications(cfg, {"s": {"used": 80, "resets_at": "w1"}})
        app.check_notifications(cfg, {"s": {"used": 95, "resets_at": "w2"}})
        self.assertEqual(len(app.icon.messages), 2, "в новом окне порог срабатывает снова")


    def test_notify_text_session_has_limit_and_reset(self):
        app = tray.TrayApp()
        iso = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        txt = app._notify_text("ru", "session", 81, iso)
        self.assertIn("Осталось 19% лимита", txt)
        self.assertIn("Сброс через", txt)
        self.assertNotIn("До сброса", txt, "старая двусмысленная формулировка не должна вернуться")

    def test_notify_text_weekly_has_date(self):
        app = tray.TrayApp()
        iso = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
        txt = app._notify_text("ru", "weekly", 76, iso)
        self.assertIn("Осталось 24% лимита", txt)
        self.assertRegex(txt, r"\d{2}\.\d{2} в \d{2}:\d{2}", "у недели должна быть дата сброса")

    def test_notify_text_without_reset(self):
        app = tray.TrayApp()
        txt = app._notify_text("ru", "session", 85, None)
        self.assertIn("Осталось 15% лимита", txt)
        self.assertNotIn("Сброс", txt)

    def test_disabled_notifications(self):
        app = tray.TrayApp()
        app.icon = FakeIcon()
        app.check_notifications({"notify": False, "lang": "ru"},
                                {"s": {"used": 99, "resets_at": "w1"}})
        self.assertEqual(len(app.icon.messages), 0)


class TestStrings(unittest.TestCase):
    """Именно отсутствующий ключ перевода ('mcp') однажды уронил окно трея."""

    REQUIRED = (
        "session", "weekly", "mcp", "left", "used", "no_data", "error",
        "reset", "updated_at", "details", "refresh", "exit",
        "upd_ext", "upd_available", "upd_no_available", "upd_done_ver",
        "upd_incomplete", "upd_none", "upd_fail", "tray_restarting",
        "restart", "restarting", "restart_fail",
        "key_menu", "key_prompt", "key_current_set", "key_current_none",
        "key_saved", "key_empty", "save_btn", "cancel_btn",
        "auto_on", "auto_off", "notify", "no_key",
    )

    def test_all_keys_present_in_both_locales(self):
        for lang in ("ru", "en"):
            for key in self.REQUIRED:
                self.assertIn(key, tray.STRINGS[lang], f"нет ключа '{key}' в локали {lang}")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self._orig_config = tray.CONFIG_PATH
        self._orig_state = tray.STATE_PATH
        tray.CONFIG_PATH = tmp / "tray_config.json"
        tray.STATE_PATH = tmp / "tray_state.json"

    def tearDown(self):
        tray.CONFIG_PATH = self._orig_config
        tray.STATE_PATH = self._orig_state
        self._tmp.cleanup()

    def test_tr_does_not_raise_on_missing_key(self):
        app = tray.TrayApp()
        self.assertEqual(app.tr("no_such_key"), "no_such_key",
                         "отсутствующий ключ должен возвращаться строкой, а не падать")

    def test_window_keys_resolve(self):
        app = tray.TrayApp()
        for key in ("mcp", "session", "weekly", "left", "reset", "no_data"):
            self.assertIsInstance(app.tr(key), str)


class TestValueMode(unittest.TestCase):
    """Режим показа чисел в трее: остаток (по умолчанию) или израсходовано."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self._orig_config = tray.CONFIG_PATH
        self._orig_state = tray.STATE_PATH
        tray.CONFIG_PATH = tmp / "tray_config.json"
        tray.STATE_PATH = tmp / "tray_state.json"

    def tearDown(self):
        tray.CONFIG_PATH = self._orig_config
        tray.STATE_PATH = self._orig_state
        self._tmp.cleanup()

    def test_default_is_remaining(self):
        self.assertEqual(tray.load_config()["valueMode"], "remaining")

    def test_set_and_read_used(self):
        self.assertTrue(tray.set_value_mode("used"))
        self.assertEqual(tray.load_config()["valueMode"], "used")
        self.assertIn('"valueMode": "used"',
                      tray.CONFIG_PATH.read_text(encoding="utf-8"))

    def test_display_math(self):
        app = tray.TrayApp()
        app.current_cfg["valueMode"] = "remaining"
        self.assertEqual(app._display(40), 60)   # израсходовано 40 -> осталось 60
        app.current_cfg["valueMode"] = "used"
        self.assertEqual(app._display(40), 40)

    def test_strings_for_both_modes(self):
        for lang in ("ru", "en"):
            for key in ("value_mode_remaining", "value_mode_used"):
                self.assertIn(key, tray.STRINGS[lang])


@unittest.skipUnless(sys.platform == "win32", "DPAPI доступен только на Windows")
class TestKeyProtection(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self._orig_config = tray.CONFIG_PATH
        self._orig_env = {k: os.environ.pop(k, None) for k in ENV_KEY_VARS}
        tray.CONFIG_PATH = tmp / "tray_config.json"

    def tearDown(self):
        tray.CONFIG_PATH = self._orig_config
        for k, v in self._orig_env.items():
            if v is not None:
                os.environ[k] = v
        self._tmp.cleanup()

    def test_dpapi_roundtrip(self):
        secret = b"super-secret-key"
        blob = tray.protect_secret(secret)
        self.assertIsNotNone(blob)
        self.assertNotIn(secret, blob)
        self.assertEqual(tray.unprotect_secret(blob), secret)

    def test_key_is_not_stored_in_plaintext(self):
        secret = "my-glm-api-key"
        self.assertTrue(tray.write_protected_key(secret))
        raw = tray.CONFIG_PATH.read_text(encoding="utf-8")
        self.assertNotIn(secret, raw, "ключ не должен лежать открытым в конфиге")
        self.assertIn("apiKeyProtected", raw)

    def test_config_reads_protected_key(self):
        tray.write_protected_key("my-glm-api-key")
        cfg = tray.load_config()
        self.assertEqual(cfg["apiKey"], "my-glm-api-key")

    def test_plain_key_is_migrated_on_read(self):
        tray.CONFIG_PATH.write_text('{"apiKey": "legacy-key"}', encoding="utf-8")
        cfg = tray.load_config()
        self.assertEqual(cfg["apiKey"], "legacy-key")
        raw = tray.CONFIG_PATH.read_text(encoding="utf-8")
        self.assertNotIn("legacy-key", raw, "старый открытый ключ должен быть перешифрован")
        self.assertIn("apiKeyProtected", raw)


if __name__ == "__main__":
    unittest.main()
