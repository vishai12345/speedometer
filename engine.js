/*!
 * Pacer measurement engine.
 * Pure logic, no DOM: GPS fix validation, speed filtering, signal state,
 * trip distance, 0-100 timing and overspeed alerting.
 * Runs in the browser (global PacerEngine) and in Node (module.exports) so it can be tested on recorded or synthetic traces.
 * All internal units are SI: metres, seconds, m/s. Timestamps are epoch milliseconds.
 */
(function (root) {
  "use strict";

  const DEFAULTS = {
    sigmaA: 1.5,          // m/s^2  process noise: how fast real speed can plausibly change
    dopplerSigma: 0.45,   // m/s    measurement noise of coords.speed with a good fix
    dopplerAccRef: 20,    // m      above this horizontal accuracy, Doppler noise is scaled up proportionally
    maxDopplerAcc: 100,   // m      Doppler speed from fixes worse than this is ignored
    maxDerivedAcc: 25,    // m      position-derived speed needs both fixes at least this good
    minDerivedDt: 0.8,    // s      ...and at least this far apart
    maxSpeed: 90,         // m/s    (324 km/h) anything faster is rejected as implausible
    maxAccel: 12,         // m/s^2  (1.2 g) a jump implying more than this is treated as a likely outlier
    gateSigma: 8,         // innovation gate width in standard deviations
    maxFixAgeMs: 5000,    // ms     fixes older than this when received are stale cache, not live data
    staleMs: 4000,        // ms     no usable fix for this long means the signal is lost
    poorAcc: 30,          // m      fixes worse than this are usable but flagged as poor
    moveOn: 0.9,          // m/s    (3.2 km/h) three consecutive estimates above this start "moving"
    moveSure: 2.5,        // m/s    (9 km/h) one estimate above this starts "moving" immediately
    moveOff: 0.5,         // m/s    (1.8 km/h) speed below which it counts as stopped (hysteresis)
    maxIntegrateGap: 5,   // s      integrate speed over gaps up to this long
    maxBridgeGap: 60,     // s      bridge longer gaps with straight-line distance between good fixes
    maxElapsedStep: 60,   // s      cap on elapsed time added for a single gap
    launchStill: 0.8,     // m/s    raw readings below this count as standing still (GPS noise at rest)
    launchMove: 1.2,      // m/s    a raw reading above this means the launch has begun
    launchArm: 1.5,       // s      must be stationary this long before a launch can be timed
    launchMaxGap: 1.2,    // s      a run with a fix gap longer than this is marked rough
    launchMaxAcc: 20,     // m      ...or with accuracy worse than this
    launchTimeout: 40     // s      give up if the target is not reached in this time
  };

  const toRad = d => d * Math.PI / 180;
  function haversine(a, b) {
    const R = 6371008.8, dφ = toRad(b.lat - a.lat), dλ = toRad(b.lon - a.lon);
    const h = Math.sin(dφ / 2) ** 2 + Math.cos(toRad(a.lat)) * Math.cos(toRad(b.lat)) * Math.sin(dλ / 2) ** 2;
    return 2 * R * Math.atan2(Math.sqrt(h), Math.sqrt(1 - h));
  }
  function bearing(a, b) {
    const φ1 = toRad(a.lat), φ2 = toRad(b.lat), dλ = toRad(b.lon - a.lon);
    const y = Math.sin(dλ) * Math.cos(φ2), x = Math.cos(φ1) * Math.sin(φ2) - Math.sin(φ1) * Math.cos(φ2) * Math.cos(dλ);
    return (Math.atan2(y, x) * 180 / Math.PI + 360) % 360;
  }

  /** One-dimensional Kalman filter on speed, modelled as a random walk driven by acceleration noise. */
  class SpeedKalman {
    constructor(sigmaA) { this.sa2 = sigmaA * sigmaA; this.reset(); }
    reset() { this.x = 0; this.P = 100; this.t = null; }
    predictVar(t) { const dt = this.t === null ? 0 : Math.max(0, (t - this.t) / 1000); return this.P + this.sa2 * dt * dt; }
    update(z, r, t) {
      if (this.t === null) { this.x = z; this.P = r; this.t = t; return z; }
      const dt = Math.min(Math.max((t - this.t) / 1000, 0.001), 30);
      this.t = t;
      this.P += this.sa2 * dt * dt;
      const K = this.P / (this.P + r);
      this.x = Math.max(0, this.x + K * (z - this.x));
      this.P *= 1 - K;
      return this.x;
    }
  }

  /**
   * Measures a standstill-to-target launch from raw (unfiltered) speed readings.
   * Raw readings are used because the filter adds lag that would bias the time.
   * Start and end instants are linearly interpolated between fixes to remove sampling delay.
   */
  class LaunchTimer {
    constructor(cfg) { this.cfg = cfg; this.target = 100 / 3.6; this.reset(); this.last = null; this.best = null; }
    reset() { this.phase = "idle"; this.stillSince = null; this.prev = null; this.t0 = null; this.maxGap = 0; this.rough = false; }
    setTarget(ms) { if (Math.abs(ms - this.target) > 1e-6) { this.target = ms; this.last = null; this.best = null; this.reset(); } }
    feed(z, t, acc, src) {
      const c = this.cfg, prev = this.prev;
      this.prev = { z, t };
      if (z === null || src !== "doppler") { if (this.phase === "running" || this.phase === "starting") this.rough = true; return; }
      if (z < c.launchStill) {
        if (this.phase === "running" || this.phase === "starting") this.phase = "idle";
        if (this.stillSince === null) this.stillSince = t;
        this.lastStill = { z, t };
        if ((t - this.stillSince) / 1000 >= c.launchArm) this.phase = "armed";
        return;
      }
      this.stillSince = null;
      if (this.phase === "armed") {
        if (z < c.launchMove) return;
        // First moving reading. The start instant is estimated on the next reading by extrapolating back to zero speed.
        this.phase = "starting"; this.m1 = { z, t }; this.maxGap = (t - this.lastStill.t) / 1000; this.rough = false;
        return;
      }
      if (this.phase === "starting") {
        const m1 = this.m1, slope = (z - m1.z) / ((t - m1.t) / 1000);
        let t0 = slope > 0.5 ? m1.t - (m1.z / slope) * 1000 : this.lastStill.t;
        this.t0 = Math.min(Math.max(t0, this.lastStill.t), m1.t);
        this.phase = "running";
      }
      if (this.phase !== "running") return;
      if (prev) this.maxGap = Math.max(this.maxGap, (t - prev.t) / 1000);
      if (acc != null && acc > c.launchMaxAcc) this.rough = true;
      if ((t - this.t0) / 1000 > c.launchTimeout) { this.phase = "idle"; return; }
      if (z >= this.target && prev && prev.z !== null) {
        const f = prev.z >= this.target ? 0 : (this.target - prev.z) / (z - prev.z);
        const tEnd = prev.t + f * (t - prev.t);
        const secs = (tEnd - this.t0) / 1000;
        const reliable = !this.rough && this.maxGap <= c.launchMaxGap;
        this.last = { secs, reliable, at: t };
        if (reliable && (!this.best || secs < this.best.secs)) this.best = { secs, reliable, at: t };
        this.phase = "idle";
      }
    }
    running(now) { return this.phase === "running" ? Math.max(0, (now - this.t0) / 1000) : this.phase === "starting" ? 0 : null; }
    armed() { return this.phase === "armed"; }
  }

  class Engine {
    constructor(opts) {
      this.cfg = Object.assign({}, DEFAULTS, opts || {});
      this.kf = new SpeedKalman(this.cfg.sigmaA);
      this.launch = new LaunchTimer(this.cfg);
      this.started = false;
      this.resetSignal();
      this.resetTrip();
    }
    start() { this.started = true; }
    resetSignal() {
      this.kf.reset();
      this.lastFix = null;          // last fix that passed timestamp checks (any quality)
      this.lastPos = null;          // last fix with a usable position
      this.lastAccepted = null;     // { t, est, acc, src } of the last fix that updated speed
      this.lastRecvPerf = null;
      this.moving = false;
      this.heading = null; this.altitude = null; this.accel = 0;
      this.rejects = { stale: 0, duplicate: 0, implausible: 0, inaccurate: 0, outlier: 0 };
    }
    resetTrip() {
      this.trip = { dist: 0, distPos: 0, moving: 0, elapsed: 0, max: 0, bins: new Array(91).fill(0) };
    }
    exportTrip() { return JSON.parse(JSON.stringify(this.trip)); }
    importTrip(t) {
      if (!t || typeof t.dist !== "number") return;
      this.resetTrip();
      Object.assign(this.trip, t);
      if (!Array.isArray(this.trip.bins) || this.trip.bins.length !== 91) this.trip.bins = new Array(91).fill(0);
    }

    /**
     * Feed one fix. fix = { t, lat, lon, acc, speed, heading, alt }; now = Date.now() at receipt.
     * Returns { ok, reason, src } describing what happened, for diagnostics and tests.
     */
    ingest(fix, now) {
      const c = this.cfg;
      if (!fix || !Number.isFinite(fix.t)) return { ok: false, reason: "invalid" };
      if (now - fix.t > c.maxFixAgeMs) { this.rejects.stale++; return { ok: false, reason: "stale" }; }
      if (this.lastFix && fix.t <= this.lastFix.t) { this.rejects.duplicate++; return { ok: false, reason: "duplicate" }; }
      this.lastFix = fix;
      const hasPos = Number.isFinite(fix.lat) && Number.isFinite(fix.lon);
      const acc = Number.isFinite(fix.acc) ? fix.acc : null;

      // ---- pick a speed measurement z with variance r ----
      let z = null, r = null, src = null;
      if (Number.isFinite(fix.speed) && fix.speed >= 0 && (acc === null || acc <= c.maxDopplerAcc)) {
        const scale = acc === null ? 1.5 : Math.max(1, acc / c.dopplerAccRef);
        z = fix.speed; r = (c.dopplerSigma * scale) ** 2; src = "doppler";
      } else if (hasPos && this.lastPos && acc !== null && acc <= c.maxDerivedAcc && this.lastPos.acc <= c.maxDerivedAcc) {
        const dt = (fix.t - this.lastPos.t) / 1000;
        if (dt >= c.minDerivedDt && dt <= 10) {
          const d = haversine(this.lastPos, fix), err = Math.hypot(acc, this.lastPos.acc);
          // Displacement smaller than the combined position error is indistinguishable from standing still.
          z = d <= err * 0.5 ? 0 : d / dt;
          r = (err / dt) ** 2 + 0.25; src = "derived";
        }
      } else if (acc !== null && acc > c.maxDerivedAcc && !Number.isFinite(fix.speed)) {
        this.rejects.inaccurate++;
      }

      // ---- heading / altitude (independent of speed acceptance) ----
      if (hasPos) {
        if (Number.isFinite(fix.heading) && (fix.speed == null || fix.speed > c.moveOn)) this.heading = fix.heading;
        else if (this.lastPos && acc !== null && acc <= c.maxDerivedAcc) {
          const d = haversine(this.lastPos, fix);
          if (d > Math.max(8, 2 * acc)) this.heading = bearing(this.lastPos, fix);
        }
        if (Number.isFinite(fix.alt)) this.altitude = fix.alt;
      }

      if (z !== null && z > c.maxSpeed) { this.rejects.implausible++; z = null; }

      // Raw speed goes to the launch timer before filtering (filter lag would bias the time).
      this.launch.feed(z, fix.t, acc, src);

      if (z === null) { if (hasPos && acc !== null && acc <= c.maxDerivedAcc) this.lastPos = { t: fix.t, lat: fix.lat, lon: fix.lon, acc }; return { ok: false, reason: "no-speed", src }; }

      // ---- outlier handling: innovation gate + physical plausibility ----
      const prevA = this.lastAccepted;
      if (prevA) {
        const dt = Math.max((fix.t - prevA.t) / 1000, 0.001);
        const S = Math.sqrt(this.kf.predictVar(fix.t) + r);
        const jump = Math.abs(z - this.kf.x);
        if (jump > c.gateSigma * S || (dt < 3 && jump / dt > c.maxAccel)) {
          // One isolated suspicious reading is dropped. A second one in the same direction means the
          // change is real (e.g. hard braking), so it is accepted: worst case adds one fix of latency.
          const dir = Math.sign(z - this.kf.x);
          if (this._suspect !== dir) { this._suspect = dir; this.rejects.outlier++; return { ok: false, reason: "outlier", src }; }
          this._suspect = 0;
        } else this._suspect = 0;
      }

      const est = this.kf.update(z, r, fix.t);
      this.lastRecvPerf = now;

      // ---- moving state with hysteresis ----
      this._votes = est > c.moveOn ? (this._votes || 0) + 1 : 0;
      if (!this.moving && (this._votes >= 3 || est > c.moveSure)) this.moving = true;
      else if (this.moving && est < c.moveOff) this.moving = false;
      const shown = this.moving ? est : 0;

      // ---- trip integration on fix timestamps (frame-rate independent) ----
      if (prevA) {
        const dt = (fix.t - prevA.t) / 1000;
        const prevShown = prevA.shown;
        let d = 0;
        if (dt <= c.maxIntegrateGap) d = (prevShown + shown) / 2 * dt;
        else if (dt <= c.maxBridgeGap && hasPos && prevA.pos && acc !== null && acc <= c.maxDerivedAcc && prevA.pos.acc <= c.maxDerivedAcc) {
          const straight = haversine(prevA.pos, fix);
          d = straight > Math.hypot(acc, prevA.pos.acc) ? straight : 0;
        }
        this.trip.dist += d;
        if (d > 0 || this.moving) this.trip.moving += Math.min(dt, c.maxBridgeGap);
        this.trip.elapsed += Math.min(dt, c.maxElapsedStep);
        if (this.moving && dt <= c.maxIntegrateGap) {
          const bin = Math.min(90, Math.floor(shown));
          this.trip.bins[bin] += dt;
        }
        if (dt > 0.05 && dt <= 2) this.accel += 0.5 * ((est - prevA.est) / dt - this.accel);
        else if (dt > 2) this.accel = 0;
        // Diagnostic only: summed distance between successive good positions (overcounts jitter).
        if (hasPos && prevA.pos && acc !== null && acc <= c.maxDerivedAcc) this.trip.distPos += haversine(prevA.pos, fix);
      }
      const quality = this.qualityOf(acc, src);
      if (quality === "ok" && shown > this.trip.max) this.trip.max = shown;

      const pos = hasPos && acc !== null ? { lat: fix.lat, lon: fix.lon, acc } : null;
      if (hasPos && acc !== null && acc <= c.maxDerivedAcc) this.lastPos = { t: fix.t, lat: fix.lat, lon: fix.lon, acc };
      this.lastAccepted = { t: fix.t, est, shown, acc, src, pos };
      return { ok: true, src, est, shown };
    }

    qualityOf(acc, src) {
      if (src === "derived") return "poor";
      if (acc !== null && acc > this.cfg.poorAcc) return "poor";
      return "ok";
    }

    /** Signal state: idle | acquiring | ok | poor | lost. `now` is Date.now(). */
    status(now) {
      if (!this.started) return "idle";
      if (!this.lastAccepted) return "acquiring";
      if (now - this.lastRecvPerf > this.cfg.staleMs) return "lost";
      return this.qualityOf(this.lastAccepted.acc, this.lastAccepted.src);
    }
    /** Speed to show, in m/s, or null when there is no trustworthy current measurement. */
    speed(now) {
      const s = this.status(now);
      if (s !== "ok" && s !== "poor") return null;
      return this.moving ? this.kf.x : 0;
    }
    accuracy() { return this.lastAccepted ? this.lastAccepted.acc : null; }
  }

  /**
   * Overspeed alerting with tolerance, onset delay and exit hysteresis.
   * Visual state switches immediately past the threshold; the audible alert fires only after
   * the speed has stayed over for onsetMs, then repeats at most every repeatMs.
   */
  class OverspeedMonitor {
    constructor(o) {
      o = o || {};
      this.onsetMs = o.onsetMs ?? 1500;
      this.repeatMs = o.repeatMs ?? 60000;
      this.hyst = o.hyst ?? 0.83;   // m/s (3 km/h) must drop this far below the threshold to clear
      this.reset();
    }
    reset() { this.over = false; this.since = null; this.lastFire = null; }
    update(speed, limit, tolFrac, now) {
      if (speed === null || !(limit > 0)) { this.reset(); return { over: false, fire: false, near: false }; }
      const threshold = limit * (1 + (tolFrac || 0));
      if (!this.over && speed > threshold) { this.over = true; this.since = now; }
      else if (this.over && speed < threshold - this.hyst) { this.reset(); }
      let fire = false;
      if (this.over && now - this.since >= this.onsetMs && (this.lastFire === null || now - this.lastFire >= this.repeatMs)) {
        fire = true; this.lastFire = now;
      }
      return { over: this.over, fire, near: !this.over && speed >= limit * 0.9 };
    }
  }

  const api = { Engine, SpeedKalman, LaunchTimer, OverspeedMonitor, haversine, bearing, DEFAULTS };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.PacerEngine = api;
})(typeof self !== "undefined" ? self : this);
