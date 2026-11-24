import SwiftUI

/// An agent's avatar (item 1462): Dot wears its own face, every other agent the Lucide bot on one neutral disc (the theme's
/// raised ground and a hairline), whatever its id: no hue taken from it, no initial. The ring round it says how it stands: it
/// turns while the agent works and is amber while it waits for the owner (`StateRing`). It is decoration: the row or the chip
/// it stands in says the name and the state (`AgentLook.words`).
struct AgentAvatar: View {
    let face: AgentLook.Face
    var size: CGFloat = 40
    var state: ThreadState = .idle
    @Environment(\.colorSchemeContrast) private var contrast: ColorSchemeContrast

    init(id: String?, size: CGFloat = 40, state: ThreadState = .idle) {
        self.init(face: AgentLook.face(ofAgent: id), size: size, state: state)
    }

    init(face: AgentLook.Face, size: CGFloat = 40, state: ThreadState = .idle) {
        self.face = face
        self.size = size
        self.state = state
    }

    var body: some View {
        Group {
            switch face {
            case .dot:
                DotGlyph(size: size)
            case .bot:
                Circle()
                    .fill(Theme.raised)
                    .overlay(Circle().strokeBorder(contrast == .increased ? Theme.hairlineStrong : Theme.hairline, lineWidth: 1))
                    .overlay { DotIcon(.bot, size: size * 0.5, scales: false).foregroundStyle(Theme.muted) }
            }
        }
        .frame(width: size, height: size)
        .overlay { StateRing(state: state).padding(-size * 0.11) }
        .accessibilityHidden(true)
    }
}
