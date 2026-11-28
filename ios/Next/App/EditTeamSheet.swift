import DotEngine
import SwiftUI

struct EditTeamSheet: View {
    let team: TeamRecordView
    let saved: () async -> Void
    @Environment(AppModel.self) private var model: AppModel
    @Environment(\.dismiss) private var dismiss: DismissAction
    @State private var name: String
    @State private var purpose: String
    @State private var members: [String]
    @State private var agents: Loadable<[AgentSummary]> = .loading
    @State private var problem: SettingsFailure?
    @State private var saving: Bool = false

    init(team: TeamRecordView, saved: @escaping () async -> Void) {
        self.team = team
        self.saved = saved
        self._name = State(initialValue: team.name)
        self._purpose = State(initialValue: team.purpose)
        self._members = State(initialValue: team.agents)
    }

    private var title: String { String(localized: "Edit the team", comment: "Group chat: edits the team") }
    private var macPlace: String { String(localized: "Agents › Your teams", comment: "Where on the Mac: where teams are changed") }
    private var edit: TeamEdit? { TeamEdit.checked(name: name, purpose: purpose, agents: members) }

    var body: some View {
        NavigationStack {
            SettingsScroll(title: title) {
                if let problem { FailureBanner(failure: problem, macPlace: macPlace, retry: nil) }
                GroupedSection(header: String(localized: "Name", comment: "New group chat: the field of its name"), footer: nil) {
                    TextField(String(localized: "Launch, Research desk…", comment: "New group chat: example names"), text: $name)
                        .textRole(.body).padding(Space.groupedGutter).accessibilityIdentifier("editTeam.name")
                    TextField(String(localized: "What is it for? (optional)", comment: "New group chat: the field of its purpose"),
                              text: $purpose, axis: .vertical)
                        .textRole(.body).lineLimit(1...4).padding(Space.groupedGutter)
                }
                GroupedSection(header: String(localized: "Bots", comment: "Group chat: the bots of the team"), footer: nil) {
                    LoadedPage(state: agents, macPlace: macPlace, retry: { Task { await load() } }) { agents in
                        ForEach(agents.map(\.record).filter { $0.id != dotAgentId || team.agents.contains($0.id) }) { bot in
                            ToggleRow(systemImage: nil, title: bot.name, description: bot.role.isEmpty ? nil : bot.role,
                                      isOn: membership(bot.id))
                        }
                    }
                }
            }
            .disabled(saving)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(String(localized: "Cancel", comment: "Group chat: cancels editing")) { dismiss() }.disabled(saving)
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button(String(localized: "Save", comment: "Group chat: saves the team")) { Task { await save() } }
                        .disabled(saving || agents.value == nil || edit == nil)
                }
            }
            .task { await load() }
        }
        .interactiveDismissDisabled(saving)
    }

    private func membership(_ id: String) -> Binding<Bool> {
        Binding(get: { members.contains(id) }, set: { selected in
            if selected, !members.contains(id) { members.append(id) }
            if !selected { members.removeAll { $0 == id } }
        })
    }

    private func load() async {
        agents = await model.read { api throws(DaemonRequestError) in try await api.agents() }
    }

    private func save() async {
        guard let edit, !saving else { return }
        saving = true
        defer { saving = false }
        switch await model.write({ api throws(DaemonRequestError) in try await api.updateTeam(team.id, edit: edit) }) {
        case .success: await saved(); dismiss()
        case .failure(let box): problem = box.failure
        }
    }
}
