import SwiftUI

/// What stands at a row's trailing edge.
enum RowAccessory: Equatable, Sendable {
    case none
    case chevron
    case detail(String)
    case detailAndChevron(String)
}

/// A list row (review S4, R-a §2): 49 pt, 63 with a description, never under the 44 pt hit floor; a leading SF Symbol
/// in a 22 pt slot, the title in body ink, a description in secondary ink, a trailing detail and/or chevron. At the
/// accessibility text sizes the detail stands under the title instead of squeezing it. VoiceOver reads one label.
struct ListRow: View {
    let systemImage: String?
    let title: String
    let description: String?
    let accessory: RowAccessory
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize: DynamicTypeSize

    var body: some View {
        HStack(spacing: Space.s) {
            if let systemImage {
                Symbol(name: systemImage, context: .row)
                    .foregroundStyle(Tone.secondary)
                    .frame(width: Metric.iconSlot)
            }
            if dynamicTypeSize.isAccessibilitySize {
                VStack(alignment: .leading, spacing: Space.xxs) {
                    texts
                    if let detail { Text(detail).textRole(.footnote).foregroundStyle(Tone.secondary) }
                }
                Spacer(minLength: 0)
            } else {
                VStack(alignment: .leading, spacing: Space.xxs) { texts }
                Spacer(minLength: Space.s)
                if let detail {
                    Text(detail).textRole(.footnote).foregroundStyle(Tone.secondary).lineLimit(1)
                        .truncationMode(.middle)
                        .tabularNumbers()
                }
            }
            if showsChevron {
                Symbol(name: "chevron.right", context: .chevron).foregroundStyle(Tone.glyph)
            }
        }
        .padding(.horizontal, Space.groupedGutter)
        .padding(.vertical, Space.s)
        .frame(minHeight: description == nil ? Metric.row : Metric.rowWithDescription)
        .contentShape(Rectangle())
        .accessibilityElement(children: .ignore)
        .accessibilityLabel([title, description, detail].compactMap { $0 }.joined(separator: ", "))
    }

    @ViewBuilder private var texts: some View {
        Text(title).textRole(.body).foregroundStyle(Tone.text)
        if let description {
            Text(description).textRole(.footnote).foregroundStyle(Tone.secondary)
        }
    }

    private var detail: String? {
        switch accessory {
        case .detail(let text), .detailAndChevron(let text): text
        case .none, .chevron: nil
        }
    }

    private var showsChevron: Bool {
        switch accessory {
        case .chevron, .detailAndChevron: true
        case .none, .detail: false
        }
    }
}

/// A grouped section (R-a §2): a header in secondary ink, the rows on one card step at radius 24 with NO stroke and no
/// separators (rows are told apart by their spacing), an optional footer in tertiary ink.
struct GroupedSection<Content: View>: View {
    let header: String?
    let footer: String?
    @ViewBuilder let content: () -> Content

    var body: some View {
        VStack(alignment: .leading, spacing: Space.xs) {
            if let header {
                Text(header).textRole(.footnote).fontWeight(.medium).foregroundStyle(Tone.secondary)
                    .padding(.horizontal, Space.xs)
                    .accessibilityAddTraits(.isHeader)
            }
            VStack(spacing: 0) { content() }
                .background(Tone.card, in: rounded(Radius.section))
                .clipShape(rounded(Radius.section))
            if let footer {
                Text(footer).textRole(.footnote).foregroundStyle(Tone.tertiary)
                    .padding(.horizontal, Space.groupedGutter)
            }
        }
    }
}

/// A choice in a radio list: only the ink checkmark marks the chosen row (R-a §2); VoiceOver hears "selected".
struct RadioRow: View {
    let title: String
    let description: String?
    let isSelected: Bool
    let select: () -> Void

    var body: some View {
        Button(action: select) {
            HStack(spacing: Space.s) {
                VStack(alignment: .leading, spacing: Space.xxs) {
                    Text(title).textRole(.body).foregroundStyle(Tone.text)
                    if let description {
                        Text(description).textRole(.footnote).foregroundStyle(Tone.secondary)
                    }
                }
                Spacer(minLength: Space.s)
                Image(systemName: "checkmark").font(.system(.callout, weight: .semibold))
                    .foregroundStyle(Tone.text)
                    .opacity(isSelected ? 1 : 0)
                    .accessibilityHidden(true)
            }
            .padding(.horizontal, Space.groupedGutter)
            .padding(.vertical, Space.s)
            .frame(minHeight: description == nil ? Metric.row : Metric.rowWithDescription)
            .contentShape(Rectangle())
        }
        .buttonStyle(RowPressStyle())
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(isSelected ? [.isSelected] : [])
    }
}

/// A row with a system switch, tinted with the ink (no hue, D5); the description is VoiceOver's hint.
struct ToggleRow: View {
    let systemImage: String?
    let title: String
    let description: String?
    @Binding var isOn: Bool

    var body: some View {
        Toggle(isOn: $isOn) {
            HStack(spacing: Space.s) {
                if let systemImage {
                    Symbol(name: systemImage, context: .row).foregroundStyle(Tone.secondary)
                        .frame(width: Metric.iconSlot)
                }
                VStack(alignment: .leading, spacing: Space.xxs) {
                    Text(title).textRole(.body).foregroundStyle(Tone.text)
                    if let description {
                        Text(description).textRole(.footnote).foregroundStyle(Tone.secondary)
                    }
                }
            }
        }
        .toggleStyle(InkSwitchStyle())
        .padding(.horizontal, Space.groupedGutter)
        .padding(.vertical, Space.s)
        .frame(minHeight: description == nil ? Metric.row : Metric.rowWithDescription)
        .accessibilityHint(description ?? "")
    }
}

/// A row's pressed look: the nested fill, no scale.
struct RowPressStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .background(configuration.isPressed ? Tone.nested : Color.clear)
    }
}

/// A navigation row inside a grouped section.
struct NavigationRow<Destination: View>: View {
    let systemImage: String?
    let title: String
    let description: String?
    let detail: String?
    @ViewBuilder let destination: () -> Destination

    var body: some View {
        NavigationLink(destination: destination) {
            ListRow(
                systemImage: systemImage, title: title, description: description,
                accessory: detail.map { RowAccessory.detailAndChevron($0) } ?? .chevron)
        }
        .buttonStyle(RowPressStyle())
    }
}

/// The switch in the ink (D5, review S3): on = the ink track with a thumb in the ground's colour, off = the nested track
/// with an ink thumb, so the thumb reads against its track in both appearances (the system thumb is white, which
/// vanishes on a light ink track in the dark look). The `Toggle` keeps its system accessibility (a switch, on/off).
struct InkSwitchStyle: ToggleStyle {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion: Bool
    @Environment(\.isEnabled) private var isEnabled: Bool

    func makeBody(configuration: Configuration) -> some View {
        HStack(spacing: Space.s) {
            configuration.label
            Spacer(minLength: Space.s)
            Button {
                configuration.isOn.toggle()
            } label: {
                Capsule()
                    .fill(configuration.isOn ? Tone.action : Tone.nested)
                    .frame(width: 51, height: 31)
                    .overlay(alignment: configuration.isOn ? .trailing : .leading) {
                        Circle()
                            .fill(configuration.isOn ? Tone.onAction : Tone.text)
                            .frame(width: 25, height: 25)
                            .padding(3)
                    }
                    .frame(minWidth: Metric.hit, minHeight: Metric.hit)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .opacity(isEnabled ? 1 : 0.45)
            .accessibilityHidden(true)
        }
        .contentShape(Rectangle())
        .onTapGesture { configuration.isOn.toggle() }
        .haptic(.selection, trigger: configuration.isOn)
        .animation(motionAnimation(.adjust, reduceMotion: reduceMotion), value: configuration.isOn)
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isToggle)
        .accessibilityValue(
            configuration.isOn
                ? String(localized: "On", comment: "VoiceOver value of a switch that is on")
                : String(localized: "Off", comment: "VoiceOver value of a switch that is off"))
        .accessibilityAction { configuration.isOn.toggle() }
    }
}
