import AppKit
import SwiftUI

struct FileNode: Identifiable, Hashable {
    let id: String   // absolute path
    let name: String
    let isDir: Bool
    var children: [FileNode]? = nil
}

@MainActor
final class Workspace: ObservableObject {
    @Published var root: URL
    @Published var tree: [FileNode] = []
    @Published var openPath: String?
    @Published var text = ""
    @Published var savedText = ""
    @Published var gitStatus: [(status: String, path: String)] = []
    @Published var gitBranch = ""
    @Published var gitDiff = ""
    @Published var filter = ""
    @Published var message: String?
    private var stream: FSEventStreamRef?

    private let ignored: Set<String> = [".git", "node_modules", ".build", "__pycache__", ".venv", "venv", ".pytest_cache", ".mypy_cache", ".ruff_cache", "dist", ".DS_Store"]

    var dirty: Bool { text != savedText }
    var language: String { openPath.map(Highlighter.language(for:)) ?? "plain" }

    init(root: URL) {
        self.root = root
        reload()
        watch()
    }

    func setRoot(_ url: URL) { stop(); root = url; openPath = nil; text = ""; savedText = ""; reload(); watch() }

    func reload() {
        tree = scan(root, depth: 0)
        refreshGit()
    }

    private func scan(_ url: URL, depth: Int) -> [FileNode] {
        guard depth < 6, let items = try? FileManager.default.contentsOfDirectory(at: url, includingPropertiesForKeys: [.isDirectoryKey], options: []) else { return [] }
        return items
            .filter { !ignored.contains($0.lastPathComponent) }
            .sorted { a, b in
                let ad = (try? a.resourceValues(forKeys: [.isDirectoryKey]).isDirectory) ?? false
                let bd = (try? b.resourceValues(forKeys: [.isDirectoryKey]).isDirectory) ?? false
                if ad != bd { return ad }
                return a.lastPathComponent.localizedStandardCompare(b.lastPathComponent) == .orderedAscending
            }
            .map { u in
                let isDir = (try? u.resourceValues(forKeys: [.isDirectoryKey]).isDirectory) ?? false
                return FileNode(id: u.path, name: u.lastPathComponent, isDir: isDir, children: isDir ? scan(u, depth: depth + 1) : nil)
            }
    }

    func open(_ path: String) {
        guard let data = FileManager.default.contents(atPath: path) else { message = "Cannot read \(path)"; return }
        if data.count > 2_000_000 { message = "File too large for the editor (\(data.count / 1024) KB)"; return }
        guard let s = String(data: data, encoding: .utf8) ?? String(data: data, encoding: .isoLatin1) else { message = "Binary file"; return }
        openPath = path
        text = s
        savedText = s
        message = nil
    }

    func save() {
        guard let p = openPath else { return }
        do { try text.write(toFile: p, atomically: true, encoding: .utf8); savedText = text; message = "Saved \((p as NSString).lastPathComponent)"; refreshGit() }
        catch { message = "Save failed: \(error.localizedDescription)" }
    }

    func newFile(in dir: String, name: String) {
        let p = (dir as NSString).appendingPathComponent(name)
        FileManager.default.createFile(atPath: p, contents: Data())
        reload(); open(p)
    }

    // MARK: git
    func refreshGit() {
        Task.detached { [root] in
            let branch = Self.run("git", ["rev-parse", "--abbrev-ref", "HEAD"], cwd: root).trimmingCharacters(in: .whitespacesAndNewlines)
            let status = Self.run("git", ["status", "--porcelain"], cwd: root)
            let rows: [(String, String)] = status.split(separator: "\n").compactMap { line in
                guard line.count > 3 else { return nil }
                let st = String(line.prefix(2)); let path = String(line.dropFirst(3))
                return (st, path)
            }
            await MainActor.run { self.gitBranch = branch; self.gitStatus = rows.map { (status: $0.0, path: $0.1) } }
        }
    }

    func diff(for path: String?) {
        Task.detached { [root] in
            let args = path.map { ["diff", "--", $0] } ?? ["diff"]
            let out = Self.run("git", args, cwd: root)
            await MainActor.run { self.gitDiff = out.isEmpty ? "(no unstaged changes)" : out }
        }
    }

    nonisolated static func run(_ cmd: String, _ args: [String], cwd: URL) -> String {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        p.arguments = [cmd] + args
        p.currentDirectoryURL = cwd
        let pipe = Pipe()
        p.standardOutput = pipe; p.standardError = pipe
        do { try p.run() } catch { return "" }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        return String(decoding: data, as: UTF8.self)
    }

    // MARK: FSEvents
    private func watch() {
        var ctx = FSEventStreamContext(version: 0, info: Unmanaged.passUnretained(self).toOpaque(), retain: nil, release: nil, copyDescription: nil)
        let cb: FSEventStreamCallback = { _, info, _, _, _, _ in
            guard let info else { return }
            let ws = Unmanaged<Workspace>.fromOpaque(info).takeUnretainedValue()
            Task { @MainActor in ws.debouncedReload() }
        }
        guard let s = FSEventStreamCreate(nil, cb, &ctx, [root.path] as CFArray, FSEventStreamEventId(kFSEventStreamEventIdSinceNow), 0.8,
                                          FSEventStreamCreateFlags(kFSEventStreamCreateFlagFileEvents | kFSEventStreamCreateFlagIgnoreSelf)) else { return }
        FSEventStreamSetDispatchQueue(s, DispatchQueue.main)
        FSEventStreamStart(s)
        stream = s
    }

    private var reloadWork: DispatchWorkItem?
    private func debouncedReload() {
        reloadWork?.cancel()
        let w = DispatchWorkItem { [weak self] in
            guard let self else { return }
            self.tree = self.scan(self.root, depth: 0)
            self.refreshGit()
            if let p = self.openPath, !self.dirty, let d = FileManager.default.contents(atPath: p), let s = String(data: d, encoding: .utf8), s != self.savedText {
                self.text = s; self.savedText = s
            }
        }
        reloadWork = w
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.3, execute: w)
    }

    private func stop() {
        if let s = stream { FSEventStreamStop(s); FSEventStreamInvalidate(s); FSEventStreamRelease(s); stream = nil }
    }
}

struct IDEView: View {
    @EnvironmentObject var model: StudioModel
    @StateObject private var ws: Workspace
    @State private var showTerminal = true
    @State private var terminalCommand: String?
    @State private var rightTab = "git"
    @State private var newFileName = ""
    @State private var newFileDir: String?
    @State private var searchResults: [(path: String, line: Int, text: String)] = []
    @State private var searchQuery = ""

    init(workspacePath: String) {
        _ws = StateObject(wrappedValue: Workspace(root: URL(fileURLWithPath: workspacePath)))
    }

    var body: some View {
        HSplitView {
            sidebar.frame(minWidth: 200, idealWidth: 240, maxWidth: 360)
            VSplitView {
                editor
                if showTerminal {
                    TerminalPane(workingDirectory: ws.root.path, pendingCommand: $terminalCommand)
                        .frame(minHeight: 120, idealHeight: 220)
                }
            }
            rightPane.frame(minWidth: 220, idealWidth: 300, maxWidth: 420)
        }
        .toolbar {
            ToolbarItemGroup {
                Button { pickFolder() } label: { Label("Open folder", systemImage: "folder") }
                Button { ws.save() } label: { Label("Save", systemImage: "square.and.arrow.down") }.disabled(!ws.dirty).keyboardShortcut("s")
                Button { showTerminal.toggle() } label: { Label("Terminal", systemImage: "terminal") }.keyboardShortcut("`")
                Button { terminalCommand = "cd \(ws.root.path.shellQuoted) && python3 -m pytest -q" } label: { Label("Run tests", systemImage: "checkmark.seal") }
                Button { terminalCommand = "cd \(ws.root.path.shellQuoted) && git status" } label: { Label("Git status", systemImage: "arrow.triangle.branch") }
            }
        }
        .sheet(isPresented: Binding(get: { newFileDir != nil }, set: { if !$0 { newFileDir = nil } })) {
            VStack(spacing: 12) {
                Text("New file in \((newFileDir ?? "") as NSString).lastPathComponent)").font(.headline)
                TextField("name.py", text: $newFileName).textFieldStyle(.roundedBorder).frame(width: 280)
                HStack { Button("Cancel") { newFileDir = nil }; Button("Create") { if let d = newFileDir, !newFileName.isEmpty { ws.newFile(in: d, name: newFileName) }; newFileDir = nil; newFileName = "" }.buttonStyle(.borderedProminent) }
            }.padding(20)
        }
    }

    private var sidebar: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Image(systemName: "folder.fill").foregroundStyle(Theme.accent)
                Text(ws.root.lastPathComponent).font(.headline).lineLimit(1)
                Spacer()
                Button { ws.reload() } label: { Image(systemName: "arrow.clockwise") }.buttonStyle(.plain)
                Button { newFileDir = ws.root.path } label: { Image(systemName: "doc.badge.plus") }.buttonStyle(.plain)
            }.padding(.horizontal, 8).padding(.top, 8)
            TextField("Filter files", text: $ws.filter).textFieldStyle(.roundedBorder).padding(.horizontal, 8)
            List(selection: Binding(get: { ws.openPath }, set: { if let p = $0 { ws.open(p) } })) {
                OutlineGroup(filteredTree, children: \.children) { node in
                    HStack(spacing: 4) {
                        Image(systemName: node.isDir ? "folder" : icon(for: node.name)).foregroundStyle(node.isDir ? .secondary : Theme.accent).font(.caption)
                        Text(node.name).font(.callout).lineLimit(1)
                        if let st = gitMark(node.id) { Spacer(); Text(st).font(.caption2.bold()).foregroundStyle(.orange) }
                    }
                    .tag(node.isDir ? "" : node.id)
                    .contextMenu {
                        if node.isDir { Button("New file here") { newFileDir = node.id } }
                        Button("Reveal in Finder") { NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: node.id)]) }
                        Button("Copy path") { NSPasteboard.general.clearContents(); NSPasteboard.general.setString(node.id, forType: .string) }
                        Button("Open in terminal") { terminalCommand = "cd \((node.isDir ? node.id : (node.id as NSString).deletingLastPathComponent).shellQuoted)" }
                    }
                }
            }
            .listStyle(.sidebar)
        }
    }

    private var filteredTree: [FileNode] {
        guard !ws.filter.isEmpty else { return ws.tree }
        func prune(_ nodes: [FileNode]) -> [FileNode] {
            nodes.compactMap { n in
                if n.isDir {
                    let kids = prune(n.children ?? [])
                    return kids.isEmpty ? nil : FileNode(id: n.id, name: n.name, isDir: true, children: kids)
                }
                return n.name.localizedCaseInsensitiveContains(ws.filter) ? n : nil
            }
        }
        return prune(ws.tree)
    }

    private func gitMark(_ path: String) -> String? {
        let rel = path.replacingOccurrences(of: ws.root.path + "/", with: "")
        return ws.gitStatus.first { $0.path == rel || $0.path.hasPrefix(rel + "/") }?.status.trimmingCharacters(in: .whitespaces)
    }

    private func icon(for name: String) -> String {
        switch (name as NSString).pathExtension {
        case "py": return "chevron.left.forwardslash.chevron.right"
        case "swift": return "swift"
        case "md": return "doc.text"
        case "json", "yaml", "yml", "toml": return "curlybraces"
        case "sh": return "terminal"
        default: return "doc"
        }
    }

    private var editor: some View {
        VStack(spacing: 0) {
            HStack(spacing: 8) {
                if let p = ws.openPath {
                    Text(p.replacingOccurrences(of: ws.root.path + "/", with: "")).font(.caption.monospaced()).lineLimit(1)
                    if ws.dirty { Circle().fill(.orange).frame(width: 7, height: 7) }
                    Pill(text: ws.language)
                } else { Text("No file open").font(.caption).foregroundStyle(.secondary) }
                Spacer()
                if let m = ws.message { Text(m).font(.caption).foregroundStyle(.secondary) }
                Text("\(ws.text.split(separator: "\n", omittingEmptySubsequences: false).count) lines").font(.caption2).foregroundStyle(.secondary)
            }
            .padding(.horizontal, 10).padding(.vertical, 5)
            .background(Theme.panel)
            if ws.openPath != nil {
                CodeEditor(text: $ws.text, language: ws.language, onSave: { ws.save() })
            } else {
                VStack(spacing: 8) {
                    Image(systemName: "chevron.left.forwardslash.chevron.right").font(.system(size: 40)).foregroundStyle(.secondary)
                    Text("Pick a file from the tree, or ask William to write one.").foregroundStyle(.secondary)
                }.frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .frame(minHeight: 200)
    }

    private var rightPane: some View {
        VStack(spacing: 0) {
            Picker("", selection: $rightTab) { Text("Git").tag("git"); Text("Search").tag("search"); Text("Ask").tag("ask") }
                .pickerStyle(.segmented).padding(8)
            switch rightTab {
            case "search": searchPane
            case "ask": askPane
            default: gitPane
            }
        }
    }

    private var gitPane: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Image(systemName: "arrow.triangle.branch"); Text(ws.gitBranch).font(.headline)
                Spacer()
                Button { ws.refreshGit() } label: { Image(systemName: "arrow.clockwise") }.buttonStyle(.plain)
            }.padding(.horizontal, 8)
            List {
                ForEach(Array(ws.gitStatus.enumerated()), id: \.offset) { _, row in
                    HStack {
                        Text(row.status).font(.caption.monospaced().bold()).foregroundStyle(row.status.contains("?") ? .green : .orange).frame(width: 22)
                        Text(row.path).font(.caption).lineLimit(1)
                    }
                    .contentShape(Rectangle())
                    .onTapGesture { ws.diff(for: row.path); ws.open(ws.root.appendingPathComponent(row.path).path) }
                }
                if ws.gitStatus.isEmpty { Text("Working tree clean").font(.caption).foregroundStyle(.secondary) }
            }
            .frame(minHeight: 120, maxHeight: 260)
            HStack {
                Button("Diff all") { ws.diff(for: nil) }
                Button("Stage all") { terminalCommand = "git add -A && git status --short" }
                Button("Commit…") { terminalCommand = "git commit" }
                Button("Push") { terminalCommand = "git push" }
            }.font(.caption).padding(.horizontal, 8)
            ScrollView {
                Text(ws.gitDiff.isEmpty ? "Select a file for its diff." : ws.gitDiff)
                    .font(.caption2.monospaced()).textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading).padding(8)
            }
        }
    }

    private var searchPane: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                TextField("Search in workspace (rg)", text: $searchQuery).textFieldStyle(.roundedBorder)
                    .onSubmit { runSearch() }
                Button("Go") { runSearch() }
            }.padding(.horizontal, 8)
            List {
                ForEach(Array(searchResults.enumerated()), id: \.offset) { _, r in
                    VStack(alignment: .leading, spacing: 1) {
                        Text("\((r.path as NSString).lastPathComponent):\(r.line)").font(.caption.bold())
                        Text(r.text.trimmingCharacters(in: .whitespaces)).font(.caption2.monospaced()).lineLimit(1).foregroundStyle(.secondary)
                    }
                    .contentShape(Rectangle())
                    .onTapGesture { ws.open(ws.root.appendingPathComponent(r.path).path) }
                }
                if searchResults.isEmpty && !searchQuery.isEmpty { Text("No results").font(.caption).foregroundStyle(.secondary) }
            }
        }
    }

    private func runSearch() {
        let q = searchQuery
        guard !q.isEmpty else { searchResults = []; return }
        let root = ws.root
        Task.detached {
            let tool = FileManager.default.isExecutableFile(atPath: "/opt/homebrew/bin/rg") ? "/opt/homebrew/bin/rg" : "rg"
            var out = Workspace.run(tool, ["-n", "--no-heading", "-m", "200", "-S", q, "."], cwd: root)
            if out.isEmpty || out.contains("No such file") {
                out = Workspace.run("grep", ["-rn", "-I", "--exclude-dir=.git", "--exclude-dir=node_modules", "--exclude-dir=.build", q, "."], cwd: root)
            }
            let rows: [(String, Int, String)] = out.split(separator: "\n").prefix(200).compactMap { line in
                let parts = line.split(separator: ":", maxSplits: 2, omittingEmptySubsequences: false)
                guard parts.count == 3, let n = Int(parts[1]) else { return nil }
                return (String(parts[0]).replacingOccurrences(of: "./", with: ""), n, String(parts[2]))
            }
            await MainActor.run { searchResults = rows.map { (path: $0.0, line: $0.1, text: $0.2) } }
        }
    }

    @State private var askText = ""
    private var askPane: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Ask William about the open file. The prompt goes through the orchestrator (rules → local → free cloud → Cursor) and lands in the Intake conversation.")
                .font(.caption).foregroundStyle(.secondary).padding(.horizontal, 8)
            TextEditor(text: $askText).font(.body).frame(minHeight: 80, maxHeight: 160).padding(.horizontal, 8)
            HStack {
                Button("Explain file") { askText = "Explain what this file does and its weak points." }
                Button("Find bugs") { askText = "Review this file for bugs and propose fixes." }
            }.font(.caption).padding(.horizontal, 8)
            Button("Send") {
                let ctx = ws.openPath.map { p in "File \(p.replacingOccurrences(of: ws.root.path + "/", with: "")):\n```\n\(String(ws.text.prefix(12000)))\n```\n\n" } ?? ""
                let q = askText; askText = ""
                Task { await model.send(ctx + q, voice: false) }
            }.buttonStyle(.borderedProminent).padding(.horizontal, 8).disabled(askText.isEmpty || model.busy)
            if let last = model.chat.last(where: { $0.role == "william" }) {
                ScrollView { Text(last.text).font(.caption).textSelection(.enabled).padding(8).frame(maxWidth: .infinity, alignment: .leading) }
            }
            Spacer()
        }
    }

    private func pickFolder() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true; panel.canChooseFiles = false; panel.allowsMultipleSelection = false
        panel.directoryURL = ws.root
        if panel.runModal() == .OK, let url = panel.url {
            ws.setRoot(url)
            model.workspacePath = url.path
        }
    }
}
