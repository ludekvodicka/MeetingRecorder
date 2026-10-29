# Meeting Recorder browser companion

This optional Brave/Chrome/Edge extension reports supported meeting pages to the desktop app's
loopback-only bridge. It does not read browser history, capture tabs, record audio, or send
data anywhere except `127.0.0.1`.

Supported pages:

- Google Meet meeting-code URLs
- Zoom web-client URLs
- Microsoft Teams web meeting/call URLs

For installation, use Meeting Recorder's **Settings > Reveal Companion Folder**, then open
`brave://extensions`, `chrome://extensions`, or `edge://extensions`. Enable **Developer mode**,
choose **Load unpacked**, and select the revealed `browser-extension` directory. Keep Meeting
Recorder running; its status bar reports whether the companion heartbeat is connected.
