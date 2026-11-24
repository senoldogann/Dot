import SwiftUI

/// Liquid Glass for floating chrome only (plan §3): the composer, the working capsule and its circle, toasts. Under
/// Reduce Transparency the glass becomes the opaque `glassFallback` fill; pages and cards never use this.
struct FloatingGlass<S: Shape>: ViewModifier {
    let shape: S
    let glass: Glass
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency: Bool

    func body(content: Content) -> some View {
        if reduceTransparency {
            content.background(Tone.glassFallback, in: shape)
        } else {
            content.glassEffect(glass, in: shape)
        }
    }
}

extension View {
    /// Glass that only shows (a toast, a status).
    func floatingGlass<S: Shape>(in shape: S) -> some View {
        modifier(FloatingGlass(shape: shape, glass: .regular))
    }

    /// Glass that answers a touch (the composer, a capsule segment, the scroll-to-bottom circle).
    func interactiveGlass<S: Shape>(in shape: S) -> some View {
        modifier(FloatingGlass(shape: shape, glass: .regular.interactive()))
    }
}
