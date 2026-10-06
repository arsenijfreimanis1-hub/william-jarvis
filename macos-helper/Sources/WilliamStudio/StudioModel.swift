import Foundation
import SwiftUI

struct SpanNode: Identifiable, Equatable {
    let id: String          // span_id
    var traceId: String
    var parentId: String?
    var device: String
    var kind: String        // orchestrator | agent | tool | model | link | governor
    var name: String
    var agent: String?
    var status: String
    var provider: String?
    var model: String?
    var tokens: Int
    var durationMs: Int?
    var startedAt: Date
    var input: String?
    var output: String?
    var error: String?

    static func from(_ j: JSON) -> SpanNode? {
        guard let id = j["span_id"].string, let trace = j["trace_id"].string else { return nil }
        return SpanNode(
            id: id, traceId: trace, parentId: j["parent_span_id"].string,
            device: j["device"].string ?? "mini", kind: j["kind"].string ?? "span", name: j["name"].string ?? "?",
            agent: j["agent"].string, status: j["status"].string ?? "running", provider: j["provider"].string,
            model: j["model"].string, tokens: j["tokens"].int ?? ((j["prompt_tokens"].int ?? 0) + (j["completion_tokens"].int ?? 0)),
            durationMs: j["duration_ms"].int, startedAt: ISO.date(j["started_at"].string) ?? Date(),
            input: j["input"].string, output: j["output"].string, error: j["error"].string
        )
    }
}

struct JournalEntry: Identifiable, Equatable {
    let id: Int
    var kind: String
    var device: String
    var agent: String?
    var summary: String
    var detail: String?
    var severity: Int
    var resolved: Bool
    var createdAt: Date
    var traceId: String?

    static func from(_ j: JSON) -> JournalEntry? {
        guard let id = j["id"].int else { return nil }
        return JournalEntry(id: id, kind: j["kind"].string ?? "note", device: j["device"].string ?? "mini",
                            agent: j["agent"].string, summary: j["summary"].string ?? "", detail: j["detail"].string,
                            severity: j["severity"].int ?? 1, resolved: (j["resolved"].int ?? 0) == 1,
                            createdAt: ISO.date(j["created_at"].string) ?? Date(), traceId: j["trace_id"].string)
    }
}

struct SystemSample: Equatable {
    var device: String = "mini"
    var ts: Date = Date()
    var cpuBusy: Double? = nil
    var freePercent: Double? = nil
    var freeMB: Double? = nil
    var swapUsedMB: Double? = nil
    var throttled: Bool = false
    var load1: Double? = nil
    var mode: String = "normal"
    var ollama: [String] = []

    static func fromCompact(_ j: JSON, device: String) -> SystemSample {
        SystemSample(device: device, ts: ISO.date(j["ts"].string) ?? Date(), cpuBusy: j["cpu_busy"].double,
                     freePercent: j["free_percent"].double, freeMB: j["free_mb"].double,
                     swapUsedMB: j["swap_used_mb"].double, throttled: j["throttled"].bool ?? false,
                     load1: j["load"][0].double, mode: j["mode"].string ?? "normal",
                     ollama: j["ollama"].array.compactMap { $0["name"].string })
    }
}

struct GovernorDecision: Identifiable, Equatable {
    var id: String { ts + action + reason }
    var ts: String
    var action: String
    var reason: String
    var by: String
}

struct ChatTurn: Identifiable, Equatable {
    let id = UUID()
    var role: String  // user | william
    var text: String
    var engine: String?
    var agent: String?
    var tokens: Int?
    var traceId: String?
    var at = Date()
}

enum ISO {
    static let f1: ISO8601DateFormatter = { let f = ISO8601DateFormatter(); f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]; return f }()
    static let f2: ISO8601DateFormatter = { let f = ISO8601DateFormatter(); f.formatOptions = [.withInternetDateTime]; return f }()
    static let sqlite: DateFormatter = { let f = DateFormatter(); f.dateFormat = "yyyy-MM-dd HH:mm:ss"; f.timeZone = TimeZone(identifier: "UTC"); return f }()
    static func date(_ s: String?) -> Date? {
        guard let s else { return nil }
        return f1.date(from: s) ?? f2.date(from: s) ?? sqlite.date(from: s)
    }
}

@MainActor
final class StudioModel: ObservableObject {
    // Connection
    @AppStorage("studio.coreURL") var coreURLString = "http://127.0.0.1:8787"
    @AppStorage("studio.miniURL") var miniURLString = ""
    @AppStorage("studio.token") var token = ""
    @AppStorage("studio.workspace") var workspacePath = NSString("~/jarvis-core").expandingTildeInPath
    @AppStorage("studio.autoSendAfterSpeech") var autoSendAfterSpeech = false
    @AppStorage("studio.graphWindowMinutes") var graphWindowMinutes = 10

    @Published var connected = false
    @Published var lastError: String?
    @Published var role = "?"
    @Published var persona = "?"
    @Published var linkPeers: [JSON] = []

    // Live data
    @Published var spans: [String: SpanNode] = [:]
    @Published var journal: [JournalEntry] = []
    @Published var systems: [String: SystemSample] = [:]   // by device
    @Published var history: [String: [SystemSample]] = [:]
    @Published var decisions: [GovernorDecision] = []
    @Published var topProcesses: [JSON] = []
    @Published var ourProcesses: [JSON] = []
    @Published var governorPolicy: JSON = .null
    @Published var tokens: JSON = .null
    @Published var agentGraph: JSON = .null
    @Published var offers: [JSON] = []
    @Published var providers: JSON = .null
    @Published var health: JSON = .null
    @Published var selfhealReport: JSON = .null
    @Published var chat: [ChatTurn] = []
    @Published var busy = false
    @Published var selectedSpan: SpanNode?

    let api: StudioAPI
    private var streamTask: Task<Void, Never>?
    private var pollTask: Task<Void, Never>?

    init() {
        api = StudioAPI(base: URL(string: "http://127.0.0.1:8787/api")!, token: "")
        Task { await reconfigure() }
    }

    var coreURL: URL { URL(string: coreURLString) ?? URL(string: "http://127.0.0.1:8787")! }
    var apiBase: URL { coreURL.appendingPathComponent("api") }

    func reconfigure() async {
        await api.configure(base: apiBase, token: token)
        streamTask?.cancel(); pollTask?.cancel()
        streamTask = Task { await streamLoop() }
        pollTask = Task { await pollLoop() }
        await refreshAll()
    }

    // MARK: live stream
    private func streamLoop() async {
        while !Task.isCancelled {
            do {
                for try await event in api.events(base: apiBase, token: token) {
                    connected = true
                    handle(event)
                }
            } catch {
                connected = false
                lastError = error.localizedDescription
            }
            try? await Task.sleep(for: .seconds(3))
        }
    }

    private func handle(_ e: JSON) {
        let kind = e["kind"].string ?? ""
        let meta = e["metadata"]
        switch kind {
        case "span":
            if let node = SpanNode.from(meta) { spans[node.id] = node; pruneSpans() }
        case "journal":
            if let row = JournalEntry.from(meta["journal"]) {
                journal.removeAll { $0.id == row.id }
                journal.insert(row, at: 0)
                if journal.count > 500 { journal.removeLast(journal.count - 500) }
            }
        case "system":
            let sys = meta["system"]
            if !sys.isNull {
                let device = sys["device"].string ?? role
                let sample = SystemSample.fromCompact(sys, device: device)
                systems[device] = sample
                history[device, default: []].append(sample)
                if history[device]!.count > 240 { history[device]!.removeFirst() }
            }
        case "governor":
            let d = GovernorDecision(ts: meta["ts"].string ?? "", action: meta["action"].string ?? "",
                                     reason: meta["reason"].string ?? e["detail"].string ?? "", by: meta["by"].string ?? "")
            decisions.insert(d, at: 0)
            if decisions.count > 100 { decisions.removeLast() }
        case "offer":
            Task { await refreshOffers() }
        default:
            break
        }
    }

    private func pruneSpans() {
        let cutoff = Date().addingTimeInterval(-Double(max(2, graphWindowMinutes)) * 60)
        for (id, s) in spans where s.startedAt < cutoff && s.status != "running" { spans.removeValue(forKey: id) }
    }

    // MARK: polling
    private func pollLoop() async {
        while !Task.isCancelled {
            await refreshSystem()
            try? await Task.sleep(for: .seconds(5))
        }
    }

    func refreshAll() async {
        await refreshIdentity()
        await refreshSystem()
        await refreshSpans()
        await refreshJournal()
        await refreshTokens()
        await refreshGraph()
        await refreshOffers()
        await refreshProviders()
    }

    func refreshIdentity() async {
        do {
            let brain = try await api.get("brain")
            role = brain["role"].string ?? "?"
            persona = brain["persona"].string ?? "?"
            let link = try await api.get("link/status")
            linkPeers = link["peers"].array
            health = try await api.get("health")
            connected = true
        } catch { lastError = error.localizedDescription; connected = false }
    }

    func refreshSystem() async {
        guard let live = try? await api.get("system/live") else { return }
        let device = live["device"].string ?? role
        let sample = live["sample"]
        if !sample.isNull {
            var s = SystemSample.fromCompact(.object([
                "ts": sample["ts"], "cpu_busy": sample["cpu"]["busy"], "load": sample["load"],
                "free_mb": sample["memory"]["free_mb"],
                "free_percent": sample["memory"]["pressure_free_percent"].isNull ? sample["memory"]["free_percent"] : sample["memory"]["pressure_free_percent"],
                "swap_used_mb": sample["swap"]["used_mb"], "throttled": sample["thermal"]["throttled"],
                "mode": live["mode"], "ollama": sample["ollama"],
            ]), device: device)
            s.mode = live["mode"].string ?? "normal"
            systems[device] = s
            topProcesses = sample["top_processes"].array
        }
        ourProcesses = live["ours"].array
        governorPolicy = live["policy"]
        if decisions.isEmpty {
            decisions = live["decisions"].array.reversed().map {
                GovernorDecision(ts: $0["ts"].string ?? "", action: $0["action"].string ?? "", reason: $0["reason"].string ?? "", by: $0["by"].string ?? "")
            }
        }
        if history[device]?.isEmpty ?? true {
            history[device] = live["history"].array.map {
                SystemSample(device: device, ts: ISO.date($0["ts"].string) ?? Date(), cpuBusy: $0["cpu_busy"].double,
                             freePercent: $0["free_percent"].double, swapUsedMB: $0["swap_used_mb"].double, load1: $0["load1"].double)
            }
        }
        for peer in live["peers"].array {
            let dev = peer["role"].string ?? "peer"
            if !peer["system"].isNull { systems[dev] = SystemSample.fromCompact(peer["system"], device: dev) }
        }
    }

    func refreshSpans() async {
        guard let res = try? await api.get("spans", query: ["limit": "400"]) else { return }
        for j in res["spans"].array { if let n = SpanNode.from(j) { spans[n.id] = n } }
        for j in res["active"].array { if let n = SpanNode.from(j) { spans[n.id] = n } }
        pruneSpans()
    }

    func refreshJournal() async {
        guard let res = try? await api.get("journal", query: ["limit": "200"]) else { return }
        journal = res["entries"].array.compactMap(JournalEntry.from)
    }

    func refreshTokens() async { tokens = (try? await api.get("tokens", query: ["days": "7"])) ?? .null }
    func refreshGraph() async { agentGraph = (try? await api.get("agent-graph")) ?? .null }
    func refreshOffers() async { offers = (try? await api.get("providers/offers", query: ["status": "new"]))?["offers"].array ?? [] }
    func refreshProviders() async { providers = (try? await api.get("providers")) ?? .null }

    // MARK: actions
    func send(_ text: String, voice: Bool) async {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        chat.append(ChatTurn(role: "user", text: trimmed))
        busy = true
        defer { busy = false }
        do {
            let res = try await api.post("chat", ["message": trimmed, "source": voice ? "voice" : "studio"])
            chat.append(ChatTurn(role: "william", text: res["reply"].string ?? "(no reply)", engine: res["engine"].string,
                                 agent: res["agent_name"].string, tokens: res["tokens"].int, traceId: res["trace_id"].string))
        } catch {
            chat.append(ChatTurn(role: "william", text: "Error: \(error.localizedDescription)", engine: "error"))
        }
        await refreshTokens()
    }

    func governor(_ action: String, target: Int? = nil) async {
        var body: [String: Any] = ["action": action]
        if let target { body["target"] = target }
        _ = try? await api.post("system/governor", body)
        await refreshSystem()
    }

    func setPolicy(_ updates: [String: Any]) async {
        _ = try? await api.post("system/governor/policy", ["updates": updates])
        await refreshSystem()
    }

    func selfHeal(apply: Bool) async {
        busy = true; defer { busy = false }
        selfhealReport = (try? await api.post("selfheal", ["apply": apply, "run_tests": false])) ?? .null
        await refreshJournal()
    }

    func resolveJournal(_ id: Int) async {
        _ = try? await api.post("journal/\(id)/resolve")
        await refreshJournal()
    }

    func huntKeys() async {
        busy = true; defer { busy = false }
        _ = try? await api.post("providers/offers/hunt")
        await refreshOffers()
    }

    func markOffer(_ id: Int, _ status: String) async {
        _ = try? await api.post("providers/offers/\(id)", ["status": status])
        await refreshOffers()
    }

    func saveKey(provider: String, key: String) async -> String {
        do {
            let res = try await api.post("providers/keys", ["provider": provider, "key": key])
            await refreshProviders()
            return res["ok"].bool == false ? (res["error"].string ?? "failed") : "saved"
        } catch { return error.localizedDescription }
    }

    // MARK: derived
    var activeSpans: [SpanNode] { spans.values.filter { $0.status == "running" }.sorted { $0.startedAt < $1.startedAt } }
    var recentSpans: [SpanNode] { spans.values.sorted { $0.startedAt > $1.startedAt } }
    func children(of id: String) -> [SpanNode] { spans.values.filter { $0.parentId == id }.sorted { $0.startedAt < $1.startedAt } }
}
