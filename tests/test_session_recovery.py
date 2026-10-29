"""A recording survives the end of the Windows session and is not closed by accident.

Windows can kill the process during logoff before the encoder has run. The capture files
then stay in the output directory, and the next start has to turn them into a recording.
"""

import os
import subprocess
import sys
import textwrap

import numpy as np
import soundfile as sf

from audiorecorder.recordings.manager import RecordingManager


def tone(path, seconds=0.2, rate=48000):
    samples = 0.3 * np.sin(2 * np.pi * 440 * np.arange(int(seconds * rate)) / rate)
    sf.write(str(path), samples.astype("float32"), rate)


def run_window(script, tmp_path):
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script), str(tmp_path)],
        capture_output=True, text=True, timeout=20, env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_orphaned_captures_are_found_with_and_without_system_audio(tmp_path):
    tone(tmp_path / "_rec_mic_a.wav")
    tone(tmp_path / "_rec_system_a.wav")
    tone(tmp_path / "_rec_mic_b.wav")
    (tmp_path / "_rec_system_c.wav").write_bytes(b"system without a microphone track")

    captures = RecordingManager(str(tmp_path)).orphaned_captures()

    assert [(system, mic) for system, mic, _ in captures] == [
        (str(tmp_path / "_rec_system_a.wav"), str(tmp_path / "_rec_mic_a.wav")),
        (None, str(tmp_path / "_rec_mic_b.wav")),
    ]


def test_a_capture_that_cannot_be_encoded_is_set_aside_with_its_audio(tmp_path):
    (tmp_path / "_rec_mic_a.wav").write_bytes(b"mic")
    (tmp_path / "_rec_system_a.wav").write_bytes(b"system")

    kept = RecordingManager.set_aside_capture(
        str(tmp_path / "_rec_system_a.wav"), str(tmp_path / "_rec_mic_a.wav"))

    assert sorted(os.path.basename(path) for path in kept) == [
        "Unrecovered_mic_a.wav", "Unrecovered_system_a.wav"]
    assert RecordingManager(str(tmp_path)).orphaned_captures() == []


def test_start_up_encodes_a_capture_left_by_a_killed_session(tmp_path):
    tone(tmp_path / "_rec_mic_a.wav")
    tone(tmp_path / "_rec_system_a.wav")
    (tmp_path / "_rec_mic_b.wav").write_bytes(b"not audio")
    run_window("""
        import sys
        from pathlib import Path
        from PyQt6.QtCore import QTimer
        from PyQt6.QtWidgets import QApplication
        from audiorecorder.ui import main_window as mw

        app = QApplication([])
        directory = Path(sys.argv[1])
        window = mw.MainWindow({'output_dir': str(directory), 'meeting_detection': False})

        def check():
            if not window._encoder_queue and not window._active_encoders:
                app.quit()
            else:
                QTimer.singleShot(50, check)

        QTimer.singleShot(0, check)
        QTimer.singleShot(15000, lambda: app.exit(9))
        assert app.exec() == 0
        names = sorted(path.name for path in directory.iterdir())
        assert len([name for name in names if name.endswith('.m4a')]) == 1, names
        assert not [name for name in names if name.startswith('_rec_')], names
        assert 'Unrecovered_mic_b.wav' in names, names
    """, tmp_path)


def test_closing_during_a_recording_asks_and_the_session_end_stops_capture(tmp_path):
    run_window("""
        import sys
        from PyQt6.QtCore import QTimer
        from PyQt6.QtWidgets import QApplication, QMessageBox
        from audiorecorder.audio.session import CaptureSession
        from audiorecorder.ui import main_window as mw

        class Backend:
            active_system_source = 'Test system'
            active_mic_source = 'Test microphone'
            stopped = False
            def set_level_callbacks(self, *args): pass
            def set_error_callback(self, callback): pass
            def set_system_tap(self, callback): pass
            def set_mic_tap(self, callback): pass
            def start(self, *args): pass
            def stop(self):
                Backend.stopped = True
                return None, None
            def close(self): pass

        app = QApplication([])
        app.setQuitOnLastWindowClosed(False)
        mw.CaptureSession = lambda: CaptureSession(Backend)
        answers = []
        mw.QMessageBox.question = staticmethod(
            lambda *args: answers.append(args[1]) or QMessageBox.StandardButton.No)
        window = mw.MainWindow({'output_dir': sys.argv[1], 'meeting_detection': False})
        window.show()

        def recording():
            if not window._is_recording:
                QTimer.singleShot(20, recording)
                return
            window.close()
            assert answers == ['Recording in Progress'], answers
            assert window._is_recording and not window._quitting
            window.end_session()
            assert Backend.stopped
            assert window._capture_session.backend is None
            window.close()
            assert answers == ['Recording in Progress'], answers
            app.quit()

        QTimer.singleShot(0, window._start_recording)
        QTimer.singleShot(0, recording)
        QTimer.singleShot(5000, lambda: app.exit(9))
        assert app.exec() == 0
    """, tmp_path)
