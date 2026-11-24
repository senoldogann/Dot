import SwiftUI

/// The Notifications group of Settings (item 1441): the three kinds of push Dot may tell this phone, each a switch with one
/// line of what it means. A flip shows at once; one Dot does not keep goes back with a plain line. Without Dot's answer the
/// last switches the phone knew are shown dimmed and do not move.
struct NotificationSwitches: View {
    let awareness: AwarenessModel
    /// Whether the phone has a link: the switches are read again when it comes back.
    let online: Bool

    var body: some View {
        let push: PushSwitches = awareness.push
        VStack(alignment: .leading, spacing: Space.s) {
            if let failure: String = push.failure { NoticeCard(words: failure) }
            CardGroup(
                title: "Notifications",
                footer: "These are the alerts Dot sends to this phone. While you have Dot open on it, a task that finishes stays quiet."
            ) {
                ForEach(Array(PushKind.allCases.enumerated()), id: \.element) { index, kind in
                    if index > 0 { CardDivider() }
                    CardRow(symbol: kind.symbol, title: kind.title, detail: kind.words) {
                        Toggle(
                            kind.title,
                            isOn: Binding(get: { awareness.push.shown[kind] }, set: { awareness.flip(kind, to: $0) })
                        )
                        .labelsHidden()
                        .accessibilityIdentifier("notify-\(kind.title)")
                        .sensoryFeedback(.selection, trigger: push.shown[kind])
                    }
                }
                .disabled(!push.canFlip)
                .opacity(push.isLive ? 1 : 0.5)
            }
            if let line: String = push.offlineLine {
                Label(line, systemImage: "wifi.slash").font(Theme.meta).foregroundStyle(Theme.faint).padding(.horizontal, Space.l)
            }
        }
        .task(id: online) { await awareness.loadPush() }
        .dotAnimation(Motion.smooth, value: push.isLive)
    }
}
