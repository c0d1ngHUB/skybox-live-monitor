"""Run real KDE Sensors on a private bus; never alter the desktop daemon."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_real_sensor_samples_constant_zero_invalid_and_service_loss(tmp_path):
    runner = shutil.which('qmltestrunner') or '/usr/lib/qt6/bin/qmltestrunner'
    if not Path(runner).exists() or not shutil.which('dbus-run-session'):
        if os.environ.get('SKYBOX_REQUIRE_QML') == '1':
            pytest.fail('Qt and private D-Bus runner are required')
        pytest.skip('Qt and private D-Bus runner unavailable')
    # pytest may use a managed Python without Debian's dbus/gi packages.
    backend_python = '/usr/bin/python3'
    try:
        subprocess.run([backend_python, '-c', 'import dbus,gi'], check=True, capture_output=True)
    except subprocess.CalledProcessError:
        if os.environ.get('SKYBOX_REQUIRE_QML') == '1':
            pytest.fail('Python dbus and gi are required for sensor integration')
        pytest.skip('Python dbus/gi unavailable')
    source = (ROOT / 'contents/ui/main.qml').read_text()
    props = '    property real cpu:' + source.split('    property real cpu:', 1)[1].split('    fullRepresentation:', 1)[0]
    props = props.replace('    preferredRepresentation: fullRepresentation', '').replace('    Plasmoid.backgroundHints: PlasmaCore.Types.NoBackground', '')
    sensors = '\n'.join(line for line in source.splitlines() if 'Sensors.Sensor {' in line and any(f'sensorId: "{sensor}"' in line for sensor in ('cpu/all/usage', 'cpu/all/averageTemperature', 'memory/physical/usedPercent', 'memory/physical/used', 'memory/physical/total')))
    qml = '''import QtQuick
import QtTest
import org.kde.plasma.core as PlasmaCore
import org.kde.ksysguard.sensors 1.0 as Sensors
import ''' + json.dumps(str(ROOT / 'contents/code/monitor_logic.js')) + ''' as MonitorLogic
Item { id:root
''' + props + sensors + '''
    Sensors.Sensor { id:driver; sensorId:"test/idle"; enabled:true }
    Timer { interval:20; running:true; repeat:true; onTriggered: root.telemetryNowMs=Date.now() }
    TestCase { name:"SystemSensors"; when:windowShown
        function test_samples_and_disconnect() {
            wait(100)
            verify(isNaN(root.cpu),"Metadata readiness must not invent a zero before the first sample")
            driver.sensorId="test/constant"
            tryCompare(root,"cpu",42,2000)
            var stamp=root.systemSensorStates.cpu.stampMs
            wait(250)
            verify(root.systemSensorStates.cpu.stampMs>stamp,"Identical samples must renew receipt age")
            compare(root.cpu,42)
            driver.sensorId="test/invalid"
            tryVerify(function(){return isNaN(root.cpu)},2000)
            verify(isNaN(root.ram));verify(isNaN(root.cpuTemp))
            driver.sensorId="test/zero"
            tryCompare(root,"cpu",0,2000)
            compare(root.ram,0);compare(root.cpuTemp,0);compare(root.ramUsedBytes,0)
            compare(root.fmtMemoryPair(root.ramUsedBytes,root.ramTotalBytes),"0 / 1 GiB")
            driver.sensorId="test/lost"
            tryVerify(function(){return isNaN(root.cpu)},7000)
            verify(isNaN(root.ram));verify(isNaN(root.cpuTemp))
        }
    }
}
'''
    (tmp_path / 'tst_sensors.qml').write_text(qml)
    backend = tmp_path / 'backend.py'
    backend.write_text('''import dbus,dbus.service,dbus.mainloop.glib
from gi.repository import GLib
import os,subprocess,sys
dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
bus=dbus.SessionBus(); name=dbus.service.BusName('org.kde.ksystemstats1',bus)
class Backend(dbus.service.Object):
    subscribed=set()
    mode='idle'
    @dbus.service.method('org.kde.ksystemstats1',in_signature='as',out_signature='a{s(sssuidd)}')
    def sensors(self,ids):
        for sensor in ids:
            if str(sensor).startswith('test/'):
                self.mode=str(sensor).split('/')[1]
                if self.mode=='lost': bus.release_name('org.kde.ksystemstats1')
        return {str(i):(str(i),str(i),'',dbus.UInt32(6),dbus.Int32(0),dbus.Double(0),dbus.Double(100)) for i in ids}
    @dbus.service.method('org.kde.ksystemstats1',in_signature='as',out_signature='a(sv)')
    def sensorData(self,ids): return []
    @dbus.service.method('org.kde.ksystemstats1',in_signature='as',out_signature='')
    def subscribe(self,ids): self.subscribed.update(map(str,ids))
    @dbus.service.method('org.kde.ksystemstats1',in_signature='as',out_signature='')
    def unsubscribe(self,ids): pass
    @dbus.service.signal('org.kde.ksystemstats1',signature='a(sv)')
    def newSensorData(self,data): pass
backend=Backend(bus,'/org/kde/ksystemstats1')
child=subprocess.Popen(sys.argv[1:],env={**os.environ,'QT_QPA_PLATFORM':'offscreen'},stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
loop=GLib.MainLoop()
def tick():
    if backend.mode in ('constant','invalid','zero'):
        raw={'constant':42,'invalid':'invalid','zero':0}[backend.mode]
        data=[]
        for sensor in backend.subscribed:
            if sensor.startswith('test/'): continue
            value=1073741824 if sensor.endswith('/total') else raw
            value=dbus.String(value,variant_level=1) if isinstance(value,str) else dbus.Double(value,variant_level=1)
            data.append((sensor,value))
        backend.newSensorData(data)
    return True
def check():
    if child.poll() is not None:
        loop.quit(); return False
    return True
GLib.timeout_add(100,tick);GLib.timeout_add(50,check);loop.run()
print(child.communicate()[0]);sys.exit(child.returncode)
''')
    config = tmp_path / 'bus.conf'
    config.write_text('<busconfig><type>session</type><listen>unix:tmpdir=/tmp</listen>'
                      '<policy context="default"><allow send_destination="*"/>'
                      '<allow receive_sender="*"/><allow own="*"/></policy></busconfig>')
    result = subprocess.run(['dbus-run-session','--config-file='+str(config),'--',backend_python,str(backend),runner,'-input',str(tmp_path)],
                            capture_output=True,text=True,timeout=18)
    assert result.returncode == 0, result.stdout + result.stderr
