"""Which output device the system track should record, decided again during the recording.

A call app can play on a device other than the Windows default: a browser tab may choose
its own output, Teams has its own speaker setting. Recording the configured or default
device then records silence while the other side talks into headphones. Two rules, in order:

* **The call program plays elsewhere.** When a program holding the microphone (a meeting
  app or a browser) has an active session on another device and none on the recorded one,
  follow it to the device where it is loudest.
* **Silence while you speak.** Without such a program, when the recorded device has been
  silent for SILENT_SECONDS while the microphone heard speech in that time, and another
  device plays something, follow the loudest one. This covers a manual recording and a
  call program the recorder does not know.
"""

from dataclasses import dataclass

# How long the recorded device may stay silent while you speak before another one is tried.
SILENT_SECONDS = 15.0
# Meter levels (0 to 1, from rms_level) that count as sound. The same as the meters' grey.
SOUND_LEVEL = 0.08
# A session peak (linear amplitude) that counts as playing something audible.
PLAYING_PEAK = 0.01


@dataclass(frozen=True)
class OutputChoice:
    name: str
    reason: str  # "call" or "silence"


class OutputFollower:
    def __init__(self, now):
        self.last_system_sound = now
        self.last_speech = None

    def system_level(self, level, now):
        if level >= SOUND_LEVEL:
            self.last_system_sound = now

    def mic_level(self, level, now):
        if level >= SOUND_LEVEL:
            self.last_speech = now

    def choose(self, current, outputs, call_programs, now):
        """The device to switch to, or None to stay on ``current``."""
        call_outputs = [o for o in outputs if call_programs and o.plays(call_programs)]
        if call_outputs:
            if any(o.name == current for o in call_outputs):
                return None
            best = max(call_outputs, key=lambda o: o.peak(call_programs))
            return self._switched(best.name, "call", now)

        silent_for = now - self.last_system_sound
        spoke = self.last_speech is not None and now - self.last_speech < SILENT_SECONDS
        if silent_for < SILENT_SECONDS or not spoke:
            return None
        playing = [o for o in outputs if o.name != current and o.peak() >= PLAYING_PEAK]
        if not playing:
            return None
        return self._switched(max(playing, key=lambda o: o.peak()).name, "silence", now)

    def _switched(self, name, reason, now):
        # The new device gets its own full silence period before the next silence switch.
        self.last_system_sound = now
        return OutputChoice(name, reason)
