"""Two copies of the application install two global keyboard hooks.

One Ctrl+Space then starts two dictations, transcribes twice and pastes the text into the
document twice, with nothing on screen to explain it. This is what stops that.
"""

import uuid

import pytest
from PyQt6.QtCore import QCoreApplication

from audiorecorder.single_instance import SingleInstance


@pytest.fixture
def app():
    return QCoreApplication.instance() or QCoreApplication([])


@pytest.fixture
def lock_name():
    """A lock of the tests' own, so a running application does not fail the suite."""
    return f"AudioRecorder.test-{uuid.uuid4().hex}"


@pytest.fixture
def owner(app, lock_name):
    """The copy that got there first, cleaned up afterwards."""
    instance = SingleInstance(lock_name)
    assert instance.take_ownership(), "the first copy must be allowed to run"
    yield instance
    if instance._server is not None:
        instance.close()


class TestSingleInstance:
    def test_the_first_copy_takes_ownership(self, owner):
        assert owner._server is not None

    def test_a_second_copy_is_refused(self, owner):
        second = SingleInstance(owner._name)
        assert second.take_ownership() is False
        assert second._server is None

    def test_the_owner_is_told_that_someone_asked_for_it(self, app, owner):
        asked = []
        owner.another_instance_started.connect(lambda: asked.append(True))

        SingleInstance(owner._name).take_ownership()
        for _ in range(50):
            app.processEvents()
            if asked:
                break

        assert asked, "the running copy has to hear that it is wanted"

    def test_ownership_is_available_again_once_released(self, app, owner):
        owner.close()

        successor = SingleInstance(owner._name)
        try:
            assert successor.take_ownership(), "closing the app must free the lock"
        finally:
            if successor._server is not None:
                successor.close()

    def test_a_copy_of_an_older_build_is_found_by_its_legacy_name(self, app, lock_name):
        legacy = SingleInstance(f"{lock_name}-legacy")
        assert legacy.take_ownership()
        try:
            current = SingleInstance(lock_name, legacy_name=f"{lock_name}-legacy")
            assert current.take_ownership() is False
            assert current._server is None
        finally:
            legacy.close()


def test_each_user_gets_their_own_socket_name(monkeypatch):
    """Windows named pipes are machine-wide; Remote Desktop users must not meet."""
    from types import SimpleNamespace

    from audiorecorder import single_instance

    names = []
    for home in ("C:/Users/alice", "C:/Users/bob"):
        monkeypatch.setattr(single_instance, "QDir", SimpleNamespace(homePath=lambda h=home: h))
        names.append(single_instance.user_server_name())
    assert names[0] != names[1]
    assert all(name.startswith(single_instance.LEGACY_SERVER_NAME + ".") for name in names)
