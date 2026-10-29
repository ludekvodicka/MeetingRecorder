"""Native macOS system-audio capture through ScreenCaptureKit.

This module is imported only by the macOS backend. Keeping the framework imports here
allows the package and its tests to remain importable on Windows and Linux, where PyObjC
is intentionally not installed.
"""

import ctypes
import ctypes.util
import logging
import threading

import numpy as np

from audiorecorder.audio.backend import CaptureUnresolved

log = logging.getLogger(__name__)

CAPTURE_RATE = 48000
CAPTURE_CHANNELS = 2
_OPERATION_TIMEOUT = 15
_STREAM_OUTPUT_CLASS = None


def _error_text(error):
    if error is None:
        return None
    description = getattr(error, "localizedDescription", None)
    return str(description()) if description is not None else str(error)


def _await_result(invoke, operation):
    finished = threading.Event()
    result = {}

    def completion(value, error):
        result["value"] = value
        result["error"] = error
        finished.set()

    invoke(completion)
    if not finished.wait(_OPERATION_TIMEOUT):
        raise CaptureUnresolved(f"Timed out while waiting for ScreenCaptureKit to {operation}")
    if result.get("error") is not None:
        message = _error_text(result["error"])
        raise RuntimeError(f"ScreenCaptureKit could not {operation}: {message}")
    return result.get("value")


def _await_action(invoke, operation):
    finished = threading.Event()
    result = {}

    def completion(error):
        result["error"] = error
        finished.set()

    invoke(completion)
    if not finished.wait(_OPERATION_TIMEOUT):
        raise CaptureUnresolved(f"Timed out while waiting for ScreenCaptureKit to {operation}")
    if result.get("error") is not None:
        message = _error_text(result["error"])
        raise RuntimeError(f"ScreenCaptureKit could not {operation}: {message}")


def _make_serial_queue(label):
    """Create a dispatch queue and return its Objective-C bridge object."""
    import objc

    library_path = ctypes.util.find_library("System")
    if not library_path:
        raise RuntimeError("Could not load the macOS dispatch runtime")
    library = ctypes.CDLL(library_path)
    create_queue = library.dispatch_queue_create
    create_queue.argtypes = [ctypes.c_char_p, ctypes.c_void_p]
    create_queue.restype = ctypes.c_void_p
    pointer = create_queue(label.encode("utf-8"), None)
    if not pointer:
        raise RuntimeError("Could not create the system-audio callback queue")
    return objc.objc_object(c_void_p=pointer)


def _decode_pcm(raw, frames, channels, bits, flags):
    """Decode linear PCM bytes described by an AudioStreamBasicDescription."""
    import CoreAudio

    byte_order = ">" if flags & CoreAudio.kAudioFormatFlagIsBigEndian else "<"

    if flags & CoreAudio.kAudioFormatFlagIsFloat:
        if bits not in (32, 64):
            raise ValueError(f"unsupported floating-point sample size: {bits}")
        dtype = np.dtype(f"{byte_order}f{bits // 8}")
        scale = None
    elif flags & CoreAudio.kAudioFormatFlagIsSignedInteger:
        if bits not in (16, 32):
            raise ValueError(f"unsupported integer sample size: {bits}")
        dtype = np.dtype(f"{byte_order}i{bits // 8}")
        scale = float(1 << (bits - 1))
    else:
        raise ValueError("unsupported ScreenCaptureKit PCM encoding")

    expected_samples = frames * channels
    available_samples = len(raw) // dtype.itemsize
    if available_samples < expected_samples:
        raise ValueError(
            f"short ScreenCaptureKit audio block: expected {expected_samples} samples, "
            f"received {available_samples}"
        )
    samples = np.frombuffer(raw, dtype=dtype, count=expected_samples)
    if flags & CoreAudio.kAudioFormatFlagIsNonInterleaved:
        samples = samples.reshape(channels, frames).T
    else:
        samples = samples.reshape(frames, channels)
    if scale is not None:
        samples = samples.astype(np.float32) / scale
    else:
        samples = samples.astype(np.float32, copy=False)
    return np.ascontiguousarray(samples)


def _sample_buffer_to_array(sample_buffer):
    import CoreAudio
    import CoreMedia

    if not CoreMedia.CMSampleBufferIsValid(sample_buffer):
        return None
    frames = int(CoreMedia.CMSampleBufferGetNumSamples(sample_buffer))
    if frames <= 0:
        return None
    description = CoreMedia.CMSampleBufferGetFormatDescription(sample_buffer)
    stream_format = CoreMedia.CMAudioFormatDescriptionGetStreamBasicDescription(description)
    if stream_format is None or stream_format.mFormatID != CoreAudio.kAudioFormatLinearPCM:
        raise ValueError("ScreenCaptureKit returned audio that is not linear PCM")

    block = CoreMedia.CMSampleBufferGetDataBuffer(sample_buffer)
    if block is None:
        return None
    byte_count = int(CoreMedia.CMBlockBufferGetDataLength(block))
    status, raw = CoreMedia.CMBlockBufferCopyDataBytes(block, 0, byte_count, None)
    if status != 0:
        raise RuntimeError(f"Could not copy ScreenCaptureKit audio bytes (status {status})")
    return _decode_pcm(
        raw,
        frames,
        int(stream_format.mChannelsPerFrame),
        int(stream_format.mBitsPerChannel),
        int(stream_format.mFormatFlags),
    )


def _stream_output_class():
    """Create the Objective-C delegate class once for the lifetime of the process."""
    global _STREAM_OUTPUT_CLASS
    if _STREAM_OUTPUT_CLASS is not None:
        return _STREAM_OUTPUT_CLASS

    import objc
    import ScreenCaptureKit
    from Foundation import NSObject

    class MeetingRecorderStreamOutput(
        NSObject,
        protocols=[
            objc.protocolNamed("SCStreamOutput"),
            objc.protocolNamed("SCStreamDelegate"),
        ],
    ):
        def initWithOwner_(self, owner):
            self = objc.super(MeetingRecorderStreamOutput, self).init()
            if self is not None:
                self._owner = owner
            return self

        def stream_didOutputSampleBuffer_ofType_(self, stream, sample_buffer, kind):
            owner = self._owner
            if not owner._running or kind != ScreenCaptureKit.SCStreamOutputTypeAudio:
                return
            try:
                data = _sample_buffer_to_array(sample_buffer)
                if data is not None:
                    owner._callback(data)
            except Exception:
                log.exception("could not process a ScreenCaptureKit audio block")
                if not getattr(owner, "_sample_error_reported", False):
                    owner._sample_error_reported = True
                    if owner._error_callback is not None:
                        owner._error_callback("System audio samples could not be written")

        def stream_didStopWithError_(self, stream, error):
            self._owner._did_stop_with_error(error)

    _STREAM_OUTPUT_CLASS = MeetingRecorderStreamOutput
    return _STREAM_OUTPUT_CLASS


class ScreenCaptureAudioStream:
    """Small start/stop adapter matching a sounddevice stream."""

    def __init__(self, callback, error_callback=None):
        self._callback = callback
        self._error_callback = error_callback
        self._stream = None
        self._output = None
        self._queue = None
        self._running = False

    def _did_stop_with_error(self, error):
        """Handle an asynchronous stop reported by ScreenCaptureKit's delegate."""
        if not self._running:
            return
        self._running = False
        message = _error_text(error) or "unknown ScreenCaptureKit error"
        log.error("system-audio capture stopped unexpectedly: %s", message)
        if self._error_callback is not None:
            try:
                self._error_callback(f"System audio capture stopped unexpectedly: {message}")
            except Exception:
                log.exception("could not report the system-audio capture failure")

    def _build(self):
        import Quartz
        import ScreenCaptureKit

        content = _await_result(
            lambda completion: ScreenCaptureKit.SCShareableContent
            .getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_(
                False, False, completion
            ),
            "list displays",
        )
        displays = list(content.displays())
        if not displays:
            raise RuntimeError("ScreenCaptureKit did not find a display to capture")
        main_display_id = Quartz.CGMainDisplayID()
        display = next(
            (candidate for candidate in displays if candidate.displayID() == main_display_id),
            displays[0],
        )

        content_filter = ScreenCaptureKit.SCContentFilter.alloc().initWithDisplay_excludingWindows_(
            display, []
        )
        configuration = ScreenCaptureKit.SCStreamConfiguration.alloc().init()
        configuration.setCapturesAudio_(True)
        configuration.setExcludesCurrentProcessAudio_(False)
        configuration.setSampleRate_(CAPTURE_RATE)
        configuration.setChannelCount_(CAPTURE_CHANNELS)
        configuration.setShowsCursor_(False)

        self._output = _stream_output_class().alloc().initWithOwner_(self)
        self._queue = _make_serial_queue("org.meetingrecorder.system-audio")
        self._stream = ScreenCaptureKit.SCStream.alloc().initWithFilter_configuration_delegate_(
            content_filter, configuration, self._output
        )
        added = self._stream.addStreamOutput_type_sampleHandlerQueue_error_(
            self._output,
            ScreenCaptureKit.SCStreamOutputTypeAudio,
            self._queue,
            None,
        )
        if isinstance(added, tuple):
            succeeded, error = added
        else:
            succeeded, error = added, None
        if not succeeded:
            raise RuntimeError(f"Could not attach system-audio output: {_error_text(error)}")

    def start(self):
        self._build()
        self._running = True
        try:
            _await_action(
                self._stream.startCaptureWithCompletionHandler_,
                "start system-audio capture",
            )
        except CaptureUnresolved:
            raise
        except Exception:
            self._running = False
            raise

    def stop(self):
        if self._stream is None or not self._running:
            return
        _await_action(
            self._stream.stopCaptureWithCompletionHandler_,
            "stop system-audio capture",
        )
        self._running = False

    def close(self):
        if self._running:
            self.stop()
        self._stream = None
        self._output = None
        self._queue = None
