import Foundation
import Observation

@Observable
@MainActor
final class VoiceStateViewModel {
    var voiceUI = VoiceUI(state: "offline", label: "Connecting…", detail: "Starting…", color: "#6b7280", animate: false)
    var liveTranscript = ""
    var isPartialTranscript = false
    var lastFinalTranscript = ""
    var lastHeard = ""
    var lastAction = ""
    var wakeWord = "hey willy"
    var micDevice = "—"

    var ollamaOnline = false
    var ollamaModel = ""
    var workerRunning = false
    var helperOnline = false
    var cursorConfigured = false
    var fullAccess = false
    var voiceMuted = false

    var tasks: [TaskRow] = []
    var activeTasks: [TaskRow] = []
    var approvals: [ApprovalRow] = []
    var pendingGoal: GoalDetail?
    var chatMessages: [ChatMessage] = []
    var sessionId: String?
    var lastError: String?
    var statusLine = ""
    var toastMessage: String?
    var isSending = false

    var learningReport = ""
    var selfStatus = ""
    var eventStats = ""
    var recentEvents: [EventRow] = []
    var terminalOutput = ""

    var improveRun: ImproveRunStatus?

    var openclawWhatsApp = false

    var selectedSection: KioskSection = .chat

    private var fastPollTask: Task<Void, Never>?
    private var slowPollTask: Task<Void, Never>?
    private let client = JarvisAPIClient.shared

    func startPolling() {
        fastPollTask?.cancel()
        slowPollTask?.cancel()
        fastPollTask = Task {
            while !Task.isCancelled {
                await refreshFast()
                let interval: UInt64 = voiceUI.animate ? 350_000_000 : 900_000_000
                try? await Task.sleep(nanoseconds: interval)
            }
        }
        slowPollTask = Task {
            while !Task.isCancelled {
                await refreshSlow()
                try? await Task.sleep(nanoseconds: 3_000_000_000)
            }
        }
    }

    func stopPolling() {
        fastPollTask?.cancel()
        slowPollTask?.cancel()
        fastPollTask = nil
        slowPollTask = nil
    }

    func refreshFast() async {
        do {
            async let dashTask = client.fetchDashboard()
            async let helperTask = client.fetchHelperStatus()
            let dash = try await dashTask
            let helper = try await helperTask

            voiceUI = dash.voice_ui ?? VoiceUIMapper.map(helper: helper)
            ollamaOnline = dash.ollama?.ok == true
            ollamaModel = dash.ollama?.default_model ?? ""
            workerRunning = dash.worker?.running == true
            helperOnline = helper.ok == true
            fullAccess = dash.security?.full_access == true
            openclawWhatsApp = dash.openclaw?.whatsapp == true
            voiceMuted = helper.voice_muted == true
            activeTasks = dash.tasks_active ?? []

            liveTranscript = helper.live_transcript ?? ""
            isPartialTranscript = helper.live_transcript_partial == true
            lastFinalTranscript = helper.last_final_transcript ?? ""
            lastHeard = helper.last_heard ?? ""
            lastAction = helper.last_action ?? ""
            wakeWord = helper.wake_word ?? "hey willy"
            micDevice = helper.microphone?.device ?? "No mic"

            if let pending = dash.approvals_pending {
                approvals = pending
            }

            statusLine = buildStatusLine(dash: dash)
            lastError = nil
        } catch {
            lastError = error.localizedDescription
            voiceUI = VoiceUI(
                state: "offline",
                label: "Cannot connect",
                detail: "JarvisCore offline — System → Fix services",
                color: "#ef4444",
                animate: false
            )
            statusLine = "Offline"
        }
    }

    func refreshSlow() async {
        tasks = (try? await client.fetchTasks()) ?? tasks
        improveRun = try? await client.fetchImproveRunStatus()
        if let sid = sessionId {
            chatMessages = (try? await client.fetchSessionMessages(sid)) ?? chatMessages
        }
    }

    func reconnectOpenClaw() async {
        do {
            _ = try await client.reconnectOpenClaw()
            showToast("Reconnecting OpenClaw WhatsApp…")
            await refreshFast()
        } catch {
            showToast("OpenClaw reconnect failed")
        }
    }

    func startImproveRun(minutes: Int) async {
        do {
            let res = try await client.startImproveRun(minutes: minutes)
            if res.ok == true {
                showToast("Self-improvement run started — full control for \(minutes)m")
                fullAccess = true
            } else {
                showToast(res.error ?? "Failed to start")
            }
            improveRun = try? await client.fetchImproveRunStatus()
        } catch {
            lastError = error.localizedDescription
            showToast("Could not start run")
        }
    }

    func stopImproveRun() async {
        do {
            _ = try await client.stopImproveRun()
            showToast("Stopping self-improvement run…")
            await refreshSlow()
            await refreshFast()
        } catch {
            lastError = error.localizedDescription
        }
    }

    private func buildStatusLine(dash: DashboardResponse) -> String {
        var parts: [String] = [dash.agent ?? "William"]
        parts.append(ollamaOnline ? "Willy online" : "Willy offline")
        parts.append(helperOnline ? "Voice online" : "Voice offline")
        if workerRunning { parts.append("Worker active") }
        if let n = dash.approval_count, n > 0 { parts.append("\(n) approvals") }
        return parts.joined(separator: " · ")
    }

    func showToast(_ message: String) {
        toastMessage = message
        Task {
            try? await Task.sleep(nanoseconds: 2_500_000_000)
            if toastMessage == message { toastMessage = nil }
        }
    }

    func startNewConversation() async {
        sessionId = nil
        chatMessages = []
        try? await client.clearTranscript()
        showToast("New conversation")
        await refreshFast()
    }

    func submitPrompt(_ text: String) async -> String? {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }
        isSending = true
        defer { isSending = false }
        do {
            let res = try await client.sendChat(trimmed, sessionId: sessionId)
            if let sid = res.session_id { sessionId = sid }
            if res.is_new_session == true {
                chatMessages = []
                try? await client.clearTranscript()
            }
            if let sid = sessionId {
                chatMessages = try await client.fetchSessionMessages(sid)
            }
            await refreshFast()
            await refreshSlow()
            return res.reply ?? res.error
        } catch {
            lastError = error.localizedDescription
            showToast("Send failed")
            return nil
        }
    }

    func createGoal(from prompt: String) async -> GoalDetail? {
        isSending = true
        defer { isSending = false }
        do {
            let created = try await client.createGoal(prompt)
            pendingGoal = try await client.fetchGoal(created.id)
            showToast("Goal drafted — review plan")
            return pendingGoal
        } catch {
            lastError = error.localizedDescription
            showToast("Goal failed")
            return nil
        }
    }

    func approvePendingGoal() async {
        guard let id = pendingGoal?.id else { return }
        do {
            _ = try await client.approveGoal(id)
            pendingGoal = try await client.fetchGoal(id)
            showToast("Goal running")
            await refreshSlow()
        } catch {
            lastError = error.localizedDescription
        }
    }

    func resolveApproval(_ id: Int, approved: Bool) async {
        do {
            _ = try await client.resolveApproval(id, approved: approved)
            showToast(approved ? "Approved" : "Denied")
            await refreshFast()
        } catch {
            lastError = error.localizedDescription
        }
    }

    func sleepVoice() async {
        try? await client.voiceSleep()
        showToast("Voice sleeping")
        await refreshFast()
    }

    func wakeVoice() async {
        try? await client.voiceWake()
        showToast("Voice waking")
        await refreshFast()
    }

    func listenNow() async {
        try? await client.voiceListen()
        showToast("Listening…")
        await refreshFast()
    }

    func toggleMute() async {
        try? await client.setMuted(!voiceMuted)
        await refreshFast()
    }

    func toggleFullAccess() async {
        let enabling = !fullAccess
        try? await client.setFullAccess(enabling)
        showToast(enabling ? "Full access on" : "Restricted mode")
        await refreshFast()
    }

    func runTerminal(_ command: String) async {
        do {
            let res = try await client.runTerminal(command)
            terminalOutput = [res.stdout, res.stderr, res.error].compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: "\n")
        } catch {
            terminalOutput = error.localizedDescription
        }
    }

    func fixServices() async {
        showToast("Restarting services…")
        await runTerminal("launchctl kickstart -k gui/$(id -u)/com.willy.jarvis-core; launchctl kickstart -k gui/$(id -u)/com.willy.jarvis-helper; launchctl kickstart -k gui/$(id -u)/com.willy.william-kiosk")
        try? await Task.sleep(nanoseconds: 3_000_000_000)
        await refreshFast()
        await refreshSlow()
        showToast(helperOnline ? "Services online" : "Still offline")
    }

    func loadLearning() async {
        if let r = try? await client.fetchLearningReport() {
            learningReport = r.report ?? "—"
        }
    }

    func loadSelfStatus() async {
        if let s = try? await client.fetchSelfStatus() {
            selfStatus = "Branch: \(s.branch ?? "?")\(s.on_sandbox == true ? " (sandbox)" : "")"
        }
    }

    func loadEvents() async {
        if let e = try? await client.fetchEvents() {
            recentEvents = e.events ?? []
            if let stats = e.stats {
                eventStats = "\(stats.today ?? 0) today · \(stats.total ?? 0) total"
            }
        }
    }

    func analyzeDesktop() async {
        showToast("Analyzing screen…")
        if let r = try? await client.analyzeDesktop() {
            terminalOutput = r.reply ?? r.error ?? "Done"
        }
    }

    func triggerBriefing() async {
        try? await client.triggerBriefing()
        showToast("Briefing triggered")
    }

    func testNotify() async {
        try? await client.testNotify()
        showToast("Notification sent")
    }

    func proposeSelfChange(_ text: String) async {
        if let r = try? await client.proposeSelfChange(text) {
            terminalOutput = r.reply ?? r.error ?? "Proposed"
            showToast("Change proposed")
        }
    }
}
