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
            VStack(spacing: 0) {
                VStack(spacing: 8) {
                    if let agent {
                        Avatar(id: agent.id, name: agent.record.name, size: 52)
                        Text(agent.record.name).font(.system(size: 17, weight: .semibold)).foregroundStyle(Theme.text)
                        if !agent.record.role.isEmpty {
                            Text(agent.record.role).font(Theme.small).foregroundStyle(Theme.muted).multilineTextAlignment(.center)
                        }
                        Text("No chat yet. Your first message starts it.").font(Theme.meta).foregroundStyle(Theme.faint).padding(.top, 4)
                    } else {
                        Text("A message starts a chat of its own.").font(Theme.small).foregroundStyle(Theme.faint)
                    }
                }
                .padding(.horizontal, 24)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                if let refusal: ModelRefusal = model.refusal {
                    VStack(alignment: .leading, spacing: 8) {
                        Text(refusal.message).font(Theme.small).foregroundStyle(Theme.warn)
                        if refusal.fallback != nil {
                            Button("Send it on the default model") { Task { await start(onDefault: true) } }
                                .buttonStyle(QuietButtonStyle(tint: Theme.accent))
                        }
                    }
                    .padding(.horizontal, 16).padding(.bottom, 8).frame(maxWidth: .infinity, alignment: .leading)
                }
                if let failure: String = model.failure {
                    Text(failure).font(Theme.meta).foregroundStyle(Theme.bad).padding(.bottom, 6)
                }
                composer
                    .background(Theme.background)
                    .overlay(alignment: .top) { Rectangle().fill(Theme.hairline).frame(height: 1) }
            }
            .background(Theme.background)
            .navigationTitle(agent.map { "Chat with \($0.record.name)" } ?? "New chat")
            .navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(Theme.background, for: .navigationBar)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } } }
            .sheet(isPresented: $showsPicker) {
                NavigationStack {
                    ModelPickerView(
                        store: store, model: model.model, effort: model.effort,
                        onModel: { model.choose(model: $0) }, onEffort: { model.choose(effort: $0) }
                    )
                    .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { showsPicker = false } } }
                }
                .tint(Theme.accent)
                .preferredColorScheme(.dark)
                .presentationDetents([.medium, .large])
                .presentationBackground(Theme.background)
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

    private var composer: some View {
        VStack(spacing: 6) {
            HStack(spacing: 8) {
                Button { showsPicker = true } label: {
                    HStack(spacing: 5) {
                        Image(systemName: "cpu").font(.system(size: 10))
                        Text(pillWords).font(Theme.meta).lineLimit(1)
                        Image(systemName: "chevron.up.chevron.down").font(.system(size: 8))
                    }
                    .foregroundStyle(Theme.muted)
                    .padding(.horizontal, 10).frame(height: 26)
                    .overlay(RoundedRectangle(cornerRadius: Theme.radius).strokeBorder(Theme.hairline, lineWidth: 1))
                }
                .accessibilityIdentifier("model-pill")
                .accessibilityLabel("Model: \(pillWords)")
                Spacer()
            }
            .padding(.horizontal, 12).padding(.top, 8)
            if !model.images.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(model.images) { image in
                            Image(uiImage: image.preview).resizable().scaledToFill().frame(width: 52, height: 52)
                                .clipShape(RoundedRectangle(cornerRadius: Theme.radius))
                        }
                    }.padding(.horizontal, 16)
                }
            }
            DictationLine(dictation: model.dictation)
            HStack(alignment: .bottom, spacing: 8) {
                PhotosPicker(selection: $picks, maxSelectionCount: 4, matching: .images) {
                    Image(systemName: "plus").font(.system(size: 16, weight: .medium)).foregroundStyle(Theme.muted).frame(width: 36, height: 36)
                }
                .accessibilityLabel("Attach a photo")
                TextField(agent.map { "Message \($0.record.name)" } ?? "Message Dot", text: $model.draft, axis: .vertical)
                    .font(Theme.body)
                    .lineLimit(1...6)
                    .focused($focused)
                    .padding(.horizontal, 12).padding(.vertical, 8)
                    .background(Theme.surface, in: RoundedRectangle(cornerRadius: Theme.radius))
                    .overlay(RoundedRectangle(cornerRadius: Theme.radius).strokeBorder(Theme.hairline, lineWidth: 1))
                    .accessibilityIdentifier("composer-field")
                MicButton(dictation: model.dictation) { model.toggleDictation() }
                Button { Task { await start(onDefault: false) } } label: {
                    Image(systemName: "arrow.up").font(.system(size: 14, weight: .bold))
                        .foregroundStyle(model.canSend ? Color.black.opacity(0.9) : Theme.faint)
                        .frame(width: 36, height: 36)
                        .background(model.canSend ? Theme.accent : Theme.raised, in: RoundedRectangle(cornerRadius: Theme.radius))
                }
                .disabled(!model.canSend)
                .accessibilityIdentifier("send")
                .accessibilityLabel("Start the chat")
            }
            .padding(.horizontal, 10)
        }
        .padding(.bottom, 8)
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
