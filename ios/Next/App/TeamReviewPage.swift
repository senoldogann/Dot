import DotEngine
import SwiftUI

// The manager review of the team (item 2068) in a bot's profile, as the Mac's Agents page has it on its Orders part:
// how often this bot reviews the team (off until the owner turns it on; choosing it here makes this bot the manager),
// Review now, what the newest review found and its proposals, each a card the owner accepts or skips. Nothing changes
// until the owner accepts a card. The web's words are `teamreview.*`.

private var reviewMacPlace: String { String(localized: "Agents", comment: "Where on the Mac: the Agents page") }

/// How often the manager reviews the team, in the web's words.
func reviewEveryWords(_ every: String) -> String {
    switch every {
    case "daily": String(localized: "Daily", comment: "Manager review: once a day")
    case "weekly": String(localized: "Weekly", comment: "Manager review: once a week")
    default: String(localized: "Off", comment: "Manager review: it runs only when the owner asks")
    }
}

/// What a bot's profile row says of the review: how often this bot reviews, or which bot does.
func reviewRowWords(_ view: TeamReviewView, botId: String, names: [String: String]) -> String {
    if view.manager == botId || view.every == "off" { return reviewEveryWords(view.manager == botId ? view.every : "off") }
    return String(localized: "\(names[view.manager] ?? "") reviews", comment: "Bot profile: another bot reviews the team; the argument is its name")
}

private func titled(_ title: String, _ fallback: String) -> String { title.isEmpty ? fallback : "“\(title)”" }

/// A finding in the owner's words, as the web says it (`findingWords`).
func findingWords(_ finding: TeamFinding) -> String {
    let bot: String = finding.agentName
    switch finding.kind {
    case "stuck_order":
        let title: String = titled(finding.subject, String(localized: "a work order", comment: "Manager review: a work order whose title is not shown"))
        return String(localized: "Work order \(title) of \(bot) has not moved for \(finding.count) days", comment: "Manager review: a stuck work order")
    case "objective_behind":
        let title: String = titled(finding.subject, String(localized: "an objective", comment: "Manager review: an objective whose title is not shown"))
        return String(localized: "Objective \(title) has had no progress for \(finding.count) days", comment: "Manager review: an objective behind")
    case "overloaded":
        let reasons: String = overloadWords(BotLoad(openOrders: 0, activeRoutines: 0, objectives: 0, runs: 0, doneShare: nil, steps: nil, reasons: finding.reasons))
        return String(localized: "\(bot) is overloaded: \(reasons)", comment: "Manager review: an overloaded bot; the second argument is why")
    case "failing":
        return String(localized: "\(bot): \(finding.count) of its \(finding.limit) runs this week failed", comment: "Manager review: a bot whose runs fail")
    default:
        return String(localized: "\(bot) ran nothing for \(finding.count) days and has no work order or routine", comment: "Manager review: an idle bot")
    }
}

/// What a proposal would change, as its card's heading (`proposalHeading`).
func proposalHeading(_ proposal: TeamProposal) -> String {
    switch proposal.kind {
    case "move_orders":
        if proposal.orderIds.count == 1 {
            return String(localized: "Move 1 work order from \(proposal.agentName) to \(proposal.toName)", comment: "Manager review: a proposal to move one order")
        }
        return String(
            localized: "Move \(proposal.orderIds.count) work orders from \(proposal.agentName) to \(proposal.toName)", comment: "Manager review: a proposal to move orders")
    case "split_bot":
        let names: String = proposal.parts.map(\.name).formatted(.list(type: .and))
        return String(localized: "Split \(proposal.agentName) into \(names)", comment: "Manager review: a proposal to split a bot; the second argument lists the new bots")
    default:
        let routine: String = titled(proposal.routineName, String(localized: "a routine", comment: "Manager review: a routine whose name is not shown"))
        return String(localized: "Switch off the routine \(routine) of \(proposal.agentName)", comment: "Manager review: a proposal to switch a routine off")
    }
}

/// The lines under a proposal's heading: the orders it moves, or the bots a split makes with the routines each takes.
func proposalLines(_ proposal: TeamProposal) -> [String] {
    switch proposal.kind {
    case "move_orders":
        return proposal.orderTitles.map { titled($0, String(localized: "a work order", comment: "Manager review: a work order whose title is not shown")) }
    case "split_bot":
        return proposal.parts.map { part in
            let takes: String =
                part.routineNames.isEmpty
                ? String(localized: "no routine", comment: "Manager review: a new bot of a split takes no routine")
                : String(
                    localized: "takes \(part.routineNames.map { titled($0, String(localized: "a routine", comment: "Manager review: a routine whose name is not shown")) }.formatted(.list(type: .and)))",
                    comment: "Manager review: the routines a new bot of a split takes over")
            return "\(part.name): \(part.role) · \(takes)"
        }
    default: return []
    }
}

/// The review in a bot's profile.
struct TeamReviewPage: View {
    let botId: String
    @Environment(AppModel.self) private var model: AppModel
    @Environment(\.locale) private var locale: Locale
    @State private var state: Loadable<TeamReviewView> = .loading
    @State private var busy: Set<String> = []
    @State private var problem: SettingsFailure?

    var body: some View {
        SettingsScroll(title: String(localized: "Manager review", comment: "Bot profile: the manager's review of the team")) {
            if let problem { FailureBanner(failure: problem, macPlace: reviewMacPlace, retry: nil) }
            LoadedPage(state: state, macPlace: reviewMacPlace, retry: { Task { await load() } }) { view in
                content(view)
            }
        }
        .task { await load() }
        .refreshable { await load() }
        .task(id: running) {
            // While a review is under way, it is read again every two seconds until it ends (or the page goes).
            while running && !Task.isCancelled {
                try? await Task.sleep(for: .seconds(2))
                await load()
            }
        }
    }

    private var running: Bool {
        if case .loaded(let view) = state { return view.running }
        return false
    }

    @ViewBuilder private func content(_ view: TeamReviewView) -> some View {
        let mine: Bool = view.manager == botId
        GroupedSection(header: nil, footer: settingsFooter(view, mine: mine)) {
            MenuPickerRow(
                title: String(localized: "How often", comment: "Manager review: how often the bot reviews the team"),
                choices: ["off", "daily", "weekly"].map { MenuChoice(value: $0, title: reviewEveryWords($0)) },
                selection: Binding(get: { mine ? view.every : "off" }, set: { every in save(every) })
            )
            .accessibilityIdentifier("review.every")
            Button { reviewNow() } label: {
                ListRow(
                    systemImage: "checklist",
                    title: view.running
                        ? String(localized: "Reviewing…", comment: "Manager review: a review is under way")
                        : String(localized: "Review now", comment: "Manager review: start a review at once"),
                    description: nil, accessory: .none)
            }
            .buttonStyle(RowPressStyle())
            .disabled(view.running)
            .accessibilityIdentifier("review.now")
        }
        if !view.error.isEmpty {
            Notice(tone: .failure, message: String(localized: "The last review failed: \(view.error)", comment: "Manager review: the last try failed; the argument is why"), dismiss: nil)
        }
        if let review = view.review {
            let reviewed: String = String(
                localized: "\(review.managerName) reviewed the team \(agoWords(review.at))", comment: "Manager review: who reviewed and when; the second argument is a span such as 2 hours ago")
            if review.findings.isEmpty {
                GroupedSection(header: reviewed, footer: nil) {
                    ListRow(
                        systemImage: "checkmark.circle",
                        title: String(
                            localized: "Nothing to change: no work order is stuck, no objective is behind, and no bot is idle, overloaded or failing.",
                            comment: "Manager review: nothing was found"),
                        description: nil, accessory: .none)
                }
            } else {
                GroupedSection(header: reviewed, footer: review.summary.isEmpty ? nil : review.summary) {
                    ForEach(Array(review.findings.enumerated()), id: \.offset) { _, finding in
                        ListRow(systemImage: nil, title: findingWords(finding), description: nil, accessory: .none)
                    }
                }
                if !review.proposals.isEmpty {
                    GroupedSection(header: String(localized: "Proposals", comment: "Manager review: the changes the manager proposes"), footer: nil) {
                        ForEach(review.proposals) { proposal in card(proposal) }
                    }
                }
            }
        }
    }

    private func settingsFooter(_ view: TeamReviewView, mine: Bool) -> String {
        if !mine && view.every != "off" {
            let name: String = model.bots.first { $0.id == view.manager }?.record.name ?? ""
            return String(
                localized: "\(name) reviews the team now. Choosing how often here makes this bot the manager.", comment: "Manager review: another bot is the manager; the argument is its name")
        }
        return String(
            localized: "Off: the manager reviews the team only when you ask. It only proposes; nothing changes until you accept a card.",
            comment: "Manager review: what off means and that nothing changes without the owner")
    }

    private func card(_ proposal: TeamProposal) -> some View {
        let working: Bool = busy.contains(proposal.id)
        return VStack(alignment: .leading, spacing: Space.xs) {
            Text(proposalHeading(proposal)).textRole(.body).fontWeight(.medium).foregroundStyle(Tone.text)
            ForEach(proposalLines(proposal), id: \.self) { line in
                Text(line).textRole(.footnote).foregroundStyle(Tone.secondary)
            }
            Text(proposal.why).textRole(.footnote).foregroundStyle(Tone.secondary)
            switch proposal.status {
            case "accepted":
                Text(String(localized: "Accepted", comment: "Manager review: the owner accepted the proposal")).textRole(.footnote).fontWeight(.medium).foregroundStyle(Tone.text)
            case "skipped":
                Text(String(localized: "Skipped", comment: "Manager review: the owner skipped the proposal")).textRole(.footnote).fontWeight(.medium).foregroundStyle(Tone.tertiary)
            default:
                HStack(spacing: Space.s) {
                    Button(String(localized: "Accept", comment: "Manager review: make the proposed change")) { decide(proposal, accept: true) }
                        .buttonStyle(PrimaryButtonStyle())
                        .accessibilityIdentifier("review.accept")
                    Button(String(localized: "Skip", comment: "Manager review: leave things as they are")) { decide(proposal, accept: false) }
                        .buttonStyle(SecondaryButtonStyle())
                        .accessibilityIdentifier("review.skip")
                }
                .disabled(working)
                .padding(.top, Space.xs)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(Space.groupedGutter)
        .accessibilityIdentifier("review.proposal")
    }

    private func agoWords(_ seconds: Double) -> String {
        let relative: RelativeDateTimeFormatter = RelativeDateTimeFormatter()
        relative.locale = locale
        relative.dateTimeStyle = .named
        return relative.localizedString(for: Date(timeIntervalSince1970: seconds), relativeTo: .now)
    }

    private func load() async {
        state = await model.read { api throws(DaemonRequestError) in try await api.teamReview() }
    }

    private func apply(_ result: Result<TeamReviewView, SettingsFailureBox>) {
        switch result {
        case .success(let view):
            problem = nil
            state = .loaded(view)
        case .failure(let box): problem = box.failure
        }
    }

    private func save(_ every: String) {
        let settings: TeamReviewSettings = TeamReviewSettings(manager: botId, every: every)
        Task { apply(await model.write { api throws(DaemonRequestError) in try await api.setTeamReview(settings) }) }
    }

    private func reviewNow() {
        Task { apply(await model.write { api throws(DaemonRequestError) in try await api.reviewTeamNow() }) }
    }

    private func decide(_ proposal: TeamProposal, accept: Bool) {
        guard !busy.contains(proposal.id) else { return }
        Task {
            busy.insert(proposal.id)
            defer { busy.remove(proposal.id) }
            let id: String = proposal.id
            apply(
                await model.write { api throws(DaemonRequestError) in
                    if accept { return try await api.acceptTeamProposal(id) }
                    return try await api.skipTeamProposal(id)
                })
            // A split makes bots and moves routines: the home's list is read again.
            if accept { await model.loadAgents() }
        }
    }
}
