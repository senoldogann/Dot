import SwiftUI

/// The address screen until a host is saved, then the daemon's web UI full screen.
struct RootView: View {
    @Bindable var model: AppModel

    var body: some View {
        Group {
            if let host: DaemonHost = model.host {
                WebScreen(model: model, host: host)
                    .id(host.name)
            } else {
                AddressView(model: model, onSaved: nil)
            }
        }
        .sheet(isPresented: $model.showsSettings) {
            SettingsView(model: model)
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
        .ignoresSafeArea(.container, edges: .bottom)
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
