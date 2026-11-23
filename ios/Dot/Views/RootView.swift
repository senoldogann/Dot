import SwiftUI

/// The address screen until a host is saved, the pairing screen until the phone has a key, then the native app. The
/// web UI stays one tap away ("Open web UI") as a full-screen fallback.
struct RootView: View {
    @Bindable var model: AppModel

    var body: some View {
        Group {
            if let host: DaemonHost = model.host {
                if let store: NativeStore = model.native {
                    ChatListView(model: model, store: store)
                        .id(host.name)
                } else {
                    PairView(model: model, host: host)
                }
            } else {
                AddressView(model: model, onSaved: nil)
            }
        }
        .preferredColorScheme(.dark)
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
                NativeSettingsView(model: model, store: store).tint(Theme.accent).preferredColorScheme(.dark).presentationBackground(Theme.background)
            } else {
                SettingsView(model: model)
            }
        }
        .fullScreenCover(isPresented: $model.showsWeb) {
            if let host: DaemonHost = model.host {
                ZStack(alignment: .topTrailing) {
                    WebScreen(model: model, host: host)
                    Button("Done") { model.showsWeb = false }
                        .buttonStyle(QuietButtonStyle())
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
        VStack(spacing: 16) {
            DotGlyph(size: 40)
            Text("Dot can't reach your Mac")
                .font(.title3.weight(.semibold))
            Text("\(host.name)\n\(message)")
                .font(.footnote)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
            Text("Is the Mac awake, Dot running and Tailscale on here?")
                .font(.footnote)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
            HStack(spacing: 12) {
                Button("Try again", action: onRetry)
                    .buttonStyle(.borderedProminent)
                Button("Change address", action: onSettings)
                    .buttonStyle(.bordered)
            }
        }
        .padding(24)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color(uiColor: .systemBackground))
    }
}

/// Dot's mark: a yellow dot with two eyes.
struct DotGlyph: View {
    let size: CGFloat

    var body: some View {
        ZStack {
            Circle().fill(Color(red: 1.0, green: 0.78, blue: 0.22))
            HStack(spacing: size * 0.14) {
                Capsule().fill(Color(white: 0.12)).frame(width: size * 0.11, height: size * 0.2)
                Capsule().fill(Color(white: 0.12)).frame(width: size * 0.11, height: size * 0.2)
            }
            .offset(y: -size * 0.06)
        }
        .frame(width: size, height: size)
        .accessibilityHidden(true)
    }
}
