import Foundation

// The web links and the files an answer names (backlog LF1), for the rows under it: a link becomes a row of its site, a
// file a chip that shows it. The files are found by the web's own rules (`static/js/prose.js`, items 195, 1077, 1088):
// a path or a name in a code span, and a document's name in the words; nothing inside a fenced code block counts.

/// A web link an answer names: its address and its site without a leading `www.` (`senoldogan.com`).
public struct AnswerLink: Sendable, Hashable {
    public let url: URL
    public let site: String

    public init(url: URL, site: String) {
        self.url = url
        self.site = site
    }
}

/// The most links and files an answer's rows show.
public let answerLinksShown: Int = 12
public let answerFilesShown: Int = 8
/// The longest path taken for a file (the web's `FILE_PATH_CHARS`).
let filePathCharacters: Int = 400

/// The site of `url` without a leading `www.`, lowercased; nil for an address with no host (pure).
public func bareSite(_ url: URL) -> String? {
    guard let host: String = url.host(percentEncoded: false)?.lowercased(), !host.isEmpty else { return nil }
    return host.hasPrefix("www.") ? String(host.dropFirst(4)) : host
}

/// The http(s) links of `markdown` in the order they first appear, each once, at most `answerLinksShown`; a link in a
/// fenced code block is code, not a link (pure).
public func answerLinks(_ markdown: String) -> [AnswerLink] {
    var links: [AnswerLink] = []
    var seen: Set<URL> = []
    let prose: String = outsideFences(markdown)
    for match in matches(of: webAddress, in: prose) {
        let written: String = trimmedAddress(match)
        guard let url: URL = URL(string: written), let site: String = bareSite(url), !seen.contains(url) else { continue }
        seen.insert(url)
        links.append(AnswerLink(url: url, site: site))
        if links.count == answerLinksShown { break }
    }
    return links
}

/// The files `markdown` names in the order they first appear, each once, at most `answerFilesShown`: a path under the
/// home folder or a file's name in a code span, a document's name in the words; none inside a fenced code block or a
/// web address (pure).
public func answerFiles(_ markdown: String) -> [String] {
    var files: [String] = []
    func take(_ path: String) {
        guard path.count <= filePathCharacters, !files.contains(path), files.count < answerFilesShown else { return }
        files.append(path)
    }
    let prose: String = outsideFences(markdown)
    for span in matches(of: codeSpan, in: prose, group: 1) where wholeMatch(homeFilePath, span) || wholeMatch(namedFile, span) {
        take(span)
    }
    // The words: with the code spans and the web addresses blanked out, so a name inside either is not taken twice.
    let words: String = blanked(blanked(prose, codeSpan), webAddress)
    for name in matches(of: fileWord, in: words, group: 1) { take(name) }
    return files
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
private let codeSpan: NSRegularExpression = regex(#"`([^`\n]+)`"#, caseless: false)
private let homeFilePath: NSRegularExpression = regex(#"^(~|/Users/[^/\s]+)/[^\n*?\[\]{}$<>|]*[^/\s*?\[\]{}$<>|]\.[A-Za-z0-9]{1,8}$"#, caseless: false)
private let namedFile: NSRegularExpression = regex(#"^(?:[\w-][\w.-]*/)*[\w-][\w.-]*\.(?:"# + codeSuffixes + ")$", caseless: true)
private let fileWord: NSRegularExpression = regex(
    #"(?<![\w./~@:-])((?:~?/(?:"# + spacedFolder + #")*|(?:[\w-][\w.-]*/)*)[\w-][\w.-]*\.(?:"# + wordSuffixes + "|"
        + wordSuffixes.uppercased() + #"))(?![\w/-])"#,
    caseless: false)
private let fence: NSRegularExpression = regex(#"^\s*(```|~~~)"#, caseless: false)

private func regex(_ pattern: String, caseless: Bool) -> NSRegularExpression {
    do {
        return try NSRegularExpression(pattern: pattern, options: caseless ? [.caseInsensitive] : [])
    } catch {
        preconditionFailure("an answer rule's pattern does not compile: \(pattern): \(error)")
    }
}

private func matches(of pattern: NSRegularExpression, in text: String) -> [String] {
    matches(of: pattern, in: text, group: 0)
}

private func matches(of pattern: NSRegularExpression, in text: String, group: Int) -> [String] {
    let whole: NSRange = NSRange(text.startIndex..., in: text)
    return pattern.matches(in: text, range: whole).compactMap { match in
        Range(match.range(at: group), in: text).map { String(text[$0]) }
    }
}

/// Whether `pattern` matches in `text` (the patterns that must take the whole text are anchored).
private func wholeMatch(_ pattern: NSRegularExpression, _ text: String) -> Bool {
    pattern.firstMatch(in: text, range: NSRange(text.startIndex..., in: text)) != nil
}

/// `text` with every match of `pattern` turned into spaces of its length, so what stands around it stays apart.
private func blanked(_ text: String, _ pattern: NSRegularExpression) -> String {
    let whole: NSRange = NSRange(text.startIndex..., in: text)
    return pattern.stringByReplacingMatches(in: text, range: whole, withTemplate: " ")
}

/// The lines of `markdown` outside its fenced code blocks; a fence left open runs to the end.
private func outsideFences(_ markdown: String) -> String {
    var inside: Bool = false
    var kept: [String] = []
    for line in markdown.components(separatedBy: "\n") {
        if wholeMatch(fence, line) {
            inside.toggle()
            continue
        }
        if !inside { kept.append(line) }
    }
    return kept.joined(separator: "\n")
}

/// An address as written, without the punctuation of the sentence it ends (`https://a.com.` → `https://a.com`).
private func trimmedAddress(_ written: String) -> String {
    var address: Substring = Substring(written)
    while let last: Character = address.last, ".,;:!?*_".contains(last) { address = address.dropLast() }
    return String(address)
}
