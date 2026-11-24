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
    @Environment(\.dynamicTypeSize) private var typeSize: DynamicTypeSize

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
                            CardRow(icon: .cpu, title: "Model for this chat", showsChevron: true) {
                                Text(modelLine).font(Theme.small).foregroundStyle(Theme.muted).lineLimit(1)
                            }
                        }
                        .buttonStyle(.plain)
                        .accessibilityIdentifier("chat-model")
                        CardDivider()
                        Button(action: onModels) {
                            CardRow(icon: .sliders, title: "Default models…", showsChevron: true)
                        }
                        .buttonStyle(.plain)
                    }
                    if !model.state.plan.isEmpty { planCard }
                    helpersSection
                    if !model.automations.isEmpty {
                        CardGroup(title: "Schedules and loops") {
                            ForEach(Array(model.automations.enumerated()), id: \.element.id) { index, automation in
                                if index > 0 { CardDivider(inset: 56) }
                                ScheduleRow(
                                    schedule: automation.schedule, isLoop: automation.loop,
                                    onChange: { model.setAutomation(automation.id, on: $0) })
                            }
                        }
                        .id(Focus.loops)
                    }
                    if model.state.helpers.isEmpty && model.automations.isEmpty {
                        // Two empty cards said the same thing twice; one quiet line says it once.
                        Text("No helpers and no schedules in this chat.")
                            .font(Theme.small).foregroundStyle(Theme.faint).padding(.horizontal, Space.l)
                    }
                }
                .padding(Space.l)
            }
            .dotScreenBackground(inSheet: true)
            .navigationTitle("Run")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { DoneButton { dismiss() } }
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

    /// The helpers (item 1467): the ones at work first, newest first, then the finished ones, newest first, each group with its
    /// count in its header, so that the five that work are not below sixty that ended. A chat with none says so. The capsule's
    /// helpers segment scrolls here (`Focus.helpers`), to the first group.
    @ViewBuilder private var helpersSection: some View {
        let groups: HelperOrder.Groups = HelperOrder.groups(model.state.helpers)
        if !groups.working.isEmpty {
            helperGroup(.working, groups.working, footer: nil).id(Focus.helpers)
        }
        if !groups.finished.isEmpty {
            if groups.working.isEmpty {
                helperGroup(.finished, groups.finished, footer: "Subagents Dot started for this chat.").id(Focus.helpers)
            } else {
                helperGroup(.finished, groups.finished, footer: "Subagents Dot started for this chat.")
            }
        }
    }

    private func helperGroup(_ group: HelperOrder.Group, _ helpers: [Helper], footer: String?) -> some View {
        CardGroup(title: HelperOrder.title(group, count: helpers.count), footer: footer) {
            ForEach(Array(helpers.enumerated()), id: \.element.id) { index, helper in
                if index > 0 { CardDivider(inset: 54) }
                HelperRow(helper: helper)
            }
        }
        .accessibilityIdentifier(group == .working ? "helpers-working" : "helpers-finished")
    }

    /// The run's standing, large: what it does, how many steps it has taken. A card like every other (the surface, a hairline): no
    /// tint over it and no tile under its glyph; the glyph and the words say how it stands.
    private var statusCard: some View {
        // At the accessibility sizes the three parts stand over each other: side by side the words ("5 helpers working") broke
        // in the middle of a word.
        let stacked: Bool = typeSize.isAccessibilitySize
        let layout: AnyLayout = stacked ? AnyLayout(VStackLayout(alignment: .leading, spacing: Space.m)) : AnyLayout(HStackLayout(spacing: Space.m))
        return layout {
            DotIcon(statusIcon, size: 24, pulse: model.state.isWorking, relativeTo: .title3)
                .foregroundStyle(statusTone)
                .frame(width: 28)
            VStack(alignment: .leading, spacing: 2) {
                Text("Status").font(Theme.meta).foregroundStyle(Theme.faint)
                Text(statusWords).font(.title3.weight(.semibold)).foregroundStyle(Theme.text)
            }
            if !stacked { Spacer() }
            if let run: RunRecord = model.run {
                VStack(alignment: stacked ? .leading : .trailing, spacing: 2) {
                    Text("Steps").font(Theme.meta).foregroundStyle(Theme.faint)
                    Text(String(run.steps)).font(.title3.weight(.semibold).monospacedDigit()).foregroundStyle(Theme.text)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(Space.l)
        .card()
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
                ProgressView(value: Double(done), total: Double(max(steps.count, 1))).tint(Theme.text)
            }
            .padding(.horizontal, Space.l).padding(.top, Space.m).padding(.bottom, Space.s)
            ForEach(Array(steps.enumerated()), id: \.offset) { index, step in
                HStack(alignment: .top, spacing: Space.m) {
                    planMark(step.status)
                        .foregroundStyle(color(for: step.status))
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

    private var statusIcon: IconName {
        switch model.state.line {
        case .working: .audioWaveform
        case .waitingOnYou: .hand
        case .waitingOnSubagents, .helpersInBackground: .users
        case .idle:
            switch model.state.runStatus {
            case "failed": .circleAlert
            case "cancelled": .stop
            default: .circleCheck
            }
        }
    }

    private var statusTone: Color {
        switch model.state.line {
        case .working, .waitingOnSubagents, .helpersInBackground: Theme.text
        case .waitingOnYou: Theme.warn
        case .idle:
            switch model.state.runStatus {
            case "failed": Theme.bad
            default: Theme.muted
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
        case .helpersInBackground(let count): HelperWords.header(count: count, runWorking: model.state.isWorking)
        }
    }

    /// The mark of a plan step: done, at work (dashed, breathing), failed, skipped, or a plain ring for one not begun.
    @ViewBuilder private func planMark(_ status: String) -> some View {
        switch status {
        case "done": DotIcon(.circleCheck, size: 16, relativeTo: .footnote)
        case "in_progress": DotIcon(.circleDashed, size: 16, pulse: true, relativeTo: .footnote)
        case "failed": DotIcon(.circleX, size: 16, relativeTo: .footnote)
        case "skipped": DotIcon(.circleMinus, size: 16, relativeTo: .footnote)
        default: Circle().strokeBorder(lineWidth: 1.5).frame(width: 13, height: 13)
        }
    }

    private func color(for status: String) -> Color {
        switch status {
        case "done": Theme.muted
        case "in_progress": Theme.text
        case "failed": Theme.bad
        default: Theme.faint
        }
    }
}

/// A helper in the inspector: a helper is a bot Dot handed a part of the work to, so it wears the bot (its ring turns while it
/// works), then its goal and how it stands; a live one says what it does.
private struct HelperRow: View {
    let helper: Helper

    var body: some View {
        HStack(alignment: .top, spacing: Space.s) {
            AgentAvatar(face: .bot, size: 26, state: helper.isLive ? .working : .idle)
                .frame(width: 30).padding(.top, 1)
            VStack(alignment: .leading, spacing: 3) {
                Text(shortened(helper.goal, 80)).font(Theme.small).foregroundStyle(Theme.text).lineLimit(2)
                if helper.isLive, let words: String = helper.words {
                    Text(words).font(Theme.meta).foregroundStyle(Theme.muted).lineLimit(1)
                }
            }
            Spacer(minLength: Space.s)
            StateMark(words: HelperOrder.badge(for: helper), kind: StateMark.Kind(helperStatus: helper.status))
        }
        .padding(.horizontal, Space.l).padding(.vertical, Space.m - 2)
        .accessibilityElement(children: .combine)
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
                DotIcon(isLoop ? .loop : .clock, size: 22, relativeTo: .title3)
                    .foregroundStyle(schedule.record.enabled ? Theme.muted : Theme.faint)
                    .frame(width: 28)
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 2) {
                    Text(schedule.record.name.isEmpty ? shortened(schedule.record.goal, 60) : schedule.record.name)
                        .font(.body).foregroundStyle(Theme.text).lineLimit(1)
                    Text(detail).font(Theme.meta).foregroundStyle(Theme.faint).lineLimit(2)
                }
            }
        }
        .tint(Theme.toggleOn)
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

extension StateMark.Kind {
    /// How a helper's run status reads as a mark: at work (neutral, moving), waiting on the owner (amber), failed (muted red),
    /// done (muted), anything else (words only).
    init(helperStatus status: String) {
        switch status {
        case "running", "queued", "ending": self = .working
        case "blocked": self = .waiting
        case "failed": self = .failed
        case "done": self = .done
        default: self = .quiet
        }
    }
}
