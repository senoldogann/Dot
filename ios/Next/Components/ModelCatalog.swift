import DotEngine
import SwiftUI

/// The app's one model catalog (PLAN §22/§24, owner-refs/ref-model-picker-1/2.png), used by every model picker (the
/// chat's settings, Settings › Models): the chosen and recent models on top, then the caller's options, then the
/// providers as groups with their mark that fold (a count when folded; all but the chosen model's start folded, so
/// hundreds of models are no long scroll), each model once in its group with its provider's mark, "Default" under the
/// daemon's main, a ✓ on the chosen one and a quiet ★ on a favourite (a long press stars it; favourites float up in their
/// group), or an eye while hidden models are managed. The search and the filter
/// are the caller's (`ModelSearchBar`); the favourites and hidden models are kept on the phone (`ModelMarks`) at once,
/// whatever the caller then keeps or drops of its own picks.
struct ModelCatalog<Options: View>: View {
    let providers: [ProviderModels]
    let defaultRef: String?
    let chosenRef: String?
    let query: String
    let filter: ModelFilter
    let managesHidden: Bool
    @Binding var marks: ModelMarks
    let pick: (ModelOption) -> Void
    /// Why the models or the daemon's defaults could not be read (`nil` while they are read or once they are), and how
    /// to read them again.
    let problem: String?
    let retry: () -> Void
    @ViewBuilder let options: () -> Options
    /// The groups the owner opened or folded by hand.
    @State private var opened: Set<String> = []
    @State private var closed: Set<String> = []
    private let recent: [String] = RecentModels.read(.standard)

    var body: some View {
        let groups: [ModelGroup] = modelGroups(
            providers, marks: marks, defaultRef: defaultRef, chosenRef: chosenRef, showsHidden: managesHidden, query: query, filter: filter)
        Group {
            // Any failed read says so with a retry: the models', or the defaults' (no ✓ and no Default without them).
            if let problem {
                VStack(alignment: .leading, spacing: Space.xs) {
                    Text(String(localized: "The models could not be read: \(problem)", comment: "Model picker: the daemon's models could not be read; the argument is why"))
                        .textRole(.footnote).foregroundStyle(Tone.secondary)
                    Button(String(localized: "Try again", comment: "Under a refused message: sends it again"), action: retry)
                        .buttonStyle(SecondaryButtonStyle())
                        .accessibilityIdentifier("modelCatalog.retry")
                }
                .padding(.horizontal, Space.groupedGutter)
            } else if providers.isEmpty {
                LoadingState(message: String(localized: "Reading the models…", comment: "Chat settings: the daemon's models are being read")).frame(minHeight: 160)
            } else if groups.isEmpty {
                Text(String(localized: "No model matches.", comment: "Chat settings: the search or filter left no model"))
                    .textRole(.footnote).foregroundStyle(Tone.secondary).padding(.horizontal, Space.groupedGutter)
            }
            if query.isEmpty, filter == .all, !recentModels.isEmpty {
                InsetSection(header: String(localized: "Recently used", comment: "Model picker section: the chosen model and the ones picked last")) {
                    ForEach(recentModels) { row in modelRow(row) }
                }
            }
            options()
            ForEach(groups) { group in providerSection(group) }
            if query.isEmpty {
                // A provider that listed nothing says why, as Settings › Models does.
                ForEach(failedProviders(providers), id: \.provider) { failed in
                    Text(verbatim: providerTitle(failed.provider) + " · " + failed.why)
                        .textRole(.footnote).foregroundStyle(Tone.tertiary).padding(.horizontal, Space.groupedGutter)
                }
            }
        }
        .onChange(of: marks) { _, next in next.write(.standard) }
    }


    @ViewBuilder private func providerSection(_ group: ModelGroup) -> some View {
        let isFolded: Bool = query.isEmpty && isFoldedNow(group)
        VStack(alignment: .leading, spacing: Space.xs) {
            Button {
                if isFoldedNow(group) {
                    opened.insert(group.provider)
                    closed.remove(group.provider)
                } else {
                    closed.insert(group.provider)
                    opened.remove(group.provider)
                }
            } label: {
                HStack(spacing: Space.xs) {
                    Image(providerMarkAsset(group.provider)).resizable().renderingMode(.template).scaledToFit()
                        .frame(width: 18, height: 18).foregroundStyle(Tone.secondary)
                    Text(providerTitle(group.provider)).textRole(.footnote).fontWeight(.medium).foregroundStyle(Tone.secondary)
                    Spacer(minLength: Space.xs)
                    if isFolded { Text(verbatim: "\(group.rows.count)").textRole(.footnote).foregroundStyle(Tone.tertiary).tabularNumbers() }
                    Symbol(name: isFolded ? "chevron.down" : "chevron.up", context: .chevron).foregroundStyle(Tone.tertiary)
                }
                .padding(.horizontal, Space.groupedGutter)
                .frame(minHeight: Metric.hit)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityAddTraits(.isHeader)
            .accessibilityValue(
                isFolded
                    ? String(localized: "Folded, \(group.rows.count) models", comment: "VoiceOver: a folded provider group; the argument is its model count")
                    : String(localized: "Open", comment: "VoiceOver: an open provider group"))
            .accessibilityIdentifier("modelCatalog.provider")
            if !isFolded {
                InsetSection(header: nil) { ForEach(group.rows) { row in modelRow(row) } }
            }
        }
    }

    /// A group is open when the owner opened it, or holds the chosen model and was not folded by hand.
    private func isFoldedNow(_ group: ModelGroup) -> Bool {
        if opened.contains(group.provider) { return false }
        if closed.contains(group.provider) { return true }
        return !group.rows.contains { $0.option.ref == chosenRef }
    }

    private var recentModels: [ModelRow] {
        recentRows(providers, recent: recent, chosenRef: chosenRef, defaultRef: defaultRef, marks: marks, showsHidden: managesHidden, limit: 4)
    }

    /// A model: its provider's mark, its name with a quiet ★ when starred and "Default" under it for the daemon's main,
    /// a ✓ on the chosen one. A long press (or VoiceOver's actions) stars it; while hidden models are managed, an eye
    /// hides or shows it. No column of empty stars and no badge: the owner's report of 2026-11-27.
    private func modelRow(_ row: ModelRow) -> some View {
        let isChosen: Bool = row.option.ref == chosenRef
        let favourite: String =
            row.isFavourite
            ? String(localized: "Remove \(row.option.label) from favourites", comment: "VoiceOver: unstars a model; the argument is its name")
            : String(localized: "Add \(row.option.label) to favourites", comment: "VoiceOver: stars a model; the argument is its name")
        return HStack(spacing: Space.s) {
            Button {
                pick(row.option)
            } label: {
                HStack(spacing: Space.s) {
                    Image(providerMarkAsset(providerOfModel(row.option.ref, in: providers))).resizable().renderingMode(.template).scaledToFit()
                        .frame(width: 20, height: 20).foregroundStyle(row.isHidden ? Tone.tertiary : Tone.text)
                        .frame(minWidth: Metric.iconSlot)
                        .accessibilityHidden(true)
                    VStack(alignment: .leading, spacing: Space.xxs) {
                        HStack(spacing: Space.xxs + 2) {
                            Text(row.option.label).textRole(.body).foregroundStyle(row.isHidden ? Tone.tertiary : Tone.text).lineLimit(2)
                            if row.isFavourite {
                                Image(systemName: "star.fill").font(.system(.caption2)).foregroundStyle(Tone.secondary).accessibilityHidden(true)
                            }
                        }
                        if row.isDefault {
                            Text(String(localized: "Default", comment: "Chat settings: the badge of the daemon's main model"))
                                .textRole(.footnote).foregroundStyle(Tone.secondary)
                        }
                    }
                    Spacer(minLength: Space.xs)
                    Image(systemName: "checkmark").font(.system(.body, weight: .semibold)).foregroundStyle(Tone.text)
                        .opacity(isChosen ? 1 : 0).accessibilityHidden(true)
                }
                .padding(.vertical, Space.xs)
                .frame(minHeight: row.isDefault ? Metric.rowWithDescription : Metric.row)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel(
                [row.option.label, row.isDefault ? String(localized: "Default", comment: "Chat settings: the badge of the daemon's main model") : nil,
                 row.isFavourite ? String(localized: "Favourite", comment: "VoiceOver: a starred model") : nil].compactMap { $0 }.joined(separator: ", "))
            .accessibilityAddTraits(isChosen ? [.isSelected] : [])
            .accessibilityAction(named: favourite) { marks = marks.togglingFavourite(row.option.ref) }
            .accessibilityIdentifier("modelCatalog.model")
            .contextMenu {
                Button(favourite, systemImage: row.isFavourite ? "star.slash" : "star") { marks = marks.togglingFavourite(row.option.ref) }
                    .accessibilityIdentifier("modelCatalog.star")
            }
            if managesHidden {
                Button { marks = marks.togglingHidden(row.option.ref) } label: {
                    Image(systemName: row.isHidden ? "eye.slash" : "eye").font(.system(.body, weight: .regular)).foregroundStyle(Tone.secondary)
                        .frame(width: Metric.hit, height: Metric.hit).contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel(
                    row.isHidden
                        ? String(localized: "Show \(row.option.label)", comment: "VoiceOver: shows a hidden model again; the argument is its name")
                        : String(localized: "Hide \(row.option.label)", comment: "VoiceOver: hides a model from the list; the argument is its name"))
            }
        }
        .padding(.leading, Space.groupedGutter)
        .padding(.trailing, managesHidden ? Space.xxs : Space.groupedGutter)
        .haptic(.selection, trigger: isChosen)
        .haptic(.selection, trigger: row.isFavourite)
    }
}

/// The floating search of a model picker: the filter (all, favourites, free) and "Find a model".
struct ModelSearchBar: View {
    @Binding var query: String
    @Binding var filter: ModelFilter

    var body: some View {
        HStack(spacing: Space.xs) {
            Menu {
                Picker(selection: $filter) {
                    Text(String(localized: "All models", comment: "Chat settings filter: every model")).tag(ModelFilter.all)
                    Text(String(localized: "Favourites", comment: "Chat settings filter: the starred models")).tag(ModelFilter.favourites)
                    Text(String(localized: "Free", comment: "Chat settings filter: the models served at no cost")).tag(ModelFilter.free)
                } label: {
                    EmptyView()
                }
            } label: {
                Image(systemName: filter == .all ? "line.3.horizontal.decrease" : "line.3.horizontal.decrease.circle.fill")
                    .font(.system(.body, weight: .medium)).foregroundStyle(Tone.text)
                    .frame(width: 48, height: 48).contentShape(Circle())
            }
            .interactiveGlass(in: Circle())
            .accessibilityLabel(String(localized: "Filter the models", comment: "VoiceOver: the filter button beside the model search"))
            .accessibilityIdentifier("modelCatalog.filter")
            HStack(spacing: Space.xs) {
                Image(systemName: "magnifyingglass").foregroundStyle(Tone.secondary)
                TextField(String(localized: "Find a model", comment: "Chat settings: the search field's placeholder"), text: $query)
                    .textRole(.body).foregroundStyle(Tone.text)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
                    .accessibilityIdentifier("modelCatalog.search")
                if !query.isEmpty {
                    Button { query = "" } label: { Image(systemName: "xmark.circle.fill").foregroundStyle(Tone.tertiary) }
                        .buttonStyle(.plain)
                        .accessibilityLabel(String(localized: "Clear the search", comment: "VoiceOver: empties the model search"))
                }
            }
            .padding(.horizontal, Space.m)
            .frame(height: 48)
            .interactiveGlass(in: Capsule())
        }
        .padding(.horizontal, Space.gutter)
        .padding(.bottom, Space.xs)
    }
}

/// A section of a picker sheet, inset and grouped as iOS draws its settings (the owner's report of 2026-11-27): the rows
/// on one card, a hairline between two of them from the text's edge, the header in quiet ink over it.
struct InsetSection<Content: View>: View {
    let header: String?
    @ViewBuilder let content: () -> Content
    @Environment(\.displayScale) private var displayScale: CGFloat

    var body: some View {
        VStack(alignment: .leading, spacing: Space.xs) {
            if let header {
                Text(header).textRole(.footnote).fontWeight(.medium).foregroundStyle(Tone.secondary)
                    .padding(.horizontal, Space.groupedGutter)
                    .accessibilityAddTraits(.isHeader)
            }
            VStack(spacing: 0) {
                Group(subviews: content()) { rows in
                    ForEach(Array(rows.enumerated()), id: \.element.id) { index, row in
                        row
                        if index < rows.count - 1 {
                            Rectangle().fill(Tone.separator).frame(height: 1 / displayScale)
                                .padding(.leading, Space.groupedGutter)
                                .accessibilityHidden(true)
                        }
                    }
                }
            }
            .background(Tone.card, in: rounded(Radius.section))
            .clipShape(rounded(Radius.section))
        }
    }
}
