import SwiftUI

/// "While you were away" (item 1443): one flat card at the top of the chat list when Dot did something on its own since the
/// owner last looked (what a schedule, an agent's standing chat or a background run did). It says how many things and lists
/// the few newest, each a row that opens its chat; putting it away marks the moment read on Dot, so that the Mac and every
/// phone agree on what is news. It is drawn from the last read, at once.
struct AwayCardView: View {
    let card: AwayCard
    let onOpen: (String) -> Void
    let onDismiss: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: Space.s) {
            HStack(alignment: .top, spacing: Space.m) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("While you were away").font(.caption.weight(.semibold)).foregroundStyle(Theme.accent)
                    Text(card.headline).font(.headline).foregroundStyle(Theme.text)
                    if !card.counts.isEmpty { Text(card.counts).font(Theme.small).foregroundStyle(Theme.muted) }
                }
                Spacer(minLength: Space.s)
                Button(action: onDismiss) {
                    DotIcon(.x, size: 14, relativeTo: .caption)
                        .foregroundStyle(Theme.muted)
                        .frame(width: 44, height: 44)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .padding(.trailing, -Space.m)
                .padding(.top, -Space.s)
                .accessibilityLabel("Mark as read")
                .accessibilityHint("Puts this away on the phone and on your Mac")
                .accessibilityIdentifier("away-dismiss")
            }
            if !card.rows.isEmpty {
                VStack(spacing: 0) {
                    ForEach(Array(card.rows.enumerated()), id: \.element.id) { index, row in
                        if index > 0 { CardDivider(inset: 0) }
                        Button { onOpen(row.run.runId) } label: { AwayRowView(row: row) }
                            .buttonStyle(.plain)
                            .accessibilityHint("Opens the chat")
                    }
                }
                if card.more > 0 {
                    Text("and \(card.more) more").font(Theme.meta).foregroundStyle(Theme.faint)
                }
            }
            if let decisions: String = card.decisions {
                Label(decisions, icon: .shieldCheck).font(Theme.meta).foregroundStyle(Theme.muted)
            }
        }
        .padding(.horizontal, Space.l)
        .padding(.vertical, Space.m)
        .frame(maxWidth: .infinity, alignment: .leading)
        .card(radius: Radius.l)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("away-card")
    }
}

/// One run of the card: how it ended, what it was, and how long ago.
private struct AwayRowView: View {
    let row: AwayCard.Row

    var body: some View {
        HStack(alignment: .top, spacing: Space.s) {
            DotIcon(icon, size: 16, relativeTo: .footnote)
                .foregroundStyle(tone)
                .frame(width: 20, height: 22)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 1) {
                Text(row.title).font(.subheadline).foregroundStyle(Theme.text).lineLimit(1)
                Text(row.detail.map { "\(row.status) · \($0)" } ?? row.status)
                    .font(Theme.meta).foregroundStyle(Theme.muted).lineLimit(2)
            }
            Spacer(minLength: Space.xs)
            Text(shortAge(AwayRules.moment(row.run))).font(Theme.meta).foregroundStyle(Theme.faint)
        }
        .padding(.vertical, Space.s)
        .frame(minHeight: 44)
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
    }

    private var icon: IconName {
        switch row.run.status {
        case "done": .circleCheck
        case "failed": .circleAlert
        case "cancelled": .stop
        case "blocked": .hand
        default: .audioWaveform
        }
    }

    private var tone: Color {
        switch row.run.status {
        case "done": Theme.good
        case "failed": Theme.bad
        case "blocked": Theme.warn
        case "cancelled": Theme.faint
        default: Theme.accent
        }
    }
}
