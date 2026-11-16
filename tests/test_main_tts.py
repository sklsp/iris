"""log_tts in iris/__main__.py: an answer's first-sound time goes to the journal as a tts event; nothing is written
when the speaker never reported one."""

import json

import iris.__main__ as imain
from iris.agent import Journal


def test_first_sound_time_writes_one_tts_entry(tmp_path):
    j = Journal(tmp_path / "journal.jsonl")

    imain.log_tts(j, 420)

    lines = (tmp_path / "journal.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    entry = {k: v for k, v in json.loads(lines[0]).items() if k != "t"}  # ignore the timestamp
    assert entry == {"event": "tts", "ms": 420}


def test_none_writes_nothing(tmp_path):
    j = Journal(tmp_path / "journal.jsonl")

    imain.log_tts(j, None)

    assert not (tmp_path / "journal.jsonl").exists()
