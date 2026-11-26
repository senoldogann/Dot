import DotEngine
import SafariServices
import SwiftUI
import UIKit
import os

/// What the rows under answers log: a site's icon that could not be fetched, a file that could not be shown.
let answersLogger: Logger = Logger(subsystem: "dev.dotagent.phone.next", category: "answers")

/// An answer with what it names under it (backlog LF1): the files as chips that open a preview sheet, the web links as
/// one row of their sites that opens a page in an in-app Safari sheet. A link in the words opens there too. Nothing
/// shows under words still being written.
struct AnswerBody: View {
    let markdown: String
    let writing: Bool
    @State private var openedLink: OpenedLink?
    @State private var openedFile: OpenedFile?

    var body: some View {
        let links: [AnswerLink] = writing ? [] : answerLinks(markdown)
        let files: [String] = writing ? [] : answerFiles(markdown)
        VStack(alignment: .leading, spacing: Space.s) {
            AnswerView(markdown: markdown, writing: writing)
            if !files.isEmpty {
                VStack(alignment: .leading, spacing: Space.xs) {
                    ForEach(files, id: \.self) { path in FileChip(path: path, open: { openedFile = OpenedFile(path: path) }) }
                }
            }
            if !links.isEmpty { SourcesRow(links: links, open: { openedLink = OpenedLink(url: $0) }) }
        }
        .environment(
            \.openURL,
            OpenURLAction { url in
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

/// The links of an answer: one link as a chip of its site; several as one "N sources" chip with the first sites'
/// icons, which unfolds into a row per link (the web's sources row, item 1184).
struct SourcesRow: View {
    let links: [AnswerLink]
    let open: (URL) -> Void
    @State private var unfolded: Bool = false

    var body: some View {
        if links.count == 1, let link = links.first {
            LinkChip(link: link, open: open)
        } else {
            VStack(alignment: .leading, spacing: 0) {
                Button {
                    withAnimation(.snappy(duration: 0.2)) { unfolded.toggle() }
                } label: {
                    HStack(spacing: Space.xs) {
                        HStack(spacing: -Space.xxs) {
                            ForEach(Array(sites.prefix(3).enumerated()), id: \.offset) { _, link in
                                SiteIcon(link: link, size: 18).padding(1.5).background(Tone.ground, in: Circle())
                            }
                        }
                        Text(String(localized: "\(links.count) sources", comment: "Under an answer: how many web links it names; tap unfolds them"))
                            .textRole(.footnote).fontWeight(.medium).foregroundStyle(Tone.secondary)
                        Symbol(name: "chevron.down", context: .chevron).foregroundStyle(Tone.tertiary).rotationEffect(.degrees(unfolded ? 180 : 0))
                    }
                    .padding(.horizontal, Space.s)
                    .frame(minHeight: 32)
                    .background(Tone.nested, in: Capsule())
                    .frame(minHeight: Metric.hit)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("answer.sources")
                .accessibilityValue(
                    unfolded
                        ? String(localized: "Unfolded", comment: "VoiceOver: the sources row shows each link")
                        : String(localized: "Folded", comment: "VoiceOver: the sources row shows only the count"))
                if unfolded {
                    VStack(alignment: .leading, spacing: 0) {
                        ForEach(links, id: \.url) { link in LinkRow(link: link, open: open) }
                    }
                    .transition(.opacity)
                }
            }
        }
    }

    /// One link of each site, in order, for the folded chip's icons.
    private var sites: [AnswerLink] {
        var seen: Set<String> = []
        return links.filter { seen.insert($0.site).inserted }
    }
}

/// One link alone: a capsule of its site's icon and its bare domain.
struct LinkChip: View {
    let link: AnswerLink
    let open: (URL) -> Void

    var body: some View {
        Button { open(link.url) } label: {
            HStack(spacing: Space.xs) {
                SiteIcon(link: link, size: 18)
                Text(verbatim: link.site).textRole(.footnote).fontWeight(.medium).foregroundStyle(Tone.secondary).lineLimit(1)
            }
            .padding(.horizontal, Space.s)
            .frame(minHeight: 32)
            .background(Tone.nested, in: Capsule())
            .frame(minHeight: Metric.hit)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .linkMenu(link.url)
        .accessibilityLabel(link.site)
        .accessibilityHint(String(localized: "Opens the page", comment: "VoiceOver hint of a link under an answer"))
        .accessibilityIdentifier("answer.link")
    }
}

/// A link of an unfolded sources row: its site's icon, its bare domain, and the rest of its address quieter.
struct LinkRow: View {
    let link: AnswerLink
    let open: (URL) -> Void

    var body: some View {
        Button { open(link.url) } label: {
            HStack(spacing: Space.s) {
                SiteIcon(link: link, size: 18)
                Text(verbatim: link.site).textRole(.footnote).fontWeight(.medium).foregroundStyle(Tone.text).lineLimit(1).layoutPriority(1)
                Text(verbatim: addressRest(link.url)).textRole(.footnote).foregroundStyle(Tone.tertiary).lineLimit(1).truncationMode(.middle)
                Spacer(minLength: 0)
            }
            .padding(.horizontal, Space.s)
            .frame(minHeight: Metric.hit)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .linkMenu(link.url)
        .accessibilityLabel(link.site)
        .accessibilityHint(String(localized: "Opens the page", comment: "VoiceOver hint of a link under an answer"))
        .accessibilityIdentifier("answer.source")
    }
}

/// The part of an address after its site (`/blog/dot`), empty for a site's front page (pure).
func addressRest(_ url: URL) -> String {
    var rest: String = url.path(percentEncoded: false)
    if let query: String = url.query(percentEncoded: false), !query.isEmpty { rest += "?" + query }
    return rest == "/" ? "" : rest
}

extension View {
    /// A link's long press: Copy, Open in Safari, Share.
    func linkMenu(_ url: URL) -> some View {
        contextMenu {
            Button(String(localized: "Copy link", comment: "Long press on a link under an answer: copies its address"), systemImage: "doc.on.doc") {
                UIPasteboard.general.url = url
            }
            Button(String(localized: "Open in Safari", comment: "Long press on a link under an answer: opens it in the Safari app"), systemImage: "safari") {
                UIApplication.shared.open(url)
            }
            ShareLink(item: url) { Label(String(localized: "Share", comment: "Long press on a link or a file preview: the share sheet"), systemImage: "square.and.arrow.up") }
        }
    }
}

/// A site's own icon, fetched once from the site and kept on the phone (`SiteIcons`); its first letter on the nested
/// disc until one comes, or when the site has none.
struct SiteIcon: View {
    let link: AnswerLink
    let size: CGFloat
    @Environment(AppModel.self) private var model: AppModel
    @State private var icon: UIImage?

    var body: some View {
        Group {
            if let icon {
                Image(uiImage: icon).resizable().interpolation(.high).scaledToFit()
                    .clipShape(RoundedRectangle(cornerRadius: size * 0.22, style: .continuous))
            } else {
                Text(verbatim: String(link.site.prefix(1)).uppercased())
                    .font(.system(size: size * 0.55, weight: .semibold))
                    .foregroundStyle(Tone.secondary)
                    .frame(width: size, height: size)
                    .background(Tone.card, in: Circle())
            }
        }
        .frame(width: size, height: size)
        .accessibilityHidden(true)
        // Only for a paired Mac's chats: the fixture scenes of the tests draw the letter, so no test reaches a site.
        .task(id: link.site) { icon = model.engine == nil ? nil : await SiteIcons.shared.icon(for: link.url) }
    }
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
