# Review response: Pacer v3.0

A response to the independent v2.0 review: what was kept, what changed and why, how it was tested, and what is still unverified.

## Audit of v2.0

| Area | Verdict | Finding |
|---|---|---|
| Single-page shell, hash routing | Keep | Screens swap without reloading, so GPS and recording never restart. |
| Canvas gauge + DOM numerals | Keep | Crisp text, cheap redraws, screen-reader friendly. |
| Kalman filter on speed | Refactor | Approach sound; tuning and noise model were wrong (see below). Moved into `engine.js`. |
| Permission gate | Refactor | Worked, but status and speed states were not separated. |
| Trip export (GPX/CSV) | Keep | Added speed to GPX, XML escaping, validated output. |
| Fix validation | Replace | Rejected Doppler speed whenever horizontal accuracy > 50 m; no duplicate, out-of-order or stale-cache checks. |
| Speed before first fix | Replace | **Bug:** showed "0 km/h", indistinguishable from a real stop. |
| Signal loss | Replace | **Bug:** last speed stayed on screen and kept being added to distance every frame. |
| Distance | Replace | Integrated per animation frame: frame-rate dependent, stopped in background tabs. |
| Parked drift | Replace | GPS noise at rest showed 1–3 km/h and added distance and moving time. |
| 0–100 timer | Replace | Used lagged filtered speed, started on first fix > 0.5 m/s, no reliability flag. |
| Overspeed alert | Replace | Beeped every 1.5 s indefinitely, no tolerance, onset delay or hysteresis; keyed off the animated needle. |
| Recording | Replace | Lost on refresh; no pause. |
| Rendering | Refactor | Gauge redrew 60×/s while parked. Now redraws only when something visible changes. |

## Key engineering decisions

**Engine separated from UI.** `engine.js` has no DOM access and runs in Node, so every measurement rule is unit-tested on traces with known ground truth (`tests/engine.test.js`).

**Signal states are explicit.** `acquiring → ok / poor → lost`. Speed is `null` (shown as "––") unless the last usable fix is under 4 s old. `poor` means accuracy worse than 30 m or position-derived speed.

**Doppler speed is trusted independently of position accuracy.** Its noise is scaled by accuracy above 20 m rather than rejected at a hard 50 m cut-off; it is ignored only beyond 100 m. Position-derived speed is a fallback that requires both fixes within 25 m and ≥ 0.8 s apart; displacement smaller than the combined error counts as standing still.

**Outliers.** A reading outside an 8σ innovation gate, or implying more than 1.2 g, is dropped once. A second reading in the same direction is accepted, so real hard braking shows within one extra fix.

**Standing still.** "Moving" starts after 3 consecutive estimates above 3.2 km/h or one above 9 km/h, and ends below 1.8 km/h. While stopped the display reads exactly 0.

**Distance.** Trapezoidal integration of filtered speed between fix timestamps, independent of frame rate. Gaps up to 5 s are integrated; gaps up to 60 s are bridged by the straight line between good fixes (undercounts curves slightly); longer gaps add nothing. Position summing is computed as a diagnostic only: on the 20-minute test drive it overcounts by 6.8% because of position jitter, against −0.04% for integration.

**0–100.** Uses raw Doppler readings (the filter's lag would bias the time). Arms after 1.5 s stationary. Start = zero-speed instant extrapolated from the first two moving readings. Finish = interpolation between the readings either side of the target. Marked *rough* if any fix gap > 1.2 s, accuracy > 20 m or non-Doppler speed occurred during the run. Shown with "±0.5 s", never as lab-grade.

**Overspeed.** Visual state switches as soon as speed passes limit × (1 + tolerance); sound and vibration follow after 1.5 s over, then at most once a minute. Exit needs a 3 km/h drop below the threshold. Lost signal clears the state. Alerts run on measured speed, not on the animated needle. Sound is never the only cue.

**Persistence.** Trip totals and any in-progress recording are saved every 10 s and on `pagehide`/`visibilitychange`, and restored on load. Recording supports pause/resume; paused time and distance are excluded.

**Permission flow — deliberate deviation.** The review recommends a user-initiated GPS start. The product owner explicitly asked for GPS to start on page load, so Pacer requests location immediately and shows an explanation card alongside the browser prompt. If permission is already granted there is no card at all. This is a product decision that can be flipped by removing the `startGps()` call from `bootLocation()` for the `prompt` state.

## Test results

`node tests/engine.test.js` — 21/21 pass (synthetic 1 Hz traces, Doppler noise σ 0.25 m/s, position noise σ 3–6 m).

| Scenario | Result |
|---|---|
| Parked 5 min | 0 km/h 100% of fixes, 0 m, 0 s moving |
| Cruise 90 km/h | bias −0.03 km/h, jitter ±0.85 km/h (raw ±0.90) |
| Accelerating 3 m/s² | no measurable lag at fix times (display spring adds ≈0.3 s settle) |
| 20 min mixed drive | distance −0.04% (position sum +6.8%), moving time +0.5% |
| 25 s tunnel | speed hidden, status "lost", distance −0.6% |
| +65 km/h single spike | display moved 1.9 km/h |
| No Doppler speed | fallback bias +1.2 km/h, jitter ±2.8 km/h, flagged "poor" |
| Duplicate / out-of-order / cached fixes | rejected |
| 0–100, true 9.26 s | 9.25 s reliable; with 2.5 s gap: flagged rough |
| Hovering ±1 km/h at limit | 1 alert in 60 s |

`python3 tests/ui_scenarios.py` — 22/22 pass in Chromium with an injected fake GPS: first launch, denied + recovery, acquisition, stationary, moving, poor reception, interruption and recovery, overspeed (single alert, no repeat near the limit, clears), recording across a page reload, trip totals across reload, GPX/CSV validity, HUD mirroring, night theme, 320×568, 844×390 landscape and 1024×768 layouts.

## Not yet verified

- **Real devices.** All tests use simulated traces and desktop Chromium. iOS Safari and Android Chrome on real hardware, ideally logged next to a reference GPS, are still needed to confirm the noise model and tuning.
- **Backgrounding.** Mobile browsers pause GPS for background tabs. Pacer saves state on hide and bridges gaps up to 60 s on return; longer background periods add no distance.
- **Landscape touch targets** are 40 px on the segmented controls (44 px elsewhere) to fit 390 px-tall screens.
