import DotEngine
import PhotosUI
import SwiftUI
import UIKit
import VisionKit

/// The composer's two shapes (review S4): compact at rest (one row), split into the field and a toolbar once the
/// owner writes more than a line, focuses it, adds a file or edits a queued message.
enum ComposerLayout: Equatable, Sendable {
    case compact
    case expanded

    static func of(text: String, isFocused: Bool) -> ComposerLayout {
        if isFocused || text.contains("\n") || text.count > 32 { return .expanded }
        return .compact
    }
}

/// A photo the owner added to the message, as JPEG.
struct ComposerAttachment: Identifiable, Equatable {
    let id: UUID
    let jpeg: Data
    let preview: UIImage
}

/// What the model chip shows: the chat's model and level.
struct ModelChoice: Hashable, Identifiable {
    let ref: String
    let label: String
    let effort: String?
    var id: String { ref + "|" + (effort ?? "") }
}

/// The composer's button: an ink disc of 30 pt in a 44 pt target. Its glyph says what a press does (send, queue,
/// steer, stop, confirm an edit). While a run works and the draft has words, a long press opens both ways to send it,
/// Steer now and Queue, the follow-up setting's way checked; a pick sends the draft that way and leaves the setting as
/// it is. VoiceOver offers both as actions (D10).
struct ComposerButton: View {
    let buttons: ComposerButtons
    let perform: (ComposerAction) -> Void

    var body: some View {
        Button { perform(buttons.primary) } label: {
            Image(systemName: glyph(buttons.primary))
                .font(.system(.callout, weight: .semibold))
                .foregroundStyle(buttons.enabled ? Tone.onAction : Tone.tertiary)
                .frame(width: Metric.sendDisc, height: Metric.sendDisc)
                .background(buttons.enabled ? Tone.action : Tone.nested, in: Circle())
                .frame(width: Metric.hit, height: Metric.hit)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(!buttons.enabled)
        .keyboardShortcut(.return, modifiers: .command)
        .contextMenu {
            if buttons.alternate != nil {
                followUpChoice(.steer)
                followUpChoice(.queue)
            }
        }
        .accessibilityLabel(composerActionWords(buttons.primary))
        .accessibilityIdentifier("composer.send")
        .accessibilityActions {
            if buttons.alternate != nil {
                Button(followUpTitle(.steer)) { perform(.steer) }
                Button(followUpTitle(.queue)) { perform(.queue) }
            }
        }
    }

    /// One way to send of the long-press menu: its title, what it does under it, a check on the setting's way.
    private func followUpChoice(_ way: FollowUp) -> some View {
        let action: ComposerAction = way == .steer ? .steer : .queue
        return Toggle(isOn: Binding(get: { buttons.primary == action }, set: { _ in perform(action) })) {
            Text(followUpTitle(way))
            Text(followUpDetail(way))
        }
        .accessibilityIdentifier("composer.followUp." + way.rawValue)
    }

    private func glyph(_ action: ComposerAction) -> String {
        switch action {
        case .send: "arrow.up"
        case .queue: "text.line.last.and.arrowtriangle.forward"
        case .steer: "arrow.turn.down.right"
        case .stop: "stop.fill"
        case .confirmEdit: "checkmark"
        }
    }
}

/// A round 44 pt toolbar button of the composer.
struct ComposerIconButton: View {
    let systemImage: String
    let label: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Symbol(name: systemImage, context: .toolbar)
                .foregroundStyle(Tone.secondary)
                .frame(width: Metric.hit, height: Metric.hit)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(label)
    }
}

/// What the composer's pickers offer for a trigger (PLAN §22): the sections of one panel.
struct ComposerSuggestions: Sendable {
    let sections: @Sendable (PickerTrigger) -> [PickerSection]

    static let none: ComposerSuggestions = ComposerSuggestions(sections: { _ in [] })
}

/// The composer (plan §4.1, R-a §3): a glass pill radius 27; compact at rest with +, the field, the microphone and the
/// button on one row; expanded with the field above and +, the model · effort chip, the microphone and the button
/// beneath. Photos, the camera, files and a document scan go on the + menu; `/` offers commands and skills, `@` agents.
/// The chip opens the chat's settings (PLAN §24); the microphone turns the pill into the recorder (`VoiceRecorderBar`),
/// whose ✓ puts the words in the field.
struct ComposerView: View {
    @Binding var text: String
    @Binding var attachments: [ComposerAttachment]
    let placeholder: String
    let buttons: ComposerButtons
    let perform: (ComposerAction) -> Void
    let model: ModelChoice?
    /// Opens the chat's settings sheet from the chip; `nil` shows no chip.
    let openSettings: (() -> Void)?
    let suggestions: ComposerSuggestions
    @FocusState private var isFocused: Bool
    @State private var voice: VoiceInput = VoiceInput()
    @Environment(AppModel.self) private var app: AppModel
    @Environment(\.scenePhase) private var scenePhase: ScenePhase
    @State private var photoItems: [PhotosPickerItem] = []
    @State private var picking: Picker?
    @Environment(\.accessibilityReduceMotion) private var reduceMotion: Bool
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize: DynamicTypeSize

    enum Picker: String, Identifiable {
        case photos, camera, files, scan
        var id: String { rawValue }
    }

    var body: some View {
        // At the accessibility sizes the field takes its own line: one row would push the button off the edge.
        let layout: ComposerLayout =
            attachments.isEmpty && !dynamicTypeSize.isAccessibilitySize ? ComposerLayout.of(text: text, isFocused: isFocused) : .expanded
        let compact: Bool = layout == .compact
        VStack(alignment: .leading, spacing: Space.xs) {
            suggestionList
            if let words = voiceLine {
                Text(words).textRole(.footnote).foregroundStyle(Tone.secondary).padding(.horizontal, Space.m)
                    .onTapGesture { voice.clearFailure() }
                    .accessibilityIdentifier("composer.voiceLine")
            }
            if voice.phase.isActive {
                VoiceRecorderBar(
                    levels: voice.levels, startedAt: voice.startedAt, heard: voice.heard, transcribing: voice.phase == .transcribing,
                    cancel: { voice.cancel() }, confirm: { voice.confirm() }
                )
                .padding(.horizontal, Space.xxs)
                .interactiveGlass(in: rounded(Radius.composer))
            } else {
                fieldPill(compact: compact)
            }
        }
        .haptic(.medium, trigger: voice.phase == .recording)
        .onChange(of: scenePhase) { _, phase in if phase == .background { voice.leftTheScreen() } }
        .animation(motionAnimation(.message, reduceMotion: reduceMotion), value: layout)
        .modifier(ComposerPickers(picking: $picking, photoItems: $photoItems, add: add))
    }

    /// The line over the pill: why dictation cannot start, or why the last recording ended early.
    private var voiceLine: String? {
        if case .failed(let words) = voice.phase { return words }
        return voice.notice
    }

    private func fieldPill(compact: Bool) -> some View {
            VStack(alignment: .leading, spacing: 0) {
                if !attachments.isEmpty { attachmentStrip }
                // One field in one place whatever the layout: a field made anew when the composer grows on focus would
                // take the focus away again (found in the N1 end-to-end check).
                HStack(alignment: .bottom, spacing: 0) {
                    if compact { plusMenu }
                    TextField(placeholder, text: $text, axis: .vertical)
                        .textRole(.body).foregroundStyle(Tone.text).tint(Tone.text)
                        .lineLimit(compact ? 1...4 : 1...6)
                        .focused($isFocused)
                        .accessibilityIdentifier("composer.field")
                        .padding(.vertical, compact ? Space.s : 0)
                        .padding(.horizontal, compact ? 0 : Space.m - Space.xxs)
                        .padding(.top, compact ? 0 : Space.m)
                    if compact {
                        micButton
                        ComposerButton(buttons: buttons, perform: perform)
                    }
                }
                if !compact {
                    HStack(spacing: 0) {
                        plusMenu
                        Spacer(minLength: 0)
                        modelChip
                        micButton
                        ComposerButton(buttons: buttons, perform: perform)
                    }
                }
            }
            .padding(.horizontal, Space.xxs)
            .interactiveGlass(in: rounded(Radius.composer))
    }
}

/// The + menu's pickers: photos, files, the camera and the scanner.
private struct ComposerPickers: ViewModifier {
    @Binding var picking: ComposerView.Picker?
    @Binding var photoItems: [PhotosPickerItem]
    let add: (UIImage) -> Void

    func body(content: Content) -> some View {
        content
        .photosPicker(isPresented: Binding(get: { picking == .photos }, set: { if !$0 { picking = nil } }), selection: $photoItems, maxSelectionCount: 6, matching: .images)
        .onChange(of: photoItems) { _, items in
            Task {
                for item in items {
                    if let data = try? await item.loadTransferable(type: Data.self), let image = ImageSizing.decoded(data, longest: ImageSizing.upload) { add(image) }
                }
                photoItems = []
            }
        }
        .fileImporter(isPresented: Binding(get: { picking == .files }, set: { if !$0 { picking = nil } }), allowedContentTypes: [.image], allowsMultipleSelection: true) { result in
            guard case .success(let urls) = result else { return }
            for url in urls {
                let opened: Bool = url.startAccessingSecurityScopedResource()
                defer { if opened { url.stopAccessingSecurityScopedResource() } }
                if let data = try? Data(contentsOf: url), let image = ImageSizing.decoded(data, longest: ImageSizing.upload) { add(image) }
            }
        }
        .fullScreenCover(item: Binding(get: { picking == .camera || picking == .scan ? picking : nil }, set: { picking = $0 })) { which in
            if which == .camera {
                CameraPicker { image in
                    if let image { add(image) }
                    picking = nil
                }
                .ignoresSafeArea()
            } else {
                DocumentScanner { pages in
                    pages.forEach(add)
                    picking = nil
                }
                .ignoresSafeArea()
            }
        }
    }
}

extension ComposerView {
    /// Keeps a picture for the message at the upload size (a camera's 12 MP photo goes as about 1,600 px, not as itself),
    /// with a preview of the composer's size.
    fileprivate func add(_ image: UIImage) {
        let sized: UIImage = ImageSizing.fitted(image, longest: ImageSizing.upload)
        guard let jpeg: Data = sized.jpegData(compressionQuality: ImageSizing.quality) else { return }
        attachments.append(ComposerAttachment(id: UUID(), jpeg: jpeg, preview: ImageSizing.fitted(sized, longest: ImageSizing.preview)))
    }

    private var plusMenu: some View {
        Menu {
            Button(String(localized: "Photos", comment: "Composer + menu: picks photos"), systemImage: "photo.on.rectangle") { picking = .photos }
            if UIImagePickerController.isSourceTypeAvailable(.camera) {
                Button(String(localized: "Camera", comment: "Composer + menu: takes a photo"), systemImage: "camera") { picking = .camera }
            }
            Button(String(localized: "Files", comment: "Composer + menu: picks image files"), systemImage: "folder") { picking = .files }
            if VNDocumentCameraViewController.isSupported {
                Button(String(localized: "Scan a document", comment: "Composer + menu: scans paper pages"), systemImage: "doc.viewfinder") { picking = .scan }
            }
            if UIPasteboard.general.hasImages {
                Button(String(localized: "Paste image", comment: "Composer + menu: adds the image on the clipboard"), systemImage: "doc.on.clipboard") {
                    UIPasteboard.general.images?.forEach(add)
                }
            }
        } label: {
            Symbol(name: "plus", context: .toolbar).foregroundStyle(Tone.secondary).frame(width: Metric.hit, height: Metric.hit).contentShape(Rectangle())
        }
        .accessibilityLabel(String(localized: "Attach", comment: "VoiceOver: the composer's button that adds a photo or file"))
    }

    @ViewBuilder fileprivate var modelChip: some View {
        if let model, let openSettings {
            Button(action: openSettings) {
                HStack(spacing: Space.xxs) {
                    Text(model.label).textRole(.footnote).fontWeight(.medium).foregroundStyle(Tone.text).lineLimit(1)
                    if let effort = model.effort { Text(effortTitle(effort)).textRole(.footnote).foregroundStyle(Tone.tertiary).lineLimit(1) }
                    Symbol(name: "chevron.down", context: .status).foregroundStyle(Tone.glyph)
                }
                .padding(.horizontal, Space.xs)
                .frame(minHeight: Metric.hit)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel(String(localized: "Model, \(chipWords(model))", comment: "VoiceOver: the composer's model menu; the argument is the model and effort"))
            .accessibilityHint(String(localized: "Opens the chat's settings", comment: "VoiceOver hint of the composer's model chip"))
            .accessibilityIdentifier("composer.model")
        }
    }

    private func chipWords(_ choice: ModelChoice) -> String {
        [choice.label, choice.effort.map(effortTitle)].compactMap { $0 }.joined(separator: " · ")
    }

    /// The microphone: the pill becomes the recorder; ✓ adds the words after what was typed.
    fileprivate var micButton: some View {
        ComposerIconButton(systemImage: "mic", label: String(localized: "Dictate", comment: "VoiceOver: the composer's microphone button")) {
            let defaults: UserDefaults = .standard
            voice.start(
                VoiceInput.Start(
                    on: TranscribeOn.current(defaults), mac: app.engine?.api, locale: DictationLanguage.current(defaults).locale(app: Locale.current),
                    order: MicSlot.read(defaults),
                    deliver: { words in text = DictationText.merged(base: text, heard: words) },
                    learned: { port in MicSlot.write(learning(port, into: MicSlot.read(defaults)), defaults) }))
        }
        .accessibilityIdentifier("composer.mic")
    }

    private var attachmentStrip: some View {
        ScrollView(.horizontal) {
            HStack(spacing: Space.xs) {
                ForEach(attachments) { attachment in
                    Image(uiImage: attachment.preview).resizable().scaledToFill().frame(width: 56, height: 56).clipShape(rounded(Radius.inner))
                        .overlay(alignment: .topTrailing) {
                            Button { attachments.removeAll { $0.id == attachment.id } } label: {
                                Image(systemName: "xmark.circle.fill").symbolRenderingMode(.palette).foregroundStyle(Tone.onAction, Tone.action)
                                    .frame(width: Metric.hit, height: Metric.hit, alignment: .topTrailing)
                            }
                            .buttonStyle(.plain)
                            .offset(x: 8, y: -8)
                            .accessibilityLabel(String(localized: "Remove the photo", comment: "VoiceOver: removes a photo from the message"))
                        }
                        .accessibilityLabel(String(localized: "Photo", comment: "VoiceOver: a photo added to the message"))
                }
            }
            .padding(.horizontal, Space.m)
            .padding(.top, Space.m)
        }
        .scrollIndicators(.hidden)
    }

    /// The one panel above the composer (§22): a quiet uppercase header per section, rows of icon · name · description,
    /// filtered as the owner types; a tap puts the token in the text.
    @ViewBuilder private var suggestionList: some View {
        let sections: [PickerSection] = pickerTrigger(in: text).map(suggestions.sections) ?? []
        if !sections.isEmpty {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.xs) {
                    ForEach(sections) { section in
                        Text(pickerHeader(section.kind)).textRole(.label).fontWeight(.semibold).textCase(.uppercase).tracking(1.2)
                            .foregroundStyle(Tone.tertiary).padding(.horizontal, Space.m).padding(.top, Space.xs)
                            .accessibilityAddTraits(.isHeader)
                        ForEach(section.items) { item in
                            Button {
                                text = completing(text, with: item.token)
                            } label: {
                                HStack(alignment: .firstTextBaseline, spacing: Space.s) {
                                    Symbol(name: pickerSymbol(item.kind), context: .tool).foregroundStyle(Tone.secondary).frame(width: Metric.iconSlot)
                                    Text(item.kind == .command || item.kind == .skill ? item.token : item.name).textRole(.body).foregroundStyle(Tone.text)
                                        .lineLimit(1).layoutPriority(1)
                                    Text(item.detail).textRole(.footnote).foregroundStyle(Tone.tertiary).lineLimit(1)
                                    Spacer(minLength: 0)
                                }
                                .padding(.horizontal, Space.m)
                                .frame(minHeight: Metric.hit)
                                .contentShape(Rectangle())
                            }
                            .buttonStyle(.plain)
                            .accessibilityElement(children: .combine)
                            .accessibilityIdentifier("picker.row")
                        }
                    }
                }
                .padding(.vertical, Space.xs)
            }
            .scrollBounceBehavior(.basedOnSize)
            // Bounded, so the composer under it stays above the keyboard.
            .frame(maxHeight: 230)
            .fixedSize(horizontal: false, vertical: true)
            .background(Tone.card, in: rounded(Radius.bubble))
        }
    }
}

/// The bar over the composer while a queued message is edited (§20): "Editing queued message · Cancel".
struct EditingQueuedBar: View {
    let cancel: () -> Void

    var body: some View {
        HStack(spacing: Space.xs) {
            Symbol(name: "pencil", context: .tool).foregroundStyle(Tone.secondary)
            Text(String(localized: "Editing queued message", comment: "Bar over the composer while a queued message is edited")).textRole(.footnote).foregroundStyle(Tone.secondary)
            Spacer(minLength: 0)
            Button(String(localized: "Cancel", comment: "Edit sheet toolbar: discards the edits"), action: cancel)
                .textRole(.body).fontWeight(.medium).foregroundStyle(Tone.text).frame(minHeight: Metric.hit)
                .accessibilityIdentifier("composer.cancelEdit")
        }
        .padding(.horizontal, Space.m)
    }
}

/// The system camera.
struct CameraPicker: UIViewControllerRepresentable {
    let done: (UIImage?) -> Void

    func makeUIViewController(context: Context) -> UIImagePickerController {
        let picker: UIImagePickerController = UIImagePickerController()
        picker.sourceType = .camera
        picker.delegate = context.coordinator
        return picker
    }

    func updateUIViewController(_ controller: UIImagePickerController, context: Context) {}
    func makeCoordinator() -> Coordinator { Coordinator(done: done) }

    final class Coordinator: NSObject, UIImagePickerControllerDelegate, UINavigationControllerDelegate {
        let done: (UIImage?) -> Void
        init(done: @escaping (UIImage?) -> Void) { self.done = done }
        func imagePickerController(_ picker: UIImagePickerController, didFinishPickingMediaWithInfo info: [UIImagePickerController.InfoKey: Any]) {
            done(info[.originalImage] as? UIImage)
        }
        func imagePickerControllerDidCancel(_ picker: UIImagePickerController) { done(nil) }
    }
}

/// The system document scanner: each page as an image.
struct DocumentScanner: UIViewControllerRepresentable {
    let done: ([UIImage]) -> Void

    func makeUIViewController(context: Context) -> VNDocumentCameraViewController {
        let scanner: VNDocumentCameraViewController = VNDocumentCameraViewController()
        scanner.delegate = context.coordinator
        return scanner
    }

    func updateUIViewController(_ controller: VNDocumentCameraViewController, context: Context) {}
    func makeCoordinator() -> Coordinator { Coordinator(done: done) }

    final class Coordinator: NSObject, VNDocumentCameraViewControllerDelegate {
        let done: ([UIImage]) -> Void
        init(done: @escaping ([UIImage]) -> Void) { self.done = done }
        func documentCameraViewController(_ controller: VNDocumentCameraViewController, didFinishWith scan: VNDocumentCameraScan) {
            done((0..<scan.pageCount).map { scan.imageOfPage(at: $0) })
        }
        func documentCameraViewControllerDidCancel(_ controller: VNDocumentCameraViewController) { done([]) }
        func documentCameraViewController(_ controller: VNDocumentCameraViewController, didFailWithError error: Error) { done([]) }
    }
}

/// What the floating capsule says about the chat's work; a segment exists only while it has something to say.
struct WorkingSummary: Equatable, Sendable {
    /// The chat's state in one word with the clock (item 1769: Working, never what the run does, which the
    /// transcript's live line says).
    let status: ChatStatus?
    /// "Waiting on 2 helpers" in the status's place while the main run only waits for its helpers; `nil` while it takes
    /// a step of its own ("Working" and the turn's clock).
    let activity: String?
    /// Since when the run works: the capsule's clock ticks every second from it, whether events come or not; `nil`
    /// while it waits on its helpers.
    let since: Date?
    let queued: Int
    /// The helpers the run started that ended, and all of them ("2/3" in the people segment).
    let helpersDone: Int
    let helpersTotal: Int
    /// The plan of the chat's newest run (item 1880); `nil` when that run keeps none.
    let plan: PlanProgress?
    /// The run can be stopped from the capsule.
    let canStop: Bool

    var isEmpty: Bool { status == nil && queued == 0 && helpersTotal == 0 && plan == nil }
}

/// The working capsule and the scroll-to-bottom circle in one glass container, so the two merge (plan §4.1, R-a §3).
/// The capsule is one pill for everything about the chat's work, thin dividers between its segments (item 1880, the
/// owner's reference app): "Working 1m 50s" or "Waiting on 2 helpers" | Stop | Plan 1/5 | queued | helpers done/total.
/// Each segment is its own button, at least 44 pt each way. It stays one line at the default text size: in words where
/// they fit, else Stop, the plan and the queue as their glyphs with the numbers and "Working" as its spinner before the
/// clock (the smallest iPhone with every segment shown). At the accessibility sizes the segments stack, as glyphs.
struct FloatingWorkingControl: View {
    let summary: WorkingSummary
    let showsScrollToBottom: Bool
    let openStatus: () -> Void
    let stop: () -> Void
    let openPlan: () -> Void
    let openQueue: () -> Void
    let openHelpers: () -> Void
    let scrollToBottom: () -> Void
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize: DynamicTypeSize

    var body: some View {
        GlassEffectContainer(spacing: 12) {
            HStack(spacing: Space.xs) {
                if !summary.isEmpty {
                    if compact {
                        capsule(glyphs: true)
                    } else {
                        // One line always: in words where they fit, else Stop, the plan and the queue as their glyphs and
                        // the status as its spinner and clock (the smallest iPhone with every segment shown).
                        ViewThatFits(in: .horizontal) {
                            capsule(glyphs: false)
                            capsule(glyphs: true)
                        }
                    }
                }
                if showsScrollToBottom {
                    Button(action: scrollToBottom) {
                        Symbol(name: "chevron.down", context: .toolbar)
                            .foregroundStyle(Tone.text)
                            .frame(width: Metric.hit, height: Metric.hit)
                            .contentShape(Circle())
                    }
                    .buttonStyle(.plain)
                    .interactiveGlass(in: Circle())
                    .accessibilityLabel(String(localized: "Scroll to the newest message", comment: "VoiceOver: the round button over the composer"))
                }
            }
        }
    }

    private var compact: Bool { dynamicTypeSize.isAccessibilitySize }

    /// The pill; with `glyphs`, Stop, the plan and the queue are their glyphs with the numbers, "Working" is its spinner
    /// before the clock, and the segments sit closer.
    private func capsule(glyphs: Bool) -> some View {
        let inset: CGFloat = glyphs ? Space.xs : Space.s
        let layout: AnyLayout = compact ? AnyLayout(VStackLayout(alignment: .leading, spacing: 0)) : AnyLayout(HStackLayout(spacing: 0))
        return layout {
            if let status = summary.status {
                // The status and its clock are drawn every second, whether events come or not: a clock drawn with
                // the screen froze between events and jumped when they came (the owner's report of 2026-11-25). The
                // ticks are counted from the run's start, never from `.now`: a schedule from `.now` began again at
                // every drawing of the screen, so words streaming faster than once a second held the clock still.
                TimelineView(.periodic(from: summary.since ?? .distantPast, by: 1)) { context in
                    let clock: String? = summary.since.map { elapsedWords(from: $0, to: context.date) }
                    segment(action: openStatus, label: [summary.activity ?? status.word, clock].compactMap { $0 }.joined(separator: ", "), inset: inset) {
                        HStack(spacing: Space.xxs + 1) {
                            if let activity = summary.activity {
                                ActivityWord(text: activity)
                            } else if glyphs, clock != nil, status == .working {
                                ProgressView().controlSize(.mini).tint(Tone.secondary)
                            } else {
                                StatusWord(status: status)
                            }
                            if let clock {
                                Text(clock).textRole(.label).foregroundStyle(Tone.tertiary).tabularNumbers().lineLimit(1).fixedSize()
                            }
                        }
                    }
                    .accessibilityIdentifier("capsule.status")
                }
            }
            if summary.canStop {
                if summary.status != nil { divider }
                segment(action: stop, label: String(localized: "Stop", comment: "Chat toolbar: stops the run at work"), inset: inset) {
                    if glyphs {
                        Image(systemName: "stop.fill").textRole(.label).foregroundStyle(Tone.text)
                    } else {
                        Text(String(localized: "Stop", comment: "Chat toolbar: stops the run at work")).textRole(.label).fontWeight(.medium)
                            .foregroundStyle(Tone.text).lineLimit(1).fixedSize()
                    }
                }
                .accessibilityIdentifier("capsule.stop")
            }
            if let plan = summary.plan {
                if summary.status != nil || summary.canStop { divider }
                segment(
                    action: openPlan,
                    label: String(localized: "Plan, \(plan.done) of \(plan.total) steps done", comment: "VoiceOver: the plan chip beside the working capsule; the arguments are the steps done and all steps"),
                    inset: inset
                ) {
                    Label {
                        Text(verbatim: glyphs ? "\(plan.done)/\(plan.total)" : String(localized: "Plan \(plan.done)/\(plan.total)", comment: "The plan chip beside the working capsule; the arguments are the steps done and all steps"))
                            .lineLimit(1).fixedSize()
                    } icon: {
                        Image(systemName: plan.done == plan.total ? "checklist.checked" : "checklist")
                    }
                    .textRole(.label).fontWeight(.medium).tabularNumbers()
                    .foregroundStyle(Tone.secondary)
                }
                .accessibilityIdentifier("capsule.plan")
            }
            if summary.queued > 0 {
                if summary.status != nil || summary.canStop || summary.plan != nil { divider }
                segment(action: openQueue, label: String(localized: "\(summary.queued) queued", comment: "VoiceOver: the capsule segment of queued messages; the argument is their count"), inset: inset) {
                    Label { Text(verbatim: "\(summary.queued)").lineLimit(1).fixedSize() } icon: { Image(systemName: "list.number") }.textRole(.label).fontWeight(.medium)
                        .tabularNumbers().foregroundStyle(Tone.secondary)
                }
                .accessibilityIdentifier("capsule.queue")
            }
            if summary.helpersTotal > 0 {
                if summary.status != nil || summary.canStop || summary.plan != nil || summary.queued > 0 { divider }
                segment(
                    action: openHelpers,
                    label: String(localized: "Helpers, \(summary.helpersDone) of \(summary.helpersTotal) done", comment: "VoiceOver: the capsule segment of the run's helpers; the arguments are the helpers that ended and all of them"),
                    inset: inset
                ) {
                    Label { Text(verbatim: "\(summary.helpersDone)/\(summary.helpersTotal)").lineLimit(1).fixedSize() } icon: { Image(systemName: "person.2") }
                        .textRole(.label).fontWeight(.medium).tabularNumbers().foregroundStyle(Tone.secondary)
                }
                .accessibilityIdentifier("capsule.helpers")
            }
        }
        .padding(.horizontal, Space.xxs)
        .interactiveGlass(in: compact ? AnyShape(rounded(Radius.bubble)) : AnyShape(Capsule()))
    }

    @ViewBuilder private var divider: some View {
        if !compact {
            Rectangle().fill(Tone.separator).frame(width: 1, height: 14).accessibilityHidden(true)
        }
    }

    private func segment<Label: View>(action: @escaping () -> Void, label: String, inset: CGFloat, @ViewBuilder content: () -> Label)
        -> some View
    {
        Button(action: action) {
            content()
                .padding(.horizontal, inset)
                .frame(minWidth: Metric.hit, minHeight: Metric.hit)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(label)
        .accessibilityAddTraits(.isButton)
    }
}

/// What a run does now, as the capsule's status word: the working spinner and one short line.
struct ActivityWord: View {
    let text: String

    var body: some View {
        HStack(spacing: Space.xxs + 1) {
            ProgressView().controlSize(.mini).tint(Tone.secondary)
            Text(text).textRole(.label).fontWeight(.medium).lineLimit(1).truncationMode(.tail).frame(maxWidth: 200, alignment: .leading)
        }
        .foregroundStyle(ChatStatus.working.tone.color)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(text)
    }
}
