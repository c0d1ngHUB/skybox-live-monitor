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
            var label = findChild(root, "hindsightConsolidationValue")
            verify(label !== null)
            compare(label.text, "KEINE DATEN")
            apply({processing: 2, pending: 1})
            compare(label.text, "2 LAUFEN · 1 WARTET")
            compare(label.color, root.neonGreen)
            verify(label.contentWidth <= label.width, "label must fit in the existing card")
            apply({processing: 0, pending: 2})
            compare(label.text, "0 LAUFEN · 2 WARTEN")
            apply({processing: 0, pending: 0})
            compare(label.text, "0 LAUFEN · 0 WARTEN")
            apply({processing: 2, pending: null})
            compare(label.text, "KEINE DATEN")
            compare(root.hindsightState, "HEALTHY")
            apply({processing: 1, pending: 0})
            compare(label.text, "1 LÄUFT · 0 WARTEN")
            root.resetServices()
            compare(label.text, "KEINE DATEN")
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
