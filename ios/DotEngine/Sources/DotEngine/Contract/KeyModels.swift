import Foundation

// The routes of Settings › AI services on the phone (item 2054, `routes_keys.py`): each provider's key as the
// daemon describes it, and the answer of its check. A key goes to the daemon and never comes back: the view holds where
// the key in use comes from and its last four characters, as the web shows them. These types decode only; the app
// keeps none of them past the page that read them.

/// Where the key in use comes from: the environment Dot was started with, Dot's own `.env` file, or the Keychain.
public enum KeySource: String, Decodable, Sendable, Hashable {
    case environment
    case envFile = "env_file"
    case keychain
}

/// The last check of a key since the daemon started: whether the provider took it, its words and when.
public struct KeyCheck: Decodable, Sendable, Hashable {
    public let ok: Bool
    public let detail: String
    public let at: Double
}

/// What the phone says of a key at a glance.
public enum KeyState: Sendable, Hashable {
    /// No key is set anywhere Dot reads one.
    case missing
    /// A key is set and its last check did not fail (or none ran since it changed).
    case set
    /// A key is set and the provider refused it at its last check.
    case failing
}

/// `GET /api/keys`: one provider's key. `check` is absent from a daemon older than item 2054.
public struct KeyView: Decodable, Sendable, Hashable, Identifiable {
    public let provider: String
    public let name: String
    public let source: KeySource?
    public let saved: Bool
    public let tail: String
    public let check: KeyCheck?

    public var id: String { name }

    public var state: KeyState {
        guard source != nil else { return .missing }
        return check?.ok == false ? .failing : .set
    }

    /// Whether Dot's `.env` holds a key the phone may remove; a key from the environment or the Keychain is the Mac's.
    public var removable: Bool { saved }

    /// Whether a key Dot was started with comes before the one saved in Dot, so that a key saved here is not used.
    public var shadowed: Bool { source == .environment }

    /// The same key with the answer of a check just made as its last check.
    public func checked(_ result: KeyCheckResult, at: Double) -> KeyView {
        KeyView(provider: provider, name: name, source: source, saved: saved, tail: tail, check: KeyCheck(ok: result.ok, detail: result.detail, at: at))
    }

    public init(provider: String, name: String, source: KeySource?, saved: Bool, tail: String, check: KeyCheck?) {
        self.provider = provider
        self.name = name
        self.source = source
        self.saved = saved
        self.tail = tail
        self.check = check
    }
}

/// `GET /api/keys`: every provider's key, in the daemon's order.
public struct KeysView: Decodable, Sendable, Hashable {
    public let keys: [KeyView]
}

/// `POST /api/keys/<name>/check`: whether the provider takes the key in use, in its words.
public struct KeyCheckResult: Decodable, Sendable, Hashable {
    public let ok: Bool
    public let detail: String
}

/// Whether `text` is a key the daemon takes (`KeyRequest`): printable characters without spaces, 1 to 1000 of them,
/// once the ends are trimmed. The field checks it before anything is sent.
public func isSendableKey(_ text: String) -> Bool {
    let trimmed: String = trimmedKey(text)
    return !trimmed.isEmpty && trimmed.count <= 1000 && trimmed.unicodeScalars.allSatisfy { $0.value >= 0x21 && $0.value <= 0x7E }
}

/// A pasted key without the spaces and line breaks around it.
public func trimmedKey(_ text: String) -> String { text.trimmingCharacters(in: .whitespacesAndNewlines) }
