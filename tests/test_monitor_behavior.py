#!/usr/bin/env python3
"""Behavioral regression tests for monitor logic and executable data sources."""

import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).parents[1]
QML = ROOT / "contents/ui/main.qml"
LOGIC = ROOT / "contents/code/monitor_logic.js"
NETWORK_COUNTERS = ROOT / "contents/code/network_counters.sh"
CPU_SNAPSHOT = ROOT / "contents/code/cpu_process_snapshot.py"
README = ROOT / "README.md"


def command_for(source_id: str) -> str:
    text = QML.read_text()
    start = text.index(f"id: {source_id}")
    match = re.search(r'property string command:\s*("(?:[^"\\]|\\.)*")', text[start:])
    if not match:
        raise AssertionError(f"No command found for {source_id}")
    return json.loads(match.group(1))


def executable(path: Path, body: str) -> None:
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class MonitorBehaviorTests(unittest.TestCase):
    def test_history_points_are_right_aligned_until_window_is_full(self):
        script = (
            f"const m=require({json.dumps(str(LOGIC))});"
            "console.log(JSON.stringify([m.historyX(0,1,300,300),m.historyX(0,2,300,300),m.historyX(1,2,300,300),m.historyX(0,300,300,300)]));"
        )
        result = subprocess.run(["node", "-e", script], text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(result.stdout), [300, 298.99665551839465, 300, 0])

    def test_history_fills_close_at_visible_sample_bounds(self):
        text = QML.read_text()
        self.assertEqual(text.count("var firstX ="), 3)
        self.assertEqual(text.count("var lastX ="), 3)
        self.assertEqual(text.count("ctx.lineTo(firstX, height)"), 3)
        self.assertEqual(text.count("ctx.lineTo(lastX, height)"), 3)

    def test_service_state_normalization_is_semantically_stable(self):
        script = (
            f"const m=require({json.dumps(str(LOGIC))});"
            "console.log(JSON.stringify(["
            "m.normalizeServiceState('running'),"
            "m.normalizeServiceState('healthy'),"
            "m.normalizeServiceState('idle'),"
            "m.normalizeServiceState('down'),"
            "m.serviceSymbol('OPERATIONAL'),"
            "m.serviceSymbol('DEGRADED'),"
            "m.serviceSymbol('OFFLINE'),"
            "m.openAiOauthState(3, 3),"
            "m.openAiOauthState(0, 3),"
            "m.openAiOauthState(1, 3)"
            "]));"
        )
        result = subprocess.run(["node", "-e", script], text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(result.stdout), ["OPERATIONAL", "OPERATIONAL", "OPERATIONAL", "OFFLINE", "●", "▲", "✕", "OPERATIONAL", "OFFLINE", "DEGRADED"])

    def test_unconfigured_openai_store_is_not_reported_as_an_outage(self):
        """0/0 keys means "never set up", not "down".

        A missing credential store used to surface as OFFLINE, which colours the
        header banner critical and claims an outage that does not exist.
        """
        script = (
            f"const m=require({json.dumps(str(LOGIC))});"
            "console.log(JSON.stringify(["
            "m.openAiOauthState(0, 0),"
            "m.openAiOauthState(-1, 0),"
            "m.serviceSymbol(m.openAiOauthState(0, 0)),"
            "m.serviceTone(m.openAiOauthState(0, 0)),"
            "m.normalizeServiceState('NOT_CONFIGURED'),"
            "m.serviceSymbol('NOT_CONFIGURED'),"
            "m.normalizeServiceState('offline'),"
            "m.serviceTone('OFFLINE')"
            "]));"
        )
        result = subprocess.run(["node", "-e", script], text=True, capture_output=True, check=True)
        self.assertEqual(
            json.loads(result.stdout),
            ["NOT_CONFIGURED", "UNKNOWN", "○", "muted", "NOT_CONFIGURED", "○", "OFFLINE", "critical"],
        )

    def test_network_rate_and_axis_agree_on_one_unit_threshold(self):
        """The panel picks one unit for axis and rate from peak + live value.

        Two disagreements used to exist: the axis label followed the chart ceiling
        (peak + 15% headroom) while the rate followed the raw value, and the ceiling
        itself is retained with hysteresis, so after a short burst the axis stayed in
        MBIT/S while the live line read KBIT/S (observed live: axis "UPLOAD · MBIT/S"
        over "↑ 12.9 KBIT/S" at a 0.035 Mbit peak).
        """
        script = (
            f"const m=require({json.dumps(str(LOGIC))});"
            "console.log(JSON.stringify(["
            "m.networkRateLabel(100000),"
            "m.networkRateLabel(125000),"
            "m.networkRateLabel(150000),"
            "m.networkAxisUnit(150000, 150000),"
            "m.networkAxisUnit(100000, 100000),"
            "m.networkRateLabel(4346, m.networkPanelUsesMbit(4346, 4346)),"
            "m.networkAxisUnit(4346, 4346),"
            "m.networkAxisUnit(0, 0),"
            # A retained Mbit ceiling must not force the panel into MBIT/S.
            "m.networkAxisUnit(4346, 4346),"
            "m.networkRateLabel(4346, m.networkPanelUsesMbit(4346, 4346)),"
            # Live spike above the window peak still lifts the panel to Mbit.
            "m.networkAxisUnit(0, 150000),"
            "m.networkRateLabel(150000, m.networkPanelUsesMbit(0, 150000))"
            "]));"
        )
        result = subprocess.run(["node", "-e", script], text=True, capture_output=True, check=True)
        self.assertEqual(
            json.loads(result.stdout),
            [
                "800 KBIT/S",
                "1 MBIT/S",
                "1.2 MBIT/S",
                "MBIT/S",
                "KBIT/S",
                "34.8 KBIT/S",
                "KBIT/S",
                "KBIT/S",
                "KBIT/S",
                "34.8 KBIT/S",
                "MBIT/S",
                "1.2 MBIT/S",
            ],
        )


    def test_slow_disk_poll_gets_its_own_stale_budget(self):
        """A 30 s disk poll must not be flagged stale by the 15 s default window."""
        script = (
            f"const m=require({json.dumps(str(LOGIC))});"
            "console.log(JSON.stringify(["
            "m.staleAfterMsFor('diskUsed', 15000),"
            "m.staleAfterMsFor('diskPercent', 15000),"
            "m.staleAfterMsFor('diskTotal', 15000),"
            "m.staleAfterMsFor('cpuUsage', 15000),"
            # 25 s after the last disk read: healthy, not stale.
            "m.staleDomains(25000,{cpuUsage:25000,cpuTemperature:25000,gpu0Telemetry:25000,gpu1Telemetry:25000,memoryPercent:25000,memoryUsed:25000,memoryTotal:25000,network:25000,diskPercent:25000,diskUsed:25000,diskTotal:25000,uptime:25000,loadAverage:25000},15000),"
            # 100 s after the last disk read: stale again.
            "m.staleDomains(100000,{cpuUsage:100000,cpuTemperature:100000,gpu0Telemetry:100000,gpu1Telemetry:100000,memoryPercent:100000,memoryUsed:100000,memoryTotal:100000,network:100000,diskPercent:0,diskUsed:0,diskTotal:0,uptime:100000,loadAverage:100000},15000)"
            "]));"
        )
        result = subprocess.run(["node", "-e", script], text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(result.stdout), [90000, 90000, 90000, 15000, [], ["DISK"]])

    def test_disk_card_no_longer_depends_on_the_sensor_accounting(self):
        """The df view moved to contents/code/disk_usage.py, covered by
        tests/test_disk_usage.py; the old JS helpers and the sensors that carried
        the f_bavail accounting must be gone from both files."""
        logic = LOGIC.read_text()
        qml = QML.read_text()
        for gone in ("diskUsedBytes", "diskReservedBytes", "diskUsedPercent"):
            self.assertNotIn(gone, logic)
        self.assertNotIn('sensorId: "disk/all/used"', qml)
        self.assertNotIn('sensorId: "disk/all/free"', qml)
        self.assertIn("disk_usage.py", qml)

    def test_freshness_reports_each_stale_domain(self):
        script = (
            f"const m=require({json.dumps(str(LOGIC))});"
            "console.log(JSON.stringify(m.staleDomains(20000,{cpuUsage:19000,cpuTemperature:19000,gpu0Telemetry:19000,gpu1Telemetry:0,memoryPercent:18000,memoryUsed:18000,memoryTotal:18000,network:10000,diskPercent:19500,diskUsed:19500,diskTotal:19500,uptime:19000,loadAverage:19000},6000)));"
        )
        result = subprocess.run(["node", "-e", script], text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(result.stdout), ["GPU 1", "NETWORK"])

    def test_cpu_process_rates_use_per_pid_cpu_time_deltas(self):
        script = (
            f"const m=require({json.dumps(str(LOGIC))});"
            "const previous={'10':{cpuSeconds:4},'20':{cpuSeconds:8},'30':{cpuSeconds:9},'40':{cpuSeconds:10},'50':{cpuSeconds:1}};"
            "const current=[{pid:10,cpuSeconds:6,name:'fast'},{pid:20,cpuSeconds:8.5,name:'slow'},{pid:30,cpuSeconds:10,name:'third'},{pid:40,cpuSeconds:11,name:'fourth'},{pid:50,cpuSeconds:1.2,name:'fifth'},{pid:60,cpuSeconds:9,name:'new'}];"
            "console.log(JSON.stringify(m.cpuProcessRates(previous,current,5000,4)));"
        )
        result = subprocess.run(["node", "-e", script], text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(result.stdout), [
            {"pid": 10, "name": "fast", "cpu": "40.0"},
            {"pid": 30, "name": "third", "cpu": "20.0"},
            {"pid": 40, "name": "fourth", "cpu": "20.0"},
            {"pid": 20, "name": "slow", "cpu": "10.0"},
        ])

    def test_cpu_snapshot_reads_precise_proc_jiffies_and_process_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = Path(tmp)
            (proc / "42").mkdir()
            # Fields 14 and 15 are utime/stime; the comm field may contain spaces.
            (proc / "42/stat").write_text(
                "42 (render worker) S 1 2 3 4 5 6 7 8 9 10 125 25 0 0 0 0 0 0 0 0\n"
            )
            result = subprocess.run(
                ["python3", str(CPU_SNAPSHOT), "--proc-root", str(proc), "--clock-ticks", "100"],
                text=True, capture_output=True, check=True,
            )
            self.assertEqual(result.stdout.strip(), "42 1.500000 render worker")

    def test_nvidia_memory_parser_selects_first_gpu_from_multi_gpu_output(self):
        output = "0, 100, 1000\n1, 200, 2000"
        script = (
            f"const m=require({json.dumps(str(LOGIC))});"
            f"console.log(JSON.stringify(m.parseNvidiaMemory({json.dumps(output)},0)));"
        )
        result = subprocess.run(["node", "-e", script], text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(result.stdout), {"usedMiB": 100, "totalMiB": 1000})

    def test_network_scale_uses_50_mbit_steps_for_download(self):
        # 180 Mbit/s peak → 180/0.85 ≈ 207 Mbit → ceil(207/50)·50 = 250
        samples = [180000000/8] * 5
        script = (
            f"const m=require({json.dumps(str(LOGIC))});"
            f"console.log(m.networkScaleMbit({json.dumps(samples)},50,600));"
        )
        result = subprocess.run(["node", "-e", script], text=True, capture_output=True, check=True)
        self.assertEqual(int(result.stdout.strip()), 250)

    def test_network_scale_caps_at_600_mbit(self):
        samples = [700000000/8] * 3
        script = (
            f"const m=require({json.dumps(str(LOGIC))});"
            f"console.log(m.networkScaleMbit({json.dumps(samples)},50,600));"
        )
        result = subprocess.run(["node", "-e", script], text=True, capture_output=True, check=True)
        self.assertEqual(int(result.stdout.strip()), 600)

    def test_network_scale_uses_5_mbit_steps_for_upload(self):
        # 12 Mbit/s peak → 12/0.85 ≈ 13.8 → ceil(13.8/5)·5 = 15
        samples = [12000000/8] * 5
        script = (
            f"const m=require({json.dumps(str(LOGIC))});"
            f"console.log(m.networkScaleMbit({json.dumps(samples)},5,50));"
        )
        result = subprocess.run(["node", "-e", script], text=True, capture_output=True, check=True)
        self.assertEqual(int(result.stdout.strip()), 15)

    def test_network_scale_idle_stays_at_minimum_step(self):
        samples = [0, 1024, 2048]
        script = (
            f"const m=require({json.dumps(str(LOGIC))});"
            f"console.log(m.networkScaleMbit({json.dumps(samples)},50,600)+\" \"+m.networkScaleMbit({json.dumps(samples)},5,50));"
        )
        result = subprocess.run(["node", "-e", script], text=True, capture_output=True, check=True)
        self.assertEqual(result.stdout.strip(), "50 5")

    def test_adaptive_network_scale_keeps_background_traffic_visible(self):
        samples = [60] * 5
        script = (
            f"const m=require({json.dumps(str(LOGIC))});"
            f"console.log(JSON.stringify([m.adaptiveNetworkScale({json.dumps(samples)},'download'),m.adaptiveNetworkScale({json.dumps(samples)},'upload')]));"
        )
        result = subprocess.run(["node", "-e", script], text=True, capture_output=True, check=True)
        download, upload = json.loads(result.stdout)
        self.assertEqual(download, {"ceilingMbit": 0.0025, "stepMbit": 0.0025})
        self.assertEqual(upload, {"ceilingMbit": 0.0025, "stepMbit": 0.0025})

    def test_adaptive_network_scale_preserves_high_throughput_caps(self):
        samples = [700000000/8] * 3
        script = (
            f"const m=require({json.dumps(str(LOGIC))});"
            f"console.log(JSON.stringify([m.adaptiveNetworkScale({json.dumps(samples)},'download'),m.adaptiveNetworkScale({json.dumps(samples)},'upload')]));"
        )
        result = subprocess.run(["node", "-e", script], text=True, capture_output=True, check=True)
        download, upload = json.loads(result.stdout)
        self.assertEqual(download["ceilingMbit"], 600)
        self.assertEqual(upload["ceilingMbit"], 50)

    def test_network_detection_prefers_default_route_over_first_interface(self):
        command = command_for("netDetectSource")
        with tempfile.TemporaryDirectory() as tmp:
            bindir = Path(tmp)
            executable(bindir / "ip", "printf '%s\\n' 'default via 192.0.2.1 dev enp7s0 proto dhcp'\n")
            executable(bindir / "ls", "printf '%s\\n' eno1 enp7s0 lo\n")
            env = os.environ.copy()
            env["PATH"] = f"{bindir}:/usr/bin:/bin"
            result = subprocess.run(command, shell=True, text=True, capture_output=True, env=env, check=True)
            self.assertEqual(result.stdout.strip(), "enp7s0")

    def test_network_detection_fallback_preserves_wlo_names(self):
        command = command_for("netDetectSource")
        with tempfile.TemporaryDirectory() as tmp:
            bindir = Path(tmp)
            executable(bindir / "ip", "exit 0\n")
            executable(bindir / "ls", "printf '%s\\n' lo wlo1 tailscale0\n")
            env = os.environ.copy()
            env["PATH"] = f"{bindir}:/usr/bin:/bin"
            result = subprocess.run(command, shell=True, text=True, capture_output=True, env=env, check=True)
            self.assertEqual(result.stdout.strip(), "wlo1")

    def test_network_counters_read_requested_interface_from_proc_fixture(self):
        fixture = """Inter-| Receive | Transmit
 face |bytes packets errs drop fifo frame compressed multicast|bytes packets errs drop fifo colls carrier compressed
    lo: 10 1 0 0 0 0 0 0 10 1 0 0 0 0 0 0
 enp7s0: 123456 20 0 0 0 0 0 0 654321 30 0 0 0 0 0 0
"""
        with tempfile.TemporaryDirectory() as tmp:
            proc_net_dev = Path(tmp) / "net-dev"
            proc_net_dev.write_text(fixture)
            result = subprocess.run(
                ["sh", str(NETWORK_COUNTERS), "enp7s0", str(proc_net_dev)],
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertEqual(result.stdout.strip(), "123456 654321")

    def test_qml_polls_proc_network_counters_instead_of_dynamic_ksystemstats_ids(self):
        text = QML.read_text()
        self.assertIn("id: networkCountersSource", text)
        self.assertIn("network_counters.sh", text)
        self.assertNotIn('sensorId: "network/" + root.netIf', text)


    def test_readme_uses_installable_repository_root(self):
        text = README.read_text()
        self.assertIn("kpackagetool6 --type Plasma/Applet --install .", text)
        self.assertNotIn("cp -a com.skybox.verticalsysmonitor", text)

    def test_readme_uses_pytest_as_main_test_suite(self):
        text = README.read_text()
        self.assertIn("python3 -m pytest -q", text)
        self.assertIn("node --check contents/code/monitor_logic.js", text)
        self.assertIn("qmllint contents/ui/main.qml", text)
        self.assertNotIn("python3 tests/test_dashboard_source.py", text)
        self.assertNotIn("python3 tests/test_monitor_behavior.py", text)
        self.assertNotIn("python3 tests/test_hermes_think_time.py", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
