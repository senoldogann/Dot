import Foundation

/// What the web view does with a navigation.
enum NavigationDecision: Equatable, Sendable {
    /// The daemon's own origin (or a blob/about document of it): load it in the web view.
    case allow
    /// Another origin the owner followed: open it in Safari, never in the web view.
    case openExternally
    /// Anything else (another origin in a frame, plain http in a frame, unknown schemes): drop it.
    case block
}

/// Schemes a link may hand to the system (Safari, Mail, Phone, Messages).
private let externalSchemes: Set<String> = ["https", "http", "mailto", "tel", "sms", "facetime"]

/// Whether `url` is the daemon's origin: https, the host, the default port.
func isDaemonOrigin(_ url: URL, host: DaemonHost) -> Bool {
    url.scheme?.lowercased() == "https" && url.host()?.lowercased() == host.name && (url.port == nil || url.port == 443)
}

/// Decides a navigation of the web view.
///
/// `opensFrame` is true for the main frame and for a new window (`target=_blank`, `window.open`); only those may send
/// a URL to the system, so a frame of a page cannot open Safari on its own.
func decideNavigation(url: URL, host: DaemonHost, opensFrame: Bool) -> NavigationDecision {
    if isDaemonOrigin(url, host: host) {
        return .allow
    }
    let scheme: String = url.scheme?.lowercased() ?? ""
    switch scheme {
    case "about":
        return .allow
    case "blob":
        // blob:https://host/uuid: the inner URL names the origin that made it.
        let inner: String = String(url.absoluteString.dropFirst("blob:".count))
        if let innerURL: URL = URL(string: inner), isDaemonOrigin(innerURL, host: host) {
            return .allow
        }
        return .block
    default:
        if opensFrame && externalSchemes.contains(scheme) {
            return .openExternally
        }
        return .block
    }
}

/// A message of the page refused by the bridge. Logged, never shown to the page.
enum BridgeRejection: Error, Equatable {
    case notMainFrame
    case runPage
    case foreignOrigin(protocol: String, host: String, port: Int)
    case notAnObject
    case unknownType(String)
    case badKey
}

/// What the page's `dot` message handler accepts.
enum BridgeMessage: Equatable, Sendable {
    /// The page was paired with the daemon and hands over the phone's key.
    case paired(key: String)
    /// The page asks for the app's page of the iPhone's Settings, where its notifications are switched (item 1005).
    case openSettings
    /// The owner started a run on the phone: the app opens the lock screen's card, which brings no notification from
    /// the app in front (item 1096).
    case working
}

/// The origin of the frame that sent a message, as WebKit reports it.
struct MessageOrigin: Equatable, Sendable {
    let isMainFrame: Bool
    let scheme: String
    let host: String
    /// 0 when the origin uses its scheme's default port.
    let port: Int
    /// The path of the frame's document, e.g. `/` for the web UI or `/pages/<id>` for a page a run wrote.
    let path: String
}

/// Whether `path` is a page a run wrote or a look the owner added (the daemon's `/pages/` and `/looks/`): served from
/// the daemon's origin, but not the web UI, so it never reaches the bridge or the microphone.
func isRunPage(_ path: String) -> Bool {
    path.hasPrefix("/pages/") || path.hasPrefix("/looks/")
}

/// Validates a message posted to `window.webkit.messageHandlers.dot` and reads it.
///
/// Only the main frame of `https://<host>` on the default port is heard. `body` is what WebKit decoded from the
/// page's JSON value (`WKScriptMessage.body` is untyped, so it is narrowed here at once); anything but
/// `{type: "paired", key: "phone.…"}`, `{type: "openSettings"}` and `{type: "working"}` is refused.
func readBridgeMessage(body: Any, origin: MessageOrigin, host: DaemonHost) -> Result<BridgeMessage, BridgeRejection> {
    guard origin.isMainFrame else {
        return .failure(.notMainFrame)
    }
    guard !isRunPage(origin.path) else {
        return .failure(.runPage)
    }
    guard origin.scheme == "https", origin.host.lowercased() == host.name, origin.port == 0 || origin.port == 443
    else {
        return .failure(.foreignOrigin(protocol: origin.scheme, host: origin.host, port: origin.port))
    }
    guard let object: NSDictionary = body as? NSDictionary, let type: String = object["type"] as? String else {
        return .failure(.notAnObject)
    }
    if type == "openSettings" {
        return .success(.openSettings)
    }
    if type == "working" {
        return .success(.working)
    }
    guard type == "paired" else {
        return .failure(.unknownType(type))
    }
    guard let key: String = object["key"] as? String, isPhoneKey(key) else {
        return .failure(.badKey)
    }
    return .success(.paired(key: key))
}

/// Whether `key` has the shape of the daemon's phone keys: `phone.<id>.<secret>`, URL-safe characters, bounded.
func isPhoneKey(_ key: String) -> Bool {
    let allowed: CharacterSet = CharacterSet(
        charactersIn: "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    return key.hasPrefix("phone.") && key.count >= 16 && key.count <= 256
        && key.unicodeScalars.allSatisfy { allowed.contains($0) }
}

/// Whether `text` has the shape of the daemon's approval ids (`apr_…`): letters, digits, `_` and `-`, bounded, so that
/// one from a notification's payload is a path segment and nothing more (item 1008).
func isApprovalId(_ text: String) -> Bool {
    let allowed: CharacterSet = CharacterSet(
        charactersIn: "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
    return !text.isEmpty && text.count <= 64 && text.unicodeScalars.allSatisfy { allowed.contains($0) }
}

/// The lowercase hex of a token as APNs and the daemon expect it.
func hexString(_ data: Data) -> String {
    data.map { String(format: "%02x", $0) }.joined()
}

/// A token shortened for the log: its first 6 hex digits.
func redactToken(_ hex: String) -> String {
    "\(hex.prefix(6))…(\(hex.count))"
}

/// The route a notification asks to open, as a daemon URL; `nil` unless it is an absolute path on the daemon or one
/// of the web UI's own routes (`#/runs/<id>`, as the daemon's pushes name them), which open on the daemon's root.
func routeURL(_ route: String, host: DaemonHost) -> URL? {
    let path: String = route.hasPrefix("#/") ? "/" + route : route
    guard path.hasPrefix("/"), !path.hasPrefix("//"), let url: URL = URL(string: path, relativeTo: host.origin)
    else {
        return nil
    }
    let absolute: URL = url.absoluteURL
    return isDaemonOrigin(absolute, host: host) ? absolute : nil
}
