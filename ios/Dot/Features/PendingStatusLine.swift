import SwiftUI

/// What stands under a message that the Mac has not yet taken: that it is being sent, that it waits for its turn or for the
/// link (it goes by itself), or that it did not go, with what to do about it. The message itself is kept on disk.
struct PendingStatusLine: View {
    let send: PendingSend
    let onRetry: () -> Void
    let onDiscard: () -> Void

    var body: some View {
        HStack(spacing: 12) {
            if send.sending {
                ProgressView().controlSize(.mini)
                Text("Sending…").font(.system(size: 12)).foregroundStyle(.secondary)
            } else if send.waiting {
                Image(systemName: "clock").font(.system(size: 11)).foregroundStyle(.secondary)
                Text("Waiting to send").font(.system(size: 12)).foregroundStyle(.secondary)
                Button("Retry now", action: onRetry).font(.system(size: 12))
                Button("Edit", action: onDiscard).font(.system(size: 12))
            } else {
                Text(send.note ?? "Not sent").font(.system(size: 12)).foregroundStyle(Color.red).lineLimit(2)
                Button("Retry", action: onRetry).font(.system(size: 12))
                Button("Edit", action: onDiscard).font(.system(size: 12))
            }
        }
        .accessibilityIdentifier("pending-status")
    }
}
