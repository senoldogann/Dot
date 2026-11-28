import DotEngine
import SwiftUI

/// Dot's own face: a light disc with two dark eyes on the ground (the app icon's mark, D12). Agents other than Dot get
/// a neutral disc with a symbol; never a hue taken from an id.
struct DotFace: View {
    let size: CGFloat

    var body: some View {
        DotFaceDrawing(size: size, pose: .rest)
    }
}

/// Dot's face in a pose (`LivingFace`): the eyes where it looks, as open as it says.
struct DotFaceDrawing: View {
    let size: CGFloat
    let pose: FacePose

    var body: some View {
        ZStack {
            Circle().fill(Tone.action)
            HStack(spacing: size * 0.15) {
                Capsule().fill(Tone.onAction).frame(width: size * 0.11, height: size * 0.22 * pose.open)
                Capsule().fill(Tone.onAction).frame(width: size * 0.11, height: size * 0.22 * pose.open)
            }
            .offset(x: size * pose.glance, y: -size * 0.02)
        }
        .frame(width: size, height: size)
        .accessibilityHidden(true)
    }
}

/// Another agent's avatar: the nested disc with a symbol in the secondary ink.
struct AgentAvatar: View {
    let size: CGFloat

    var body: some View {
        ZStack {
            Circle().fill(Tone.nested)
            Image(systemName: "person.crop.circle")
                .font(.system(size: size * 0.5, weight: .regular))
                .foregroundStyle(Tone.secondary)
        }
        .frame(width: size, height: size)
        .accessibilityHidden(true)
    }
}

/// The sheet recipe (plan §2, review S5): the sheet's own ground, the grabber, inline title. An inspector closes only
/// (✕ leading); an edit has ✕ leading and an ink ✓ trailing (the system's confirmation placement draws it prominent).
struct SheetChrome: ViewModifier {
    let title: String
    let close: () -> Void

    func body(content: Content) -> some View {
        NavigationStack {
            content
                .scrollContentBackground(.hidden)
                .background(Tone.sheet)
                .navigationTitle(title)
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) {
                        Button(String(localized: "Close", comment: "Sheet toolbar: closes an inspector sheet"), systemImage: "xmark", role: .close, action: close)
                    }
                }
        }
        .presentationDetents([.medium, .large])
        .presentationDragIndicator(.visible)
        .presentationBackground(Tone.sheet)
        .tint(Tone.text)
    }
}

/// The edit variant: ✕ cancels, the ink ✓ confirms; while there are unsaved edits a drag does not dismiss.
struct EditSheetChrome: ViewModifier {
    let title: String
    let isDirty: Bool
    let cancel: () -> Void
    let confirm: () -> Void

    func body(content: Content) -> some View {
        NavigationStack {
            content
                .scrollContentBackground(.hidden)
                .background(Tone.sheet)
                .navigationTitle(title)
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) {
                        Button(String(localized: "Cancel", comment: "Edit sheet toolbar: discards the edits"), systemImage: "xmark", role: .cancel, action: cancel)
                    }
                    ToolbarItem(placement: .confirmationAction) {
                        Button(String(localized: "Save", comment: "Edit sheet toolbar: keeps the edits"), systemImage: "checkmark", role: .confirm, action: confirm)
                            .disabled(!isDirty)
                    }
                }
        }
        .presentationDetents([.medium, .large])
        .presentationDragIndicator(.visible)
        .presentationBackground(Tone.sheet)
        .interactiveDismissDisabled(isDirty)
        .tint(Tone.text)
    }
}

extension View {
    func inspectorSheet(title: String, close: @escaping () -> Void) -> some View {
        modifier(SheetChrome(title: title, close: close))
    }

    func editSheet(title: String, isDirty: Bool, cancel: @escaping () -> Void, confirm: @escaping () -> Void)
        -> some View
    {
        modifier(EditSheetChrome(title: title, isDirty: isDirty, cancel: cancel, confirm: confirm))
    }

    /// A grouped page's column on a wide screen: at most 700 pt, centred (iPad regular width), the full width on a
    /// phone. Prose inside a chat uses `ProseColumn` instead.
    func readableColumn() -> some View {
        frame(maxWidth: 700).frame(maxWidth: .infinity)
    }

    /// A page's ground behind a scrolling grouped page.
    func pageGround() -> some View {
        scrollContentBackground(.hidden).background(Tone.ground)
    }
}

/// The icon sample sheet (review S4): every symbol the app uses, grouped by context, each in its context's one weight.
struct SymbolSampleSheet: View {
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Space.groupedGutter) {
                ForEach(SymbolContext.allCases, id: \.self) { context in
                    GroupedSection(header: context.title, footer: nil) {
                        LazyVGrid(columns: [GridItem(.adaptive(minimum: 52), spacing: Space.xs)], spacing: Space.s) {
                            ForEach(context.symbols, id: \.self) { name in
                                Symbol(name: name, context: context)
                                    .foregroundStyle(context == .chevron ? Tone.glyph : Tone.text)
                                    .frame(width: Metric.hit, height: Metric.hit)
                                    .accessibilityHidden(false)
                                    .accessibilityLabel(name)
                            }
                        }
                        .padding(Space.groupedGutter)
                    }
                }
            }
            .padding(.horizontal, Space.gutter)
            .padding(.vertical, Space.groupedGutter)
        }
    }
}
