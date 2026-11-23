import SwiftUI

/// One row of the conversation.
struct ChatRow: View {
    let item: ChatItem
    let onDecide: (ApprovalCard, Bool, String) -> Void

    var body: some View {
        switch item.kind {
        case .user(let text):
            HStack(spacing: 0) {
                Spacer(minLength: 56)
                Text(LinkStyle.styled(text))
                    .font(Theme.body)
                    .foregroundStyle(Theme.text)
                    .tint(Theme.link)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 9)
                    .background(Theme.raised, in: RoundedRectangle(cornerRadius: Theme.radius))
                    .overlay(RoundedRectangle(cornerRadius: Theme.radius).strokeBorder(Theme.hairline, lineWidth: 1))
            }
        case .assistant(let text, _):
            MessageText(text: text)
        case .answer(let text, let status):
            VStack(alignment: .leading, spacing: 6) {
                MessageText(text: text)
                if status == "failed" || status == "cancelled" {
                    Text(status == "failed" ? "The run failed" : "Stopped")
                        .font(Theme.meta)
                        .foregroundStyle(status == "failed" ? Theme.bad : Theme.faint)
                }
            }
        case .tool(let row):
            ToolLine(row: row)
        case .approval(let card):
            ApprovalCardView(card: card, onDecide: onDecide)
        case .note(let text, let tone):
            Text(text)
                .font(Theme.meta)
                .foregroundStyle(tone == .warn ? Theme.warn : tone == .bad ? Theme.bad : Theme.faint)
        }
    }
}

/// Dot's words: prose with inline markdown and plain bright links, fenced code in a hairline block.
struct MessageText: View {
    let text: String

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(Array(MessageBlocks.parse(text).enumerated()), id: \.offset) { _, block in
                switch block {
                case .prose(let prose):
                    Text(LinkStyle.styled(prose))
                        .font(Theme.body)
                        .foregroundStyle(Theme.text)
                        .tint(Theme.link)
                        .lineSpacing(3)
                        .frame(maxWidth: .infinity, alignment: .leading)
                case .code(let code):
                    ScrollView(.horizontal, showsIndicators: false) {
                        Text(code)
                            .font(Theme.mono)
                            .foregroundStyle(Theme.text)
                            .padding(10)
                    }
                    .card(fill: Theme.surface)
                }
            }
        }
        .textSelection(.enabled)
    }
}

/// A tool step as one line: what it did, and whether it is still going.
struct ToolLine: View {
    let row: ToolRow

    var body: some View {
        HStack(spacing: 8) {
            switch row.state {
            case .running:
                ProgressView().controlSize(.mini).tint(Theme.muted).frame(width: 12, height: 12)
            case .ok:
                Image(systemName: "checkmark").font(.system(size: 9, weight: .bold)).foregroundStyle(Theme.faint).frame(width: 12)
            case .failed:
                Image(systemName: "xmark").font(.system(size: 9, weight: .bold)).foregroundStyle(Theme.bad).frame(width: 12)
            }
            Text(row.line)
                .font(.system(size: 12))
                .foregroundStyle(row.state == .failed ? Theme.bad : Theme.muted)
                .lineLimit(1)
                .truncationMode(.middle)
        }
        .accessibilityElement(children: .combine)
    }
}

/// A step the run asks the owner about, or a question: Allow and Deny call the daemon.
struct ApprovalCardView: View {
    let card: ApprovalCard
    let onDecide: (ApprovalCard, Bool, String) -> Void
    @State private var answer: String = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 6) {
                Image(systemName: card.question != nil ? "questionmark.bubble" : "hand.raised")
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(card.isPending ? Theme.warn : Theme.faint)
                Text(card.question != nil ? "Dot asks" : "Dot asks to " + stepName)
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(card.isPending ? Theme.warn : Theme.faint)
                Spacer()
                if !card.isPending {
                    Text(resolution).font(Theme.meta).foregroundStyle(Theme.faint)
                }
            }
            Text(card.question ?? card.summary)
                .font(Theme.small)
                .foregroundStyle(Theme.text)
                .frame(maxWidth: .infinity, alignment: .leading)
            if card.isPending {
                if card.severity == "secret" {
                    // A secret is given on the Mac or in the web UI, never typed into a phone's chat (the daemon's
                    // decision route refuses a yes to it); the phone may skip it.
                    Text("This asks for a secret. Give it on your Mac or in the web UI.").font(Theme.meta).foregroundStyle(Theme.muted)
                    Button("Skip") { onDecide(card, false, "") }.buttonStyle(QuietButtonStyle(tint: Theme.muted))
                } else if card.question != nil {
                    TextField("Your answer", text: $answer, axis: .vertical)
                        .font(Theme.small)
                        .padding(8)
                        .background(Theme.background, in: RoundedRectangle(cornerRadius: Theme.radius))
                        .overlay(RoundedRectangle(cornerRadius: Theme.radius).strokeBorder(Theme.hairline, lineWidth: 1))
                    HStack(spacing: 8) {
                        Button("Answer") { onDecide(card, true, answer) }
                            .buttonStyle(QuietButtonStyle(tint: Theme.accent, filled: true))
                            .disabled(answer.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                        Button("Skip") { onDecide(card, false, "") }.buttonStyle(QuietButtonStyle(tint: Theme.muted))
                    }
                } else {
                    HStack(spacing: 8) {
                        Button("Allow") { onDecide(card, true, "") }.buttonStyle(QuietButtonStyle(tint: Theme.accent, filled: true))
                        Button("Deny") { onDecide(card, false, "") }.buttonStyle(QuietButtonStyle(tint: Theme.muted))
                    }
                }
            }
        }
        .padding(12)
        .card(fill: Theme.surface)
        .overlay(RoundedRectangle(cornerRadius: Theme.radius).strokeBorder(card.isPending ? Theme.warn.opacity(0.35) : .clear, lineWidth: 1))
    }

    private var stepName: String { card.tool.replacingOccurrences(of: "_", with: " ") }

    private var resolution: String {
        switch card.status {
        case "approved": "Allowed"
        case "denied": "Denied"
        case "left": "Left for later"
        default: "No longer asked"
        }
    }
}
