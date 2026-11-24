from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "check_ci_python_versions", Path(__file__).resolve().parents[1] / "scripts" / "check_ci_python_versions.py"
)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

CYCLES = [
    {"cycle": "3.15", "eol": "2031-10-31"},
    {"cycle": "3.10", "eol": "2026-10-31"},
    {"cycle": "3.9", "eol": "2025-10-31"},
]


def repo(tmp_path: Path, workflow: str, requires: str | None = None, name: str = "r") -> Path:
    root = tmp_path / name
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "workflows" / "ci.yml").write_text(workflow)
    if requires:
        (root / "pyproject.toml").write_text(f'[project]\nrequires-python = "{requires}"\n')
    return root


def test_hardcoded_lists_are_found_and_auto_is_clean(tmp_path):
    bad = repo(tmp_path, "jobs:\n  ci:\n    with:\n      python-versions: '[\"3.10\", \"3.12\"]'  # old\n", name="bad")
    good = repo(tmp_path, "jobs:\n  ci:\n    with:\n      python-versions: auto\n  ic:\n    with:\n      python-versions: auto-edges\n", name="good")
    assert mod.hardcoded_lists(bad) == [("ci.yml", '[\"3.10\", \"3.12\"]')]
    assert mod.hardcoded_lists(good) == []


def test_expression_values_are_not_flagged(tmp_path):
    r = repo(tmp_path, "python-versions: ${{ inputs.versions }}\n")
    assert mod.hardcoded_lists(r) == []


def test_eol_floor_is_reported_only_when_the_feed_says_so(tmp_path):
    r = repo(tmp_path, "python-versions: auto\n", requires=">=3.10")
    eol = mod.eol_minors(date(2026, 11, 20), CYCLES)
    assert eol == {"3.10", "3.9"}
    assert any("end of life" in p for p in mod.check_repo(r, eol))
    assert mod.check_repo(r, mod.eol_minors(date(2026, 1, 1), CYCLES)) == []
    assert mod.check_repo(r, None) == []  # offline: skip, do not guess


def test_discover_skips_vendored_and_archived(tmp_path):
    repo(tmp_path, "x: 1\n", name="a")
    repo(tmp_path, "x: 1\n", name="archive-repos/old")
    repo(tmp_path, "x: 1\n", name="b/.venv/lib")
    assert [p.name for p in mod.discover(tmp_path)] == ["a"]


def test_strict_exit_code(tmp_path, capsys):
    r = repo(tmp_path, "python-versions: '[\"3.12\"]'\n")
    assert mod.main(["--repo", str(r)]) == 0
    assert mod.main(["--repo", str(r), "--strict"]) == 1
    assert "hard-coded" in capsys.readouterr().out


def test_prose_mentioning_python_versions_is_not_flagged(tmp_path):
    doc = repo(tmp_path, "on:\n  inputs:\n    python-versions:\n      description: >-\n        text\n", name="doc")
    assert mod.hardcoded_lists(doc) == []


PINNED_MATRIX = (
    "jobs:\n  pytest:\n    strategy:\n      matrix:\n        python-version: [\"3.10\"]\n"
    "    steps:\n      - run: pip install git+https://github.com/casonk/dyno-lab.git\n"
)


def sibling(tmp_path: Path, name: str, requires: str) -> Path:
    path = tmp_path / name
    path.mkdir()
    (path / "pyproject.toml").write_text(f'[project]\nrequires-python = "{requires}"\n')
    return path


def test_matrix_python_version_list_is_flagged_as_hardcoded(tmp_path):
    bad = repo(tmp_path, PINNED_MATRIX, name="bad")
    assert mod.hardcoded_lists(bad) == [("ci.yml", '["3.10"]')]


def test_ci_python_below_a_sibling_dependency_floor_is_reported(tmp_path):
    sibling(tmp_path, "dyno-lab", ">=3.11")
    bad = repo(tmp_path, PINNED_MATRIX, name="app")
    problems = mod.check_repo(bad, None)
    assert any("tests Python 3.10 but dependency dyno-lab requires >=3.11" in p for p in problems)


def test_declared_floor_below_a_sibling_dependency_floor_is_reported(tmp_path):
    sibling(tmp_path, "dyno-lab", ">=3.11")
    bad = repo(tmp_path, "jobs: {}\n# pip install git+https://github.com/casonk/dyno-lab.git\n", requires=">=3.10", name="app")
    assert any("below dependency dyno-lab's >=3.11" in p for p in mod.check_repo(bad, None))


def test_auto_versions_and_missing_siblings_are_clean(tmp_path):
    ok = repo(tmp_path, "python-versions: auto\n# git+https://github.com/casonk/dyno-lab.git\n", name="ok")
    assert mod.check_repo(ok, None) == []  # sibling not checked out: nothing to compare
    sibling(tmp_path, "dyno-lab", ">=3.11")
    assert mod.check_repo(ok, None) == []


def test_strict_exits_nonzero_on_a_dependency_conflict(tmp_path):
    sibling(tmp_path, "dyno-lab", ">=3.11")
    bad = repo(tmp_path, PINNED_MATRIX, name="app")
    assert mod.main(["--repo", str(bad), "--strict"]) == 1
