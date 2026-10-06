import SwiftUI

enum Theme {
    static let bg = Color(nsColor: .windowBackgroundColor)
    static let panel = Color(nsColor: .controlBackgroundColor)
    static let accent = Color(red: 0.36, green: 0.62, blue: 1.0)

    static func color(forKind kind: String) -> Color {
        switch kind {
        case "orchestrator": return .purple
        case "agent": return accent
        case "tool": return .green
        case "model": return .orange
        case "link": return .teal
        case "governor": return .pink
        default: return .gray
        }
    }

    static func color(forStatus status: String) -> Color {
        switch status {
        case "running": return .yellow
        case "done": return .green
        case "failed": return .red
        case "cancelled", "skipped": return .gray
        default: return .secondary
        }
    }

    static func color(forJournal kind: String) -> Color {
        switch kind {
        case "input": return .secondary
        case "decision": return accent
        case "problem": return .red
        case "weakness": return .orange
        case "strength": return .green
        case "fix": return .mint
        default: return .gray
        }
    }

    static func color(forDevice device: String) -> Color {
        device == "macbook" ? .teal : .purple
    }
}

struct Card<Content: View>: View {
    var title: String
    var subtitle: String? = nil
    @ViewBuilder var content: Content
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline) {
                Text(title).font(.headline)
                if let subtitle { Text(subtitle).font(.caption).foregroundStyle(.secondary) }
                Spacer()
            }
            content
        }
        .padding(12)
        .background(Theme.panel, in: RoundedRectangle(cornerRadius: 10))
    }
}

struct Pill: View {
    var text: String
    var color: Color = .secondary
    var body: some View {
        Text(text).font(.caption2.weight(.semibold)).padding(.horizontal, 6).padding(.vertical, 2)
            .background(color.opacity(0.18), in: Capsule()).foregroundStyle(color)
    }
}

extension Int {
    var compact: String {
        if self >= 1_000_000 { return String(format: "%.1fM", Double(self) / 1_000_000) }
        if self >= 1_000 { return String(format: "%.1fk", Double(self) / 1_000) }
        return String(self)
    }
}
