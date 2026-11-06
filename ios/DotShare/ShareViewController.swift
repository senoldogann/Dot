import SwiftUI
import UIKit
import UniformTypeIdentifiers
import os

/// The share sheet's Dot (item 1009): what another app shares (a page's address, text, up to four pictures) goes to Dot
/// as a new chat, with the words the owner adds of what to do with it. The phone's key and the daemon's host are the
/// app's own, read from the Keychain's access group and the app group; nothing is sent until the owner taps Send.
final class ShareViewController: UIViewController {
    override func viewDidLoad() {
        super.viewDidLoad()
        let model: ShareModel = ShareModel(
            items: (extensionContext?.inputItems as? [NSExtensionItem]) ?? [],
            onDone: { [weak self] in self?.extensionContext?.completeRequest(returningItems: nil) },
            onCancel: { [weak self] in
                self?.extensionContext?.cancelRequest(withError: CocoaError(.userCancelled))
            })
        let host: UIHostingController<ShareView> = UIHostingController(rootView: ShareView(model: model))
        addChild(host)
        host.view.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(host.view)
        NSLayoutConstraint.activate([
            host.view.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            host.view.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            host.view.topAnchor.constraint(equalTo: view.topAnchor),
            host.view.bottomAnchor.constraint(equalTo: view.bottomAnchor),
        ])
        host.didMove(toParent: self)
        Task { await model.read() }
    }
}

/// What was shared, read from the extension's items.
struct Shared: Sendable {
    var urls: [URL] = []
    var texts: [String] = []
    var pictures: [Data] = []
}

/// The most pictures one share sends, and the longest side each is sent at.
private let maxPictures: Int = 4
private let pictureSide: CGFloat = 2048

@MainActor
@Observable
final class ShareModel {
    var shared: Shared = Shared()
    var note: String = ""
    var reading: Bool = true
    var sending: Bool = false
    var problem: String?

    @ObservationIgnored private let items: [NSExtensionItem]
    @ObservationIgnored private let onDone: () -> Void
    @ObservationIgnored let onCancel: () -> Void
    @ObservationIgnored private let logger: Logger = Logger(subsystem: "dev.dotagent.phone", category: "share")

    init(items: [NSExtensionItem], onDone: @escaping () -> Void, onCancel: @escaping () -> Void) {
        self.items = items
        self.onDone = onDone
        self.onCancel = onCancel
    }

    /// Reads the shared items: an address, a text and the pictures, each kind in the order shared.
    func read() async {
        var found: Shared = Shared()
        for provider in items.flatMap({ $0.attachments ?? [] }) {
            if provider.hasItemConformingToTypeIdentifier(UTType.url.identifier) {
                if let url: URL = try? await provider.loadItem(forTypeIdentifier: UTType.url.identifier) as? URL {
                    found.urls.append(url)
                }
            } else if provider.hasItemConformingToTypeIdentifier(UTType.image.identifier),
                found.pictures.count < maxPictures
            {
                if let jpeg: Data = await jpegOf(provider) {
                    found.pictures.append(jpeg)
                }
            } else if provider.hasItemConformingToTypeIdentifier(UTType.plainText.identifier) {
                if let text: String = try? await provider.loadItem(forTypeIdentifier: UTType.plainText.identifier)
                    as? String
                {
                    found.texts.append(text)
                }
            }
        }
        shared = found
        reading = false
    }

    /// Sends the share as a new chat: each picture kept first, then the chat with the note, the texts and the addresses.
    func send() async {
        sending = true
        problem = nil
        let hostStore: HostStore = HostStore()
        guard let host: DaemonHost = hostStore.load(buildHost: buildHostFromInfoPlist()) else {
            fail("Open Dot once and type your Mac's address first.")
            return
        }
        let key: String
        do {
            guard let saved: String = try PhoneKeyStore().read() else {
                fail("Open Dot and sign in with your Mac's code first.")
                return
            }
            key = saved
        } catch {
            logger.error("phone key unreadable: error=\(error.description, privacy: .public)")
            fail("Dot could not read this phone's key. Open Dot and sign in again.")
            return
        }
        let client: DaemonClient = DaemonClient(session: URLSession(configuration: .ephemeral), maxAttempts: 3)
        do {
            var ids: [String] = []
            for (index, picture) in shared.pictures.enumerated() {
                let upload: AttachmentUpload = AttachmentUpload(
                    name: "shared-\(index + 1).jpg", mediaType: "image/jpeg",
                    dataB64: picture.base64EncodedString())
                ids.append(try await client.uploadAttachment(upload, host: host, key: key))
            }
            try await client.startChat(ChatStart(goal: goalOf(shared, note: note), attachments: ids), host: host, key: key)
            logger.info("shared to Dot: pictures=\(ids.count) addresses=\(self.shared.urls.count)")
            onDone()
        } catch {
            logger.error("sharing to Dot failed: error=\(String(describing: error), privacy: .public)")
            fail("Dot did not take it. Check that Tailscale is on, then try again.")
        }
    }

    private func fail(_ message: String) {
        problem = message
        sending = false
    }
}

/// The new chat's words: what the owner wrote, then what was shared, each on its own line; "Look at this" when the owner
/// wrote nothing.
func goalOf(_ shared: Shared, note: String) -> String {
    let asked: String = note.trimmingCharacters(in: .whitespacesAndNewlines)
    let lines: [String] =
        [asked.isEmpty ? "Look at this." : asked]
        + shared.texts.map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }.filter { !$0.isEmpty }
        + shared.urls.map(\.absoluteString)
    return lines.joined(separator: "\n\n")
}

/// The picture of `provider` as a JPEG at most `pictureSide` on its longer side, or `nil` when it is none.
@MainActor
private func jpegOf(_ provider: NSItemProvider) async -> Data? {
    let item: NSSecureCoding? = try? await provider.loadItem(forTypeIdentifier: UTType.image.identifier)
    let image: UIImage?
    switch item {
    case let url as URL:
        image = UIImage(contentsOfFile: url.path())
    case let data as Data:
        image = UIImage(data: data)
    case let picture as UIImage:
        image = picture
    default:
        image = nil
    }
    guard let image else {
        return nil
    }
    let scale: CGFloat = min(1, pictureSide / max(image.size.width, image.size.height))
    let size: CGSize = CGSize(width: image.size.width * scale, height: image.size.height * scale)
    let drawn: UIImage = UIGraphicsImageRenderer(size: size).image { _ in image.draw(in: CGRect(origin: .zero, size: size)) }
    return drawn.jpegData(compressionQuality: 0.85)
}

struct ShareView: View {
    @Bindable var model: ShareModel

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("What should Dot do with it?", text: $model.note, axis: .vertical)
                        .lineLimit(2...6)
                }
                Section("Sharing") {
                    if model.reading {
                        ProgressView()
                    } else {
                        ForEach(model.shared.urls, id: \.self) { url in
                            Label(url.host() ?? url.absoluteString, systemImage: "link").lineLimit(1)
                        }
                        ForEach(Array(model.shared.texts.enumerated()), id: \.offset) { _, text in
                            Label(text, systemImage: "text.alignleft").lineLimit(2)
                        }
                        if !model.shared.pictures.isEmpty {
                            Label(
                                model.shared.pictures.count == 1 ? "1 picture" : "\(model.shared.pictures.count) pictures",
                                systemImage: "photo")
                        }
                    }
                }
                if let problem: String = model.problem {
                    Section {
                        Text(problem).foregroundStyle(.red)
                    }
                }
            }
            .navigationTitle("Send to Dot")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { model.onCancel() }
                        .disabled(model.sending)
                }
                ToolbarItem(placement: .confirmationAction) {
                    if model.sending {
                        ProgressView()
                    } else {
                        Button("Send") { Task { await model.send() } }
                            .disabled(model.reading)
                    }
                }
            }
        }
    }
}
