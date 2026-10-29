"""GPU cards expose model identity and used VRAM without hover."""
from pathlib import Path
import json
import subprocess

QML = Path(__file__).resolve().parents[1] / "contents/ui/main.qml"


def test_used_vram_label_uses_decimal_gb_and_handles_missing_telemetry():
    source = QML.read_text()
    start = source.index("    function gpuVramUsedText(")
    helper = source[start:source.index("    function vramPercent(", start)]
    probe = helper + '''
console.log(JSON.stringify([
    gpuVramUsedText(3836, true), gpuVramUsedText(939, true),
    gpuVramUsedText(43, true), gpuVramUsedText(0, true),
    gpuVramUsedText(8192, true), gpuVramUsedText(3836, false),
    gpuVramUsedText(-1, true), gpuVramUsedText(NaN, true)
]));
'''
    result = subprocess.run(["node", "-e", probe], capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == [
        "VRAM 4.0GB", "VRAM 1.0GB", "VRAM 0.05GB", "VRAM 0.0GB",
        "VRAM 8.6GB", "VRAM --", "VRAM --", "VRAM --"
    ]
    assert "text: root.gpuVramUsedText(cardData.vramUsedMiB, cardData.available)" in source
    # The percentage still drives bar fill; exact readings stay in the tooltip.
    assert "Math.max(0, Math.min(1, (cardData.vramFill || 0) / 100))" in source
    assert ' + " MiB / " + ' in source
    assert ' + "% · 1 GB = 10⁹ Bytes"' in source


def test_model_name_is_visible_in_every_gpu_card_process_column():
    source = QML.read_text()
    cards = source.split("// --- Two-GPU row", 1)[1].split("x: index", 1)[0]
    for index in range(2):
        row = next(line for line in cards.splitlines() if '{kind:"gpu", label:"GPU ' + str(index) in line)
        assert f"gpuName:root.gpu{index}Name" in row
    assert 'metricKind === "gpu" ? cardData.gpuName : "TOP · RAM"' in source
    assert 'text: processDetails.heading + processDetails.headingSuffix' in source
    assert 'cardData.labelTooltip + " · TOP · VRAM' in source
