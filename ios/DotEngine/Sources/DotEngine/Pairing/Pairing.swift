import Foundation
import Security

/// DotNext's own app group: the engine's files live under its `v2/` folder (plan §11, §12).
public let nextAppGroup: String = "group.dev.dotagent.phone.next"

/// Keeps the daemon's address in DotNext's app group defaults. The address is not a secret; the key is the Keychain's.
public struct HostStore: Sendable {
    private static let key: String = "daemonHost"

    public init() {}

    private var defaults: UserDefaults? { UserDefaults(suiteName: nextAppGroup) }

    /// The address saved, or `nil`; a loopback address only when `loopbackAllowed` (debug builds).
    public func load(loopbackAllowed: Bool) -> DaemonHost? {
        guard let saved: String = defaults?.string(forKey: Self.key) else { return nil }
        if loopbackAllowed, let local: DaemonHost = parseLoopbackDaemonHost(saved) { return local }
        return try? parseDaemonHost(saved, buildHost: nil).get()
    }

    public func save(_ host: DaemonHost) { defaults?.set(host.origin.absoluteString, forKey: Self.key) }

    public func forget() { defaults?.removeObject(forKey: Self.key) }
}

/// A Keychain call that failed, with its `OSStatus`.
public struct KeychainError: Error, CustomStringConvertible, Equatable {
    public let operation: String
    public let status: OSStatus

    public var description: String {
        let message: String = (SecCopyErrorMessageString(status, nil) as String?) ?? "unknown"
        return "Keychain \(operation) failed: \(status) (\(message))"
    }
}

/// Keeps the phone's key in the Keychain: a generic password, service `dev.dotagent.phone.next`, account `phone-key`.
/// Moved from the old app's `PhoneKeyStore`; no access group is named, so the Keychain uses the first entry of DotNext's
/// `keychain-access-groups` entitlement (`<team>.dev.dotagent.phone.next`), never the old app's. Readable after the first
/// unlock, never leaves this device.
public struct PhoneKeyStore: Sendable {
    private static let service: String = "dev.dotagent.phone.next"
    private static let account: String = "phone-key"

    public init() {}

    private func baseQuery() -> [CFString: Any] {
        [
            kSecClass: kSecClassGenericPassword,
            kSecAttrService: Self.service,
            kSecAttrAccount: Self.account,
            kSecUseDataProtectionKeychain: true,
        ]
    }

    public func read() throws(KeychainError) -> String? {
        var query: [CFString: Any] = baseQuery()
        query[kSecReturnData] = true
        query[kSecMatchLimit] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status: OSStatus = SecItemCopyMatching(query as CFDictionary, &result)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess else { throw KeychainError(operation: "read", status: status) }
        guard let data: Data = result as? Data, let key: String = String(data: data, encoding: .utf8) else {
            throw KeychainError(operation: "decode", status: errSecDecode)
        }
        return key
    }

    public func save(_ key: String) throws(KeychainError) {
        let update: [CFString: Any] = [
            kSecValueData: Data(key.utf8),
            kSecAttrAccessible: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
        ]
        let updateStatus: OSStatus = SecItemUpdate(baseQuery() as CFDictionary, update as CFDictionary)
        if updateStatus == errSecSuccess { return }
        guard updateStatus == errSecItemNotFound else { throw KeychainError(operation: "update", status: updateStatus) }
        var add: [CFString: Any] = baseQuery()
        add.merge(update) { (_: Any, new: Any) -> Any in new }
        let addStatus: OSStatus = SecItemAdd(add as CFDictionary, nil)
        guard addStatus == errSecSuccess else { throw KeychainError(operation: "add", status: addStatus) }
    }

    public func delete() throws(KeychainError) {
        let status: OSStatus = SecItemDelete(baseQuery() as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else { throw KeychainError(operation: "delete", status: status) }
    }
}

/// Why pairing did not work, for the app to say in the owner's language.
public enum PairingFailure: Error, Equatable, Sendable {
    /// The code is not 8 letters and digits.
    case malformedCode
    /// The daemon refused the code (wrong, used or out of date).
    case codeRefused
    /// The daemon is from before the sync calls: "Update Dot on your Mac".
    case daemonTooOld
    /// The Mac could not be reached.
    case unreachable(detail: String)
    case keychain(detail: String)
}

/// The code as the daemon takes it: letters and digits only, upper case, 8 of them; `nil` otherwise. Pure.
public func normalizedPairingCode(_ typed: String) -> String? {
    let kept: String = typed.uppercased().filter { $0.isASCII && ($0.isLetter || $0.isNumber) }
    return kept.count == 8 ? kept : nil
}

/// Pairs with the typed 8-character code (`POST /api/phone/pair`, the existing route), checks that the daemon knows the
/// sync calls (an older one answers `/api/threads/view` 404), then keeps the key and the address.
public func pair(host: DaemonHost, typedCode: String, session: URLSession, keys: PhoneKeyStore, hosts: HostStore) async throws(PairingFailure) -> String {
    guard let code: String = normalizedPairingCode(typedCode) else { throw .malformedCode }
    let key: String
    do {
        key = try await DaemonAPI.pair(code: code, host: host, session: session)
    } catch {
        if let status: Int = error.statusCode, (400..<500).contains(status), status != 404 { throw .codeRefused }
        throw .unreachable(detail: error.description)
    }
    let api: DaemonAPI = DaemonAPI(host: host, key: key, session: session, streamSession: session)
    do {
        _ = try await api.threadsView()
    } catch {
        if error.statusCode == 404 { throw .daemonTooOld }
        throw .unreachable(detail: error.description)
    }
    do {
        try keys.save(key)
    } catch {
        throw .keychain(detail: error.description)
    }
    hosts.save(host)
    return key
}
