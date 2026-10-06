import SwiftUI

struct SettingsView: View {
    @EnvironmentObject var model: StudioModel
    @State private var tab = "connection"

    var body: some View {
        HStack(alignment: .top, spacing: 0) {
            List(selection: $tab) {
                Label("Connection & devices", systemImage: "network").tag("connection")
                Label("Brain & personas", systemImage: "brain.head.profile").tag("brain")
                Label("Providers & keys", systemImage: "key").tag("keys")
                Label("Token economy", systemImage: "chart.bar").tag("tokens")
                Label("Governor", systemImage: "gauge.with.dots.needle.67percent").tag("governor")
                Label("Agents & skills", systemImage: "person.3").tag("agents")
                Label("Self-heal", systemImage: "cross.case").tag("selfheal")
            }
            .listStyle(.sidebar)
            .frame(width: 220)
            Divider()
            ScrollView {
                Group {
                    switch tab {
                    case "connection": ConnectionSettings()
                    case "brain": BrainSettings()
                    case "keys": KeysSettings()
                    case "tokens": TokenSettings()
                    case "governor": GovernorSettings()
                    case "agents": AgentsSettings()
                    default: SelfHealSettings()
                    }
                }
                .padding(20)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }
}

struct ConnectionSettings: View {
    @EnvironmentObject var model: StudioModel
    @State private var bootstrapHost = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Connection & devices").font(.title.bold())
            Card(title: "This Studio talks to") {
                TextField("Core URL", text: $model.coreURLString).textFieldStyle(.roundedBorder)
                SecureField("Fleet token (JARVIS_FLEET_TOKEN)", text: $model.token).textFieldStyle(.roundedBorder)
                HStack {
                    Button("Reconnect") { Task { await model.reconfigure() } }.buttonStyle(.borderedProminent)
                    Circle().fill(model.connected ? .green : .red).frame(width: 10, height: 10)
                    Text(model.connected ? "connected · role \(model.role) · \(model.persona)" : (model.lastError ?? "offline")).font(.caption)
                }
                Text("On the MacBook point this at http://127.0.0.1:8787 (its own core, role=macbook). The MacBook core dials the Mini over Tailscale.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Card(title: "Link peers", subtitle: model.linkPeers.isEmpty ? "none" : "\(model.linkPeers.count)") {
                ForEach(Array(model.linkPeers.enumerated()), id: \.offset) { _, p in
                    Grid(alignment: .leading, horizontalSpacing: 12) {
                        GridRow { Text("role").foregroundStyle(.secondary); Text(p["role"].string ?? "") }
                        GridRow { Text("host").foregroundStyle(.secondary); Text(p["hostname"].string ?? "") }
                        GridRow { Text("remote").foregroundStyle(.secondary); Text(p["remote"].string ?? "") }
                        GridRow { Text("heartbeat").foregroundStyle(.secondary); Text(p["last_heartbeat"].string ?? "") }
                    }.font(.caption)
                }
                Button("Refresh") { Task { await model.refreshIdentity() } }
            }
            Card(title: "Bootstrap the other Mac", subtitle: "one line") {
                Text("On the MacBook, with Tailscale logged in on both machines, run:").font(.caption)
                let host = bootstrapHost.isEmpty ? (model.coreURL.host ?? "mini") : bootstrapHost
                Text("curl -fsSL http://\(host):8787/api/bootstrap/macbook.sh | bash")
                    .font(.caption.monospaced()).textSelection(.enabled).padding(8)
                    .background(Theme.panel, in: RoundedRectangle(cornerRadius: 6))
                TextField("Mini Tailscale hostname / IP (optional)", text: $bootstrapHost).textFieldStyle(.roundedBorder)
                Text("Installs brew, python, ollama, tailscale, clones william-jarvis, writes role=macbook and the link peer URL, starts launchd, syncs skills.")
                    .font(.caption2).foregroundStyle(.secondary)
            }
            Card(title: "Health") {
                Text(model.health.prettyString).font(.caption2.monospaced()).textSelection(.enabled).lineLimit(30)
            }
        }
    }
}

struct BrainSettings: View {
    @EnvironmentObject var model: StudioModel
    @State private var steward = ""
    @State private var scout = ""
    @State private var status = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Brain & personas").font(.title.bold())
            Text("Each device has one brain. The Mini runs **Steward** (calm control plane), the MacBook runs **Scout** (fast intake). Personas are agents: `jarvis/agents/brain/<name>/README.md`. Edit here and save; the core reloads on next prompt.")
                .font(.callout)
            HStack(alignment: .top, spacing: 12) {
                personaEditor("Steward (Mac mini)", $steward, file: "jarvis/agents/brain/steward/README.md")
                personaEditor("Scout (MacBook)", $scout, file: "jarvis/agents/brain/scout/README.md")
            }
            Text(status).font(.caption).foregroundStyle(.secondary)
            Card(title: "Decision policy") {
                Text("rules (0 tokens) → local Ollama (0 tokens) → free cloud under soft ceiling → Cursor only for heavy multi-file work. Governor mode and token budget feed into every decision; see `jarvis/brain/decision.py`.")
                    .font(.caption)
            }
        }
        .task { steward = load("jarvis/agents/brain/steward/README.md"); scout = load("jarvis/agents/brain/scout/README.md") }
    }

    private func personaEditor(_ title: String, _ text: Binding<String>, file: String) -> some View {
        Card(title: title, subtitle: file) {
            TextEditor(text: text).font(.caption.monospaced()).frame(minHeight: 300)
            Button("Save") { status = save(file, text.wrappedValue) }
        }.frame(maxWidth: .infinity)
    }

    private func path(_ rel: String) -> URL { URL(fileURLWithPath: model.workspacePath).appendingPathComponent(rel) }
    private func load(_ rel: String) -> String { (try? String(contentsOf: path(rel), encoding: .utf8)) ?? "" }
    private func save(_ rel: String, _ text: String) -> String {
        do { try text.write(to: path(rel), atomically: true, encoding: .utf8); return "Saved \(rel) at \(Date().formatted(date: .omitted, time: .standard))" }
        catch { return "Save failed: \(error.localizedDescription)" }
    }
}

struct KeysSettings: View {
    @EnvironmentObject var model: StudioModel
    @State private var provider = ""
    @State private var key = ""
    @State private var status = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Providers & keys").font(.title.bold())
            Card(title: "Free providers", subtitle: "keys are masked; stored in .env on the core") {
                let list = model.providers["providers"].array
                if list.isEmpty { Text(model.providers.isNull ? "Loading…" : "No providers reported.").font(.caption) }
                Grid(alignment: .leading, horizontalSpacing: 10, verticalSpacing: 4) {
                    GridRow { Text("provider"); Text("key"); Text("status"); Text("used today"); Text("ceiling") }.font(.caption2).foregroundStyle(.secondary)
                    ForEach(Array(list.enumerated()), id: \.offset) { _, p in
                        GridRow {
                            Text(p["id"].string ?? p["name"].string ?? "?").bold()
                            Text(p["has_key"].bool == true || p["configured"].bool == true ? (p["masked"].string ?? "••••") : "—").monospaced()
                            Pill(text: p["status"].string ?? (p["ok"].bool == true ? "ok" : "?"), color: p["ok"].bool == false ? .red : .green)
                            Text("\(p["used_today"].int ?? p["usage"]["today"].int ?? 0)").monospacedDigit()
                            Text(p["soft_ceiling"].text == "null" ? "" : p["soft_ceiling"].text)
                        }.font(.caption)
                    }
                }
                HStack {
                    Button("Rescan .env") { Task { _ = try? await model.api.post("providers/scan"); await model.refreshProviders() } }
                    Button("Probe all") { Task { _ = try? await model.api.post("providers/probe"); await model.refreshProviders() } }
                }
            }
            Card(title: "Add a key") {
                HStack {
                    TextField("provider id (e.g. groq, gemini, mistral)", text: $provider).textFieldStyle(.roundedBorder)
                    SecureField("API key", text: $key).textFieldStyle(.roundedBorder)
                    Button("Save") { Task { status = await model.saveKey(provider: provider, key: key); if status == "saved" { key = "" } } }
                        .buttonStyle(.borderedProminent).disabled(provider.count < 2 || key.count < 8)
                }
                Text(status).font(.caption).foregroundStyle(status == "saved" ? .green : .red)
            }
            Card(title: "Key Hunter", subtitle: "\(model.offers.count) new offers") {
                Text("Searches HN, Reddit, GitHub and the web every 6 h for new free AI API tiers and notifies you. Mark offers as claimed once you have signed up and added the key above.")
                    .font(.caption).foregroundStyle(.secondary)
                HStack { Button("Hunt now") { Task { await model.huntKeys() } }; if model.busy { ProgressView().controlSize(.small) } }
                ForEach(Array(model.offers.enumerated()), id: \.offset) { _, o in
                    HStack(alignment: .top) {
                        VStack(alignment: .leading) {
                            Text(o["title"].string ?? o["provider"].string ?? "offer").bold()
                            if let u = o["url"].string, let url = URL(string: u) { Link(u, destination: url).font(.caption2) }
                            Text(o["summary"].string ?? "").font(.caption2).foregroundStyle(.secondary).lineLimit(2)
                        }
                        Spacer()
                        if let id = o["id"].int {
                            Button("Claimed") { Task { await model.markOffer(id, "claimed") } }
                            Button("Dismiss") { Task { await model.markOffer(id, "dismissed") } }
                        }
                    }.font(.caption)
                }
            }
        }
        .task { await model.refreshProviders(); await model.refreshOffers() }
    }
}

struct TokenSettings: View {
    @EnvironmentObject var model: StudioModel

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack { Text("Token economy").font(.title.bold()); Spacer(); Button("Refresh") { Task { await model.refreshTokens() } } }
            let sp = model.tokens["spans"]
            HStack(spacing: 12) {
                stat("Spans (7d)", sp["spans"].int ?? sp["count"].int ?? 0)
                stat("Zero-token", sp["zero_token_spans"].int ?? 0)
                stat("Prompt tok", sp["prompt_tokens"].int ?? 0)
                stat("Completion tok", sp["completion_tokens"].int ?? 0)
                if let r = sp["zero_token_ratio"].double {
                    Card(title: "Zero-token ratio") { Text("\(Int(r * 100))%").font(.title.monospacedDigit()).foregroundStyle(r > 0.6 ? .green : .orange) }
                }
            }
            Card(title: "By agent", subtitle: "spans") {
                table(sp["by_agent"].array, nameKey: "agent")
            }
            Card(title: "By provider", subtitle: "spans") {
                table(sp["by_provider"].array, nameKey: "provider")
            }
            Card(title: "Ledger by agent × device", subtitle: "provider usage rows") {
                let rows = model.tokens["ledger"].array
                Grid(alignment: .leading, horizontalSpacing: 10, verticalSpacing: 3) {
                    GridRow { Text("agent"); Text("device"); Text("provider"); Text("calls"); Text("tokens") }.font(.caption2).foregroundStyle(.secondary)
                    ForEach(Array(rows.prefix(40).enumerated()), id: \.offset) { _, r in
                        GridRow {
                            Text(r["agent"].string ?? "—"); Text(r["device"].string ?? "—"); Text(r["provider"].string ?? "—")
                            Text("\(r["calls"].int ?? 0)").monospacedDigit()
                            Text("\((r["tokens"].int ?? ((r["prompt_tokens"].int ?? 0) + (r["completion_tokens"].int ?? 0))).compact)").monospacedDigit()
                        }.font(.caption)
                    }
                }
            }
        }
        .task { await model.refreshTokens() }
    }

    private func stat(_ title: String, _ n: Int) -> some View {
        Card(title: title) { Text(n.compact).font(.title.monospacedDigit()) }
    }

    private func table(_ rows: [JSON], nameKey: String) -> some View {
        Grid(alignment: .leading, horizontalSpacing: 10, verticalSpacing: 3) {
            ForEach(Array(rows.prefix(30).enumerated()), id: \.offset) { _, r in
                GridRow {
                    Text(r[nameKey].string ?? "—")
                    Text("\(r["spans"].int ?? r["count"].int ?? 0) spans").foregroundStyle(.secondary)
                    Text("\(((r["prompt_tokens"].int ?? 0) + (r["completion_tokens"].int ?? 0) + (r["tokens"].int ?? 0)).compact) tok").monospacedDigit()
                }.font(.caption)
            }
        }
    }
}

struct GovernorSettings: View {
    @EnvironmentObject var model: StudioModel
    @State private var cpuHigh = 90.0
    @State private var cpuSeconds = 60.0
    @State private var memLow = 10.0
    @State private var swapHigh = 1800.0
    @State private var hogSeconds = 300.0
    @State private var thermal = true
    @State private var singleFlight = true
    @State private var enabled = true
    @State private var loaded = false

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Governor").font(.title.bold())
            Text("The governor samples the system every 5 s and decides whether to keep, pause, resume or cancel William's own work so your Mac stays responsive. Nothing of yours is ever cancelled — only subprocesses William started.")
                .font(.callout)
            Card(title: "Policy") {
                Toggle("Governor enabled", isOn: $enabled)
                slider("Pause when CPU busy ≥", $cpuHigh, 50...100, "%")
                slider("…for at least", $cpuSeconds, 10...300, "s")
                slider("Pause when memory free ≤", $memLow, 2...40, "%")
                slider("Pause when swap used ≥", $swapHigh, 256...8192, "MB")
                slider("Cancel our CPU hog after", $hogSeconds, 30...1800, "s")
                Toggle("Pause on thermal throttling", isOn: $thermal)
                Toggle("Serialize local model calls (Ollama single-flight)", isOn: $singleFlight)
                Button("Apply") {
                    Task {
                        await model.setPolicy(["enabled": enabled, "cpu_high_percent": cpuHigh, "cpu_high_seconds": Int(cpuSeconds),
                                               "mem_free_low_percent": memLow, "swap_used_high_mb": swapHigh,
                                               "cancel_our_cpu_hog_after_seconds": Int(hogSeconds), "thermal_pause": thermal,
                                               "ollama_single_flight": singleFlight])
                    }
                }.buttonStyle(.borderedProminent)
            }
        }
        .onAppear { load() }
        .onChange(of: model.governorPolicy) { _, _ in if !loaded { load() } }
    }

    private func load() {
        let p = model.governorPolicy
        guard !p.isNull else { return }
        cpuHigh = p["cpu_high_percent"].double ?? cpuHigh
        cpuSeconds = p["cpu_high_seconds"].double ?? cpuSeconds
        memLow = p["mem_free_low_percent"].double ?? memLow
        swapHigh = p["swap_used_high_mb"].double ?? swapHigh
        hogSeconds = p["cancel_our_cpu_hog_after_seconds"].double ?? hogSeconds
        thermal = p["thermal_pause"].bool ?? thermal
        singleFlight = p["ollama_single_flight"].bool ?? singleFlight
        enabled = p["enabled"].bool ?? enabled
        loaded = true
    }

    private func slider(_ label: String, _ v: Binding<Double>, _ range: ClosedRange<Double>, _ unit: String) -> some View {
        HStack {
            Text(label).frame(width: 220, alignment: .leading)
            Slider(value: v, in: range)
            Text("\(Int(v.wrappedValue)) \(unit)").monospacedDigit().frame(width: 80, alignment: .trailing)
        }.font(.caption)
    }
}

struct AgentsSettings: View {
    @EnvironmentObject var model: StudioModel
    @State private var skills: [JSON] = []

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Agents & skills").font(.title.bold())
            let nodes = model.agentGraph["nodes"].array
            Card(title: "Agents", subtitle: "\(nodes.count) · each is a folder with README.md + POSTTHOUGHT.md") {
                Grid(alignment: .leading, horizontalSpacing: 10, verticalSpacing: 3) {
                    GridRow { Text("name"); Text("engine"); Text("group"); Text("device"); Text("requires"); Text("cooldown") }.font(.caption2).foregroundStyle(.secondary)
                    ForEach(Array(nodes.enumerated()), id: \.offset) { _, n in
                        GridRow {
                            Text(n["name"].string ?? "")
                            Pill(text: n["engine"].string ?? "?", color: n["engine"].string == "rules" ? .green : .orange)
                            Text(n["group"].string ?? "")
                            Text(n["device_affinity"].string ?? "any")
                            Text(n["requires"].array.compactMap { $0.string }.joined(separator: ", "))
                            Text(n["cooldown_hours"].double.map { $0 > 0 ? "\(Int($0)) h" : "" } ?? "")
                        }.font(.caption)
                    }
                }
                HStack {
                    Button("Reload roster") { Task { await model.refreshGraph() } }
                    Button("Re-seed agents from folders") { runScript("scripts/seed-agents.py") }
                }
            }
            Card(title: "Skills", subtitle: "\(skills.count) in .agents/skills") {
                Text("Synced with `scripts/sync-skills.sh` (skills.sh registry via `npx skills add`). Agents list the skills they need in README front matter; the runtime injects them into the prompt.")
                    .font(.caption).foregroundStyle(.secondary)
                let cols = [GridItem(.adaptive(minimum: 180))]
                LazyVGrid(columns: cols, alignment: .leading, spacing: 4) {
                    ForEach(Array(skills.enumerated()), id: \.offset) { _, s in
                        Pill(text: s["name"].string ?? s.string ?? "?", color: (s["source"].string ?? "").contains("coach") ? .purple : .secondary)
                    }
                }
                Button("Sync skills now") { runScript("scripts/sync-skills.sh") }
            }
        }
        .task {
            await model.refreshGraph()
            let res = (try? await model.api.get("skills")) ?? .null
            skills = res["skills"].array.isEmpty ? res.array : res["skills"].array
        }
    }

    private func runScript(_ rel: String) {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/zsh")
        p.arguments = ["-lc", "cd \(model.workspacePath.shellQuoted) && (test -x \(rel) && ./\(rel) || python3 \(rel))"]
        try? p.run()
    }
}

struct SelfHealSettings: View {
    @EnvironmentObject var model: StudioModel
    @State private var diagnosis: JSON = .null

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Self-heal").font(.title.bold())
            Text("Diagnoses services, ports, logs, dependencies, open journal problems and skills; applies safe fixes (restart launchd services, rotate logs, reseed agents, reinstall deps) and journals every fix.")
                .font(.callout)
            HStack {
                Button { Task { await model.selfHeal(apply: true) } } label: { Label("Fix myself", systemImage: "cross.case.fill") }
                    .buttonStyle(.borderedProminent).controlSize(.large).disabled(model.busy)
                Button("Diagnose only") { Task { diagnosis = (try? await model.api.get("selfheal")) ?? .null } }
                if model.busy { ProgressView().controlSize(.small) }
            }
            if !model.selfhealReport.isNull {
                Card(title: "Last run", subtitle: model.selfhealReport["ts"].string) {
                    let fixes = model.selfhealReport["fixes"].array
                    let probs = model.selfhealReport["problems"].array
                    Text("\(probs.count) problems · \(fixes.count) fixes").font(.caption)
                    ForEach(Array(fixes.enumerated()), id: \.offset) { _, f in
                        HStack(alignment: .top) { Pill(text: f["ok"].bool == false ? "failed" : "fixed", color: f["ok"].bool == false ? .red : .green); Text(f["summary"].string ?? f["action"].string ?? f.text).font(.caption) }
                    }
                    ForEach(Array(probs.enumerated()), id: \.offset) { _, p in
                        HStack(alignment: .top) { Pill(text: p["severity"].text, color: .orange); Text(p["summary"].string ?? p.text).font(.caption) }
                    }
                }
            }
            if !diagnosis.isNull {
                Card(title: "Diagnosis") {
                    Text(diagnosis["diagnosis"].prettyString).font(.caption2.monospaced()).textSelection(.enabled)
                }
            }
            let open = model.journal.filter { ($0.kind == "problem" || $0.kind == "weakness") && !$0.resolved }
            Card(title: "Open problems", subtitle: "\(open.count)") {
                ForEach(open.prefix(15)) { e in
                    HStack { Pill(text: e.kind, color: Theme.color(forJournal: e.kind)); Text(e.summary).font(.caption).lineLimit(1); Spacer(); Button("resolve") { Task { await model.resolveJournal(e.id) } }.buttonStyle(.link).font(.caption2) }
                }
            }
        }
    }
}

extension String {
    var shellQuoted: String { "'" + replacingOccurrences(of: "'", with: "'\\''") + "'" }
}
