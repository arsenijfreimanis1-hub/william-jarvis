import SwiftUI

/// Live DAG: devices → orchestrators → agents → tools/models, built from spans.
struct CallGraphView: View {
    @EnvironmentObject var model: StudioModel
    @State private var pulse = false
    @State private var hover: String?

    private struct Layout {
        var positions: [String: CGPoint] = [:]
        var columns: [[SpanNode]] = []
    }

    var body: some View {
        HSplitView {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Text("Call graph").font(.largeTitle.bold())
                    Spacer()
                    Pill(text: "\(model.activeSpans.count) active", color: .yellow)
                    Pill(text: "\(model.spans.count) spans / \(model.graphWindowMinutes) min")
                    Stepper("", value: $model.graphWindowMinutes, in: 2...120, step: 2).labelsHidden()
                    Button { Task { await model.refreshSpans() } } label: { Image(systemName: "arrow.clockwise") }
                }
                legend
                GeometryReader { geo in
                    let layout = computeLayout(size: geo.size)
                    ZStack(alignment: .topLeading) {
                        Canvas { ctx, _ in
                            drawEdges(ctx: ctx, layout: layout)
                        }
                        ForEach(layout.columns.flatMap { $0 }) { node in
                            if let p = layout.positions[node.id] {
                                nodeView(node)
                                    .position(p)
                                    .onTapGesture { model.selectedSpan = node }
                                    .onHover { hover = $0 ? node.id : nil }
                            }
                        }
                        columnHeaders(layout: layout, size: geo.size)
                    }
                    .background(Theme.panel.opacity(0.4), in: RoundedRectangle(cornerRadius: 12))
                }
                .onAppear { withAnimation(.easeInOut(duration: 0.9).repeatForever(autoreverses: true)) { pulse.toggle() } }
            }
            .padding()
            .frame(minWidth: 560)

            detail
                .frame(minWidth: 300, idealWidth: 360)
        }
    }

    private var legend: some View {
        HStack(spacing: 10) {
            ForEach(["orchestrator", "agent", "tool", "model", "link", "governor"], id: \.self) { k in
                HStack(spacing: 4) { Circle().fill(Theme.color(forKind: k)).frame(width: 8, height: 8); Text(k).font(.caption2) }
            }
            Divider().frame(height: 12)
            HStack(spacing: 4) { Circle().fill(.yellow).frame(width: 8, height: 8); Text("running").font(.caption2) }
            HStack(spacing: 4) { Circle().fill(.red).frame(width: 8, height: 8); Text("failed").font(.caption2) }
            Spacer()
        }
    }

    // Columns: 0 devices/orchestrators, 1 agents (depth 1), 2 nested agents/tools, 3 models/tools leaf
    private func computeLayout(size: CGSize) -> Layout {
        var layout = Layout()
        let nodes = model.recentSpans
        guard !nodes.isEmpty else { return layout }
        var depth: [String: Int] = [:]
        func d(_ n: SpanNode) -> Int {
            if let v = depth[n.id] { return v }
            var v = 0
            if let p = n.parentId, let parent = model.spans[p] { v = min(3, d(parent) + 1) }
            depth[n.id] = v
            return v
        }
        var cols: [[SpanNode]] = [[], [], [], []]
        for n in nodes.prefix(160) { cols[d(n)].append(n) }
        for i in cols.indices { cols[i].sort { ($0.device, $0.startedAt) < ($1.device, $1.startedAt) } }
        layout.columns = cols
        let colW = size.width / 4
        for (ci, col) in cols.enumerated() {
            let count = max(col.count, 1)
            let rowH = min(56, (size.height - 50) / CGFloat(count))
            let totalH = rowH * CGFloat(count)
            let startY = max(40, (size.height - totalH) / 2 + rowH / 2)
            for (ri, n) in col.enumerated() {
                layout.positions[n.id] = CGPoint(x: colW * CGFloat(ci) + colW / 2, y: startY + rowH * CGFloat(ri))
            }
        }
        return layout
    }

    private func drawEdges(ctx: GraphicsContext, layout: Layout) {
        for col in layout.columns {
            for n in col {
                guard let p = n.parentId, let from = layout.positions[p], let to = layout.positions[n.id] else { continue }
                var path = Path()
                path.move(to: from)
                let c1 = CGPoint(x: from.x + (to.x - from.x) * 0.5, y: from.y)
                let c2 = CGPoint(x: from.x + (to.x - from.x) * 0.5, y: to.y)
                path.addCurve(to: to, control1: c1, control2: c2)
                let color = n.status == "running" ? Color.yellow : (n.status == "failed" ? .red : Theme.color(forKind: n.kind).opacity(0.6))
                ctx.stroke(path, with: .color(color), style: StrokeStyle(lineWidth: n.status == "running" ? 2.5 : 1.2,
                                                                         dash: n.status == "running" ? [6, 4] : []))
            }
        }
    }

    private func columnHeaders(layout: Layout, size: CGSize) -> some View {
        let titles = ["Devices / orchestrators", "Agents", "Required agents / tools", "Models / tools"]
        return HStack(spacing: 0) {
            ForEach(0..<4, id: \.self) { i in
                Text(titles[i]).font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                    .frame(width: size.width / 4)
            }
        }
        .padding(.top, 8)
    }

    private func nodeView(_ n: SpanNode) -> some View {
        let running = n.status == "running"
        return VStack(spacing: 2) {
            HStack(spacing: 4) {
                Circle().fill(Theme.color(forStatus: n.status)).frame(width: 7, height: 7)
                    .scaleEffect(running && pulse ? 1.5 : 1)
                Text(n.agent ?? n.name).font(.caption.weight(.semibold)).lineLimit(1)
            }
            HStack(spacing: 4) {
                Text(n.kind).font(.caption2).foregroundStyle(.secondary)
                if n.tokens > 0 { Text("\(n.tokens.compact) tok").font(.caption2).foregroundStyle(.orange) }
                else if n.status == "done" { Text("0 tok").font(.caption2).foregroundStyle(.green) }
                if let ms = n.durationMs { Text("\(ms) ms").font(.caption2).foregroundStyle(.secondary) }
            }
        }
        .padding(.horizontal, 8).padding(.vertical, 5)
        .background(Theme.color(forKind: n.kind).opacity(hover == n.id || model.selectedSpan?.id == n.id ? 0.35 : 0.15),
                    in: RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Theme.color(forDevice: n.device).opacity(0.7), lineWidth: 1))
        .frame(maxWidth: 170)
    }

    private var detail: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 10) {
                if let s = model.selectedSpan {
                    Text(s.agent ?? s.name).font(.title2.bold())
                    HStack { Pill(text: s.kind, color: Theme.color(forKind: s.kind)); Pill(text: s.status, color: Theme.color(forStatus: s.status)); Pill(text: s.device, color: Theme.color(forDevice: s.device)) }
                    Grid(alignment: .leading, horizontalSpacing: 12, verticalSpacing: 4) {
                        GridRow { Text("trace").foregroundStyle(.secondary); Text(s.traceId.prefix(12)).monospaced() }
                        GridRow { Text("provider").foregroundStyle(.secondary); Text(s.provider ?? "—") }
                        GridRow { Text("model").foregroundStyle(.secondary); Text(s.model ?? "—") }
                        GridRow { Text("tokens").foregroundStyle(.secondary); Text("\(s.tokens)") }
                        GridRow { Text("duration").foregroundStyle(.secondary); Text(s.durationMs.map { "\($0) ms" } ?? "running") }
                        GridRow { Text("started").foregroundStyle(.secondary); Text(s.startedAt.formatted(date: .omitted, time: .standard)) }
                    }.font(.caption)
                    if let i = s.input { section("Input", i) }
                    if let o = s.output { section("Output", o) }
                    if let e = s.error { section("Error", e, color: .red) }
                    let kids = model.children(of: s.id)
                    if !kids.isEmpty {
                        Text("Children").font(.headline)
                        ForEach(kids) { k in
                            Button { model.selectedSpan = k } label: {
                                HStack { Pill(text: k.kind, color: Theme.color(forKind: k.kind)); Text(k.agent ?? k.name); Spacer(); Text("\(k.tokens) tok").font(.caption2) }
                            }.buttonStyle(.plain)
                        }
                    }
                    if let p = s.parentId, let parent = model.spans[p] {
                        Button("↑ parent: \(parent.agent ?? parent.name)") { model.selectedSpan = parent }.buttonStyle(.link)
                    }
                    journalFor(trace: s.traceId)
                } else {
                    Text("Select a node").font(.title3).foregroundStyle(.secondary)
                    Text("Nodes are spans: every orchestrator, agent, required agent, tool and model call across both devices. Yellow dashed edges are calls in flight right now.")
                        .font(.caption).foregroundStyle(.secondary)
                    agentGraphStatic
                }
            }
            .padding()
        }
    }

    private func section(_ title: String, _ text: String, color: Color = .primary) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(.headline)
            Text(text).font(.caption.monospaced()).foregroundStyle(color).textSelection(.enabled)
                .padding(8).frame(maxWidth: .infinity, alignment: .leading)
                .background(Theme.panel, in: RoundedRectangle(cornerRadius: 6))
        }
    }

    private func journalFor(trace: String) -> some View {
        let rows = model.journal.filter { $0.traceId == trace }
        return Group {
            if !rows.isEmpty {
                Text("Journal for this trace").font(.headline)
                ForEach(rows) { r in
                    HStack(alignment: .top) { Pill(text: r.kind, color: Theme.color(forJournal: r.kind)); Text(r.summary).font(.caption) }
                }
            }
        }
    }

    private var agentGraphStatic: some View {
        let nodes = model.agentGraph["nodes"].array
        let edges = model.agentGraph["edges"].array
        return Card(title: "Agent roster", subtitle: "\(nodes.count) agents · \(edges.count) requires-edges") {
            ForEach(Array(nodes.enumerated()), id: \.offset) { _, n in
                HStack {
                    Pill(text: n["engine"].string ?? "?", color: n["engine"].string == "rules" ? .green : .orange)
                    Text(n["name"].string ?? "").font(.caption)
                    Spacer()
                    Text(n["group"].string ?? "").font(.caption2).foregroundStyle(.secondary)
                    Pill(text: n["device_affinity"].string ?? "any", color: Theme.color(forDevice: n["device_affinity"].string ?? ""))
                }
            }
        }
    }
}
