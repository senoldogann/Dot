import DotEngine
import SwiftUI
import WebKit
import os

struct ShownPageCard: View {
    let page: ShownPage
    @Environment(AppModel.self) private var model: AppModel
    @State private var fullScreen: Bool = false
    @State private var height: CGFloat = 160

    var body: some View {
        if let host: DaemonHost = model.pageHost {
            let url: URL = page.url(on: host)
            PageSurface(url: url, fittedHeight: $height) {
                Button {
                    fullScreen = true
                } label: {
                    Image(systemName: "arrow.up.left.and.arrow.down.right")
                        .font(.body)
                        .frame(width: Metric.hit, height: Metric.hit)
                        .background(Tone.card, in: rounded(Radius.inner))
                        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topTrailing)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .foregroundStyle(Tone.text)
                .accessibilityLabel(
                    String(localized: "Full screen", comment: "Opens a page shown in the chat full screen")
                )
                .accessibilityHint(page.title)
                .accessibilityIdentifier("shownPage.open")
            }
            .fullScreenCover(isPresented: $fullScreen) {
                FullScreenPage(title: page.title, url: url)
            }
            .id(url)
        }
    }
}

private struct FullScreenPage: View {
    let title: String
    let url: URL
    @Environment(\.dismiss) private var dismiss: DismissAction

    var body: some View {
        NavigationStack {
            PageSurface(url: url, fittedHeight: nil) { EmptyView() }
                .navigationTitle(title)
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) {
                        Button(
                            String(localized: "Close", comment: "Sheet toolbar: closes an inspector sheet"),
                            systemImage: "xmark", role: .close
                        ) {
                            dismiss()
                        }
                        .accessibilityIdentifier("shownPage.close")
                    }
                }
        }
        .tint(Tone.text)
    }
}

private enum PageLoad: Equatable {
    case loading
    case ready
    case failed
}

private struct PageSurface<Overlay: View>: View {
    let url: URL
    let fittedHeight: Binding<CGFloat>?
    @ViewBuilder let overlay: () -> Overlay
    @State private var load: PageLoad = .loading
    @State private var attempt: Int = 0

    var body: some View {
        Group {
            if load == .failed {
                VStack(spacing: Space.xxs) {
                    Text(String(localized: "The page could not be shown.", comment: "A page in the chat failed to load"))
                        .textRole(.footnote)
                        .foregroundStyle(Tone.secondary)
                    Button(String(localized: "Show it again", comment: "Retries loading a page shown in the chat")) {
                        load = .loading
                        attempt += 1
                    }
                    .frame(minHeight: Metric.hit)
                }
                .padding(Space.s)
            } else {
                PageWebView(url: url, height: fittedHeight, load: $load)
                    .id(attempt)
                    .allowsHitTesting(fittedHeight == nil)
                    .accessibilityHidden(fittedHeight != nil)
                    .frame(height: fittedHeight?.wrappedValue)
                    .overlay {
                        if load == .loading {
                            ProgressView()
                        } else {
                            overlay()
                        }
                    }
            }
        }
        .frame(maxWidth: .infinity, maxHeight: fittedHeight == nil ? .infinity : nil)
        .background(Tone.ground)
    }
}

/// SwiftUI creates the web view only when the lazy transcript row is realised. Its stable identity lets WebKit keep
/// its document through scrolls, transcript updates and theme changes; only an explicit retry creates it again.
private struct PageWebView: UIViewRepresentable {
    let url: URL
    let height: Binding<CGFloat>?
    @Binding var load: PageLoad
    @Environment(\.colorScheme) private var scheme: ColorScheme

    func makeCoordinator() -> PageNavigation {
        PageNavigation(url: url, height: height, load: $load)
    }

    func makeUIView(context: Context) -> WKWebView {
        let configuration: WKWebViewConfiguration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        configuration.preferences.javaScriptCanOpenWindowsAutomatically = false
        configuration.dataDetectorTypes = []
        if height != nil {
            let content: WKUserContentController = configuration.userContentController
            content.add(context.coordinator, contentWorld: .defaultClient, name: "dotPageHeight")
            content.addUserScript(
                WKUserScript(
                    source: Self.measurement, injectionTime: .atDocumentEnd, forMainFrameOnly: true,
                    in: .defaultClient))
        }
        let web: WKWebView = WKWebView(frame: .zero, configuration: configuration)
        web.navigationDelegate = context.coordinator
        web.uiDelegate = context.coordinator
        web.isOpaque = false
        web.backgroundColor = .clear
        web.scrollView.isScrollEnabled = height == nil
        web.scrollView.contentInsetAdjustmentBehavior = .never
        web.overrideUserInterfaceStyle = scheme == .dark ? .dark : .light
        context.coordinator.open(in: web)
        return web
    }

    func updateUIView(_ web: WKWebView, context: Context) {
        let style: UIUserInterfaceStyle = scheme == .dark ? .dark : .light
        if web.overrideUserInterfaceStyle != style { web.overrideUserInterfaceStyle = style }
    }

    static func dismantleUIView(_ web: WKWebView, coordinator: PageNavigation) {
        web.navigationDelegate = nil
        web.uiDelegate = nil
        web.configuration.userContentController.removeScriptMessageHandler(
            forName: "dotPageHeight", contentWorld: .defaultClient)
        web.stopLoading()
    }

    // The same inner height and bounds as the web frame. Coalesce layout notifications and stop sending once capped.
    // This script and its numeric-only channel live in WebKit's isolated client world, out of reach of page scripts.
    private static let measurement: String = """
        (() => {
            const root = document.documentElement;
            let seen = 0;
            let queued = false;
            const observer = new ResizeObserver(() => {
                if (queued) return;
                queued = true;
                requestAnimationFrame(() => {
                    queued = false;
                    const size = root.scrollHeight > root.clientHeight ? root.scrollHeight : root.getBoundingClientRect().height;
                    const height = Math.min(2000, Math.max(80, Math.ceil(size)));
                    if (height === seen) return;
                    seen = height;
                    window.webkit.messageHandlers.dotPageHeight.postMessage(height);
                });
            });
            observer.observe(root);
            if (document.body) observer.observe(document.body);
        })();
        """
}

/// The only document admitted is the first successful HTML response at the exact page URL, with an enforced
/// sandbox for scripts alone. The daemon supplies the same CSP to WebKit and to the web frame.
@MainActor
private final class PageNavigation: NSObject, WKNavigationDelegate, WKUIDelegate,
    WKScriptMessageHandler
{
    private static let logger: Logger = Logger(
        subsystem: "dev.dotagent.phone.next", category: "shown-page")
    private let url: URL
    private let height: Binding<CGFloat>?
    private let load: Binding<PageLoad>
    private var initial: WKNavigation?
    private var requested: Bool = false
    private var accepted: Bool = false

    init(url: URL, height: Binding<CGFloat>?, load: Binding<PageLoad>) {
        self.url = url
        self.height = height
        self.load = load
    }

    func open(in web: WKWebView) {
        initial = web.load(URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 10))
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction) async
        -> WKNavigationActionPolicy
    {
        guard !requested, action.targetFrame?.isMainFrame != false, action.request.url == url else {
            if accepted, action.navigationType == .linkActivated {
                Self.logger.notice("A link tried to leave a shown page")
            }
            return .cancel
        }
        requested = true
        return .allow
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationResponse: WKNavigationResponse) async
        -> WKNavigationResponsePolicy
    {
        guard !accepted, navigationResponse.isForMainFrame else { return .cancel }
        guard let response: HTTPURLResponse = navigationResponse.response as? HTTPURLResponse,
            response.url == url, response.statusCode == 200, response.mimeType == "text/html",
            let policy: String = response.value(forHTTPHeaderField: "Content-Security-Policy"),
            policy.split(separator: ";").contains(where: {
                $0.split(whereSeparator: \.isWhitespace) == ["sandbox", "allow-scripts"]
            })
        else {
            Self.logger.error(
                "The page response was refused status=\((navigationResponse.response as? HTTPURLResponse)?.statusCode ?? 0)"
            )
            load.wrappedValue = .failed
            return .cancel
        }
        accepted = true
        return .allow
    }

    func webView(_ webView: WKWebView, didReceiveServerRedirectForProvisionalNavigation navigation: WKNavigation!) {
        guard navigation === initial else { return }
        Self.logger.error("The page address redirected")
        webView.stopLoading()
        load.wrappedValue = .failed
    }

    func webView(_ webView: WKWebView, didCommit navigation: WKNavigation!) {
        if accepted, navigation === initial { load.wrappedValue = .ready }
    }

    func webView(
        _ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!,
        withError error: Error
    ) {
        if navigation === initial { fail(error) }
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        if navigation === initial { fail(error) }
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        Self.logger.error("The page's WebKit process stopped")
        load.wrappedValue = .failed
    }

    private func fail(_ error: Error) {
        guard (error as? URLError)?.code != .cancelled else { return }
        Self.logger.error(
            "The page did not load error=\(error.localizedDescription, privacy: .private)")
        load.wrappedValue = .failed
    }

    func webView(
        _ webView: WKWebView, decideMediaCapturePermissionsFor origin: WKSecurityOrigin,
        initiatedBy frame: WKFrameInfo, type: WKMediaCaptureType
    ) async -> WKPermissionDecision {
        .deny
    }

    func webView(
        _ webView: WKWebView, requestDeviceOrientationAndMotionPermissionFor origin: WKSecurityOrigin,
        initiatedByFrame frame: WKFrameInfo
    ) async -> WKPermissionDecision {
        .deny
    }

    func userContentController(
        _ controller: WKUserContentController, didReceive message: WKScriptMessage
    ) {
        guard accepted, message.frameInfo.isMainFrame,
            let measured: Double = message.body as? Double, measured.isFinite,
            let height: Binding<CGFloat> = height
        else { return }
        let fitted: CGFloat = min(2000, max(80, measured.rounded(.up)))
        if fitted != height.wrappedValue { height.wrappedValue = fitted }
    }
}
