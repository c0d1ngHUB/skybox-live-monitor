"""GPU cards expose model identity and used VRAM without hover."""
from pathlib import Path
import json
import subprocess

QML = Path(__file__).resolve().parents[1] / "contents/ui/main.qml"


def test_used_vram_label_shows_used_and_total_gib_and_handles_missing_telemetry():
    source = QML.read_text()
    start = source.index("    function gpuVramUsedText(")
    helper = source[start:source.index("    function vramPercent(", start)]
    probe = helper + '''
console.log(JSON.stringify([
    gpuVramUsedText(3836, 24467, true), gpuVramUsedText(939, 8192, true),
    gpuVramUsedText(43, 8192, true), gpuVramUsedText(0, 8192, true),
    gpuVramUsedText(8192, 8192, true), gpuVramUsedText(3836, 24467, false),
    gpuVramUsedText(-1, 8192, true), gpuVramUsedText(NaN, 8192, true),
    gpuVramUsedText(3836, 0, true), gpuVramUsedText(3836, NaN, true)
]));
'''
    result = subprocess.run(["node", "-e", probe], capture_output=True, text=True, check=True)
    # Binary GiB like the RAM pair; the total is whole GiB so the two GPUs
    # (24467 MiB and 8192 MiB) read as 24 and 8. No total → used only.
    assert json.loads(result.stdout) == [
        "VRAM 3.7 / 24 GiB", "VRAM 0.9 / 8 GiB", "VRAM 0.04 / 8 GiB", "VRAM 0.0 / 8 GiB",
        "VRAM 8.0 / 8 GiB", "VRAM --", "VRAM --", "VRAM --",
        "VRAM 3.7 GiB", "VRAM 3.7 GiB"
    ]
    assert "text: root.gpuVramUsedText(cardData.vramUsedMiB, cardData.vramTotalMiB, cardData.available)" in source
    # The percentage still drives bar fill; exact readings stay in the tooltip.
    assert "Math.max(0, Math.min(1, (cardData.vramFill || 0) / 100))" in source
    assert ' + " MiB / " + ' in source
    assert ' + "% belegt"' in source and "10⁹" not in source


def test_model_name_is_visible_in_every_gpu_card_process_column():
    source = QML.read_text()
    cards = source.split("// --- Two-GPU row", 1)[1].split("x: index", 1)[0]
    for index in range(2):
        row = next(line for line in cards.splitlines() if '{kind:"gpu", label:"GPU ' + str(index) in line)
        assert f"gpuName:root.gpu{index}Name" in row
    assert 'metricKind === "gpu" ? cardData.gpuName : "TOP · RAM"' in source
    assert 'text: processDetails.heading + processDetails.headingSuffix' in source
    assert 'cardData.labelTooltip + " · TOP · VRAM' in source
