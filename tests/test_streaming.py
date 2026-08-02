"""A tidy WebSocket close is not a failure.

websocket-client hands the server's closing frame to the error callback, so the end of a
finished dictation used to be reported as `Soniox error: fin=1 opcode=8 data=b'\\x03\\xe8'`.
That aborted finish() before the transcribed text was read, so the dictation was never
pasted even though the transcription had succeeded.
"""

import websocket

from audiorecorder.dictation.streaming import SonioxStreamingSession, is_normal_close


def close_frame(status=None):
    data = b"" if status is None else status.to_bytes(2, "big")
    return websocket.ABNF(opcode=websocket.ABNF.OPCODE_CLOSE, data=data)


class TestIsNormalClose:
    def test_status_1000_is_normal(self):
        assert is_normal_close(close_frame(1000))

    def test_a_close_without_a_status_is_treated_as_normal(self):
        assert is_normal_close(close_frame())

    def test_an_abnormal_status_is_not(self):
        assert not is_normal_close(close_frame(1006))
        assert not is_normal_close(close_frame(1011))

    def test_a_data_frame_is_not_a_close(self):
        frame = websocket.ABNF(opcode=websocket.ABNF.OPCODE_TEXT, data=b"hello")
        assert not is_normal_close(frame)

    def test_a_real_exception_is_not_a_close(self):
        assert not is_normal_close(ConnectionResetError("connection reset"))
        assert not is_normal_close(RuntimeError("boom"))


class TestSessionErrorHandling:
    def session(self):
        return SonioxStreamingSession(api_key="x")

    def test_a_normal_close_leaves_no_error_behind(self):
        s = self.session()
        s._on_error(None, close_frame(1000))
        assert s._error is None
        assert s._finished.is_set()

    def test_a_real_error_is_kept(self):
        s = self.session()
        s._on_error(None, ConnectionResetError("connection reset"))
        assert "connection reset" in s._error
        assert s._finished.is_set()

    def test_finish_after_a_normal_close_does_not_raise(self):
        """The whole point: the text has to survive the end of the stream."""
        s = self.session()
        s._final_tokens = [{"text": "hello"}]
        s._on_error(None, close_frame(1000))

        s.finish(timeout=0.1)

        assert s.get_final_text() == "hello"
