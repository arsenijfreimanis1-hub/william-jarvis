import SwiftUI

struct IntakeView: View {
    @EnvironmentObject var model: StudioModel
    @StateObject private var speech = SpeechIntake()
    @State private var draft = ""
    @FocusState private var focused: Bool

    var body: some View {
        HSplitView {
            VStack(spacing: 12) {
                header
                conversation
                composer
            }
            .padding()
            .frame(minWidth: 480)

            VStack(alignment: .leading, spacing: 10) {
                Card(title: "Brain on this device", subtitle: model.role) {
                    HStack {
                        Image(systemName: model.role == "macbook" ? "laptopcomputer" : "macmini")
                            .font(.title2).foregroundStyle(Theme.color(forDevice: model.role))
                        VStack(alignment: .leading) {
                            Text(model.persona.capitalized).font(.title3.weight(.semibold))
                            Text(model.role == "macbook" ? "Fast intake; hands briefs to Steward over the link."
                                 : "Calm control plane; delegates to agents.").font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
                Card(title: "Link", subtitle: model.linkPeers.isEmpty ? "no peer" : "\(model.linkPeers.count) peer(s)") {
                    if model.linkPeers.isEmpty {
                        Text("No other device connected. The Mini accepts on /ws/link; the MacBook dials it.")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    ForEach(Array(model.linkPeers.enumerated()), id: \.offset) { _, p in
                        HStack {
                            Circle().fill(.green).frame(width: 8, height: 8)
                            Text(p["role"].string ?? "peer").bold()
                            Text(p["hostname"].string ?? "").foregroundStyle(.secondary)
                            Spacer()
                            if let cpu = p["system"]["cpu_busy"].double { Text("CPU \(Int(cpu))%").font(.caption.monospacedDigit()) }
                        }
                    }
                }
                Card(title: "Now running", subtitle: "\(model.activeSpans.count)") {
                    if model.activeSpans.isEmpty { Text("Idle").font(.caption).foregroundStyle(.secondary) }
                    ForEach(model.activeSpans.prefix(8)) { s in
                        HStack {
                            Pill(text: s.kind, color: Theme.color(forKind: s.kind))
                            Text(s.agent ?? s.name).lineLimit(1)
                            Spacer()
                            Pill(text: s.device, color: Theme.color(forDevice: s.device))
                        }.font(.caption)
                    }
                }
                Spacer()
            }
            .padding()
            .frame(minWidth: 280, idealWidth: 320)
        }
        .onAppear { speech.requestAuthorization() }
        .onChange(of: speech.listening) { _, listening in
            if !listening, !speech.transcript.isEmpty {
                draft = speech.transcript
                if model.autoSendAfterSpeech { Task { await sendDraft(voice: true) } }
            }
        }
        .onChange(of: speech.transcript) { _, t in if speech.listening { draft = t } }
    }

    private var header: some View {
        HStack {
            Text("Intake").font(.largeTitle.bold())
            Spacer()
            if model.busy { ProgressView().controlSize(.small) }
            Circle().fill(model.connected ? .green : .red).frame(width: 10, height: 10)
            Text(model.connected ? "core online" : (model.lastError ?? "offline")).font(.caption).foregroundStyle(.secondary)
        }
    }

    private var conversation: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 10) {
                    ForEach(model.chat) { turn in
                        VStack(alignment: turn.role == "user" ? .trailing : .leading, spacing: 4) {
                            Text(turn.text)
                                .textSelection(.enabled)
                                .padding(10)
                                .background(turn.role == "user" ? Theme.accent.opacity(0.2) : Theme.panel,
                                            in: RoundedRectangle(cornerRadius: 10))
                            HStack(spacing: 6) {
                                if let e = turn.engine { Pill(text: e) }
                                if let a = turn.agent { Pill(text: a, color: Theme.accent) }
                                if let t = turn.tokens { Pill(text: "\(t) tok", color: t == 0 ? .green : .orange) }
                                if let tr = turn.traceId {
                                    Button("trace") { model.selectedSpan = model.spans.values.first { $0.traceId == tr && $0.parentId == nil } }
                                        .buttonStyle(.link).font(.caption2)
                                }
                            }
                        }
                        .frame(maxWidth: .infinity, alignment: turn.role == "user" ? .trailing : .leading)
                        .id(turn.id)
                    }
                }
                .padding(.vertical, 4)
            }
            .onChange(of: model.chat.count) { _, _ in
                if let last = model.chat.last { withAnimation { proxy.scrollTo(last.id, anchor: .bottom) } }
            }
        }
    }

    private var composer: some View {
        VStack(spacing: 8) {
            HStack(alignment: .bottom, spacing: 8) {
                Button {
                    speech.toggle()
                } label: {
                    ZStack {
                        Circle().fill(speech.listening ? Color.red.opacity(0.25 + Double(speech.level) * 0.6) : Theme.panel)
                            .frame(width: 44, height: 44)
                        Image(systemName: speech.listening ? "waveform" : "mic.fill").font(.title3)
                            .foregroundStyle(speech.listening ? .red : .primary)
                    }
                }
                .buttonStyle(.plain)
                .help(speech.authorized ? "Push to talk (on-device)" : "Grant Speech + Microphone permission in System Settings")
                .keyboardShortcut(.space, modifiers: [.command])

                TextEditor(text: $draft)
                    .font(.body)
                    .frame(minHeight: 44, maxHeight: 140)
                    .scrollContentBackground(.hidden)
                    .padding(6)
                    .background(Theme.panel, in: RoundedRectangle(cornerRadius: 8))
                    .focused($focused)

                Button {
                    Task { await sendDraft(voice: false) }
                } label: { Image(systemName: "paperplane.fill").font(.title3) }
                    .buttonStyle(.borderedProminent)
                    .keyboardShortcut(.return, modifiers: [.command])
                    .disabled(draft.trimmingCharacters(in: .whitespaces).isEmpty || model.busy)
            }
            HStack {
                if let err = speech.error { Text(err).font(.caption).foregroundStyle(.red) }
                Toggle("Send automatically after speech", isOn: $model.autoSendAfterSpeech).font(.caption)
                Spacer()
                Text("⌘Space talk · ⌘↩ send").font(.caption2).foregroundStyle(.secondary)
            }
        }
    }

    private func sendDraft(voice: Bool) async {
        let text = draft
        draft = ""
        await model.send(text, voice: voice)
    }
}
