// Validates the measurement engine against synthetic GPS traces with known ground truth.
// Run: node tests/engine.test.js
"use strict";
const assert = require("assert");
const { Engine, OverspeedMonitor, haversine } = require("../engine.js");

// ---------- deterministic noise ----------
let seed = 42;
const rand = () => { seed = (seed * 1664525 + 1013904223) % 4294967296; return seed / 4294967296; };
const gauss = () => { let u = 0, v = 0; while (!u) u = rand(); while (!v) v = rand(); return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v); };

/**
 * Build a trace from a true speed profile.
 * profile(t) -> true speed m/s. Path curves gently so position-summed distance is tested on a non-straight route.
 * Options: hz, dur (s), dopplerNoise (m/s), posNoise (m), acc (m), drop: [[t0,t1]] seconds with no fixes,
 * noDoppler: true to report speed=null, spikes: {t: extraSpeed}.
 */
function makeTrace(profile, o) {
  o = Object.assign({ hz: 1, dur: 120, dopplerNoise: 0.25, posNoise: 3, acc: 5, drop: [], noDoppler: false, spikes: {} }, o);
  const dtSim = 0.01, T0 = 1_700_000_000_000;
  let lat = 12.9716, lon = 77.5946, hdg = 30, dist = 0;
  const fixes = [], truth = [];
  let nextFix = 0;
  for (let t = 0; t <= o.dur + 1e-9; t += dtSim) {
    const v = profile(t);
    const d = v * dtSim; dist += d;
    hdg += 0.6 * dtSim * Math.min(v / 10, 1) * Math.sin(t / 25) * 6;
    lat += d * Math.cos(hdg * Math.PI / 180) / 111320;
    lon += d * Math.sin(hdg * Math.PI / 180) / (111320 * Math.cos(lat * Math.PI / 180));
    if (t + 1e-9 >= nextFix) {
      nextFix += 1 / o.hz;
      const dropped = o.drop.some(([a, b]) => t >= a && t < b);
      truth.push({ t, v, dist });
      if (dropped) continue;
      const ms = T0 + Math.round(t * 1000);
      let sp = o.noDoppler ? null : Math.max(0, v + gauss() * o.dopplerNoise + (v < 0.05 ? Math.abs(gauss()) * 0.35 : 0));
      const key = Math.round(t);
      if (o.spikes[key] !== undefined && Math.abs(t - key) < 1e-6 && sp !== null) sp += o.spikes[key];
      fixes.push({ t: ms, lat: lat + gauss() * o.posNoise / 111320, lon: lon + gauss() * o.posNoise / (111320 * Math.cos(lat * Math.PI / 180)), acc: o.acc, speed: sp, heading: v > 1 ? hdg : NaN, alt: 900 });
    }
  }
  return { fixes, truth, totalDist: dist, T0 };
}

function run(trace, opts) {
  const e = new Engine(opts); e.start();
  const out = [];
  for (const f of trace.fixes) { const now = f.t + 150; e.ingest(f, now); out.push({ t: (f.t - trace.T0) / 1000, shown: e.speed(now), est: e.kf.x, status: e.status(now) }); }
  return { e, out };
}

const results = [];
function check(name, cond, detail) { results.push({ name, pass: !!cond, detail }); }

// ---------- 1. Stationary: no phantom speed, no phantom distance ----------
{
  const tr = makeTrace(() => 0, { dur: 300, posNoise: 6, acc: 10 });
  const { e, out } = run(tr);
  const zeroFrac = out.filter(o => o.shown === 0).length / out.length;
  check("Stationary: speed reads 0", zeroFrac >= 0.98, `${(zeroFrac * 100).toFixed(1)}% of fixes show 0`);
  check("Stationary: distance stays near 0", e.trip.dist < 5, `${e.trip.dist.toFixed(2)} m over 5 min (position-sum would be ${e.trip.distPos.toFixed(0)} m)`);
  check("Stationary: no moving time", e.trip.moving < 5, `${e.trip.moving.toFixed(1)} s`);
}

// ---------- 2. Constant cruise accuracy ----------
{
  const tr = makeTrace(() => 25, { dur: 180 });
  const { out } = run(tr);
  const s = out.slice(5).map(o => o.shown - 25);
  const mean = s.reduce((a, b) => a + b, 0) / s.length, sd = Math.sqrt(s.reduce((a, b) => a + (b - mean) ** 2, 0) / s.length);
  check("Cruise 90 km/h: bias", Math.abs(mean) < 0.15, `${(mean * 3.6).toFixed(2)} km/h`);
  check("Cruise 90 km/h: jitter", sd < 0.3, `±${(sd * 3.6).toFixed(2)} km/h (raw GPS noise ±${(0.25 * 3.6).toFixed(2)})`);
}

// ---------- 3. Latency during acceleration ----------
{
  const prof = t => t < 10 ? 0 : t < 20 ? (t - 10) * 3 : 30;
  const tr = makeTrace(prof, { dur: 40, hz: 1 });
  const { out } = run(tr);
  // find time shift that best aligns filtered speed with truth during the ramp
  let best = { lag: 0, err: Infinity };
  for (let lag = 0; lag <= 2; lag += 0.05) {
    let err = 0, n = 0;
    for (const o of out) if (o.t >= 12 && o.t <= 19) { const tv = prof(o.t - lag); err += (o.shown - tv) ** 2; n++; }
    if (err / n < best.err) best = { lag, err: err / n };
  }
  check("Acceleration: filter lag", best.lag <= 0.5, `${best.lag.toFixed(2)} s behind true speed at 3 m/s²`);
}

// ---------- 4. Full drive distance ----------
{
  const prof = t => { // urban + highway + stops
    const c = t % 200;
    if (c < 10) return 0; if (c < 20) return (c - 10) * 1.4; if (c < 80) return 14; if (c < 90) return 14 - (c - 80) * 1.4;
    if (c < 100) return 0; if (c < 115) return (c - 100) * 2; if (c < 180) return 30; return Math.max(0, 30 - (c - 180) * 1.5);
  };
  const tr = makeTrace(prof, { dur: 1200 });
  const { e } = run(tr);
  const errInt = (e.trip.dist - tr.totalDist) / tr.totalDist, errPos = (e.trip.distPos - tr.totalDist) / tr.totalDist;
  check("20-min drive: distance error (speed integration)", Math.abs(errInt) < 0.01, `${(errInt * 100).toFixed(2)}% of ${(tr.totalDist / 1000).toFixed(2)} km`);
  results.push({ name: "20-min drive: distance error (position sum, for comparison)", pass: null, detail: `${(errPos * 100).toFixed(2)}%` });
  const movingTruth = tr.truth.filter(x => x.v > 0.5).length; // "moving" = above ~2 km/h
  check("20-min drive: moving time", Math.abs(e.trip.moving - movingTruth) / movingTruth < 0.03, `${e.trip.moving.toFixed(0)} s vs ${movingTruth} s true`);
}

// ---------- 5. Signal loss (tunnel) ----------
{
  const tr = makeTrace(() => 20, { dur: 120, drop: [[50, 75]] });
  const e = new Engine(); e.start();
  let nullDuringGap = true, sawLost = false;
  const T0 = tr.T0; let fi = 0;
  for (let t = 0; t <= 120; t += 0.5) {
    const now = T0 + t * 1000;
    while (fi < tr.fixes.length && tr.fixes[fi].t <= now) { e.ingest(tr.fixes[fi], now); fi++; }
    if (t > 56 && t < 75) { if (e.speed(now) !== null) nullDuringGap = false; if (e.status(now) === "lost") sawLost = true; }
  }
  check("Tunnel: no stale speed shown during loss", nullDuringGap && sawLost, sawLost ? "status 'lost', speed hidden" : "never reported lost");
  const err = (e.trip.dist - tr.totalDist) / tr.totalDist;
  check("Tunnel: distance bridged across 25 s gap", Math.abs(err) < 0.03, `${(err * 100).toFixed(2)}%`);
}

// ---------- 6. Outlier spike ----------
{
  const tr = makeTrace(() => 15, { dur: 60, spikes: { 30: 18 } });
  const { out } = run(tr);
  const at = out.find(o => Math.abs(o.t - 30) < 0.01);
  check("Single bad reading (+65 km/h spike)", Math.abs(at.shown - 15) < 1.5, `displayed moved ${((at.shown - 15) * 3.6).toFixed(1)} km/h`);
}

// ---------- 7. Doppler missing: position-derived fallback ----------
{
  const tr = makeTrace(() => 20, { dur: 120, noDoppler: true, posNoise: 3, acc: 5 });
  const { out } = run(tr);
  const s = out.slice(10).map(o => o.shown - 20);
  const mean = s.reduce((a, b) => a + b, 0) / s.length, sd = Math.sqrt(s.reduce((a, b) => a + (b - mean) ** 2, 0) / s.length);
  check("No Doppler speed: fallback works", Math.abs(mean) < 0.5 && sd < 1.5, `bias ${(mean * 3.6).toFixed(2)} km/h, jitter ±${(sd * 3.6).toFixed(2)} km/h`);
  check("No Doppler speed: flagged as poor", out.slice(10).every(o => o.status === "poor"), "status 'poor'");
}

// ---------- 8. Bad timestamps ----------
{
  const e = new Engine(); e.start(); const T = 1_700_000_000_000;
  e.ingest({ t: T, lat: 1, lon: 1, acc: 5, speed: 10 }, T + 100);
  const dup = e.ingest({ t: T, lat: 1, lon: 1, acc: 5, speed: 30 }, T + 200);
  const old = e.ingest({ t: T - 500, lat: 1, lon: 1, acc: 5, speed: 30 }, T + 300);
  const cached = e.ingest({ t: T - 60000, lat: 1, lon: 1, acc: 5, speed: 30 }, T + 400);
  check("Duplicate / out-of-order / cached fixes rejected", dup.reason === "duplicate" && old.reason === "duplicate" && cached.reason === "stale", `${dup.reason}, ${old.reason}, ${cached.reason}`);
  const e2 = new Engine(); e2.start();
  check("Before first fix: speed is unknown, not 0", e2.speed(T) === null && e2.status(T) === "acquiring", `status '${e2.status(T)}', speed ${e2.speed(T)}`);
}

// ---------- 9. 0-100 km/h ----------
{
  const a = 3; // m/s^2  -> true 0-100 = 9.26 s
  const prof = t => t < 10 ? 0 : Math.min((t - 10) * a, 33);
  const truthSecs = (100 / 3.6) / a;
  const tr = makeTrace(prof, { dur: 40 });
  const { e } = run(tr);
  const L = e.launch.last;
  check("0-100 km/h timing (1 Hz GPS)", L && Math.abs(L.secs - truthSecs) < 0.6 && L.reliable, L ? `${L.secs.toFixed(2)} s vs ${truthSecs.toFixed(2)} s true, ${L.reliable ? "reliable" : "rough"}` : "no result");
  const tr2 = makeTrace(prof, { dur: 40, drop: [[13, 15.5]] });
  const r2 = run(tr2).e.launch.last;
  check("0-100 with a GPS gap is flagged rough", r2 && !r2.reliable, r2 ? `${r2.secs.toFixed(2)} s, ${r2.reliable ? "reliable" : "rough"}` : "no result");
}

// ---------- 10. Overspeed alerting ----------
{
  const m = new OverspeedMonitor(); const lim = 100 / 3.6; let fires = 0, t = 0;
  for (; t < 60000; t += 1000) { const v = lim + (Math.sin(t / 3000) * 1.2 + gauss() * 0.3) / 3.6; if (m.update(v, lim, 0, t).fire) fires++; }
  check("Hovering ±1 km/h around the limit", fires <= 1, `${fires} alert(s) in 60 s`);
  const m2 = new OverspeedMonitor(); let f2 = 0;
  for (t = 0; t < 65000; t += 1000) if (m2.update(lim + 3, lim, 0, t).fire) f2++;
  check("Sustained overspeed repeats gently", f2 === 2, `${f2} alerts in 65 s (first after 1.5 s, then once a minute)`);
  const m3 = new OverspeedMonitor(); m3.update(lim + 5, lim, 0, 0); m3.update(lim + 5, lim, 0, 2000);
  const r3 = m3.update(null, lim, 0, 3000);
  check("Signal lost clears overspeed", !r3.over && !r3.fire, "cleared");
  const m4 = new OverspeedMonitor(); m4.update(lim * 1.04, lim, 0.05, 0); const r4 = m4.update(lim * 1.04, lim, 0.05, 5000);
  check("Tolerance respected (+4% under +5% tolerance)", !r4.over, "no alert");
}

// ---------- 11. Long gap: estimator reset, no unbounded integration ----------
{
  // 20 m/s, then a 3-minute gap (backgrounded / no GPS), car is doing 10 m/s when fixes resume
  const prof = t => t < 60 ? 20 : 10;
  const tr = makeTrace(prof, { dur: 300, drop: [[60, 240]] });
  const e = new Engine(); e.start();
  let distBefore = null, movingBefore = null, firstAfter = null;
  for (const f of tr.fixes) {
    const tt = (f.t - tr.T0) / 1000;
    if (tt >= 240 && distBefore === null) { distBefore = e.trip.dist; movingBefore = e.trip.moving; }
    e.ingest(f, f.t + 150);
    if (tt >= 240 && firstAfter === null) firstAfter = e.speed(f.t + 150);
  }
  check("Long gap (3 min): estimator restarts on fresh data", e.resets === 1 && Math.abs(firstAfter - 10) < 1.5, `first speed after gap ${(firstAfter * 3.6).toFixed(1)} km/h (true 36, before gap 72)`);
  const gapDist = e.trip.dist - distBefore - 59 * 10;  // after-gap fixes contribute ~59 s x 10 m/s
  check("Long gap (3 min): no distance or moving time invented", Math.abs(gapDist) < 15 && e.trip.moving - movingBefore < 62, `${gapDist.toFixed(1)} m beyond the post-gap driving, moving +${(e.trip.moving - movingBefore).toFixed(0)} s for 60 s of post-gap driving`);
}
{
  // parked phone, page backgrounded for 2 minutes
  const tr = makeTrace(() => 0, { dur: 240, posNoise: 6, acc: 10, drop: [[60, 180]] });
  const { e } = run(tr);
  check("Parked + 2 min in background: no distance", e.trip.dist < 2, `${e.trip.dist.toFixed(2)} m`);
}
{
  // 30 s gap is bridged by the straight line, and speed is right on the first fix after it
  const tr = makeTrace(() => 18, { dur: 120, drop: [[40, 70]] });
  const e = new Engine(); e.start(); let first = null;
  for (const f of tr.fixes) { e.ingest(f, f.t + 150); if ((f.t - tr.T0) / 1000 >= 70 && first === null) first = e.speed(f.t + 150); }
  const err = (e.trip.dist - tr.totalDist) / tr.totalDist;
  check("30 s gap: bridged, no speed jump on recovery", Math.abs(err) < 0.03 && Math.abs(first - 18) < 1, `distance ${(err * 100).toFixed(2)}%, first speed ${(first * 3.6).toFixed(1)} km/h (true 64.8)`);
}

// ---------- 12. Frame-rate independence (engine has no clock of its own) ----------
{
  const tr = makeTrace(t => 15 + 5 * Math.sin(t / 10), { dur: 120 });
  const a = run(tr).e.trip.dist;
  // same fixes, but with status/speed polled 1000 times between fixes, as a fast render loop would
  const e = new Engine(); e.start();
  for (const f of tr.fixes) { e.ingest(f, f.t + 150); for (let k = 0; k < 1000; k++) e.speed(f.t + 150 + k); }
  check("Distance independent of how often the UI reads it", Math.abs(e.trip.dist - a) < 1e-9, `${a.toFixed(3)} m both ways`);
}

// ---------- report ----------
const w = Math.max(...results.map(r => r.name.length));
let failed = 0;
for (const r of results) {
  const mark = r.pass === null ? "info" : r.pass ? "PASS" : "FAIL";
  if (r.pass === false) failed++;
  console.log(`${mark.padEnd(4)}  ${r.name.padEnd(w)}  ${r.detail}`);
}
console.log(`\n${results.filter(r => r.pass).length} passed, ${failed} failed`);
if (failed) process.exit(1);
