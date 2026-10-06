import SwiftUI

struct JournalView: View {
    @EnvironmentObject var model: StudioModel
    @State private var kindFilter = "all"
    @State private var deviceFilter = "all"
    @State private var unresolvedOnly = false
    @State private var search = ""
    @State private var selected: JournalEntry?

    private let kinds = ["all", "input", "decision", "problem", "weakness", "strength", "fix", "note"]

    var filtered: [JournalEntry] {
        model.journal.filter { e in
            (kindFilter == "all" || e.kind == kindFilter)
            && (deviceFilter == "all" || e.device == deviceFilter)
            && (!unresolvedOnly || (!e.resolved && (e.kind == "problem" || e.kind == "weakness")))
            && (search.isEmpty || e.summary.localizedCaseInsensitiveContains(search) || (e.agent ?? "").localizedCaseInsensitiveContains(search))
        }
    }

    var body: some View {
        HSplitView {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Text("Journal").font(.largeTitle.bold())
                    Spacer()
                    counts
                    Button { Task { await model.refreshJournal() } } label: { Image(systemName: "arrow.clockwise") }
                }
                HStack {
                    Picker("Kind", selection: $kindFilter) { ForEach(kinds, id: \.self) { Text($0) } }.frame(width: 150)
                    Picker("Device", selection: $deviceFilter) { ForEach(["all", "mini", "macbook"], id: \.self) { Text($0) } }.frame(width: 150)
                    Toggle("Open problems only", isOn: $unresolvedOnly)
                    TextField("Search", text: $search).textFieldStyle(.roundedBorder).frame(maxWidth: 240)
                }
                List(filtered, selection: Binding(get: { selected?.id }, set: { id in selected = filtered.first { $0.id == id } })) { e in
                    HStack(alignment: .top, spacing: 8) {
                        Pill(text: e.kind, color: Theme.color(forJournal: e.kind)).frame(width: 70, alignment: .leading)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(e.summary).lineLimit(2)
                            HStack(spacing: 6) {
                                Text(e.createdAt.formatted(date: .abbreviated, time: .shortened))
                                if let a = e.agent { Text("· \(a)") }
                                Text("· \(e.device)").foregroundStyle(Theme.color(forDevice: e.device))
                                if e.severity >= 3 { Text("· sev \(e.severity)").foregroundStyle(.red) }
                                if e.resolved { Text("· resolved").foregroundStyle(.green) }
                            }.font(.caption2).foregroundStyle(.secondary)
                        }
                    }
                    .tag(e.id)
                }
                .listStyle(.inset)
            }
            .padding()
            .frame(minWidth: 520)

            detail.frame(minWidth: 300, idealWidth: 380)
        }
    }

    private var counts: some View {
        HStack(spacing: 6) {
            ForEach(["problem", "weakness", "strength", "fix"], id: \.self) { k in
                let n = model.journal.filter { $0.kind == k }.count
                if n > 0 { Pill(text: "\(n) \(k)", color: Theme.color(forJournal: k)) }
            }
        }
    }

    private var detail: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 10) {
                if let e = selected {
                    HStack { Pill(text: e.kind, color: Theme.color(forJournal: e.kind)); Pill(text: e.device, color: Theme.color(forDevice: e.device)); if let a = e.agent { Pill(text: a, color: Theme.accent) } }
                    Text(e.summary).font(.title3.weight(.semibold)).textSelection(.enabled)
                    Text(e.createdAt.formatted(date: .complete, time: .standard)).font(.caption).foregroundStyle(.secondary)
                    if let d = e.detail, !d.isEmpty {
                        Text(d).font(.caption.monospaced()).textSelection(.enabled).padding(8)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .background(Theme.panel, in: RoundedRectangle(cornerRadius: 6))
                    }
                    if let tr = e.traceId {
                        Button("Open trace in call graph") {
                            model.selectedSpan = model.spans.values.first { $0.traceId == tr && $0.parentId == nil } ?? model.spans.values.first { $0.traceId == tr }
                        }.buttonStyle(.link)
                    }
                    if (e.kind == "problem" || e.kind == "weakness") && !e.resolved {
                        Button("Mark resolved") { Task { await model.resolveJournal(e.id); selected = nil } }.buttonStyle(.borderedProminent)
                    }
                } else {
                    Text("Select an entry").font(.title3).foregroundStyle(.secondary)
                    Text("Every input, decision, problem, weakness, strength and fix from both devices lands here. Problems feed Self-heal; strengths and weaknesses feed the Mentor.")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            .padding()
        }
    }
}
