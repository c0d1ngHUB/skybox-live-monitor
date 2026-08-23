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
        self.assertEqual(text.count("var firstX = MonitorLogic.historyX"), 3)
        self.assertEqual(text.count("var lastX = MonitorLogic.historyX"), 3)
        self.assertEqual(text.count("ctx.lineTo(firstX, height)"), 3)
        self.assertEqual(text.count("ctx.lineTo(lastX, height)"), 3)

    def test_freshness_reports_each_stale_domain(self):
        script = (
            f"const m=require({json.dumps(str(LOGIC))});"
            "console.log(JSON.stringify(m.staleDomains(20000,{cpu:19000,gpu:0,memory:18000,network:10000,disk:19500,system:19000},6000)));"
        )
        result = subprocess.run(["node", "-e", script], text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(result.stdout), ["GPU", "NETWORK"])

    def test_nvidia_memory_parser_selects_first_gpu_from_multi_gpu_output(self):
        output = "0, 100, 1000\n1, 200, 2000"
        script = (
            f"const m=require({json.dumps(str(LOGIC))});"
            f"console.log(JSON.stringify(m.parseNvidiaMemory({json.dumps(output)},0)));"
        )
        result = subprocess.run(["node", "-e", script], text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(result.stdout), {"usedMiB": 100, "totalMiB": 1000})

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
        self.assertIn('root.markDataFresh("network")', text)
        self.assertNotIn('sensorId: "network/" + root.netIf', text)

    def test_ollama_stop_is_forced_to_the_same_local_endpoint_as_discovery(self):
        command = command_for("releaseModelsSource")
        with tempfile.TemporaryDirectory() as tmp:
            bindir = Path(tmp)
            log = bindir / "ollama.log"
            executable(bindir / "curl", "printf '%s' '{\"models\":[{\"name\":\"test-model\"}]}'\n")
            executable(
                bindir / "jq",
                "case \"$*\" in *arrays*) exit 0;; *) printf '%s\\n' test-model;; esac\n",
            )
            executable(bindir / "ollama", f"printf '%s' \"$OLLAMA_HOST\" > {log}\nexit 0\n")
            env = os.environ.copy()
            env["PATH"] = f"{bindir}:/usr/bin:/bin"
            env["OLLAMA_HOST"] = "http://remote.invalid:11434"
            subprocess.run(command, shell=True, text=True, capture_output=True, env=env, check=True)
            self.assertEqual(log.read_text(), "http://127.0.0.1:11434")

    def test_readme_uses_installable_repository_root(self):
        text = README.read_text()
        self.assertIn("kpackagetool6 --type Plasma/Applet --install .", text)
        self.assertNotIn("cp -a com.skybox.verticalsysmonitor", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
