import Foundation

// The Connectors routes the Connect apps sheet reads and writes (item 1861). Each call is made once; the sheet shows a
// failure with its Retry. A connected app, an official sign-in and 1Password's token are added on the Mac alone (the
// daemon refuses a phone's key there), so no call here sends a key or a token.

private struct McpPromptRequest: Encodable {
    let server: String
    let name: String
    let arguments: [String: String]
}

private struct McpPromptDraft: Decodable {
    let text: String
}

extension DaemonAPI {
    public func expandPrompt(_ starter: PalettePrompt, arguments: [String: String]) async throws(DaemonRequestError) -> String {
        let request: McpPromptRequest = McpPromptRequest(server: starter.server, name: starter.prompt.name, arguments: arguments)
        let draft: McpPromptDraft = try await send("POST", "/api/mcp/prompts/expand", body: Self.json(request))
        return draft.text
    }

    /// Everything the sheet reads, asked together.
    public func connectSources() async throws(DaemonRequestError) -> ConnectSources {
        async let plugins: Result<PluginsView, DaemonRequestError> = result { () throws(DaemonRequestError) in try await get("/api/plugins", query: []) }
        async let connections: Result<ConnectionsView, DaemonRequestError> = result { () throws(DaemonRequestError) in try await get("/api/connections", query: []) }
        async let servers: Result<McpServersView, DaemonRequestError> = result { () throws(DaemonRequestError) in try await get("/api/mcp/servers", query: []) }
        async let vault: Result<VaultStateView, DaemonRequestError> = result { () throws(DaemonRequestError) in try await get("/api/vault", query: []) }
        async let providers: Result<[AccountProviderView], DaemonRequestError> = result { () throws(DaemonRequestError) in try await get("/api/accounts", query: []) }
        return ConnectSources(
            plugins: try await plugins.get().plugins, connections: try await connections.get(), servers: try await servers.get(), vault: try await vault.get(),
            providers: try await providers.get())
    }

    /// `POST /api/connections/{app}`: the step of a Mac application, a synced folder or an application of the web,
    /// taken on the Mac (macOS asks there, or the sign-in page opens in Dot's browser there).
    public func takeConnectionStep(app: String) async throws(DaemonRequestError) -> ConnectionView {
        try await send("POST", "/api/connections/\(app)", body: nil)
    }

    /// `DELETE /api/mcp/servers/{name}`: its tools leave the runs that start after, and Dot forgets its sign-in.
    public func removeMcpServer(_ name: String) async throws(DaemonRequestError) -> McpServersView {
        try await send("DELETE", "/api/mcp/servers/\(name)", body: nil)
    }

    /// `DELETE /api/vault`: Dot forgets 1Password's service account token.
    public func disconnectVault() async throws(DaemonRequestError) -> VaultStateView { try await send("DELETE", "/api/vault", body: nil) }

    /// `DELETE /api/accounts/{provider}/{address}`: the account is signed out.
    public func signOut(provider: String, address: String) async throws(DaemonRequestError) -> SignedOutView {
        try await send("DELETE", "/api/accounts/\(provider)/\(address)", body: nil)
    }
}

/// The outcome of `work` as a value, so that several calls can be asked at once and their first failure thrown.
private func result<Value: Sendable>(_ work: () async throws(DaemonRequestError) -> Value) async -> Result<Value, DaemonRequestError> {
    do throws(DaemonRequestError) {
        return .success(try await work())
    } catch {
        return .failure(error)
    }
}
