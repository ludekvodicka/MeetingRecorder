"""The application's side of self-update: Qt host, composition, busy guard and indicator.

The update logic itself is tested with the shared member; these tests cover what this
application adds around it.
"""

import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from PyQt6.QtCore import QEventLoop, QTimer
from PyQt6.QtWidgets import QApplication

from audiorecorder.shared.desktop.autoupdate.feed import UpdateFeedGithub
from audiorecorder.shared.desktop.autoupdate.installer import UpdateInstaller
from audiorecorder.shared.desktop.autoupdate.manager import UpdateManager
from audiorecorder.shared.desktop.autoupdate.runtime import UpdateResolution, UpdateTargetFile
from audiorecorder.shared.desktop.autoupdate.stager import UpdateStager
from audiorecorder.shared.desktop.autoupdate.status import UpdateInstalling, UpdateReady
from audiorecorder.shared.desktop.autoupdate.tests.support import (
    FILE_PATTERN,
    LATEST,
    OWNER,
    REPO,
    FakeTransport,
    ManualHost,
    file_urls,
    release_json,
    with_sums,
)
from audiorecorder.shared.desktop.autoupdate.transport import UpdateTransport
from audiorecorder.shared.desktop.autoupdate.view import UpdateViewModel
from audiorecorder.ui.update_indicator import TONE_COLORS, UpdateDialog, UpdateIndicator
from audiorecorder.updates import AppUpdates, QtUpdateHost
from audiorecorder.version import __version__

ROOT = Path(__file__).resolve().parents[1]
NEW = "9.9.0"
FILES = with_sums({FILE_PATTERN.format(version=NEW): b"new build bytes"})


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class RecordingInstaller(UpdateInstaller):
    """Records helper launches instead of starting a process."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.launches: list[tuple[bool, tuple[str, ...]]] = []

    def launch(self, staged, target, relaunch, relaunch_args) -> None:
        self.launches.append((relaunch, relaunch_args))

    def take_outcome(self, running, target):
        return None


def ready_updates(tmp_path: Path, app) -> tuple[AppUpdates, UpdateManager, RecordingInstaller,
                                                 list[bool]]:
    """An AppUpdates whose manager has downloaded and verified a newer release."""
    host = ManualHost()
    transport = FakeTransport({LATEST: release_json(NEW, FILES)}, file_urls(NEW, FILES))
    installer = RecordingInstaller(tmp_path / "cache")
    quits: list[bool] = []
    manager = UpdateManager(
        running="1.0.0", resolution=UpdateResolution("automatic", "Packaged Windows build."),
        target=UpdateTargetFile(tmp_path / "program" / "Demo.exe", FILE_PATTERN,
                                onefile_parent=False),
        host=host, feed=UpdateFeedGithub(OWNER, REPO, transport),
        stager=UpdateStager(transport, tmp_path / "cache"), installer=installer,
        release_page=AppUpdates.release_page, on_quit=lambda: quits.append(True),
        clock=host.clock)
    updates = AppUpdates(manager, app)
    manager.start()
    host.run_jobs()
    host.advance(45)
    host.run_jobs()
    assert isinstance(manager.status.state, UpdateReady)
    return updates, manager, installer, quits


def run_until(app, done, timeout_ms=5000):
    loop = QEventLoop()
    poll = QTimer()
    poll.timeout.connect(lambda: done() and loop.quit())
    poll.start(10)
    QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()
    poll.stop()


class TestSourceRun:
    def test_updates_are_off_and_never_touch_the_network(self, app, monkeypatch):
        def no_network(*args, **kwargs):
            raise AssertionError("a source run must not reach the network")

        monkeypatch.setattr(UpdateTransport, "get_json", no_network)
        monkeypatch.setattr(UpdateTransport, "get_text", no_network)
        monkeypatch.setattr(UpdateTransport, "download", no_network)
        updates = AppUpdates.create(app)
        updates.start()
        app.processEvents()

        view = updates.view()
        assert view.text == "Updates off"
        assert view.tone == "muted"
        assert view.actions == ("open_release",)

    def test_a_source_run_has_no_target(self):
        assert AppUpdates.target() is None

    def test_release_pages_stay_on_the_public_repository(self):
        assert AppUpdates.release_page("1.2.3") == (
            "https://github.com/ludekvodicka/MeetingRecorder/releases/tag/v1.2.3")
        assert AppUpdates.release_page(None) == (
            "https://github.com/ludekvodicka/MeetingRecorder/releases/latest")


class TestBusyGuard:
    def test_install_while_recording_is_refused_and_stays_ready(self, app, tmp_path):
        updates, manager, installer, quits = ready_updates(tmp_path, app)
        updates.set_busy_check(lambda: "a recording is in progress.")

        assert updates.install() == "a recording is in progress."
        assert isinstance(manager.status.state, UpdateReady)
        assert installer.launches == []
        assert quits == []

    def test_install_when_idle_starts_installing(self, app, tmp_path):
        updates, manager, installer, quits = ready_updates(tmp_path, app)

        assert updates.install() is None
        assert isinstance(manager.status.state, UpdateInstalling)

    def test_quit_at_session_end_never_installs(self, app, tmp_path):
        updates, manager, installer, quits = ready_updates(tmp_path, app)
        updates.quit(True)
        assert installer.launches == []

    def test_quit_while_busy_never_installs(self, app, tmp_path):
        updates, manager, installer, quits = ready_updates(tmp_path, app)
        updates.set_busy_check(lambda: "a recording is still being saved.")
        updates.quit(False)
        assert installer.launches == []

    def test_a_normal_quit_installs_without_relaunch(self, app, tmp_path):
        updates, manager, installer, quits = ready_updates(tmp_path, app)
        updates.quit(False)
        assert installer.launches == [(False, ())]


class TestQtUpdateHost:
    def test_results_and_errors_arrive_on_the_gui_thread(self, app):
        host = QtUpdateHost()
        gui = threading.get_ident()
        seen = {}

        def fail():
            raise ValueError("boom")

        host.run_in_background(
            threading.get_ident,
            lambda worker: seen.update(worker=worker, done=threading.get_ident()),
            lambda error: seen.update(unexpected=error))
        host.run_in_background(fail, lambda result: seen.update(unexpected=result),
                               lambda error: seen.update(error=error, failed=threading.get_ident()))
        run_until(app, lambda: "done" in seen and "failed" in seen)

        assert "unexpected" not in seen
        assert seen["worker"] != gui
        assert seen["done"] == gui
        assert seen["failed"] == gui
        assert str(seen["error"]) == "boom"

    def test_a_cancelled_timer_never_fires(self, app):
        host = QtUpdateHost()
        fired = []
        cancel = host.call_later(0.01, lambda: fired.append("cancelled"))
        host.call_later(0.05, lambda: fired.append("kept"))
        cancel()
        run_until(app, lambda: fired)
        assert fired == ["kept"]
        cancel()


class TestIndicator:
    def view(self, text, tone, actions=()):
        return UpdateViewModel(text, None, tone, actions, None)

    @pytest.mark.parametrize("text", ["Updates off", f"Version {__version__}", "Up to date"])
    def test_quiet_states_show_the_plain_version(self, app, text):
        updates = AppUpdates.create(app)
        indicator = UpdateIndicator(updates)
        updates.changed.emit(self.view(text, "muted"))
        assert indicator.text() == f"v{__version__}"
        assert TONE_COLORS["muted"] in indicator.styleSheet()

    @pytest.mark.parametrize("text,tone", [
        ("Checking for updates", "working"),
        ("Downloading 9.9.0 (42%)", "working"),
        ("Version 9.9.0 ready", "ready"),
        ("Update failed", "failed"),
    ])
    def test_active_states_show_their_text_and_color(self, app, text, tone):
        updates = AppUpdates.create(app)
        indicator = UpdateIndicator(updates)
        updates.changed.emit(self.view(text, tone))
        assert indicator.text() == text
        assert TONE_COLORS[tone] in indicator.styleSheet()

    def test_a_source_run_starts_with_the_plain_version(self, app):
        assert UpdateIndicator(AppUpdates.create(app)).text() == f"v{__version__}"

    def test_the_dialog_shows_notes_as_plain_text_and_refuses_while_busy(self, app, tmp_path):
        updates, manager, installer, quits = ready_updates(tmp_path, app)
        updates.set_busy_check(lambda: "a recording is in progress.")
        dialog = UpdateDialog(updates)

        assert dialog._text.text() == f"Version {NEW} ready"
        assert dialog._notes.toPlainText() == "Fixes."
        assert not dialog._install.isHidden()
        assert not dialog._open_release.isHidden()
        assert dialog._check.isHidden()

        dialog._install.click()
        assert not dialog._refusal.isHidden()
        assert "a recording is in progress" in dialog._refusal.text()
        assert isinstance(manager.status.state, UpdateReady)
        dialog.close()


class TestHelperDispatch:
    def test_apply_update_exits_before_qt_is_imported(self, tmp_path):
        manifest = tmp_path / "pending.json"
        manifest.write_text("not json", encoding="utf-8")
        probe = (
            "import runpy, sys\n"
            "sys.argv = ['audiorecorder', '--apply-update', sys.argv[1]]\n"
            "try:\n"
            "    runpy.run_module('audiorecorder', run_name='__main__', alter_sys=True)\n"
            "except SystemExit as stop:\n"
            "    print(stop.code, 'PyQt6' in sys.modules)\n"
        )
        result = subprocess.run([sys.executable, "-c", probe, str(manifest)], cwd=ROOT,
                                capture_output=True, text=True, timeout=60)
        assert result.stdout.split() == ["2", "False"], result.stderr
        assert (tmp_path / "helper.log").is_file()
        assert json.loads((tmp_path / "result.json").read_text("utf-8"))["installed"] is False

    def test_the_module_entry_returns_the_helper_exit_code(self, tmp_path):
        manifest = tmp_path / "pending.json"
        manifest.write_text("{}", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "-m", "audiorecorder", "--apply-update", str(manifest)],
            cwd=ROOT, capture_output=True, text=True, timeout=60)
        assert result.returncode == 2, result.stderr
