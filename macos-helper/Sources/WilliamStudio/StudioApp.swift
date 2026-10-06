import AppKit
import SwiftUI

@main
struct WilliamStudioApp: App {
    @StateObject private var model = StudioModel()
    @NSApplicationDelegateAdaptor(AppDelegate.self) var delegate

    var body: some Scene {
        WindowGroup("William Studio") {
            RootView()
                .environmentObject(model)
                .frame(minWidth: 1100, minHeight: 700)
                .preferredColorScheme(.dark)
        }
        .windowStyle(.titleBar)
        .commands {
            CommandGroup(replacing: .newItem) {}
            CommandMenu("William") {
                Button("Reconnect to core") { Task { await model.reconfigure() } }.keyboardShortcut("r", modifiers: [.command, .shift])
                Button("Fix myself (self-heal)") { Task { await model.selfHeal(apply: true) } }
                Button("Hunt for free API keys") { Task { await model.huntKeys() } }
                Divider()
                Button("Pause heavy work") { Task { await model.governor("pause") } }
                Button("Resume") { Task { await model.governor("resume") } }
            }
        }
        Settings {
            SettingsView().environmentObject(model).frame(minWidth: 900, minHeight: 600)
        }
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
}

enum Section: String, CaseIterable, Identifiable {
    case intake, graph, system, journal, ide, settings
    var id: String { rawValue }
    var title: String {
        switch self {
        case .intake: return "Intake"
        case .graph: return "Call graph"
        case .system: return "System"
        case .journal: return "Journal"
        case .ide: return "IDE"
        case .settings: return "Settings"
        }
    }
    var icon: String {
        switch self {
        case .intake: return "waveform.and.mic"
        case .graph: return "point.3.connected.trianglepath.dotted"
        case .system: return "gauge.with.dots.needle.33percent"
        case .journal: return "book.closed"
        case .ide: return "chevron.left.forwardslash.chevron.right"
        case .settings: return "gearshape"
        }
    }
}

struct RootView: View {
    @EnvironmentObject var model: StudioModel
    @State private var section: Section = .intake

    var body: some View {
        NavigationSplitView {
            List(Section.allCases, selection: $section) { s in
                Label(s.title, systemImage: s.icon).tag(s)
                    .badge(badge(for: s))
            }
            .listStyle(.sidebar)
            .navigationSplitViewColumnWidth(min: 170, ideal: 190, max: 240)
            .safeAreaInset(edge: .bottom) { statusFooter }
        } detail: {
            switch section {
            case .intake: IntakeView()
            case .graph: CallGraphView()
            case .system: SystemView()
            case .journal: JournalView()
            case .ide: IDEView(workspacePath: model.workspacePath).id(model.workspacePath)
            case .settings: SettingsView()
            }
        }
        .onChange(of: model.selectedSpan) { _, s in if s != nil, section != .graph { section = .graph } }
    }

    private func badge(for s: Section) -> Int {
        switch s {
        case .graph: return model.activeSpans.count
        case .journal: return model.journal.filter { ($0.kind == "problem") && !$0.resolved }.count
        case .settings: return model.offers.count
        default: return 0
        }
    }

    private var statusFooter: some View {
        VStack(alignment: .leading, spacing: 4) {
            Divider()
            HStack(spacing: 6) {
                Circle().fill(model.connected ? .green : .red).frame(width: 8, height: 8)
                Text(model.persona.capitalized).font(.caption.bold())
                Text("· \(model.role)").font(.caption).foregroundStyle(.secondary)
            }
            if let s = model.systems[model.role] {
                HStack(spacing: 8) {
                    Text("CPU \(Int(s.cpuBusy ?? 0))%")
                    Text("free \(Int(s.freePercent ?? 0))%")
                    if s.mode != "normal" { Pill(text: s.mode, color: .orange) }
                }.font(.caption2.monospacedDigit()).foregroundStyle(.secondary)
            }
            let z = model.tokens["spans"]["zero_token_ratio"].double
            if let z { Text("zero-token \(Int(z * 100))%").font(.caption2).foregroundStyle(z > 0.6 ? .green : .orange) }
            if !model.linkPeers.isEmpty { Text("link: \(model.linkPeers.map { $0["role"].string ?? "?" }.joined(separator: ", "))").font(.caption2).foregroundStyle(.teal) }
        }
        .padding(8)
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}
