"""Exercise current card delegates in Qt without polling services or sensors."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

REPO = Path(__file__).resolve().parents[1]


def test_live_card_updates_preserve_hover_and_visible_details(tmp_path):
    runner = shutil.which('qmltestrunner')
    if runner is None and Path('/usr/lib/qt6/bin/qmltestrunner').is_file():
        runner = '/usr/lib/qt6/bin/qmltestrunner'
    if runner is None:
        pytest.skip('Qt Quick Test runner not installed')
    source = (REPO / 'contents/ui/main.qml').read_text()
    properties = source.split('    property real cpu:', 1)[1].split('    fullRepresentation:', 1)[0]
    properties = '    property real cpu:' + properties
    properties = properties.replace('    preferredRepresentation: fullRepresentation', '')
    properties = properties.replace('    Plasmoid.backgroundHints: PlasmaCore.Types.NoBackground', '')
    cards = source.split('// --- Three-GPU row', 1)[1].split('// --- NETWORK section', 1)[0]
    cards = cards[cards.index('            Item {'):]
    cards = cards.replace('Item {', 'Item { id: metricCards; width:860; height:342;', 1)
    cards = cards.replace('delegate: Rectangle {', '''delegate: Rectangle {
        objectName: "card" + index
        Component.onCompleted: root.created++
        Component.onDestruction: root.destroyed++''', 1)
    cards = cards.replace('PlasmaCore.ToolTipArea {', '''PlasmaCore.ToolTipArea {
        onToolTipVisibleChanged: function(v) { if(v) root.shown++; }''')
    cards = cards.replace('mainText: modelData.available ? "VRAM "', 'objectName: "vramTip"; mainText: modelData.available ? "VRAM "')
    cards = cards.replace('mainText: cardData.available ? "VRAM "', 'objectName: "vramTip"; mainText: cardData.available ? "VRAM "')
    cards = cards.replace('text: root.gpuVramUsedText(', 'objectName: "vramValue"; text: root.gpuVramUsedText(')
    cards = cards.replace('width: parent.width * Math.max(', 'objectName: "vramFill"; width: parent.width * Math.max(')
    cards = cards.replace('mainText: cardData.powerText || ""', 'objectName: "powerTip"; mainText: cardData.powerText || ""')
    cards = cards.replace('mainText: root.fmtMemoryPairExact(', 'objectName: "ramTip"; mainText: root.fmtMemoryPairExact(')
    for data in ('modelData', 'cardData'):
        cards = cards.replace('text: ' + data + '.value', 'objectName: "usageValue"; text: ' + data + '.value', 1)
    script = '''import QtQuick
import QtQuick.Layouts
import QtTest
import org.kde.plasma.core as PlasmaCore
import ''' + json.dumps(str(REPO / 'contents/code/monitor_logic.js')) + ''' as MonitorLogic
Item {
    id: root; width:860; height:342
    property int created:0; property int destroyed:0; property int shown:0
''' + properties + cards + '''
    Timer { id:fast; interval:500; repeat:true; property int tick:0
        onTriggered: {
            tick++; root.telemetryNowMs=Date.now()
            root.applyGpuLoads({"0":{uuid:"GPU-a",usage:tick*13%100},
                "1":{uuid:"GPU-b",usage:0},"2":{uuid:"GPU-c",usage:0}},Date.now())
        }
    }
    TestCase {
        name:"SkyboxCardInteraction"; when:windowShown
        function initTestCase() {
            root.gpu0Available=true;root.gpu1Available=true;root.gpu2Available=true
            root.gpu0Temp=30;root.gpu0VramTotalMiB=8192;root.gpu0VramUsedMiB=4096
            root.ramUsedBytes=15000000000;root.ramTotalBytes=32000000000
            root.applyGpuLoads({"0":{uuid:"GPU-a",usage:0},"1":{uuid:"GPU-b",usage:0},
                "2":{uuid:"GPU-c",usage:0}},Date.now())
            wait(100)
        }
        function cleanup() { fast.stop(); mouseMove(root,859,341); wait(100) }
        function test_changing_gpu_usage_does_not_replace_cards_or_starve_hover() {
            var card=findChild(metricCards,"card0")
            var tip=findChild(card,"vramTip"); verify(tip!==null)
            var beforeCreated=root.created,beforeDestroyed=root.destroyed,beforeShown=root.shown
            fast.start(); mouseMove(tip,tip.width/2,tip.height/2); wait(1800)
            compare(root.created,beforeCreated,"Telemetry must preserve card instances")
            compare(root.destroyed,beforeDestroyed)
            compare(findChild(metricCards,"card0"),card)
            verify(root.shown>beforeShown,"Tooltip must open while samples keep changing")
            compare(findChild(card,"usageValue").text,Math.round(root.gpu0Usage)+"%")
        }
        function test_ram_precision_is_available_over_visible_line() {
            var card=findChild(metricCards,"card4"),tip=findChild(card,"ramTip")
            verify(tip!==null); verify(tip.height>=13,"RAM hover target must cover a text line")
            mouseMove(tip,tip.width/2,tip.height/2); wait(1200)
            verify(tip.containsMouse)
            compare(tip.mainText,"14.0 / 29.8 GiB")
        }
        function test_unavailable_gpu_hides_power_and_vram_fill_then_recovers() {
            var entry={short_name:"Synthetic GPU",temperature_c:40,
                memory_used_mib:12000,memory_total_mib:24000,
                power_draw_w:100,power_limit_w:115,process_count:0,processes:[]}
            for(var index=0;index<3;index++) {
                var card=findChild(metricCards,"card"+index)
                var fill=findChild(card,"vramFill"),power=findChild(card,"powerTip")
                root.applyGpuTelemetry(entry,index); wait(20)
                compare(power.mainText,"POWER 100/115 W");verify(power.visible)
                verify(fill.width>0)
                root.applyGpuTelemetry(null,index); wait(20)
                compare(findChild(card,"vramValue").text,"VRAM --")
                compare(findChild(card,"vramTip").mainText,"VRAM UNAVAILABLE")
                compare(power.mainText,"");compare(power.visible,false)
                compare(fill.width,0)
                root.applyGpuTelemetry(entry,index);wait(20)
                compare(power.mainText,"POWER 100/115 W");verify(power.visible)
                verify(fill.width>0)
            }
        }
        function test_unknown_sensor_values_and_valid_zero_have_distinct_cards() {
            root.cpu=Qt.binding(function(){return root.systemSensorValue("cpu")})
            root.ram=Qt.binding(function(){return root.systemSensorValue("ram")})
            root.cpuTemp=Qt.binding(function(){return root.systemSensorValue("cpuTemp")})
            root.recordSystemSensor("cpu",null,true)
            root.recordSystemSensor("ram","unavailable",true)
            root.recordSystemSensor("cpuTemp",null,true)
            wait(20)
            var cpu=findChild(metricCards,"card3"),ram=findChild(metricCards,"card4")
            compare(findChild(cpu,"usageValue").text,"--")
            compare(findChild(ram,"usageValue").text,"--")
            compare(cpu.cardData.detail,"--°C")
            root.recordSystemSensor("cpu",0,true)
            root.recordSystemSensor("ram",0,true)
            root.recordSystemSensor("cpuTemp",0,true)
            wait(20)
            compare(findChild(cpu,"usageValue").text,"0%")
            compare(findChild(ram,"usageValue").text,"0%")
            compare(cpu.cardData.detail,"0°C")
            compare(cpu.cardData.healthLevel,0);compare(ram.cardData.healthLevel,0)
        }
        function test_missing_gpu_watts_keeps_vram_temperature_and_valid_zero() {
            var entry={short_name:"Test",temperature_c:40,memory_used_mib:200,
                memory_total_mib:1000,power_draw_w:null,power_limit_w:115,process_count:0,processes:[]}
            for(var index=0;index<3;index++) {
                var card=findChild(metricCards,"card"+index)
                root.applyGpuTelemetry(entry,index);wait(20)
                compare(findChild(card,"powerTip").mainText,"POWER --/115 W")
                verify(findChild(card,"vramFill").width>0)
                compare(card.cardData.detail,"40°C")
                entry.power_draw_w=0;root.applyGpuTelemetry(entry,index);wait(20)
                compare(findChild(card,"powerTip").mainText,"POWER 0/115 W")
                compare(card.cardData.healthLevel,0)
                entry.power_draw_w=null
            }
        }
        function test_vram_label_is_outside_fill_and_alerts_match_card() {
            root.gpu0Available=true;root.gpu0VramTotalMiB=8192
            var card=findChild(metricCards,"card0")
            var value=findChild(card,"vramValue"),fill=findChild(card,"vramFill")
            verify(value!==null && fill!==null)
            var thresholds=[40,85,95]
            for(var i=0;i<thresholds.length;i++) {
                root.gpu0VramUsedMiB=8192*thresholds[i]/100; wait(30)
                card=findChild(metricCards,"card0")
                value=findChild(card,"vramValue");fill=findChild(card,"vramFill")
                verify(value.mapToItem(card,0,value.height).y<=fill.mapToItem(card,0,0).y,
                    "VRAM text must not cross filled/unfilled backgrounds")
                if(thresholds[i]>=85) compare(fill.color,card.border.color)
            }
        }
    }
}
'''
    (tmp_path / 'tst_cards.qml').write_text(script)
    result = subprocess.run([runner, '-input', str(tmp_path)],
                            env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'},
                            capture_output=True, text=True, timeout=25)
    assert result.returncode == 0, result.stdout + result.stderr
