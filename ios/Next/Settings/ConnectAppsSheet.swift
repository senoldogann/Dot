import DotEngine
import SwiftUI
import UIKit

/// Connect apps (item 1862, PLAN §27, the owner's grok-connect-apps.png in Dot's words): every service of the Mac's
/// Connectors catalogue in one sheet. ✕, the title and an "N connected" pill that lists the connected ones alone; the
/// catalogue's sections as chips (All first); Featured (the web's Popular, at most eight), then each section; a row per
/// service with its logo, name, one line and a Connect or Connected pill; a floating search over apps and skills. A row
/// opens the service's detail. Connect takes the daemon's own step: a Mac application's is started from here and
/// answered on the Mac; adding a connected app, an official sign-in and 1Password's token are taken on the Mac alone
/// (the daemon refuses a phone's key, item 678), so the row says "Finish on your Mac". "For you" is not shown: the
/// daemon gives no suggestion for services.
struct ConnectAppsSheet: View {
    /// The service whose detail opens at once (screenshot fixtures); `nil` opens the list.
    let opensDetail: String?
    @Environment(AppModel.self) private var model: AppModel
    @Environment(\.dismiss) private var dismiss: DismissAction
    @State private var sources: Loadable<ConnectSources> = .loading
    @State private var refreshProblem: SettingsFailure?
    @State private var section: String?
    @State private var onlyConnected: Bool = false
    @State private var query: String = ""
    @State private var shown: ConnectApp?
    /// The services whose step the owner started here and finishes on the Mac.
    @State private var finishing: Set<String> = []
    @State private var stepProblem: SettingsFailure?
    @FocusState private var searching: Bool

    var body: some View {
        NavigationStack {
            content
                .background(Tone.sheet)
                .navigationTitle(String(localized: "Connect apps", comment: "Title of the Connect apps sheet"))
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) {
                        Button(String(localized: "Close", comment: "Sheet toolbar: closes an inspector sheet"), systemImage: "xmark", role: .close) { dismiss() }
                    }
                    if let apps = sources.value.map(connectApps) {
                        ToolbarItem(placement: .primaryAction) { connectedPill(apps.filter(\.isConnected).count) }
                    }
                }
                .navigationDestination(for: SettingsPage.self) { page in
                    if page == .mac { MacPage() }
                }
        }
        .presentationDragIndicator(.visible)
        .presentationBackground(Tone.sheet)
        .tint(Tone.text)
        .task { await load() }
        .task { await keepReading() }
        .sheet(item: $shown) { app in
            ConnectAppDetail(
                app: current(app), finishing: finishing.contains(app.id), problem: stepProblem,
                connect: { await connect(current(app)) },
                disconnect: { await disconnect(current(app)) })
        }
    }

    // MARK: States

    @ViewBuilder private var content: some View {
        switch sources {
        case .loading:
            LoadingState(message: String(localized: "Reading your Mac…", comment: "Settings: a page waits for its first read"))
        case .failed(let failure):
            if failure == .unreachable {
                EmptyState(
                    title: String(localized: "Can't reach your Mac", comment: "Connect apps: no answer from the Mac and nothing to show"),
                    systemImage: "wifi.slash",
                    detail: String(localized: "The apps Dot works with are listed on your Mac. Check the connection and try again.", comment: "Connect apps: the offline state's line"),
                    actionTitle: String(localized: "Try again", comment: "Settings: repeats a read or write that failed")
                ) { Task { await load() } }
            } else {
                ScrollView {
                    FailureBanner(failure: failure, macPlace: macPlace, retry: { Task { await load() } }).padding(Space.gutter)
                }
            }
        case .loaded(let loaded):
            list(connectApps(loaded))
        }
    }

    private var macPlace: String { String(localized: "Settings › Connectors", comment: "Where on the Mac: the Connectors page") }

    private func list(_ apps: [ConnectApp]) -> some View {
        let words: String = query.trimmingCharacters(in: .whitespaces)
        let skills: [PaletteSkill] = filterSkills(model.palette?.skills ?? [], query: words)
        return ScrollView {
            LazyVStack(alignment: .leading, spacing: 0, pinnedViews: []) {
                chips(connectSections(apps))
                if let refreshProblem {
                    FailureBanner(failure: refreshProblem, macPlace: macPlace, retry: { Task { await load() } })
                        .padding(.horizontal, Space.gutter).padding(.bottom, 8)
                }
                if !words.isEmpty || onlyConnected || section != nil {
                    filtered(filterConnectApps(apps, section: section, onlyConnected: onlyConnected, query: words), skills: skills, words: words)
                } else {
                    whole(apps)
                }
            }
            .padding(.bottom, 64)
            .readableColumn()
        }
        .scrollDismissesKeyboard(.interactively)
        .refreshable { await load() }
        .safeAreaInset(edge: .bottom) { searchCapsule }
    }

    /// No chip, no search: Featured, then every section of the catalogue but Featured's.
    @ViewBuilder private func whole(_ apps: [ConnectApp]) -> some View {
        let featured: [ConnectApp] = Array(apps.filter { $0.section == featuredSection }.prefix(featuredLimit))
        if apps.isEmpty {
            EmptyState(
                title: String(localized: "No apps listed", comment: "Connect apps: the Mac lists no service"),
                systemImage: "square.grid.2x2",
                detail: String(localized: "Your Mac's Dot lists no app to connect. Update Dot on the Mac.", comment: "Connect apps: the empty state's line"),
                actionTitle: nil
            ) {}
            .padding(.top, 24)
        }
        if !featured.isEmpty {
            group(featuredSection, apps: featured)
        }
        ForEach(connectSections(apps).filter { $0 != featuredSection }, id: \.self) { one in
            group(one, apps: apps.filter { $0.section == one })
        }
    }

    @ViewBuilder private func filtered(_ apps: [ConnectApp], skills: [PaletteSkill], words: String) -> some View {
        if apps.isEmpty && skills.isEmpty {
            if words.isEmpty && onlyConnected {
                EmptyState(
                    title: String(localized: "Nothing connected yet", comment: "Connect apps: no service is connected"),
                    systemImage: "powerplug",
                    detail: String(localized: "Connect an app and Dot can use it in its tasks.", comment: "Connect apps: the empty state of the connected list"),
                    actionTitle: String(localized: "Show all apps", comment: "Connect apps: leaves the connected list")
                ) { onlyConnected = false }
                .padding(.top, 24)
            } else {
                EmptyState(
                    title: words.isEmpty
                        ? String(localized: "No apps here", comment: "Connect apps: a chip lists nothing")
                        : String(localized: "No results for “\(words)”", comment: "Connect apps: a search found nothing; the argument is the query"),
                    systemImage: "magnifyingglass",
                    detail: String(localized: "Try another word or another section.", comment: "Connect apps: the search empty state's line"),
                    actionTitle: String(localized: "Clear search", comment: "Connect apps: clears the search and the filters")
                ) {
                    query = ""
                    section = nil
                    onlyConnected = false
                }
                .padding(.top, 24)
            }
        } else {
            if !apps.isEmpty {
                header(
                    words.isEmpty && !onlyConnected
                        ? sectionTitle(section ?? featuredSection)
                        : String(localized: "Apps", comment: "Connect apps: the header of the apps a search or a filter found"),
                    opens: nil)
                ForEach(apps) { app in row(app) }
            }
            if !skills.isEmpty {
                header(String(localized: "Skills", comment: "Composer picker header: the skills a run can use"), opens: nil)
                ForEach(skills, id: \.name) { skill in skillRow(skill) }
            }
        }
    }

    private func group(_ id: String, apps: [ConnectApp]) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            header(sectionTitle(id), opens: id)
            ForEach(apps) { app in row(app) }
        }
    }

    // MARK: Parts

    private func connectedPill(_ count: Int) -> some View {
        Button {
            onlyConnected.toggle()
            section = nil
        } label: {
            Text(String(localized: "\(count) connected", comment: "Connect apps: the pill that lists the connected services alone; the argument is how many"))
                .textRole(.body).fontWeight(.medium)
                .foregroundStyle(onlyConnected ? Tone.onAction : Tone.text)
                .padding(.horizontal, ConnectMetric.inset)
                .frame(minHeight: 36)
                .background(onlyConnected ? Tone.action : Color.clear, in: Capsule())
                .contentShape(Capsule())
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(onlyConnected ? [.isSelected] : [])
        .accessibilityHint(String(localized: "Lists the connected apps alone", comment: "VoiceOver: the hint of the connected pill"))
        .haptic(.selection, trigger: onlyConnected)
    }

    private func chips(_ sections: [String]) -> some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                Chip(title: String(localized: "All", comment: "Connect apps: the chip of every section"), systemImage: nil, isSelected: section == nil && !onlyConnected) {
                    section = nil
                    onlyConnected = false
                }
                ForEach(sections, id: \.self) { one in
                    Chip(title: sectionTitle(one), systemImage: nil, isSelected: section == one) {
                        section = section == one ? nil : one
                        onlyConnected = false
                    }
                }
            }
            .padding(.horizontal, Space.gutter)
        }
        .haptic(.selection, trigger: section)
    }

    private func header(_ title: String, opens: String?) -> some View {
        Group {
            if let opens {
                Button {
                    section = opens
                } label: {
                    HStack(spacing: ConnectMetric.lineGap) {
                        Text(title).textRole(.headline).fontWeight(.regular).foregroundStyle(Tone.text)
                        Symbol(name: "chevron.right", context: .chevron).foregroundStyle(Tone.glyph)
                    }
                    .frame(minHeight: Metric.hit)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityAddTraits(.isHeader)
            } else {
                Text(title).textRole(.headline).fontWeight(.regular).foregroundStyle(Tone.text)
                    .frame(minHeight: Metric.hit)
                    .accessibilityAddTraits(.isHeader)
            }
        }
        .padding(.horizontal, ConnectMetric.inset)
        .padding(.top, ConnectMetric.lineGap)
    }

    private func row(_ app: ConnectApp) -> some View {
        ConnectAppRow(app: app, finishing: finishing.contains(app.id) || app.action == .waiting, open: { shown = app }, connect: { Task { await connect(app) } })
    }

    private func skillRow(_ skill: PaletteSkill) -> some View {
        HStack(spacing: ConnectMetric.gap) {
            LogoTile(logo: nil, glyph: "puzzlepiece.extension", name: skill.name)
            VStack(alignment: .leading, spacing: 2) {
                Text(skill.name).textRole(.body).foregroundStyle(Tone.text)
                Text(skill.description).textRole(.footnote).foregroundStyle(Tone.secondary).lineLimit(2)
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, ConnectMetric.inset)
        .padding(.vertical, ConnectMetric.rowPadding)
        .accessibilityElement(children: .combine)
    }

    private var searchCapsule: some View {
        HStack(spacing: ConnectMetric.gap) {
            Image(systemName: "magnifyingglass").font(.system(.body, weight: .medium)).foregroundStyle(Tone.glyph)
            TextField(String(localized: "Search apps and skills", comment: "Connect apps: the search field's placeholder"), text: $query)
                .textRole(.body)
                .focused($searching)
                .submitLabel(.search)
                .autocorrectionDisabled()
                .textInputAutocapitalization(.never)
            if !query.isEmpty {
                Button {
                    query = ""
                } label: {
                    Image(systemName: "xmark.circle.fill").foregroundStyle(Tone.glyph).frame(width: Metric.hit, height: Metric.hit)
                }
                .buttonStyle(.plain)
                .accessibilityLabel(String(localized: "Clear search", comment: "Connect apps: clears the search and the filters"))
            }
        }
        .padding(.leading, ConnectMetric.inset)
        .padding(.trailing, query.isEmpty ? ConnectMetric.inset : ConnectMetric.lineGap)
        .frame(minHeight: 50)
        .interactiveGlass(in: Capsule())
        .padding(.horizontal, Space.gutter)
        .padding(.bottom, 8)
    }

    // MARK: Reads and writes

    /// The service `app` as the latest read has it (a step or a Disconnect changes where it stands).
    private func current(_ app: ConnectApp) -> ConnectApp {
        sources.value.flatMap { loaded in connectApps(loaded).first { $0.id == app.id } } ?? app
    }

    private func load() async {
        await model.loadPalette()
        let read: Loadable<ConnectSources> = await model.read { api throws(DaemonRequestError) in try await api.connectSources() }
        switch (read, sources) {
        case (.failed(let failure), .loaded):
            // A refresh that fails keeps what was read and says why above it (STATES-TEMPLATE row 12).
            refreshProblem = failure
        default:
            refreshProblem = nil
            sources = read
        }
        if let opensDetail, shown == nil, let found = sources.value.flatMap({ connectApps($0).first { $0.id == opensDetail } }) { shown = found }
    }

    private func connect(_ app: ConnectApp) async {
        stepProblem = nil
        switch app.action {
        case .stepOnMac(let id):
            switch await model.write({ api throws(DaemonRequestError) in try await api.takeConnectionStep(app: id) }) {
            case .success:
                finishing.insert(app.id)
                await load()
            case .failure(let box):
                stepProblem = box.failure
                shown = app
            }
        case .onMacOnly:
            finishing.insert(app.id)
            shown = app
        case .connected, .waiting, .nothing:
            shown = app
        }
    }

    /// Takes away every connection of `app` Dot can take back; the first refusal stops and is shown. A sign-in the
    /// provider did not end comes back with the daemon's words of where to end it (item 1312).
    private func disconnect(_ app: ConnectApp) async -> DisconnectOutcome {
        var notices: [String] = []
        for removal in app.removals {
            let outcome: Result<String?, SettingsFailureBox> = await model.write { api throws(DaemonRequestError) in
                switch removal {
                case .server(let name):
                    _ = try await api.removeMcpServer(name)
                    return nil
                case .vault:
                    _ = try await api.disconnectVault()
                    return nil
                case .account(let provider, let address):
                    let signedOut: SignedOutView = try await api.signOut(provider: provider, address: address)
                    return signedOut.revoked ? nil : signedOut.notice
                }
            }
            switch outcome {
            case .success(let notice):
                if let notice { notices.append(notice) }
            case .failure(let box):
                await load()
                return DisconnectOutcome(failure: box.failure, notices: notices)
            }
        }
        finishing.remove(app.id)
        await load()
        return DisconnectOutcome(failure: nil, notices: notices)
    }

    /// Whether the daemon says a service waits (macOS asks on the Mac, Dot tries a server or waits for its sign-in), so
    /// the sheet reads again by itself, as the web asks again then and only then.
    private var waitsOnTheMac: Bool {
        guard let loaded = sources.value else { return false }
        return connectApps(loaded).contains { $0.action == .waiting }
    }

    /// Reads again every five seconds while a service waits on the Mac, until the sheet closes.
    private func keepReading() async {
        while !Task.isCancelled {
            do {
                try await Task.sleep(for: .seconds(5))
            } catch {
                return
            }
            if waitsOnTheMac { await load() }
        }
    }
}

/// What a Disconnect came to: the refusal that stopped it, and the providers' notices of a grant they kept.
struct DisconnectOutcome: Sendable {
    let failure: SettingsFailure?
    let notices: [String]
}

/// The words of a section of the catalogue (`views/connectors.js` SECTIONS); a section a newer daemon adds is shown by
/// its id.
func sectionTitle(_ id: String) -> String {
    switch id {
    case featuredSection: String(localized: "Featured", comment: "Connect apps: the section of the most used services")
    case "passwords": String(localized: "Password managers", comment: "Connect apps: a section of services")
    case "code": String(localized: "Code", comment: "Connect apps: a section of services")
    case "design": String(localized: "Design", comment: "Connect apps: a section of services")
    case "data": String(localized: "Data", comment: "Connect apps: a section of services")
    case "research": String(localized: "Research", comment: "Connect apps: a section of services")
    case "communication": String(localized: "Communication", comment: "Connect apps: a section of services")
    case "productivity": String(localized: "Productivity", comment: "Connect apps: a section of services")
    case "business": String(localized: "Business", comment: "Connect apps: a section of services")
    case "support": String(localized: "Support", comment: "Connect apps: a section of services")
    case "mac": String(localized: "This Mac", comment: "Settings row: the paired Mac")
    default: id.capitalized
    }
}

/// One service: its logo, name and one line, and its pill.
private struct ConnectAppRow: View {
    let app: ConnectApp
    /// The owner started its step here, or Dot waits on the Mac: the line says to finish there.
    let finishing: Bool
    let open: () -> Void
    let connect: () -> Void
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize: DynamicTypeSize

    var body: some View {
        // At the accessibility sizes the pill stands under the words, so neither the name nor the line is cut.
        Group {
            if dynamicTypeSize.isAccessibilitySize {
                VStack(alignment: .leading, spacing: ConnectMetric.gap) {
                    opener
                    pill
                }
            } else {
                HStack(spacing: ConnectMetric.gap) {
                    opener
                    pill
                }
            }
        }
        .padding(.horizontal, ConnectMetric.inset)
        .padding(.vertical, ConnectMetric.rowPadding)
    }

    private var opener: some View {
        Button(action: open) {
            // At the accessibility sizes the tile stands by the name, as Apple's own lists set an icon on the first line.
            HStack(alignment: dynamicTypeSize.isAccessibilitySize ? .top : .center, spacing: ConnectMetric.gap) {
                LogoTile(logo: app.logo, glyph: app.glyph, name: app.name)
                VStack(alignment: .leading, spacing: ConnectMetric.lineGap) {
                    Text(app.name).textRole(.body).foregroundStyle(Tone.text).lineLimit(dynamicTypeSize.isAccessibilitySize ? nil : 1)
                    Text(line).textRole(.footnote).foregroundStyle(finishing && !app.isConnected ? Tone.warn : Tone.secondary)
                        .lineLimit(dynamicTypeSize.isAccessibilitySize ? nil : 2)
                }
                Spacer(minLength: 0)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(RowPressStyle())
        .accessibilityElement(children: .ignore)
        .accessibilityLabel([app.name, line].joined(separator: ", "))
        .accessibilityHint(String(localized: "Shows what it can do", comment: "VoiceOver: a Connect apps row opens the service's detail"))
    }

    private var line: String {
        finishing && !app.isConnected ? String(localized: "Finish on your Mac", comment: "Connect apps: the step of this service is answered on the Mac") : app.does
    }

    @ViewBuilder private var pill: some View {
        switch app.action {
        case .connected:
            ConnectPill(title: String(localized: "Connected", comment: "Connect apps: the pill of a connected service"), quiet: true, action: open)
        case .waiting:
            ConnectPill(title: String(localized: "Waiting", comment: "Connect apps: the pill of a service Dot is connecting or macOS asks about"), quiet: true, action: open)
        case .stepOnMac, .onMacOnly:
            ConnectPill(title: String(localized: "Connect", comment: "Connect apps: the pill that starts connecting a service"), quiet: false, action: connect)
        case .nothing:
            EmptyView()
        }
    }
}

/// The sheet's measures, on the design bar's 4 pt scale: one inset on both sides, one gap between a row's parts.
private enum ConnectMetric {
    static let inset: CGFloat = 16
    static let gap: CGFloat = 12
    static let rowPadding: CGFloat = 12
    static let lineGap: CGFloat = 4
    /// The pill's visible height inside its 44 pt target.
    static let pill: CGFloat = 32
}

/// The trailing pill of a row: Connect in ink, Connected and Waiting quiet.
private struct ConnectPill: View {
    let title: String
    let quiet: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(title).textRole(.footnote).fontWeight(.medium)
                .foregroundStyle(quiet ? Tone.tertiary : Tone.text)
                .padding(.horizontal, ConnectMetric.inset)
                .frame(minHeight: ConnectMetric.pill)
                .background(Tone.nested, in: Capsule())
                .frame(minHeight: Metric.hit)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .fixedSize()
    }
}

/// A service's mark on its tile: the web's logo in the theme's ink, else its glyph, else its initial.
struct LogoTile: View {
    let logo: String?
    let glyph: String?
    let name: String
    @ScaledMetric(relativeTo: .body) private var scaled: CGFloat = 44
    /// Grows with the text up to 64 pt, so the words keep the row's width at the accessibility sizes.
    private var size: CGFloat { min(scaled, 64) }

    var body: some View {
        mark
            .foregroundStyle(Tone.text)
            .frame(width: size, height: size)
            .background(Tone.card, in: rounded(Radius.inner))
            .overlay(rounded(Radius.inner).strokeBorder(Tone.separator, lineWidth: 0.5))
            .accessibilityHidden(true)
    }

    @ViewBuilder private var mark: some View {
        if let asset = logoAsset(logo) {
            Image(asset).renderingMode(.template).resizable().scaledToFit().frame(width: size * 0.52, height: size * 0.52)
        } else if let symbol = glyphSymbol(glyph) {
            Image(systemName: symbol).font(.system(size: size * 0.42, weight: .regular))
        } else {
            Text(String(name.prefix(1)).uppercased()).font(.system(size: size * 0.42, weight: .semibold))
        }
    }
}

/// The asset of a logo file of the web (`slack-icon.svg` → `plugin-slack-icon`), or `nil` when the app does not carry it
/// (`scripts/sync_plugin_logos.py`).
private func logoAsset(_ logo: String?) -> String? {
    guard let logo, logo.hasSuffix(".svg") else { return nil }
    let name: String = "plugin-" + logo.dropLast(4)
    return UIImage(named: name) == nil ? nil : name
}

/// The SF Symbol of an icon name of the web (`directory.py` glyphs, the skill's puzzle piece).
private func glyphSymbol(_ glyph: String?) -> String? {
    switch glyph {
    case "calendar": "calendar"
    case "clock": "clock"
    case "file": "doc"
    case "globe": "globe"
    case "list": "list.bullet"
    case "mail": "envelope"
    case "message": "message"
    case "pencil": "pencil"
    case "search": "magnifyingglass"
    case "users": "person.2"
    case "puzzlepiece.extension": "puzzlepiece.extension"
    default: nil
    }
}

/// A service's detail: what it can do and how each way connects, who it is connected as, Connect or Disconnect.
private struct ConnectAppDetail: View {
    let app: ConnectApp
    let finishing: Bool
    let problem: SettingsFailure?
    let connect: () async -> Void
    let disconnect: () async -> DisconnectOutcome
    @Environment(\.dismiss) private var dismiss: DismissAction
    @State private var asksDisconnect: Bool = false
    @State private var working: Bool = false
    @State private var disconnectProblem: SettingsFailure?
    @State private var disconnectNotices: [String] = []

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Space.groupedGutter) {
                HStack(spacing: ConnectMetric.gap) {
                    LogoTile(logo: app.logo, glyph: app.glyph, name: app.name)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(app.name).textRole(.title).foregroundStyle(Tone.text)
                        Text(sectionTitle(app.section)).textRole(.footnote).foregroundStyle(Tone.secondary)
                    }
                }
                Text(app.does).textRole(.body).foregroundStyle(Tone.text)
                stateBlock
                if !app.accounts.isEmpty {
                    GroupedSection(header: String(localized: "Connected as", comment: "Connect apps detail: the accounts and vaults of a connected service"), footer: nil) {
                        ForEach(app.accounts, id: \.self) { account in ListRow(systemImage: "person.crop.circle", title: account, description: nil, accessory: .none) }
                    }
                }
                GroupedSection(header: String(localized: "What it can do", comment: "Connect apps detail: the ways Dot reaches the service"), footer: nil) {
                    ForEach(app.ways, id: \.self) { way in
                        ListRow(systemImage: wayGlyph(way.kind), title: wayName(way.kind), description: way.does == app.does ? way.how : "\(way.does) \(way.how)", accessory: .none)
                    }
                }
                actions
            }
            .padding(Space.gutter)
            .readableColumn()
        }
        .modifier(SheetChrome(title: app.name, close: { dismiss() }))
        .confirmationDialog(
            String(localized: "Disconnect \(app.name)?", comment: "Connect apps detail: confirmation of Disconnect; the argument is the service"),
            isPresented: $asksDisconnect, titleVisibility: .visible
        ) {
            Button(String(localized: "Disconnect", comment: "Connect apps detail: takes a service's connection away"), role: .destructive) {
                Task {
                    working = true
                    let outcome: DisconnectOutcome = await disconnect()
                    disconnectProblem = outcome.failure
                    disconnectNotices = outcome.notices
                    working = false
                }
            }
        } message: {
            Text(String(localized: "Its actions leave the tasks that start after, and Dot forgets its sign-in.", comment: "Connect apps detail: what Disconnect does"))
        }
    }

    @ViewBuilder private var stateBlock: some View {
        if let problem {
            FailureBanner(failure: problem, macPlace: String(localized: "Settings › Connectors", comment: "Where on the Mac: the Connectors page"), retry: nil)
        }
        if let disconnectProblem {
            FailureBanner(failure: disconnectProblem, macPlace: String(localized: "Settings › Connectors", comment: "Where on the Mac: the Connectors page"), retry: nil)
        }
        ForEach(disconnectNotices, id: \.self) { notice in Notice(tone: .warn, message: notice, dismiss: nil) }
        switch app.action {
        case .connected:
            Notice(tone: .info, message: String(localized: "Connected: Dot can use it in its tasks.", comment: "Connect apps detail: the service is connected"), dismiss: nil)
        case .waiting:
            Notice(tone: .warn, message: String(localized: "Finish on your Mac: Dot waits for your answer there.", comment: "Connect apps detail: Dot is connecting or macOS asks on the Mac"), dismiss: nil)
        case .onMacOnly:
            Notice(
                tone: finishing ? .warn : .info,
                message: String(
                    localized: "Finish on your Mac: open Settings › Connectors › \(app.name). Your Mac takes a sign-in, a key or a token there, not from a phone.",
                    comment: "Connect apps detail: the service is connected on the Mac alone; the argument is the service"),
                dismiss: nil)
        case .stepOnMac:
            if finishing {
                Notice(
                    tone: .warn,
                    message: String(localized: "Finish on your Mac: answer macOS there, or sign in on the page that opened in Dot's browser.", comment: "Connect apps detail: the step started from the phone is answered on the Mac"),
                    dismiss: nil)
            }
        case .nothing:
            Notice(tone: .info, message: String(localized: "Nothing to connect from here: open Settings › Connectors on your Mac to see what it needs.", comment: "Connect apps detail: no way of the service has a step"), dismiss: nil)
        }
    }

    @ViewBuilder private var actions: some View {
        if case .stepOnMac = app.action {
            Button(finishing ? String(localized: "Start again on the Mac", comment: "Connect apps detail: takes the Mac's step once more") : String(localized: "Connect", comment: "Connect apps: the pill that starts connecting a service")) {
                Task {
                    working = true
                    await connect()
                    working = false
                }
            }
            .buttonStyle(PrimaryButtonStyle())
            .frame(maxWidth: .infinity)
            .disabled(working)
        }
        if case .onMacOnly = app.action {
            Button(String(localized: "Copy instructions", comment: "Settings: copies where to do a refused thing on the Mac")) {
                UIPasteboard.general.string = String(
                    localized: "Finish on your Mac: open Settings › Connectors › \(app.name). Your Mac takes a sign-in, a key or a token there, not from a phone.",
                    comment: "Connect apps detail: the service is connected on the Mac alone; the argument is the service")
            }
            .buttonStyle(SecondaryButtonStyle())
            .frame(maxWidth: .infinity)
        }
        if !app.removals.isEmpty {
            Button(String(localized: "Disconnect", comment: "Connect apps detail: takes a service's connection away"), role: .destructive) { asksDisconnect = true }
                .buttonStyle(DestructiveButtonStyle())
                .frame(maxWidth: .infinity)
                .disabled(working)
        }
    }
}

/// The name of a way's kind (`catalogue.js` WAY_NAMES).
private func wayName(_ kind: String) -> String {
    switch kind {
    case "mcp": String(localized: "Connected app", comment: "Connect apps detail: a way Dot reaches a service, through its MCP server")
    case "mac": String(localized: "Mac app", comment: "Connect apps detail: a way Dot reaches a service, by driving its app on the Mac")
    case "web": String(localized: "Dot's browser", comment: "Connect apps detail: a way Dot reaches a service, in its own browser")
    case "folder": String(localized: "Synced folder", comment: "Connect apps detail: a way Dot reaches a service, through a folder it keeps in sync")
    case "vault": String(localized: "Password manager", comment: "Connect apps detail: a way Dot reaches a service, through 1Password's command line")
    default: kind
    }
}

/// The SF Symbol of a way's kind.
private func wayGlyph(_ kind: String) -> String {
    switch kind {
    case "mcp": "powerplug"
    case "mac": "desktopcomputer"
    case "folder": "folder"
    case "web": "globe"
    case "vault": "lock"
    default: "link"
    }
}
