import Foundation

/// A file the owner selected for a message. The name and media type travel with the content, even when sent later.
public struct OutgoingAttachment: Codable, Sendable, Equatable {
    /// A photo or a document, sent as base64 in a JSON body, which the daemon holds whole in memory (`agent/attachments.py`).
    public static let maximumBytes: Int = 20 * 1024 * 1024
    /// A video or a sound, sent as the raw body of the streamed upload and written to disk as it arrives (item 1933).
    public static let maximumMediaBytes: Int = 200 * 1024 * 1024
    public static let maximumCount: Int = 10

    public enum Content: Codable, Sendable, Equatable {
        /// Held in memory (a photo, a document).
        case bytes(Data)
        /// A file of the app's own, uploaded as it is.
        case file(URL)
        /// A file in the outbox's folder, by its name there: the folder's address changes when the app is updated.
        case kept(String)
    }

    public let name: String
    public let mediaType: String
    public let content: Content
    public let byteCount: Int

    public init(name: String, mediaType: String, data: Data) {
        self.name = name
        self.mediaType = mediaType
        self.content = .bytes(data)
        self.byteCount = data.count
    }

    public init(name: String, mediaType: String, file: URL, byteCount: Int) {
        self.name = name
        self.mediaType = mediaType
        self.content = .file(file)
        self.byteCount = byteCount
    }

    /// The same attachment kept in the outbox's folder as ``fileName``.
    func kept(as fileName: String) -> OutgoingAttachment {
        OutgoingAttachment(name: name, mediaType: mediaType, kept: fileName, byteCount: byteCount)
    }

    private init(name: String, mediaType: String, kept fileName: String, byteCount: Int) {
        self.name = name
        self.mediaType = mediaType
        self.content = .kept(fileName)
        self.byteCount = byteCount
    }
}

/// Progress of the current request within a message's batch of attachments.
public struct AttachmentUploadProgress: Sendable, Equatable {
    public let number: Int
    public let count: Int
    public let percent: Int
}
