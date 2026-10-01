# Mandatory regression suite for Pacer (third review, section 5) plus alert edge cases.
# Runs in Chromium with a controllable fake GPS injected before page load, so every scenario feeds exact fixes in real time.
# Usage: python3 -m http.server 8765 &   python3 tests/ui_scenarios.py [screenshot_dir] [fonts_dir]
# fonts_dir (optional): a folder of @fontsource packages; Google Fonts requests are then served locally so screenshots use the real typefaces.
import sys, os, glob, json, xml.etree.ElementTree as ET
from playwright.sync_api import sync_playwright

URL = "http://localhost:8765/index.html"
OUT = sys.argv[1] if len(sys.argv) > 1 else "."
FONTS = sys.argv[2] if len(sys.argv) > 2 else None
IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
os.makedirs(OUT, exist_ok=True)

FAKE = """
(() => {
  const w = {}; let n = 0;
  window.__vib = 0; window.__watchCalls = 0;
  window.__permState = window.__permState || 'granted';
  navigator.vibrate = () => { window.__vib++; return true; };
  const geo = navigator.geolocation;
  geo.watchPosition = (ok, err) => { n++; window.__watchCalls++; w[n] = { ok, err }; return n; };
  geo.clearWatch = id => { delete w[id]; };
  window.__watchers = () => Object.keys(w).length;
  window.__fix = f => Object.values(w).forEach(x => x.ok({ timestamp: Date.now(), coords: {
      latitude: f.lat, longitude: f.lon, accuracy: f.acc ?? 5, speed: f.speed ?? null, heading: f.heading ?? null, altitude: f.alt ?? 900 } }));
  window.__err = code => Object.values(w).forEach(x => x.err({ code }));
  const q = navigator.permissions.query.bind(navigator.permissions);
  navigator.permissions.query = d => d && d.name === 'geolocation'
    ? (window.__noPermApi ? Promise.reject(new TypeError('unsupported'))
       : new Promise(res => setTimeout(() => res({ state: window.__permState, onchange: null }), window.__permDelay || 0)))
    : q(d);
  if (window.__denyWake) navigator.wakeLock = { request: () => Promise.reject(new DOMException('denied', 'NotAllowedError')) };
})();
"""

def font_css():
    if not FONTS: return None
    faces = []
    for pkg, fam in [("saira-condensed", "Saira Condensed"), ("ibm-plex-sans", "IBM Plex Sans"), ("ibm-plex-mono", "IBM Plex Mono")]:
        for f in glob.glob(f"{FONTS}/fontsource-{pkg}-*/files/{pkg}-latin-*-normal.woff2"):
            wt = f.split("-latin-")[1].split("-")[0]
            faces.append(f"@font-face{{font-family:'{fam}';font-weight:{wt};font-style:normal;src:url(/__font/{os.path.basename(os.path.dirname(os.path.dirname(f)))}/{os.path.basename(f)}) format('woff2')}}")
    return "\n".join(faces)

results = []
def check(rid, name, cond, detail=""):
    results.append((rid, name, bool(cond), detail))

class Drive:
    def __init__(self, page): self.p = page; self.lat = 12.9716; self.lon = 77.5946
    def tick(self, speed, acc=5, n=1, jitter=0.0):
        import random
        for _ in range(n):
            self.lat += speed / 111320 + (random.uniform(-jitter, jitter) / 111320 if jitter else 0)
            self.p.evaluate(f"window.__fix({{lat:{self.lat},lon:{self.lon},acc:{acc},speed:{speed},heading:0}})")
            self.p.wait_for_timeout(1000)

t = lambda p, s: p.inner_text(s).strip()
J = lambda p, k: p.evaluate(f"JSON.parse(localStorage.getItem('pacer.{k}'))")
flush = lambda p: p.evaluate("window.dispatchEvent(new Event('pagehide'))")

with sync_playwright() as pw:
    b = pw.chromium.launch()
    css = font_css()
    errors = []

    def ctx(perm="granted", vw=390, vh=844, extra="", **kw):
        c = b.new_context(viewport={"width": vw, "height": vh}, device_scale_factor=2, user_agent=IPHONE, **kw)
        c.add_init_script(f"window.__permState='{perm}';{extra}" + FAKE)
        if css:
            c.route("https://fonts.googleapis.com/**", lambda r: r.fulfill(status=200, content_type="text/css", body=css))
            c.route("**/__font/**", lambda r: r.fulfill(status=200, content_type="font/woff2", body=open(glob.glob(f"{FONTS}/{r.request.url.split('/__font/')[1].split('/')[0]}/files/{r.request.url.split('/')[-1]}")[0], "rb").read()))
        else:
            c.route("https://fonts.googleapis.com/**", lambda r: r.fulfill(status=200, content_type="text/css", body=""))
        return c
    def page(c):
        p = c.new_page(); p.on("pageerror", lambda e: errors.append(str(e))); return p
    def shot(p, name, full=False, el=None):
        (p.locator(el) if el else p).screenshot(path=f"{OUT}/{name}.png", **({} if el else {"full_page": full}))

    # R1 Initial page load: no unsolicited prompt
    c = ctx("prompt"); p = page(c); p.goto(URL); p.wait_for_timeout(1500)
    check("R1", "Initial page load", p.evaluate("window.__watchCalls") == 0 and t(p, "#gateTitle") == "Turn on location to start",
          f"watchPosition calls on load: {p.evaluate('window.__watchCalls')}; card: '{t(p, '#gateTitle')}'")
    shot(p, "01_first_launch")
    # R2 Enable location: exactly one acquisition flow, even with repeated taps
    p.evaluate("const b=document.getElementById('gateBtn'); b.click(); b.click(); b.click();")
    p.wait_for_timeout(300)
    check("R2", "Enable location (tapped 3x)", p.evaluate("window.__watchers()") == 1 and t(p, "#gateTitle") == "Allow location",
          f"active watchers: {p.evaluate('window.__watchers()')}, state: '{t(p, '#gateTitle')}'")
    d = Drive(p); d.tick(0, n=2)
    check("R2b", "First fix unlocks the speedometer", p.locator("#gate").is_hidden() and t(p, "#speedNum") == "0", f"speed '{t(p, '#speedNum')}'")
    c.close()

    # R1b Returning user with permission already granted: starts without a card
    c = ctx("granted"); p = page(c); p.goto(URL); p.wait_for_timeout(800)
    check("R1b", "Returning user (already allowed)", p.evaluate("window.__watchCalls") == 1 and p.locator("#gate").is_hidden() and t(p, "#gpsText") == "Finding GPS",
          f"started automatically, no card, status '{t(p, '#gpsText')}', speed '{t(p, '#speedNum')}'")
    c.close()

    # L1-L4 Loader while the permission state is checked
    c = ctx("granted", extra="window.__permDelay=900;"); p = page(c); p.goto(URL); p.wait_for_timeout(250)
    during = (t(p, "#gateTitle"), p.is_visible("#gate"), p.evaluate("document.querySelector('.stage').inert"), p.evaluate("window.__watchCalls"), t(p, "#gpsText"))
    shot(p, "00_checking")
    p.wait_for_timeout(1300)
    check("L1", "Loader while checking, then speedometer (allowed)", during[0] == "Checking location access" and during[1] and during[2] and during[3] == 0
          and p.locator("#gate").is_hidden() and p.evaluate("window.__watchCalls") == 1 and not p.evaluate("document.querySelector('.stage').inert"),
          f"during check: loader shown, speedometer locked, GPS not started, pill '{during[4]}'; after: loader gone, GPS started, no card")
    c.close()
    c = ctx("prompt", extra="window.__permDelay=600;"); p = page(c); p.goto(URL); p.wait_for_timeout(250)
    d1 = t(p, "#gateTitle"); p.wait_for_timeout(900)
    check("L2", "Loader, then prompt card (not allowed)", d1 == "Checking location access" and t(p, "#gateTitle") == "Turn on location to start" and p.is_visible("#gateBtn") and p.evaluate("window.__watchCalls") == 0,
          f"'{d1}' → '{t(p, '#gateTitle')}', no GPS until tapped")
    c.close()
    c = ctx("granted", extra="window.__noPermApi=true;localStorage.setItem('pacer.locGranted','true');"); p = page(c); p.goto(URL); p.wait_for_timeout(900)
    check("L3", "No permission API, allowed before: straight to speedometer", p.locator("#gate").is_hidden() and p.evaluate("window.__watchCalls") == 1, "remembered from the last successful fix")
    c.close()
    c = ctx("granted", extra="window.__noPermApi=true;"); p = page(c); p.goto(URL); p.wait_for_timeout(900)
    check("L4", "No permission API, first visit: prompt card", t(p, "#gateTitle") == "Turn on location to start" and p.evaluate("window.__watchCalls") == 0, "card shown, no unsolicited prompt")
    c.close()

    # R3 Denied
    c = ctx("prompt"); p = page(c); p.goto(URL); p.wait_for_timeout(900); p.click("#gateBtn"); p.evaluate("window.__err(1)"); p.wait_for_timeout(300)
    check("R3", "Denied permission", t(p, "#gateTitle") == "Location is blocked" and p.locator("#gateSteps li").count() == 3 and p.evaluate("window.__watchers()") == 0,
          f"'{t(p, '#gateTitle')}', {p.locator('#gateSteps li').count()} device-specific steps, watcher released")
    shot(p, "02_denied"); c.close()
    c = ctx("denied"); p = page(c); p.goto(URL); p.wait_for_timeout(900)
    check("R3b", "Previously denied: no prompt, recovery shown", p.evaluate("window.__watchCalls") == 0 and t(p, "#gateTitle") == "Location is blocked", "no watch started")
    c.close()

    # Main driving session
    c = ctx(); p = page(c); p.goto(URL); p.wait_for_timeout(600); d = Drive(p)
    check("R0", "Before first fix: unavailable, not zero", t(p, "#speedNum") == "––" and t(p, "#lineDist").startswith("––"), f"speed '{t(p, '#speedNum')}', trip '{t(p, '#lineDist')}'")
    shot(p, "03_finding_gps")
    # R6 Stationary
    import random; random.seed(3)
    for _ in range(15):
        d.lat += random.uniform(-4, 4) / 111320
        p.evaluate(f"window.__fix({{lat:{d.lat},lon:{d.lon},acc:9,speed:{abs(random.gauss(0, 0.45)):.2f}}})"); p.wait_for_timeout(1000)
    flush(p)
    check("R6", "Stationary device", t(p, "#speedNum") == "0" and J(p, "trip")["dist"] < 1, f"shows {t(p, '#speedNum')}, distance {J(p, 'trip')['dist']:.2f} m after 15 s of jitter")
    # R7 Moving: step to 90 km/h
    d.tick(25, n=2)
    check("R7", "Moving device (instant 0 → 90 km/h jump)", int(t(p, "#speedNum")) >= 85, f"{t(p, '#speedNum')} km/h after 2 fixes; the first was held back as physically impossible (>1.2 g)")
    d.tick(25, n=3)
    # R4 GPS loss
    p.wait_for_timeout(5000)
    check("R4", "GPS loss", t(p, "#speedNum") == "––" and t(p, "#gpsText") == "Signal lost" and t(p, "#live") == "GPS signal lost",
          f"'{t(p, '#speedNum')}', '{t(p, '#gpsText')}', screen reader: '{t(p, '#live')}'")
    shot(p, "04_signal_lost")
    # R5 Recovery after a >10 s gap at a different speed: no sweep from the old value
    p.wait_for_timeout(6500)
    d.tick(12, n=1)
    v = t(p, "#speedNum")
    check("R5", "GPS recovery (12 s gap, 90 → 43 km/h)", v.isdigit() and abs(int(v) - 43) <= 3, f"first reading after gap: {v} km/h")
    d.tick(12, n=2)

    # Overspeed: activation, single alert, limit change, mute, GPS loss
    p.click("#alertBtn"); d.tick(30, n=4)
    o1 = p.evaluate("document.getElementById('app').classList.contains('over')"); v1 = p.evaluate("window.__vib")
    shot(p, "05_overspeed")
    for _ in range(4): p.click("#limUp")
    p.wait_for_timeout(300)
    cleared = not p.evaluate("document.getElementById('app').classList.contains('over')")
    check("A1", "Overspeed activates once; raising the limit clears it", o1 and v1 == 1 and cleared, f"over={o1}, alerts={v1}, after limit 100→120: cleared={cleared}")
    p.click("#alertBtn")   # mute
    for _ in range(4): p.click("#limDown")
    d.tick(30, n=3)
    check("A2", "Muted: red state but no sound/vibration", p.evaluate("document.getElementById('app').classList.contains('over')") and p.evaluate("window.__vib") == 1, f"alerts still {p.evaluate('window.__vib')}")
    p.click("#alertBtn")
    p.wait_for_timeout(5000)
    check("A3", "GPS lost during overspeed clears it", not p.evaluate("document.getElementById('app').classList.contains('over')") and t(p, "#speedNum") == "––", "cleared, no stale overspeed")
    d.tick(20, n=2)

    # R8 Long gap: 65 s without fixes adds no distance
    flush(p); d0 = J(p, "trip")["dist"]
    p.wait_for_timeout(65000)
    d.tick(20, n=1); flush(p); d1 = J(p, "trip")["dist"]
    check("R8", "Long GPS gap (65 s)", d1 - d0 < 50, f"distance added across the gap: {d1 - d0:.0f} m (car moved ~1.3 km; gap > 60 s is not invented)")
    d.tick(20, n=2)

    # Recording + R9 mode switching + R11 navigation + R12 pause/resume
    p.click("#recBtnD"); d.tick(20, n=3)
    pts0 = len(J(p, "rec")["points"]) if J(p, "rec") else 0
    flush(p); pts0 = len(J(p, "rec")["points"])
    for st in ["sport", "digital", "hud", "classic"]:
        p.click(f'#styleSeg button[data-st="{st}"]'); d.tick(20, n=1)
    flush(p); pts1 = len(J(p, "rec")["points"])
    check("R9", "Mode switching during recording", pts1 - pts0 == 4, f"{pts1 - pts0} points recorded across 4 mode switches (4 fixes)")
    for hsh in ["#trip", "#settings", "#about", "#drive"]:
        p.evaluate(f"location.hash='{hsh}'"); d.tick(20, n=1)
    flush(p); pts2 = len(J(p, "rec")["points"])
    check("R11", "Navigation during recording", pts2 - pts1 == 4 and p.is_visible("#recPill"), f"{pts2 - pts1} points across 4 screen changes, REC indicator still shown")
    r_before = J(p, "rec")
    p.click("#recBtnD")  # pause
    d.tick(20, n=5)
    flush(p); r_paused = J(p, "rec")
    p.click("#recBtnD")  # resume
    d.tick(20, n=3)
    flush(p); r_after = J(p, "rec")
    paused_gain = r_paused["dist"] - r_before["dist"]
    check("R12", "Pause and resume", r_paused["paused"] and paused_gain < 1 and len(r_paused["points"]) == len(r_before["points"]) and 50 < r_after["dist"] - r_paused["dist"] < 70,
          f"while paused: +{paused_gain:.1f} m, +{len(r_paused['points']) - len(r_before['points'])} points; after resume: +{r_after['dist'] - r_paused['dist']:.0f} m for 3 s at 20 m/s")

    # R10 Unit switching leaves measurements untouched
    flush(p); tb = J(p, "trip"); rb = J(p, "rec")
    for u in ["mph", "ms", "kn", "kmh"]: p.click(f'#unitSeg button[data-u="{u}"]')
    flush(p); ta = J(p, "trip"); ra = J(p, "rec")
    check("R10", "Unit switching", abs(ta["dist"] - tb["dist"]) < 1e-6 and abs(ra["dist"] - rb["dist"]) < 1e-6 and abs(ta["max"] - tb["max"]) < 1e-9,
          f"trip {tb['dist']:.1f} m, top {tb['max']:.2f} m/s before and after 4 unit changes")

    # R14 Browser refresh during recording
    p.reload(); p.wait_for_timeout(800)
    rr = J(p, "rec")
    check("R14", "Browser refresh during recording", p.is_visible("#recPill") and rr["on"] and len(rr["points"]) == len(ra["points"]),
          f"recording restored with {len(rr['points'])} points; toast: '{t(p, '#toast')}'")
    d = Drive(p); d.tick(20, n=3)
    p.evaluate("location.hash='#trip'"); p.wait_for_timeout(600)
    shot(p, "08_trip", full=True)
    # R18 Export
    p.click("#recStop"); p.wait_for_timeout(300)
    tr = J(p, "trips")[-1]
    with p.expect_download() as dl: p.click('#trips button[data-x="gpx"]')
    g = ET.fromstring(open(dl.value.path()).read()); ns = {"g": "http://www.topografix.com/GPX/1/1"}
    with p.expect_download() as dl2: p.click('#trips button[data-x="csv"]')
    rows = open(dl2.value.path()).read().strip().splitlines()
    speeds = [float(r.split(",")[4]) for r in rows[1:]]
    check("R18", "Export (GPX + CSV)", len(g.findall('.//g:trkpt', ns)) == len(tr['pts']) and rows[0] == "time_utc,latitude,longitude,altitude_m,speed_kmh" and abs(sorted(speeds)[len(speeds) // 2] - 72) < 2,
          f"GPX: valid XML, {len(g.findall('.//g:trkpt', ns))} points; CSV median speed {sorted(speeds)[len(speeds) // 2]:.1f} km/h (true 72)")
    p.click("#recStart"); p.wait_for_timeout(200); p.click("#recStop"); p.wait_for_timeout(200)
    check("R18b", "Export: empty recording not saved", len(J(p, "trips")) == 1 and "Nothing was saved" in t(p, "#recInfo"), t(p, "#recInfo"))
    shot(p, "09_trip_saved", full=True)

    # R13 Reset
    p.click("#resetTrip"); p.click("#resetTrip"); p.wait_for_timeout(200)
    flush(p); after_reset = J(p, "trip")["dist"]
    p.reload(); p.wait_for_timeout(500); p.evaluate("location.hash='#trip'"); p.wait_for_timeout(400)
    check("R13", "Reset", after_reset == 0 and t(p, "#tDist") == "––", f"stored distance after reset: {after_reset}; after reload Trip shows '{t(p, '#tDist')}' (no GPS yet)")

    # R15 Storage failure
    p.evaluate("location.hash='#drive'"); d = Drive(p); d.tick(20, n=2)
    p.evaluate("(() => { Storage.prototype.setItem = function(){ throw new DOMException('full','QuotaExceededError'); }; return true; })()")
    flush(p); p.wait_for_timeout(200)
    msg = t(p, "#toast"); d.tick(20, n=2)
    check("R15", "Storage failure", "Can't save" in msg and t(p, "#speedNum").isdigit(), f"message: '{msg}'; speed still live: {t(p, '#speedNum')}")
    c.close()

    # R16 Wake Lock denial
    c = ctx(extra="window.__denyWake=true;"); p = page(c); p.goto(URL + "#settings"); p.wait_for_timeout(400)
    p.click("#wakeSw"); p.wait_for_timeout(300)
    sw = p.get_attribute("#wakeSw", "aria-checked"); sub = t(p, "#wakeSub")
    p.evaluate("location.hash='#drive'"); d = Drive(p); d.tick(20, n=2); p.click("#fsBtn"); d.tick(20, n=1)
    check("R16", "Wake Lock denial", sw == "false" and "can't keep the screen on" in sub and t(p, "#speedNum").isdigit(), f"switch off, explained; drive mode still works ({t(p, '#speedNum')} km/h)")
    shot(p, "10_drive_mode"); c.close()

    # R19 Reduced motion
    c = ctx(reduced_motion="reduce"); p = page(c); p.goto(URL); p.wait_for_timeout(400); d = Drive(p); d.tick(0, n=2)
    d.tick(3, n=1)   # realistic 3 m/s² launch
    p.evaluate(f"window.__fix({{lat:{d.lat + 0.00005},lon:{d.lon},acc:5,speed:6}})"); p.wait_for_timeout(120)
    anim = p.evaluate("getComputedStyle(document.querySelector('.gps .bars')).animationName + '/' + getComputedStyle(document.querySelector('.dial')).transitionDuration")
    check("R19", "Reduced motion", int(t(p, "#speedNum")) >= 20 and anim.startswith("none"), f"display at {t(p, '#speedNum')} km/h 120 ms after a 11→22 km/h fix (filtered value; the eased needle would still be near 13); animations: {anim}")
    c.close()

    # R17 Canvas resize + R20 layouts + screenshots of all modes
    c = ctx(); p = page(c); p.goto(URL); p.wait_for_timeout(400); d = Drive(p); d.tick(22, n=3)
    for st in ["classic", "sport", "digital", "hud"]:
        p.click(f'#styleSeg button[data-st="{st}"]'); d.tick(22, n=1); shot(p, f"05_mode_{st}")
    p.click('#styleSeg button[data-st="classic"]')
    sizes = []
    for vw, vh in [(390, 844), (360, 640), (430, 932), (844, 390), (390, 844)]:
        p.set_viewport_size({"width": vw, "height": vh}); d.tick(22, n=1)
        sizes.append(p.evaluate("(() => { const c = document.getElementById('gauge'), r = c.getBoundingClientRect(); return [Math.round(r.width), Math.round(r.height), c.width, c.height, devicePixelRatio]; })()"))
    ok = all(abs(w - h) <= 1 and abs(cw - round(w * dpr)) <= 1 and abs(ch - round(h * dpr)) <= 1 for w, h, cw, ch, dpr in sizes)
    check("R17", "Canvas resize", ok, "; ".join(f"{w}px→{cw}px" for w, h, cw, ch, dpr in sizes) + " (square, DPR-scaled)")
    c.close()

    for name, vw, vh in [("small_360x640", 360, 640), ("iphone_390x844", 390, 844), ("landscape_844x390", 844, 390), ("landscape_667x375", 667, 375)]:
        c = ctx(vw=vw, vh=vh); p = page(c); p.goto(URL); p.wait_for_timeout(400); d = Drive(p); d.tick(22, n=2)
        hs = p.evaluate("document.documentElement.scrollWidth > innerWidth")
        sel = ["#recBtnD", "#alertBtn", "#limUp", "#limDown", "#fsBtn"] + [f'#unitSeg button[data-u="{u}"]' for u in ["kmh", "kn"]] + [f'#styleSeg button[data-st="{s}"]' for s in ["classic", "hud"]]
        bx = [p.locator(s).bounding_box() for s in sel]
        inside = all(x and x["x"] >= 0 and x["x"] + x["width"] <= vw + .5 and x["y"] + x["height"] <= vh - 40 for x in bx)
        mins = min(min(x["width"], x["height"]) for x in bx)
        land = vw > vh
        check("R20" if land else "R20p", f"Layout {name}", not hs and inside and mins >= 44,
              f"no sideways scroll, all controls visible above the tab bar, smallest target {mins:.0f}px, dial {p.locator('#dial').bounding_box()['width']:.0f}px")
        shot(p, f"06_{name}")
        c.close()

    # Settings + night theme screenshots
    c = ctx(); p = page(c); p.goto(URL + "#settings"); p.wait_for_timeout(400); shot(p, "11_settings", full=True)
    p.click('#themeSeg button[data-th="dark"]'); p.evaluate("location.hash='#drive'"); d = Drive(p); d.tick(22, n=3); shot(p, "12_night")
    c.close()
    b.close()

w = max(len(r[1]) for r in results); failed = 0
for rid, n, ok, det in results:
    failed += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {rid:<4} {n.ljust(w)}  {det}")
print(f"\n{sum(r[2] for r in results)} passed, {failed} failed, page errors: {errors or 'none'}")
json.dump([{"id": r[0], "test": r[1], "pass": r[2], "detail": r[3]} for r in results], open(f"{OUT}/results.json", "w"), indent=1, ensure_ascii=False)
sys.exit(1 if failed or errors else 0)
