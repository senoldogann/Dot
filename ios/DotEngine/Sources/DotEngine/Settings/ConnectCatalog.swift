import Foundation

// The Connect apps sheet's catalogue (item 1861, PLAN §27): the daemon's listings with where each stands and what the
// phone can do about it, read the way the web's `catalogue.js` reads them. What a phone may start is the daemon's to
// say: adding a connected app (item 678), an official sign-in and 1Password's token (`on_this_mac`) are refused to a
// phone's key, so those finish on the Mac; a Mac application's step (`POST /api/connections/{id}`) is taken from the
// phone and answered on the Mac, where macOS asks or Dot's browser opens.

/// What the phone can do for a service that is not connected.
public enum ConnectAction: Sendable, Hashable {
    /// One of its ways is connected.
    case connected
    /// Its step runs on the Mac and the phone may start it: `POST /api/connections/{app}`.
    case stepOnMac(app: String)
    /// The daemon takes this from the Mac alone: the owner finishes it there.
    case onMacOnly
    /// Dot tries it now, or macOS asks on the Mac.
    case waiting
    /// None of its ways has a step to take (the application is not installed, a folder does not sync).
    case nothing
}

/// What Disconnect takes away, one each.
public enum ConnectRemoval: Sendable, Hashable {
    /// `DELETE /api/mcp/servers/{name}`: a server added on the Connectors page or from another agent of the Mac.
    case server(String)
    /// `DELETE /api/vault`: 1Password's service account token.
    case vault
    /// `DELETE /api/accounts/{provider}/{address}`: an official sign-in.
    case account(provider: String, address: String)
}

/// A service as the sheet shows it.
public struct ConnectApp: Sendable, Hashable, Identifiable {
    public let id: String
    public let name: String
    public let section: String
    public let does: String
    public let logo: String?
    public let glyph: String?
    public let ways: [PluginWayView]
    public let action: ConnectAction
    /// Who it is connected as: the accounts signed in, the vaults 1Password reads.
    public let accounts: [String]
    public let removals: [ConnectRemoval]

    public init(
        id: String, name: String, section: String, does: String, logo: String?, glyph: String?, ways: [PluginWayView], action: ConnectAction,
        accounts: [String], removals: [ConnectRemoval]
    ) {
        self.id = id
        self.name = name
        self.section = section
        self.does = does
        self.logo = logo
        self.glyph = glyph
        self.ways = ways
        self.action = action
        self.accounts = accounts
        self.removals = removals
    }

    public var isConnected: Bool { action == .connected }
}

/// The section the web opens on and the sheet shows as Featured (item 1000).
public let featuredSection: String = "popular"
/// How many services Featured shows at most (PLAN §27).
public let featuredLimit: Int = 8

/// The states of an application that mean Dot reaches it (`catalogue.js` `CONNECTED_STATES`).
private let connectedStates: Set<String> = ["allowed", "signed_in", "synced", "lent"]

/// Every listing of `sources` with where it stands, in the daemon's order.
public func connectApps(_ sources: ConnectSources) -> [ConnectApp] {
    sources.plugins.map { plugin in connectApp(plugin, sources) }
}

private func connectApp(_ plugin: PluginView, _ sources: ConnectSources) -> ConnectApp {
    let signedIn: [(AccountProviderView, SignedInAccount)] = sources.providers.filter { owns(plugin.id, provider: $0.id) }
        .flatMap { provider in provider.accounts.map { (provider, $0) } }
    let vaultWay: Bool = plugin.ways.contains { $0.kind == "vault" }
    let vaultNames: [String] = vaultWay && vaultReady(sources.vault) ? sources.vault.vaults.map(\.name) : []
    let connected: Bool = !signedIn.isEmpty || plugin.ways.contains { wayConnected($0, sources) }
    return ConnectApp(
        id: plugin.id, name: plugin.name, section: plugin.section, does: plugin.does, logo: plugin.logo, glyph: plugin.glyph, ways: plugin.ways,
        action: connected ? .connected : firstAction(plugin.ways, sources),
        accounts: signedIn.map(\.1.address) + vaultNames,
        removals: plugin.ways.flatMap { removals($0, sources) } + signedIn.map { ConnectRemoval.account(provider: $0.0.id, address: $0.1.address) })
}

/// Whether the provider `provider` of the official sign-ins (`google`, `microsoft`) is the listing `listing`'s
/// (`google`, `microsoft-365`).
private func owns(_ listing: String, provider: String) -> Bool {
    listing == provider || listing.hasPrefix(provider + "-")
}

private func vaultReady(_ vault: VaultStateView) -> Bool {
    vault.connected && vault.error == nil && !vault.vaults.isEmpty
}

private func server(_ way: PluginWayView, _ sources: ConnectSources) -> McpServerView? {
    sources.servers.servers.first { $0.service == way.ref }
}

private func application(_ way: PluginWayView, _ sources: ConnectSources) -> ConnectionView? {
    sources.connections.apps.first { $0.id == way.ref }
}

private func wayConnected(_ way: PluginWayView, _ sources: ConnectSources) -> Bool {
    switch way.kind {
    case "mcp": server(way, sources)?.state == "ready"
    case "vault": vaultReady(sources.vault)
    default: application(way, sources).map { connectedStates.contains($0.state) } ?? false
    }
}

/// The first thing its ways offer, in their order (`catalogue.js` `firstStep`): a way that is under way is said as
/// waiting; a way the daemon takes from the Mac alone, as finishing there.
private func firstAction(_ ways: [PluginWayView], _ sources: ConnectSources) -> ConnectAction {
    for way in ways {
        switch way.kind {
        case "vault":
            if !sources.vault.connected { return .onMacOnly }
        case "mcp":
            guard let found = server(way, sources) else {
                if sources.servers.services.first(where: { $0.id == way.ref })?.added == true { continue }
                return .onMacOnly
            }
            if found.state == "trying" || found.state == "signing_in" { return .waiting }
            if found.state == "left_out" && found.origin == "connected" { return .onMacOnly }
        case "mac", "folder", "web":
            guard let app = application(way, sources) else { continue }
            if app.waiting { return .waiting }
            if app.step != nil { return .stepOnMac(app: app.id) }
        default:
            continue
        }
    }
    return .nothing
}

private func removals(_ way: PluginWayView, _ sources: ConnectSources) -> [ConnectRemoval] {
    switch way.kind {
    case "mcp":
        guard let found = server(way, sources), found.origin == "connected" || found.origin == "elsewhere" else { return [] }
        return [.server(found.name)]
    case "vault":
        return sources.vault.connected ? [.vault] : []
    default:
        return []
    }
}

/// The sections of `apps`, in the order they first stand (the daemon's), for the chips.
public func connectSections(_ apps: [ConnectApp]) -> [String] {
    var seen: [String] = []
    for app in apps where !seen.contains(app.section) { seen.append(app.section) }
    return seen
}

/// The services of `apps` the sheet lists: of `section` alone when one is chosen, connected ones alone when asked, and
/// those whose name or words begin with every word of `query`.
public func filterConnectApps(_ apps: [ConnectApp], section: String?, onlyConnected: Bool, query: String) -> [ConnectApp] {
    let wanted: [String] = searchWords(query)
    return apps.filter { app in
        if let section, app.section != section { return false }
        if onlyConnected && !app.isConnected { return false }
        return covers(wanted, searchWords(app.name) + searchWords(app.does))
    }
}

/// The skills whose name or description begin with every word of `query`; none for an empty query (the sheet lists
/// skills only while searching).
public func filterSkills(_ skills: [PaletteSkill], query: String) -> [PaletteSkill] {
    let wanted: [String] = searchWords(query)
    guard !wanted.isEmpty else { return [] }
    return skills.filter { covers(wanted, searchWords($0.name) + searchWords($0.description)) }
}

private func covers(_ wanted: [String], _ words: [String]) -> Bool {
    wanted.allSatisfy { want in words.contains { $0.hasPrefix(want) } }
}
