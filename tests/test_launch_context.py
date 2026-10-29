from types import SimpleNamespace

from audiorecorder.macos_launch import login_launch


def test_login_context_uses_apple_event_not_saved_preference():
    event = SimpleNamespace(
        eventID=lambda: int.from_bytes(b"oapp", "big"),
        paramDescriptorForKeyword_=lambda key: SimpleNamespace(
            enumCodeValue=lambda: int.from_bytes(b"lgit", "big")
        ),
    )
    assert login_launch(event)
    assert not login_launch(None)
    event.eventID = lambda: 0
    assert not login_launch(event)
