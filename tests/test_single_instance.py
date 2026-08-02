"""Two copies of the application install two global keyboard hooks.

One Ctrl+Space then starts two dictations, transcribes twice and pastes the text into the
document twice, with nothing on screen to explain it. This is what stops that.
"""

import pytest
from PyQt6.QtCore import QCoreApplication

from audiorecorder.single_instance import SingleInstance


@pytest.fixture
def app():
    return QCoreApplication.instance() or QCoreApplication([])


@pytest.fixture
def owner(app):
    """The copy that got there first, cleaned up afterwards."""
    instance = SingleInstance()
    assert instance.take_ownership(), "the first copy must be allowed to run"
    yield instance
    if instance._server is not None:
        instance._server.close()


class TestSingleInstance:
    def test_the_first_copy_takes_ownership(self, owner):
        assert owner._server is not None

    def test_a_second_copy_is_refused(self, owner):
        second = SingleInstance()
        assert second.take_ownership() is False
        assert second._server is None

    def test_the_owner_is_told_that_someone_asked_for_it(self, app, owner):
        asked = []
        owner.another_instance_started.connect(lambda: asked.append(True))

        SingleInstance().take_ownership()
        for _ in range(50):
            app.processEvents()
            if asked:
                break

        assert asked, "the running copy has to hear that it is wanted"

    def test_ownership_is_available_again_once_released(self, app, owner):
        owner._server.close()
        owner._server = None

        successor = SingleInstance()
        try:
            assert successor.take_ownership(), "closing the app must free the lock"
        finally:
            if successor._server is not None:
                successor._server.close()
