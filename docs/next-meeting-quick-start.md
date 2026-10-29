# Meeting Recorder — next-meeting quick start

## 1. Install and open the app

1. Open the DMG.
2. Drag **Meeting Recorder.app** onto the **Applications** shortcut.
3. Open Finder, choose **Go > Applications**, and launch **Meeting Recorder**. The app name
   contains a space. If Spotlight has not indexed it yet, use Finder rather than Spotlight.
4. If an unsigned build is blocked, open Terminal and run:

   ```sh
   xattr -cr "/Applications/Meeting Recorder.app"
   ```

5. Start one short test recording. Grant **Microphone** and **Screen & System Audio
   Recording** in **System Settings > Privacy & Security**, restart Meeting Recorder, and
   repeat the test. Both level meters must move.

## 2. Configure the meeting workflow

1. Open **Settings**.
2. Keep **System audio source** and **Microphone** on **Default** unless the level meters show
   the wrong devices.
3. Add the Soniox API key if you want transcription, live translation, or live speakers.
4. Keep **Show a recording prompt when a meeting is detected** enabled.
5. Enable **Launch at login and keep meeting detection available** if prompts should work after
   every Mac login. macOS may also show this under **General > Login Items**.
6. Optionally enable **Use macOS Calendar**. macOS requests Calendar access; this is used only
   to find imminent events with attendees or a supported conference link.
7. Start with manual recording. For native Teams/Zoom automation, complete the
   [supervised test checklist](native-call-test-checklist.md) before approving your app version.
   Window/media/browser/Calendar prompts remain advisory. Countdown defaults are five seconds.
8. Use **Readiness** to request/test native notifications and check observed login status.
   Focus can suppress banners. The installed app offers a native **Start Recording** action;
   when unavailable, use the in-app prompt. **Test Audio (3 seconds)** uses saved device settings.

## 3. Optional browser meeting detection

1. In Meeting Recorder, open **Settings** and choose **Reveal Companion Folder**. This opens
   the permanent copy bundled inside the installed app; do not select a folder from the DMG.
2. In Brave open `brave://extensions`; in Chrome use `chrome://extensions`; in Edge use
   `edge://extensions`.
3. Enable **Developer mode**, choose **Load unpacked**, and select the revealed
   `browser-extension` folder.
4. Confirm the app status bar changes from **Browser: not connected** to
   **Browser: connected** within about 30 seconds.
5. The extension can see only supported Meet/Zoom/Teams meeting pages and can contact only
   `127.0.0.1`. It has no history, tabs, or tab-capture permission.

## 4. Before joining

1. Open Meeting Recorder and confirm it shows no recording in progress.
   Closing the red window button keeps detection running in the menu bar; use the menu-bar
   icon to start/stop recording or open the app. Use **Quit** in the Dock menu when prompts
   are no longer wanted.
2. For live speaker labels, enable **Live Speakers**. It labels the microphone as **Me** and
   remote diarization as **Remote 1**, **Remote 2**, etc. This adds up to two paid Soniox
   realtime streams.
3. Enable **RTS Translate** only if live translation is needed; it adds another paid realtime
   stream.
4. Join the meeting. Native Teams/Zoom/Webex/Slack Huddle or the optional browser companion
   should show an advisory recording prompt. Native Teams media detection is conservative,
   so allow up to about 20 seconds. Choose **Start Recording**. Qualified native call controls
   can instead offer an automatic countdown after the supervised setup and audio test.
5. Confirm both level meters move. If system audio does not move, stop immediately and fix
   Screen & System Audio Recording permission before continuing.

## 5. During and after the meeting

1. When remote labels appear, choose **Name Speakers** and map them to participant names.
   Names assigned late are still applied to the complete saved speaker transcript.
2. Stop recording at the end. Wait for **Saved** in the status bar before quitting.
3. The output folder contains the `.m4a` plus `.speakers.md` and `.speakers.json` when live
   speakers recognized speech. RTS translation adds `.live.md`.
4. Choose **Transcribe** for the full Soniox batch transcript. The live speaker sidecars are
   immediate aids; batch diarization remains the more accurate transcript of record.

If no prompt appears, recording still works manually. Check that meeting detection is enabled,
Meeting Recorder is still running, and—only for browser calls—the unpacked extension remains
enabled. The app status bar must say **Browser: connected**; if it does not, open Settings and
reveal/load the bundled companion again. For native Teams, open Settings after joining and
check that **Teams media monitor** changes from **idle** to **active**.
