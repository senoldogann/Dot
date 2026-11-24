import Observation
import PhotosUI
import SwiftUI
import UIKit
import os

/// A new chat: a message that starts a chat of its own on the model picked (item 1350), or, when `agent` is set, the
/// agent's own chat when it has none (`POST /api/runs` with the agent). The words stay in the box until the daemon took
/// them; a model that cannot answer is refused by name, with a button that sends the message on the default model.
@MainActor
@Observable
final class NewChatModel {
    let store: NativeStore
    let agent: AgentSummary?
    var draft: String
    var images: [PickedImage] = []
    /// The model picked (`nil`: the default) and the reasoning level (`nil`: the model's default).
    private(set) var model: String?
    private(set) var effort: String?
    private(set) var starting: Bool = false
    private(set) var failure: String?
    private(set) var refusal: ModelRefusal?
    let dictation: Dictation = Dictation()

    @ObservationIgnored private let logger: Logger = Logger(subsystem: "dev.dotagent.phone", category: "new-chat")
    /// The message whose answer may have been lost: sent again, it carries the same key and the daemon starts it once.
    @ObservationIgnored private var unsent: (text: String, key: String)?

    init(store: NativeStore, agent: AgentSummary?) {
        self.store = store
        self.agent = agent
        self.draft = store.drafts.text(for: Drafts.newChatKey(agent: agent?.id))
    }

    var draftKey: String { Drafts.newChatKey(agent: agent?.id) }
    var canSend: Bool { !starting && (!draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !images.isEmpty) }

    func persistDraft() { store.drafts.save(draft, for: draftKey) }

    /// What the box held when dictation began, and what it wrote since: a key the owner types while it listens ends it at
    /// once (the words they typed stay), not at the next word it hears.
    @ObservationIgnored private var dictationBase: String = ""
    @ObservationIgnored private var dictationWritten: DictationWritten = DictationWritten()

    func draftChanged() {
        persistDraft()
        if dictation.state.isActive, draft != (dictationWritten.text ?? dictationBase) { dictation.stop() }
    }

    func choose(model ref: String?) {
        model = ref
        if let level: String = effort, let option: ModelOption = store.providers.option(ref ?? store.models?.main),
            !option.pickableEfforts.contains(level)
        {
            effort = nil
        }
        refusal = nil
    }

    func choose(effort level: String?) { effort = level }

    func toggleDictation() {
        let base: String = draft
        let written: DictationWritten = DictationWritten()
        dictationBase = base
        dictationWritten = written
        dictation.toggle { [weak self] heard in
            guard let self else { return }
            // Typing while it listens stops it, and what was typed stays.
            guard let next: String = DictationText.update(current: self.draft, lastWritten: written.text, base: base, heard: heard) else {
                self.dictation.stop()
                return
            }
            written.text = next
            self.draft = next
        }
    }

    func pick(_ items: [PhotosPickerItem]) async {
        for item in items {
            guard let data: Data = try? await item.loadTransferable(type: Data.self), let image: UIImage = UIImage(data: data),
                let jpeg: Data = image.jpegData(compressionQuality: 0.85)
            else { continue }
            images.append(PickedImage(name: "photo-\(images.count + 1).jpg", data: jpeg, preview: image))
        }
    }

    /// Starts the chat; the thread to open, or `nil` with `failure` or `refusal` saying why not. `onDefault` sends the
    /// same words on the default model, as the refusal's button does.
    func start(onDefault: Bool = false) async -> ThreadSummary? {
        let text: String = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard canSend else { return nil }
        starting = true
        failure = nil
        refusal = nil
        defer { starting = false }
        let words: String = text.isEmpty ? "(photo)" : text
        let key: String = unsent?.text == words ? unsent?.key ?? newMessageKey() : newMessageKey()
        unsent = (words, key)
        do {
            var ids: [String] = []
            let uploader: DaemonClient = DaemonClient(session: store.api.session, maxAttempts: 2)
            for image in images {
                let upload: AttachmentUpload = AttachmentUpload(name: image.name, mediaType: "image/jpeg", dataB64: image.data.base64EncodedString())
                ids.append(try await uploader.uploadAttachment(upload, host: store.host, key: store.api.key))
            }
            let started: RunRecord = try await store.api.start(
                chat: NewChat(goal: words, key: key, attachments: ids, choices: choices(onDefault: onDefault)))
            unsent = nil
            draft = ""
            images = []
            store.drafts.remove(draftKey)
            // The daemon has the run now: a failed read of its page is no failure to start. The chat opens from the run
            // the daemon answered with, and its own session reads the rest.
            let detail: RunDetail? = try? await store.api.detail(run: started.id)
            Task { await store.refreshList() }
            return ThreadSummary.started(run: started, detail: detail)
        } catch {
            logger.error("a chat was not started: \(String(describing: error), privacy: .public)")
            if let refused: ModelRefusal = ModelRefusal.from(error) {
                refusal = refused
            } else {
                failure = "Dot could not start the chat. Your message is still here."
            }
            return nil
        }
    }

    private func choices(onDefault: Bool) -> MessageChoices {
        MessageChoices(mode: "auto", model: onDefault ? nil : model, effort: onDefault ? nil : effort, agentId: agent?.id)
    }
}

/// The new-chat sheet: the box, the model and level picked, the mic and Send.
struct NewChatView: View {
    let store: NativeStore
    let agent: AgentSummary?
    let onStarted: (ThreadSummary) -> Void
    @State private var model: NewChatModel
    @State private var picks: [PhotosPickerItem] = []
    @State private var showsPicker: Bool = false
    @FocusState private var focused: Bool
    @Environment(\.dismiss) private var dismiss: DismissAction

    init(store: NativeStore, agent: AgentSummary?, onStarted: @escaping (ThreadSummary) -> Void) {
        self.store = store
        self.agent = agent
        self.onStarted = onStarted
        _model = State(initialValue: NewChatModel(store: store, agent: agent))
    }

    var body: some View {
        NavigationStack {
            hero
                .safeAreaInset(edge: .bottom, spacing: 0) {
                    VStack(spacing: Space.s) {
                        if let refusal: ModelRefusal = model.refusal {
                            VStack(alignment: .leading, spacing: Space.s) {
                                Label(refusal.message, systemImage: "exclamationmark.triangle.fill")
                                    .font(Theme.small).foregroundStyle(Theme.text)
                                if refusal.fallback != nil {
                                    Button("Send it on the default model") { Task { await start(onDefault: true) } }
                                        .dotButton(.prominent)
                                }
                            }
                            .padding(Space.m)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .background(Theme.warn.opacity(0.12), in: RoundedRectangle(cornerRadius: Radius.l))
                            .overlay(RoundedRectangle(cornerRadius: Radius.l).strokeBorder(Theme.warn.opacity(0.4), lineWidth: 1))
                        }
                        if let failure: String = model.failure {
                            Label(failure, systemImage: "exclamationmark.circle.fill").font(Theme.meta).foregroundStyle(Theme.bad)
                        }
                        composer
                    }
                    .padding(.horizontal, Space.s + 2)
                    .padding(.bottom, Space.xs)
                }
                .dotScreenBackground(inSheet: true)
                .navigationTitle(agent.map { "Chat with \($0.record.name)" } ?? "New chat")
                .navigationBarTitleDisplayMode(.inline)
                .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } } }
                .sheet(isPresented: $showsPicker) {
                    NavigationStack {
                        ModelPickerView(
                            store: store, model: model.model, effort: model.effort,
                            onModel: { model.choose(model: $0) }, onEffort: { model.choose(effort: $0) }
                        )
                        .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { showsPicker = false } } }
                    }
                    .dotSheet()
                    .presentationDetents([.medium, .large])
                }
                .onChange(of: model.draft) { model.draftChanged() }
                .onChange(of: picks) {
                    let chosen: [PhotosPickerItem] = picks
                    picks = []
                    Task { await model.pick(chosen) }
                }
                .task {
                    await store.loadProviders()
                    focused = true
                }
                .onDisappear { model.dictation.stop(); store.drafts.flush() }
        }
    }

    /// What the sheet says above the box: who the chat is with, or what a new chat is.
    private var hero: some View {
        VStack(spacing: Space.s) {
            if let agent {
                Avatar(id: agent.id, name: agent.record.name, size: 64)
                    .padding(.bottom, Space.s)
                Text(agent.record.name).font(.title2.weight(.semibold)).foregroundStyle(Theme.text)
                if !agent.record.role.isEmpty {
                    Text(agent.record.role).font(.subheadline).foregroundStyle(Theme.muted).multilineTextAlignment(.center)
                }
                Text("No chat yet. Your first message starts it.").font(Theme.meta).foregroundStyle(Theme.faint).padding(.top, Space.xs)
            } else {
                DotEmptyState(
                    symbol: "sparkles", title: "A new chat",
                    message: "A message starts a chat of its own. Dot works on it and asks you only when it must.")
            }
        }
        .padding(.horizontal, Space.xl)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var composer: some View {
        VStack(spacing: Space.s) {
            HStack(spacing: Space.s) {
                Button { showsPicker = true } label: {
                    HStack(spacing: Space.xs + 1) {
                        Image(systemName: "cpu").font(.system(size: 11, weight: .medium))
                        Text(pillWords).font(Theme.meta.weight(.medium)).lineLimit(1)
                        Image(systemName: "chevron.up.chevron.down").font(.system(size: 9, weight: .semibold))
                    }
                    .foregroundStyle(Theme.text)
                    .padding(.horizontal, Space.m).frame(minHeight: 36)
                    .dotGlass(in: Capsule(), interactive: true)
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("model-pill")
                .accessibilityLabel("Model: \(pillWords)")
                Spacer()
            }
            VStack(spacing: Space.s) {
                if !model.images.isEmpty {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: Space.s) {
                            ForEach(model.images) { image in
                                Image(uiImage: image.preview).resizable().scaledToFill().frame(width: 52, height: 52)
                                    .clipShape(RoundedRectangle(cornerRadius: Radius.m - 2))
                            }
                        }.padding(.horizontal, Space.m)
                    }
                }
                DictationLine(dictation: model.dictation)
                HStack(alignment: .bottom, spacing: 2) {
                    PhotosPicker(selection: $picks, maxSelectionCount: 4, matching: .images) {
                        Image(systemName: "plus").font(.system(size: 17, weight: .medium)).foregroundStyle(Theme.muted)
                            .frame(width: 44, height: 44).contentShape(Rectangle())
                    }
                    .accessibilityLabel("Attach a photo")
                    TextField("", text: $model.draft, prompt: Text(agent.map { "Message \($0.record.name)" } ?? "Message Dot").foregroundStyle(Theme.muted), axis: .vertical)
                        .font(Theme.body)
                        .lineLimit(1...6)
                        .focused($focused)
                        .padding(.vertical, Space.m - 1)
                        .frame(minHeight: 44)
                        .accessibilityIdentifier("composer-field")
                    MicButton(dictation: model.dictation) { model.toggleDictation() }
                    Button { Task { await start(onDefault: false) } } label: {
                        Image(systemName: "arrow.up").font(.system(size: 15, weight: .bold))
                            .foregroundStyle(model.canSend ? Theme.onAccent : Theme.faint)
                            .frame(width: 36, height: 36)
                            .background(Circle().fill(model.canSend ? AnyShapeStyle(Theme.accentGradient) : AnyShapeStyle(Theme.faint.opacity(0.16))))
                            .frame(width: 44, height: 44)
                            .contentShape(Circle())
                    }
                    .buttonStyle(.plain)
                    .disabled(!model.canSend)
                    .accessibilityIdentifier("send")
                    .accessibilityLabel("Start the chat")
                    .sensoryFeedback(.impact(weight: .light), trigger: model.starting) { _, new in new }
                }
                .padding(.horizontal, Space.xs)
            }
            .dotGlass(in: RoundedRectangle(cornerRadius: Radius.xl + 2))
        }
    }

    private var pillWords: String {
        let words: String = modelWords(model.model, in: store.providers, main: store.models?.main)
        guard let level: String = model.effort else { return words }
        return "\(words) · \(effortWords(level))"
    }

    private func start(onDefault: Bool) async {
        guard let thread: ThreadSummary = await model.start(onDefault: onDefault) else { return }
        dismiss()
        onStarted(thread)
    }
}
