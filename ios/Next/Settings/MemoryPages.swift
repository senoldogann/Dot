import DotEngine
import SwiftUI

// MARK: Memory (items 1759, 1773)

/// Tags as typed: split at commas, trimmed, blanks dropped, each once (pure; the web's `parseTags`).
func parsedTags(_ text: String) -> [String] {
    var seen: [String] = []
    for part in text.split(separator: ",") {
        let tag: String = part.trimmingCharacters(in: .whitespaces)
        if !tag.isEmpty, !seen.contains(tag) { seen.append(tag) }
    }
    return seen
}

/// The notes read so far, and how many there are in all.
struct NotesState: Sendable {
    let items: [MemoryNote]
    let total: Int
}

/// Settings › Agents › Memory, what a phone needs of it (PLAN §26): the runs' proposed rewrites of the owner's notes
/// (item 1711) to Use or Drop, a search, and every note, newest change first, to read, edit or forget. The trash and
/// the learning's inner workings are the Mac's.
struct MemoryPage: View {
    @Environment(AppModel.self) private var model: AppModel
    @State private var proposals: Loadable<[MemoryNote]> = .loading
    @State private var notes: Loadable<NotesState> = .loading
    @State private var query: String = ""
    @State private var hits: Loadable<[MemoryHit]>?
    @State private var opened: MemoryNote?
    @State private var problem: SettingsFailure?
    @State private var deciding: String?

    private var macPlace: String { String(localized: "Settings › Memory", comment: "Where on the Mac: the Memory page") }
    private let pageSize: Int = 30

    var body: some View {
        SettingsScroll(title: String(localized: "Memory", comment: "Settings row")) {
            if let problem { FailureBanner(failure: problem, macPlace: macPlace, retry: nil) }
            LoadedPage(state: proposals, macPlace: macPlace, retry: { Task { await load() } }) { list in
                GroupedSection(
                    header: String(localized: "Proposed changes", comment: "Memory: runs' rewrites of the owner's notes"),
                    footer: list.isEmpty
                        ? String(localized: "None waits. When a task wants to change a note you wrote, it waits here.", comment: "Memory: no proposals")
                        : String(localized: "A task wants to change a note you wrote or approved. Your words stay until you choose.", comment: "Memory: what a proposal is")
                ) {
                    ForEach(list) { note in proposalCard(note) }
                }
            }
            GroupedSection(header: String(localized: "Search", comment: "Memory: the search section"), footer: nil) {
                HStack(spacing: Space.s) {
                    Symbol(name: "magnifyingglass", context: .row).foregroundStyle(Tone.secondary)
                    TextField(String(localized: "Search what Dot remembers", comment: "Memory: the search field"), text: $query)
                        .submitLabel(.search).textRole(.body)
                        .onSubmit { Task { await search() } }
                        .onChange(of: query) { if query.isEmpty { hits = nil } }
                }
                .padding(.horizontal, Space.groupedGutter).frame(minHeight: Metric.row)
            }
            if let hits {
                LoadedPage(state: hits, macPlace: macPlace, retry: { Task { await search() } }) { found in
                    if found.isEmpty {
                        ContentUnavailableView.search(text: query)
                    } else {
                        GroupedSection(header: String(localized: "\(found.count) notes", comment: "Memory: the number of search hits"), footer: nil) {
                            ForEach(found, id: \.record.id) { hit in noteRow(hit.record) }
                        }
                    }
                }
            } else {
                LoadedPage(state: notes, macPlace: macPlace, retry: { Task { await loadNotes(count: pageSize) } }) { state in
                    if state.items.isEmpty {
                        EmptyState(
                            title: String(localized: "Nothing remembered yet", comment: "Memory: no notes"), systemImage: "brain",
                            detail: String(localized: "Ask Dot to remember something during a task.", comment: "Memory: how a note is made"), actionTitle: nil, action: {}).fillsScrollPage()
                    } else {
                        GroupedSection(header: String(localized: "\(state.total) notes", comment: "Memory: the number of notes"), footer: nil) {
                            ForEach(state.items) { note in noteRow(note) }
                            if state.items.count < state.total {
                                Button { Task { await loadNotes(count: state.items.count + pageSize) } } label: {
                                    ListRow(systemImage: nil, title: String(localized: "Show more notes", comment: "Memory: reads the next notes"), description: nil, accessory: .none)
                                }
                                .buttonStyle(RowPressStyle())
                            }
                        }
                    }
                }
            }
        }
        .sheet(item: $opened) { note in
            MemoryNoteSheet(note: note) { change in await apply(change, to: note) }
        }
        .task { await load() }
        .refreshable { await load() }
    }

    private func noteRow(_ note: MemoryNote) -> some View {
        Button { opened = note } label: {
            VStack(alignment: .leading, spacing: Space.xxs) {
                Text(note.content).textRole(.body).foregroundStyle(Tone.text).lineLimit(2).multilineTextAlignment(.leading)
                if note.pinnedAt != nil || !note.tags.isEmpty {
                    Text(((note.pinnedAt != nil ? [String(localized: "Pinned", comment: "Memory: a pinned note")] : []) + note.tags.prefix(3).map { "#\($0)" }).joined(separator: " · "))
                        .textRole(.footnote).foregroundStyle(Tone.secondary).lineLimit(1)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, Space.groupedGutter).padding(.vertical, Space.s)
            .frame(minHeight: Metric.row)
            .contentShape(Rectangle())
        }
        .buttonStyle(RowPressStyle())
    }

    @ViewBuilder private func proposalCard(_ note: MemoryNote) -> some View {
        if let proposed = note.proposed {
            VStack(alignment: .leading, spacing: Space.s) {
                Text(String(localized: "Now", comment: "Memory proposal: the note's words as they stand")).textRole(.label).foregroundStyle(Tone.secondary)
                Text(note.content).textRole(.body).foregroundStyle(Tone.secondary)
                HStack(spacing: Space.xs) {
                    Text(String(localized: "Proposed", comment: "Memory proposal: the run's words")).textRole(.label).foregroundStyle(Tone.secondary)
                    if proposed.tainted {
                        Text(String(localized: "From outside content", comment: "Memory proposal: the run had read outside content")).textRole(.label).foregroundStyle(Tone.warn)
                            .padding(.horizontal, Space.xs).padding(.vertical, 2).background(Tone.warnFill, in: Capsule())
                    }
                }
                Text(proposed.content).textRole(.body).foregroundStyle(Tone.text)
                HStack(spacing: Space.s) {
                    Button(String(localized: "Use", comment: "Memory proposal: take the run's words")) { Task { await decide(note, take: true) } }
                        .buttonStyle(PrimaryButtonStyle())
                    Button(String(localized: "Drop", comment: "Memory proposal: keep the owner's words")) { Task { await decide(note, take: false) } }
                        .buttonStyle(SecondaryButtonStyle())
                }
                .disabled(deciding == note.id)
            }
            .padding(Space.groupedGutter)
            .accessibilityElement(children: .contain)
        }
    }

    private func load() async {
        proposals = await model.read { api throws(DaemonRequestError) in try await api.proposedNotes() }
        await loadNotes(count: max(notes.value?.items.count ?? pageSize, pageSize))
        if hits != nil { await search() }
    }

    /// Reads the first `count` notes, the newest change first, in pages of at most 200 (the daemon's most).
    private func loadNotes(count: Int) async {
        var items: [MemoryNote] = []
        var total: Int = 0
        repeat {
            let offset: Int = items.count
            let limit: Int = min(count - offset, 200)
            let read: Loadable<MemoryList> = await model.read { api throws(DaemonRequestError) in try await api.memories(limit: limit, offset: offset, order: .changed) }
            switch read {
            case .loaded(let list):
                items += list.items
                total = list.total
                if list.items.isEmpty { total = items.count }
            case .failed(let failure):
                notes = .failed(failure)
                return
            case .loading:
                return
            }
        } while items.count < min(count, total)
        notes = .loaded(NotesState(items: items, total: total))
    }

    private func search() async {
        let words: String = query.trimmingCharacters(in: .whitespaces)
        guard !words.isEmpty else {
            hits = nil
            return
        }
        hits = .loading
        hits = await model.read { api throws(DaemonRequestError) in try await api.searchMemory(words, limit: 30) }
    }

    private func decide(_ note: MemoryNote, take: Bool) async {
        guard let at = note.proposed?.at else { return }
        deciding = note.id
        let id: String = note.id
        let result: Result<MemoryNote, SettingsFailureBox> = await model.write { api throws(DaemonRequestError) in
            take ? try await api.takeProposal(note: id, at: at) : try await api.dropProposal(note: id, at: at)
        }
        switch result {
        case .success:
            if var all = proposals.value {
                all.removeAll { $0.id == id }
                proposals = .loaded(all)
            }
            problem = nil
            await loadNotes(count: max(notes.value?.items.count ?? pageSize, pageSize))
        case .failure(let box):
            problem = box.failure
            proposals = await model.read { api throws(DaemonRequestError) in try await api.proposedNotes() }
        }
        deciding = nil
    }

    /// Does what the open note's sheet asked; `nil` when the Mac did it (the sheet closes), else why not.
    private func apply(_ change: NoteChange, to note: MemoryNote) async -> SettingsFailure? {
        let id: String = note.id
        let result: Result<MemoryNote, SettingsFailureBox> =
            switch change {
            case .edit(let write): await model.write { api throws(DaemonRequestError) in try await api.editMemory(id, note: write) }
            case .forget: await model.write { api throws(DaemonRequestError) in try await api.forgetMemory(id) }
            }
        switch result {
        case .success:
            problem = nil
            await loadNotes(count: max(notes.value?.items.count ?? pageSize, pageSize))
            if hits != nil { await search() }
            return nil
        case .failure(let box): return box.failure
        }
    }
}

/// What the open note's sheet asks of the Mac.
enum NoteChange: Sendable {
    case edit(MemoryWrite)
    case forget
}

/// An open note: its whole words and tags; Edit (words and tags) and Forget (to the Mac's trash, after a confirmation).
struct MemoryNoteSheet: View {
    let note: MemoryNote
    let apply: (NoteChange) async -> SettingsFailure?
    @Environment(\.dismiss) private var dismiss: DismissAction
    @State private var editing: Bool = false
    @State private var content: String = ""
    @State private var tags: String = ""
    @State private var working: Bool = false
    @State private var forgetAsked: Bool = false
    @State private var problem: SettingsFailure?

    var body: some View {
        NavigationStack {
            SettingsScroll(title: editing ? String(localized: "Edit note", comment: "Memory: the edit's title") : String(localized: "Note", comment: "Memory: an open note's title")) {
                if let problem { FailureBanner(failure: problem, macPlace: String(localized: "Settings › Memory", comment: "Where on the Mac: the Memory page"), retry: nil) }
                if editing {
                    GroupedSection(header: String(localized: "Note", comment: "Memory: an open note's title"), footer: nil) {
                        TextField(String(localized: "Note", comment: "Memory: an open note's title"), text: $content, axis: .vertical).lineLimit(3...14).textRole(.body).padding(Space.groupedGutter)
                    }
                    GroupedSection(header: String(localized: "Tags", comment: "Memory: a note's tags"), footer: String(localized: "Optional, separated by commas.", comment: "Memory: the tags' hint")) {
                        TextField(String(localized: "Tags", comment: "Memory: a note's tags"), text: $tags).textRole(.body).autocorrectionDisabled().textInputAutocapitalization(.never)
                            .padding(.horizontal, Space.groupedGutter).frame(minHeight: Metric.row)
                    }
                } else {
                    GroupedSection(header: nil, footer: nil) {
                        Text(note.content).textRole(.body).foregroundStyle(Tone.text).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading).padding(Space.groupedGutter)
                        if !note.tags.isEmpty {
                            Text(note.tags.map { "#\($0)" }.joined(separator: " ")).textRole(.footnote).foregroundStyle(Tone.secondary).padding([.horizontal, .bottom], Space.groupedGutter)
                        }
                    }
                    GroupedSection(header: nil, footer: nil) {
                        Button { editing = true } label: {
                            ListRow(systemImage: nil, title: String(localized: "Edit", comment: "Schedule menu: edit"), description: nil, accessory: .none)
                        }
                        .buttonStyle(RowPressStyle())
                        Button { forgetAsked = true } label: {
                            ListRow(systemImage: nil, title: String(localized: "Forget", comment: "Memory: sends a note to the trash"), description: nil, accessory: .none)
                        }
                        .buttonStyle(RowPressStyle())
                    }
                    .disabled(working)
                }
            }
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(String(localized: "Close", comment: "Sheet toolbar: closes an inspector sheet"), systemImage: "xmark") { if editing { editing = false } else { dismiss() } }
                }
                if editing {
                    ToolbarItem(placement: .confirmationAction) {
                        Button(String(localized: "Save", comment: "Edit sheet toolbar: keeps the edits"), systemImage: "checkmark") {
                            Task { await run(.edit(MemoryWrite(content: content.trimmingCharacters(in: .whitespacesAndNewlines), tags: parsedTags(tags)))) }
                        }
                        .disabled(working || content.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    }
                }
            }
            .confirmationDialog(String(localized: "Forget this note?", comment: "Memory: confirm forgetting a note"), isPresented: $forgetAsked, titleVisibility: .visible) {
                Button(String(localized: "Forget", comment: "Memory: sends a note to the trash"), role: .destructive) { Task { await run(.forget) } }
            } message: {
                Text(String(localized: "It goes to the trash on your Mac, where it can be restored for a while.", comment: "Memory: what forgetting does"))
            }
        }
        .presentationDetents([.medium, .large])
        .interactiveDismissDisabled(working)
        .onAppear {
            content = note.content
            tags = note.tags.joined(separator: ", ")
        }
    }

    private func run(_ change: NoteChange) async {
        working = true
        problem = await apply(change)
        working = false
        if problem == nil { dismiss() }
    }
}
