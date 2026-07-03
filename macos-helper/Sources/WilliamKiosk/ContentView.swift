import SwiftUI

struct ContentView: View {
    @Bindable var model: VoiceStateViewModel
    @State private var showGoalSheet = false

    var body: some View {
        ZStack(alignment: .top) {
            NavigationSplitView {
                sidebar
            } detail: {
                detailContent
            }
            .navigationSplitViewStyle(.balanced)

            if let toast = model.toastMessage {
                ToastView(message: toast)
                    .padding(.top, 12)
                    .transition(.move(edge: .top).combined(with: .opacity))
                    .animation(.easeOut(duration: 0.2), value: toast)
            }
        }
        .background(KioskTheme.background)
        .sheet(isPresented: $showGoalSheet) {
            if let goal = model.pendingGoal {
                GoalApprovalSheet(
                    goal: goal,
                    onApprove: {
                        Task {
                            await model.approvePendingGoal()
                            showGoalSheet = false
                        }
                    },
                    onDismiss: { showGoalSheet = false }
                )
            }
        }
        .onAppear {
            Task {
                await model.loadLearning()
                await model.loadSelfStatus()
                await model.loadEvents()
            }
        }
    }

    private var sidebar: some View {
        VStack(alignment: .leading, spacing: 0) {
            VStack(alignment: .leading, spacing: 6) {
                Text("William")
                    .font(.system(size: 26, weight: .bold, design: .rounded))
                Text("Agent Control")
                    .font(.caption.weight(.medium))
                    .foregroundStyle(KioskTheme.accent)
                Text(model.statusLine)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .lineLimit(3)
            }
            .padding(20)

            List(KioskSection.allCases, selection: $model.selectedSection) { section in
                HStack {
                    Label(section.rawValue, systemImage: section.icon)
                    Spacer()
                    if section == .approvals, !model.approvals.isEmpty {
                        Text("\(model.approvals.count)")
                            .font(.caption2.bold())
                            .padding(.horizontal, 7)
                            .padding(.vertical, 2)
                            .background(KioskTheme.warn.opacity(0.25))
                            .clipShape(Capsule())
                    }
                    if section == .tasks, !model.activeTasks.isEmpty {
                        Text("\(model.activeTasks.count)")
                            .font(.caption2.bold())
                            .padding(.horizontal, 7)
                            .padding(.vertical, 2)
                            .background(KioskTheme.accent.opacity(0.35))
                            .clipShape(Capsule())
                    }
                }
                .tag(section)
            }
            .listStyle(.sidebar)

            VStack(alignment: .leading, spacing: 10) {
                HStack(spacing: 8) {
                    StatusPill(label: "Voice", on: model.helperOnline)
                    StatusPill(label: "Willy", on: model.ollamaOnline)
                    StatusPill(label: "Worker", on: model.workerRunning)
                }
                if let err = model.lastError {
                    Text(err)
                        .font(.caption2)
                        .foregroundStyle(KioskTheme.danger)
                        .lineLimit(2)
                }
            }
            .padding(16)
        }
        .frame(minWidth: 240)
        .background(KioskTheme.surface)
    }

    @ViewBuilder
    private var detailContent: some View {
        switch model.selectedSection {
        case .home:
            HomeTabView(model: model, showGoalSheet: $showGoalSheet)
        case .chat:
            ChatTabView(model: model)
        case .tasks:
            TasksTabView(model: model, showGoalSheet: $showGoalSheet)
        case .approvals:
            ApprovalsTabView(model: model)
        case .system:
            SystemTabView(model: model)
        }
    }
}

struct HomeTabView: View {
    @Bindable var model: VoiceStateViewModel
    @Binding var showGoalSheet: Bool
    @State private var prompt = ""
    @State private var reply = ""

    var body: some View {
        ScrollView {
            VStack(spacing: 24) {
                VoiceHeroView(voiceUI: model.voiceUI)

                HStack(spacing: 10) {
                    Button { Task { await model.listenNow() } } label: {
                        Label("Listen", systemImage: "mic.fill")
                    }
                    .buttonStyle(PrimaryButtonStyle(tint: KioskTheme.success))

                    Button { Task { await model.wakeVoice() } } label: {
                        Label("Wake", systemImage: "sun.max")
                    }
                    .buttonStyle(PrimaryButtonStyle(tint: KioskTheme.accentDim))

                    Button { Task { await model.sleepVoice() } } label: {
                        Label("Sleep", systemImage: "moon")
                    }
                    .buttonStyle(.bordered)

                    Button { Task { await model.toggleMute() } } label: {
                        Label(model.voiceMuted ? "Unmute" : "Mute", systemImage: model.voiceMuted ? "speaker.slash" : "speaker.wave.2")
                    }
                    .buttonStyle(.bordered)
                }

                KioskCard(title: "Live dictation") {
                    HStack {
                        if model.isPartialTranscript {
                            Text("LIVE")
                                .font(.caption2.bold())
                                .foregroundStyle(KioskTheme.success)
                                .padding(.horizontal, 6)
                                .padding(.vertical, 2)
                                .background(KioskTheme.success.opacity(0.15))
                                .clipShape(Capsule())
                        }
                        Spacer()
                        Text(model.lastAction)
                            .font(.caption2)
                            .foregroundStyle(.tertiary)
                    }
                    Text(displayTranscript)
                        .font(.system(.title3, design: .monospaced))
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.vertical, 4)
                    HStack {
                        Label(model.micDevice, systemImage: "mic")
                        Spacer()
                        Text("Wake: \"\(model.wakeWord)\"")
                    }
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    if model.voiceUI.state == "standby" || model.voiceUI.state == "sleeping" {
                        Text("Tap Listen, or say the wake phrase, then your command.")
                            .font(.caption)
                            .foregroundStyle(KioskTheme.warn)
                    }
                }

                if !model.activeTasks.isEmpty {
                    KioskCard(title: "In progress") {
                        ForEach(model.activeTasks.prefix(5)) { task in
                            HStack {
                                ProgressView()
                                    .controlSize(.small)
                                    .opacity(task.status == "running" ? 1 : 0.3)
                                Text(task.title).lineLimit(1)
                                Spacer()
                                Text(task.status)
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                }

                if !reply.isEmpty {
                    KioskCard(title: "Last reply") {
                        Text(reply)
                            .font(.body)
                    }
                }

                HStack(spacing: 10) {
                    TextField("Command William…", text: $prompt)
                        .textFieldStyle(.roundedBorder)
                        .disabled(model.isSending)
                        .onSubmit { Task { await send() } }
                    Button(model.isSending ? "…" : "Send") { Task { await send() } }
                        .buttonStyle(PrimaryButtonStyle())
                        .disabled(model.isSending || prompt.trimmingCharacters(in: .whitespaces).isEmpty)
                    Button("Goal") {
                        Task {
                            if await model.createGoal(from: prompt) != nil {
                                showGoalSheet = true
                                prompt = ""
                            }
                        }
                    }
                    .buttonStyle(.bordered)
                    .disabled(model.isSending)
                }
            }
            .padding(28)
        }
    }

    private var displayTranscript: String {
        if !model.liveTranscript.isEmpty {
            return model.liveTranscript + (model.isPartialTranscript ? " …" : "")
        }
        if !model.lastFinalTranscript.isEmpty { return model.lastFinalTranscript }
        return "Waiting for speech…"
    }

    private func send() async {
        if let text = await model.submitPrompt(prompt) {
            reply = text
            prompt = ""
        }
    }
}

struct ChatTabView: View {
    @Bindable var model: VoiceStateViewModel
    @State private var prompt = ""

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Text("Command William")
                    .font(.title2.bold())
                Spacer()
                Button("New chat") { Task { await model.startNewConversation() } }
                    .buttonStyle(.bordered)
                if !model.activeTasks.isEmpty {
                    Text("\(model.activeTasks.count) running")
                        .font(.caption)
                        .foregroundStyle(KioskTheme.accent)
                }
            }
            .padding(.horizontal, 20)
            .padding(.top, 16)

            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 14) {
                        if model.chatMessages.isEmpty {
                            Text("Start a conversation with William.")
                                .foregroundStyle(.secondary)
                                .padding()
                        }
                        ForEach(model.chatMessages) { msg in
                            chatBubble(msg)
                        }
                    }
                    .padding(20)
                }
                .onChange(of: model.chatMessages.count) { _, _ in
                    if let last = model.chatMessages.last {
                        withAnimation(.easeOut(duration: 0.15)) { proxy.scrollTo(last.id, anchor: .bottom) }
                    }
                }
            }
            Divider().overlay(KioskTheme.border)
            HStack(spacing: 12) {
                TextField("Message…", text: $prompt, axis: .vertical)
                    .lineLimit(1...5)
                    .textFieldStyle(.roundedBorder)
                    .disabled(model.isSending)
                Button("Send") {
                    Task {
                        _ = await model.submitPrompt(prompt)
                        prompt = ""
                    }
                }
                .buttonStyle(PrimaryButtonStyle())
                .disabled(model.isSending || prompt.trimmingCharacters(in: .whitespaces).isEmpty)
            }
            .padding(16)
        }
    }

    private func chatBubble(_ msg: ChatMessage) -> some View {
        HStack(alignment: .top) {
            if msg.role == "user" { Spacer(minLength: 80) }
            VStack(alignment: msg.role == "user" ? .trailing : .leading, spacing: 4) {
                Text(msg.content)
                    .padding(14)
                    .background(msg.role == "user" ? KioskTheme.accent.opacity(0.22) : Color.white.opacity(0.07))
                    .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
                if let engine = msg.engine, !engine.isEmpty {
                    HStack(spacing: 6) {
                        Text(engine)
                        if engine == "system" || engine == "terminal" {
                            Text("executed")
                                .foregroundStyle(KioskTheme.success)
                        }
                    }
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
                }
            }
            if msg.role != "user" { Spacer(minLength: 80) }
        }
        .id(msg.id)
    }
}

struct TasksTabView: View {
    @Bindable var model: VoiceStateViewModel
    @Binding var showGoalSheet: Bool

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                if let goal = model.pendingGoal {
                    KioskCard(title: "Goal #\(goal.id)") {
                        Text(goal.prompt).font(.headline)
                        Text("Status: \(goal.status)")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        if goal.status == "awaiting_approval" {
                            Button("Review & approve") { showGoalSheet = true }
                                .buttonStyle(PrimaryButtonStyle())
                        }
                    }
                }

                TaskPlanView(
                    tasks: model.activeTasks.isEmpty ? model.tasks : model.activeTasks,
                    goal: model.pendingGoal,
                    ollamaOnline: model.ollamaOnline,
                    workerRunning: model.workerRunning
                )

                KioskCard(title: "All tasks") {
                    ForEach(groupedTasks.keys.sorted(), id: \.self) { status in
                        if let rows = groupedTasks[status], !rows.isEmpty {
                            DisclosureGroup("\(status.capitalized) (\(rows.count))", isExpanded: .constant(status == "running" || status == "queued")) {
                                ForEach(rows.prefix(15)) { task in
                                    HStack {
                                        Text(task.title).lineLimit(1)
                                        Spacer()
                                        Text("#\(task.id)")
                                            .font(.caption2.monospaced())
                                            .foregroundStyle(.tertiary)
                                    }
                                }
                            }
                        }
                    }
                }
            }
            .padding(24)
        }
    }

    private var groupedTasks: [String: [TaskRow]] {
        Dictionary(grouping: model.tasks, by: { $0.status })
    }
}

struct ApprovalsTabView: View {
    @Bindable var model: VoiceStateViewModel

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text("Approvals")
                    .font(.title.bold())
                if model.approvals.isEmpty {
                    KioskCard {
                        Text("Nothing waiting for approval.")
                            .foregroundStyle(.secondary)
                    }
                } else {
                    ForEach(model.approvals) { item in
                        KioskCard(title: item.action ?? "Action") {
                            Text(item.detail ?? "")
                                .foregroundStyle(.secondary)
                            HStack(spacing: 12) {
                                Button("Deny") {
                                    Task { await model.resolveApproval(item.id, approved: false) }
                                }
                                .buttonStyle(.bordered)
                                Button("Approve") {
                                    Task { await model.resolveApproval(item.id, approved: true) }
                                }
                                .buttonStyle(PrimaryButtonStyle())
                            }
                        }
                    }
                }
            }
            .padding(24)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

struct SystemTabView: View {
    @Bindable var model: VoiceStateViewModel
    @State private var terminalCmd = ""
    @State private var selfDesc = ""
    @State private var improveMinutes = 30

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                KioskCard(title: "OpenClaw / WhatsApp") {
                    HStack {
                        Image(systemName: model.openclawWhatsApp ? "checkmark.circle.fill" : "xmark.circle.fill")
                            .foregroundStyle(model.openclawWhatsApp ? KioskTheme.success : KioskTheme.danger)
                        Text(model.openclawWhatsApp ? "WhatsApp connected" : "WhatsApp offline")
                        Spacer()
                        Button("Reconnect") { Task { await model.reconnectOpenClaw() } }
                            .buttonStyle(.bordered)
                    }
                    Text("Inbound WhatsApp → OpenClaw → JarvisCore. Outbound replies via bridge.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                improveRunCard

                KioskCard(title: "Security") {
                    Toggle("Full access — bypass approval gates", isOn: Binding(
                        get: { model.fullAccess },
                        set: { _ in Task { await model.toggleFullAccess() } }
                    ))
                    .toggleStyle(.switch)
                }

                KioskCard(title: "Services") {
                    Button("Fix services — restart core, voice, kiosk") {
                        Task { await model.fixServices() }
                    }
                    .buttonStyle(PrimaryButtonStyle())
                    Text("Use if voice or tasks stop responding.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                KioskCard(title: "Terminal") {
                    TextField("launchctl, brew, git…", text: $terminalCmd)
                        .textFieldStyle(.roundedBorder)
                    Button("Run") { Task { await model.runTerminal(terminalCmd) } }
                        .buttonStyle(.bordered)
                    if !model.terminalOutput.isEmpty {
                        Text(model.terminalOutput)
                            .font(.system(.caption, design: .monospaced))
                            .textSelection(.enabled)
                            .padding(10)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .background(Color.black.opacity(0.35))
                            .clipShape(RoundedRectangle(cornerRadius: 8))
                    }
                }

                KioskCard(title: "Tools") {
                    HStack {
                        Button("Analyze screen") { Task { await model.analyzeDesktop() } }
                        Button("Morning briefing") { Task { await model.triggerBriefing() } }
                        Button("Test notify") { Task { await model.testNotify() } }
                    }
                    .buttonStyle(.bordered)
                    Text(model.selfStatus).font(.caption).foregroundStyle(.secondary)
                    TextField("Self-improvement proposal…", text: $selfDesc)
                        .textFieldStyle(.roundedBorder)
                    Button("Propose change") { Task { await model.proposeSelfChange(selfDesc) } }
                        .buttonStyle(.bordered)
                }

                KioskCard(title: "Learning") {
                    Button("Refresh") { Task { await model.loadLearning(); await model.loadEvents() } }
                    Text(model.eventStats).font(.caption).foregroundStyle(.secondary)
                    Text(model.learningReport).font(.caption).lineLimit(10)
                }
            }
            .padding(24)
        }
    }

    private var improveRunCard: some View {
        KioskCard(title: "Self improvement") {
            Text("Full computer control: tests all AI-built features, finds bugs, researches fixes, edits code via Cursor, re-tests. No speech tests.")
                .font(.caption)
                .foregroundStyle(.secondary)

            HStack {
                Text("Time limit")
                Picker("", selection: $improveMinutes) {
                    Text("15 min").tag(15)
                    Text("30 min").tag(30)
                    Text("45 min").tag(45)
                    Text("60 min").tag(60)
                    Text("90 min").tag(90)
                }
                .pickerStyle(.segmented)
                .disabled(model.improveRun?.running == true)
            }

            if model.improveRun?.running == true {
                HStack {
                    ProgressView()
                    Text("Running — \(model.improveRun?.seconds_remaining ?? 0)s left")
                        .font(.subheadline.weight(.medium))
                    Spacer()
                    Text("\(model.improveRun?.fixes_applied ?? 0) fixes")
                        .font(.caption)
                        .foregroundStyle(KioskTheme.success)
                }
                Button("Stop run") { Task { await model.stopImproveRun() } }
                    .buttonStyle(.bordered)
                    .tint(KioskTheme.danger)
            } else {
                Button("Run self improvement") {
                    Task { await model.startImproveRun(minutes: improveMinutes) }
                }
                .buttonStyle(PrimaryButtonStyle(tint: KioskTheme.warn))
            }

            if let findings = model.improveRun?.findings, !findings.isEmpty {
                Text("Latest test results")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
                ForEach(findings) { f in
                    HStack {
                        Image(systemName: f.ok == true ? "checkmark.circle.fill" : "xmark.circle.fill")
                            .foregroundStyle(f.ok == true ? KioskTheme.success : KioskTheme.danger)
                        Text(f.name)
                        Spacer()
                    }
                    .font(.caption)
                }
            }

            if let logs = model.improveRun?.log, !logs.isEmpty {
                Text("Activity log")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 4) {
                        ForEach(logs.suffix(25)) { entry in
                            Text(entry.message ?? "")
                                .font(.system(.caption2, design: .monospaced))
                                .foregroundStyle(entry.level == "error" ? KioskTheme.danger : .secondary)
                        }
                    }
                }
                .frame(maxHeight: 160)
            }
        }
    }
}
