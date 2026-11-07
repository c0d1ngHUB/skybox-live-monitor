import QtQuick
import QtQuick.Controls as QQC2
import org.kde.plasma.plasma5support as PlasmaSupport

// MOSS-TTS (the /voice speech output) on/off. While the scheduler service runs it keeps MOSS
// loaded on the Blackwell (resident mode, ~16.6 GB); during a Qwen session or under memory
// pressure it unloads MOSS and loads it again afterwards. "Off" stops the service, frees the
// memory completely and /voice replies fall back to text. All state logic lives in
// moss_control.py.
Item {
    id: control
    implicitWidth: 180
    implicitHeight: 32
    width: implicitWidth
    height: implicitHeight

    // Defaults mirror main.qml's palette; main.qml binds them explicitly.
    property color ink: "#E8F7FF"
    property color muted: "#A0C8D8"
    property color accent: "#96F5F6"
    property color warning: "#FFD166"
    property color surface: "#082A3A"
    property string mode: "unknown"
    property bool ready: false
    property bool busy: false
    property string blocker: ""
    property string note: ""
    property string serviceState: "unknown"
    // Resident mode (scheduler since 2026-11-07): is MOSS actually in the Blackwell's memory?
    property bool resident: false
    property bool loaded: false
    property string mossState: ""
    property string mossReason: ""
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
    // Running, but MOSS is not in memory (loading, waiting for Qwen/memory, load error).
    readonly property bool notLoaded: running && resident && !loaded
    readonly property bool loadError: notLoaded && mossState === "error"
    readonly property bool canToggle: !actionBusy && (mode === "on" || mode === "off")
    // Same state vocabulary as the service rows and the Qwen button.
    readonly property string stateSymbol: actionBusy ? "◐"
        : mode === "unknown" ? "?"
        : errorMessage.length || loadError ? "▲"
        : notLoaded ? "◐"
        : running ? "●" : "○"
    readonly property string toolTipText: (mode === "unknown" ? "MOSS-Zustand unbekannt." : note)
        + (blocker.length ? "\n" + blocker + "." : "")
        + (errorMessage.length ? "\n\nFehler: " + errorMessage : "")
        + "\n\nMOSS ist die Sprachausgabe für /voice. Solange MOSS läuft, bleibt es auf der Blackwell geladen (ca. 16,4 GiB) und antwortet ohne Ladezeit; während einer Qwen-Sitzung wird es entladen und danach automatisch wieder geladen."
        + "\nBeenden stoppt den Dienst samt MOSS und gibt den Grafikspeicher vollständig frei (wartet auf eine laufende Sprachantwort); Sprachantworten kommen dann nur als Text. Starten lädt MOSS wieder."

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
        resident = valid && payload.resident === true
        loaded = valid && payload.loaded === true
        mossState = valid ? String(payload.moss_state || "") : ""
        mossReason = valid ? String(payload.moss_reason || "") : ""
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
            var word
            if (control.actionBusy) word = control.activeOperation === "stop" ? "stoppt …" : "startet …"
            else if (control.mode === "unknown") word = "unbekannt"
            else if (control.errorMessage.length) word = "Fehler · " + (control.running ? "stoppen" : "starten")
            // Running but not in memory: say so instead of "stoppen" (a click still stops MOSS).
            else if (control.notLoaded && control.mossState === "loading") word = "lädt …"
            else if (control.loadError) word = "Fehler · stoppen"
            else if (control.notLoaded) word = control.mossReason === "qwen" ? "wartet (Qwen)" : "wartet"
            else word = control.running ? "stoppen" : "starten"
            return control.stateSymbol + " MOSS " + word.toUpperCase()
        }
        onClicked: control.toggleMoss()
        background: Rectangle {
            radius: 9
            color: toggleButton.down ? Qt.lighter(control.surface, 1.4)
                : toggleButton.hovered ? Qt.lighter(control.surface, 1.2) : control.surface
            border.width: toggleButton.activeFocus ? 2 : 1
            border.color: control.errorMessage.length || control.loadError ? control.warning
                : (control.running && !control.notLoaded) || toggleButton.activeFocus ? control.accent : control.muted
            opacity: toggleButton.enabled || control.actionBusy ? 1 : 0.65
        }
        contentItem: Text {
            text: toggleButton.text
            font.family: "DejaVu Sans Mono"
            font.pixelSize: 12
            font.bold: true
            color: control.errorMessage.length || control.loadError ? control.warning
                : control.running && !control.notLoaded ? control.accent : control.ink
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
