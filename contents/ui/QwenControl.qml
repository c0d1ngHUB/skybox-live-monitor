import QtQuick
import QtQuick.Controls as QQC2
import org.kde.plasma.plasma5support as PlasmaSupport

// Independent, read-only status polling; actions use the existing GPU handoff.
Item {
    id: control
    implicitWidth: 228
    implicitHeight: 32
    width: implicitWidth
    height: implicitHeight

    property color ink: "#D8EEF3"
    property color muted: "#8E9DA3"
    property color accent: "#33E6D3"
    property color warning: "#FFBF69"
    property color surface: "#082A3A"
    property string qwenState: "unknown"
    property string detail: "Qwen-Dienstzustand wird geprüft."
    property bool actionBusy: false
    property string errorMessage: ""
    property string activeOperation: ""
    property string activeCommand: ""
    property string stdoutBuffer: ""
    property string stderrBuffer: ""
    property int requestId: 0
    property double lastStatusMs: 0
    readonly property string helperPath: decodeURIComponent(Qt.resolvedUrl("../code/qwen_control.py").toString().replace(/^file:\/\//, ""))
    readonly property bool modelOn: qwenState === "running" || qwenState === "sleeping"
    readonly property bool canToggle: !actionBusy && (modelOn || qwenState === "stopped" || qwenState === "failed")
    readonly property string toolTipText: detail
        + (errorMessage.length ? "\n\nFehler: " + errorMessage : "")
        + "\n\nStarten reserviert die Blackwell für Qwen. Stoppen beendet laufende Qwen-Anfragen und gibt die GPU an Hindsight zurück."

    function beginCommand(operation) {
        if (activeCommand.length) executable.disconnectSource(activeCommand)
        requestId++
        activeOperation = operation
        stdoutBuffer = ""
        stderrBuffer = ""
        // Quote the resolved path; neither action nor request ID is user input.
        var quotedPath = "'" + helperPath.replace(/'/g, "'\\''") + "'"
        activeCommand = "python3 " + quotedPath + " " + operation + " --request-id " + requestId
        executable.connectSource(activeCommand)
    }

    function pollStatus() {
        if (actionBusy) return
        if (lastStatusMs > 0 && Date.now() - lastStatusMs > 15000) {
            qwenState = "unknown"
            detail = "Qwen-Dienstzustand ist veraltet; erneute Prüfung läuft."
        }
        if (!activeCommand.length) beginCommand("status")
    }

    function toggleQwen() {
        if (!canToggle) return
        var operation = modelOn ? "stop" : "start"
        actionBusy = true
        errorMessage = ""
        beginCommand(operation)
    }

    function handleData(source, data) {
        // Check identity BEFORE touching buffers, including A→B→A sequences.
        if (!activeCommand.length || source !== activeCommand) return
        stdoutBuffer += data["stdout"] || ""
        stderrBuffer += data["stderr"] || ""
        if (data["exit code"] === undefined) return
        var completedOperation = activeOperation
        var payload = null
        try { payload = JSON.parse(stdoutBuffer.trim()) } catch (error) { payload = null }
        var knownStates = ["running", "sleeping", "stopped", "starting", "stopping", "failed", "unavailable", "unknown"]
        var valid = payload !== null && knownStates.indexOf(payload.state) >= 0
        qwenState = valid ? payload.state : "unknown"
        detail = valid ? String(payload.detail || "") : "Qwen-Dienstzustand nicht lesbar."
        lastStatusMs = valid ? Date.now() : 0
        if (completedOperation !== "status") {
            if (Number(data["exit code"]) !== 0 || !valid || payload.ok !== true) {
                errorMessage = (payload && payload.error) ? String(payload.error)
                    : (stderrBuffer.trim().slice(-1600) || "Qwen-Umschaltung fehlgeschlagen.")
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
        objectName: "qwenToggleButton"
        anchors.fill: parent
        enabled: control.canToggle
        hoverEnabled: true
        focusPolicy: Qt.StrongFocus
        Accessible.name: text
        Accessible.description: control.toolTipText
        text: {
            if (control.actionBusy || control.qwenState === "starting" || control.qwenState === "stopping")
                return "Qwen 3.8 " + ((control.activeOperation === "stop" || control.qwenState === "stopping") ? "stoppt …" : "startet …")
            if (control.qwenState === "unavailable") return "Qwen 3.8 fehlt"
            if (control.qwenState === "unknown") return "Qwen 3.8 · unbekannt"
            return (control.errorMessage.length ? "Fehler · " : "") + "Qwen 3.8 "
                + (control.modelOn ? "stoppen" : "starten")
        }
        onClicked: control.toggleQwen()
        background: Rectangle {
            radius: 5
            color: toggleButton.down ? Qt.lighter(control.surface, 1.4)
                : toggleButton.hovered ? Qt.lighter(control.surface, 1.2) : control.surface
            border.width: toggleButton.activeFocus ? 2 : 1
            border.color: control.errorMessage.length || control.qwenState === "failed" ? control.warning
                : control.modelOn || toggleButton.activeFocus ? control.accent : control.muted
            opacity: toggleButton.enabled || control.actionBusy ? 1 : 0.65
        }
        contentItem: Text {
            text: toggleButton.text
            font.family: "Inter"
            font.pixelSize: 13
            font.weight: Font.DemiBold
            color: control.errorMessage.length || control.qwenState === "failed" ? control.warning
                : control.modelOn ? control.accent : control.ink
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
        interval: 3000
        running: true
        repeat: true
        onTriggered: control.pollStatus()
    }
    Component.onCompleted: pollStatus()
}
