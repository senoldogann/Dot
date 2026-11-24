import SwiftUI

/// The address screen until a host is saved, the pairing screen until the phone has a key, then the native app. The
/// web UI stays one tap away ("Open web UI") as a full-screen fallback.
struct RootView: View {
    @Bindable var model: AppModel

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
                } else {
                    PairView(model: model, host: host)
                }
            } else {
                AddressView(model: model, onSaved: nil)
            }
        }
        .tint(Theme.accent)
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
                        .dotButton(.glass)
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
            Button("Change address", action: onSettings).dotButton(.glass)
        }
        .padding(Space.xl)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background { DotBackground(glow: 0.6) }
    }
}

/// Dot's mark: a luminous blue dot with two eyes, the app icon's sphere drawn small.
struct DotGlyph: View {
    let size: CGFloat

    var body: some View {
        ZStack {
            Circle().fill(
                RadialGradient(
                    colors: [Color(red: 0.62, green: 0.80, blue: 1.0), Color(red: 0.24, green: 0.45, blue: 0.93), Color(red: 0.10, green: 0.22, blue: 0.62)],
                    center: UnitPoint(x: 0.35, y: 0.28), startRadius: 0, endRadius: size * 0.85))
            Circle().strokeBorder(Color.white.opacity(0.25), lineWidth: max(0.5, size * 0.015))
            HStack(spacing: size * 0.15) {
                Capsule().fill(Color(red: 0.04, green: 0.07, blue: 0.2)).frame(width: size * 0.11, height: size * 0.21)
                Capsule().fill(Color(red: 0.04, green: 0.07, blue: 0.2)).frame(width: size * 0.11, height: size * 0.21)
            }
            .offset(y: -size * 0.02)
        }
        .frame(width: size, height: size)
        .accessibilityHidden(true)
    }
}
