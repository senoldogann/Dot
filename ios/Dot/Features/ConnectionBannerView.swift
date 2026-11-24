import SwiftUI

/// The thin line under the status bar that says the phone is offline (what it shows was saved), trying again, or that its
/// key was refused. It sits over every screen and never blocks reading. A short blip of "Reconnecting…" is not shown: it
/// appears only when it lasts. With nothing to say it is empty, and nothing runs: its clock exists only while it shows.
struct ConnectionBannerView: View {
    let store: NativeStore
    @State private var settled: Bool = false

    var body: some View {
        let now: ConnectionBanner? = store.banner()
        Group {
            if let banner: ConnectionBanner = now, banner.kind != .reconnecting || settled {
                TimelineView(.periodic(from: .now, by: 30)) { context in
                    bar(store.banner(now: context.date) ?? banner)
                }
                .transition(.opacity)
            }
        }
        .animation(.easeOut(duration: 0.2), value: now)
        .task(id: now?.kind) {
            settled = false
            guard now?.kind == .reconnecting else { return }
            try? await Task.sleep(for: .milliseconds(1500))
            if !Task.isCancelled { settled = true }
        }
    }

    private func bar(_ banner: ConnectionBanner) -> some View {
        HStack(spacing: 6) {
            Image(systemName: icon(banner.kind)).font(.system(size: 11, weight: .medium))
            Text(banner.text).font(.system(size: 12)).lineLimit(1).minimumScaleFactor(0.85)
            Spacer(minLength: 0)
        }
        .foregroundStyle(banner.kind == .unauthorized ? Color.orange : Color.secondary)
        .padding(.horizontal, 16)
        .frame(height: 26)
        .frame(maxWidth: .infinity)
        .background(Color.white.opacity(0.06))
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("connection-banner")
    }

    private func icon(_ kind: ConnectionBanner.Kind) -> String {
        switch kind {
        case .offline: "wifi.slash"
        case .reconnecting: "arrow.triangle.2.circlepath"
        case .unauthorized: "key"
        }
    }
}
