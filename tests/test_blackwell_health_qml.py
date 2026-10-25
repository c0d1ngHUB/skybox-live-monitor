"""Exercise the real health QML with clearly synthetic inputs, no production polls."""
import json
import os
from pathlib import Path
import subprocess
import shutil

import pytest

ROOT=Path(__file__).parents[1]


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


def test_health_widget_click_staleness_nulls_and_events(tmp_path):
    runner = qml_runner(tmp_path)
    code='''import QtQuick
import QtTest
import '''+json.dumps((ROOT/'contents/ui').as_uri())+'''
Item {
 id:root; width:860; height:300
 BlackwellHealth { id:h; width:parent.width; height:implicitHeight; pollingEnabled:false }
 TestCase {
  name:"BlackwellHealth"; when:windowShown
  function test_health() {
   compare(h.fresh,false)
   compare(h.total({value:null}),"?")
   compare(h.number(null),"--")
   verify(h.detailText().indexOf("UNBEKANNT")>=0)
   var t=Date.now()/1000
   h.payload={available:true,stamp:t,started:t-60,covered_s:50,energy_wh:1.2,
     average_w:100,average_covered_s:50,journal_available:false,events:[],history:[],
     current:{aer:{"gpu/fatal":0},thermal:false,reasons_complete:true,reasons:["sw_power_cap"],sm_mhz:2000,memory_mhz:14001,limit_w:115}}
   h.nowMs=t*1000
   compare(h.fresh,true);compare(h.uncertain,false)
   h.payload.events=[{kind:"aer",time:t,text:"aer/gpu/fatal +1"}]
   h.payload=Object.assign({},h.payload)
   compare(h.eventWarning,true)
   compare(h.reasonsText(),"POWER-LIMIT")
   h.payload.current.reasons=[];h.payload.current.reasons_complete=false
   h.payload=Object.assign({},h.payload)
   compare(h.reasonsText(),"LIMITS UNBEKANNT")
   mouseClick(h,20,20);compare(h.expanded,true);compare(h.height,255)
   mouseClick(h,20,20);compare(h.expanded,false)
   h.nowMs=(t+30)*1000
   compare(h.fresh,false);compare(h.reasonsText(),"LIMITS UNBEKANNT")
   verify(h.detailText().indexOf("undefined")<0)
  }
 }
}
'''
    (tmp_path/'tst_health.qml').write_text(code)
    result=subprocess.run([runner,'-input',str(tmp_path/'tst_health.qml')],env={**os.environ,'QT_QPA_PLATFORM':'offscreen'},text=True,capture_output=True,timeout=25)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'BlackwellHealth::test_health()' in result.stdout, result.stdout + result.stderr
    assert 'ReferenceError' not in result.stderr and 'TypeError' not in result.stderr
