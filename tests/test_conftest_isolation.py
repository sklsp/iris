"""The autouse _journal_in_tmp fixture keeps every data file inside tmp_path, so a pytest run never leaves
a folder or a file in the developer's real ~/.iris (the trash alone had 668 entries before this)."""

import os

from iris.policy import _trash_dir


def test_the_trash_used_by_tests_is_inside_tmp_path(tmp_path):
    """The fixture points IRIS_TRASH_DIR into tmp_path, so a skill removal in any test trashes there."""
    assert tmp_path in _trash_dir().parents


def test_every_data_file_points_into_tmp_path(tmp_path):
    """Notes, reminders, the queue and the settings file all live under this test's tmp_path."""
    for var in ("IRIS_NOTES", "IRIS_REMINDERS", "IRIS_QUEUE", "IRIS_SETTINGS"):
        assert os.environ[var].startswith(str(tmp_path)), var
