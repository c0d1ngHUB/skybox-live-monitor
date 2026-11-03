"""Dependency handling must skip locally but fail closed in integration CI."""
from pathlib import Path
import subprocess

import pytest

import qml_support

ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize("required", [False, True])
def test_missing_qml_runner_is_explicit(monkeypatch, tmp_path, required):
    monkeypatch.setenv("SKYBOX_REQUIRE_QML", "1" if required else "0")
    real_run = qml_support.subprocess.run

    def missing(args, **kwargs):
        if 'qmltestrunner' in str(args[0]):
            raise FileNotFoundError('synthetic missing Qt runner')
        return real_run(args, **kwargs)

    monkeypatch.setattr(qml_support.subprocess, "run", missing)
    with pytest.raises(pytest.fail.Exception if required else pytest.skip.Exception):
        qml_support.qml_runner(tmp_path)


@pytest.mark.parametrize("required", [False, True])
def test_missing_qml_imports_is_explicit(monkeypatch, tmp_path, required):
    monkeypatch.setenv("SKYBOX_REQUIRE_QML", "1" if required else "0")
    monkeypatch.setattr(qml_support.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, '', 'module "org.kde.plasma.core" is not installed'))
    with pytest.raises(pytest.fail.Exception if required else pytest.skip.Exception):
        qml_support.qml_runner(tmp_path)


@pytest.mark.parametrize("required", [False, True])
def test_zero_discovered_qml_tests_cannot_pass(monkeypatch, tmp_path, required):
    monkeypatch.setenv("SKYBOX_REQUIRE_QML", "1" if required else "0")
    monkeypatch.setattr(qml_support.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, '0 passed, 0 failed', ''))
    with pytest.raises(pytest.fail.Exception, match='did not execute'):
        qml_support.qml_runner(tmp_path)


def test_ci_installs_qt6_plasma_before_required_regressions():
    workflow = (ROOT / '.github/workflows/ci.yml').read_text()
    assert 'container: debian:trixie' in workflow  # Ubuntu 24.04 only packages Plasma 5.
    assert 'SKYBOX_REQUIRE_QML: "1"' in workflow
    install = workflow.index('apt-get install')
    run = workflow.index('python3 -m pytest')
    for package in ('qt6-declarative-dev-tools', 'qml6-module-qttest', 'plasma-desktoptheme', 'qml6-module-org-kde-plasma-plasma5support'):
        assert package in workflow[install:run]
    assert install < run
