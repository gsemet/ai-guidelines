"""Contract tests for the publicly installable distribution."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path
from tarfile import open as tar_open

import pytest

pytestmark = pytest.mark.slow


def build_distributions(project_root: Path, output: Path) -> tuple[Path, Path]:
    """Build both standard distributions without using a private index.

    The version is SCM-derived, so the artifact names are discovered from the
    build output rather than hardcoded.
    """
    subprocess.run(
        ["uv", "build", "--out-dir", str(output)],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    )
    wheels = sorted(output.glob("ai_guidelines-*-py3-none-any.whl"))
    sources = sorted(output.glob("ai_guidelines-*.tar.gz"))
    assert len(wheels) == 1, f"expected exactly one wheel, found {wheels}"
    assert len(sources) == 1, f"expected exactly one sdist, found {sources}"
    return wheels[0], sources[0]


def test_distribution_metadata_and_contents(project_root: Path, tmp_path: Path) -> None:
    """The built artifacts expose only the standalone public package contract."""
    wheel, source = build_distributions(project_root, tmp_path)
    assert wheel.is_file()
    assert source.is_file()

    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        metadata_name = next(name for name in names if name.endswith(".dist-info/METADATA"))
        metadata = archive.read(metadata_name).decode()
        entry_points = archive.read(
            next(name for name in names if name.endswith(".dist-info/entry_points.txt"))
        ).decode()
        assert "Name: ai-guidelines" in metadata
        assert "Requires-Python: >=3.10" in metadata
        assert "License-File: LICENSE" in metadata
        assert "Requires-Dist: click" in metadata
        assert "Requires-Dist: pydantic" in metadata
        assert "guidelines = ai_guidelines:main" in entry_points
        assert "ai_guidelines/__init__.py" in names
        assert not any(name.startswith(("tests/", ".github/")) for name in names)
        assert not any("__pycache__" in name or name.endswith(".pyc") for name in names)

    with tar_open(source) as archive:
        names = archive.getnames()
        assert any(name.endswith("/LICENSE") for name in names)
        assert any(name.endswith("/src/ai_guidelines/__init__.py") for name in names)
        assert not any("__pycache__" in name or name.endswith(".pyc") for name in names)


@pytest.mark.skipif(sys.platform == "win32", reason="The local test runner uses POSIX paths.")
def test_installed_wheel_exposes_facade_help_and_local_sync(
    project_root: Path, tmp_path: Path
) -> None:
    """An isolated wheel install can run the documented local-source workflow."""
    wheel, _ = build_distributions(project_root, tmp_path / "dist")
    environment = tmp_path / "venv"
    subprocess.run(
        ["uv", "venv", str(environment)], cwd=project_root, check=True, capture_output=True
    )
    interpreter = environment / "bin/python"
    subprocess.run(
        ["uv", "pip", "install", "--python", str(interpreter), str(wheel)],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    )

    facade = subprocess.run(
        [str(interpreter), "-c", "import ai_guidelines; print(ai_guidelines.__version__)"],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": ""},
    )
    # The version is SCM-derived; assert only that it is a non-empty PEP 440 string.
    assert re.match(r"^\d+\.\d+", facade.stdout.strip()), facade.stdout
    help_result = subprocess.run(
        [str(environment / "bin/guidelines"), "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "sync" in help_result.stdout

    source = tmp_path / "source"
    project = tmp_path / "project"
    source.mkdir()
    project.mkdir()
    (source / "quality.guideline.md").write_text("quality\n", encoding="utf-8")
    command = [str(environment / "bin/guidelines")]
    subprocess.run(
        command + ["add", str(source), "quality", "--alias", "quality"], cwd=project, check=True
    )
    subprocess.run(command + ["sync"], cwd=project, check=True)
    assert (project / ".github/guidelines/quality.guidelines.md").is_file()
    assert (project / "guidelines.lock.json").is_file()
