"""Exercise the real health QML with clearly synthetic inputs, no production polls."""
import json
import os
from pathlib import Path
import subprocess

ROOT=Path(__file__).parents[1]

def test_health_widget_click_staleness_nulls_and_events(tmp_path):
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
     current:{ecc_mode:"Enabled",ecc:{dram_correctable:0},aer:{"gpu/fatal":0},thermal:false,reasons_complete:true,reasons:["sw_power_cap"],sm_mhz:2000,memory_mhz:14001,limit_w:115}}
   h.nowMs=t*1000
   compare(h.fresh,true);compare(h.uncertain,true)
   h.payload.events=[{kind:"ecc",time:t,text:"ecc/dram_correctable +1"}]
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
    result=subprocess.run(['/usr/lib/qt6/bin/qmltestrunner','-input',str(tmp_path)],env={**os.environ,'QT_QPA_PLATFORM':'offscreen'},text=True,capture_output=True,timeout=25)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'ReferenceError' not in result.stderr and 'TypeError' not in result.stderr
