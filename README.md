# Pacer Speedometer

A GPS speedometer that runs in the browser: Kalman-filtered speed, a 60 fps dial, trip computer, 60-second speed graph and an overspeed alert.

**Live:** https://vishai12345.github.io/speedometer/

## Use
1. Open the live link on your phone in Safari or Chrome.
2. Tap **Device GPS** and allow location.
3. Go outdoors with a clear view of the sky.

The **Simulator** mode replays a test drive, so you can try the app without moving.

## Deploy
The whole app is one file, `index.html`, with no build step. GitHub Pages serves it from `main` / root over HTTPS, which browsers require for location access.
