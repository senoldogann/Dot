import DotEngine
import SwiftUI

// What a bot is given to do and what it passes on (items 87, 907, 908), as the Mac's Agents page lists it, in a bot's profile:
// Work orders, Objectives and Hand-offs, each to read. The web writes none of them (the lead, Dot, hands work out and
// judges it in its chats, and objectives are kept by Dot), so the phone reads them too; the one write of this group is the
// profile's Pause and Resume (`POST /api/agents/{id}/pause|resume`).

private var workMacPlace: String { String(localized: "Agents", comment: "Where on the Mac: the Agents page") }

// MARK: Reading

extension Loadable {
    /// The same read with its value turned into another; a read that is still on its way or failed stays so.
    func map<Other: Sendable>(_ change: (Value) -> Other) -> Loadable<Other> {
        switch self {
        case .loading: .loading
        case .failed(let failure): .failed(failure)
        case .loaded(let value): .loaded(change(value))
        }
    }

    /// Two reads as one: both there, or the first that failed, or still on its way.
    func and<Other: Sendable>(_ other: Loadable<Other>) -> Loadable<(Value, Other)> {
        switch (self, other) {
        case (.loaded(let first), .loaded(let second)): .loaded((first, second))
        case (.failed(let failure), _), (_, .failed(let failure)): .failed(failure)
        default: .loading
        }
    }
}

/// The three routes a bot's work comes from, each read on its own, so that a page shows what its own route answered.
@MainActor func readOrders(_ model: AppModel) async -> Loadable<[WorkOrderView]> {
    await model.read { api throws(DaemonRequestError) in try await api.workOrders() }
}

@MainActor func readObjectives(_ model: AppModel) async -> Loadable<[ObjectiveRecord]> {
    await model.read { api throws(DaemonRequestError) in try await api.objectives() }
}

@MainActor func readTeam(_ model: AppModel) async -> Loadable<TeamView> {
    await model.read { api throws(DaemonRequestError) in try await api.team() }
}

/// The objectives of a bot, read: an agent's are those its orders serve, so its orders are read with them.
@MainActor func readBotObjectives(_ model: AppModel, botId: String) async -> Loadable<[ObjectiveRecord]> {
    async let orders: Loadable<[WorkOrderView]> = readOrders(model)
    async let objectives: Loadable<[ObjectiveRecord]> = readObjectives(model)
    return await orders.and(objectives).map { botObjectives(botId: botId, orders: $0.0, objectives: $0.1) }
}

// MARK: Words

/// Where an order stands, as the web's Agents page says it.
func workStateWords(_ order: WorkOrderRecord) -> String {
    switch order.state {
    case .assigned: String(localized: "Handed out", comment: "Work order: recorded, its attempt not started")
    case .working: String(localized: "Working", comment: "Bot profile: the bot has a run at work")
    case .inReview: String(localized: "Waiting for Dot's check", comment: "Work order: the attempt ended and the lead has not judged it")
    case .returned: String(localized: "Sent back", comment: "Work order: the lead sent it back for another attempt")
    case .accepted: String(localized: "Accepted", comment: "Work order: the lead accepted the result")
    case .escalated: String(localized: "Waiting for you", comment: "Work order: it waits for the owner's decision")
    case .cancelled: String(localized: "Cancelled", comment: "Work order: it was cancelled")
    case nil: order.status
    }
}

private func spanText(_ span: SpanWords) -> String {
    switch span {
    case .minutes(let count): String(localized: "\(count) min", comment: "A short span of time: minutes")
    case .hours(let count): String(localized: "\(count) h", comment: "A span of time: hours")
    case .days(let count): String(localized: "\(count) days", comment: "A span of time: days")
    }
}

/// "due in 3 h" or "2 h late".
func dueText(_ due: DueWords) -> String {
    switch due {
    case .dueIn(let span): String(localized: "due in \(spanText(span))", comment: "Work order: when it is due; the argument is a span such as 3 h")
    case .late(let span): String(localized: "\(spanText(span)) late", comment: "Work order: how late it is; the argument is a span such as 2 h")
    }
}

private func objectiveStateWords(_ objective: ObjectiveRecord) -> String {
    switch objective.status {
    case "active": String(localized: "Active", comment: "Campaign: Dot keeps working on it")
    case "done": String(localized: "Done", comment: "Campaign session: ended done")
    case "dropped": String(localized: "Let go", comment: "Objective: the owner and Dot let it go")
    default: objective.status
    }
}

/// Text cut to `limit` characters as the web's `clip()` cuts it: whitespace flattened, "…" where it was cut.
private func flatClip(_ text: String, _ limit: Int) -> String {
    let flat: String = text.split(whereSeparator: \.isWhitespace).joined(separator: " ")
    return flat.count > limit ? String(flat.prefix(limit - 1)) + "…" : flat
}

private func agoText(_ seconds: Double, locale: Locale) -> String {
    let relative: RelativeDateTimeFormatter = RelativeDateTimeFormatter()
    relative.locale = locale
    relative.dateTimeStyle = .named
    return relative.localizedString(for: Date(timeIntervalSince1970: seconds), relativeTo: .now)
}

private func timeText(_ seconds: Double, locale: Locale) -> String {
    Date(timeIntervalSince1970: seconds).formatted(.dateTime.hour().minute().locale(locale))
}

private func dayText(_ seconds: Double, locale: Locale) -> String {
    Date(timeIntervalSince1970: seconds).formatted(.dateTime.day().month().year().locale(locale))
}

/// Words Dot wrote after it read content third parties can author: not the owner's, whatever they look like.
private var outsideWords: String {
    String(localized: "Written after Dot read content from outside", comment: "Work order and objective: written by a run that read pages or mail others can write")
}

/// A block of words in a card, as the profile shows a bot's instructions.
private struct WorkTextCard: View {
    let text: String

    var body: some View {
        Text(text).textRole(.body).foregroundStyle(Tone.text).multilineTextAlignment(.leading).textSelection(.enabled)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(Space.groupedGutter)
    }
}

// MARK: Work orders

/// A bot's work orders: the open ones first, then those accepted or cancelled; one opens to its brief and what done means.
struct WorkOrdersPage: View {
    let botId: String
    @Environment(AppModel.self) private var model: AppModel
    @Environment(\.locale) private var locale: Locale
    @State private var state: Loadable<[WorkOrderView]> = .loading

    var body: some View {
        SettingsScroll(title: String(localized: "Work orders", comment: "Bot profile: the work the lead handed to the bot")) {
            LoadedPage(state: state, macPlace: workMacPlace, retry: { Task { await load() } }) { orders in
                if orders.isEmpty {
                    EmptyState(
                        title: String(localized: "No work orders yet", comment: "Work orders: empty"), systemImage: "list.bullet.rectangle",
                        detail: String(localized: "When Dot hands a bot a piece of work, it shows here with where it stands.", comment: "Work orders: how one comes to be"),
                        actionTitle: nil, action: {})
                } else {
                    let open: [WorkOrderView] = orders.filter { $0.order.isOpen }
                    let over: [WorkOrderView] = orders.filter { !$0.order.isOpen }
                    if !open.isEmpty {
                        GroupedSection(header: String(localized: "Open", comment: "Work orders: the section of those not over"), footer: over.isEmpty ? footer : nil) {
                            ForEach(open) { view in row(view) }
                        }
                    }
                    if !over.isEmpty {
                        GroupedSection(header: String(localized: "Finished", comment: "Work orders: the section of those accepted or cancelled"), footer: footer) {
                            ForEach(over) { view in row(view) }
                        }
                    }
                }
            }
        }
        .task { await load() }
        .refreshable { await load() }
    }

    private var footer: String {
        String(localized: "Dot hands work out and judges it in its chats; orders are read here.", comment: "Work orders: where they are made and judged")
    }

    private func row(_ view: WorkOrderView) -> some View {
        let order: WorkOrderRecord = view.order
        var meta: [String] = []
        if botId == dotAgentId { meta.append(view.agentName.isEmpty ? String(localized: "A bot that is gone", comment: "Work order: its bot was deleted") : view.agentName) }
        if order.attempts > 0 { meta.append(String(localized: "\(order.attempts) tries", comment: "Work order: how many attempts it has had")) }
        meta.append(String(localized: "handed out \(agoText(order.createdAt, locale: locale))", comment: "Work order: when it was handed out; the argument is a span such as 2 hours ago"))
        if let due = order.due(now: Date.now.timeIntervalSince1970) { meta.append(dueText(due)) }
        var lines: [String] = [meta.joined(separator: " · "), order.note.isEmpty ? "" : flatClip(order.note, 200)].filter { !$0.isEmpty }
        if let objective = view.objectiveTitle { lines.append(String(localized: "For: \(flatClip(objective, 120))", comment: "Work order: the objective it serves")) }
        if order.tainted { lines.append(outsideWords) }
        return NavigationLink {
            WorkOrderPage(view: view)
        } label: {
            StateRow(title: flatClip(order.title, 140), state: workStateWords(order), lines: lines)
        }
        .buttonStyle(RowPressStyle())
        .accessibilityIdentifier("work.order")
    }

    private func load() async {
        state = await readOrders(model).map { botOrders(botId: botId, orders: $0) }
    }
}

/// A work order: its state, who has it, its brief and what done means, and the last thing said of it.
struct WorkOrderPage: View {
    let view: WorkOrderView
    @Environment(\.locale) private var locale: Locale

    var body: some View {
        let order: WorkOrderRecord = view.order
        SettingsScroll(title: String(localized: "Work order", comment: "Title of one work order's page")) {
            if order.tainted { Notice(tone: .info, message: outsideWords, dismiss: nil) }
            GroupedSection(header: nil, footer: nil) {
                ListRow(systemImage: nil, title: String(localized: "State", comment: "Campaign: its status"), description: nil, accessory: .detail(workStateWords(order)))
                if !view.agentName.isEmpty {
                    ListRow(systemImage: nil, title: String(localized: "Given to", comment: "Work order: the bot that has it"), description: nil, accessory: .detail(view.agentName))
                }
                if order.attempts > 0 {
                    ListRow(systemImage: nil, title: String(localized: "Attempts", comment: "Work order: how many tries it has had"), description: nil, accessory: .detail("\(order.attempts)"))
                }
                ListRow(systemImage: nil, title: String(localized: "Handed out", comment: "Work order: recorded, its attempt not started"), description: nil, accessory: .detail(agoText(order.createdAt, locale: locale)))
                if let due = order.due(now: Date.now.timeIntervalSince1970) {
                    ListRow(systemImage: nil, title: String(localized: "Due", comment: "Work order: its time limit"), description: nil, accessory: .detail(dueText(due)))
                }
            }
            GroupedSection(header: String(localized: "Title", comment: "Work order: what it is"), footer: nil) { WorkTextCard(text: order.title) }
            if !order.brief.isEmpty {
                GroupedSection(header: String(localized: "Brief", comment: "Work order: what is asked"), footer: nil) { WorkTextCard(text: order.brief) }
            }
            if !order.acceptance.isEmpty {
                GroupedSection(header: String(localized: "Done means", comment: "Work order: what counts as done"), footer: nil) { WorkTextCard(text: order.acceptance) }
            }
            if !order.note.isEmpty {
                GroupedSection(header: String(localized: "Last note", comment: "Work order: the last thing said of it"), footer: nil) {
                    WorkTextCard(text: order.note)
                }
            }
            if let objective = view.objectiveTitle {
                GroupedSection(header: String(localized: "For the objective", comment: "Work order: the objective it serves"), footer: nil) { WorkTextCard(text: objective) }
            }
        }
    }
}

// MARK: Objectives

/// The objectives a bot's work serves (Dot's own page lists every one): the active ones first, then those done or let go.
struct ObjectivesPage: View {
    let botId: String
    @Environment(AppModel.self) private var model: AppModel
    @Environment(\.locale) private var locale: Locale
    @State private var state: Loadable<[ObjectiveRecord]> = .loading

    var body: some View {
        SettingsScroll(title: String(localized: "Objectives", comment: "Bot profile: what the owner wants moved forward")) {
            LoadedPage(state: state, macPlace: workMacPlace, retry: { Task { await load() } }) { objectives in
                if objectives.isEmpty {
                    EmptyState(
                        title: String(localized: "No objectives yet", comment: "Objectives: empty"), systemImage: "target",
                        detail: botId == dotAgentId
                            ? String(localized: "Tell Dot what you want moved forward over time and it keeps an objective for it.", comment: "Objectives: how Dot's come to be")
                            : String(localized: "The objectives of the work Dot hands this bot show here.", comment: "Objectives: what an agent's page lists"),
                        actionTitle: nil, action: {})
                } else {
                    let active: [ObjectiveRecord] = objectives.filter(\.isActive)
                    let over: [ObjectiveRecord] = objectives.filter { !$0.isActive }
                    if !active.isEmpty {
                        GroupedSection(header: String(localized: "Active", comment: "Campaign: Dot keeps working on it"), footer: over.isEmpty ? footer : nil) {
                            ForEach(active) { objective in row(objective) }
                        }
                    }
                    if !over.isEmpty {
                        GroupedSection(header: String(localized: "Finished", comment: "Work orders: the section of those accepted or cancelled"), footer: footer) {
                            ForEach(over) { objective in row(objective) }
                        }
                    }
                }
            }
        }
        .task { await load() }
        .refreshable { await load() }
    }

    private var footer: String {
        String(localized: "Dot keeps these for you and writes their progress as it works; they are read here.", comment: "Objectives: who keeps them")
    }

    private func row(_ objective: ObjectiveRecord) -> some View {
        var lines: [String] = [flatClip(objective.why, 200)].filter { !$0.isEmpty }
        if !objective.measure.isEmpty { lines.append(String(localized: "Judged by: \(flatClip(objective.measure, 200))", comment: "Objective: how progress is judged; the argument is the measure")) }
        if let newest = objective.notes.last {
            lines.append("\(dayText(newest.at, locale: locale)): \(flatClip(newest.text, 200))")
        } else if !objective.workflow.nextStep.isEmpty && objective.isActive {
            lines.append(String(localized: "Next: \(flatClip(objective.workflow.nextStep, 200))", comment: "Objective: the step that comes next"))
        }
        if objective.hasOutsideWords { lines.append(outsideWords) }
        return NavigationLink {
            ObjectivePage(objective: objective)
        } label: {
            StateRow(title: flatClip(objective.title, 140), state: objectiveStateWords(objective), lines: lines)
        }
        .buttonStyle(RowPressStyle())
        .accessibilityIdentifier("work.objective")
    }

    private func load() async {
        state = await readBotObjectives(model, botId: botId)
    }
}

/// An objective: why it matters, how progress is judged, the step that comes next and its dated lines of progress.
struct ObjectivePage: View {
    let objective: ObjectiveRecord
    @Environment(\.locale) private var locale: Locale

    var body: some View {
        SettingsScroll(title: String(localized: "Objective", comment: "Title of one objective's page")) {
            if objective.hasOutsideWords { Notice(tone: .info, message: outsideWords, dismiss: nil) }
            GroupedSection(header: nil, footer: nil) {
                ListRow(systemImage: nil, title: String(localized: "State", comment: "Campaign: its status"), description: nil, accessory: .detail(objectiveStateWords(objective)))
                ListRow(systemImage: nil, title: String(localized: "Started", comment: "Campaign: when it started"), description: nil, accessory: .detail(dayText(objective.createdAt, locale: locale)))
            }
            GroupedSection(header: String(localized: "Title", comment: "Work order: what it is"), footer: nil) { WorkTextCard(text: objective.title) }
            if !objective.why.isEmpty {
                GroupedSection(header: String(localized: "Why it matters", comment: "Objective: why the owner wants it"), footer: nil) { WorkTextCard(text: objective.why) }
            }
            if !objective.measure.isEmpty {
                GroupedSection(header: String(localized: "Judged by", comment: "Objective: how progress is judged"), footer: nil) { WorkTextCard(text: objective.measure) }
            }
            if !objective.workflow.nextStep.isEmpty {
                GroupedSection(header: String(localized: "Next step", comment: "Objective: the step that comes next"), footer: nil) { WorkTextCard(text: objective.workflow.nextStep) }
            }
            if !objective.notes.isEmpty {
                GroupedSection(header: String(localized: "Progress", comment: "Objective: its dated lines of progress"), footer: nil) {
                    ForEach(Array(objective.notes.reversed().enumerated()), id: \.offset) { _, note in
                        LinesRow(title: note.text, lines: [dayText(note.at, locale: locale)], detail: nil)
                    }
                }
            }
        }
    }
}

// MARK: Hand-offs

/// What stands where a reply would: that the bot asked is at work on it, as the web's Handoffs page says, or that it ended with none.
private func noReplyWords(_ message: TeamMessage) -> String {
    message.isAtWork
        ? String(localized: "\(message.toName) is working on it", comment: "Hand-off: the bot asked has not answered yet; the argument is its name")
        : String(localized: "No reply", comment: "Hand-off: it ended without a reply")
}

/// A hand-off in a list, as the web's Handoffs page draws it: the two faces, "From → To", the time on the title's baseline
/// (under it at the accessibility sizes), the message and its reply, and the chevron centred on the row.
private struct HandoffRow: View {
    let from: (id: String, look: AgentLook?)
    let to: (id: String, look: AgentLook?)
    let title: String
    let time: String
    let lines: [String]
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize: DynamicTypeSize

    var body: some View {
        HStack(spacing: Space.s) {
            HStack(spacing: -Space.xs) {
                BotFace(id: from.id, look: from.look, size: 28)
                BotFace(id: to.id, look: to.look, size: 28)
            }
            .frame(minWidth: Metric.iconSlot)
            VStack(alignment: .leading, spacing: Space.xxs) {
                if dynamicTypeSize.isAccessibilitySize {
                    Text(title).textRole(.body).foregroundStyle(Tone.text).multilineTextAlignment(.leading)
                    Text(time).textRole(.footnote).foregroundStyle(Tone.secondary)
                } else {
                    HStack(alignment: .firstTextBaseline, spacing: Space.s) {
                        Text(title).textRole(.body).foregroundStyle(Tone.text).multilineTextAlignment(.leading)
                        Spacer(minLength: Space.s)
                        Text(time).textRole(.footnote).foregroundStyle(Tone.secondary)
                    }
                }
                ForEach(Array(lines.enumerated()), id: \.offset) { _, line in
                    Text(line).textRole(.footnote).foregroundStyle(Tone.secondary).multilineTextAlignment(.leading)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            Symbol(name: "chevron.right", context: .chevron).foregroundStyle(Tone.tertiary)
        }
        .padding(.horizontal, Space.groupedGutter).padding(.vertical, Space.s)
        .frame(minHeight: Metric.rowWithDescription, alignment: .leading)
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
    }
}

/// Today's messages between the bot and the others: those it received, then those it sent; one opens to the message and its reply.
struct HandoffsPage: View {
    let botId: String
    @Environment(AppModel.self) private var model: AppModel
    @Environment(\.locale) private var locale: Locale
    @State private var state: Loadable<BotHandoffs> = .loading

    var body: some View {
        SettingsScroll(title: String(localized: "Hand-offs", comment: "Bot profile: messages between the bot and the other bots")) {
            LoadedPage(state: state, macPlace: workMacPlace, retry: { Task { await load() } }) { handoffs in
                if handoffs.count == 0 {
                    EmptyState(
                        title: String(localized: "No hand-offs today", comment: "Hand-offs: empty"), systemImage: "arrow.left.arrow.right",
                        detail: String(localized: "When this bot and another one message each other, it shows here for the day.", comment: "Hand-offs: what the page lists"),
                        actionTitle: nil, action: {})
                } else {
                    if !handoffs.received.isEmpty {
                        GroupedSection(header: String(localized: "Received", comment: "Hand-offs: messages sent to the bot"), footer: handoffs.sent.isEmpty ? footer : nil) {
                            ForEach(handoffs.received) { message in row(message) }
                        }
                    }
                    if !handoffs.sent.isEmpty {
                        GroupedSection(header: String(localized: "Sent to others", comment: "Hand-offs: messages the bot sent to other bots"), footer: footer) {
                            ForEach(handoffs.sent) { message in row(message) }
                        }
                    }
                }
            }
        }
        .task { await load() }
        .refreshable { await load() }
    }

    private var footer: String {
        String(localized: "Today's messages between bots, newest first.", comment: "Hand-offs: what the list holds")
    }

    private func look(_ id: String) -> AgentLook? { model.bots.first { $0.id == id }?.record.look }

    private func row(_ message: TeamMessage) -> some View {
        var lines: [String] = [flatClip(message.message, 200)]
        if let reply = message.reply, !reply.isEmpty {
            lines.append(String(localized: "Reply: \(flatClip(reply, 200))", comment: "Hand-off: the answer of the bot that was asked"))
        } else {
            lines.append(noReplyWords(message))
        }
        return NavigationLink {
            HandoffPage(message: message)
        } label: {
            HandoffRow(
                from: (message.fromId, look(message.fromId)), to: (message.toId, look(message.toId)),
                title: "\(message.fromName) → \(message.toName)", time: timeText(message.ts, locale: locale), lines: lines)
        }
        .buttonStyle(RowPressStyle())
        .accessibilityIdentifier("work.handoff")
    }

    private func load() async {
        state = await readTeam(model).map { botHandoffs(botId: botId, team: $0) }
    }
}

/// A hand-off: who sent it and to whom, when, the message and the reply.
struct HandoffPage: View {
    let message: TeamMessage
    @Environment(\.locale) private var locale: Locale

    var body: some View {
        SettingsScroll(title: String(localized: "Hand-off", comment: "Title of one hand-off's page")) {
            GroupedSection(header: nil, footer: nil) {
                ListRow(systemImage: nil, title: String(localized: "From", comment: "Hand-off: who sent it"), description: nil, accessory: .detail(message.fromName))
                ListRow(systemImage: nil, title: String(localized: "To", comment: "Hand-off: who it was sent to"), description: nil, accessory: .detail(message.toName))
                ListRow(systemImage: nil, title: String(localized: "Sent at", comment: "Hand-off: when it was sent"), description: nil, accessory: .detail(timeText(message.ts, locale: locale)))
            }
            GroupedSection(header: String(localized: "Message", comment: "Hand-off: what was asked"), footer: nil) { WorkTextCard(text: message.message) }
            GroupedSection(header: String(localized: "Reply", comment: "Hand-off: the answer"), footer: nil) {
                if let reply = message.reply, !reply.isEmpty {
                    WorkTextCard(text: reply)
                } else {
                    WorkTextCard(text: noReplyWords(message))
                }
            }
        }
    }
}

// MARK: Profile rows

/// The profile's Work group: Work orders, Objectives and Hand-offs, each with how many wait on something. The three reads are
/// the profile's, each its own, so that a route that fails leaves the counts of the others.
struct BotWorkSection: View {
    let botId: String
    let orders: Loadable<[WorkOrderView]>
    let objectives: Loadable<[ObjectiveRecord]>
    let handoffs: Loadable<BotHandoffs>

    var body: some View {
        GroupedSection(header: String(localized: "Work", comment: "Bot profile: the section of what the bot is given and passes on"), footer: nil) {
            NavigationRow(
                systemImage: "list.bullet.rectangle", title: String(localized: "Work orders", comment: "Bot profile: the work the lead handed to the bot"), description: nil,
                detail: orders.value.map { all in
                    let open: Int = all.filter { $0.order.isOpen }.count
                    return open > 0 ? String(localized: "\(open) open", comment: "Bot profile: how many work orders are not over") : none
                }
            ) { WorkOrdersPage(botId: botId) }
            .accessibilityIdentifier("profile.workOrders")
            NavigationRow(
                systemImage: "target", title: String(localized: "Objectives", comment: "Bot profile: what the owner wants moved forward"), description: nil,
                detail: objectives.value.map { all in
                    let active: Int = all.filter(\.isActive).count
                    return active > 0 ? String(localized: "\(active) active", comment: "Bot profile: how many objectives are active") : none
                }
            ) { ObjectivesPage(botId: botId) }
            .accessibilityIdentifier("profile.objectives")
            NavigationRow(
                systemImage: "arrow.left.arrow.right", title: String(localized: "Hand-offs", comment: "Bot profile: messages between the bot and the other bots"), description: nil,
                detail: handoffs.value.map { $0.count > 0 ? String(localized: "\($0.count) today", comment: "Bot profile: how many messages between bots there were today") : none }
            ) { HandoffsPage(botId: botId) }
            .accessibilityIdentifier("profile.handoffs")
        }
    }

    private var none: String { String(localized: "None", comment: "Bot profile: nothing in a group of its work") }
}
