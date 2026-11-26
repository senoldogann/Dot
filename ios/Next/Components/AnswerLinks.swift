import DotEngine
import SafariServices
import SwiftUI
import UIKit
import os

/// What the rows under answers log: a site's icon that could not be fetched, a file that could not be shown.
let answersLogger: Logger = Logger(subsystem: "dev.dotagent.phone.next", category: "answers")

/// An answer whose references open in place (the owner, 2026-11-26): a link in its words opens in Safari's in-app view,
/// a file it names (`answerInline`'s `dotfile:` address) in its preview sheet. Nothing is listed under the answer.
struct AnswerBody: View {
    let markdown: String
    let writing: Bool
    @State private var openedLink: OpenedLink?
    @State private var openedFile: OpenedFile?

    var body: some View {
        AnswerView(markdown: markdown, writing: writing)
            .environment(
                \.openURL,
                OpenURLAction { url in
                    if let path: String = fileReferencePath(url) {
                        openedFile = OpenedFile(path: path)
                        return .handled
                    }
                    guard let scheme: String = url.scheme?.lowercased(), scheme == "http" || scheme == "https" else { return .systemAction }
                    openedLink = OpenedLink(url: url)
                    return .handled
                }
            )
            .sheet(item: $openedLink) { link in SafariSheet(url: link.url).ignoresSafeArea() }
            .sheet(item: $openedFile) { file in FilePreviewSheet(path: file.path, close: { openedFile = nil }) }
    }
}

struct OpenedLink: Identifiable, Hashable {
    let url: URL
    var id: URL { url }
}

struct OpenedFile: Identifiable, Hashable {
    let path: String
    var id: String { path }
}

/// The sites' icons the phone fetched: in memory, and on disk in the app's caches (`SiteIcons/<site>.png`) so a site is
/// asked once. A site is asked for its own icon only (`/apple-touch-icon.png`, then `/favicon.ico`), never another
/// service; a site that has none is not asked again until the app starts anew.
actor SiteIcons {
    static let shared: SiteIcons = SiteIcons(folder: URL.cachesDirectory.appending(path: "SiteIcons", directoryHint: .isDirectory))

    private let folder: URL
    private let session: URLSession
    private var held: [String: UIImage] = [:]
    private var missing: Set<String> = []
    private var asking: [String: Task<UIImage?, Never>] = [:]

    init(folder: URL) {
        self.folder = folder
        let configuration: URLSessionConfiguration = .ephemeral
        configuration.timeoutIntervalForRequest = 8
        configuration.httpCookieAcceptPolicy = .never
        session = URLSession(configuration: configuration)
    }

    func icon(for url: URL) async -> UIImage? {
        guard let site: String = bareSite(url), let scheme: String = url.scheme?.lowercased() else { return nil }
        let key: String = siteIconKey(scheme: scheme, site: url.host(percentEncoded: false) ?? site, port: url.port)
        if let image: UIImage = held[key] { return image }
        if missing.contains(key) { return nil }
        if let pending: Task<UIImage?, Never> = asking[key] { return await pending.value }
        let file: URL = folder.appending(path: key + ".png")
        let session: URLSession = session
        let task: Task<UIImage?, Never> = Task {
            if let data: Data = try? Data(contentsOf: file), let image: UIImage = UIImage(data: data) { return image }
            for path in ["/apple-touch-icon.png", "/favicon.ico"] {
                var parts: URLComponents = URLComponents()
                parts.scheme = scheme
                parts.host = url.host(percentEncoded: false)
                parts.port = url.port
                parts.path = path
                guard let address: URL = parts.url else { continue }
                do {
                    let (data, response): (Data, URLResponse) = try await session.data(from: address)
                    guard (response as? HTTPURLResponse)?.statusCode == 200, let image: UIImage = UIImage(data: data), image.size.width >= 8 else { continue }
                    if let png: Data = image.pngData() {
                        try FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
                        try png.write(to: file, options: .atomic)
                    }
                    return image
                } catch {
                    answersLogger.info("a site's icon was not fetched address=\(address.absoluteString, privacy: .public) error=\(String(describing: error), privacy: .public)")
                }
            }
            return nil
        }
        asking[key] = task
        let image: UIImage? = await task.value
        asking[key] = nil
        if let image { held[key] = image } else { missing.insert(key) }
        return image
    }
}

/// The name a site's icon is kept under: its scheme, host and port in letters a file name takes (pure).
func siteIconKey(scheme: String, site: String, port: Int?) -> String {
    let raw: String = "\(scheme)_\(site)" + (port.map { "_\($0)" } ?? "")
    return String(raw.lowercased().map { $0.isLetter || $0.isNumber || $0 == "." || $0 == "-" ? $0 : "_" })
}

/// A web page in Safari's own in-app view, in a sheet: its Done and a swipe down close it.
struct SafariSheet: UIViewControllerRepresentable {
    let url: URL

    func makeUIViewController(context: Context) -> SFSafariViewController {
        let configuration: SFSafariViewController.Configuration = SFSafariViewController.Configuration()
        configuration.entersReaderIfAvailable = false
        let controller: SFSafariViewController = SFSafariViewController(url: url, configuration: configuration)
        controller.dismissButtonStyle = .done
        return controller
    }

    func updateUIViewController(_ controller: SFSafariViewController, context: Context) {}
}
