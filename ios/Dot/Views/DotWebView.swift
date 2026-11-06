import SwiftUI
import UIKit
import WebKit
import os

/// The daemon's web UI in a WKWebView that loads only `https://<host>`.
///
/// Navigations to another origin go to Safari (`decideNavigation`). When the build names `DOT_HOST`, the host is the
/// app's only `WKAppBoundDomains` entry and `limitsNavigationsToAppBoundDomains` makes WebKit enforce it as well;
/// without it (the host typed at run time) the app's own check is what holds.
struct DotWebView: UIViewRepresentable {
    let host: DaemonHost
    let model: AppModel
    /// Bumped by the error screen's "Try again"; a change reloads the page.
    let reloadCount: Int
    let onLoadError: (String?) -> Void

    func makeCoordinator() -> Coordinator {
        Coordinator(host: host, model: model, onLoadError: onLoadError)
    }

    func makeUIView(context: Context) -> WKWebView {
        let configuration: WKWebViewConfiguration = WKWebViewConfiguration()
        configuration.limitsNavigationsToAppBoundDomains = model.buildHost != nil
        configuration.allowsInlineMediaPlayback = true
        configuration.mediaTypesRequiringUserActionForPlayback = []
        configuration.applicationNameForUserAgent = "Mobile/15E148 DotPhone/\(appVersion())"
        configuration.websiteDataStore = .default()
        configuration.userContentController.add(
            WeakMessageHandler(target: context.coordinator), contentWorld: .page, name: "dot")

        let webView: WKWebView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = context.coordinator
        webView.uiDelegate = context.coordinator
        webView.allowsBackForwardNavigationGestures = true
        webView.scrollView.contentInsetAdjustmentBehavior = .never
        #if DEBUG
            webView.isInspectable = true
        #endif

        let press: UILongPressGestureRecognizer = UILongPressGestureRecognizer(
            target: context.coordinator, action: #selector(Coordinator.openSettings(_:)))
        press.numberOfTouchesRequired = 2
        press.minimumPressDuration = 0.8
        press.delegate = context.coordinator
        webView.addGestureRecognizer(press)

        context.coordinator.lastReloadCount = reloadCount
        webView.load(URLRequest(url: host.origin))
        return webView
    }

    func updateUIView(_ webView: WKWebView, context: Context) {
        if reloadCount != context.coordinator.lastReloadCount {
            context.coordinator.lastReloadCount = reloadCount
            if webView.url == nil {
                webView.load(URLRequest(url: host.origin))
            } else {
                webView.reload()
            }
        }
        if let url: URL = model.pendingURL {
            model.pendingURL = nil
            webView.load(URLRequest(url: url))
        }
    }

    static func dismantleUIView(_ webView: WKWebView, coordinator: Coordinator) {
        webView.configuration.userContentController.removeScriptMessageHandler(forName: "dot", contentWorld: .page)
    }

    @MainActor
    final class Coordinator: NSObject, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandler,
        UIGestureRecognizerDelegate
    {
        private let host: DaemonHost
        private let model: AppModel
        private let onLoadError: (String?) -> Void
        private let logger: Logger = Logger(subsystem: "dev.dotagent.phone", category: "web")
        var lastReloadCount: Int = 0

        init(host: DaemonHost, model: AppModel, onLoadError: @escaping (String?) -> Void) {
            self.host = host
            self.model = model
            self.onLoadError = onLoadError
        }

        // MARK: Navigation

        func webView(
            _ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction
        ) async -> WKNavigationActionPolicy {
            guard let url: URL = navigationAction.request.url else {
                return .cancel
            }
            let opensFrame: Bool = navigationAction.targetFrame?.isMainFrame ?? true
            switch decideNavigation(url: url, host: host, opensFrame: opensFrame) {
            case .allow:
                return .allow
            case .openExternally:
                await UIApplication.shared.open(url)
                return .cancel
            case .block:
                logger.warning("navigation blocked: scheme=\(url.scheme ?? "", privacy: .public) host=\(url.host() ?? "", privacy: .public)")
                return .cancel
            }
        }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            onLoadError(nil)
        }

        func webView(
            _ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: any Error
        ) {
            reportLoadError(error)
        }

        func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: any Error) {
            reportLoadError(error)
        }

        func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
            logger.warning("web content process ended: reloading")
            webView.reload()
        }

        private func reportLoadError(_ error: any Error) {
            let nsError: NSError = error as NSError
            // A navigation cancelled by the policy above (or by a newer one) is not a failure to load.
            if nsError.domain == NSURLErrorDomain && nsError.code == NSURLErrorCancelled {
                return
            }
            if nsError.domain == "WebKitErrorDomain" && nsError.code == 102 {  // Frame load interrupted by policy.
                return
            }
            logger.error("load failed: domain=\(nsError.domain, privacy: .public) code=\(nsError.code)")
            onLoadError(nsError.localizedDescription)
        }

        // MARK: Windows and permissions

        /// `target=_blank` and `window.open`: the daemon's own pages load here, other origins go to Safari.
        func webView(
            _ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
            for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures
        ) -> WKWebView? {
            guard let url: URL = navigationAction.request.url else {
                return nil
            }
            switch decideNavigation(url: url, host: host, opensFrame: true) {
            case .allow:
                webView.load(navigationAction.request)
            case .openExternally:
                UIApplication.shared.open(url)
            case .block:
                logger.warning("new window blocked: scheme=\(url.scheme ?? "", privacy: .public)")
            }
            return nil
        }

        /// The microphone and camera, for the daemon's own main frame only (voice messages, photos).
        func webView(
            _ webView: WKWebView, decideMediaCapturePermissionsFor origin: WKSecurityOrigin,
            initiatedBy frame: WKFrameInfo, type: WKMediaCaptureType
        ) async -> WKPermissionDecision {
            isDaemonFrame(frame) ? .grant : .deny
        }

        // MARK: Bridge

        func userContentController(
            _ userContentController: WKUserContentController, didReceive message: WKScriptMessage
        ) {
            let frame: WKFrameInfo = message.frameInfo
            let origin: MessageOrigin = MessageOrigin(
                isMainFrame: frame.isMainFrame, scheme: frame.securityOrigin.protocol,
                host: frame.securityOrigin.host, port: frame.securityOrigin.port,
                path: frame.request.url?.path() ?? "")
            switch readBridgeMessage(body: message.body, origin: origin, host: host) {
            case .success(.paired(let key)):
                model.didPair(key: key)
            case .failure(let rejection):
                logger.warning("bridge message refused: reason=\(String(describing: rejection), privacy: .public)")
            }
        }

        private func isDaemonFrame(_ frame: WKFrameInfo) -> Bool {
            frame.isMainFrame && !isRunPage(frame.request.url?.path() ?? "")
                && frame.securityOrigin.protocol == "https"
                && frame.securityOrigin.host.lowercased() == host.name
                && (frame.securityOrigin.port == 0 || frame.securityOrigin.port == 443)
        }

        // MARK: Settings gesture

        @objc func openSettings(_ recognizer: UILongPressGestureRecognizer) {
            if recognizer.state == .began {
                model.showsSettings = true
            }
        }

        func gestureRecognizer(
            _ gestureRecognizer: UIGestureRecognizer,
            shouldRecognizeSimultaneouslyWith otherGestureRecognizer: UIGestureRecognizer
        ) -> Bool {
            true
        }
    }
}

/// Holds the coordinator weakly: `WKUserContentController` retains its handlers, which would keep the web view's
/// coordinator alive for ever.
@MainActor
private final class WeakMessageHandler: NSObject, WKScriptMessageHandler {
    private weak var target: (any WKScriptMessageHandler)?

    init(target: any WKScriptMessageHandler) {
        self.target = target
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage)
    {
        target?.userContentController(userContentController, didReceive: message)
    }
}

private func appVersion() -> String {
    (Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String) ?? "0"
}
