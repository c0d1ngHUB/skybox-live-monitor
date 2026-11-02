import QtQuick
import QtQuick.Controls as QQC2
import org.kde.plasma.plasma5support as PlasmaSupport

// MOSS-TTS (the /voice speech output) on/off. The scheduler service loads MOSS onto the
// Blackwell for one voice reply at a time, so "on" costs no idle VRAM; "off" stops the
// service and /voice replies fall back to text. All state logic lives in moss_control.py.
Item {
    id: control
    implicitWidth: 180
    implicitHeight: 32
    width: implicitWidth
    height: implicitHeight

    property color ink: "#D8EEF3"
    property color muted: "#8E9DA3"
    property color accent: "#33E6D3"
    property color warning: "#FFBF69"
    property color surface: "#082A3A"
    property string mode: "unknown"
    property bool ready: false
    property bool busy: false
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
    readonly property string helperPath: decodeURIComponent(Qt.resolvedUrl("../code/moss_control.py").toString().replace(/^file:\/\//, ""))
    readonly property bool running: mode === "on"
    readonly property bool canToggle: !actionBusy && (mode === "on" || mode === "off")
    readonly property string toolTipText: (mode === "unknown" ? "MOSS-Zustand unbekannt." : note)
        + (blocker.length ? "\n" + blocker + "." : "")
        + (errorMessage.length ? "\n\nFehler: " + errorMessage : "")
        + "\n\nMOSS ist die Sprachausgabe für /voice. Beenden stoppt den Scheduler samt MOSS (wartet auf eine laufende Sprachantwort); Sprachantworten kommen dann nur als Text."
        + "\nStarten bringt MOSS zurück. Pro Sprachantwort lädt MOSS kurz auf die Blackwell (ca. 17 GB) und gibt sie danach wieder frei."

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
            note = "MOSS-Zustand ist veraltet; erneute Prüfung läuft."
        }
        if (!activeCommand.length) beginCommand("status")
    }

    function toggleMoss() {
        if (!canToggle) return
        actionBusy = true
        errorMessage = ""
        beginCommand(running ? "stop" : "start")
    }

    function handleData(source, data) {
        if (!activeCommand.length || source !== activeCommand) return
        stdoutBuffer += data["stdout"] || ""
        stderrBuffer += data["stderr"] || ""
        if (data["exit code"] === undefined) return
        var completedOperation = activeOperation
        var payload = null
        try { payload = JSON.parse(stdoutBuffer.trim()) } catch (error) { payload = null }
        var valid = payload !== null && (payload.mode === "on" || payload.mode === "off")
        mode = valid ? payload.mode : "unknown"
        ready = valid && payload.ready === true
        busy = valid && payload.busy === true
        blocker = valid ? String(payload.blocker || "") : ""
        note = valid ? String(payload.note || "") : ""
        serviceState = valid ? String(payload.service || "") : "unknown"
        lastStatusMs = valid ? Date.now() : 0
        if (completedOperation !== "status") {
            if (Number(data["exit code"]) !== 0 || !valid || payload.ok !== true) {
                errorMessage = (payload && payload.error) ? String(payload.error)
                    : (stderrBuffer.trim().slice(-600) || "MOSS-Umschaltung fehlgeschlagen.")
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
        objectName: "mossToggleButton"
        anchors.fill: parent
        enabled: control.canToggle
        hoverEnabled: true
        focusPolicy: Qt.StrongFocus
        Accessible.name: text
        Accessible.description: control.toolTipText
        text: {
            if (control.actionBusy)
                return "MOSS " + (control.activeOperation === "stop" ? "beendet …" : "startet …")
            if (control.mode === "unknown") return "MOSS · unbekannt"
            var prefix = control.errorMessage.length ? "Fehler · " : ""
            return prefix + (control.running ? "MOSS beenden" : "MOSS starten")
        }
        onClicked: control.toggleMoss()
        background: Rectangle {
            radius: 5
            color: toggleButton.down ? Qt.lighter(control.surface, 1.4)
                : toggleButton.hovered ? Qt.lighter(control.surface, 1.2) : control.surface
            border.width: toggleButton.activeFocus ? 2 : 1
            border.color: control.errorMessage.length ? control.warning
                : control.running || toggleButton.activeFocus ? control.accent : control.muted
            opacity: toggleButton.enabled || control.actionBusy ? 1 : 0.65
        }
        contentItem: Text {
            text: toggleButton.text
            font.family: "Inter"
            font.pixelSize: 13
            font.weight: Font.DemiBold
            color: control.errorMessage.length ? control.warning
                : control.running ? control.accent : control.ink
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
