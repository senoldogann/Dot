import AVFoundation
import CoreTransferable
import DotEngine
import os
import SwiftUI
import UIKit
import UniformTypeIdentifiers

private let composerLogger: Logger = Logger(subsystem: "dev.dotagent.phone.next", category: "composer")

/// How far a video's re-encoding has got (item 1992), for its tile's ring.
@MainActor @Observable final class ExportProgress: Equatable {
    var fraction: Double = 0
    nonisolated static func == (left: ExportProgress, right: ExportProgress) -> Bool { left === right }
}

struct ComposerAttachment: Identifiable, Equatable {
    enum Content: Equatable {
        case ready(OutgoingAttachment)
        case needsTrim(MovieFile, name: String, bytes: Int)
        /// A video being re-encoded: its tile is on show at once, with the first frame and a ring that fills.
        case exporting(name: String, ExportProgress)
    }

    let id: UUID
    let content: Content
    let preview: UIImage?
    let duration: Double?
    let icon: String
    /// The temporary file a ready video or sound is sent from: it is removed when this is let go, so the chip holds it.
    var holds: MovieFile? = nil

    var outgoing: OutgoingAttachment? {
        if case .ready(let file) = content { return file }
        return nil
    }

    var name: String {
        switch content {
        case .ready(let file): file.name
        case .needsTrim(_, let name, _): name
        case .exporting(let name, _): name
        }
    }

    /// The ring of a video still being prepared; `nil` once it is ready.
    var exportProgress: ExportProgress? {
        if case .exporting(_, let progress) = content { return progress }
        return nil
    }

    var size: String {
        let bytes: Int
        switch content {
        case .ready(let file): bytes = file.byteCount
        case .needsTrim(_, _, let count): bytes = count
        case .exporting: return ""
        }
        return Int64(bytes).formatted(.byteCount(style: .file))
    }

    var length: String? {
        duration.map { Duration.seconds($0).formatted(.time(pattern: .minuteSecond)) }
    }

    static func photo(_ image: UIImage) throws -> ComposerAttachment {
        // A picture goes at the upload size (a camera's 12 MP photo as about 1,600 px, not as itself), with a preview of the
        // composer's size (item 1926).
        let sized: UIImage = ImageSizing.fitted(image, longest: ImageSizing.upload)
        guard let data: Data = sized.jpegData(compressionQuality: ImageSizing.quality) else { throw ComposerMediaError.invalidImage }
        try ComposerMediaError.checkSize(data.count, name: "Photo.jpg")
        return ComposerAttachment(
            id: UUID(), content: .ready(OutgoingAttachment(name: "Photo.jpg", mediaType: "image/jpeg", data: data)), preview: ImageSizing.fitted(sized, longest: ImageSizing.preview),
            duration: nil, icon: "photo")
    }

    static func document(_ url: URL) async throws -> ComposerAttachment {
        let scoped: Bool = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }
        let info: URLResourceValues = try url.resourceValues(forKeys: [.fileSizeKey, .contentTypeKey, .isRegularFileKey])
        let name: String = url.lastPathComponent
        guard info.isRegularFile == true, let bytes = info.fileSize, bytes > 0 else { throw ComposerMediaError.invalidDocument(name) }
        let type: UTType = info.contentType ?? .data
        if type.conforms(to: .movie) || type.conforms(to: .video) {
            let movie: MovieFile = try MovieFile.copy(url)
            return try await video(movie, name: name, id: UUID(), progress: nil)
        }
        if type.conforms(to: .audio) {
            // A sound goes as it is, as a file (item 1933): the daemon keeps it for watch_video up to the video's limit.
            guard bytes <= OutgoingAttachment.maximumMediaBytes else { throw ComposerMediaError.tooLargeMedia(name, bytes) }
            let copy: MovieFile = try MovieFile.copy(url)
            let sound: OutgoingAttachment = OutgoingAttachment(name: name, mediaType: type.preferredMIMEType ?? "audio/mpeg", file: copy.url, byteCount: bytes)
            return ComposerAttachment(id: UUID(), content: .ready(sound), preview: nil, duration: nil, icon: "waveform", holds: copy)
        }
        try ComposerMediaError.checkSize(bytes, name: name)
        let data: Data = try Data(contentsOf: url)
        try ComposerMediaError.checkSize(data.count, name: name)
        let file: OutgoingAttachment = OutgoingAttachment(name: name, mediaType: type.preferredMIMEType ?? "application/octet-stream", data: data)
        let icon: String = type.conforms(to: .audio) ? "waveform" : type.conforms(to: .pdf) ? "doc.richtext" : "doc"
        let thumbnail: UIImage? = type.conforms(to: .image) ? UIImage(data: data) : nil
        return ComposerAttachment(id: UUID(), content: .ready(file), preview: thumbnail, duration: nil, icon: icon)
    }

    /// The tile of a video on show before it is re-encoded: its first frame and length, read from the source.
    @MainActor static func exportingVideo(_ source: MovieFile, name: String, id: UUID) async throws -> ComposerAttachment {
        let asset: AVURLAsset = AVURLAsset(url: source.url)
        let duration: Double = try await asset.load(.duration).seconds
        guard duration.isFinite, duration > 0 else { throw ComposerMediaError.invalidVideo(name) }
        let generator: AVAssetImageGenerator = AVAssetImageGenerator(asset: asset)
        generator.appliesPreferredTrackTransform = true
        generator.maximumSize = CGSize(width: 240, height: 240)
        let frame: CGImage = try await generator.image(at: .zero).image
        let shown: String = URL(fileURLWithPath: name).deletingPathExtension().lastPathComponent + ".mp4"
        return ComposerAttachment(
            id: id, content: .exporting(name: shown, ExportProgress()), preview: UIImage(cgImage: frame), duration: duration, icon: "film")
    }

    /// A video made ready to send: re-encoded to the target bitrate, as ``id``'s tile (the one ``exportingVideo`` showed, whose ring
    /// is ``progress``).
    static func video(_ source: MovieFile, name: String, id: UUID, progress: ExportProgress?) async throws -> ComposerAttachment {
        defer { withExtendedLifetime(source) {} }
        let asset: AVURLAsset = AVURLAsset(url: source.url)
        let duration: Double = try await asset.load(.duration).seconds
        guard duration.isFinite, duration > 0 else { throw ComposerMediaError.invalidVideo(name) }
        let output: MovieFile = MovieFile(fileExtension: "mp4")
        // Re-encoded for a target bitrate (item 1992, `VideoTarget`), not to a preset: a minute is about 14 MB.
        let ring: Ring = Ring(progress)
        do {
            try await VideoTranscoder.export(from: source.url, to: output.url, progress: { ring.set($0) })
        } catch is CancellationError {
            throw CancellationError()
        } catch {
            composerLogger.error("Video export failed: \(String(describing: error), privacy: .public)")
            throw ComposerMediaError.invalidVideo(name)
        }
        let outputAsset: AVURLAsset = AVURLAsset(url: output.url)
        let generator: AVAssetImageGenerator = AVAssetImageGenerator(asset: outputAsset)
        generator.appliesPreferredTrackTransform = true
        generator.maximumSize = CGSize(width: 240, height: 240)
        let frame: CGImage = try await generator.image(at: .zero).image
        guard let bytes = try output.url.resourceValues(forKeys: [.fileSizeKey]).fileSize else {
            throw ComposerMediaError.invalidVideo(name)
        }
        let exportedName: String = URL(fileURLWithPath: name).deletingPathExtension().lastPathComponent + ".mp4"
        guard bytes > OutgoingAttachment.maximumMediaBytes else {
            let video: OutgoingAttachment = OutgoingAttachment(name: exportedName, mediaType: "video/mp4", file: output.url, byteCount: bytes)
            return ComposerAttachment(id: id, content: .ready(video), preview: UIImage(cgImage: frame), duration: duration, icon: "film", holds: output)
        }
        return ComposerAttachment(id: id, content: .needsTrim(output, name: exportedName, bytes: bytes), preview: UIImage(cgImage: frame), duration: duration, icon: "film")
    }
}

/// Hands an export's progress to the main actor, a step of 2 % at a time: a frame at 30 a second is not a change of the ring.
private final class Ring: @unchecked Sendable {
    private let progress: ExportProgress?
    private let lock: NSLock = NSLock()
    private var last: Double = 0

    init(_ progress: ExportProgress?) { self.progress = progress }

    func set(_ fraction: Double) {
        guard let progress else { return }
        let step: Bool = lock.withLock {
            guard fraction - last >= 0.02 else { return false }
            last = fraction
            return true
        }
        if step { Task { @MainActor in progress.fraction = fraction } }
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
    case tooLargeMedia(String, Int)
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
        case .tooLargeMedia(let name, let bytes):
            String(localized: "\(name) is \(Int64(bytes).formatted(.byteCount(style: .file))). The limit is 200 MiB for a video or a sound.", comment: "Composer attachment error; arguments are file name and size")
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

    @ScaledMetric(relativeTo: .caption) private var nameWidth: CGFloat = 160

    var body: some View {
        if attachment.preview != nil {
            tile
        } else {
            fileChip
        }
    }

    /// A photo or a video: its picture as a tile, with the play glyph, the length and the ring over it, and the × at its corner
    /// (item 1992); a video still too big says so under it, with the way to trim it.
    private var tile: some View {
        VStack(alignment: .leading, spacing: Space.xxs) {
            MediaTile(
                preview: attachment.preview, duration: attachment.duration, playable: attachment.icon == "film",
                icon: attachment.icon, progress: attachment.exportProgress.map(\.fraction))
                .overlay(alignment: .topTrailing) {
                    Button(action: remove) {
                        Image(systemName: "xmark.circle.fill")
                            .symbolRenderingMode(.palette).foregroundStyle(.white, .black.opacity(0.6))
                            .frame(width: Metric.hit, height: Metric.hit)
                    }
                    .accessibilityLabel(String(localized: "Remove \(attachment.name)", comment: "VoiceOver: removes an attachment from the message"))
                    .accessibilityIdentifier("composer.remove")
                }
                .accessibilityElement(children: .contain)
                .accessibilityLabel(tileWords)
                .accessibilityIdentifier(attachment.icon == "film" ? "composer.videoTile" : "composer.photoTile")
            if attachment.outgoing == nil && attachment.exportProgress == nil {
                Text(String(localized: "Over the 200 MiB limit", comment: "On a video chip when the exported video is still too big"))
                    .textRole(.label).foregroundStyle(Tone.failure)
                if case .needsTrim(let file, _, _) = attachment.content, UIVideoEditorController.canEditVideo(atPath: file.url.path) {
                    Button(String(localized: "Trim video", comment: "Composer button: opens the video trimmer"), action: trim)
                        .textRole(.footnote).frame(minHeight: Metric.hit)
                }
            }
            if attachment.icon == "film" {
                Text(verbatim: attachment.size).textRole(.label).foregroundStyle(Tone.secondary)
                    .accessibilityIdentifier("composer.videoSize")
            }
        }
        .buttonStyle(.plain)
    }

    /// What VoiceOver says of the tile: the kind, its name, and a video's length.
    private var tileWords: String {
        if attachment.icon == "film", let length = attachment.length {
            return String(localized: "Video \(attachment.name), \(length)", comment: "VoiceOver: a video in the composer; the arguments are its name and its length")
        }
        return attachment.icon == "film"
            ? String(localized: "Video \(attachment.name)", comment: "VoiceOver: a video in the composer that is being prepared; the argument is its name")
            : String(localized: "Photo \(attachment.name)", comment: "VoiceOver: a photo the owner's message carried; the argument is its name")
    }

    private var fileChip: some View {
        HStack(spacing: Space.xs) {
            Image(systemName: attachment.icon).foregroundStyle(Tone.secondary).accessibilityHidden(true)
            VStack(alignment: .leading, spacing: Space.xxs) {
                Text(verbatim: attachment.name).textRole(.footnote).lineLimit(1).truncationMode(.middle)
                    .frame(maxWidth: nameWidth, alignment: .leading)
                Text(verbatim: attachment.size).textRole(.label).foregroundStyle(Tone.secondary)
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
