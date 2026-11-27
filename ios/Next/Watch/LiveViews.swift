import DotEngine
import SwiftUI
import UIKit

// The live views (items 1838-1840): Watch the Mac (full screen, from Activity), a run's live picture in its chat (a
// floating mini player while the chat scrolls, full screen on a tap, with what the run does over the picture), and the
// takeover of the run's page from the full screen. Black ground and dark glass chrome whatever the appearance, as a
// photo or video viewer: the picture is the content.

// MARK: Words

/// The status line of a live picture.
func feedLine(_ state: FeedState, connecting: String, fps: Double, quality: WatchQuality?) -> String {
    switch state {
    case .idle: return String(localized: "Paused", comment: "Live picture status: nothing is read (the view is away)")
    case .connecting: return connecting
    case .live:
        var parts: [String] = [String(localized: "Live", comment: "Live picture status: frames are coming")]
        if let quality {
            parts.append(
                quality == .hd
                    ? String(localized: "HD", comment: "Live picture status: the full HD quality")
                    : String(localized: "Low data", comment: "Live picture status: the phone-sized quality for a weak link"))
        }
        if fps >= 1 { parts.append(String(localized: "\(Int(fps.rounded())) fps", comment: "Live picture status: frames a second")) }
        return parts.joined(separator: " · ")
    case .reconnecting: return String(localized: "Reconnecting…", comment: "Live picture status: the stream broke and is tried again")
    case .stopped(let reason): return reason
    case .ended: return String(localized: "The task has ended", comment: "Live picture status: the run is over, its browser has no page to show (web computer.js)")
    }
}

/// A call the live picture's caption tells, as the chat's work log tells it (item 1996): the same mark and the same
/// plain words, so a step reads alike in the chat and over the picture.
func captionEntry(_ call: HelperStep) -> ToolEntry {
    ToolEntry(
        id: "", tool: call.tool, kind: ToolKind.of(call.tool), target: call.target, args: "", output: "", state: .running, images: [], startedAt: call.at,
        endedAt: nil)
}

// MARK: Pieces

/// The dot of a live picture: full ink while frames come, faint otherwise (never red, which is a failure's, D5). It does
/// not pulse: an animation that never ends keeps the app from ever being idle, for VoiceOver's focus and for UI tests.
struct LiveDot: View {
    let live: Bool

    var body: some View {
        Circle()
            .fill(live ? Color.white : Color.white.opacity(0.4))
            .frame(width: 7, height: 7)
            .accessibilityHidden(true)
    }
}

/// What the run does now, over the bottom of the picture: the call at work with the work log's mark and words, or
/// the thinking between calls, on dark glass.
struct ActionCaption: View {
    let call: HelperStep?
    let fallback: String?
    let compact: Bool

    var body: some View {
        if let words = call.map({ toolLine(captionEntry($0)) }) ?? fallback {
            HStack(spacing: compact ? 5 : Space.xs) {
                Image(systemName: call.map { toolSymbol(captionEntry($0).kind) } ?? "brain")
                    .font(compact ? .system(.caption2, weight: .semibold) : .system(.footnote, weight: .medium))
                    .symbolRenderingMode(.monochrome)
                    .contentTransition(.symbolEffect(.replace))
                Text(words)
                    .textRole(compact ? .micro : .footnote)
                    .fontWeight(.medium)
                    .lineLimit(1)
                    .truncationMode(.middle)
                    .contentTransition(.opacity)
            }
            .foregroundStyle(.white)
            .padding(.horizontal, compact ? 7 : Space.m)
            .padding(.vertical, compact ? 4 : Space.xs)
            .floatingGlass(in: Capsule())
            .animation(.easeOut(duration: MotionToken.adjust.seconds), value: words)
            .accessibilityElement(children: .combine)
            .accessibilityLabel(words)
            .accessibilityIdentifier("live.caption")
        }
    }
}

/// A round glass button of the viewer's chrome (✕, rotate, the menu), 44 pt.
struct ChromeButton: View {
    let systemImage: String
    let label: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: systemImage)
                .font(.system(.body, weight: .medium))
                .foregroundStyle(.white)
                .frame(width: 32, height: 32)
        }
        // The system's glass button: its own press effect, the 44 pt target, opaque under Reduce Transparency.
        .buttonStyle(.glass)
        .buttonBorderShape(.circle)
        .accessibilityLabel(label)
    }
}

/// Rotates the window between portrait and landscape (the picture is a wide screen).
@MainActor
func rotateWindow(to landscape: Bool) {
    guard let scene = UIApplication.shared.connectedScenes.compactMap({ $0 as? UIWindowScene }).first else { return }
    scene.requestGeometryUpdate(.iOS(interfaceOrientations: landscape ? .landscapeRight : .portrait)) { _ in }
}

/// The viewer's frame: the picture on black, the chrome on top (title, status, actions) and at the foot (the caller's),
/// hidden after a few seconds of a live picture and back on a tap.
struct LiveViewer<Foot: View>: View {
    let title: String
    let state: FeedState
    let status: String
    let connecting: String
    let retry: () -> Void
    let picture: UIView
    let pictureSize: CGSize
    let control: (any CanvasControl)?
    let fitToken: Int
    let menu: [LiveMenuItem]
    let close: () -> Void
    @ViewBuilder let foot: () -> Foot
    @State private var chromeShown: Bool = true
    @State private var hideTask: Task<Void, Never>?
    @State private var landscape: Bool = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion: Bool
    @Environment(\.accessibilityVoiceOverEnabled) private var voiceOver: Bool
    @Environment(\.verticalSizeClass) private var verticalSizeClass: UserInterfaceSizeClass?

    private var live: Bool { state == .live }

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()
            LiveCanvasView(picture: picture, pictureSize: pictureSize, control: control, onWatchTap: toggleChrome, fitToken: fitToken)
                .ignoresSafeArea()
                .opacity(state == .live || state == .idle ? 1 : 0.35)
            FeedPlaceholder(state: state, connecting: connecting, retry: retry)
            VStack(spacing: 0) {
                // The top bar hides after a few seconds of a live picture; the foot (what the run does, Take over) stays.
                ZStack(alignment: .top) {
                    if chromeShown || control != nil { top.transition(.opacity) }
                }
                .frame(minHeight: Metric.hit + Space.xs, alignment: .top)
                .animation(motionAnimation(chromeShown ? .enter : .exit, reduceMotion: reduceMotion), value: chromeShown)
                Spacer(minLength: 0)
                foot()
                    .padding(.horizontal, Space.gutter)
                    .padding(.bottom, Space.s)
            }
        }
        .environment(\.colorScheme, .dark)
        .statusBarHidden(!chromeShown)
        .persistentSystemOverlays(chromeShown ? .automatic : .hidden)
        .onChange(of: live) { _, isLive in if isLive { scheduleHide() } }
        .onAppear {
            landscape = verticalSizeClass == .compact
            if live { scheduleHide() }
        }
        .onDisappear {
            hideTask?.cancel()
            if landscape { rotateWindow(to: false) }
        }
    }

    private var top: some View {
        HStack(spacing: Space.s) {
            ChromeButton(systemImage: "xmark", label: String(localized: "Close", comment: "Sheet toolbar: closes an inspector sheet"), action: close)
            VStack(alignment: .leading, spacing: 1) {
                Text(title).textRole(.footnote).fontWeight(.semibold).foregroundStyle(.white).lineLimit(1)
                HStack(spacing: 5) {
                    LiveDot(live: live)
                    Text(status).textRole(.caption).foregroundStyle(.white.opacity(0.75)).lineLimit(1).tabularNumbers()
                        .contentTransition(.numericText())
                }
            }
            .padding(.horizontal, Space.m)
            .frame(minHeight: Metric.hit)
            .floatingGlass(in: Capsule())
            .accessibilityElement(children: .combine)
            Spacer(minLength: 0)
            ChromeButton(
                systemImage: landscape ? "rectangle.portrait.rotate" : "rectangle.landscape.rotate",
                label: landscape
                    ? String(localized: "Rotate to portrait", comment: "Live viewer: turns the picture upright")
                    : String(localized: "Rotate to landscape", comment: "Live viewer: turns the picture on its side, as wide as the screen")
            ) {
                landscape.toggle()
                rotateWindow(to: landscape)
            }
            if !menu.isEmpty {
                Menu {
                    ForEach(menu) { item in
                        Button {
                            item.action()
                        } label: {
                            if item.chosen { Label(item.title, systemImage: "checkmark") } else { Text(item.title) }
                        }
                    }
                } label: {
                    Image(systemName: "ellipsis")
                        .font(.system(.body, weight: .medium))
                        .foregroundStyle(.white)
                        .frame(width: 32, height: 32)
                }
                .buttonStyle(.glass)
                .buttonBorderShape(.circle)
                .accessibilityLabel(String(localized: "Quality", comment: "Live viewer: the menu of the picture's quality"))
            }
        }
        .padding(.horizontal, Space.gutter)
        .padding(.top, Space.xs)
    }

    private func toggleChrome() {
        chromeShown.toggle()
        if chromeShown, live { scheduleHide() }
    }

    private func scheduleHide() {
        hideTask?.cancel()
        // VoiceOver keeps the chrome: a control that hides by itself cannot be found by swiping.
        guard !voiceOver else { return }
        hideTask = Task {
            try? await Task.sleep(for: .seconds(3))
            if !Task.isCancelled { chromeShown = false }
        }
    }
}

/// One choice of the viewer's menu.
struct LiveMenuItem: Identifiable {
    let id: String
    let title: String
    let chosen: Bool
    let action: () -> Void
}

/// What stands on the picture while it has no frame: why, and Retry when it stopped.
struct FeedPlaceholder: View {
    let state: FeedState
    let connecting: String
    let retry: () -> Void

    var body: some View {
        switch state {
        case .live, .idle:
            EmptyView()
        case .connecting, .reconnecting:
            VStack(spacing: Space.m) {
                ProgressView().controlSize(.large).tint(.white)
                Text(state == .connecting ? connecting : feedLine(state, connecting: connecting, fps: 0, quality: nil))
                    .textRole(.footnote).foregroundStyle(.white.opacity(0.8))
                if case .reconnecting(let reason) = state {
                    Text(reason).textRole(.caption).foregroundStyle(.white.opacity(0.55)).multilineTextAlignment(.center).lineLimit(3)
                }
            }
            .padding(Space.xl)
            .accessibilityElement(children: .combine)
        case .stopped(let reason):
            VStack(spacing: Space.l) {
                Image(systemName: "exclamationmark.triangle").font(.system(.title2, weight: .medium)).foregroundStyle(.white).accessibilityHidden(true)
                Text(String(localized: "The screen could not be shown", comment: "Live picture: the daemon refused the stream (web watch.failed)"))
                    .textRole(.headline).foregroundStyle(.white)
                Text(reason).textRole(.footnote).foregroundStyle(.white.opacity(0.75)).multilineTextAlignment(.center)
                Button(String(localized: "Try again", comment: "Live picture: reads the stream again (web watch.retry)"), action: retry)
                    .buttonStyle(PrimaryButtonStyle())
            }
            .padding(Space.xxl)
            .accessibilityElement(children: .contain)
        case .ended:
            VStack(spacing: Space.m) {
                Image(systemName: "clock").font(.system(.title2, weight: .medium)).foregroundStyle(.white).accessibilityHidden(true)
                Text(String(localized: "The task has ended", comment: "Live picture status: the run is over, its browser has no page to show (web computer.js)"))
                    .textRole(.headline).foregroundStyle(.white)
                Text(String(localized: "Its browser's page is shown here only while a task is going on.", comment: "Live picture: why an ended run shows no page (web computer.js)"))
                    .textRole(.footnote).foregroundStyle(.white.opacity(0.75)).multilineTextAlignment(.center)
            }
            .padding(Space.xxl)
            .accessibilityElement(children: .combine)
        }
    }
}

// MARK: Watch the Mac

/// The row that opens the Mac's live screen full screen (item 1838): in Activity and in Dot's profile.
struct WatchMacRow: View {
    @State private var watching: Bool = false

    var body: some View {
        Button {
            watching = true
        } label: {
            ListRow(
                systemImage: "play.display", title: String(localized: "Watch the Mac", comment: "Activity tab row: opens the Mac's live screen"), description: nil,
                accessory: .chevron)
        }
        .buttonStyle(RowPressStyle())
        .accessibilityIdentifier("activity.watchMac")
        .fullScreenCover(isPresented: $watching) { WatchMacScreen(close: { watching = false }) }
    }
}

/// The Mac's screen, full screen, whether a task works or not (item 1838; web `views/watch.js`). Nothing is read while
/// it is closed or the app is away.
struct WatchMacScreen: View {
    let close: () -> Void
    @Environment(AppModel.self) private var model: AppModel
    @Environment(\.scenePhase) private var scenePhase: ScenePhase
    @AppStorage("watchQuality") private var choiceRaw: String = WatchQualityChoice.auto.rawValue
    @State private var feed: ScreenFeed = ScreenFeed()

    private var connecting: String { String(localized: "Connecting to the Mac's screen…", comment: "Watch: the live screen is being opened (web watch.connecting)") }

    var body: some View {
        LiveViewer(
            title: model.macName ?? String(localized: "Your Mac", comment: "Watch: title when the Mac's name is not known"),
            state: feed.state, status: feedLine(feed.state, connecting: connecting, fps: feed.fps, quality: feed.quality),
            connecting: connecting, retry: start, picture: feed.picture, pictureSize: feed.pictureSize, control: nil, fitToken: 0,
            menu: qualityMenu, close: close
        ) {
            EmptyView()
        }
        .onAppear {
            feed.choice = WatchQualityChoice(rawValue: choiceRaw) ?? .auto
            start()
        }
        .onDisappear { feed.stop() }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active { start() } else { feed.stop() }
        }
    }

    private var qualityMenu: [LiveMenuItem] {
        WatchQualityChoice.allCases.map { choice in
            LiveMenuItem(id: choice.rawValue, title: qualityTitle(choice), chosen: feed.choice == choice) {
                choiceRaw = choice.rawValue
                feed.choice = choice
            }
        }
    }

    private func start() {
        guard let api = model.settingsAPI, !feed.running else { return }
        feed.start(api: api)
    }
}

func qualityTitle(_ choice: WatchQualityChoice) -> String {
    switch choice {
    case .auto: String(localized: "Automatic quality", comment: "Live viewer menu: the link decides HD or light")
    case .hd: String(localized: "HD", comment: "Live picture status: the full HD quality")
    case .light: String(localized: "Low data (weak connection)", comment: "Live viewer menu: the phone-sized quality")
    }
}
