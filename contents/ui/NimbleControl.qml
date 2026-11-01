import QtQuick
import QtQuick.Controls as QQC2
import org.kde.plasma.plasma5support as PlasmaSupport

// Nimble v3 residency: "auto" keeps the model unloaded (loaded briefly on
// demand), "hold" pins it on the Blackwell. The policy lives in nimble-ctl.
Item {
    id: control
    implicitWidth: 200
    implicitHeight: 32
    width: implicitWidth
    height: implicitHeight

    property color ink: "#D8EEF3"
    property color muted: "#8E9DA3"
    property color accent: "#33E6D3"
    property color warning: "#FFBF69"
    property color surface: "#082A3A"
    property string mode: "unknown"
    property var loaded: null
    property int vramMib: 0
    property string blocker: ""
    property string note: ""
    property string serviceState: "unknown"
    property bool actionBusy: false
    property string errorMessage: ""
    property string activeOperation: ""
    property string activeCommand: ""
    property string stdoutBuffer: ""
    property string stderrBuffer: ""
    property int requestId: 0
    property double lastStatusMs: 0
    readonly property string helperPath: decodeURIComponent(Qt.resolvedUrl("../code/nimble_control.py").toString().replace(/^file:\/\//, ""))
    readonly property bool holding: mode === "hold"
    readonly property bool canToggle: !actionBusy && (mode === "auto" || mode === "hold")
    readonly property string toolTipText: stateLine()
        + (errorMessage.length ? "\n\nFehler: " + errorMessage : "")
        + "\n\nAuto (Standard): Nimble bleibt entladen; der Fakt-Scorer lädt es nur für seinen Lauf (~8 s) und gibt den Grafikspeicher danach wieder frei."
        + "\nLaden: Nimble bleibt dauerhaft auf der Blackwell (~15 GB), bis du es wieder entlädst. Qwen und MOSS haben weiterhin Vorrang."

    function stateLine() {
        if (mode === "unknown") return "Nimble-Zustand unbekannt."
        var residency = loaded === true ? "geladen (" + (vramMib / 1024).toFixed(1) + " GB)"
            : loaded === false ? "entladen" : "Server nicht erreichbar"
        var line = "Modus: " + (holding ? "dauerhaft geladen" : "Auto") + " · aktuell " + residency
        if (blocker.length) line += "\n" + blocker + "."
        if (note.length) line += "\n" + note
        return line
    }

    function beginCommand(operation) {
        if (activeCommand.length) executable.disconnectSource(activeCommand)
        requestId++
        activeOperation = operation
        stdoutBuffer = ""
        stderrBuffer = ""
        // Bounded source names (requestId % 4): Plasma5Support keeps one
        // property per source name ever connected (see QwenControl.qml).
        var quotedPath = "'" + helperPath.replace(/'/g, "'\\''") + "'"
        activeCommand = "python3 " + quotedPath + " " + operation + " --request-id " + (requestId % 4)
        executable.connectSource(activeCommand)
    }

    function pollStatus() {
        if (actionBusy) return
        if (lastStatusMs > 0 && Date.now() - lastStatusMs > 20000) {
            mode = "unknown"
            note = "Nimble-Zustand ist veraltet; erneute Prüfung läuft."
        }
        if (!activeCommand.length) beginCommand("status")
    }

    function toggleNimble() {
        if (!canToggle) return
        actionBusy = true
        errorMessage = ""
        beginCommand(holding ? "auto" : "hold")
    }

    function handleData(source, data) {
        if (!activeCommand.length || source !== activeCommand) return
        stdoutBuffer += data["stdout"] || ""
        stderrBuffer += data["stderr"] || ""
        if (data["exit code"] === undefined) return
        var completedOperation = activeOperation
        var payload = null
        try { payload = JSON.parse(stdoutBuffer.trim()) } catch (error) { payload = null }
        var valid = payload !== null && (payload.mode === "auto" || payload.mode === "hold")
        mode = valid ? payload.mode : "unknown"
        loaded = valid ? payload.loaded : null
        vramMib = valid && payload.vram_mib ? Number(payload.vram_mib) : 0
        blocker = valid ? String(payload.blocker || "") : ""
        note = valid ? String(payload.note || "") : ""
        serviceState = valid ? String(payload.service || "") : "unknown"
        lastStatusMs = valid ? Date.now() : 0
        if (completedOperation !== "status") {
            if (Number(data["exit code"]) !== 0 || !valid || payload.ok !== true) {
                errorMessage = (payload && payload.error) ? String(payload.error)
                    : (stderrBuffer.trim().slice(-600) || "Nimble-Umschaltung fehlgeschlagen.")
            }
            actionBusy = false
        }
        executable.disconnectSource(source)
        activeCommand = ""
        activeOperation = ""
        stdoutBuffer = ""
        stderrBuffer = ""
    }

    QQC2.Button {
        id: toggleButton
        objectName: "nimbleToggleButton"
        anchors.fill: parent
        enabled: control.canToggle
        hoverEnabled: true
        focusPolicy: Qt.StrongFocus
        Accessible.name: text
        Accessible.description: control.toolTipText
        text: {
            if (control.actionBusy)
                return "Nimble v3 " + (control.activeOperation === "auto" ? "entlädt …" : "lädt …")
            if (control.mode === "unknown") return "Nimble v3 · unbekannt"
            var prefix = control.errorMessage.length ? "Fehler · " : ""
            if (control.holding)
                return prefix + (control.loaded === true ? "Nimble v3 entladen" : "Nimble v3 · wartet")
            return prefix + "Nimble v3 laden"
        }
        onClicked: control.toggleNimble()
        background: Rectangle {
            radius: 5
            color: toggleButton.down ? Qt.lighter(control.surface, 1.4)
                : toggleButton.hovered ? Qt.lighter(control.surface, 1.2) : control.surface
            border.width: toggleButton.activeFocus ? 2 : 1
            border.color: control.errorMessage.length ? control.warning
                : control.holding || toggleButton.activeFocus ? control.accent : control.muted
            opacity: toggleButton.enabled || control.actionBusy ? 1 : 0.65
        }
        contentItem: Text {
            text: toggleButton.text
            font.family: "Inter"
            font.pixelSize: 13
            font.weight: Font.DemiBold
            color: control.errorMessage.length ? control.warning
                : control.holding ? control.accent : control.ink
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
        QQC2.ToolTip.visible: hovered
        QQC2.ToolTip.delay: 600
        QQC2.ToolTip.text: control.toolTipText
    }

    PlasmaSupport.DataSource {
        id: executable
        engine: "executable"
        connectedSources: []
        onNewData: function(source, data) { control.handleData(source, data) }
    }
    Timer {
        interval: 5000
        running: true
        repeat: true
        onTriggered: control.pollStatus()
    }
    Component.onCompleted: pollStatus()
}
