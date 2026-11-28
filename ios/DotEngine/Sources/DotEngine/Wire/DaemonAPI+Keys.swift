import Foundation

// The key routes Settings › AI services reads and writes (item 2054), as the web's `views/keys.js` calls them.
// A key is sent once, at the owner's tap, never queued in the outbox nor retried: a failure is shown and the owner
// taps again. The answer never holds the key, only its last four characters.

private struct KeyBody: Encodable { let value: String }

extension DaemonAPI {
    public func keys() async throws(DaemonRequestError) -> KeysView { try await get("/api/keys", query: []) }

    /// Save `value` as the key `name` in Dot's `.env`; the answer is the key's view, its last four characters at most.
    public func saveKey(_ name: String, value: String) async throws(DaemonRequestError) -> KeyView {
        try await send("POST", "/api/keys/\(name)", body: Self.json(KeyBody(value: value)))
    }

    /// Remove the key `name` from Dot's `.env`; 404 when Dot's file holds none (a key from the environment or the Keychain).
    public func removeKey(_ name: String) async throws(DaemonRequestError) -> KeyView {
        try await send("DELETE", "/api/keys/\(name)", body: nil)
    }

    /// Ask the provider whether it takes the key in use; a phone may ask once in 10 seconds for each key (429).
    public func checkKey(_ name: String) async throws(DaemonRequestError) -> KeyCheckResult {
        try await send("POST", "/api/keys/\(name)/check", body: nil)
    }
}
