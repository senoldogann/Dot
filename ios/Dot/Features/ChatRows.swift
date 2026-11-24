import SwiftUI
import UIKit

/// One row of the conversation. Dot's words are plain text on the ground (no bubble); the owner's words are a raised
/// rounded box; a tool step is a quiet line; a step that needs the owner is a card that asks.
struct ChatRow: View {
    let item: ChatItem
    let onDecide: (ApprovalCard, Bool, String) -> Void
    var onTool: (ToolRow) -> Void = { _ in }

    var body: some View {
        switch item.kind {
        case .user(let text):
            HStack(spacing: 0) {
                Spacer(minLength: 48)
                Text(LinkStyle.styled(text))
                    .font(Theme.body)
                    .foregroundStyle(Theme.text)
                    .tint(Theme.text)
                    .padding(.horizontal, Space.l)
                    .padding(.vertical, Space.m - 1)
                    .background(Theme.raised, in: RoundedRectangle(cornerRadius: Radius.l + 2))
                    .overlay(RoundedRectangle(cornerRadius: Radius.l + 2).strokeBorder(Theme.hairline, lineWidth: 1))
            }
        case .assistant(let text, _):
            MessageText(text: text)
        case .answer(let text, let status):
            VStack(alignment: .leading, spacing: Space.s) {
                MessageText(text: text)
                if status == "failed" || status == "cancelled" {
                    Label(status == "failed" ? "The run failed" : "Stopped", icon: status == "failed" ? .circleAlert : .stop)
                        .font(Theme.meta)
                        .foregroundStyle(status == "failed" ? Theme.bad : Theme.faint)
                }
            }
        case .tool(let row):
            Button { onTool(row) } label: { ToolLine(row: row) }
                .buttonStyle(.plain)
                .accessibilityIdentifier("tool-\(row.callId)")
                .accessibilityHint("Shows the step's arguments and result")
        case .approval(let card):
            ApprovalCardView(card: card, onDecide: onDecide)
        case .note(let text, let tone):
            Text(text)
                .font(Theme.meta)
                .foregroundStyle(tone == .warn ? Theme.warn : tone == .bad ? Theme.bad : Theme.faint)
                .frame(maxWidth: .infinity, alignment: .center)
                .multilineTextAlignment(.center)
        }
    }
}

/// Dot's words: prose with inline markdown and plain bright links, fenced code in a block of its own.
struct MessageText: View {
    let text: String

    var body: some View {
        VStack(alignment: .leading, spacing: Space.m) {
            ForEach(Array(MessageBlocks.parse(text).enumerated()), id: \.offset) { _, block in
                switch block {
                case .prose(let prose):
                    Text(LinkStyle.styled(prose))
                        .font(Theme.body)
                        .foregroundStyle(Theme.text)
                        .tint(Theme.text)
                        .lineSpacing(4)
                        .frame(maxWidth: .infinity, alignment: .leading)
                case .code(let code):
                    CodeBlock(code: code)
                }
            }
        }
    }
}

/// A fenced block: SF Mono on its own ground, scrolling sideways, with a copy button.
struct CodeBlock: View {
    let code: String
    @State private var copied: Bool = false

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            Text(code)
                .font(Theme.mono)
                .foregroundStyle(Theme.text)
                .padding(.leading, Space.m)
                .padding(.trailing, 44)
                .padding(.vertical, Space.m)
                .textSelection(.enabled)
        }
        .background(Theme.codeFill, in: RoundedRectangle(cornerRadius: Radius.m))
        .overlay(RoundedRectangle(cornerRadius: Radius.m).strokeBorder(Theme.hairline, lineWidth: 1))
        .overlay(alignment: .topTrailing) {
            Button {
                UIPasteboard.general.string = code
                copied = true
                Task {
                    try? await Task.sleep(for: .seconds(1.5))
                    copied = false
                }
            } label: {
                DotIcon(copied ? .check : .copy, size: 16, relativeTo: .footnote)
                    .foregroundStyle(copied ? Theme.text : Theme.muted)
                    .frame(width: 40, height: 40)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .sensoryFeedback(.success, trigger: copied) { _, new in new }
            .accessibilityLabel(copied ? "Copied" : "Copy the code")
        }
    }
}

/// A tool step as one quiet line: the tool's symbol, what it did, and whether it is still going. The symbol pulses while
/// the step runs and gives way to a check when it is done.
struct ToolLine: View {
    let row: ToolRow

    var body: some View {
        let words: (verb: String, rest: String) = ToolGlyph.split(row.line)
        HStack(alignment: .firstTextBaseline, spacing: Space.s) {
            DotIcon(ToolGlyph.icon(for: row.tool), size: 15, pulse: row.state == .running, relativeTo: .footnote)
                .foregroundStyle(row.state == .running ? Theme.text : Theme.faint)
                .frame(width: 18)
            VStack(alignment: .leading, spacing: 1) {
                line(words)
                    .lineLimit(1)
                    .truncationMode(.middle)
                if row.state == .failed, let why: String = row.failure {
                    Text("Failed · \(why)").font(Theme.meta).foregroundStyle(Theme.bad).lineLimit(1)
                }
            }
            Spacer(minLength: Space.xs)
            Group {
                switch row.state {
                case .running:
                    ProgressView().controlSize(.mini).tint(Theme.muted)
                case .ok:
                    DotIcon(.check, size: 11, relativeTo: .caption2).foregroundStyle(Theme.faint)
                case .failed:
                    DotIcon(.circleAlert, size: 14, relativeTo: .caption).foregroundStyle(Theme.bad)
                }
            }
            .frame(width: 14)
        }
        .frame(minHeight: 28)
        // The line is 28 points tall and quiet; a finger gets 44 (it reaches over the gap above and below).
        .contentShape(Rectangle().inset(by: -8))
        .dotAnimation(Motion.snappy, value: row.state)
        .accessibilityElement(children: .combine)
        .accessibilityValue(row.state == .running ? "running" : row.state == .failed ? "failed" + (row.failure.map { ": " + $0 } ?? "") : "done")
    }

    private func line(_ words: (verb: String, rest: String)) -> Text {
        if words.verb.isEmpty { return Text(words.rest).font(Theme.small).foregroundStyle(Theme.muted) }
        return Text(words.verb).font(Theme.small.weight(.medium)).foregroundStyle(Theme.muted)
            + Text("  ") + Text(words.rest).font(Theme.mono).foregroundStyle(row.state == .failed ? Theme.muted : Theme.faint)
    }
}

/// A step the run asks the owner about, or a question: Allow and Deny call the daemon. While it waits it is the loudest
/// thing in the chat (the run is stopped on it); once answered it folds into a quiet line.
struct ApprovalCardView: View {
    let card: ApprovalCard
    let onDecide: (ApprovalCard, Bool, String) -> Void
    @State private var answer: String = ""
    @State private var expanded: Bool = false

    var body: some View {
        Group {
            if card.isPending { pending } else { settled }
        }
        .sensoryFeedback(.success, trigger: card.status) { old, new in old == "pending" && new == "approved" }
        .sensoryFeedback(.warning, trigger: card.status) { old, new in old == "pending" && new != "approved" }
        .dotAnimation(Motion.smooth, value: card.status)
    }

    // MARK: Waiting

    private var presentation: ApprovalPresentation { ApprovalPresentation.make(tool: card.tool, summary: card.summary) }

    private var pending: some View {
        VStack(alignment: .leading, spacing: Space.m) {
            HStack(spacing: Space.m) {
                DotIcon(icon, size: 22, relativeTo: .title3)
                    .foregroundStyle(Theme.warn)
                    .frame(width: 28)
                VStack(alignment: .leading, spacing: 1) {
                    Text(card.question != nil ? "Dot has a question" : "Waiting for your OK")
                        .font(.caption.weight(.semibold)).foregroundStyle(Theme.warn)
                    if card.question == nil {
                        Text(presentation.title).font(.headline).foregroundStyle(Theme.text).lineLimit(2)
                    }
                }
                Spacer(minLength: 0)
            }
            if let question: String = card.question {
                // The question is the headline: it is what the owner has to read, so it is not in a box.
                Text(question).font(.headline).foregroundStyle(Theme.text).frame(maxWidth: .infinity, alignment: .leading)
            } else {
                Button { expanded.toggle() } label: {
                    Text(presentation.command)
                        .font(Theme.mono)
                        .foregroundStyle(Theme.text)
                        .lineLimit(expanded ? nil : 4)
                        .multilineTextAlignment(.leading)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(Space.m - 2)
                        .background(Theme.codeFill, in: RoundedRectangle(cornerRadius: Radius.s))
                }
                .buttonStyle(.plain)
                .accessibilityHint(expanded ? "Shows less" : "Shows all of it")
                if let why: String = presentation.why {
                    Text("Why Dot asks: \(why)").font(Theme.meta).foregroundStyle(Theme.muted)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            actions
        }
        .padding(Space.l)
        .card(border: Theme.warn.opacity(0.45))
        .accessibilityElement(children: .contain)
    }

    @ViewBuilder private var actions: some View {
        if card.severity == "secret" {
            // A secret is given on the Mac or in the web UI, never typed into a phone's chat (the daemon's decision
            // route refuses a yes to it); the phone may skip it.
            Label("This asks for a secret. Give it on your Mac or in the web UI.", icon: .lock)
                .font(Theme.small).foregroundStyle(Theme.muted)
            Button { onDecide(card, false, "") } label: { Text("Skip").frame(maxWidth: .infinity) }
                .dotButton(.secondary).controlSize(.large)
        } else if card.question != nil {
            TextField("", text: $answer, prompt: Text("Type your answer").foregroundStyle(Theme.muted), axis: .vertical)
                .font(Theme.body)
                .padding(Space.m)
                .background(Theme.raised, in: RoundedRectangle(cornerRadius: Radius.m))
                .overlay(RoundedRectangle(cornerRadius: Radius.m).strokeBorder(Theme.hairline, lineWidth: 1))
            ViewThatFits(in: .horizontal) {
                HStack(spacing: Space.s) { skip; answerButton }
                VStack(spacing: Space.s) { answerButton; skip }
            }
            .controlSize(.large)
        } else {
            ViewThatFits(in: .horizontal) {
                HStack(spacing: Space.s) { deny; allow }
                VStack(spacing: Space.s) { allow; deny }
            }
            .controlSize(.large)
        }
    }

    private var skip: some View {
        Button { onDecide(card, false, "") } label: { Text("Skip").lineLimit(1).frame(maxWidth: .infinity) }.dotButton(.secondary)
    }

    private var answerButton: some View {
        Button { onDecide(card, true, answer) } label: { Text("Send answer").lineLimit(1).frame(maxWidth: .infinity) }
            .dotButton(.primary)
            .disabled(answer.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
    }

    private var deny: some View {
        Button { onDecide(card, false, "") } label: { Text("Deny").lineLimit(1).frame(maxWidth: .infinity) }.dotButton(.secondary)
    }

    private var allow: some View {
        Button { onDecide(card, true, "") } label: { Text("Allow once").lineLimit(1).frame(maxWidth: .infinity) }.dotButton(.primary)
    }

    // MARK: Answered

    private var settled: some View {
        HStack(alignment: .top, spacing: Space.s) {
            DotIcon(resolutionIcon, size: 15, relativeTo: .footnote)
                .foregroundStyle(resolutionTone)
                .frame(width: 18, height: 20)
            VStack(alignment: .leading, spacing: 2) {
                Text(resolution + (card.question != nil ? "" : " · " + presentation.title))
                    .font(Theme.small.weight(.medium)).foregroundStyle(Theme.muted)
                Text(card.question ?? presentation.command)
                    .font(card.question != nil ? Theme.small : Theme.mono)
                    .foregroundStyle(Theme.faint)
                    .lineLimit(2)
            }
            Spacer(minLength: 0)
        }
        .padding(.vertical, Space.xs)
        .accessibilityElement(children: .combine)
    }

    private var icon: IconName {
        if card.severity == "secret" { return .lock }
        return card.question != nil ? .help : .hand
    }

    private var resolution: String {
        switch card.status {
        case "approved": "Allowed"
        case "denied": "Denied"
        case "left": "Left for later"
        default: "No longer asked"
        }
    }

    private var resolutionIcon: IconName {
        switch card.status {
        case "approved": .circleCheck
        case "denied": .circleX
        default: .circleMinus
        }
    }

    private var resolutionTone: Color {
        switch card.status {
        case "approved", "denied": Theme.muted
        default: Theme.faint
        }
    }
}

extension ToolRow: Identifiable {
    var id: String { callId }
}
