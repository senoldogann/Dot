import SwiftUI

/// The run inspector, a bottom sheet: the run's status, its plan, its helpers (subagents) and the schedules and loops
/// of the chat with a switch each.
struct RunInspector: View {
    /// The part of the sheet a segment of the dock's capsule asks for: it is scrolled to when the sheet opens.
    enum Focus: String { case helpers, loops }

    let model: ChatModel
    var focus: Focus?
    let onModels: () -> Void
    /// Asks for the sheet's full height (the model picker is a long list).
    var onExpand: () -> Void = {}
    @Environment(\.dismiss) private var dismiss: DismissAction

    var body: some View {
        NavigationStack {
            ScrollViewReader { proxy in
            ScrollView {
                VStack(alignment: .leading, spacing: Space.xl) {
                    statusCard
                    CardGroup(title: "Run") {
                        NavigationLink {
                            ChatModelPicker(model: model).onAppear { onExpand() }
                        } label: {
                            CardRow(symbol: "cpu", title: "Model for this chat", showsChevron: true) {
                                Text(modelLine).font(Theme.small).foregroundStyle(Theme.muted).lineLimit(1)
                            }
                        }
                        .buttonStyle(.plain)
                        .accessibilityIdentifier("chat-model")
                        CardDivider()
                        Button(action: onModels) {
                            CardRow(symbol: "slider.horizontal.3", tint: Theme.muted, title: "Default models…", showsChevron: true)
                        }
                        .buttonStyle(.plain)
                    }
                    if !model.state.plan.isEmpty { planCard }
                    CardGroup(title: "Helpers", footer: model.state.helpers.isEmpty ? nil : "Subagents Dot started for this chat.") {
                        if model.state.helpers.isEmpty {
                            EmptyRow(symbol: "person.2", words: "No helpers in this chat.")
                        }
                        ForEach(Array(model.state.helpers.enumerated()), id: \.element.id) { index, helper in
                            if index > 0 { CardDivider(inset: 48) }
                            HelperRow(helper: helper)
                        }
                    }
                    .id(Focus.helpers)
                    CardGroup(title: "Schedules and loops") {
                        if model.automations.isEmpty {
                            EmptyRow(symbol: "clock.arrow.circlepath", words: "None bound to this chat.")
                        }
                        ForEach(Array(model.automations.enumerated()), id: \.element.id) { index, automation in
                            if index > 0 { CardDivider() }
                            ScheduleRow(
                                schedule: automation.schedule, isLoop: automation.loop,
                                onChange: { model.setAutomation(automation.id, on: $0) })
                        }
                    }
                    .id(Focus.loops)
                }
                .padding(Space.l)
            }
            .dotScreenBackground(inSheet: true)
            .navigationTitle("Run")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
            .task {
                await model.refreshAutomations()
                await model.store.loadProviders()
            }
            .task(id: focus) {
                // A segment of the dock's capsule asked for a part of the sheet: it is brought to the top, once the
                // sheet is on the screen.
                guard let focus else { return }
                try? await Task.sleep(for: .milliseconds(250))
                withAnimation(.easeOut(duration: 0.25)) { proxy.scrollTo(focus, anchor: .top) }
            }
            }
        }
    }

    /// The run's standing, large: what it does, how many steps it has taken.
    private var statusCard: some View {
        HStack(spacing: Space.m) {
            Image(systemName: statusSymbol)
                .font(.system(size: 20, weight: .semibold))
                .foregroundStyle(statusTone)
                .symbolEffect(.pulse, options: .repeating, isActive: model.state.isWorking)
                .frame(width: 48, height: 48)
                .background(statusTone.opacity(0.14), in: RoundedRectangle(cornerRadius: Radius.m))
            VStack(alignment: .leading, spacing: 2) {
                Text("Status").font(Theme.meta).foregroundStyle(Theme.faint)
                Text(statusWords).font(.title3.weight(.semibold)).foregroundStyle(Theme.text)
            }
            Spacer()
            if let run: RunRecord = model.run {
                VStack(alignment: .trailing, spacing: 2) {
                    Text("Steps").font(Theme.meta).foregroundStyle(Theme.faint)
                    Text(String(run.steps)).font(.title3.weight(.semibold).monospacedDigit()).foregroundStyle(Theme.text)
                }
            }
        }
        .padding(Space.l)
        .background {
            RoundedRectangle(cornerRadius: Radius.l)
                .fill(statusTone.opacity(0.08))
                .background(Theme.surface, in: RoundedRectangle(cornerRadius: Radius.l))
        }
        .overlay(RoundedRectangle(cornerRadius: Radius.l).strokeBorder(Theme.hairline, lineWidth: 1))
        .accessibilityElement(children: .combine)
    }

    private var planCard: some View {
        let steps: [PlanStep] = model.state.plan
        let done: Int = steps.filter { $0.status == "done" }.count
        return CardGroup(title: "Plan") {
            VStack(alignment: .leading, spacing: Space.s) {
                HStack {
                    Text("\(done) of \(steps.count) done").font(Theme.meta.weight(.medium).monospacedDigit()).foregroundStyle(Theme.muted)
                    Spacer()
                }
                ProgressView(value: Double(done), total: Double(max(steps.count, 1))).tint(Theme.accent)
            }
            .padding(.horizontal, Space.l).padding(.top, Space.m).padding(.bottom, Space.s)
            ForEach(Array(steps.enumerated()), id: \.offset) { index, step in
                HStack(alignment: .top, spacing: Space.m) {
                    Image(systemName: icon(for: step.status))
                        .font(.system(size: 14))
                        .foregroundStyle(color(for: step.status))
                        .symbolEffect(.pulse, options: .repeating, isActive: step.status == "in_progress")
                        .frame(width: 20).padding(.top, 1)
                    Text(step.text).font(Theme.small)
                        .foregroundStyle(step.status == "done" ? Theme.muted : Theme.text)
                        .strikethrough(step.status == "skipped")
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .padding(.horizontal, Space.l).padding(.vertical, Space.s)
                .padding(.bottom, index == steps.count - 1 ? Space.xs : 0)
            }
        }
    }

    private var statusSymbol: String {
        switch model.state.line {
        case .working: "waveform"
        case .waitingOnYou: "hand.raised.fill"
        case .waitingOnSubagents, .helpersInBackground: "person.2.fill"
        case .idle:
            switch model.state.runStatus {
            case "failed": "exclamationmark.circle.fill"
            case "cancelled": "stop.circle.fill"
            default: "checkmark.circle.fill"
            }
        }
    }

    private var statusTone: Color {
        switch model.state.line {
        case .working, .waitingOnSubagents, .helpersInBackground: Theme.accent
        case .waitingOnYou: Theme.warn
        case .idle:
            switch model.state.runStatus {
            case "failed": Theme.bad
            case "cancelled": Theme.faint
            default: Theme.good
            }
        }
    }

    /// The model the chat's next message runs on, and its level when one is picked: "opus · High".
    private var modelLine: String {
        let words: String = modelWords(model.choices?.model, in: model.store.providers, main: model.store.models?.main)
        guard let level: String = model.choices?.effort else { return words }
        return "\(words) · \(effortWords(level))"
    }

    private var statusWords: String {
        switch model.state.line {
        case .idle: (model.state.runStatus ?? "Idle").capitalizedFirst
        case .working: "Working"
        case .waitingOnYou: "Waiting on you"
        case .waitingOnSubagents(let count, _): "Waiting on \(count) helper\(count == 1 ? "" : "s")"
        case .helpersInBackground(let count): "\(count) helper\(count == 1 ? "" : "s") in the background"
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

/// A helper in the inspector: a branch mark under the run, its goal, and how it stands; a live one says what it does.
private struct HelperRow: View {
    let helper: Helper

    var body: some View {
        HStack(alignment: .top, spacing: Space.s) {
            Image(systemName: "arrow.turn.down.right")
                .font(.system(size: 12, weight: .medium)).foregroundStyle(Theme.faint)
                .frame(width: 20).padding(.top, 3)
            VStack(alignment: .leading, spacing: 3) {
                Text(shortened(helper.goal, 80)).font(Theme.small).foregroundStyle(Theme.text).lineLimit(2)
                if helper.isLive, let words: String = helper.words {
                    Text(words).font(Theme.meta).foregroundStyle(Theme.muted).lineLimit(1)
                }
            }
            Spacer(minLength: Space.s)
            StatusPill(text: helper.status.capitalizedFirst, symbol: helper.isLive ? "waveform" : nil, tone: helper.isLive ? Theme.accent : Theme.faint, pulse: helper.isLive)
        }
        .padding(.horizontal, Space.l).padding(.vertical, Space.m - 2)
        .accessibilityElement(children: .combine)
    }
}

/// An empty line of a card, said quietly with its symbol.
struct EmptyRow: View {
    let symbol: String
    let words: String

    var body: some View {
        HStack(spacing: Space.m) {
            Image(systemName: symbol).font(.system(size: 14)).foregroundStyle(Theme.faint).frame(width: 20)
            Text(words).font(Theme.small).foregroundStyle(Theme.muted)
            Spacer()
        }
        .padding(.horizontal, Space.l).frame(minHeight: 52)
    }
}

/// A schedule or loop with its on/off switch.
struct ScheduleRow: View {
    let schedule: ScheduleView
    var isLoop: Bool = false
    let onChange: (Bool) -> Void

    var body: some View {
        Toggle(isOn: Binding(get: { schedule.record.enabled }, set: { onChange($0) })) {
            HStack(spacing: Space.m) {
                Image(systemName: isLoop ? "repeat" : "clock")
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundStyle(schedule.record.enabled ? Theme.accent : Theme.faint)
                    .frame(width: 30, height: 30)
                    .background((schedule.record.enabled ? Theme.accent : Theme.faint).opacity(0.14), in: RoundedRectangle(cornerRadius: Radius.s))
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 2) {
                    Text(schedule.record.name.isEmpty ? shortened(schedule.record.goal, 60) : schedule.record.name)
                        .font(.subheadline).foregroundStyle(Theme.text).lineLimit(1)
                    Text(detail).font(Theme.meta).foregroundStyle(Theme.faint).lineLimit(2)
                }
            }
        }
        .tint(Theme.accentFill)
        .padding(.horizontal, Space.l).padding(.vertical, Space.s)
        .frame(minHeight: 56)
        .sensoryFeedback(.selection, trigger: schedule.record.enabled)
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

/// The picker for one chat's model and level: it reads the chat's choices as they stand, and the daemon keeps what is
/// picked (it is the chat's, not the default).
struct ChatModelPicker: View {
    let model: ChatModel

    var body: some View {
        ModelPickerView(
            store: model.store, model: model.choices?.model, effort: model.choices?.effort,
            onModel: { model.choose(model: $0, among: model.store.providers) }, onEffort: { model.choose(effort: $0) })
    }
}
