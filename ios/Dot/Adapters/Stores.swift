import Foundation
import Security

/// Keeps the daemon's host in UserDefaults. The host is not a secret; the phone key is kept in the Keychain.
struct HostStore: Sendable {
    private static let defaultsKey: String = "daemonHost"

    /// The host saved before, or `nil` when none is (or what is saved no longer parses).
    func load(buildHost: String?) -> DaemonHost? {
        guard let saved: String = UserDefaults.standard.string(forKey: Self.defaultsKey) else {
            return nil
        }
        return try? parseDaemonHost(saved, buildHost: buildHost).get()
    }

    func save(_ host: DaemonHost) {
        UserDefaults.standard.set(host.name, forKey: Self.defaultsKey)
    }
}

/// The host this build was made for (`DOT_HOST`, written into `WKAppBoundDomains`), or `nil` when it names none.
func buildHostFromInfoPlist() -> String? {
    guard let domains: [String] = Bundle.main.object(forInfoDictionaryKey: "WKAppBoundDomains") as? [String] else {
        return nil
    }
    return domains.first.map { $0.lowercased() }
}

/// A Keychain call that failed, with its `OSStatus`.
struct KeychainError: Error, CustomStringConvertible {
    let operation: String
    let status: OSStatus

    var description: String {
        let message: String = (SecCopyErrorMessageString(status, nil) as String?) ?? "unknown"
        return "Keychain \(operation) failed: \(status) (\(message))"
    }
}

/// Keeps the phone's key in the Keychain: a generic password, service `dev.dotagent.phone`, account `phone-key`.
///
/// No access group is named in the queries: the Keychain then uses the first entry of the app's
/// `keychain-access-groups` entitlement, `<team>.dev.dotagent.phone`, which the widget extension shares. The item is
/// readable after the first unlock (a push may wake the app while the phone is locked) and never leaves this device.
struct PhoneKeyStore: Sendable {
    private static let service: String = "dev.dotagent.phone"
    private static let account: String = "phone-key"

    private func baseQuery() -> [CFString: Any] {
        [
            kSecClass: kSecClassGenericPassword,
            kSecAttrService: Self.service,
            kSecAttrAccount: Self.account,
            kSecUseDataProtectionKeychain: true,
        ]
    }

    /// The saved key, or `nil` when there is none.
    func read() throws(KeychainError) -> String? {
        var query: [CFString: Any] = baseQuery()
        query[kSecReturnData] = true
        query[kSecMatchLimit] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status: OSStatus = SecItemCopyMatching(query as CFDictionary, &result)
        if status == errSecItemNotFound {
            return nil
        }
        guard status == errSecSuccess else {
            throw KeychainError(operation: "read", status: status)
        }
        guard let data: Data = result as? Data, let key: String = String(data: data, encoding: .utf8) else {
            throw KeychainError(operation: "decode", status: errSecDecode)
        }
        return key
    }

    /// Saves `key`, replacing any key saved before.
    func save(_ key: String) throws(KeychainError) {
        let data: Data = Data(key.utf8)
        let update: [CFString: Any] = [
            kSecValueData: data,
            kSecAttrAccessible: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
        ]
        let updateStatus: OSStatus = SecItemUpdate(baseQuery() as CFDictionary, update as CFDictionary)
        if updateStatus == errSecSuccess {
            return
        }
        guard updateStatus == errSecItemNotFound else {
            throw KeychainError(operation: "update", status: updateStatus)
        }
        var add: [CFString: Any] = baseQuery()
        add.merge(update) { (_: Any, new: Any) -> Any in new }
        let addStatus: OSStatus = SecItemAdd(add as CFDictionary, nil)
        guard addStatus == errSecSuccess else {
            throw KeychainError(operation: "add", status: addStatus)
        }
    }

    /// Removes the key; nothing happens when none is saved.
    func delete() throws(KeychainError) {
        let status: OSStatus = SecItemDelete(baseQuery() as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw KeychainError(operation: "delete", status: status)
        }
    }
}
