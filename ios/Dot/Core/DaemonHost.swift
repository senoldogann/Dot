import Foundation

/// The daemon's tailnet address: a lowercase `*.ts.net` host reached over HTTPS on the default port.
struct DaemonHost: Equatable, Sendable {
    /// The host name, e.g. `mac.tailnet.ts.net`.
    let name: String
    /// `https://<name>`, the one origin the web view loads.
    let origin: URL

    /// The URL of an absolute path on the daemon, e.g. `/api/phone/apns`.
    func url(path: String) -> URL {
        origin.appending(path: path)
    }
}

/// Why an address typed by the owner is refused. The description is shown to them as it is.
enum DaemonHostError: Error, Equatable, CustomStringConvertible {
    case empty
    case notHTTPS(scheme: String)
    case malformed
    case notTailnet(host: String)
    case hasPort
    case hasCredentials
    case hasPath
    case notBuildHost(expected: String)

    var description: String {
        switch self {
        case .empty:
            "Type the address of your Mac, like mac.your-tailnet.ts.net."
        case .notHTTPS(let scheme):
            "The address must use https, not \(scheme)."
        case .malformed:
            "This is not an address. Type it like mac.your-tailnet.ts.net."
        case .notTailnet(let host):
            "\(host) is not a tailnet address. It must end in .ts.net."
        case .hasPort:
            "Leave the port out: Dot is reached on the standard https port."
        case .hasCredentials:
            "Leave the user name and password out of the address."
        case .hasPath:
            "Type the address alone, without a path, query or fragment."
        case .notBuildHost(let expected):
            "This build of Dot only opens \(expected)."
        }
    }
}

/// Parses what the owner typed into the daemon's host.
///
/// Accepts `mac.tailnet.ts.net` or `https://mac.tailnet.ts.net/`; refuses any other scheme, a port, credentials, a
/// path, a query, a fragment and any host not under `.ts.net`. `buildHost` is the `DOT_HOST` the app was built for
/// (its `WKAppBoundDomains` entry), `nil` when the build names none; when set, only that host is accepted.
func parseDaemonHost(_ input: String, buildHost: String?) -> Result<DaemonHost, DaemonHostError> {
    let trimmed: String = input.trimmingCharacters(in: .whitespacesAndNewlines)
    if trimmed.isEmpty {
        return .failure(.empty)
    }
    let withScheme: String = trimmed.contains("://") ? trimmed : "https://\(trimmed)"
    guard let components: URLComponents = URLComponents(string: withScheme), let scheme: String = components.scheme
    else {
        return .failure(.malformed)
    }
    if scheme.lowercased() != "https" {
        return .failure(.notHTTPS(scheme: scheme))
    }
    if components.user != nil || components.password != nil {
        return .failure(.hasCredentials)
    }
    if components.port != nil {
        return .failure(.hasPort)
    }
    if !(components.path.isEmpty || components.path == "/") || components.query != nil || components.fragment != nil {
        return .failure(.hasPath)
    }
    guard let rawHost: String = components.host, !rawHost.isEmpty else {
        return .failure(.malformed)
    }
    let host: String = rawHost.lowercased()
    if !isTailnetHost(host) {
        return .failure(.notTailnet(host: host))
    }
    if let buildHost: String, host != buildHost.lowercased() {
        return .failure(.notBuildHost(expected: buildHost))
    }
    guard let origin: URL = URL(string: "https://\(host)") else {
        return .failure(.malformed)
    }
    return .success(DaemonHost(name: host, origin: origin))
}

/// Whether `host` is a DNS name under `.ts.net` with at least one label of its own, every label of letters, digits
/// and hyphens.
func isTailnetHost(_ host: String) -> Bool {
    let labels: [Substring] = host.split(separator: ".", omittingEmptySubsequences: false)
    guard labels.count >= 3, labels.suffix(2) == ["ts", "net"] else {
        return false
    }
    let allowed: CharacterSet = CharacterSet(charactersIn: "abcdefghijklmnopqrstuvwxyz0123456789-")
    return labels.allSatisfy { (label: Substring) -> Bool in
        !label.isEmpty && label.count <= 63 && !label.hasPrefix("-") && !label.hasSuffix("-")
            && label.unicodeScalars.allSatisfy { allowed.contains($0) }
    }
}
