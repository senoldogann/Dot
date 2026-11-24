import SwiftUI
import UIKit

/// The card a chat shows when its run waits for a step only the owner can do in the browser (item 1444): one calm card, no
/// credential and no page text, only the host the daemon names. Its button opens the run's page of the web UI in Safari,
/// where Take over lets the owner do the step from the phone; the chat itself is still one tap away
/// (`dotagent://run/<id>`).
struct HandoffCardView: View {
    let card: HandoffCard
    let onOpenWeb: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: Space.m) {
            HStack(alignment: .top, spacing: Space.m) {
                DotIcon(.globe, size: 20, relativeTo: .title3)
                    .foregroundStyle(Theme.accent)
                    .frame(width: 36, height: 36)
                    .background(Theme.accent.opacity(0.14), in: Circle())
                    .accessibilityHidden(true)
                Text(card.headline).font(.headline).foregroundStyle(Theme.text).fixedSize(horizontal: false, vertical: true)
            }
            Text(HandoffCard.explanation).font(Theme.small).foregroundStyle(Theme.muted).fixedSize(horizontal: false, vertical: true)
            Button(action: onOpenWeb) {
                Label(HandoffCard.buttonTitle, icon: .external, size: 16).frame(maxWidth: .infinity)
            }
            .dotButton(.glass)
            .controlSize(.large)
            .accessibilityIdentifier("handoff-open")
        }
        .padding(Space.l)
        .frame(maxWidth: .infinity, alignment: .leading)
        .card(radius: Radius.l)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("handoff-card")
    }

    /// Opens the run's page of the web UI in Safari (the system's browser, not the app's own web view), on the address the app
    /// already holds. A run that is not plainly one opens nothing.
    @MainActor
    static func openInSafari(_ card: HandoffCard, on host: DaemonHost) {
        guard let url: URL = card.webURL(on: host) else { return }
        UIApplication.shared.open(url)
    }
}
