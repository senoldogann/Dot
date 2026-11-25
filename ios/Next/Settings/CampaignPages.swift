import DotEngine
import SwiftUI

// Settings › Agents › Campaigns as a page of the app (item 1895), on the routes the web's `campaigns.js` uses: the
// list, a campaign's goal, state, notes and recent sessions, and Stop, Resume and Mark achieved as the daemon takes them
// (`CampaignRecord.actions`). Deleting one is the Mac's. Until the bot profile of PLAN §27 lands (#1121), the page sits
// under Settings › Agents; it moves to Dot's profile with it.

// MARK: Pieces

/// A moment as the owner reads it: "5 minutes ago", "tomorrow".
private func agoWords(_ seconds: Double, now: Date, locale: Locale) -> String {
    let relative: RelativeDateTimeFormatter = RelativeDateTimeFormatter()
    relative.locale = locale
    relative.dateTimeStyle = .named
    return relative.localizedString(for: Date(timeIntervalSince1970: seconds), relativeTo: now)
}

/// Text cut to `limit` characters as the web's `clip()` cuts it: whitespace flattened, "…" where it was cut.
private func clippedText(_ text: String, _ limit: Int) -> String {
    let flat: String = text.split(whereSeparator: \.isWhitespace).joined(separator: " ")
    return flat.count > limit ? String(flat.prefix(limit - 1)) + "…" : flat
}

/// A row of a page: a title, the lines under it (each wraps), and a trailing detail.
private struct LinesRow: View {
    let title: String
    let lines: [String]
    let detail: String?

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: Space.s) {
            VStack(alignment: .leading, spacing: Space.xxs) {
                Text(title).textRole(.body).foregroundStyle(Tone.text).multilineTextAlignment(.leading)
                ForEach(Array(lines.enumerated()), id: \.offset) { _, line in
                    Text(line).textRole(.footnote).foregroundStyle(Tone.secondary).multilineTextAlignment(.leading)
                }
            }
            Spacer(minLength: Space.s)
            if let detail { Text(detail).textRole(.footnote).foregroundStyle(Tone.secondary) }
        }
        .padding(.horizontal, Space.groupedGutter).padding(.vertical, Space.s)
        .frame(minHeight: lines.isEmpty ? Metric.row : Metric.rowWithDescription, alignment: .leading)
        .accessibilityElement(children: .combine)
    }
}

/// A campaign in the list: its title with its state on the same baseline, then the lines under it across the whole
/// width (the goal, the sessions, the last progress), and the chevron centred on the row.
private struct CampaignRow: View {
    let title: String
    let state: String
    let lines: [String]

    var body: some View {
        HStack(spacing: Space.s) {
            VStack(alignment: .leading, spacing: Space.xxs) {
                HStack(alignment: .firstTextBaseline, spacing: Space.s) {
                    Text(title).textRole(.body).foregroundStyle(Tone.text).multilineTextAlignment(.leading)
                    Spacer(minLength: Space.s)
                    Text(state).textRole(.footnote).foregroundStyle(Tone.secondary)
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

/// A button row of a section (Stop, Resume), in the ink.
private struct ActionRow: View {
    let title: String
    let systemImage: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: Space.s) {
                Symbol(name: systemImage, context: .row).frame(minWidth: Metric.iconSlot)
                Text(title).textRole(.body)
                Spacer(minLength: 0)
            }
            .foregroundStyle(Tone.text)
            .padding(.horizontal, Space.groupedGutter)
            .frame(minHeight: Metric.row)
            .contentShape(Rectangle())
        }
        .buttonStyle(RowPressStyle())
    }
}

/// What a confirmation asks: its title, its words and the button that does it.
private struct Confirmation: Identifiable {
    let id: UUID
    let title: String
    let message: String
    let button: String
    let destructive: Bool
    let act: @MainActor () async -> Void
}

private extension View {
    /// Asks `asking` before it is done, as the web's confirm dialogs do.
    func confirming(_ asking: Binding<Confirmation?>) -> some View {
        confirmationDialog(
            asking.wrappedValue?.title ?? "", isPresented: Binding(get: { asking.wrappedValue != nil }, set: { shown in if !shown { asking.wrappedValue = nil } }),
            titleVisibility: .visible, presenting: asking.wrappedValue
        ) { confirmation in
            Button(confirmation.button, role: confirmation.destructive ? .destructive : nil) { Task { await confirmation.act() } }
        } message: { confirmation in
            Text(confirmation.message)
        }
    }
}

// MARK: Campaigns

private func campaignStatusWords(_ status: String) -> String {
    switch status {
    case "active": String(localized: "Active", comment: "Campaign: Dot keeps working on it")
    case "achieved": String(localized: "Achieved", comment: "Campaign: its goal is met")
    case "stopped": String(localized: "Stopped", comment: "Campaign: stopped")
    default: status
    }
}

private func entryKindWords(_ kind: String) -> String {
    switch kind {
    case "progress": String(localized: "Progress", comment: "Campaign note: progress")
    case "setback": String(localized: "Setback", comment: "Campaign note: a setback")
    case "strategy": String(localized: "Strategy", comment: "Campaign note: a strategy")
    case "question": String(localized: "Question", comment: "Campaign note: a question")
    default: String(localized: "Note", comment: "Campaign note: a note")
    }
}

/// "2 tests fixed": a note's numbers as the web's chips.
private func numbersWords(_ numbers: [String: Double], locale: Locale) -> String {
    numbers.sorted { $0.key < $1.key }.map { "\($0.value.formatted(.number.locale(locale))) \($0.key.replacingOccurrences(of: "_", with: " "))" }.joined(separator: " · ")
}

/// The next step of an active campaign, as the list and the page say it.
private func campaignNextWords(status: String, working: String?, nextLocal: String?) -> String? {
    guard status == "active" else { return nil }
    if working != nil { return String(localized: "A session is working", comment: "Campaign: a session runs now") }
    if let nextLocal { return String(localized: "Next session \(nextLocal)", comment: "Campaign: when the next session starts; the argument is the Mac's words") }
    return String(localized: "No session planned", comment: "Campaign: no session is planned")
}

private func sessionStatusWords(_ status: String) -> String {
    switch status {
    case "queued", "running", "blocked", "ending": String(localized: "Working", comment: "Campaign session: at work")
    case "done": String(localized: "Done", comment: "Campaign session: ended done")
    case "failed": String(localized: "Failed", comment: "Campaign session: failed")
    case "cancelled": String(localized: "Stopped", comment: "Campaign: stopped")
    default: status
    }
}

private func sessionsWords(_ count: Int) -> String {
    String(localized: "\(count) sessions", comment: "Campaign: the number of sessions")
}

/// Settings › Agents › Campaigns: goals Dot keeps working on, one session at a time (`/api/campaigns`).
struct CampaignsPage: View {
    @Environment(AppModel.self) private var model: AppModel
    @Environment(\.locale) private var locale: Locale
    @State private var state: Loadable<[CampaignSummary]> = .loading

    var body: some View {
        SettingsScroll(title: String(localized: "Campaigns", comment: "Settings row: goals Dot keeps working on")) {
            LoadedPage(state: state, macPlace: String(localized: "Settings › Campaigns", comment: "Where on the Mac: the Campaigns page"), retry: { Task { await load() } }) { campaigns in
                if campaigns.isEmpty {
                    EmptyState(
                        title: String(localized: "No campaigns yet", comment: "Campaigns: empty"), systemImage: "target",
                        detail: String(localized: "Switch on Keep going when you start a task, and Dot works on it until the result is checked.", comment: "Campaigns: how one starts"),
                        actionTitle: nil, action: {})
                } else {
                    GroupedSection(header: nil, footer: String(localized: "Goals that Dot keeps working on, one session at a time, until a check confirms they are done.", comment: "Campaigns: what a campaign is")) {
                        ForEach(campaigns) { campaign in
                            NavigationLink(value: SettingsPage.campaign(campaign.id)) {
                                CampaignRow(title: campaign.record.title, state: campaignStatusWords(campaign.record.status), lines: lines(campaign))
                            }
                            .buttonStyle(RowPressStyle())
                        }
                    }
                }
            }
        }
        .task { await load() }
        .refreshable { await load() }
    }

    private func lines(_ campaign: CampaignSummary) -> [String] {
        var meta: [String] = [sessionsWords(campaign.sessions)]
        let goal: String = clippedText(campaign.record.goal, 140)
        if let next = campaignNextWords(status: campaign.record.status, working: campaign.working, nextLocal: campaign.nextLocal) { meta.append(next) }
        var lines: [String] = [goal, meta.joined(separator: " · ")].filter { !$0.isEmpty }
        if let progress = campaign.progress {
            let numbers: String = numbersWords(progress.numbers, locale: locale)
            lines.append("\(agoWords(progress.ts, now: .now, locale: locale)): \(clippedText(progress.text, 220))" + (numbers.isEmpty ? "" : " · \(numbers)"))
        } else {
            lines.append(String(localized: "No progress recorded yet", comment: "Campaign: no progress note"))
        }
        return lines
    }

    private func load() async {
        state = await model.read { api throws(DaemonRequestError) in try await api.campaigns() }
    }
}

/// A campaign: its state, goal and targets, its notes and recent sessions, and Stop, Resume and Mark achieved.
struct CampaignPage: View {
    let id: String
    @Environment(AppModel.self) private var model: AppModel
    @Environment(\.locale) private var locale: Locale
    @State private var state: Loadable<CampaignDetail> = .loading
    @State private var problem: SettingsFailure?
    @State private var asking: Confirmation?
    @State private var acting: Bool = false
    /// How many of the newest sessions the page lists; the State row counts them all.
    private let recentSessions: Int = 10

    private var macPlace: String { String(localized: "Settings › Campaigns", comment: "Where on the Mac: the Campaigns page") }

    var body: some View {
        SettingsScroll(title: state.value?.record.title ?? String(localized: "Campaign", comment: "Title of a campaign's page while it loads")) {
            if let problem { FailureBanner(failure: problem, macPlace: macPlace, retry: nil) }
            LoadedPage(state: state, macPlace: macPlace, retry: { Task { await load() } }) { campaign in content(campaign) }
        }
        .task { await load() }
        .refreshable { await load() }
        .confirming($asking)
    }

    @ViewBuilder private func content(_ campaign: CampaignDetail) -> some View {
        let record: CampaignRecord = campaign.record
        GroupedSection(header: nil, footer: nil) {
            ListRow(systemImage: "target", title: String(localized: "State", comment: "Campaign: its status"), description: nil, accessory: .detail(campaignStatusWords(record.status)))
            ListRow(systemImage: "repeat", title: String(localized: "Sessions", comment: "Campaign section: its runs"), description: campaignNextWords(status: record.status, working: campaign.working, nextLocal: campaign.nextLocal), accessory: .detail("\(campaign.sessions.count)"))
            ListRow(systemImage: "checkmark.shield", title: String(localized: "Approval mode", comment: "Inspector row: the run's approval mode"), description: nil, accessory: .detail(approvalWords(record.approvals)))
            ListRow(systemImage: "cpu", title: String(localized: "Model", comment: "Campaign: the model its sessions use"), description: nil, accessory: .detail(record.model.split(separator: ":", maxSplits: 1).last.map(String.init) ?? record.model))
            ListRow(systemImage: "sum", title: String(localized: "Usage", comment: "Settings row: the tokens of the last days"), description: nil, accessory: .detail(tokenWords(campaign.tokens, locale: locale)))
            ListRow(systemImage: "calendar", title: String(localized: "Started", comment: "Campaign: when it started"), description: nil, accessory: .detail(Date(timeIntervalSince1970: record.createdAt).formatted(date: .abbreviated, time: .shortened)))
        }
        GroupedSection(header: String(localized: "Goal", comment: "Campaign: its goal"), footer: nil) {
            Text(record.goal).textRole(.body).foregroundStyle(Tone.text).padding(Space.groupedGutter).frame(maxWidth: .infinity, alignment: .leading)
            if record.targets.isEmpty {
                LinesRow(title: String(localized: "Targets", comment: "Campaign: what counts as done"), lines: [String(localized: "Not set yet: the first session records them.", comment: "Campaign: no targets yet")], detail: nil)
            } else {
                LinesRow(title: String(localized: "Targets", comment: "Campaign: what counts as done"), lines: [record.targets], detail: nil)
                LinesRow(title: String(localized: "Verified by", comment: "Campaign: how the targets are checked"), lines: [record.verification], detail: nil)
            }
            if record.status == "achieved" && !record.evidence.isEmpty {
                LinesRow(title: String(localized: "Evidence", comment: "Campaign: what showed it achieved"), lines: [record.evidence], detail: nil)
            }
        }
        let waiting: [CampaignQuestion] = campaign.questions.filter { $0.status == "pending" || $0.status == "left" }
        if !waiting.isEmpty {
            GroupedSection(header: String(localized: "Waiting for you", comment: "Campaign section: questions that wait"), footer: String(localized: "Answer in the campaign's chat.", comment: "Campaign: where a question is answered")) {
                ForEach(waiting) { question in LinesRow(title: question.summary, lines: [agoWords(question.createdAt, now: .now, locale: locale)], detail: nil) }
            }
        }
        GroupedSection(header: String(localized: "Progress notes", comment: "Campaign section: what the sessions wrote"), footer: campaign.entries.isEmpty ? String(localized: "Empty: the sessions write what they did here.", comment: "Campaign: no notes") : nil) {
            ForEach(campaign.entries.reversed()) { entry in
                let numbers: String = numbersWords(entry.numbers, locale: locale)
                LinesRow(title: entry.text, lines: ["\(entryKindWords(entry.kind)) · \(agoWords(entry.ts, now: .now, locale: locale))" + (numbers.isEmpty ? "" : " · \(numbers)")], detail: nil)
            }
        }
        GroupedSection(header: String(localized: "Recent sessions", comment: "Campaign section: its latest runs, newest first"), footer: campaign.sessions.isEmpty ? String(localized: "None yet.", comment: "Campaign: no sessions") : nil) {
            ForEach(Array(campaign.sessions.enumerated().reversed().prefix(recentSessions)), id: \.element.id) { index, session in
                LinesRow(
                    title: String(localized: "Session \(index + 1)", comment: "Campaign: a session's name; the argument is its number"),
                    lines: [
                        "\(Date(timeIntervalSince1970: session.createdAt).formatted(date: .abbreviated, time: .shortened)) · \(tokenWords(session.inputTokens + session.outputTokens, locale: locale))",
                        session.result.map { clippedText($0, 300) },
                    ].compactMap { $0 }.filter { !$0.isEmpty },
                    detail: sessionStatusWords(session.status))
            }
        }
        GroupedSection(header: nil, footer: nil) {
            ForEach(record.actions, id: \.self) { action in actionRow(action) }
        }
        .disabled(acting)
    }

    /// The row of what the daemon takes now: Stop and Mark achieved ask first, as the web does; Resume does not.
    @ViewBuilder private func actionRow(_ action: CampaignAction) -> some View {
        switch action {
        case .stop:
            ActionRow(title: String(localized: "Stop", comment: "Campaign: no session starts until it is resumed"), systemImage: "stop.circle") {
                asking = Confirmation(
                    id: UUID(), title: String(localized: "Stop this campaign?", comment: "Campaign: confirmation of Stop"),
                    message: String(localized: "No session starts after this, and a session that is working now is stopped. You can resume it later.", comment: "Campaign: what Stop does"),
                    button: String(localized: "Stop", comment: "Campaign: no session starts until it is resumed"), destructive: true
                ) { await act(.stop) }
            }
        case .resume:
            ActionRow(title: String(localized: "Resume", comment: "Campaign: lets it go on; its next session starts within a minute"), systemImage: "play.circle") { Task { await act(.resume) } }
        case .achieved:
            ActionRow(title: String(localized: "Mark achieved", comment: "Campaign: closes it as achieved"), systemImage: "checkmark.circle") {
                asking = Confirmation(
                    id: UUID(), title: String(localized: "Mark this campaign achieved?", comment: "Campaign: confirmation of Mark achieved"),
                    message: String(localized: "Nothing checks it: your word closes the campaign, and a session that is working now is stopped.", comment: "Campaign: what Mark achieved does"),
                    button: String(localized: "Mark achieved", comment: "Campaign: closes it as achieved"), destructive: false
                ) { await act(.achieved) }
            }
        }
    }

    private func load() async {
        let id: String = id
        state = await model.read { api throws(DaemonRequestError) in try await api.campaign(id) }
        if state.value != nil { problem = nil }
    }

    private func act(_ action: CampaignAction) async {
        let id: String = id
        acting = true
        switch await model.write({ api throws(DaemonRequestError) in try await api.actOnCampaign(id, action) }) {
        case .success(let campaign):
            state = .loaded(campaign)
            problem = nil
        case .failure(let box):
            // A refused action most often means the campaign moved on meanwhile (a session achieved it: 409): read it
            // again, so the actions offered are what the daemon takes now; the failure stays shown.
            await load()
            problem = box.failure
        }
        acting = false
    }
}

// MARK: Search

/// Campaigns as Settings' search finds it: the web's own words of the page (settingstabs.js) and the same in the app's
/// language.
func campaignsIndexEntry() -> (SettingsEntry, SettingsPage) {
    (
        SettingsEntry(
            id: "campaigns", title: String(localized: "Campaigns", comment: "Settings row: goals Dot keeps working on"),
            path: String(localized: "Agents", comment: "Settings section: models, schedules, rules, memory"),
            synonyms: ["goals sessions long running keep going pause stop resume", String(localized: "goals sessions keep going stop resume", comment: "Settings search synonyms of Campaigns (space separated)")]),
        .campaigns
    )
}
