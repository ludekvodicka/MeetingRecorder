"""A real Qt loop must wait for a pending capture start before completing Quit."""

import os
import subprocess
import sys
import textwrap


def test_quit_during_start_reconciles_late_success_and_releases_capture(tmp_path):
    script = textwrap.dedent("""
        import sys
        import time
        from PyQt6.QtCore import QTimer
        from PyQt6.QtWidgets import QApplication
        from audiorecorder.app import QuitFilter
        from audiorecorder.audio.session import CaptureSession
        from audiorecorder.ui import main_window as mw

        class Backend:
            active_system_source = 'Test system'
            active_mic_source = 'Test microphone'
            def set_level_callbacks(self, *args): pass
            def set_error_callback(self, callback): pass
            def set_system_tap(self, callback): pass
            def set_mic_tap(self, callback): pass
            def start(self, *args): time.sleep(.08)
            def stop(self): return None, None
            def close(self): pass

        app = QApplication([])
        app.setQuitOnLastWindowClosed(False)
        mw.CaptureSession = lambda: CaptureSession(Backend)
        window = mw.MainWindow({'output_dir': sys.argv[1], 'meeting_detection': False})
        quit_filter = QuitFilter(window)
        app.installEventFilter(quit_filter)
        observed = []
        QTimer.singleShot(0, window._start_recording)
        QTimer.singleShot(20, app.quit)
        QTimer.singleShot(40, lambda: observed.append(window._lifecycle.controller.closing))
        QTimer.singleShot(3000, lambda: app.exit(9))
        code = app.exec()
        assert code == 0, code
        assert observed == [True], observed
        assert window._capture_session.backend is None
        assert window._lifecycle.controller.state == 'idle'
        assert window._exit_ready
        app.removeEventFilter(quit_filter)
    """)
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
