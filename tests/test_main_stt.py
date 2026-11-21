"""log_stt in iris/__main__.py: a turn's speech-recognition time goes to the journal as an stt event; nothing is
written when the session has no timing (text mode and push-to-talk have no session at all)."""

import json

import iris.__main__ as imain
from iris.agent import Journal


class _Session:
    def __init__(self, last_stt_ms):
        self.last_stt_ms = last_stt_ms


def test_session_with_timing_writes_one_stt_entry(tmp_path):
    j = Journal(tmp_path / "journal.jsonl")

    imain.log_stt(j, _Session(640))

    lines = (tmp_path / "journal.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    entry = {k: v for k, v in json.loads(lines[0]).items() if k != "t"}  # ignore the timestamp
    assert entry == {"event": "stt", "ms": 640}


def test_none_timing_writes_nothing(tmp_path):
    j = Journal(tmp_path / "journal.jsonl")

    imain.log_stt(j, _Session(None))

    assert not (tmp_path / "journal.jsonl").exists()


def test_missing_attribute_writes_nothing(tmp_path):
    j = Journal(tmp_path / "journal.jsonl")

    imain.log_stt(j, object())  # text mode and push-to-talk pass no session at all

    assert not (tmp_path / "journal.jsonl").exists()
