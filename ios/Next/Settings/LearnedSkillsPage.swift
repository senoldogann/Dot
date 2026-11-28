import DotEngine
import SwiftUI

private var skillsTitle: String { String(localized: "Skills", comment: "Settings: learned skills") }
private var skillsPlace: String { String(localized: "Settings › Skills", comment: "Where on the Mac: learned skills") }

struct LearnedSkillsPage: View {
    @Environment(AppModel.self) private var model: AppModel
    @State private var state: Loadable<LearnedSkills> = .loading

    var body: some View {
        SettingsScroll(title: skillsTitle) {
            LoadedPage(state: state, macPlace: skillsPlace, retry: { Task { await load() } }) { skills in
                GroupedSection(header: nil, footer: nil) {
                    if skills.items.isEmpty {
                        ListRow(systemImage: nil, title: String(localized: "No learned skills yet", comment: "Skills: the list is empty"),
                                description: nil, accessory: .none)
                    }
                    ForEach(skills.items) { skill in
                        NavigationRow(systemImage: skill.pinned ? "pin" : "book", title: skill.name, description: skill.description,
                                      detail: skill.resting ? String(localized: "Resting", comment: "Skills: a skill is resting") : nil) {
                            LearnedSkillPage(skill: skill) { state = .loaded($0) }
                        }
                    }
                }
            }
        }
        .task { await load() }
        .refreshable { await load() }
    }

    private func load() async {
        state = await model.read { api throws(DaemonRequestError) in try await api.learnedSkills() }
    }
}

private enum SkillConfirmation: Identifiable {
    case restore(LearnedSkillVersion)
    case remove

    var id: Int {
        switch self {
        case .restore(let version): version.id
        case .remove: -1
        }
    }
}

private struct LearnedSkillPage: View {
    let didChange: (LearnedSkills) -> Void
    @Environment(AppModel.self) private var model: AppModel
    @Environment(\.dismiss) private var dismiss: DismissAction
    @State private var skill: LearnedSkill
    @State private var versions: Loadable<SkillVersions> = .loading
    @State private var confirmation: SkillConfirmation?
    @State private var problem: SettingsFailure?
    @State private var busy: Bool = false

    init(skill: LearnedSkill, didChange: @escaping (LearnedSkills) -> Void) {
        self._skill = State(initialValue: skill)
        self.didChange = didChange
    }

    var body: some View {
        SettingsScroll(title: skill.name) {
            if let problem { FailureBanner(failure: problem, macPlace: skillsPlace, retry: nil) }
            actions
            GroupedSection(header: String(localized: "Versions", comment: "Skills: past versions of a learned skill"), footer: nil) {
                LoadedPage(state: versions, macPlace: skillsPlace, retry: { Task { await load() } }) { history in
                    ForEach(history.items.reversed()) { version in
                        DisclosureGroup {
                            versionDetails(version)
                        } label: {
                            ListRow(systemImage: "clock", title: String(localized: "Version \(version.id)", comment: "Skills: the number of a version"),
                                    description: Date(timeIntervalSince1970: version.note.at).formatted(),
                                    accessory: version.current ? .detail(String(localized: "Current", comment: "Skills: the version in use")) : .none)
                        }
                    }
                }
            }
        }
        .disabled(busy)
        .task { await load() }
        .refreshable { await load() }
        .confirmationDialog(
            String(localized: "Change this skill?", comment: "Skills: asks before restoring or removing a learned skill"),
            isPresented: Binding(get: { confirmation != nil }, set: { if !$0 { confirmation = nil } }),
            titleVisibility: .visible, presenting: confirmation
        ) { choice in
            switch choice {
            case .restore(let version):
                Button(String(localized: "Restore", comment: "Skills: puts back a past version")) {
                    Task {
                        let name = skill.name
                        await change { api throws(DaemonRequestError) in try await api.restoreLearnedSkill(name, version: version.id) }
                        await load()
                    }
                }
            case .remove:
                Button(String(localized: "Delete", comment: "Skills: removes a learned skill"), role: .destructive) {
                    Task {
                        let name = skill.name
                        await change { api throws(DaemonRequestError) in try await api.removeLearnedSkill(name) }
                    }
                }
            }
            Button(String(localized: "Cancel", comment: "Skills: cancels restoring or deleting"), role: .cancel) {}
        } message: { choice in
            switch choice {
            case .restore:
                Text(String(localized: "The earlier steps become a new version.", comment: "Skills: restoring keeps both versions"))
            case .remove:
                Text(String(localized: "This permanently deletes the skill and its versions. A later task may learn it again.", comment: "Skills: deletion is permanent and includes all versions"))
            }
        }
    }

    private var actions: some View {
        let name = skill.name
        return GroupedSection(header: nil, footer: skill.description) {
            ToggleRow(systemImage: "pin", title: String(localized: "Pinned", comment: "Skills: keep a skill from resting"),
                      description: nil, isOn: Binding(get: { skill.pinned }, set: { pinned in
                Task { await change { api throws(DaemonRequestError) in try await api.pinLearnedSkill(name, pinned: pinned) } }
            }))
            if skill.agentId != nil {
                ToggleRow(systemImage: "person.2", title: String(localized: "Share with all bots", comment: "Skills: make this bot's skill available to all"),
                          description: nil, isOn: Binding(get: { skill.shared }, set: { shared in
                    Task { await change { api throws(DaemonRequestError) in try await api.shareLearnedSkill(name, shared: shared) } }
                }))
            }
            if skill.resting {
                button(String(localized: "Wake", comment: "Skills: bring a resting skill back"), symbol: "sun.max") {
                    Task { await change { api throws(DaemonRequestError) in try await api.wakeLearnedSkill(name) } }
                }
            } else if !skill.pinned && !skill.retired {
                button(String(localized: "Put to rest", comment: "Skills: stop offering this skill until it is woken"), symbol: "moon") {
                    Task { await change { api throws(DaemonRequestError) in try await api.restLearnedSkill(name) } }
                }
            }
            button(String(localized: "Delete", comment: "Skills: removes a learned skill"), symbol: "trash") { confirmation = .remove }
        }
    }

    private func versionDetails(_ version: LearnedSkillVersion) -> some View {
        VStack(alignment: .leading, spacing: Space.s) {
            Text(verbatim: version.text.description).textRole(.body)
            if !version.text.when.isEmpty { Text(verbatim: version.text.when).textRole(.footnote).foregroundStyle(Tone.secondary) }
            ForEach(Array(version.text.steps.enumerated()), id: \.offset) { _, step in
                Text(verbatim: step).textRole(.body)
            }
            if version.note.tainted != false {
                Notice(tone: .warn, message: String(localized: "These steps may come from outside content.", comment: "Skills: the version may include third-party content"), dismiss: nil)
            }
            if !version.current {
                Button(String(localized: "Restore", comment: "Skills: puts back a past version")) {
                    confirmation = .restore(version)
                }
                .buttonStyle(SecondaryButtonStyle())
            }
        }
        .padding(Space.groupedGutter)
    }

    private func button(_ title: String, symbol: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            ListRow(systemImage: symbol, title: title, description: nil, accessory: .none)
        }
        .buttonStyle(RowPressStyle())
    }

    private func load() async {
        let name = skill.name
        versions = await model.read { api throws(DaemonRequestError) in try await api.learnedSkillVersions(name) }
    }

    private func change(_ call: (DaemonAPI) async throws(DaemonRequestError) -> LearnedSkills) async {
        guard !busy else {
            problem = .other(String(localized: "Wait for the current change to finish.", comment: "Skills: a change is already saving"))
            return
        }
        busy = true
        defer { busy = false }
        switch await model.write(call) {
        case .success(let latest):
            problem = nil
            didChange(latest)
            if let changed = latest.items.first(where: { $0.name == skill.name }) {
                skill = changed
            } else {
                dismiss()
            }
        case .failure(let box): problem = box.failure
        }
    }
}
