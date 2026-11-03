"""Shared Qt Quick Test runner probe for the QML regression tests."""
import os
import shutil
import subprocess

import pytest


def qml_runner(tmp_path):
    """Probe dependencies only; actual component/test failures must never skip."""
    runner = shutil.which('qmltestrunner') or '/usr/lib/qt6/bin/qmltestrunner'
    probe = tmp_path / 'tst_dependencies.qml'
    probe.write_text('''import QtQuick
import QtTest
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.plasma5support as PlasmaSupport
TestCase { name: "Dependencies"; function test_imports() { verify(true) } }
''')
    try:
        result = subprocess.run([runner, '-input', str(probe)],
            env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'},
            text=True, capture_output=True, timeout=25)
    except OSError as error:
        reason = str(error)
    else:
        if result.returncode == 0:
            if 'Dependencies::test_imports()' not in result.stdout:
                pytest.fail('Qt dependency probe did not execute its test: ' + result.stdout + result.stderr)
            return runner
        reason = result.stdout + result.stderr
    message = 'Qt Quick/Plasma integration dependencies unavailable: ' + reason
    if os.environ.get('SKYBOX_REQUIRE_QML') == '1':
        pytest.fail(message)
    pytest.skip(message)
