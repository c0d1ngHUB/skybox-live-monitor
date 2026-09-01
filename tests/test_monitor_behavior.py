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

    def test_freshness_reports_each_stale_domain(self):
        updates = {
            "cpuUsage": 19_000, "cpuTemperature": 19_000,
            "gpu0Telemetry": 0, "gpu1Telemetry": 0,
            "memoryPercent": 18_000, "memoryUsed": 18_000, "memoryTotal": 18_000,
            "network": 10_000,
            "diskPercent": 19_500, "diskUsed": 19_500, "diskTotal": 19_500,
            "uptime": 19_000, "loadAverage": 19_000,
        }
        script = (
            f"const m=require({json.dumps(str(LOGIC))});"
            "console.log(JSON.stringify(m.staleDomains(20000,{cpuUsage:19000,cpuTemperature:19000,gpu0Telemetry:19000,gpu1Telemetry:0,memoryPercent:18000,memoryUsed:18000,memoryTotal:18000,network:10000,diskPercent:19500,diskUsed:19500,diskTotal:19500,uptime:19000,loadAverage:19000},6000)));"
        )
        result = subprocess.run(["node", "-e", script], text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(result.stdout), ["GPU 1", "NETWORK"])

    def test_cpu_process_rates_use_per_pid_cpu_time_deltas(self):
        script = (
            f"const m=require({json.dumps(str(LOGIC))});"
            "const previous={'10':{cpuSeconds:4},'20':{cpuSeconds:8}};"
            "const current=[{pid:10,cpuSeconds:6,name:'fast'},{pid:20,cpuSeconds:8.5,name:'slow'},{pid:30,cpuSeconds:9,name:'new'}];"
            "console.log(JSON.stringify(m.cpuProcessRates(previous,current,5000,2)));"
        )
        result = subprocess.run(["node", "-e", script], text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(result.stdout), [
            {"pid": 10, "name": "fast", "cpu": "40.0"},
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
        self.assertIn('root.markMetricFresh("network")', text)
        self.assertNotIn('sensorId: "network/" + root.netIf', text)


    def test_readme_uses_installable_repository_root(self):
        text = README.read_text()
        self.assertIn("kpackagetool6 --type Plasma/Applet --install .", text)
        self.assertNotIn("cp -a com.skybox.verticalsysmonitor", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
