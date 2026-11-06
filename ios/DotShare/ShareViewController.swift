import SwiftUI
import UIKit
import ImageIO
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

/// What was shared, read from the extension's items, and how many items could not be read.
struct Shared: Sendable {
    var urls: [URL] = []
    var texts: [String] = []
    var pictures: [Data] = []
    var unread: Int = 0
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
    /// The share's key: one chat however often Send is tapped or the request sent again.
    @ObservationIgnored private let key: String = UUID().uuidString
    /// The ids of the pictures kept, so that Send tapped again keeps none twice.
    @ObservationIgnored private var kept: [String] = []
    @ObservationIgnored private let onDone: () -> Void
    @ObservationIgnored let onCancel: () -> Void
    @ObservationIgnored private let logger: Logger = Logger(subsystem: "dev.dotagent.phone", category: "share")

    init(items: [NSExtensionItem], onDone: @escaping () -> Void, onCancel: @escaping () -> Void) {
        self.items = items
        self.onDone = onDone
        self.onCancel = onCancel
    }

    /// Reads the shared items: an address, a text and the pictures, each kind in the order shared. An item that cannot
    /// be read (a picture still in iCloud, a format the phone cannot draw) is logged and counted, and the sheet says so.
    func read() async {
        var found: Shared = Shared()
        for provider in items.flatMap({ $0.attachments ?? [] }) {
            do {
                // A picture first: a photo is handed over as a file's address too, which is no page to read.
                if provider.hasItemConformingToTypeIdentifier(UTType.image.identifier) {
                    if found.pictures.count < maxPictures {
                        found.pictures.append(try await jpeg(from: try await pictureData(provider)))
                    }
                } else if provider.hasItemConformingToTypeIdentifier(UTType.url.identifier) {
                    guard let url: URL = try await provider.loadItem(forTypeIdentifier: UTType.url.identifier) as? URL
                    else {
                        throw CocoaError(.fileReadCorruptFile)
                    }
                    found.urls.append(url)
                } else if provider.hasItemConformingToTypeIdentifier(UTType.plainText.identifier) {
                    guard
                        let text: String = try await provider.loadItem(
                            forTypeIdentifier: UTType.plainText.identifier) as? String
                    else {
                        throw CocoaError(.fileReadCorruptFile)
                    }
                    found.texts.append(text)
                }
            } catch {
                logger.error("a shared item could not be read: error=\(String(describing: error), privacy: .public)")
                found.unread += 1
            }
        }
        shared = found
        if found.unread > 0 {
            problem = found.unread == 1 ? "1 item could not be read and is left out." : "\(found.unread) items could not be read and are left out."
        }
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
            for (index, picture) in shared.pictures.enumerated().dropFirst(kept.count) {
                let upload: AttachmentUpload = AttachmentUpload(
                    name: "shared-\(index + 1).jpg", mediaType: "image/jpeg",
                    dataB64: picture.base64EncodedString())
                kept.append(try await client.uploadAttachment(upload, host: host, key: key))
            }
            let start: ChatStart = ChatStart(goal: goalOf(shared, note: note), attachments: kept, key: self.key)
            try await client.startChat(start, host: host, key: key)
            logger.info("shared to Dot: pictures=\(self.kept.count) addresses=\(self.shared.urls.count)")
            onDone()
        } catch let error as DaemonRequestError {
            logger.error("sharing to Dot failed: error=\(error.description, privacy: .public)")
            fail(sendingFailed(error))
        } catch {
            logger.error("sharing to Dot failed: error=\(String(describing: error), privacy: .public)")
            fail("Dot's answer could not be read. Open Dot to see whether the chat started.")
        }
    }

    private func fail(_ message: String) {
        problem = message
        sending = false
    }
}

/// What the sheet says of a send the daemon refused or did not get, by why.
func sendingFailed(_ error: DaemonRequestError) -> String {
    switch error {
    case .transport:
        "Your Mac did not answer. Check that Tailscale is on, then try again."
    case .status(_, let code, _) where code == 401 || code == 403:
        "This phone is no longer signed in to Dot. Open Dot and sign in with your Mac's code."
    case .status(_, let code, _) where code == 413 || code == 422:
        "Dot did not take it: it is too long. Share less of it, then try again."
    case .status(_, let code, _):
        "Dot refused it (\(code)). Try again, or open Dot."
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

/// The bytes of the picture `provider` shares, read on the main actor that owns the provider.
@MainActor
private func pictureData(_ provider: NSItemProvider) async throws -> Data {
    let item: NSSecureCoding? = try await provider.loadItem(forTypeIdentifier: UTType.image.identifier)
    switch item {
    case let url as URL:
        return try Data(contentsOf: url)
    case let data as Data:
        return data
    case let picture as UIImage:
        guard let png: Data = picture.pngData() else {
            throw CocoaError(.fileReadCorruptFile)
        }
        return png
    default:
        throw CocoaError(.fileReadCorruptFile)
    }
}

/// `data` as a JPEG at most `pictureSide` pixels on its longer side, upright. ImageIO makes the small picture without
/// opening the whole one (a 48 MP photo opened takes some 195 MB, past what a share extension may hold, which the
/// system ends without a word), off the main actor so that the sheet stays live; throws when it is no picture the
/// phone can read.
private func jpeg(from data: Data) async throws -> Data {
    try await Task.detached(priority: .userInitiated) {
        guard let source: CGImageSource = CGImageSourceCreateWithData(data as CFData, nil) else {
            throw CocoaError(.fileReadCorruptFile)
        }
        let options: [CFString: Any] = [
            kCGImageSourceThumbnailMaxPixelSize: pictureSide,
            kCGImageSourceCreateThumbnailFromImageAlways: true,
            kCGImageSourceCreateThumbnailWithTransform: true,
            kCGImageSourceShouldCacheImmediately: true,
        ]
        guard let small: CGImage = CGImageSourceCreateThumbnailAtIndex(source, 0, options as CFDictionary) else {
            throw CocoaError(.fileReadCorruptFile)
        }
        guard let jpeg: Data = UIImage(cgImage: small).jpegData(compressionQuality: 0.85) else {
            throw CocoaError(.fileWriteUnknown)
        }
        return jpeg
    }.value
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
