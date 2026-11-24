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
        let refused: Bool = banner.kind == .unauthorized
        return HStack(spacing: Space.s) {
            DotIcon(icon(banner.kind), size: 14, pulse: banner.kind == .reconnecting, relativeTo: .caption)
            Text(banner.text).font(Theme.meta.weight(.medium)).lineLimit(1).minimumScaleFactor(0.85)
        }
        .foregroundStyle(refused ? Theme.warn : Theme.text)
        .padding(.horizontal, Space.m).frame(minHeight: 30)
        .dotGlass(in: Capsule(), tint: refused ? Theme.warn.opacity(0.22) : nil)
        .padding(.top, Space.xs)
        .frame(maxWidth: .infinity)
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("connection-banner")
    }

    private func icon(_ kind: ConnectionBanner.Kind) -> IconName {
        switch kind {
        case .offline: .wifiOff
        case .reconnecting: .refresh
        case .unauthorized: .key
        }
    }
}
