# GLM Mochi — GLM Limits

A Chrome extension that shows your **GLM Coding Plan** session (5h) and weekly usage at a glance.

## Features

- **Session (5h) metric** — token-budget window from `TOKENS_LIMIT` (unit 3, number 5)
- **Weekly metric** — from `TOKENS_LIMIT` (unit 6, number 1)
- **Optional MCP usage** — from `TIME_LIMIT` (monthly meter)
- **Color-coded bars** — green <50%, yellow <75%, amber <90%, red ≥90%
- **Countdown to reset** — live-updating every second
- **Three view modes** — popup, window, compact
- **Document PiP** — pin on top
- **i18n** — English / Русский
- **Themes** — Clay / Dark

## API contract

The extension polls the GLM quota endpoint:

```
GET https://open.bigmodel.cn/api/monitor/usage/quota/limit   (China)
GET https://api.z.ai/api/monitor/usage/quota/limit           (International)
```

**Auth:** raw API key in `Authorization` header — **no `Bearer` prefix**.

Response shape:

```json
{
  "code": 200,
  "success": true,
  "data": {
    "level": "pro",
    "limits": [
      { "type": "TOKENS_LIMIT", "unit": 3, "number": 5, "percentage": 62, "nextResetTime": 1725000000000 },
      { "type": "TOKENS_LIMIT", "unit": 6, "number": 1, "percentage": 41, "nextResetTime": 1725700000000 },
      { "type": "TIME_LIMIT",   "unit": 3, "number": 1, "percentage": 10, "nextResetTime": 1727700000000 }
    ]
  }
}
```

- `percentage` — how much of the window is **already used**; the UI shows the remaining share.
- `nextResetTime` — epoch milliseconds; reset clocks follow Singapore Time (GMT+8).
- Window selection: `TOKENS_LIMIT` with `unit=3, number=5` → session (5h); `unit=6, number=1` → weekly; `TIME_LIMIT` → optional monthly MCP row. Any other hourly/weekly `TOKENS_LIMIT` entry is used as a fallback when the canonical ones are absent.

## Installation

1. Open `chrome://extensions`
2. Enable **Developer mode** (top-right toggle)
3. Click **Load unpacked** and select this directory
4. Click the extension icon, enter your API key, choose platform, and save

## Settings

| Setting          | Description                                                  |
| ---------------- | ------------------------------------------------------------ |
| API Key          | Your GLM Coding Plan API key (stored locally in the browser) |
| Platform         | China (open.bigmodel.cn) or International (api.z.ai)         |
| Refresh interval | Seconds between polls (30–3600, default 120)                 |
| View mode        | Popup, Window, or Compact                                    |
| Language         | English / Русский                                            |
| Theme            | Clay / Dark                                                  |
| Show MCP         | Display MCP usage row                                        |

The API key is entered once in the extension settings and kept in `chrome.storage.local` on your machine; it is only sent to the selected GLM quota endpoint.

## Files

- `manifest.json` — MV3 manifest
- `background.js` — service worker: polling, caching, settings
- `popup.html` — UI markup
- `popup.css` — styling
- `popup.js` — rendering, settings, i18n, PiP
- `icons/icon16.png`, `icon48.png`, `icon128.png` — toolbar icons

## License

MIT
