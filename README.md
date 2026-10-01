# Pacer Speedometer

A GPS speedometer that runs in your phone's browser. No app store, no account, no backend: your location never leaves the phone.

**Live:** https://vishai12345.github.io/speedometer/

## Use
1. Open the link in Safari or Chrome on your phone, tap **Enable location** and allow it. Next time it starts by itself.
2. The Drive screen shows your speed. Set the limit with − / +, pick units and a style, tap **Record** to save a trip and **Alert** for a chime above the limit.
3. Tap the expand icon for drive mode: gauge only, full screen, screen kept on.

## Screens
- **Drive:** speed first; GPS status, speed limit, trip line and quick controls.
- **Trip:** distance, times, speed breakdown, 5-minute chart, recording (start / pause / stop) and saved trips with GPX and CSV export.
- **Settings:** alert tolerance, theme, keep screen on, saved data.
- **About:** how speed and distance are measured, and validation results.

## Files
- `index.html` — the app (UI, rendering, recording, permissions).
- `engine.js` — measurement engine with no DOM: fix validation, Kalman speed filter, signal states, distance, 0–100 timing, overspeed alerting.
- `tests/engine.test.js` — engine tests on synthetic GPS traces with known ground truth: `node tests/engine.test.js`
- `tests/ui_scenarios.py` — browser scenario tests with a fake GPS (Playwright): `python3 -m http.server 8765 & python3 tests/ui_scenarios.py`
- `docs/VERIFICATION.md` — requirement-by-requirement verification report with test evidence (v3.1).
- `docs/REVIEW-RESPONSE.md` — v3.0 audit and engineering decisions.
- `docs/test-results/`, `docs/screenshots/` — raw test output and screenshots from the latest run.

## Deploy
Static files only. GitHub Pages serves `main` / root over HTTPS, which browsers require for location.
