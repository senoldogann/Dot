import SwiftUI

/// The run inspector, a bottom sheet: the run's status, its plan, its helpers (subagents) and the schedules and loops
/// of the chat with a switch each.
struct RunInspector: View {
    let model: ChatModel
    let onModels: () -> Void
    @Environment(\.dismiss) private var dismiss: DismissAction

    var body: some View {
        NavigationStack {
            List {
                Section("Run") {
                    Group {
                    row("Status", statusWords)
                    if let run: RunRecord = model.run {
                        row("Model", run.model)
                        row("Steps", String(run.steps))
                    }
                    Button("Models…", action: onModels).font(Theme.small)
                                    }
                    .listRowBackground(Theme.surface)
                }
                if !model.state.plan.isEmpty {
                    Section("Plan") {
                        Group {
                        ForEach(Array(model.state.plan.enumerated()), id: \.offset) { _, step in
                            HStack(alignment: .top, spacing: 8) {
                                Image(systemName: icon(for: step.status)).font(.system(size: 11))
                                    .foregroundStyle(color(for: step.status)).frame(width: 14).padding(.top, 3)
                                Text(step.text).font(Theme.small).foregroundStyle(step.status == "done" ? Theme.muted : Theme.text)
                            }
                        }
                                            }
                        .listRowBackground(Theme.surface)
                    }
                }
                Section("Helpers") {
                    Group {
                    if model.state.helpers.isEmpty {
                        Text("No helpers in this chat.").font(Theme.small).foregroundStyle(Theme.faint)
                    }
                    ForEach(model.state.helpers) { helper in
                        VStack(alignment: .leading, spacing: 2) {
                            HStack {
                                Text(shortened(helper.goal, 80)).font(Theme.small).foregroundStyle(Theme.text).lineLimit(2)
                                Spacer()
                                Text(helper.status).font(Theme.meta).foregroundStyle(helper.isLive ? Theme.accent : Theme.faint)
                            }
                            if helper.isLive, let words: String = helper.words {
                                Text(words).font(Theme.meta).foregroundStyle(Theme.faint).lineLimit(1)
                            }
                        }
                    }
                                    }
                    .listRowBackground(Theme.surface)
                }
                Section("Schedules and loops") {
                    Group {
                    if model.automations.isEmpty {
                        Text("None bound to this chat.").font(Theme.small).foregroundStyle(Theme.faint)
                    }
                    ForEach(model.automations) { automation in
                        ScheduleRow(
                            schedule: automation.schedule, isLoop: automation.loop,
                            onChange: { model.setAutomation(automation.id, on: $0) })
                    }
                                    }
                    .listRowBackground(Theme.surface)
                }
            }
            .scrollContentBackground(.hidden)
            .listRowBackground(Theme.surface)
            .navigationTitle("Run")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
            .task { await model.refreshAutomations() }
        }
    }

    private var statusWords: String {
        switch model.state.line {
        case .idle: model.state.runStatus ?? "Idle"
        case .working: "Working"
        case .waitingOnYou: "Waiting on you"
        case .waitingOnSubagents(let count, _): "Waiting on \(count) subagent\(count == 1 ? "" : "s")"
        }
    }

    private func row(_ name: String, _ value: String) -> some View {
        LabeledContent {
            Text(value).font(Theme.small).foregroundStyle(Theme.muted)
        } label: {
            Text(name).font(Theme.small).foregroundStyle(Theme.text)
        }
    }

    private func icon(for status: String) -> String {
        switch status {
        case "done": "checkmark.circle.fill"
        case "in_progress": "circle.dotted"
        case "failed": "xmark.circle"
        case "skipped": "minus.circle"
        default: "circle"
        }
    }

    private func color(for status: String) -> Color {
        switch status {
        case "done": Theme.good
        case "in_progress": Theme.accent
        case "failed": Theme.bad
        default: Theme.faint
        }
    }
}

/// A schedule or loop with its on/off switch.
struct ScheduleRow: View {
    let schedule: ScheduleView
    var isLoop: Bool = false
    let onChange: (Bool) -> Void

    var body: some View {
        Toggle(isOn: Binding(get: { schedule.record.enabled }, set: { onChange($0) })) {
            VStack(alignment: .leading, spacing: 2) {
                Text(schedule.record.name.isEmpty ? shortened(schedule.record.goal, 60) : schedule.record.name)
                    .font(Theme.small).foregroundStyle(Theme.text).lineLimit(1)
                Text(detail).font(Theme.meta).foregroundStyle(Theme.faint).lineLimit(1)
            }
        }
        .tint(Theme.accent)
    }

    private var detail: String {
        var parts: [String] = []
        if isLoop { parts.append("Loop") }
        parts.append(schedule.when)
        if schedule.record.enabled, let next: String = schedule.nextLocal { parts.append("next \(next)") }
        if let agent: ScheduleAgentRef = schedule.agent { parts.append(agent.name) }
        return parts.joined(separator: " · ")
    }
}
