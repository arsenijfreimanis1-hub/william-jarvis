import SwiftUI

struct TaskPlanView: View {
    let tasks: [TaskRow]
    let goal: GoalDetail?
    let ollamaOnline: Bool
    let workerRunning: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 8) {
                EngineBadge(title: "Willy", active: ollamaOnline)
                EngineBadge(title: "Worker", active: workerRunning)
                if let goal {
                    Text("Goal #\(goal.id) · \(goal.status)")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            if let goal {
                Text(goal.prompt)
                    .font(.headline)
                    .lineLimit(2)
                if let tree = goal.tree {
                    Text("\(tree.done ?? 0)/\(tree.total ?? 0) done · \(tree.pending ?? 0) pending")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            let rows: [TaskRow] = {
                if let goalTasks = goal?.tasks, !goalTasks.isEmpty {
                    return Array(goalTasks.prefix(8))
                }
                return Array(tasks.filter { ["queued", "running", "pending"].contains($0.status) }.prefix(8))
            }()
            if rows.isEmpty {
                Text("No active tasks")
                    .foregroundStyle(.secondary)
            } else {
                ForEach(rows) { task in
                    HStack {
                        Circle()
                            .fill(statusColor(task.status))
                            .frame(width: 8, height: 8)
                        Text(task.title)
                            .lineLimit(1)
                        Spacer()
                        Text(task.status)
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                    .padding(.vertical, 4)
                }
            }
        }
        .padding(18)
        .background(KioskTheme.surfaceRaised)
        .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 16, style: .continuous)
                .stroke(KioskTheme.border, lineWidth: 1)
        )
    }

    private func statusColor(_ status: String) -> Color {
        switch status {
        case "done": return .green
        case "failed": return .red
        case "running", "queued": return .blue
        default: return .gray
        }
    }
}

struct EngineBadge: View {
    let title: String
    let active: Bool

    var body: some View {
        Text(title)
            .font(.caption2.weight(.semibold))
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(active ? Color.green.opacity(0.2) : Color.gray.opacity(0.2))
            .foregroundStyle(active ? .green : .secondary)
            .clipShape(Capsule())
    }
}
