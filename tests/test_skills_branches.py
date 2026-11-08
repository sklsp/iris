"""The branches of iris/skills.py that tests/test_skills.py does not reach: start_build without a builder
config (the hub fallback), _spec against a missing or broken skill.toml, an approval for a skill whose folder
is gone, an oversized shared zip and approved_tools skipping an approved skill with a broken spec. No real
skill runs; the coding agent is faked in sys.modules."""

import json
import sys
import types
import zipfile

import pytest

from iris import skills


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("IRIS_SKILLS", str(tmp_path / "skills"))
    monkeypatch.setenv("IRIS_TRASH_DIR", str(tmp_path / "trash"))
    monkeypatch.setenv("IRIS_CONFIG", str(tmp_path / "config.toml"))
    return tmp_path


def _building(name, toml_text=None):
    """A skill folder in status building; skill.toml only when given."""
    folder = skills.skills_dir() / name
    folder.mkdir(parents=True)
    if toml_text is not None:
        (folder / "skill.toml").write_text(toml_text, encoding="utf-8")
    skills._save_meta(name, {"name": name, "tier": "safe", "status": "building"})
    return folder


def test_start_build_without_a_builder_starts_the_hub(monkeypatch):
    calls = []

    def start_in_hub(prompt, cwd):
        calls.append((prompt, cwd))
        return "started hub"

    mod = types.ModuleType("iris.autopilot")
    mod.start_in_hub = start_in_hub
    monkeypatch.setitem(sys.modules, "iris.autopilot", mod)
    folder = skills.save_plan("Say hi through the hub", "hub_skill", "safe", "A run() that greets.", set())
    assert skills.start_build("hub_skill") == "started hub"
    prompt, cwd = calls[0]
    assert str(folder / "plan.md") in prompt and cwd == str(folder)
    data = skills.meta("hub_skill")
    assert data["status"] == "building" and data["builder"] == "started hub"


def test_check_reports_a_missing_or_broken_skill_toml():
    _building("no_toml")
    r = skills.check("no_toml")
    assert r["status"] == "blocked"
    assert any(p.startswith("skill.toml is missing or broken:") for p in r["problems"])
    _building("bad_toml", 'description = "x')
    r = skills.check("bad_toml")
    assert r["status"] == "blocked"
    assert any(p.startswith("skill.toml is missing or broken:") for p in r["problems"])


def test_check_reports_params_that_are_not_a_table_of_tables():
    _building("odd_params", 'description = "X."\nparams = "nope"\n')
    r = skills.check("odd_params")
    assert r["status"] == "blocked"
    assert "skill.toml params must be a table of tables" in r["problems"]


def test_an_approval_for_a_missing_skill_is_not_ok():
    d = skills.skills_dir()
    d.mkdir(parents=True, exist_ok=True)
    skills._write(d / "approved.json", json.dumps({"ghost": "abc"}) + "\n")
    assert skills._approved_ok("ghost") is False


def test_import_refuses_a_shared_zip_that_is_too_big(tmp_path):
    big = tmp_path / "big.zip"
    with zipfile.ZipFile(big, "w") as z:
        z.writestr("skill.json", json.dumps({"name": "big_one"}))
        for f in skills.SHARED:
            z.writestr(f, "x" * (skills.MAX_SHARE + 1) if f == "test_skill.py" else "y")
    with pytest.raises(ValueError, match="the shared skill is too big"):
        skills.import_skill(big)


def test_approved_tools_skips_an_approved_skill_with_a_broken_spec():
    name = "broken_toml"
    folder = skills.skills_dir() / name
    folder.mkdir(parents=True)
    (folder / "skill.toml").write_bytes(b'description = "x')
    (folder / "skill.py").write_text("def run():\n    return 'x'\n", encoding="utf-8")
    skills._save_meta(name, {"name": name, "tier": "safe", "status": "ready"})
    skills._write(skills.skills_dir() / "approved.json", json.dumps({name: skills._digest(name, "safe")}) + "\n")
    assert skills._approved_ok(name)
    assert skills.approved_tools(set()) == []


def test_trust_json_that_is_a_list_trusts_nothing():
    d = skills.skills_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / "trust.json").write_text(json.dumps([]), encoding="utf-8")
    assert skills._trust() == {"tiers": {}, "checked": {}}
