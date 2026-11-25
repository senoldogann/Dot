import DotEngine
import SwiftUI
import UIKit
import WebKit
import os

/// A page that is not native yet, opened from the daemon's web app inside DotNext (PLAN §4.4, PARITY
/// "Web row mechanism"): the page `#/settings/<tab>` of the paired Mac, signed in with this phone's key, which a script
/// puts where the page keeps its key (`localStorage["dot.token"]`, api.js) before the page's own scripts run, on the
/// Mac's origin only; the page proves the daemon holds it (`/api/proof`) before it sends it. The store is in memory
/// alone, so the key leaves with the page. Only the Mac's own origin loads here; any other address opens in Safari.
struct WebSettingsPage: View {
    /// A page of the web app's settings (`settingstabs.js` TABS).
    struct Target: Hashable, Sendable {
        let tab: String
        let title: String

        init(tab: String, title: String) {
            self.tab = tab
            self.title = title
        }

        /// The page's hash route: a settings tab (`vault` → `/settings/vault`) or a page of its own (`/watch`, `/agents`).
        var route: String { tab.hasPrefix("/") ? tab : "/settings/\(tab)" }

        /// The web's own search words of the page (settingstabs.js), so the phone's search finds it by them too.
        var words: String {
            switch tab {
            case "agents": "agent team handoffs helpers"
            case "browser": "chrome profile headless sign in"
            case "websearch": "engines searxng brave"
            case "campaigns": "goals sessions long running"
            case "harness": "harness learning lessons proposals self-improvement"
            case "connectors": "connect apps services mcp accounts google github slack notion"
            case "plugins": "gallery install extensions"
            case "skills": "learned built-in versions teach"
            case "keys": "keys api providers openai anthropic"
            case "channels": "imessage telegram whatsapp messaging"
            case "vault": "logins passwords cards contacts"
            case "snapshots": "screen capture command key pictures"
            case "phone": "tailscale pair devices iphone phones"
            case "repair": "problems fixes claude code sessions self-repair"
            case "diagnostics": "processes logs disk usage"
            case "licences": "about notices licences software open source"
            default: ""
            }
        }

        var symbol: String {
            switch tab {
            case "agents": "person.2"
            case "browser": "globe"
            case "websearch": "magnifyingglass"
            case "campaigns": "target"
            case "harness": "sparkles"
            case "connectors": "powerplug"
            case "plugins": "puzzlepiece.extension"
            case "skills": "wand.and.stars"
            case "keys": "key"
            case "channels": "message"
            case "vault": "lock"
            case "snapshots": "photo"
            case "phone": "iphone"
            case "repair": "wrench.adjustable"
            case "diagnostics": "terminal"
            case "licences": "doc.text"
            default: "globe"
            }
        }

        static var agentPages: [Target] {
            [
                Target(tab: "browser", title: String(localized: "Browser", comment: "Settings row: Dot's own Chrome")),
                Target(tab: "websearch", title: String(localized: "Web search", comment: "Settings row: how Dot searches the web")),
                Target(tab: "campaigns", title: String(localized: "Campaigns", comment: "Settings row: goals Dot keeps working on")),
                Target(tab: "harness", title: String(localized: "Self-improvement", comment: "Settings row: what Dot learns from its tasks")),
            ]
        }

        static var servicePages: [Target] {
            [
                Target(tab: "connectors", title: String(localized: "Connectors", comment: "Settings row: the apps and services Dot works with")),
                Target(tab: "plugins", title: String(localized: "Plugins", comment: "Settings row: plugins Dot can install")),
                Target(tab: "skills", title: String(localized: "Skills", comment: "Settings row: what Dot knows how to do")),
                Target(tab: "keys", title: String(localized: "AI services", comment: "Settings row: the keys of the AI services")),
                Target(tab: "channels", title: String(localized: "Channels", comment: "Settings row: iMessage and the other ways to reach Dot")),
                Target(tab: "vault", title: String(localized: "Vault", comment: "Settings row: logins, cards and contacts Dot can use")),
                Target(tab: "snapshots", title: String(localized: "Snapshots", comment: "Settings row: pictures of the screen for Dot")),
            ]
        }

        static var systemPages: [Target] {
            [
                Target(tab: "phone", title: String(localized: "Phones and Tailscale", comment: "Settings row: the web page of paired phones and the tailnet")),
                Target(tab: "repair", title: String(localized: "Self-repair", comment: "Settings row: Dot mends its own problems")),
                Target(tab: "diagnostics", title: String(localized: "Diagnostics", comment: "Settings row: the programs Dot runs and its logs")),
                Target(tab: "licences", title: String(localized: "Open source licences", comment: "Settings row: the software Dot is built from")),
            ]
        }

        static var allPages: [Target] { agentPages + servicePages + systemPages }
    }

    let target: Target
    @Environment(AppModel.self) private var model: AppModel
    @State private var loading: Bool = true
    @State private var problem: String?
    @State private var reloads: Int = 0

    var body: some View {
        Group {
            if let api = model.settingsAPI {
                ZStack {
                    DaemonWebView(api: api, route: target.route, reloads: reloads, loading: $loading, problem: $problem)
                        .opacity(problem == nil ? 1 : 0)
                    if let problem {
                        EmptyState(
                            title: String(localized: "The page did not open", comment: "Web settings page: the load failed"), systemImage: "wifi.exclamationmark",
                            detail: problem, actionTitle: String(localized: "Try again", comment: "Settings: repeats a read or write that failed")
                        ) {
                            self.problem = nil
                            reloads += 1
                        }
                    } else if loading {
                        ProgressView().controlSize(.large).tint(Tone.secondary)
                    }
                }
            } else {
                EmptyState(
                    title: String(localized: "Not paired with a Mac", comment: "Emergency stop: no Mac to stop"), systemImage: "desktopcomputer",
                    detail: String(localized: "Pair this phone first.", comment: "Web settings page: no Mac to open"), actionTitle: nil, action: {})
            }
        }
        .background(Tone.ground)
        .navigationTitle(target.title)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) { WebMark() }
        }
    }
}

/// Where a navigation of the web page may go: the Mac's own origin loads; an http(s) address elsewhere opens in Safari;
/// anything else is blocked (pure, tested).
enum WebNavigation: Equatable {
    case allow
    case openExternally
    case block
}

func decideWebNavigation(url: URL, origin: URL) -> WebNavigation {
    if url.scheme == origin.scheme, url.host()?.lowercased() == origin.host()?.lowercased(), url.port == origin.port { return .allow }
    if url.scheme == "about" { return .allow }
    if url.scheme == "https" || url.scheme == "http" || url.scheme == "mailto" { return .openExternally }
    return .block
}

/// The address of a route of the web app on the Mac (`/settings/vault` → `https://<Mac>/#/settings/vault`).
func webAddress(origin: URL, route: String) -> URL {
    var components: URLComponents = URLComponents(url: origin, resolvingAgainstBaseURL: false) ?? URLComponents()
    components.path = "/"
    components.fragment = route
    return components.url ?? origin
}

/// The script that hands the page this phone's key before its own scripts run: it writes the key only on the Mac's
/// own origin (a page of any other origin gets nothing), as JSON so no character of it is read as script.
func keyScript(origin: URL, key: String) -> String {
    let json: (String) -> String = { text in
        do {
            return String(decoding: try JSONEncoder().encode(text), as: UTF8.self)
        } catch {
            preconditionFailure("a String always encodes as JSON: \(error)")
        }
    }
    let expected: String = origin.absoluteString.hasSuffix("/") ? String(origin.absoluteString.dropLast()) : origin.absoluteString
    return "if (location.origin === \(json(expected))) { try { localStorage.setItem(\"dot.token\", \(json(key))); } catch (e) {} }"
}

struct DaemonWebView: UIViewRepresentable {
    let api: DaemonAPI
    let route: String
    let reloads: Int
    @Binding var loading: Bool
    @Binding var problem: String?

    func makeCoordinator() -> Coordinator { Coordinator(origin: api.host.origin, route: route, loading: $loading, problem: $problem) }

    func makeUIView(context: Context) -> WKWebView {
        let configuration: WKWebViewConfiguration = WKWebViewConfiguration()
        // A store of its own in memory: the key the page keeps lives as long as this page, never in a shared store.
        configuration.websiteDataStore = .nonPersistent()
        configuration.applicationNameForUserAgent = "Mobile/15E148 DotNext"
        configuration.userContentController.addUserScript(
            WKUserScript(source: keyScript(origin: api.host.origin, key: api.key), injectionTime: .atDocumentStart, forMainFrameOnly: true))
        let view: WKWebView = WKWebView(frame: .zero, configuration: configuration)
        view.navigationDelegate = context.coordinator
        view.uiDelegate = context.coordinator
        view.isOpaque = false
        view.backgroundColor = .clear
        #if DEBUG
            view.isInspectable = true
        #endif
        context.coordinator.lastReloads = reloads
        view.load(URLRequest(url: webAddress(origin: api.host.origin, route: route)))
        return view
    }

    func updateUIView(_ view: WKWebView, context: Context) {
        guard reloads != context.coordinator.lastReloads else { return }
        context.coordinator.lastReloads = reloads
        view.load(URLRequest(url: webAddress(origin: api.host.origin, route: route)))
    }

    @MainActor
    final class Coordinator: NSObject, WKNavigationDelegate, WKUIDelegate {
        let origin: URL
        let route: String
        @Binding var loading: Bool
        @Binding var problem: String?
        var lastReloads: Int = 0
        private let logger: Logger = Logger(subsystem: "dev.dotagent.phone.next", category: "web")

        init(origin: URL, route: String, loading: Binding<Bool>, problem: Binding<String?>) {
            self.origin = origin
            self.route = route
            _loading = loading
            _problem = problem
        }

        func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction) async -> WKNavigationActionPolicy {
            guard let url: URL = navigationAction.request.url else { return .cancel }
            switch decideWebNavigation(url: url, origin: origin) {
            case .allow: return .allow
            case .openExternally:
                await UIApplication.shared.open(url)
                return .cancel
            case .block:
                logger.warning("web settings navigation blocked scheme=\(url.scheme ?? "", privacy: .public)")
                return .cancel
            }
        }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            loading = false
        }

        func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: any Error) { report(error) }
        func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: any Error) { report(error) }

        func webViewWebContentProcessDidTerminate(_ webView: WKWebView) { webView.reload() }

        func webView(
            _ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration, for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures
        ) -> WKWebView? {
            if let url: URL = navigationAction.request.url {
                switch decideWebNavigation(url: url, origin: origin) {
                case .allow: webView.load(navigationAction.request)
                case .openExternally: UIApplication.shared.open(url)
                case .block: break
                }
            }
            return nil
        }

        private func report(_ error: any Error) {
            let nsError: NSError = error as NSError
            if nsError.domain == NSURLErrorDomain && nsError.code == NSURLErrorCancelled { return }
            if nsError.domain == "WebKitErrorDomain" && nsError.code == 102 { return }
            logger.error("web settings load failed domain=\(nsError.domain, privacy: .public) code=\(nsError.code)")
            loading = false
            problem = nsError.localizedDescription
        }
    }
}
