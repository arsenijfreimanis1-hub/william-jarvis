import AppKit
import SwiftTerm
import SwiftUI

/// Real terminal (zsh) embedded via SwiftTerm.
struct TerminalPane: NSViewRepresentable {
    var workingDirectory: String
    @Binding var pendingCommand: String?

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeNSView(context: Context) -> LocalProcessTerminalView {
        let tv = LocalProcessTerminalView(frame: .zero)
        tv.processDelegate = context.coordinator
        tv.font = NSFont.monospacedSystemFont(ofSize: 12, weight: .regular)
        tv.nativeBackgroundColor = NSColor(calibratedWhite: 0.08, alpha: 1)
        tv.nativeForegroundColor = NSColor(calibratedWhite: 0.92, alpha: 1)
        var env = ProcessInfo.processInfo.environment
        env["TERM"] = "xterm-256color"
        env["COLORTERM"] = "truecolor"
        env["LANG"] = env["LANG"] ?? "en_US.UTF-8"
        env["WILLIAM_STUDIO"] = "1"
        let envArray = env.map { "\($0.key)=\($0.value)" }
        let shell = env["SHELL"] ?? "/bin/zsh"
        tv.startProcess(executable: shell, args: ["-l"], environment: envArray, execName: "-" + (shell as NSString).lastPathComponent, currentDirectory: workingDirectory)
        context.coordinator.view = tv
        return tv
    }

    func updateNSView(_ tv: LocalProcessTerminalView, context: Context) {
        if let cmd = pendingCommand {
            tv.send(txt: cmd + "\n")
            DispatchQueue.main.async { pendingCommand = nil }
        }
    }

    final class Coordinator: NSObject, LocalProcessTerminalViewDelegate {
        weak var view: LocalProcessTerminalView?
        func sizeChanged(source: LocalProcessTerminalView, newCols: Int, newRows: Int) {}
        func setTerminalTitle(source: LocalProcessTerminalView, title: String) {}
        func hostCurrentDirectoryUpdate(source: TerminalView, directory: String?) {}
        func processTerminated(source: TerminalView, exitCode: Int32?) {
            source.feed(text: "\r\n[process exited \(exitCode ?? 0)] — reopen the IDE tab to restart the shell\r\n")
        }
    }
}
