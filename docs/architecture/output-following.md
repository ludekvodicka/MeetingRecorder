# System audio follows the call's output device (Windows)

## Decision

On 2026-11-10 a Google Meet call in Brave was recorded without the other side. Meet played
into Bluetooth headphones selected in Meet itself. The Windows default output and the
configured **System audio source** were the speakers on the jack. A WASAPI loopback records
one device, so the recording held only the microphone. Setting the source to **Default**
would not have helped: a browser tab, Teams and Zoom can each choose their own output.

The owner chose that the recorder finds the device by itself during the recording. It
switches without asking, writes a line to the call log and shows the change in the status
bar and on the system meter.

## Behavior

- **Who plays where** (`audio/output_activity.py`). Windows keeps an audio session for every
  program with an open stream on an output device. `WindowsOutputMonitor.outputs()` reads the
  active sessions of every active output through `pycaw`, with the exe names of the process
  and its parents and the session's peak meter. A browser plays through its audio service, a
  child process with the same exe name. A tab with its own output is a second session of the
  same process on another device. The recorder's own process is left out. A look runs on
  the UI thread and takes about 4 ms, because each device name is read only once per
  endpoint ID: `pycaw`'s device list reads every property of every device, about 80 ms.
- **Which device to record** (`audio/output_follow.py`, `OutputFollower.choose`), every
  5 seconds with the call watch (`MainWindow._follow_output`), while the system track records:
  1. **Call rule.** The call programs are the meeting apps and browsers that hold the
     microphone (`MicrophoneUse.call_programs`; the Teams package maps to `ms-teams.exe`).
     When one of them has an active session on other devices and none on the recorded one,
     the recorder switches to the device where it is loudest. The state counts, not the
     level, so a silent moment of the call does not matter.
  2. **Silence rule.** Without a call program on any device: when the recorded device has
     been below the meter's sound level for 15 seconds (`SILENT_SECONDS`), the microphone
     heard speech in those 15 seconds, and another device plays audibly (peak at least 0.01),
     the recorder switches to the loudest such device. This covers a manual recording and a
     call app the recorder does not know. After a switch the new device gets its own
     15 seconds.
- **The switch** (`CaptureSession.switch_system` on the capture thread, then
  `WasapiBackend.switch_system_source`). The new loopback opens first, then the old one
  closes. Both write into the same system WAV, so the recording stays one file. The new
  device is converted to the rate and channel count the track was opened with
  (`Downsampler.convert`, mono copied to every channel; the encoder mixes to mono anyway).
  A stream generation number drops late blocks of the closed stream. A device that cannot
  open keeps the current one and is not tried again in this recording.
- **Silence padding.** A loopback delivers no data while nothing plays on its device: measured
  0 frames in 3 seconds. Before this change the system track lost every such pause and ran
  ahead of the microphone. Now the microphone is the clock: when the loopback has delivered
  nothing for 0.25 seconds (`PAD_AFTER_SECONDS`), each microphone block fills the system track
  with silence up to the microphone's position. A playing loopback is never padded, so clock
  drift between two devices is left as before.
- **Log.** `call-detection.log` gets `call-audio=<devices>` on each change of the devices the
  call programs play on, and one line per switch or failed switch with its reason.

## Constraints and trade-offs

- **Windows only.** macOS (ScreenCaptureKit) and Linux (`.monitor`) have no output monitor and
  `CaptureBackend.switch_system_source` refuses there.
- **A device records everything that plays on it.** Music from another program on the call's
  headphones is in the recording. Recording a process instead of a device (Windows process
  loopback) would avoid that, but a browser process holds every tab, so a second tab playing
  elsewhere would be mixed in. Deferred.
- **The first seconds after a change** of the call's device are missing, up to the 5-second
  look interval. Under the silence rule up to 15 seconds are missing.
- **Two devices with the same name** (two HDMI outputs of one monitor model) cannot be told
  apart: PortAudio names carry no endpoint ID, and the first one is opened.
- **Not verified on a real call yet:** whether new Teams plays through `ms-teams.exe` or a
  child of it, and that a Meet tab with its own output shows as a `brave.exe` session on that
  device. The `call-audio=` entries of the next calls show it.
- Automation verifies the devices against the last Test Audio only at the start; a switch
  later in the recording is expected and does not stop it.

## Verification

`tests/test_output_follow.py` covers both rules, parent processes, the call programs, the
window switching and logging, and a device that fails to open. `tests/test_wasapi_switch.py`
covers padding, no padding while the loopback plays, a switch from 48 kHz stereo to 44.1 kHz
mono into the same track with late blocks of the old stream dropped, and an unknown device.
A live run on this PC recorded the BenQ BL3200 loopback while a helper process played a
-54 dBFS tone on the speakers: the monitor saw `python.exe` on the speakers within a second,
the switch put the tone into the track only after the switch, and the system and microphone
tracks ended with exactly the same frame count (279552).
