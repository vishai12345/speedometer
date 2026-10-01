# Browser scenario tests for Pacer (the review's test table).
# Uses a controllable fake GPS injected before the page loads, so each scenario feeds exact fixes in real time.
# Run: python3 -m http.server 8765 &  then  python3 tests/ui_scenarios.py [screenshot_dir]
import sys, time, xml.etree.ElementTree as ET
from playwright.sync_api import sync_playwright

URL = "http://localhost:8765/index.html"
OUT = sys.argv[1] if len(sys.argv) > 1 else "."
IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"

FAKE_GPS = """
(() => {
  const w = {}; let n = 0;
  window.__vib = 0; window.__permState = window.__permState || 'granted';
  navigator.vibrate = () => { window.__vib++; return true; };
  const geo = navigator.geolocation;
  geo.watchPosition = (ok, err) => { n++; w[n] = { ok, err }; return n; };
  geo.clearWatch = id => { delete w[id]; };
  window.__watchers = () => Object.keys(w).length;
  window.__fix = f => Object.values(w).forEach(x => x.ok({ timestamp: Date.now(), coords: {
      latitude: f.lat, longitude: f.lon, accuracy: f.acc ?? 5, speed: f.speed ?? null, heading: f.heading ?? null, altitude: f.alt ?? 900 } }));
  window.__err = code => Object.values(w).forEach(x => x.err({ code }));
  const q = navigator.permissions.query.bind(navigator.permissions);
  navigator.permissions.query = d => d && d.name === 'geolocation'
    ? Promise.resolve({ state: window.__permState, onchange: null }) : q(d);
})();
"""

results = []
def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))

class Drive:
    """Feeds 1 Hz fixes along a straight north-bound road."""
    def __init__(self, page): self.p = page; self.lat = 12.9716; self.lon = 77.5946
    def tick(self, speed, acc=5, n=1, doppler=True):
        for _ in range(n):
            self.lat += speed / 111320
            self.p.evaluate(f"window.__fix({{lat:{self.lat},lon:{self.lon},acc:{acc},speed:{speed if doppler else 'null'},heading:0}})")
            self.p.wait_for_timeout(1000)

def text(p, sel): return p.inner_text(sel).strip()

with sync_playwright() as pw:
    b = pw.chromium.launch()

    def ctx(perm="granted", vw=390, vh=844, ua=IPHONE, **kw):
        c = b.new_context(viewport={"width": vw, "height": vh}, device_scale_factor=2, user_agent=ua, **kw)
        c.add_init_script(f"window.__permState = '{perm}';" + FAKE_GPS)
        return c
    errors = []
    def page(c):
        p = c.new_page(); p.on("pageerror", lambda e: errors.append(str(e))); return p

    # 1. First launch: permission prompt pending
    c = ctx("prompt"); p = page(c); p.goto(URL); p.wait_for_timeout(800)
    check("First launch: explains before/while asking", text(p, "#gateTitle") == "Allow location to start" and p.evaluate("document.querySelector('.stage').inert"), text(p, "#gateTitle"))
    p.screenshot(path=f"{OUT}/s01_first_launch.png"); c.close()

    # 2. Permission denied
    c = ctx("prompt"); p = page(c); p.goto(URL); p.wait_for_timeout(500); p.evaluate("window.__err(1)"); p.wait_for_timeout(400)
    check("Permission denied: recovery steps shown", text(p, "#gateTitle") == "Location is blocked" and p.locator("#gateSteps li").count() == 3 and p.evaluate("window.__watchers()") == 0,
          f"{text(p, '#gateTitle')}, {p.locator('#gateSteps li').count()} iPhone steps, GPS watch released")
    p.screenshot(path=f"{OUT}/s02_denied.png")
    # recovery: user allows, comes back, taps Try again, fix arrives
    p.click("#gateBtn"); d = Drive(p); d.tick(0, n=2)
    check("Permission denied: recovers after Try again", p.locator("#gate").is_hidden() and text(p, "#speedNum") == "0", f"speed {text(p, '#speedNum')}")
    c.close()

    # 3. Acquiring, 4. stationary, 5. moving, 6. poor, 7. interruption, 8. overspeed
    c = ctx(); p = page(c); p.goto(URL); p.wait_for_timeout(1500)
    check("GPS acquisition: no fake zero", text(p, "#speedNum") == "––" and text(p, "#gpsText") == "Finding GPS", f"shows '{text(p, '#speedNum')}' / '{text(p, '#gpsText')}'")
    p.screenshot(path=f"{OUT}/s03_acquiring.png")
    d = Drive(p)
    import random; random.seed(1)
    for _ in range(8):
        d.lat += random.uniform(-3, 3) / 111320
        p.evaluate(f"window.__fix({{lat:{d.lat},lon:{d.lon},acc:8,speed:{abs(random.gauss(0, 0.4)):.2f}}})"); p.wait_for_timeout(1000)
    check("Stationary: reads 0, GPS ok", text(p, "#speedNum") == "0" and text(p, "#gpsText").startswith("GPS"), f"{text(p, '#speedNum')} / {text(p, '#gpsText')}")
    dist0 = p.evaluate("JSON.parse(localStorage.getItem('pacer.trip')||'{\"dist\":0}').dist")
    d.tick(25, n=6)
    check("Moving: 90 km/h shown", text(p, "#speedNum") in ("89", "90", "91"), f"{text(p, '#speedNum')} km/h")
    p.screenshot(path=f"{OUT}/s05_moving_classic.png")
    d.tick(25, acc=60, n=3)
    check("Poor reception: flagged", text(p, "#gpsText").startswith("Weak GPS"), text(p, "#gpsText"))
    d.tick(25, n=2)
    # interruption
    p.wait_for_timeout(5500)
    lost_ok = text(p, "#speedNum") == "––" and text(p, "#gpsText") == "Signal lost"
    check("GPS interruption: no stale speed", lost_ok, f"{text(p, '#speedNum')} / {text(p, '#gpsText')}")
    p.screenshot(path=f"{OUT}/s07_lost.png")
    d.tick(25, n=2)
    check("Signal returns: speed back", text(p, "#speedNum") in ("89", "90", "91"), text(p, "#speedNum"))
    # overspeed with alert on
    p.click("#alertBtn")
    d.tick(30, n=8)   # 108 km/h for 8 s
    over = p.evaluate("document.getElementById('app').classList.contains('over')")
    vib = p.evaluate("window.__vib")
    check("Overspeed: red state + single alert", over and vib == 1, f"over={over}, alerts={vib} in 8 s")
    p.screenshot(path=f"{OUT}/s08_overspeed.png")
    d.tick(27.5, n=4)   # 99 km/h: inside hysteresis band, should not re-alert
    check("Overspeed: no repeat near the limit", p.evaluate("window.__vib") == 1, f"alerts={p.evaluate('window.__vib')}")
    d.tick(20, n=3)
    check("Overspeed clears below the limit", not p.evaluate("document.getElementById('app').classList.contains('over')"), "cleared")

    # 9. Recording across refresh
    p.click("#recBtnD"); d.tick(20, n=10)
    pts_before = p.evaluate("JSON.parse(localStorage.getItem('pacer.rec')||'{\"points\":[]}').points.length")
    p.evaluate("window.dispatchEvent(new Event('pagehide'))")
    pts_saved = p.evaluate("JSON.parse(localStorage.getItem('pacer.rec')).points.length")
    p.reload(); p.wait_for_timeout(800)
    resumed = p.is_visible("#recPill")
    d = Drive(p); d.tick(20, n=6)
    p.goto(URL + "#trip"); p.wait_for_timeout(600)
    p.screenshot(path=f"{OUT}/s09_trip.png", full_page=True)
    p.click("#recStop"); p.wait_for_timeout(400)
    trips = p.evaluate("JSON.parse(localStorage.getItem('pacer.trips'))")
    tr = trips[-1]
    check("Recording survives refresh", resumed and pts_saved >= 9 and len(tr["pts"]) >= pts_saved + 5, f"{pts_saved} points before reload, {len(tr['pts'])} saved after")
    check("Recorded distance plausible", 300 < tr["dist"] < 360, f"{tr['dist']:.0f} m for ~16 s at 72 km/h (≈320 m)")
    # 10. Export
    with p.expect_download() as dl: p.click('#trips button[data-x="gpx"]')
    gpx = open(dl.value.path()).read()
    root_el = ET.fromstring(gpx)
    ns = {"g": "http://www.topografix.com/GPX/1/1"}
    n_pts = len(root_el.findall(".//g:trkpt", ns))
    with p.expect_download() as dl2: p.click('#trips button[data-x="csv"]')
    csv_lines = open(dl2.value.path()).read().strip().splitlines()
    check("GPX export valid", n_pts == len(tr["pts"]), f"well-formed XML, {n_pts} track points")
    check("CSV export valid", csv_lines[0].startswith("time_utc,latitude") and len(csv_lines) == len(tr["pts"]) + 1, f"{len(csv_lines) - 1} rows")
    # persistence of trip computer across refresh
    p.evaluate("window.dispatchEvent(new Event('pagehide'))")
    dist_before = p.evaluate("JSON.parse(localStorage.getItem('pacer.trip')).dist")
    p.reload(); p.wait_for_timeout(600)
    check("Trip totals survive refresh", abs(p.evaluate("JSON.parse(localStorage.getItem('pacer.trip')).dist") - dist_before) < 1, f"{dist_before:.0f} m kept")

    # styles while moving
    p.goto(URL + "#drive"); p.wait_for_timeout(300); d = Drive(p); d.tick(22, n=3)
    for st in ["sport", "digital", "hud"]:
        p.click(f'#styleSeg button[data-st="{st}"]'); d.tick(22, n=1)
        p.locator(".drive").screenshot(path=f"{OUT}/s10_style_{st}.png")
    p.click("#mirrorBtn"); d.tick(22, n=1)
    check("HUD mirror applied", p.evaluate("getComputedStyle(document.getElementById('dial')).transform").startswith("matrix(-1"), "scaleX(-1)")
    p.click("#mirrorBtn"); p.click('#styleSeg button[data-st="classic"]')

    # 11. Theme
    p.goto(URL + "#settings"); p.click('#themeSeg button[data-th="dark"]'); p.wait_for_timeout(300)
    p.goto(URL + "#drive"); d = Drive(p); d.tick(22, n=2)
    bg = p.evaluate("getComputedStyle(document.body).backgroundColor")
    check("Night theme applied", bg == "rgb(11, 16, 22)", bg)
    p.screenshot(path=f"{OUT}/s11_night.png")
    c.close()

    # 12. Small screen and landscape
    for name, vw, vh in [("small_320x568", 320, 568), ("landscape_844x390", 844, 390), ("tablet_1024x768", 1024, 768)]:
        c = ctx(vw=vw, vh=vh); p = page(c); p.goto(URL); p.wait_for_timeout(500); d = Drive(p); d.tick(22, n=3)
        hs = p.evaluate("document.documentElement.scrollWidth > innerWidth")
        boxes = [p.locator(s).bounding_box() for s in ["#recBtnD", "#alertBtn", "#limUp", "#unitSeg", "#styleSeg"]]
        reach = all(bx and bx["x"] >= 0 and bx["x"] + bx["width"] <= vw + 0.5 for bx in boxes)
        small = [round(min(bx["width"], bx["height"])) for bx in boxes]
        dial = p.locator("#dial").bounding_box()
        check(f"Layout {name}", not hs and reach and dial["width"] >= 200, f"no sideways scroll, controls on screen, dial {dial['width']:.0f}px, min control {min(small)}px")
        p.screenshot(path=f"{OUT}/s12_{name}.png", full_page=True)
        c.close()

    b.close()

w = max(len(r[0]) for r in results); failed = 0
for n, ok, det in results:
    failed += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {n.ljust(w)}  {det}")
print(f"\n{sum(r[1] for r in results)} passed, {failed} failed, page errors: {errors or 'none'}")
sys.exit(1 if failed or errors else 0)
