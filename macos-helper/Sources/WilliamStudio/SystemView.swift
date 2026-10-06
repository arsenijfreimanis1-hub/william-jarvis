import Charts
import SwiftUI

struct SystemView: View {
    @EnvironmentObject var model: StudioModel
    @State private var confirmCancel: Int?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Text("System").font(.largeTitle.bold())
                    Spacer()
                    ForEach(model.systems.keys.sorted(), id: \.self) { dev in
                        let s = model.systems[dev]!
                        Pill(text: "\(dev) · \(s.mode)", color: s.mode == "normal" ? Theme.color(forDevice: dev) : .orange)
                    }
                    Button { Task { _ = try? await model.api.post("system/sample"); await model.refreshSystem() } } label: { Image(systemName: "arrow.clockwise") }
                }

                HStack(alignment: .top, spacing: 12) {
                    ForEach(model.systems.keys.sorted(), id: \.self) { dev in
                        deviceCard(dev, model.systems[dev]!)
                    }
                    if model.systems.isEmpty {
                        Card(title: "Waiting for first sample") { ProgressView() }
                    }
                }

                HStack(alignment: .top, spacing: 12) {
                    Card(title: "CPU busy %", subtitle: "last \(model.history.values.first?.count ?? 0) samples") {
                        chart(\.cpuBusy, max: 100)
                    }
                    Card(title: "Memory free %") { chart(\.freePercent, max: 100) }
                    Card(title: "Swap used MB") { chart(\.swapUsedMB, max: nil) }
                }
                .frame(height: 180)

                HStack(alignment: .top, spacing: 12) {
                    governorCard
                    decisionsCard
                }

                HStack(alignment: .top, spacing: 12) {
                    oursCard
                    topCard
                }
            }
            .padding()
        }
        .alert("Cancel process \(confirmCancel ?? 0)?", isPresented: Binding(get: { confirmCancel != nil }, set: { if !$0 { confirmCancel = nil } })) {
            Button("Cancel it", role: .destructive) { if let pid = confirmCancel { Task { await model.governor("cancel", target: pid) } }; confirmCancel = nil }
            Button("Keep running", role: .cancel) { confirmCancel = nil }
        } message: { Text("The governor will SIGTERM it and journal the decision.") }
    }

    private func deviceCard(_ dev: String, _ s: SystemSample) -> some View {
        Card(title: dev == "macbook" ? "MacBook Pro" : "Mac mini", subtitle: s.ts.formatted(date: .omitted, time: .standard)) {
            HStack(spacing: 14) {
                gauge("CPU", s.cpuBusy, unit: "%", warn: 90)
                gauge("Free", s.freePercent, unit: "%", warn: nil, low: 10)
                gauge("Swap", s.swapUsedMB, unit: "MB", warn: 1800, scale: 4096)
                VStack(alignment: .leading, spacing: 4) {
                    Label(s.throttled ? "Thermal throttled" : "Thermal ok", systemImage: s.throttled ? "thermometer.high" : "thermometer.low")
                        .foregroundStyle(s.throttled ? .red : .green)
                    if let l = s.load1 { Text("load \(l, specifier: "%.2f")") }
                    Text(s.ollama.isEmpty ? "ollama: idle" : "ollama: \(s.ollama.joined(separator: ", "))").lineLimit(1)
                }.font(.caption)
            }
        }.frame(maxWidth: .infinity)
    }

    private func gauge(_ label: String, _ value: Double?, unit: String, warn: Double?, low: Double? = nil, scale: Double = 100) -> some View {
        let v = value ?? 0
        let bad = (warn.map { v >= $0 } ?? false) || (low.map { v <= $0 && value != nil } ?? false)
        return VStack(spacing: 2) {
            Gauge(value: min(v, scale), in: 0...scale) { EmptyView() } currentValueLabel: {
                Text(value.map { unit == "%" ? "\(Int($0))" : "\(Int($0))" } ?? "—").font(.caption.monospacedDigit())
            }
            .gaugeStyle(.accessoryCircularCapacity)
            .tint(bad ? .red : (label == "Free" ? .green : Theme.accent))
            .scaleEffect(0.85)
            Text("\(label) \(unit)").font(.caption2).foregroundStyle(.secondary)
        }
    }

    private func chart(_ key: KeyPath<SystemSample, Double?>, max: Double?) -> some View {
        Chart {
            ForEach(model.history.keys.sorted(), id: \.self) { dev in
                ForEach(Array((model.history[dev] ?? []).enumerated()), id: \.offset) { _, s in
                    if let v = s[keyPath: key] {
                        LineMark(x: .value("t", s.ts), y: .value("v", v))
                            .foregroundStyle(by: .value("device", dev))
                            .interpolationMethod(.monotone)
                    }
                }
            }
        }
        .chartForegroundStyleScale(["mini": Color.purple, "macbook": Color.teal])
        .chartYScale(domain: 0...(max ?? Swift.max(512, (model.history.values.flatMap { $0 }.compactMap { $0[keyPath: key] }.max() ?? 0) * 1.2)))
        .chartXAxis(.hidden)
        .chartLegend(.hidden)
    }

    private var governorCard: some View {
        let p = model.governorPolicy
        return Card(title: "Governor", subtitle: "decides keep / pause / resume / cancel") {
            HStack {
                Button("Keep") { Task { await model.governor("keep") } }
                Button("Pause heavy work") { Task { await model.governor("pause") } }
                Button("Resume") { Task { await model.governor("resume") } }.buttonStyle(.borderedProminent)
            }
            Grid(alignment: .leading, horizontalSpacing: 10, verticalSpacing: 4) {
                policyRow("enabled", p["enabled"].text)
                policyRow("cpu high", "\(p["cpu_high_percent"].text)% for \(p["cpu_high_seconds"].text)s")
                policyRow("mem free low", "\(p["mem_free_low_percent"].text)%")
                policyRow("swap high", "\(p["swap_used_high_mb"].text) MB")
                policyRow("thermal pause", p["thermal_pause"].text)
                policyRow("ollama single-flight", p["ollama_single_flight"].text)
                policyRow("cancel hog after", "\(p["cancel_our_cpu_hog_after_seconds"].text)s")
            }.font(.caption)
            Text("Edit thresholds in Settings → Governor.").font(.caption2).foregroundStyle(.secondary)
        }.frame(maxWidth: .infinity)
    }

    private func policyRow(_ k: String, _ v: String) -> some View {
        GridRow { Text(k).foregroundStyle(.secondary); Text(v).monospacedDigit() }
    }

    private var decisionsCard: some View {
        Card(title: "Recent decisions", subtitle: "\(model.decisions.count)") {
            if model.decisions.isEmpty { Text("Nothing yet — governor is in normal mode.").font(.caption).foregroundStyle(.secondary) }
            ForEach(model.decisions.prefix(10)) { d in
                HStack(alignment: .top, spacing: 6) {
                    Pill(text: d.action, color: d.action == "cancel" ? .red : (d.action == "pause" ? .orange : .green))
                    VStack(alignment: .leading) {
                        Text(d.reason).font(.caption)
                        Text("\(d.by) · \(d.ts.suffix(12))").font(.caption2).foregroundStyle(.secondary)
                    }
                }
            }
        }.frame(maxWidth: .infinity)
    }

    private var oursCard: some View {
        Card(title: "William's processes", subtitle: "governable") {
            if model.ourProcesses.isEmpty { Text("No registered subprocesses.").font(.caption).foregroundStyle(.secondary) }
            ForEach(Array(model.ourProcesses.enumerated()), id: \.offset) { _, item in
                // API gives [pid, {label, task_id, started, cancellable}] pairs
                let pid = item[0].int ?? item["pid"].int ?? 0
                let info = item[1].isNull ? item : item[1]
                HStack {
                    Text("\(pid)").monospacedDigit().foregroundStyle(.secondary)
                    Text(info["label"].string ?? "?").lineLimit(1)
                    Spacer()
                    if info["cancellable"].bool ?? true {
                        Button(role: .destructive) { confirmCancel = pid } label: { Image(systemName: "xmark.circle") }.buttonStyle(.plain)
                    }
                }.font(.caption)
            }
        }.frame(maxWidth: .infinity)
    }

    private var topCard: some View {
        Card(title: "Top processes", subtitle: "by CPU") {
            Grid(alignment: .leading, horizontalSpacing: 10, verticalSpacing: 3) {
                GridRow { Text("pid"); Text("cpu"); Text("rss"); Text("name") }.font(.caption2).foregroundStyle(.secondary)
                ForEach(Array(model.topProcesses.prefix(12).enumerated()), id: \.offset) { _, p in
                    GridRow {
                        Text("\(p["pid"].int ?? 0)").monospacedDigit()
                        Text("\(p["cpu"].double ?? 0, specifier: "%.0f")%").monospacedDigit().foregroundStyle((p["cpu"].double ?? 0) > 80 ? .red : .primary)
                        Text("\(p["rss_mb"].double ?? 0, specifier: "%.0f")M").monospacedDigit()
                        Text(p["name"].string ?? "").lineLimit(1)
                    }.font(.caption)
                }
            }
        }.frame(maxWidth: .infinity)
    }
}
