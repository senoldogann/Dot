import DotEngine
import SwiftUI
import UIKit

// Following a run live inside its chat (items 1839-1840): while the chat's run drives its browser or the Mac's desktop,
// a mini player floats at the top of the chat with the picture and what the run does now; a tap opens it full screen,
// where the owner can take the browser over ("Take over", tap and type on the page from the phone) and hand it back.

/// The live picture of one run: the browser's cast or the Mac's screen, and the owner's hold of its page.
@MainActor
@Observable
final class LiveFollow {
    let run: String
    let surface: RunSurface
    let cast: CastFeed?
    let screen: ScreenFeed?
    let control: RunControl

    init(run: String, surface: RunSurface) {
        self.run = run
        self.surface = surface
        cast = surface == .browser ? CastFeed(run: run) : nil
        screen = surface == .desktop ? ScreenFeed() : nil
        control = RunControl(run: run)
    }

    var state: FeedState { cast?.state ?? screen?.state ?? .idle }
    var picture: UIView { cast?.picture ?? screen?.picture ?? UIView() }
    var pictureSize: CGSize { cast?.pictureSize ?? screen?.pictureSize ?? CGSize(width: 16, height: 10) }
    var fps: Double { cast?.fps ?? screen?.fps ?? 0 }
    var running: Bool { cast?.running ?? screen?.running ?? false }

    func start(api: DaemonAPI) {
        control.use(api: api)
        guard !running else { return }
        if let cast { cast.start(api: api) }
        if let screen { screen.start(api: api) }
    }

    func stop() {
        cast?.stop()
        screen?.stop()
    }
}

/// What a chat needs of its run to follow it live.
struct LiveRunFacts: Equatable {
    let run: String
    let surface: RunSurface
    let active: Bool
    let hold: RunHold
    let action: LiveAction?
    /// The call at work that is not the page's (a command, a file), as the chat's work log names it (item 1996).
    let workCall: HelperStep?
    /// The words between calls: thinking, writing.
    let workWords: String?
}

/// The facts of the chat's newest run of its own that used a browser or the desktop, while it works.
@MainActor
func liveRunFacts(_ chat: ChatProjection?) -> LiveRunFacts? {
    guard let chat, let run = chat.view?.runs.last(where: { $0.parentRunId == nil && $0.isActive }) else { return nil }
    let events: [EventRecord] = chat.runs[run.id]?.events ?? []
    guard let surface: RunSurface = runSurface(events) else { return nil }
    var words: String?
    var call: HelperStep?
    switch activity(of: chat, run: run.id) {
    case .thinking: words = String(localized: "Thinking", comment: "Capsule: the run's model is thinking")
    case .writing: words = String(localized: "Writing", comment: "Capsule: the run's model is writing its words")
    case .tool(let step): call = step
    case nil: words = nil
    }
    return LiveRunFacts(
        run: run.id, surface: surface, active: true, hold: runHold(events), action: liveAction(events), workCall: call, workWords: words)
}

/// Adds the live follow to a chat: the floating mini player, the header's Watch live, and the full screen.
struct LiveFollowModifier: ViewModifier {
    let thread: String
    @Environment(AppModel.self) private var model: AppModel
    @Environment(\.scenePhase) private var scenePhase: ScenePhase
    @Environment(\.accessibilityReduceMotion) private var reduceMotion: Bool
    @State private var follow: LiveFollow?
    @State private var facts: LiveRunFacts?
    @State private var fullScreen: Bool = false
    @AppStorage("liveMiniCollapsed") private var collapsed: Bool = false
    @AppStorage("liveMiniLeading") private var leading: Bool = false
    @State private var drag: CGSize = .zero

    func body(content: Content) -> some View {
        // Only with a daemon to read the picture from: a chat drawn from a fixture (no daemon) shows no player that would
        // wait forever.
        let current: LiveRunFacts? = model.settingsAPI == nil ? nil : liveRunFacts(model.chats[thread])
        return content
            .overlay(alignment: leading ? .topLeading : .topTrailing) {
                if let follow, current != nil, !fullScreen {
                    mini(follow, facts: current)
                        .padding(.horizontal, Space.s)
                        .padding(.top, Space.xs)
                        .offset(drag)
                        .gesture(dragToSide)
                        .transition(.scale(scale: 0.85, anchor: leading ? .topLeading : .topTrailing).combined(with: .opacity))
                }
            }
            .animation(motionAnimation(.enter, reduceMotion: reduceMotion), value: current?.run)
            .toolbar {
                if current != nil {
                    ToolbarItem(placement: .topBarTrailing) {
                        Button(String(localized: "Watch live", comment: "Chat header: opens the live picture of what the run does"), systemImage: "play.display") {
                            fullScreen = true
                        }
                        .accessibilityIdentifier("chat.watchLive")
                    }
                }
            }
            .fullScreenCover(isPresented: $fullScreen) {
                if let follow {
                    RunLiveScreen(follow: follow, facts: facts, close: { fullScreen = false })
                }
            }
            .onChange(of: current, initial: true) { _, now in adopt(now) }
            .onChange(of: scenePhase) { _, phase in
                if phase == .active { startIfShown() } else { follow?.stop() }
            }
            .onChange(of: collapsed) { _, _ in startIfShown() }
            .onChange(of: fullScreen) { _, _ in startIfShown() }
            .onDisappear {
                if !fullScreen { follow?.stop() }
            }
    }

    private func adopt(_ now: LiveRunFacts?) {
        if let now {
            facts = now
        } else if let last = facts, last.active {
            // Ended: the full screen, if open, keeps its last frame and offers nothing to take over.
            facts = LiveRunFacts(run: last.run, surface: last.surface, active: false, hold: .free, action: nil, workCall: nil, workWords: nil)
        }
        if let now, follow?.run != now.run || follow?.surface != now.surface {
            follow?.stop()
            follow = LiveFollow(run: now.run, surface: now.surface)
            startIfShown()
        }
        // After the follow is made: a run held already (the web's Use it here, this phone before it was reopened) shows
        // Hand back and takes the owner's touches at once.
        follow?.control.follow(now?.hold ?? .free, active: now != nil)
        if now == nil, !fullScreen {
            // The run ended: the picture goes with it (the full screen keeps its last frame and says so).
            follow?.stop()
            follow = nil
        }
    }

    /// The picture is read while it shows: the mini player open, or the full screen.
    private func startIfShown() {
        guard let follow, let api = model.settingsAPI else { return }
        if (fullScreen || !collapsed) && scenePhase == .active {
            follow.start(api: api)
        } else {
            follow.stop()
        }
    }

    @ViewBuilder private func mini(_ follow: LiveFollow, facts: LiveRunFacts?) -> some View {
        if collapsed {
            LiveMiniPill(surface: follow.surface, live: follow.state == .live) { collapsed = false }
        } else {
            LiveMiniPlayer(follow: follow, facts: facts, open: { fullScreen = true }, collapse: { collapsed = true })
        }
    }

    private var dragToSide: some Gesture {
        DragGesture(minimumDistance: 8)
            .onChanged { value in drag = value.translation }
            .onEnded { value in
                let across: Bool = leading ? value.predictedEndTranslation.width > 80 : value.predictedEndTranslation.width < -80
                withAnimation(reduceMotion ? nil : .spring(response: 0.35, dampingFraction: 0.82)) {
                    if across { leading.toggle() }
                    drag = .zero
                }
            }
    }
}

extension View {
    /// The live follow of the chat `thread` (items 1839-1840).
    func liveFollow(thread: String) -> some View { modifier(LiveFollowModifier(thread: thread)) }
}

// MARK: The mini player

/// Hosts a picture view as it is (no zoom), filling its frame with the picture's aspect.
struct PictureHost: UIViewRepresentable {
    let picture: UIView

    func makeUIView(context: Context) -> UIView {
        let host: UIView = UIView()
        host.backgroundColor = .black
        host.clipsToBounds = true
        host.isUserInteractionEnabled = false
        // The picture changes with every frame: kept out of the accessibility tree, which the player's own label names.
        host.accessibilityElementsHidden = true
        return host
    }

    func updateUIView(_ host: UIView, context: Context) {
        if picture.superview !== host {
            picture.removeFromSuperview()
            picture.frame = host.bounds
            picture.autoresizingMask = [.flexibleWidth, .flexibleHeight]
            host.addSubview(picture)
        }
    }
}

/// The floating picture over the chat: the run's page or the Mac's screen, a live dot, and what the run does now.
struct LiveMiniPlayer: View {
    let follow: LiveFollow
    let facts: LiveRunFacts?
    let open: () -> Void
    let collapse: () -> Void
    @Environment(\.horizontalSizeClass) private var sizeClass: UserInterfaceSizeClass?

    private var width: CGFloat { sizeClass == .regular ? 260 : 176 }

    var body: some View {
        let size: CGSize = follow.pictureSize
        let height: CGFloat = (width * size.height / max(size.width, 1)).rounded()
        ZStack(alignment: .bottom) {
            PictureHost(picture: follow.picture)
                .opacity(follow.state == .live ? 1 : 0.5)
            switch follow.state {
            case .live:
                EmptyView()
            case .stopped, .ended:
                // Why the picture is not coming, as the full screen says it (where Try again is).
                Text(feedLine(follow.state, connecting: "", fps: 0, quality: nil))
                    .textRole(.micro).fontWeight(.medium).foregroundStyle(.white).multilineTextAlignment(.center)
                    .lineLimit(3).padding(Space.s).frame(maxWidth: .infinity, maxHeight: .infinity)
            default:
                ProgressView().tint(.white).frame(maxWidth: .infinity, maxHeight: .infinity)
            }
            // A shade under the caption only, so the page keeps its own colours.
            LinearGradient(colors: [.clear, .black.opacity(0.5)], startPoint: UnitPoint(x: 0.5, y: 0.62), endPoint: .bottom)
                .allowsHitTesting(false)
            ActionCaption(call: facts?.action?.call ?? facts?.workCall, fallback: facts?.workWords, compact: true)
                .padding(6)
        }
        .frame(width: width, height: min(max(height, 80), width * 1.4))
        .overlay(alignment: .topLeading) {
            HStack(spacing: 4) {
                LiveDot(live: follow.state == .live)
                Text(follow.control.controlling
                    ? String(localized: "You", comment: "Mini player: the owner holds the run's page")
                    : String(localized: "Live", comment: "Live picture status: frames are coming"))
                    .textRole(.micro).fontWeight(.semibold).foregroundStyle(.white)
            }
            .padding(.horizontal, 6).padding(.vertical, 3)
            .background(.black.opacity(0.45), in: Capsule())
            .padding(6)
        }
        .overlay(alignment: .topTrailing) {
            Button(action: collapse) {
                Image(systemName: "chevron.up")
                    .font(.system(.caption2, weight: .bold))
                    .foregroundStyle(.white)
                    .frame(width: 22, height: 22)
                    .background(.black.opacity(0.45), in: Circle())
                    .frame(width: Metric.hit, height: Metric.hit, alignment: .topTrailing)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .padding(4)
            .accessibilityLabel(String(localized: "Hide the live picture", comment: "Mini player: folds it into a small Live pill"))
        }
        .clipShape(rounded(Radius.inner))
        .shadow(color: .black.opacity(0.28), radius: 14, y: 6)
        .contentShape(rounded(Radius.inner))
        .onTapGesture(perform: open)
        .environment(\.colorScheme, .dark)
        .accessibilityElement(children: .contain)
        .accessibilityLabel(
            follow.surface == .browser
                ? String(localized: "Dot's browser, live", comment: "VoiceOver: the mini player of the run's browser")
                : String(localized: "Your Mac's screen, live", comment: "VoiceOver: the mini player of the Mac's screen"))
        .accessibilityAction(named: String(localized: "Open full screen", comment: "VoiceOver action: opens the live picture full screen"), open)
        .accessibilityIdentifier("live.mini")
    }
}

/// The mini player folded: a small glass pill that opens it again.
struct LiveMiniPill: View {
    let surface: RunSurface
    let live: Bool
    let expand: () -> Void

    var body: some View {
        Button(action: expand) {
            HStack(spacing: Space.xs) {
                Image(systemName: surface == .browser ? "globe" : "desktopcomputer").font(.system(.footnote, weight: .medium))
                Text(String(localized: "Live", comment: "Live picture status: frames are coming")).textRole(.label).fontWeight(.semibold)
                Image(systemName: "chevron.down").font(.system(.caption2, weight: .bold))
            }
            .foregroundStyle(Tone.text)
            .padding(.horizontal, Space.m)
            .frame(minHeight: 34)
            .contentShape(Capsule())
        }
        .buttonStyle(.plain)
        .interactiveGlass(in: Capsule())
        .frame(minHeight: Metric.hit)
        .accessibilityLabel(String(localized: "Show the live picture", comment: "Mini pill: opens the folded mini player"))
        .accessibilityIdentifier("live.pill")
    }
}

// MARK: Full screen

/// A run's live picture over the whole screen, with what it does, and the takeover of its browser.
struct RunLiveScreen: View {
    let follow: LiveFollow
    let facts: LiveRunFacts?
    let close: () -> Void
    @Environment(AppModel.self) private var model: AppModel
    @State private var typing: Bool = false
    @State private var fitToken: Int = 0

    private var connecting: String {
        follow.surface == .browser
            ? String(localized: "Waiting for the page", comment: "Live picture: the first frame of the run's browser has not come (web computer.js)")
            : String(localized: "Connecting to the Mac's screen…", comment: "Watch: the live screen is being opened (web watch.connecting)")
    }

    private var title: String {
        follow.surface == .browser
            ? String(localized: "Dot's browser", comment: "Live viewer title: the run's browser")
            : model.macName ?? String(localized: "Your Mac", comment: "Watch: title when the Mac's name is not known")
    }

    private var status: String {
        let line: String = feedLine(follow.state, connecting: connecting, fps: follow.fps, quality: follow.screen?.quality)
        guard follow.state == .live, let host = follow.cast.flatMap({ URL(string: $0.address)?.host() }) else { return line }
        return "\(line) · \(host)"
    }

    var body: some View {
        LiveViewer(
            title: title, state: follow.state, status: status, connecting: connecting, retry: retry, picture: follow.picture,
            pictureSize: follow.pictureSize, control: follow.control.controlling ? follow.control : nil, fitToken: fitToken, menu: [], close: close
        ) {
            foot
        }
        .background { KeyCatcher(active: $typing, control: follow.control).frame(width: 1, height: 1).opacity(0.01) }
        .onChange(of: follow.control.controlling) { _, controlling in
            if !controlling {
                typing = false
                fitToken += 1
            }
        }
        .haptic(.success, trigger: follow.control.feel)
    }

    @ViewBuilder private var foot: some View {
        VStack(spacing: Space.s) {
            if let refusal = follow.control.refusal {
                Notice(tone: .warn, message: refusal, dismiss: { follow.control.clearRefusal() })
            }
            if !follow.control.controlling, facts?.active ?? false {
                ActionCaption(call: facts?.action?.call ?? facts?.workCall, fallback: facts?.workWords, compact: false)
            }
            if let note = follow.control.note, follow.control.controlling {
                Text(note).textRole(.caption).fontWeight(.medium).foregroundStyle(.white)
                    .padding(.horizontal, Space.m).padding(.vertical, Space.xs)
                    .floatingGlass(in: Capsule())
                    .accessibilityAddTraits(.updatesFrequently)
            }
            if follow.surface == .browser, facts?.active ?? false, follow.state != .ended {
                controls
            }
        }
    }

    @ViewBuilder private var controls: some View {
        HStack(spacing: Space.s) {
            switch follow.control.phase {
            case .watching, .taking:
                Button {
                    guard let api = model.settingsAPI else { return }
                    Task { await follow.control.takeOver(api: api) }
                } label: {
                    Label(String(localized: "Take over", comment: "Live viewer: hold the run and use its page from the phone (web computer.js)"), systemImage: "hand.raised")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(PrimaryButtonStyle())
                .disabled(follow.control.phase == .taking)
                .accessibilityHint(
                    String(localized: "Dot waits at its next step while you tap and type on the page.", comment: "VoiceOver hint of Take over"))
                .accessibilityIdentifier("live.takeOver")
            case .held, .givingBack:
                Button {
                    typing.toggle()
                } label: {
                    Image(systemName: typing ? "keyboard.chevron.compact.down" : "keyboard")
                        .font(.system(.body, weight: .medium))
                        .frame(width: Metric.hit, height: Metric.hit)
                }
                .buttonStyle(SecondaryButtonStyle())
                .accessibilityLabel(
                    typing
                        ? String(localized: "Hide the keyboard", comment: "Live viewer: hides the keyboard that types on the page")
                        : String(localized: "Type on the page", comment: "Live viewer: shows the keyboard that types on the page"))
                .accessibilityIdentifier("live.keyboard")
                Button {
                    guard let api = model.settingsAPI else { return }
                    typing = false
                    Task { await follow.control.handBack(api: api) }
                } label: {
                    Label(String(localized: "Hand back", comment: "Live viewer: let the run go on with its page (web computer.js Return control)"), systemImage: "arrow.uturn.backward")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(PrimaryButtonStyle())
                .disabled(follow.control.phase == .givingBack)
                .accessibilityIdentifier("live.handBack")
            }
        }
    }

    private func retry() {
        guard let api = model.settingsAPI, follow.state != .ended else { return }
        follow.start(api: api)
    }
}

// MARK: The keyboard

/// An invisible view that takes the phone's keyboard while `active`, and hands what is typed to the page: text, Return
/// as Enter, ⌫ as Backspace.
struct KeyCatcher: UIViewRepresentable {
    @Binding var active: Bool
    let control: RunControl

    func makeUIView(context: Context) -> KeyCatcherView {
        let view: KeyCatcherView = KeyCatcherView()
        view.control = control
        return view
    }

    func updateUIView(_ view: KeyCatcherView, context: Context) {
        view.control = control
        if active, !view.isFirstResponder {
            view.becomeFirstResponder()
        } else if !active, view.isFirstResponder {
            view.resignFirstResponder()
        }
    }
}

final class KeyCatcherView: UIView, UIKeyInput {
    weak var control: RunControl?
    var autocorrectionType: UITextAutocorrectionType = .no
    var autocapitalizationType: UITextAutocapitalizationType = .none
    var spellCheckingType: UITextSpellCheckingType = .no
    var smartQuotesType: UITextSmartQuotesType = .no
    var smartDashesType: UITextSmartDashesType = .no
    var smartInsertDeleteType: UITextSmartInsertDeleteType = .no
    var keyboardType: UIKeyboardType = .default
    var returnKeyType: UIReturnKeyType = .default

    override var canBecomeFirstResponder: Bool { true }
    var hasText: Bool { true }

    func insertText(_ text: String) {
        MainActor.assumeIsolated {
            if text == "\n" {
                control?.key("Enter")
            } else {
                control?.type(text)
            }
        }
    }

    func deleteBackward() {
        MainActor.assumeIsolated { control?.key("Backspace") }
    }
}
