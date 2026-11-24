import SwiftUI

/// The address screen until a host is saved, the pairing screen until the phone has a key, then the native app. The
/// web UI stays one tap away ("Open web UI") as a full-screen fallback.
struct RootView: View {
    @Bindable var model: AppModel
    @Environment(\.scenePhase) private var scenePhase

    /// Debug builds: `-DotGallery 1|2|3` shows the design gallery instead of the app.
    private static var galleryPage: Int? {
        #if DEBUG
        let page: Int = UserDefaults.standard.integer(forKey: "DotGallery")
        return page > 0 ? page : nil
        #else
        return nil
        #endif
    }

    var body: some View {
        Group {
            if let gallery: Int = Self.galleryPage {
                #if DEBUG
                DesignGallery(page: gallery)
                #endif
            } else if let host: DaemonHost = model.host {
                if let store: NativeStore = model.native {
                    // The line that says the phone is offline stands over the screens, under the status bar, and has no height
                    // while there is nothing to say.
                    VStack(spacing: 0) {
                        ConnectionBannerView(store: store)
                        ChatListView(model: model, store: store)
                            .id(host.name)
                    }
                    .task(id: scenePhase) {
                        if scenePhase == .active { await store.followSharedAppearance() }
                    }
                    .id(ObjectIdentifier(store))
                } else {
                    PairView(model: model, host: host)
                }
            } else {
                AddressView(model: model, onSaved: nil)
            }
        }
        .tint(Theme.text)
        .background(Theme.background)
        .onOpenURL { model.open(url: $0) }
        .task {
            // Debug builds: a link given at launch, once the list is on the screen.
            if let url: URL = model.debugOpenURL {
                model.debugOpenURL = nil
                try? await Task.sleep(for: .milliseconds(800))
                model.open(url: url)
            }
        }
        .sheet(isPresented: $model.showsSettings) {
            if let store: NativeStore = model.native {
                NativeSettingsView(model: model, store: store).dotSheet()
            } else {
                SettingsView(model: model)
            }
        }
        .fullScreenCover(isPresented: $model.showsWeb) {
            if let host: DaemonHost = model.host {
                ZStack(alignment: .topTrailing) {
                    WebScreen(model: model, host: host)
                    Button("Done") { model.showsWeb = false }
                        .dotButton(.secondary)
                        .padding(.top, 54).padding(.trailing, 12)
                }
            }
        }
    }
}

/// The web UI with an overlay for a load that failed.
private struct WebScreen: View {
    @Bindable var model: AppModel
    let host: DaemonHost
    @State private var loadError: String?
    @State private var reloadCount: Int = 0

    var body: some View {
        DotWebView(
            host: host, model: model, reloadCount: reloadCount,
            onLoadError: { (message: String?) in loadError = message }
        )
        // The page runs under the status bar and the Dynamic Island as it does added to the Home Screen
        // (viewport-fit=cover): its own ground fills the top in the theme's colour and its bar keeps out of the island
        // by env(safe-area-inset-top). Kept to the safe area, the band above the page was the app's white.
        .ignoresSafeArea(.container, edges: .all)
        .overlay {
            if let loadError {
                LoadErrorView(
                    host: host, message: loadError,
                    onRetry: {
                        self.loadError = nil
                        reloadCount += 1
                    },
                    onSettings: { model.showsSettings = true })
            }
        }
    }
}

private struct LoadErrorView: View {
    let host: DaemonHost
    let message: String
    let onRetry: () -> Void
    let onSettings: () -> Void

    var body: some View {
        VStack(spacing: Space.l) {
            DotEmptyState(
                icon: .monitorOff, title: "Dot can't reach your Mac",
                message: "\(host.name)\n\(message)\n\nIs the Mac awake, Dot running and Tailscale on here?", tone: Theme.warn,
                actionTitle: "Try again", action: onRetry)
            Button("Change address", action: onSettings).dotButton(.secondary)
        }
        .padding(Space.xl)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background { DotBackground() }
    }
}

/// Dot's mark: a flat graphite disc with two eyes in the ink (the same neutral disc the other agents' bots stand on, and the
/// same two eyes the app icon has). No gradient, no hue.
struct DotGlyph: View {
    let size: CGFloat
    @Environment(\.colorSchemeContrast) private var contrast: ColorSchemeContrast

    var body: some View {
        ZStack {
            Circle().fill(Theme.raised)
            Circle().strokeBorder(contrast == .increased ? Theme.hairlineStrong : Theme.hairline, lineWidth: max(0.5, size * 0.02))
            HStack(spacing: size * 0.15) {
                Capsule().fill(Theme.text).frame(width: size * 0.11, height: size * 0.21)
                Capsule().fill(Theme.text).frame(width: size * 0.11, height: size * 0.21)
            }
            .offset(y: -size * 0.02)
        }
        .frame(width: size, height: size)
        .accessibilityHidden(true)
    }
}
