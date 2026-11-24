import SwiftUI

/// The entry to the inbox, at the top of the chat list while anything waits on the owner (item 1442): one flat card with a
/// hairline, the count on a badge and the newest ask in a line. It is drawn from what the phone last read, at once.
struct NeedsYouEntry: View {
    let count: Int
    let preview: String?
    let onOpen: () -> Void

    var body: some View {
        Button(action: onOpen) {
            HStack(spacing: Space.m) {
                Text("\(count)")
                    .font(.footnote.weight(.bold).monospacedDigit())
                    .foregroundStyle(Theme.background)
                    .frame(minWidth: 28, minHeight: 28)
                    .background(Theme.warn, in: Capsule())
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 2) {
                    Text(NeedsYouRules.entry(count: count)).font(.headline).foregroundStyle(Theme.text)
                    if let preview {
                        Text(preview).font(Theme.small).foregroundStyle(Theme.muted).lineLimit(2).multilineTextAlignment(.leading)
                    }
                }
                Spacer(minLength: Space.s)
                Image(systemName: "chevron.right").font(.system(size: 12, weight: .semibold)).foregroundStyle(Theme.faint)
            }
            .padding(Space.l)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background {
                RoundedRectangle(cornerRadius: Radius.l)
                    .fill(Theme.warn.opacity(0.08))
                    .background(Theme.surface, in: RoundedRectangle(cornerRadius: Radius.l))
            }
            .overlay(RoundedRectangle(cornerRadius: Radius.l).strokeBorder(Theme.warn.opacity(0.4), lineWidth: 1))
            .contentShape(RoundedRectangle(cornerRadius: Radius.l))
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(NeedsYouRules.entry(count: count))\(preview.map { ". " + $0 } ?? "")")
        .accessibilityHint("Opens what waits on you")
        .accessibilityIdentifier("needs-you-entry")
    }
}

/// What waits on the owner (item 1442), newest first: each row says who asks and what, with the answer where one may be given
/// here. An approval has Allow once and Deny, a question its choices as buttons (the recommended one marked) or a field for
/// words, a secret is never answered here, and each row opens its chat. The list is drawn from the last read; pulling it
/// down reads it again. An answer is kept on the phone and sent when the link allows, as in the chat.
struct NeedsYouView: View {
    let store: NativeStore
    /// Opens the chat of a run (the sheet goes away first).
    let onOpen: (String) -> Void
    @Environment(\.dismiss) private var dismiss: DismissAction

    var body: some View {
        let awareness: AwarenessModel = store.awareness
        let rows: [NeedsYouRow] = NeedsYouRules.rows(
            from: awareness.needsYou, chatTitle: { id in store.threads.first(where: { $0.id == id })?.title },
            answering: NeedsYouRules.answering(in: store.outbox.items))
        NavigationStack {
            ScrollView {
                LazyVStack(alignment: .leading, spacing: Space.m) {
                    if !awareness.needsYouIsLive {
                        Label("Showing what the phone last read from Dot.", systemImage: "wifi.slash")
                            .font(Theme.meta).foregroundStyle(Theme.faint)
                    }
                    if rows.isEmpty {
                        DotEmptyState(
                            symbol: "checkmark.circle", title: "Nothing waits on you",
                            message: "When Dot needs an answer, it shows here and on your lock screen.", tone: Theme.good
                        )
                        .padding(.top, Space.xl)
                    }
                    ForEach(rows) { row in
                        NeedsYouRowView(
                            row: row, onAnswer: { store.outbox.enqueue($0) },
                            onOpenChat: {
                                dismiss()
                                onOpen(row.item.runId)
                            },
                            onOpenWeb: {
                                if let card: HandoffCard = HandoffCard.make(from: row.item) {
                                    HandoffCardView.openInSafari(card, on: store.host)
                                }
                            })
                    }
                }
                .padding(Space.l)
            }
            .refreshable { await awareness.refreshNeedsYou() }
            .dotScreenBackground(inSheet: true)
            .navigationTitle("Needs you")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
            .task { await awareness.refreshNeedsYou() }
        }
    }
}

/// One row of the inbox. Dot's own words and the owner's answers never share a box with a secret's value: a row of a secret
/// has no field and no button that takes one.
struct NeedsYouRowView: View {
    let row: NeedsYouRow
    let onAnswer: (OutboxItem.Action) -> Void
    let onOpenChat: () -> Void
    /// Opens the run's page of the web UI in Safari (a hand-off's button).
    var onOpenWeb: () -> Void = {}
    @State private var typed: String = ""
    @State private var expanded: Bool = false

    var body: some View {
        VStack(alignment: .leading, spacing: Space.m) {
            header
            ask
            if row.answering {
                Label("Your answer is on its way to Dot.", systemImage: "paperplane").font(Theme.small).foregroundStyle(Theme.muted)
            } else {
                answers
            }
            Button(action: onOpenChat) {
                HStack(spacing: Space.xs) {
                    Text("Open chat")
                    Image(systemName: "chevron.right").font(.system(size: 11, weight: .semibold))
                }
                .font(Theme.small.weight(.medium))
                .foregroundStyle(Theme.accent)
                .frame(minHeight: 44, alignment: .leading)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("needs-you-open-chat")
        }
        .padding(Space.l)
        .background {
            RoundedRectangle(cornerRadius: Radius.l)
                .fill(Theme.warn.opacity(0.07))
                .background(Theme.surface, in: RoundedRectangle(cornerRadius: Radius.l))
        }
        .overlay(RoundedRectangle(cornerRadius: Radius.l).strokeBorder(Theme.warn.opacity(0.4), lineWidth: 1))
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("needs-you-row-\(row.id)")
    }

    // MARK: Who asks

    private var header: some View {
        HStack(spacing: Space.m) {
            Avatar(id: row.who == "Dot" ? "dot" : row.who, name: row.who, size: 32, state: .waiting)
            VStack(alignment: .leading, spacing: 1) {
                Text(row.who).font(.subheadline.weight(.semibold)).foregroundStyle(Theme.text).lineLimit(1)
                if let chat: String = row.chat, !chat.isEmpty, chat != row.who {
                    Text(chat).font(Theme.meta).foregroundStyle(Theme.muted).lineLimit(1)
                }
            }
            Spacer(minLength: Space.s)
            Text(shortAge(row.item.since)).font(Theme.meta).foregroundStyle(Theme.faint)
        }
        .accessibilityElement(children: .combine)
    }

    // MARK: What is asked

    @ViewBuilder private var ask: some View {
        switch row.ask {
        case .approval(let title, let command, let why, let mayAllow):
            Text(title).font(.headline).foregroundStyle(Theme.text)
            if !command.isEmpty {
                Button { expanded.toggle() } label: {
                    Text(command)
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
            }
            if let why { Text("Why Dot asks: \(why)").font(Theme.meta).foregroundStyle(Theme.muted) }
            if !mayAllow {
                Text("This shows only the start of it. Read all of it in the chat before you allow it.")
                    .font(Theme.meta).foregroundStyle(Theme.warn)
            }
        case .question(let text, _):
            Text(text).font(.headline).foregroundStyle(Theme.text)
        case .secret:
            Label("Dot needs a secret: enter it on your Mac", systemImage: "lock.shield.fill")
                .font(.subheadline).foregroundStyle(Theme.text)
        case .handoff(let line):
            Text(line).font(.headline).foregroundStyle(Theme.text)
            Text(HandoffCard.explanation).font(Theme.small).foregroundStyle(Theme.muted)
        case .other(let line):
            Text(line).font(.headline).foregroundStyle(Theme.text)
        }
    }

    // MARK: What may be answered here

    @ViewBuilder private var answers: some View {
        switch row.ask {
        case .approval(_, _, _, let mayAllow):
            DotGlassGroup(spacing: Space.s) {
                ViewThatFits(in: .horizontal) {
                    HStack(spacing: Space.s) { deny; if mayAllow { allow } else { readAll } }
                    VStack(spacing: Space.s) { if mayAllow { allow } else { readAll }; deny }
                }
                .controlSize(.large)
            }
        case .question(_, let choices):
            if choices.isEmpty {
                TextField("", text: $typed, prompt: Text("Type your answer").foregroundStyle(Theme.muted), axis: .vertical)
                    .font(Theme.body)
                    .padding(Space.m)
                    .background(Theme.background, in: RoundedRectangle(cornerRadius: Radius.m))
                    .overlay(RoundedRectangle(cornerRadius: Radius.m).strokeBorder(Theme.hairline, lineWidth: 1))
                DotGlassGroup(spacing: Space.s) {
                    ViewThatFits(in: .horizontal) {
                        HStack(spacing: Space.s) { skip; send }
                        VStack(spacing: Space.s) { send; skip }
                    }
                    .controlSize(.large)
                }
            } else {
                DotGlassGroup(spacing: Space.s) {
                    VStack(spacing: Space.s) {
                        ForEach(choices) { choice in
                            Button {
                                perform(row.decision(approve: true, note: choice.label))
                            } label: {
                                HStack(spacing: Space.s) {
                                    Text(choice.label).lineLimit(2).multilineTextAlignment(.leading)
                                    Spacer(minLength: Space.xs)
                                    if choice.recommended {
                                        Text("Recommended").font(Theme.tiny.weight(.semibold)).textCase(.uppercase)
                                    }
                                }
                                .frame(maxWidth: .infinity)
                            }
                            .dotButton(choice.recommended ? .prominent : .glass)
                            .accessibilityHint(choice.recommended ? "The answer Dot recommends" : "")
                        }
                        skip
                    }
                    .controlSize(.large)
                }
            }
        case .handoff:
            Button(action: onOpenWeb) {
                Label(HandoffCard.buttonTitle, systemImage: "arrow.up.forward.app").frame(maxWidth: .infinity)
            }
            .dotButton(.glass)
            .controlSize(.large)
            .accessibilityIdentifier("needs-you-open-web")
        case .secret, .other:
            EmptyView()
        }
    }

    private var deny: some View {
        Button { perform(row.decision(approve: false)) } label: { Text("Deny").lineLimit(1).frame(maxWidth: .infinity) }
            .dotButton(.glass).accessibilityIdentifier("needs-you-deny")
    }

    private var allow: some View {
        Button { perform(row.decision(approve: true)) } label: { Text("Allow once").lineLimit(1).frame(maxWidth: .infinity) }
            .dotButton(.prominent).accessibilityIdentifier("needs-you-allow")
    }

    private var readAll: some View {
        Button(action: onOpenChat) { Text("Read it all").lineLimit(1).frame(maxWidth: .infinity) }
            .dotButton(.prominent).accessibilityIdentifier("needs-you-read-all")
    }

    private var skip: some View {
        Button { perform(row.decision(approve: false)) } label: { Text("Skip").lineLimit(1).frame(maxWidth: .infinity) }
            .dotButton(.glass).accessibilityIdentifier("needs-you-skip")
    }

    private var send: some View {
        Button { perform(row.decision(approve: true, note: typed)) } label: { Text("Send answer").lineLimit(1).frame(maxWidth: .infinity) }
            .dotButton(.prominent)
            .disabled(typed.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            .accessibilityIdentifier("needs-you-send")
    }

    /// Hands the answer to the outbox, which keeps it on disk and sends it when the link allows; a row that may not be
    /// answered here gives none.
    private func perform(_ action: OutboxItem.Action?) {
        guard let action else { return }
        onAnswer(action)
    }
}
