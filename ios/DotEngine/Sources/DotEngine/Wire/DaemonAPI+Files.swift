import Foundation

// The files an answer names, as the web's file panel shows them (`routes_files.py`, filepanel.js): what the Mac shows of
// a file, its pictures and pages, and a document opened in its application on the Mac (backlog LF1).

/// The size of a PDF's page in points, turned as the page says.
public struct FilePageSize: Codable, Sendable, Hashable {
    public let width: Double
    public let height: Double

    public init(width: Double, height: Double) {
        self.width = width
        self.height = height
    }
}

/// What the Mac shows of a file (`ShownFile.preview`): a PDF's pages, an image, a text of at most 512 KB, or a kind
/// the phone shows by its facts alone (a table, an Office file) and a file the Mac does not show, with why.
public enum FilePreview: Sendable, Hashable, Decodable {
    /// The pages' sizes and the widths in pixels a page is drawn at (`/api/files/page`).
    case pdf(pages: [FilePageSize], widths: [Int])
    case image
    /// `cut` when the file holds more than the text.
    case text(String, cut: Bool)
    /// A kind the Mac draws for its own panel (`table`, `document`, `workbook`, `presentation`).
    case drawn(kind: String)
    case unshown(reason: String)

    private enum Keys: String, CodingKey {
        case kind
        case pages
        case widths
        case text
        case cut
        case reason
    }

    public init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: Keys.self)
        let kind: String = try container.decode(String.self, forKey: .kind)
        switch kind {
        case "pdf":
            self = .pdf(pages: try container.decode([FilePageSize].self, forKey: .pages), widths: try container.decode([Int].self, forKey: .widths))
        case "image":
            self = .image
        case "text":
            self = .text(try container.decode(String.self, forKey: .text), cut: try container.decode(Bool.self, forKey: .cut))
        case "unshown":
            self = .unshown(reason: try container.decode(String.self, forKey: .reason))
        default:
            self = .drawn(kind: kind)
        }
    }
}

/// `GET /api/files/preview`: a file named by its path, where it is (its links followed), its name, its size in bytes,
/// when it changed (seconds since the epoch), whether it may open in its application on the Mac, and what is shown of it.
public struct ShownFile: Decodable, Sendable, Hashable {
    public let path: String
    public let name: String
    public let size: Int
    public let modified: Double
    public let opens: Bool
    public let preview: FilePreview

    public init(path: String, name: String, size: Int, modified: Double, opens: Bool, preview: FilePreview) {
        self.path = path
        self.name = name
        self.size = size
        self.modified = modified
        self.opens = opens
        self.preview = preview
    }
}

private struct OpenFileBody: Encodable { let path: String }

/// The width a PDF page is fetched at for the phone: the widest the Mac draws up to 1200 pixels (a phone's screen at 3x
/// is about 1200 wide), the narrowest when none is that small (pure).
public func phonePageWidth(_ widths: [Int]) -> Int? {
    widths.filter { $0 <= 1200 }.max() ?? widths.min()
}

extension DaemonAPI {
    /// What the Mac shows of the file at `path`: absolute, under `~/`, or relative to the workspace. 404 when there is
    /// no such file; 422 when it is outside the places the panel shows.
    public func shownFile(path: String) async throws(DaemonRequestError) -> ShownFile {
        try await get("/api/files/preview", query: [URLQueryItem(name: "path", value: path)])
    }

    /// The bytes of the image at `path`.
    public func fileImage(path: String) async throws(DaemonRequestError) -> Data {
        try await bytes("/api/files/image", query: [URLQueryItem(name: "path", value: path)])
    }

    /// Page `number` (from 1) of the PDF at `path` as a PNG `width` pixels wide, one of the preview's `widths`.
    public func filePage(path: String, number: Int, width: Int) async throws(DaemonRequestError) -> Data {
        try await bytes(
            "/api/files/page",
            query: [URLQueryItem(name: "path", value: path), URLQueryItem(name: "number", value: String(number)), URLQueryItem(name: "width", value: String(width))])
    }

    /// Opens the document at `path` in its application on the Mac; 422 for a file that opens in none.
    public func openFileOnMac(path: String) async throws(DaemonRequestError) {
        try await sendVoid("POST", "/api/files/open", body: Self.json(OpenFileBody(path: path)))
    }

    private func bytes(_ path: String, query: [URLQueryItem]) async throws(DaemonRequestError) -> Data {
        var built: URLRequest = request(method: "GET", path: path, query: query, body: nil)
        built.setValue("*/*", forHTTPHeaderField: "Accept")
        built.timeoutInterval = 30
        return try await data(built, path: path)
    }
}
