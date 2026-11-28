import DotEngine
import SwiftUI

// Activity's skill and plugin proposals (PLAN §26: Dot's proposals reach the owner through Activity; the Skills and
// Plugins pages stay on the Mac): a merge or rewrite of learned skills to use or drop (item 585), and a plugin the
// nightly scout found to install or skip (item 712), each read and answered in a sheet.

private var proposalsOnTheMac: String {
    String(localized: "Settings › Skills and Settings › Plugins", comment: "Where on the Mac: the skills' and plugins' proposals")
}

/// What the section read: both lists, or why one could not be read.
struct SkillPluginPicture: Sendable {
    var skills: Loadable<SkillProposalsView>
    var plugins: Loadable<ScoutProposals>

    var isEmpty: Bool { (skills.value?.items.isEmpty ?? true) && (plugins.value?.proposals.isEmpty ?? true) }
    var failure: SettingsFailure? { skills.failure ?? plugins.failure }
}

/// The section, under Self-improvement's proposals; it reads again when `wake` changes (a run or a lifecycle event heard).
struct SkillPluginProposalsSection: View {
    let wake: Int
    let say: (_ words: String, _ symbol: String) -> Void
    @Environment(AppModel.self) private var model: AppModel
    @State private var picture: SkillPluginPicture = SkillPluginPicture(skills: .loading, plugins: .loading)
    @State private var skill: SkillProposalView?
    @State private var plugin: PluginProposal?

    var body: some View {
        VStack(alignment: .leading, spacing: Space.groupedGutter) {
            if let failure = picture.failure {
                FailureBanner(failure: failure, macPlace: proposalsOnTheMac, retry: { Task { await load() } })
            }
            if !picture.isEmpty {
                GroupedSection(
                    header: String(localized: "Skills and plugins", comment: "Activity: the skill and plugin proposals"),
                    footer: String(localized: "Dot proposes; nothing changes until you choose.", comment: "Activity: what the skill and plugin proposals do")
                ) {
                    ForEach(picture.skills.value?.items ?? []) { shown in
                        ActivityItem(
                            symbol: "book", title: skillTitle(shown.proposal), line: shown.stale ?? shown.proposal.reason,
                            tone: shown.stale != nil ? .quiet : shown.proposal.tainted ? .warn : .neutral, opens: true
                        ) { skill = shown }
                        .accessibilityIdentifier("proposal.skill")
                    }
                    ForEach(picture.plugins.value?.proposals ?? [], id: \.key) { shown in
                        ActivityItem(
                            symbol: "puzzlepiece.extension", title: pluginTitle(shown), line: shown.does,
                            tone: shown.risk == "medium" || shown.risk == "high" ? .warn : .neutral, opens: true
                        ) { plugin = shown }
                        .accessibilityIdentifier("proposal.plugin")
                    }
                }
            }
        }
        .task(id: wake) { await load() }
        .sheet(item: $skill) { shown in SkillProposalSheet(shown: shown, done: settled) }
        .sheet(item: $plugin) { shown in PluginProposalSheet(shown: shown, done: settled) }
    }

    private func settled(_ words: String) {
        say(words, "checkmark")
        Task { await load() }
    }

    private func load() async {
        async let skills = model.read { api throws(DaemonRequestError) in try await api.skillProposals() }
        async let plugins = model.read { api throws(DaemonRequestError) in try await api.pluginProposals() }
        let fresh: SkillPluginPicture = SkillPluginPicture(skills: await skills, plugins: await plugins)
        guard !Task.isCancelled else { return }
        picture = SkillPluginPicture(skills: picture.skills.keeping(fresh.skills), plugins: picture.plugins.keeping(fresh.plugins))
    }
}

/// "Merge Find flights + Book trains" or "Rewrite Find flights".
func skillTitle(_ proposal: SkillProposal) -> String {
    let names: String = proposal.names.joined(separator: " + ")
    return proposal.kind == "merge"
        ? String(localized: "Merge \(names)", comment: "Activity: a proposal that merges learned skills; the argument names them")
        : String(localized: "Rewrite \(names)", comment: "Activity: a proposal that rewrites a learned skill; the argument names it")
}

/// "Plugin: Browser tools" or "Update: Browser tools".
func pluginTitle(_ proposal: PluginProposal) -> String {
    proposal.kind == "update"
        ? String(localized: "Update: \(proposal.name)", comment: "Activity: the scout proposes an update of a plugin; the argument is its name")
        : String(localized: "Plugin: \(proposal.name)", comment: "Activity: the scout proposes a new plugin; the argument is its name")
}

/// How far the scout trusts a publisher, in the web's words: Anthropic's own or the maker of what its skills are about
/// (`a`), or the community (`b`).
func tierWords(_ tier: String) -> String {
    tier == "a"
        ? String(localized: "Anthropic or its maker", comment: "Plugin proposal: the publisher is Anthropic or the maker of what its skills are about")
        : String(localized: "Community", comment: "Plugin proposal: a community publisher")
}

/// A skill proposal: why, what the skill would say, and Use or Drop.
private struct SkillProposalSheet: View {
    let shown: SkillProposalView
    let done: (String) -> Void
    @Environment(AppModel.self) private var model: AppModel
    @Environment(\.dismiss) private var dismiss: DismissAction
    @State private var working: Bool = false
    @State private var problem: SettingsFailure?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Space.groupedGutter) {
                if let problem { FailureBanner(failure: problem, macPlace: proposalsOnTheMac, retry: nil) }
                Text(skillTitle(shown.proposal)).textRole(.headline).foregroundStyle(Tone.text).fixedSize(horizontal: false, vertical: true)
                Text(shown.proposal.reason).textRole(.body).foregroundStyle(Tone.secondary).fixedSize(horizontal: false, vertical: true)
                if let stale = shown.stale {
                    Notice(tone: .failure, message: String(localized: "It cannot be used now: \(stale)", comment: "Skill proposal: why it cannot be applied; the daemon's words"), dismiss: nil)
                }
                if shown.proposal.tainted {
                    Notice(
                        tone: .warn,
                        message: String(localized: "A step may come from content others can write. Read it before you use it.", comment: "Skill proposal: the draft may carry outside content"),
                        dismiss: nil)
                }
                GroupedSection(header: shown.proposal.draft.kind, footer: nil) {
                    VStack(alignment: .leading, spacing: Space.xs) {
                        Text(shown.proposal.draft.description).textRole(.body).foregroundStyle(Tone.text).fixedSize(horizontal: false, vertical: true)
                        ForEach(Array(shown.proposal.draft.steps.enumerated()), id: \.offset) { index, step in
                            HStack(alignment: .firstTextBaseline, spacing: Space.xs) {
                                Text(verbatim: "\(index + 1).").textRole(.body).foregroundStyle(Tone.secondary).tabularNumbers()
                                Text(step).textRole(.body).foregroundStyle(Tone.text).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                            }
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(Space.groupedGutter)
                }
                VStack(spacing: Space.s) {
                    if shown.stale == nil {
                        Button { Task { await answer(yes: true) } } label: {
                            Text(String(localized: "Use", comment: "Skill proposal: writes the new version")).frame(maxWidth: .infinity)
                        }
                        .buttonStyle(PrimaryButtonStyle())
                        .disabled(working)
                        .accessibilityIdentifier("proposal.use")
                    }
                    Button { Task { await answer(yes: false) } } label: {
                        Text(String(localized: "Drop", comment: "Skill proposal: drops it")).frame(maxWidth: .infinity)
                    }
                    .buttonStyle(SecondaryButtonStyle())
                    .disabled(working)
                    .accessibilityIdentifier("proposal.drop")
                }
            }
            .padding(.horizontal, Space.gutter)
            .padding(.vertical, Space.groupedGutter)
        }
        .fullSheet(title: String(localized: "Skill proposal", comment: "Activity: the sheet of a skill proposal")) { dismiss() }
    }

    private func answer(yes: Bool) async {
        working = true
        defer { working = false }
        let id: String = shown.proposal.id
        let result: Result<Void, SettingsFailureBox> = yes
            ? await model.write { api throws(DaemonRequestError) in try await api.acceptSkillProposal(id) }
            : await model.write { api throws(DaemonRequestError) in try await api.dismissSkillProposal(id) }
        switch result {
        case .success:
            done(
                yes
                    ? String(localized: "In use: the skill's new version is written.", comment: "Skill proposal: it was used")
                    : String(localized: "Dropped.", comment: "Skill proposal: it was dropped"))
            dismiss()
        case .failure(let box): problem = box.failure
        }
    }
}

/// A plugin proposal: what it does, why it fits, what it needs, its review and skills, and Install or Skip.
private struct PluginProposalSheet: View {
    let shown: PluginProposal
    let done: (String) -> Void
    @Environment(AppModel.self) private var model: AppModel
    @Environment(\.dismiss) private var dismiss: DismissAction
    @State private var working: Bool = false
    @State private var problem: SettingsFailure?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Space.groupedGutter) {
                if let problem { FailureBanner(failure: problem, macPlace: proposalsOnTheMac, retry: nil) }
                Text(pluginTitle(shown)).textRole(.headline).foregroundStyle(Tone.text).fixedSize(horizontal: false, vertical: true)
                Text(String(localized: "By \(shown.publisher) · \(tierWords(shown.tier))", comment: "Plugin proposal: its publisher and how far it is trusted"))
                    .textRole(.footnote).foregroundStyle(Tone.secondary)
                if shown.risk == "medium" || shown.risk == "high" {
                    Notice(
                        tone: .warn,
                        message: shown.risk == "high"
                            ? String(localized: "The review rates its risk high. Read it before you install it.", comment: "Plugin proposal: the review's risk is high")
                            : String(localized: "The review rates its risk medium. Read it before you install it.", comment: "Plugin proposal: the review's risk is medium"),
                        dismiss: nil)
                }
                GroupedSection(header: nil, footer: nil) {
                    VStack(alignment: .leading, spacing: Space.s) {
                        fact(String(localized: "What it does", comment: "Plugin proposal: what it does"), shown.does)
                        fact(String(localized: "Why it fits", comment: "Plugin proposal: why the scout proposes it"), shown.why)
                        if !shown.needs.isEmpty { fact(String(localized: "What it needs", comment: "Plugin proposal: what its skills need"), shown.needs) }
                        if !shown.skills.isEmpty { fact(String(localized: "Skills", comment: "Plugin proposal: the skills it brings"), shown.skills.joined(separator: ", ")) }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(Space.groupedGutter)
                }
                VStack(spacing: Space.s) {
                    Button { Task { await answer(install: true) } } label: {
                        Text(String(localized: "Install", comment: "Plugin proposal: installs it")).frame(maxWidth: .infinity)
                    }
                    .buttonStyle(PrimaryButtonStyle())
                    .disabled(working)
                    .accessibilityIdentifier("proposal.install")
                    Button { Task { await answer(install: false) } } label: {
                        Text(String(localized: "Skip", comment: "Plugin proposal: skips it")).frame(maxWidth: .infinity)
                    }
                    .buttonStyle(SecondaryButtonStyle())
                    .disabled(working)
                    .accessibilityIdentifier("proposal.skip")
                }
            }
            .padding(.horizontal, Space.gutter)
            .padding(.vertical, Space.groupedGutter)
        }
        .fullSheet(title: String(localized: "Plugin proposal", comment: "Activity: the sheet of a plugin proposal")) { dismiss() }
    }

    private func fact(_ label: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: Space.xxs) {
            Text(label).textRole(.footnote).foregroundStyle(Tone.secondary)
            Text(value).textRole(.body).foregroundStyle(Tone.text).fixedSize(horizontal: false, vertical: true)
        }
    }

    private func answer(install: Bool) async {
        working = true
        defer { working = false }
        let proposal: PluginProposal = shown
        let result: Result<Void, SettingsFailureBox> = install
            ? await model.write { api throws(DaemonRequestError) in try await api.installPluginProposal(proposal) }
            : await model.write { api throws(DaemonRequestError) in try await api.skipPluginProposal(proposal) }
        switch result {
        case .success:
            done(
                install
                    ? String(localized: "Installed: the tasks that start from now on are offered its skills.", comment: "Plugin proposal: it was installed")
                    : String(localized: "Skipped: the scout will not propose it again.", comment: "Plugin proposal: it was skipped"))
            dismiss()
        case .failure(let box): problem = box.failure
        }
    }
}
