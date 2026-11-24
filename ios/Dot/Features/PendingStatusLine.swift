import SwiftUI

/// What stands under a message that the Mac has not yet taken: that it is being sent, that it waits for its turn or for the
/// link (it goes by itself), or that it did not go, with what to do about it. The message itself is kept on disk.
struct PendingStatusLine: View {
    let send: PendingSend
    let onRetry: () -> Void
    let onDiscard: () -> Void

    var body: some View {
        HStack(spacing: Space.m) {
            if send.sending {
                ProgressView().controlSize(.mini)
                Text("Sending…").font(Theme.meta).foregroundStyle(Theme.faint)
            } else if send.waiting {
                DotIcon(.clock, size: 13, relativeTo: .caption).foregroundStyle(Theme.faint)
                Text("Waiting to send").font(Theme.meta).foregroundStyle(Theme.faint)
                Button("Retry now", action: onRetry).font(Theme.meta.weight(.semibold))
                Button("Edit", action: onDiscard).font(Theme.meta.weight(.semibold))
            } else {
                Text(send.note ?? "Not sent").font(Theme.meta).foregroundStyle(Theme.bad).lineLimit(2)
                Button("Retry", action: onRetry).font(Theme.meta.weight(.semibold))
                Button("Edit", action: onDiscard).font(Theme.meta.weight(.semibold))
            }
        }
        .accessibilityIdentifier("pending-status")
    }
}
