import Foundation

/// A file the owner selected for a message. The name and media type travel with the bytes, even when sent later.
public struct OutgoingAttachment: Codable, Sendable, Equatable {
    public static let maximumBytes: Int = 20 * 1024 * 1024
    public static let maximumCount: Int = 10

    public let name: String
    public let mediaType: String
    public let data: Data

    public init(name: String, mediaType: String, data: Data) {
        self.name = name
        self.mediaType = mediaType
        self.data = data
    }
}

/// Progress of the current request within a message's batch of attachments.
public struct AttachmentUploadProgress: Sendable, Equatable {
    public let number: Int
    public let count: Int
    public let percent: Int
}
