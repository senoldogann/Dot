import AVFoundation
import CoreTransferable
import DotEngine
import os
import SwiftUI
import UIKit
import UniformTypeIdentifiers

private let composerLogger: Logger = Logger(subsystem: "dev.dotagent.phone.next", category: "composer")

struct ComposerAttachment: Identifiable, Equatable {
    enum Content: Equatable {
        case ready(OutgoingAttachment)
        case needsTrim(MovieFile, name: String, bytes: Int)
    }

    let id: UUID
    let content: Content
    let preview: UIImage?
    let duration: Double?
    let icon: String

    var outgoing: OutgoingAttachment? {
        if case .ready(let file) = content { return file }
        return nil
    }

    var name: String {
        switch content {
        case .ready(let file): file.name
        case .needsTrim(_, let name, _): name
        }
    }

    var size: String {
        let bytes: Int
        switch content {
        case .ready(let file): bytes = file.data.count
        case .needsTrim(_, _, let count): bytes = count
        }
        return Int64(bytes).formatted(.byteCount(style: .file))
    }

    var length: String? {
        duration.map { Duration.seconds($0).formatted(.time(pattern: .minuteSecond)) }
    }

    static func photo(_ image: UIImage) throws -> ComposerAttachment {
        guard let data: Data = image.jpegData(compressionQuality: 0.8) else { throw ComposerMediaError.invalidImage }
        try ComposerMediaError.checkSize(data.count, name: "Photo.jpg")
        return ComposerAttachment(id: UUID(), content: .ready(OutgoingAttachment(name: "Photo.jpg", mediaType: "image/jpeg", data: data)), preview: image, duration: nil, icon: "photo")
    }

    static func document(_ url: URL) async throws -> ComposerAttachment {
        let scoped: Bool = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }
        let info: URLResourceValues = try url.resourceValues(forKeys: [.fileSizeKey, .contentTypeKey, .isRegularFileKey])
        let name: String = url.lastPathComponent
        guard info.isRegularFile == true, let bytes = info.fileSize, bytes > 0 else { throw ComposerMediaError.invalidDocument(name) }
        let type: UTType = info.contentType ?? .data
        if type.conforms(to: .movie) || type.conforms(to: .video) {
            return try await video(MovieFile.copy(url), name: name)
        }
        try ComposerMediaError.checkSize(bytes, name: name)
        let data: Data = try Data(contentsOf: url)
        try ComposerMediaError.checkSize(data.count, name: name)
        let file: OutgoingAttachment = OutgoingAttachment(name: name, mediaType: type.preferredMIMEType ?? "application/octet-stream", data: data)
        let icon: String = type.conforms(to: .audio) ? "waveform" : type.conforms(to: .pdf) ? "doc.richtext" : "doc"
        let thumbnail: UIImage? = type.conforms(to: .image) ? UIImage(data: data) : nil
        return ComposerAttachment(id: UUID(), content: .ready(file), preview: thumbnail, duration: nil, icon: icon)
    }

    static func video(_ source: MovieFile, name: String) async throws -> ComposerAttachment {
        defer { withExtendedLifetime(source) {} }
        let asset: AVURLAsset = AVURLAsset(url: source.url)
        let duration: Double = try await asset.load(.duration).seconds
        guard duration.isFinite, duration > 0,
            let export = AVAssetExportSession(asset: asset, presetName: AVAssetExportPreset1280x720)
        else { throw ComposerMediaError.invalidVideo(name) }
        let output: MovieFile = MovieFile(fileExtension: "mp4")
        export.shouldOptimizeForNetworkUse = true
        try await export.export(to: output.url, as: .mp4)
        let outputAsset: AVURLAsset = AVURLAsset(url: output.url)
        let generator: AVAssetImageGenerator = AVAssetImageGenerator(asset: outputAsset)
        generator.appliesPreferredTrackTransform = true
        generator.maximumSize = CGSize(width: 240, height: 240)
        let frame: CGImage = try await generator.image(at: .zero).image
        guard let bytes = try output.url.resourceValues(forKeys: [.fileSizeKey]).fileSize else {
            throw ComposerMediaError.invalidVideo(name)
        }
        let exportedName: String = URL(fileURLWithPath: name).deletingPathExtension().lastPathComponent + ".mp4"
        let content: Content
        if bytes > OutgoingAttachment.maximumBytes {
            content = .needsTrim(output, name: exportedName, bytes: bytes)
        } else {
            content = .ready(OutgoingAttachment(name: exportedName, mediaType: "video/mp4", data: try Data(contentsOf: output.url)))
        }
        return ComposerAttachment(id: UUID(), content: content, preview: UIImage(cgImage: frame), duration: duration, icon: "film")
    }
}

/// Owns a temporary media copy beyond the system picker's callback.
final class MovieFile: Sendable, Equatable, Transferable {
    let url: URL
    let originalName: String

    init(fileExtension: String) {
        let name: String = UUID().uuidString + "." + fileExtension
        url = FileManager.default.temporaryDirectory.appending(path: name)
        originalName = name
    }

    private init(source: URL) {
        url = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString).appendingPathExtension(source.pathExtension)
        originalName = source.lastPathComponent
    }

    static func copy(_ source: URL) throws -> MovieFile {
        let file: MovieFile = MovieFile(source: source)
        try FileManager.default.copyItem(at: source, to: file.url)
        return file
    }

    deinit {
        // A cancelled export may never have created its destination.
        if FileManager.default.fileExists(atPath: url.path) {
            do { try FileManager.default.removeItem(at: url) }
            catch { composerLogger.error("Temporary video cleanup failed: \(error.localizedDescription, privacy: .public)") }
        }
    }

    static func == (left: MovieFile, right: MovieFile) -> Bool { left.url == right.url }

    static var transferRepresentation: some TransferRepresentation {
        FileRepresentation(importedContentType: .movie) { try MovieFile.copy($0.file) }
    }
}

enum ComposerMediaError: LocalizedError {
    case invalidImage
    case invalidDocument(String)
    case invalidVideo(String)
    case tooLarge(String, Int)
    case tooMany

    static func checkSize(_ bytes: Int, name: String) throws {
        guard bytes <= OutgoingAttachment.maximumBytes else { throw Self.tooLarge(name, bytes) }
    }

    var errorDescription: String? {
        switch self {
        case .invalidImage:
            String(localized: "The image could not be read.", comment: "Composer attachment error")
        case .invalidDocument(let name):
            String(localized: "Could not read \(name). Choose a file.", comment: "Composer attachment error; argument is the file name")
        case .invalidVideo(let name):
            String(localized: "Could not prepare \(name) as a video.", comment: "Composer attachment error; argument is the file name")
        case .tooLarge(let name, let bytes):
            String(localized: "\(name) is \(Int64(bytes).formatted(.byteCount(style: .file))). The limit is 20 MiB per attachment.", comment: "Composer attachment error; arguments are file name and size")
        case .tooMany:
            String(localized: "At most 10 attachments can go with a message.", comment: "Composer attachment count limit")
        }
    }
}

struct VideoTrimmer: UIViewControllerRepresentable {
    let file: MovieFile
    let maximumDuration: Double
    let done: (Result<MovieFile?, Error>) -> Void

    func makeUIViewController(context: Context) -> UIVideoEditorController {
        let editor: UIVideoEditorController = UIVideoEditorController()
        editor.videoPath = file.url.path
        editor.videoQuality = .typeHigh
        editor.videoMaximumDuration = maximumDuration
        editor.delegate = context.coordinator
        return editor
    }

    func updateUIViewController(_ controller: UIVideoEditorController, context: Context) {}
    func makeCoordinator() -> Coordinator { Coordinator(done: done) }

    final class Coordinator: NSObject, UIVideoEditorControllerDelegate, UINavigationControllerDelegate {
        let done: (Result<MovieFile?, Error>) -> Void

        init(done: @escaping (Result<MovieFile?, Error>) -> Void) { self.done = done }

        func videoEditorController(_ editor: UIVideoEditorController, didSaveEditedVideoToPath path: String) {
            done(Result { try MovieFile.copy(URL(fileURLWithPath: path)) })
        }

        func videoEditorControllerDidCancel(_ editor: UIVideoEditorController) { done(.success(nil)) }
        func videoEditorController(_ editor: UIVideoEditorController, didFailWithError error: Error) { done(.failure(error)) }
    }
}

struct ComposerAttachmentChip: View {
    let attachment: ComposerAttachment
    let trim: () -> Void
    let remove: () -> Void

    @ScaledMetric(relativeTo: .caption) private var thumbnailSize: CGFloat = 44
    @ScaledMetric(relativeTo: .caption) private var nameWidth: CGFloat = 160

    var body: some View {
        HStack(spacing: Space.xs) {
            HStack(spacing: Space.xs) {
                if let preview = attachment.preview {
                    Image(uiImage: preview).resizable().scaledToFill()
                        .frame(width: thumbnailSize, height: thumbnailSize).clipShape(rounded(Radius.inner))
                        .accessibilityHidden(true)
                } else {
                    Image(systemName: attachment.icon).foregroundStyle(Tone.secondary).accessibilityHidden(true)
                }
                VStack(alignment: .leading, spacing: Space.xxs) {
                    Text(verbatim: attachment.name).textRole(.footnote).lineLimit(1).truncationMode(.middle)
                        .frame(maxWidth: nameWidth, alignment: .leading)
                    if let length = attachment.length {
                        Text(verbatim: "\(length) · \(attachment.size)").textRole(.label).foregroundStyle(Tone.secondary)
                            .accessibilityIdentifier("composer.videoDuration")
                    } else {
                        Text(verbatim: attachment.size).textRole(.label).foregroundStyle(Tone.secondary)
                    }
                    if attachment.outgoing == nil {
                        Text(String(localized: "Over the 20 MiB limit", comment: "On a video chip when the exported video is still too big"))
                            .textRole(.label).foregroundStyle(Tone.failure)
                    }
                }
            }
            if case .needsTrim(let file, _, _) = attachment.content, UIVideoEditorController.canEditVideo(atPath: file.url.path) {
                Button(String(localized: "Trim video", comment: "Composer button: opens the video trimmer"), action: trim)
                    .textRole(.footnote).frame(minHeight: Metric.hit)
            }
            Button(action: remove) {
                Image(systemName: "xmark.circle.fill").foregroundStyle(Tone.secondary).frame(width: Metric.hit, height: Metric.hit)
            }
            .accessibilityLabel(String(localized: "Remove \(attachment.name)", comment: "VoiceOver: removes an attachment from the message"))
        }
        .buttonStyle(.plain)
        .padding(.leading, Space.xs)
        .padding(.vertical, Space.xxs)
        .background(Tone.nested, in: rounded(Radius.inner))
    }
}

func attachmentMessage() -> String {
    String(localized: "See the attachments.", comment: "Message text for a send with only attached files")
}

func messageStorageFailure() -> String {
    String(localized: "The message could not be saved on this phone. Try again.", comment: "Shown when the outbox could not keep a message or attachment")
}

struct AttachmentTransferLine: View {
    let key: String
    let progress: AttachmentUploadProgress
    @Environment(AppModel.self) private var model: AppModel

    var body: some View {
        HStack(spacing: Space.xs) {
            ProgressView(value: Double(progress.percent), total: 100) {
                Text(String(localized: "Uploading attachment \(progress.number) of \(progress.count)", comment: "Message status during an attachment upload; arguments are the ordinal and file count"))
                    .textRole(.label)
            }
            Button(String(localized: "Cancel upload", comment: "Stops the current attachment upload and keeps the message available for retry")) {
                model.cancelUpload(key)
            }
            .textRole(.label)
            .frame(minHeight: Metric.hit)
        }
    }
}
