# Verification report: Pacer v3.1

Response to the third-iteration review. Evidence is from the code in this repository and from test runs on it. Every requirement below lists previous behaviour, updated behaviour, the code changed, the test and the result.

**Raw evidence:** [`test-results/engine-tests.txt`](test-results/engine-tests.txt) (26/26), [`test-results/browser-tests.txt`](test-results/browser-tests.txt) (31/31, no page errors), [`screenshots/`](screenshots/).

**Not claimed:** no test in this report was run on a physical phone. Browser tests run in desktop Chromium with phone viewports, an iPhone user agent and a scripted fake GPS. Engine tests use synthetic GPS traces with known ground truth, not recorded drives.

## 0. What the review saw vs. what is deployed

Several "outstanding" items (50 m rejection threshold, per-frame distance integration, "Starting GPS" text) describe **v2.x**, which was replaced in commit `bd9a797` (v3.0) before this review. The live site already served `engine.js` with timestamp-based integration at review time. Those items are marked **already fixed in v3.0**, with the test that proves it. Items that were genuinely open are marked **fixed in v3.1**.

| v2.x (reviewed) | v3.0 / v3.1 (current) |
|---|---|
| 50 m accuracy cut-off on all fixes | Doppler speed kept up to 100 m accuracy, noise scaled above 20 m; position fallback needs ≤ 25 m |
| Distance = filtered speed × frame time, every animation frame | Distance integrated in `engine.js` between GPS fix timestamps; render loop only reads it |
| Last speed kept on screen when GPS stopped | Speed is `null` ("––") after 4 s without a usable fix |
| Alert tone every 1.5 s while over | Onset 1.5 s, repeat ≤ 1/min, 3 km/h exit hysteresis |

## 1. Requirement-by-requirement

### 2.1 Permission flow — fixed in v3.1
- **Previous:** v3.0 called `watchPosition()` on page load, so a prompt could appear without user action. This was the product owner's earlier instruction, but it conflicted with the review.
- **Decision (product owner):** hybrid. If permission is already *granted*, GPS starts on load; no prompt is possible in that state. If it is *prompt* or unknown, nothing starts until the user taps **Enable location**. If *denied*, recovery steps are shown and no watch is started.
- **Code:** `index.html` → `bootLocation()`, `#gateBtn` handler, `startGps()` (calls `stopGps()` first, so repeated taps can't stack watchers), new `asking` gate state, and a 20 s fallback that re-offers the button if a prompt is dismissed silently.
- **Tests:** R1, R1b, R2, R2b, R3, R3b.
- **Results:**
  - On first launch, `watchPosition` is called 0 times.
  - Three rapid taps leave exactly 1 active watcher.
  - The first fix unlocks the speedometer.
  - A returning user who already allowed location starts with no card.
  - Denied: the watcher is released and 3 platform-specific steps are shown.
  - Previously denied: no watcher is started.
- **Limitation:** Safari versions without the Permissions API always show the card (one extra tap). Prompt wording and dismissal behaviour on real iOS and Android are unverified.

### 2.2 GPS signal states — already fixed in v3.0, verified
- **States:** `idle → acquiring → ok | poor → lost` in `Engine.status()`.
  - **Signal availability:** acquiring (no fix yet) and lost (no usable fix for 4 s).
  - **Measurement quality:** ok, or poor (accuracy > 30 m, or position-derived speed).
  - The UI shows these separately: the pill text and colour, plus 0–3 signal bars.
- **Tests:** R0, R4, R5; engine "Before first fix", "Tunnel", "No Doppler speed: flagged as poor".
- **Results:**
  - Before the first fix: "––" and "Finding GPS".
  - After 5 s without fixes: "––", "Signal lost", and the screen reader announces "GPS signal lost".
  - Recovery shows the new speed (43 km/h) on the first fix.
  - Weak accuracy shows "Weak GPS ±60 m".

### 2.3 / 3.2 Speed filtering and measurement uncertainty — already fixed in v3.0, refined in v3.1
- **Measurement model (`engine.js` → `ingest()`):**
  - **Doppler `coords.speed`:** σ = 0.45 m/s, scaled by `accuracy / 20` only when accuracy exceeds 20 m.
    - Horizontal accuracy is used **only as a quality proxy** for scaling, never as a speed standard deviation.
    - Missing accuracy uses σ × 1.5.
  - **Position-derived speed:** σ² = (√(acc₁² + acc₂²) / Δt)² + 0.25. Both fixes must be ≤ 25 m and ≥ 0.8 s apart. Displacement below half the combined error counts as 0.
  - **Process model:** speed as a random walk with σₐ = 1.5 m/s².
- **Long gaps (3.1), new in v3.1:**
  - After more than 10 s between accepted fixes (`resetGap`), the filter is discarded and re-initialised from the next reading.
  - The moving state, outlier memory and acceleration are reset too.
  - Previously the filter only grew its variance, which in practice re-initialised it, but implicitly.
- **Tests:** engine 2, 3, 6, 7, 11 (long gap), 30 s gap.
- **Results:**
  - Cruise bias −0.03 km/h, jitter ±0.85 km/h (raw ±0.90).
  - Lag at 3 m/s²: 0.00 s at fix times.
  - A +65 km/h spike moved the display 1.9 km/h.
  - After a 3-minute gap, the first reading is 34.2 km/h (true 36; 72 before the gap), with 1 reset.
- **Limitation:** the noise parameters are assumptions validated only on synthetic noise. Real phone traces are needed to tune σ values.

### 2.4 Trip distance — already fixed in v3.0, verified further in v3.1
- **Code:** `engine.js` → `ingest()`, the "trip integration on fix timestamps" block. The render loop (`frame()` in `index.html`) never writes distance.
- **Rules:**
  - Trapezoid of displayed speed between fix timestamps (Δt ≤ 5 s).
  - 5–60 s gaps: straight line between good fixes, only if it exceeds the combined position error.
  - Gaps over 60 s: nothing is added.
  - Moving time is counted only when distance was added or Δt ≤ 5 s.
- **Tests:** engine 4, 5, 11, 12; browser R8, R9, R10, R6.
- **Results:**
  - 20-minute drive: −0.04% (summing positions would give +6.8%).
  - Reading speed 1,000× between fixes leaves distance unchanged to 1e-9 m.
  - A 65 s gap in the browser adds 0 m; a parked phone after 2 minutes in the background adds 0 m.
  - Mode switching records exactly one point per fix.
  - 4 unit changes leave the stored distance and top speed bit-identical.
- **Limitation:** gap bridging uses a straight line, so it undercounts curved roads (−2.8% on a test with a 30 s gap).

### 2.5 Recording lifecycle — verified, two defects fixed in v3.1
- **States:** `off → on ⇄ paused → off (save | discard-if-empty)`, held in `rec` in `index.html`.
- **Defects found and fixed:**
  1. **Reset trip while recording** lost recording distance, because the stored offset `rec.lastDist` was not reset. Fixed in `#resetTrip`.
  2. **No guard against double start.** `recStart()` now returns if a recording is already on. Double save was already impossible, because `recStop()` returns when off.
- **Persistence:** trip totals and the recording are saved every 10 s and on `pagehide`/`visibilitychange`, and restored on load with a toast.
- **Storage failure:** `persist()` now checks every write. On failure it shows a one-time, non-fatal message, and the trip keeps running.
- **Tests:** R9, R11, R12, R13, R14, R15, R18, R18b.
- **Results:**
  - **Navigation:** 4 points across 4 screen changes.
  - **Pause:** +0 m and +0 points while paused; +61 m for 3 s at 20 m/s after resume.
  - **Refresh:** recording restored with all 14 points.
  - **Reset:** stored distance is 0, and Trip shows "––" after reload.
  - **Storage failure:** message shown, speed stays live.
  - **Empty recording:** not saved, with an explanation.
- **Limitation:** a background tab gets no GPS on mobile, so recording pauses in practice while the browser is hidden.

### 2.6 Drive screen — verified, one defect fixed in v3.1
- **Hierarchy:** the speed numeral is 21–50% of gauge width depending on style, and is the largest element in every mode. GPS status is a small pill. Controls are quiet segmented groups below the trip line.
- **Defect fixed:** segmented controls were 38 px tall (34 px in landscape), below the 44 px target. They are now 44 px everywhere.
- **Tests:** R20 at 360×640, 390×844, 844×390 and 667×375.
- **Results:** no sideways scroll, every control above the tab bar, smallest target 44 px, gauge 220–358 px.
- **Screenshots:** `screenshots/06_*.png`, `03_finding_gps.png`, `04_signal_lost.png`, `05_overspeed.png`.

### 2.7 Display modes — verified
- All four styles are draw functions that receive the same `{has, vU, maxU}` built from `engine.speed()`. There is no per-mode state.
- Switching modes does not touch the engine or the recorder (R9).
- **HUD mirror:** CSS `transform: scaleX(-1)` on the gauge, verified in Chromium.
- **Screenshots:** `screenshots/05_mode_{classic,sport,digital,hud}.png`.
- **Limitation:** mirroring and HUD legibility as a windshield reflection have not been checked on a real phone.

### 2.8 Trip screen — fixed in v3.1
- **Previous:** Trip values showed `0` / `0:00` before any GPS data.
- **Updated:** `tripHasData()` makes distance, elapsed, moving and top show "––" until the first fix. Average moving shows "––" until 5 s of movement, and the Drive trip line does the same. Real zeros, such as parked with GPS, still show 0.
- **Chart:**
  - 5-minute bounded buffer (305 s).
  - `null` samples break the line.
  - **New in v3.1:** the line also breaks where consecutive samples are more than 2.5 s apart, e.g. after the tab was hidden.
  - Labelled axes in the current unit.
- **Tests:** R0, R13, R18b; screenshot `09_trip_saved.png`.

### 2.9 Performance and resources — verified
- **Single loop:** one `requestAnimationFrame` loop for the whole app (started once at boot), and one GPS watcher at most (R2).
- **Redraws:** the gauge redraws only when a signature of style, unit, limit, tolerance, rounded speed, top speed, theme and canvas size changes, so a parked phone at a steady 0 does not redraw.
- **Canvas sizing:** canvases resize through `ResizeObserver` to CSS size × DPR (capped at 3). Test R17 checked five viewport sizes: always square and DPR-scaled.
- **Wake Lock:** re-requested on `visibilitychange` when wanted. A denial is explained and the app keeps working (R16).

### 2.10 Accessibility — verified in Chromium
- **Touch targets:** ≥ 44 px on the Drive screen (R20).
- **Contrast (WCAG):**

| Pair | Day | Night | Needed |
|---|---|---|---|
| Speed number | 15.2 | 16.4 | 7 |
| Secondary text | 5.4 | 7.2 | 4.5 |
| Amber near-limit number | 4.2 | 10.8 | 3 (large) |
| Red over-limit number | 4.9 | 6.2 | 3 (large) |
| Button text | 4.9–5.6 | 5.7–9.9 | 4.5 |
| HUD number on black | 17.6 | 17.6 | 7 |

- **Reduced motion:** with motion reduced, the speed jumps straight to the measured value with no easing, and all CSS animations stop (R19).
- **Screen reader:** a polite live region announces status changes ("GPS signal lost", "Over the speed limit") at once, and speed at most every 10 s, only when it changes by 5 units or more.
- **Limitation:** not yet tested with VoiceOver or TalkBack.

### 3.3 Speed history — verified
Timestamps come from `Date.now()` on each 1 s sample. Missing data is `null` and drawn as a gap. The buffer is capped at 305 samples, and the chart draws only while the Trip screen is open.

### 3.4 Overspeed alerts — verified, one defect fixed in v3.1
- **Defect fixed:** changing the limit or tolerance mid-alert kept the old alert state. Both now call `overspeed.reset()`.
- **Tests:** engine 10 plus browser A1–A3.
- **Results:**
  - Hovering ±1 km/h at the limit: 1 alert in 60 s.
  - Sustained overspeed: 2 alerts in 65 s.
  - Raising the limit from 100 to 120 clears the alert immediately.
  - Muted: red state only, no sound or vibration.
  - GPS lost during an alert clears it.
- **Audio:** the chime is unlocked on a user tap, and the visual cue is always present, so sound is never the only signal.

## 2. Mandatory regression tests (section 5)

| # | Test | Result | Evidence |
|---|---|---|---|
| 1 | Initial page load | PASS | 0 `watchPosition` calls |
| 2 | Enable location | PASS | 1 watcher after 3 taps |
| 3 | Denied permission | PASS | steps shown, watcher released |
| 4 | GPS loss | PASS | "––", "Signal lost", announced |
| 5 | GPS recovery | PASS | 43 km/h on first fix after a 12 s gap |
| 6 | Stationary device | PASS | 0 m after jitter (browser); 0 m in 5 min (engine) |
| 7 | Moving device | PASS | 0.00 s lag at 3 m/s² (engine); an instant 0→90 jump is held for one fix as physically impossible |
| 8 | Long GPS gap | PASS | 0 m added across 65 s |
| 9 | Mode switching | PASS | 4/4 points recorded |
| 10 | Unit switching | PASS | stored values identical |
| 11 | Navigation | PASS | 4/4 points, REC indicator kept |
| 12 | Pause and resume | PASS | 0 m paused, 61 m resumed |
| 13 | Reset | PASS | 0 stored, "––" after reload |
| 14 | Browser refresh | PASS | recording restored with all points |
| 15 | Storage failure | PASS | non-fatal message, speed live |
| 16 | Wake Lock denial | PASS | explained, app works |
| 17 | Canvas resize | PASS | 5 sizes, square, DPR-scaled |
| 18 | Export | PASS | valid GPX; CSV in km/h, median 72.0 (true 72) |
| 19 | Reduced motion | PASS | no easing, animations off |
| 20 | Mobile landscape | PASS | 844×390 and 667×375, 44 px targets |

## 3. Outstanding limitations

1. **No physical-device testing.** iOS Safari and Android Chrome behaviour still needs a real drive, ideally logged against a reference GPS: permission dialogs, background suspension, Wake Lock, vibration (iOS has none), mirrored HUD legibility.
2. **Filter parameters** are validated on synthetic noise only. A recorded-trace replay harness exists (`tests/engine.test.js` accepts any fix list), but no real traces are committed yet.
3. **Background:** mobile browsers suspend GPS for hidden tabs. Pacer adds no distance for gaps over 60 s, so a trip driven mostly with the screen off will under-report.
4. **Screen readers:** announcement wording and frequency are untested with VoiceOver and TalkBack.
