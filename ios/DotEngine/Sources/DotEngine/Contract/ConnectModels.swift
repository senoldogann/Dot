import Foundation

// The routes of the web's Connectors page (items 316 and 542) as the phone's Connect apps sheet reads them (item 1861).
// Kinds, states and steps stay strings: a daemon that adds one breaks nothing, and the catalogue
// (`connectApps`) treats a word it does not know as nothing to take.

/// A way Dot reaches a service (`connections/directory.py` `PluginWayView`): `kind` is `mcp`, `mac`, `folder`, `web`
/// or `vault`, `ref` its entry in that catalogue; `how`, `data` and `does` are the catalogue's words.
public struct PluginWayView: Codable, Sendable, Hashable {
    public let kind: String
    public let ref: String
    public let how: String
    public let data: String
    public let does: String

    public init(kind: String, ref: String, how: String, data: String, does: String) {
        self.kind = kind
        self.ref = ref
        self.how = how
        self.data = data
        self.does = does
    }
}

/// A listing of `GET /api/plugins`: one service, the section it stands in, its mark (`logo`, a file of
/// `static/icons/plugins`, else `glyph`, an icon name of the web) and its ways.
public struct PluginView: Codable, Sendable, Hashable, Identifiable {
    public let id: String
    public let name: String
    public let section: String
    public let does: String
    public let logo: String?
    public let glyph: String?
    public let ways: [PluginWayView]

    public init(id: String, name: String, section: String, does: String, logo: String?, glyph: String?, ways: [PluginWayView]) {
        self.id = id
        self.name = name
        self.section = section
        self.does = does
        self.logo = logo
        self.glyph = glyph
        self.ways = ways
    }
}

/// `GET /api/plugins`.
public struct PluginsView: Codable, Sendable, Hashable {
    public let plugins: [PluginView]

    public init(plugins: [PluginView]) { self.plugins = plugins }
}

/// An application of `GET /api/connections` (item 278): `state` where it stands, `step` what sets it up (`ask`,
/// `settings`, `sign_in`, `open_app`; `nil` when there is none to take), `waiting` while macOS asks on the Mac.
public struct ConnectionView: Codable, Sendable, Hashable {
    public let id: String
    public let kind: String
    public let name: String
    public let does: String
    public let state: String
    public let step: String?
    public let waiting: Bool

    public init(id: String, kind: String, name: String, does: String, state: String, step: String?, waiting: Bool) {
        self.id = id
        self.kind = kind
        self.name = name
        self.does = does
        self.state = state
        self.step = step
        self.waiting = waiting
    }
}

/// `GET /api/connections`.
public struct ConnectionsView: Codable, Sendable, Hashable {
    public let apps: [ConnectionView]
    public let browserMode: String
    public let headless: Bool

    public init(apps: [ConnectionView], browserMode: String, headless: Bool) {
        self.apps = apps
        self.browserMode = browserMode
        self.headless = headless
    }

    enum CodingKeys: String, CodingKey {
        case apps, headless
        case browserMode = "browser_mode"
    }
}

/// An MCP server Dot has (`GET /api/mcp/servers`): `origin` where it is set up (`connected` on the Connectors page,
/// `elsewhere` from another agent, `config`, `plugin`, `builtin`), `service` the catalogue's service it serves, `state`
/// `ready`, `left_out`, `disabled`, `trying` or `signing_in`.
public struct McpServerView: Codable, Sendable, Hashable {
    public let name: String
    public let origin: String
    public let service: String?
    public let state: String
    public let error: String?

    public init(name: String, origin: String, service: String?, state: String, error: String?) {
        self.name = name
        self.origin = origin
        self.service = service
        self.state = state
        self.error = error
    }
}

/// A service of the MCP catalogue, the part the sheet reads: whether a server of the config or of a plugin holds its
/// name already (`added`).
public struct McpServiceView: Codable, Sendable, Hashable {
    public let id: String
    public let added: Bool

    public init(id: String, added: Bool) {
        self.id = id
        self.added = added
    }
}

/// `GET /api/mcp/servers` (and the answer of `DELETE /api/mcp/servers/{name}`).
public struct McpServersView: Codable, Sendable, Hashable {
    public let servers: [McpServerView]
    public let services: [McpServiceView]

    public init(servers: [McpServerView], services: [McpServiceView]) {
        self.servers = servers
        self.services = services
    }
}

/// A vault 1Password's service account reads.
public struct VaultSummary: Codable, Sendable, Hashable {
    public let name: String
    public let logins: Int

    public init(name: String, logins: Int) {
        self.name = name
        self.logins = logins
    }
}

/// `GET /api/vault` (item 493): whether Dot holds a 1Password service account token and what it reads.
public struct VaultStateView: Codable, Sendable, Hashable {
    public let cli: String?
    public let connected: Bool
    public let vaults: [VaultSummary]
    public let error: String?

    public init(cli: String?, connected: Bool, vaults: [VaultSummary], error: String?) {
        self.cli = cli
        self.connected = connected
        self.vaults = vaults
        self.error = error
    }
}

/// An account signed in with a provider's official sign-in (item 422).
public struct SignedInAccount: Codable, Sendable, Hashable {
    public let address: String

    public init(address: String) { self.address = address }
}

/// A provider of `GET /api/accounts` (Google, Microsoft) with its accounts.
public struct AccountProviderView: Codable, Sendable, Hashable {
    public let id: String
    public let name: String
    public let accounts: [SignedInAccount]

    public init(id: String, name: String, accounts: [SignedInAccount]) {
        self.id = id
        self.name = name
        self.accounts = accounts
    }
}

/// `DELETE /api/accounts/{provider}/{address}` (item 1312): whether the provider ended the grant, and where to end it
/// when it did not.
public struct SignedOutView: Codable, Sendable, Hashable {
    public let revoked: Bool
    public let notice: String?
    public let providers: [AccountProviderView]

    public init(revoked: Bool, notice: String?, providers: [AccountProviderView]) {
        self.revoked = revoked
        self.notice = notice
        self.providers = providers
    }
}

/// Everything the Connect apps sheet reads, in one value.
public struct ConnectSources: Sendable, Hashable {
    public let plugins: [PluginView]
    public let connections: ConnectionsView
    public let servers: McpServersView
    public let vault: VaultStateView
    public let providers: [AccountProviderView]

    public init(plugins: [PluginView], connections: ConnectionsView, servers: McpServersView, vault: VaultStateView, providers: [AccountProviderView]) {
        self.plugins = plugins
        self.connections = connections
        self.servers = servers
        self.vault = vault
        self.providers = providers
    }
}
