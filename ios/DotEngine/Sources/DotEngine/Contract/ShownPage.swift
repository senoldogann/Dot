import Foundation

/// A page capability received in the paired conversation. The event names a page, never a URL supplied by the run.
public struct ShownPage: Sendable, Equatable {
    public let id: String
    public let title: String

    public init?(event: EventRecord) {
        guard event.kind == "page_shown",
            let id: String = event.data["page_id"]?.string,
            id.wholeMatch(of: /page_[A-Za-z0-9_-]{24}/) != nil,
            let title: String = event.data["title"]?.string, !title.isEmpty
        else { return nil }
        self.id = id
        self.title = title
    }

    /// The daemon serves this unguessable address without a bearer key, with the page's sandbox in the response.
    public func url(on host: DaemonHost) -> URL {
        host.url(path: "/pages/\(id)")
    }
}
