import Foundation

// The web links and the files an answer's words name (backlog LF1, made inline 2026-11-26): a link shows inline as its
// site's mark and a short form, a file as its kind's symbol and its name. The files are found by the web's own rules
// (`static/js/prose.js`, items 195, 1077, 1088): a path or a name in a code span, and a document's name in the words.

/// The longest path taken for a file (the web's `FILE_PATH_CHARS`).
let filePathCharacters: Int = 400

/// The site of `url` without a leading `www.`, lowercased; nil for an address with no host (pure).
public func bareSite(_ url: URL) -> String? {
    guard let host: String = url.host(percentEncoded: false)?.lowercased(), !host.isEmpty else { return nil }
    return host.hasPrefix("www.") ? String(host.dropFirst(4)) : host
}

/// How a link shows inline in an answer's words: a GitHub pull request or issue as its number (`#1012`) with the
/// GitHub mark, a GitHub repository as `owner/repo`, any other page as its bare site with the site's own icon.
public struct InlineLinkLabel: Sendable, Equatable {
    public enum Mark: Sendable, Equatable {
        case github
        case site(String)
    }

    public let text: String
    public let mark: Mark

    public init(text: String, mark: Mark) {
        self.text = text
        self.mark = mark
    }
}

/// The short form of `url` for an inline link, nil for an address with no host (pure).
public func inlineLinkLabel(_ url: URL) -> InlineLinkLabel? {
    guard let site: String = bareSite(url) else { return nil }
    let parts: [String] = url.pathComponents.filter { $0 != "/" }
    if site == "github.com" {
        if parts.count >= 4, ["pull", "issues"].contains(parts[2]), Int(parts[3]) != nil { return InlineLinkLabel(text: "#" + parts[3], mark: .github) }
        if parts.count == 2 { return InlineLinkLabel(text: parts[0] + "/" + parts[1], mark: .github) }
        return InlineLinkLabel(text: site, mark: .github)
    }
    return InlineLinkLabel(text: site, mark: .site(site))
}

/// Whether a code span names a file the way the web shows one (`~/Desktop/report.pdf`, `notes/plan.md`) (pure).
public func codeNamesFile(_ span: String) -> Bool {
    span.count <= filePathCharacters && (wholeMatch(homeFilePath, span) || wholeMatch(namedFile, span))
}

/// Where the words `text` name a document (`summary.docx`, `~/Desktop/brief.md`), by the web's rule for words (pure).
public func fileWordRanges(_ text: String) -> [Range<String.Index>] {
    ranges(of: fileWord, in: text, group: 1).filter { text[$0].count <= filePathCharacters }
}

/// Where the words `text` hold a bare web address, without the punctuation of the sentence it ends (pure).
public func webAddressRanges(_ text: String) -> [Range<String.Index>] {
    ranges(of: webAddress, in: text, group: 0).map { range in
        let written: String = String(text[range])
        let trimmed: String = trimmedAddress(written)
        return range.lowerBound..<text.index(range.lowerBound, offsetBy: trimmed.count)
    }
}

/// What kind of file a name is, by its suffix, for its icon (pure).
public enum AnswerFileKind: Sendable, Equatable {
    case image
    case pdf
    case code
    case text
    case table
    case document
    case slides
    case page
    case other
}

public func answerFileKind(_ name: String) -> AnswerFileKind {
    let suffix: String = (name as NSString).pathExtension.lowercased()
    switch suffix {
    case "png", "jpg", "jpeg", "gif", "webp", "heic", "svg", "bmp", "tiff": return .image
    case "pdf": return .pdf
    case "py", "js", "mjs", "ts", "tsx", "jsx", "css", "sh", "rs", "go", "swift", "json", "yaml", "yml", "toml": return .code
    case "md", "markdown", "txt", "log": return .text
    case "csv", "tsv", "xls", "xlsx": return .table
    case "doc", "docx": return .document
    case "ppt", "pptx": return .slides
    case "html", "htm": return .page
    default: return .other
    }
}

// MARK: The rules

private let codeSuffixes: String =
    "md|markdown|html?|pdf|txt|csv|tsv|docx?|xlsx?|pptx?|json|ya?ml|toml|png|jpe?g|gif|webp|svg|py|js|mjs|ts|tsx|jsx|css|sh|rs|go|swift|log"
private let wordSuffixes: String = "md|markdown|html?|pdf|txt|csv|docx|xlsx|pptx"
/// A folder of a path from the home folder may have words of capitals after spaces, as the Mac's own do
/// (`Application Support`).
private let spacedFolder: String = #"[\w-][\w.-]*(?: [A-Z][\w.-]*)*/"#

private let webAddress: NSRegularExpression = regex(#"https?://[^\s<>()\[\]{}"'`|]+"#, caseless: true)
private let homeFilePath: NSRegularExpression = regex(#"^(~|/Users/[^/\s]+)/[^\n*?\[\]{}$<>|]*[^/\s*?\[\]{}$<>|]\.[A-Za-z0-9]{1,8}$"#, caseless: false)
private let namedFile: NSRegularExpression = regex(#"^(?:[\w-][\w.-]*/)*[\w-][\w.-]*\.(?:"# + codeSuffixes + ")$", caseless: true)
private let fileWord: NSRegularExpression = regex(
    #"(?<![\w./~@:-])((?:~?/(?:"# + spacedFolder + #")*|(?:[\w-][\w.-]*/)*)[\w-][\w.-]*\.(?:"# + wordSuffixes + "|"
        + wordSuffixes.uppercased() + #"))(?![\w/-])"#,
    caseless: false)

private func regex(_ pattern: String, caseless: Bool) -> NSRegularExpression {
    do {
        return try NSRegularExpression(pattern: pattern, options: caseless ? [.caseInsensitive] : [])
    } catch {
        preconditionFailure("an answer rule's pattern does not compile: \(pattern): \(error)")
    }
}

private func ranges(of pattern: NSRegularExpression, in text: String, group: Int) -> [Range<String.Index>] {
    pattern.matches(in: text, range: NSRange(text.startIndex..., in: text)).compactMap { Range($0.range(at: group), in: text) }
}

/// Whether `pattern` matches in `text` (the patterns that must take the whole text are anchored).
private func wholeMatch(_ pattern: NSRegularExpression, _ text: String) -> Bool {
    pattern.firstMatch(in: text, range: NSRange(text.startIndex..., in: text)) != nil
}

/// An address as written, without the punctuation of the sentence it ends (`https://a.com.` → `https://a.com`).
private func trimmedAddress(_ written: String) -> String {
    var address: Substring = Substring(written)
    while let last: Character = address.last, ".,;:!?*_".contains(last) { address = address.dropLast() }
    return String(address)
}
