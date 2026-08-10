# Meeting Recorder browser companion

This optional Chrome/Edge extension reports supported meeting pages to the desktop app's
loopback-only bridge. It does not read browser history, capture tabs, record audio, or send
data anywhere except `127.0.0.1`.

Supported pages:

- Google Meet meeting-code URLs
- Zoom web-client URLs
- Microsoft Teams web meeting/call URLs

For development installation, open `chrome://extensions`, enable **Developer mode**, choose
**Load unpacked**, and select this `browser-extension` directory. Keep Meeting Recorder
running; its normal meeting-detection setting controls both native and browser prompts.
