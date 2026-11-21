"""VoiceSession._transcribe timing: the whole call lands in last_stt_ms, a second call overwrites it,
and an empty transcript still sets it. No device is opened and no model is loaded: _whisper is faked."""

from typing import cast

from iris.voice import Speaker, VoiceSession


class _Seg:
    def __init__(self, text):
        self.text = text


class _FakeWhisper:
    """Stands in for faster_whisper.WhisperModel; transcribe returns the segments it was given."""

    def __init__(self, segments):
        self.segments = list(segments)

    def transcribe(self, audio, vad_filter=False):
        return list(self.segments), None


class _StepClock:
    """A clock that steps by a fixed amount on every call, so the elapsed time is exact and literal."""

    def __init__(self, step):
        self.step = step
        self.now = 0.0

    def __call__(self) -> float:
        self.now += self.step
        return self.now


class _StubSpeaker:
    def speak(self, text, stop_event=None):
        return True


def _session(step=1.0, segments=()):
    s = VoiceSession(speaker=cast(Speaker, _StubSpeaker()), clock=_StepClock(step))
    s._whisper = _FakeWhisper(segments)  # no device, no model: the timing still covers a real load when it happens
    return s


def test_transcribe_keeps_the_whole_call_in_last_stt_ms():
    s = _session(segments=[_Seg("  hello "), _Seg("world")])

    assert s.last_stt_ms is None  # nothing has been transcribed yet
    text = s._transcribe(None)

    assert text == "hello world"  # the return value is unchanged: stripped, joined with single spaces
    assert s.last_stt_ms == 1000  # two clock steps of 1.0 s apart


def test_second_transcribe_overwrites_the_timing():
    s = _session(segments=[_Seg("one")])

    s._transcribe(None)
    s._transcribe(None)

    assert s.last_stt_ms == 1000  # the second call's own span, not accumulated over both calls


def test_empty_transcript_still_sets_last_stt_ms():
    s = _session(segments=[])

    text = s._transcribe(None)

    assert text == ""
    assert s.last_stt_ms == 1000
