"""The Qt loop receives a browser candidate without waiting for a second UI timer tick."""

import os
import subprocess
import sys
import textwrap


def test_browser_detection_prompts_without_starting_audio_and_stops_with_settings(tmp_path):
    script = textwrap.dedent('''
        import sys
        import time
        from PyQt6.QtWidgets import QApplication
        from audiorecorder.meetings.browser_agent import BrowserAgentDetector
        from audiorecorder.ui import main_window as mw

        calls = []
        def transport(endpoint, message, stopped):
            calls.append(message)
            return {"ok": True, "profile": {
                "instanceId": "test", "label": "Test browser", "protocolVersion": 4,
                "agentAccess": True, "tools": [{"id": "meetings.web.googleMeet", "enabled": True}],
            }, "value": {"meetings": [{"key": "meet.google.com/abc-defg-hij", "title": "Test"}]}}

        app = QApplication([])
        mw.BrowserAgentDetector = lambda **kwargs: BrowserAgentDetector(
            discover=lambda: ("test",), transport=transport, **kwargs)
        window = mw.MainWindow({"output_dir": sys.argv[1], "meeting_detection": True,
                                "automatic_recording": True})
        deadline = time.monotonic() + 2
        while window._meeting_prompt is None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.005)
        assert window._meeting_prompt is not None
        assert window._meeting_prompt.candidate.source == "browser-agent"
        assert not window._is_recording
        assert window._capture_session.backend is None
        assert "1 profiles, 1 meeting tools" in window._browser_status_label.text()
        window.config["meeting_detection"] = False
        window._sync_meeting_detection()
        assert not window._browser_agent.status().active
        assert window._browser_agent.scan() == []
        window._browser_agent._thread.join(1)
        assert not window._browser_agent._thread.is_alive()
        assert len(calls) == 2
        window._quitting = True
        window.close()
        app.processEvents()
    ''')
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)], capture_output=True, text=True,
        timeout=10, env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
