import SwiftUI

/// The lane an agent's lines run in (item 1462): its face stands at the leading edge of the first line since someone else
/// spoke, and the lines that follow line up with its words, as the web app's chat does (items 843 and 859). At the
/// accessibility text sizes there is no room for a lane, so the face stands over the first line instead.
struct AgentLane<Content: View>: View {
    /// The face's column: the avatar's size, and the gap before the words.
    static var faceSize: CGFloat { 28 }

    let agentId: String?
    let wearsFace: Bool
    @ViewBuilder var content: Content
    @Environment(\.dynamicTypeSize) private var typeSize: DynamicTypeSize

    var body: some View {
        if typeSize.isAccessibilitySize {
            VStack(alignment: .leading, spacing: Space.xs) {
                if wearsFace { AgentAvatar(id: agentId, size: Self.faceSize) }
                content
            }
        } else {
            HStack(alignment: .top, spacing: Space.s) {
                ZStack {
                    if wearsFace { AgentAvatar(id: agentId, size: Self.faceSize) }
                }
                .frame(width: Self.faceSize, height: wearsFace ? Self.faceSize : 1)
                content
            }
        }
    }
}
