"""Exercise the actual Hindsight card and service payload handler without live polls."""
import json
import os
from pathlib import Path
import subprocess

from qml_support import qml_runner

ROOT = Path(__file__).resolve().parents[1]


def test_hindsight_card_distinguishes_active_waiting_idle_and_unavailable(tmp_path):
    source = (ROOT / 'contents/ui/main.qml').read_text()
    heading = source.index('text: "HINDSIGHT"')
    start = source.rindex('                        Rectangle {', 0, heading)
    end = source.index('\n                        }\n                    }', heading)
    card = source[start:end] + '\n                        }'
    service_source = source.split('        id: aiServicesSource\n')[1].split('\n    }', 1)[0]
    handler = service_source.split('onNewData: ', 1)[1]
    reset = source.split('    function resetServices()', 1)[1].split('    function resetNetwork()', 1)[0]
    props = source[source.index('    property string hermesGatewayState:'):source.index('    // HINDSIGHT VERDICHTUNG row')]
    qml = '''import QtQuick
import QtTest
import org.kde.plasma.core as PlasmaCore
Item {
    id: root
    width: 960; height: 60
    property color muted: "#a7b9c8"
    property color ink: "#eff8fa"
    property color neonGreen: "#39FF14"
    property double lastServiceTelemetryMs: 0
    property string buffer: ""
    function disconnectSource(source) {}
    function serviceBorderColor(state) { return muted }
    function serviceToneColor(state) { return ink }
    function serviceStateLabel(state) { return state === "HEALTHY" ? "● AKTIV" : "? UNBEKANNT" }
    function resetServices()''' + reset + props + '''
    property var handle: ''' + handler + '\n' + card + '''
    TestCase {
        name: "HindsightConsolidation"; when: windowShown
        function apply(value) {
            var payload = {gateway: "running", hindsight: "healthy", consolidation: value}
            handle("test", {stdout: JSON.stringify(payload), "exit code": 0})
        }
        function test_status_transitions() {
            var title = findChild(root, "hindsightConsolidationTitle")
            var value = findChild(root, "hindsightConsolidationValue")
            var track = findChild(root, "hindsightConsolidationTrack")
            var bar = findChild(root, "hindsightConsolidationBar")
            verify(title !== null && value !== null && track !== null && bar !== null)
            compare(value.text, "KEINE DATEN")
            compare(title.text, "KONSOLIDIERUNG")
            verify(!track.visible)
            // Running jobs with live progress: "IN BEARBEITUNG" + bar + done/total.
            apply({processing: 2, pending: 1, done: 8, total: 24})
            compare(title.text, "IN BEARBEITUNG")
            compare(title.color, root.neonGreen)
            compare(value.text, "8/24")
            compare(value.color, root.neonGreen)
            verify(track.visible)
            verify(Math.abs(bar.width - (track.width - 2) * 8 / 24) < 0.01, "bar tracks done/total")
            // A single done/total out of range must never exceed the track.
            apply({processing: 1, pending: 0, done: 30, total: 24})
            verify(bar.width <= track.width - 2)
            // Running without usable progress falls back to the job count.
            apply({processing: 1, pending: 0})
            compare(title.text, "IN BEARBEITUNG")
            compare(value.text, "1 LÄUFT")
            compare(value.color, root.neonGreen)
            verify(!track.visible)
            apply({processing: 1, pending: 0, done: 5, total: 0})
            compare(value.text, "1 LÄUFT")
            verify(!track.visible)
            apply({processing: 2, pending: 0, done: null, total: null})
            compare(value.text, "2 LAUFEN")
            verify(!track.visible)
            // Waiting only: the queue count stays visible.
            apply({processing: 0, pending: 2})
            compare(title.text, "KONSOLIDIERUNG")
            compare(title.color, root.muted)
            compare(value.text, "2 WARTEN")
            compare(value.color, root.ink)
            verify(!track.visible)
            apply({processing: 0, pending: 1})
            compare(value.text, "1 WARTET")
            // Idle is quiet, not a zero-count report.
            apply({processing: 0, pending: 0})
            compare(value.text, "RUHE")
            compare(value.color, root.muted)
            verify(!track.visible)
            // Malformed payloads are unavailable, never a zero count.
            apply({processing: 2, pending: null})
            compare(value.text, "KEINE DATEN")
            compare(root.hindsightState, "HEALTHY")
            // Progress survives alongside a waiting queue.
            apply({processing: 1, pending: 2, done: 3, total: 9})
            compare(value.text, "3/9")
            verify(track.visible)
            verify(Math.abs(bar.width - (track.width - 2) * 3 / 9) < 0.01)
            root.resetServices()
            compare(value.text, "KEINE DATEN")
            compare(title.text, "KONSOLIDIERUNG")
            verify(!track.visible)
        }
    }
}
'''
    test_file = tmp_path / 'tst_consolidation.qml'
    test_file.write_text(qml)
    runner = qml_runner(tmp_path)
    assert runner is not None
    result = subprocess.run([runner, '-input', str(test_file)],
                            env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'},
                            capture_output=True, text=True, errors='replace', timeout=25)
    assert result.returncode == 0, result.stdout + result.stderr
