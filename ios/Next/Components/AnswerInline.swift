import DotEngine
import SwiftUI
import UIKit

// The links and files an answer names, where its words name them (the owner, 2026-11-26): a link is its site's mark
// and a short form (`#1012` with the GitHub mark, `senoldogan.com` with the site's icon, or the words it was written
// with), a file its kind's symbol and its name; a tap opens the page in Safari's in-app view or the file's preview
// sheet (`AnswerBody`). Nothing is listed under the answer.

/// The mark drawn before a reference in an answer's words.
enum ReferenceMark: Hashable, Sendable {
    case github
    /// A site's own icon, by the address it is fetched for (`SiteIcons`).
    case site(URL)
    /// A file's kind's symbol.
    case file(String)
}

enum ReferenceMarkKey: AttributedStringKey {
    typealias Value = ReferenceMark
    static let name: String = "dotReferenceMark"
}

extension AttributeScopes {
    struct DotAttributes: AttributeScope {
        let referenceMark: ReferenceMarkKey
        let swiftUI: SwiftUIAttributes
    }

    var dot: DotAttributes.Type { DotAttributes.self }
}

extension AttributeDynamicLookup {
    subscript<T: AttributedStringKey>(dynamicMember keyPath: KeyPath<AttributeScopes.DotAttributes, T>) -> T {
        self[T.self]
    }
}

/// The address a file's reference opens: the app's own scheme, which `AnswerBody` turns into the file's sheet.
let fileReferenceScheme: String = "dotfile"

func fileReferenceURL(_ path: String) -> URL? {
    var parts: URLComponents = URLComponents()
    parts.scheme = fileReferenceScheme
    parts.host = "open"
    parts.queryItems = [URLQueryItem(name: "path", value: path)]
    return parts.url
}

/// The path a file reference's address names, nil for any other address and for a path the web's rules would not
/// take for a file (a link the answer's own markdown wrote with this scheme opens nothing it could not name).
func fileReferencePath(_ url: URL) -> String? {
    guard url.scheme == fileReferenceScheme,
        let path: String = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems?.first(where: { $0.name == "path" })?.value,
        codeNamesFile(path) || fileWordRanges(path).first == path.startIndex..<path.endIndex
    else { return nil }
    return path
}

extension EnvironmentValues {
    /// Whether the answer drawn is whole, so its links' site icons may be asked for (`SiteIconBook`).
    @Entry var fetchesSiteIcons: Bool = false
}

/// Inline markdown of an answer with its references marked: a link (written or bare) shows its short form, or the
/// words it was written with, after its mark; a file named in a code span or in the words shows its name after its
/// kind's symbol, as a link to its sheet. Links are underlined, in the ink (review S3). Throws when the markdown does
/// not parse.
func answerInline(markdown: String) throws -> AttributedString {
    var text: AttributedString = try answerText(markdown: markdown)
    markBareAddresses(&text)
    markFiles(&text)
    markLinks(&text)
    return text
}

/// A bare `https://…` in the words becomes a link (the parser leaves it as words).
private func markBareAddresses(_ text: inout AttributedString) {
    for run in text.runs.reversed() where run.link == nil && run.inlinePresentationIntent?.contains(.code) != true {
        let words: String = String(text[run.range].characters)
        for range in webAddressRanges(words).reversed() {
            guard let url: URL = URL(string: String(words[range])), let span = attributedRange(range, in: words, run: run.range, of: text) else { continue }
            text[span].link = url
            text[span].underlineStyle = .single
        }
    }
}

/// A file named in a code span or in the words becomes its name as a link to its sheet.
private func markFiles(_ text: inout AttributedString) {
    for run in text.runs.reversed() where run.link == nil {
        let words: String = String(text[run.range].characters)
        let isCode: Bool = run.inlinePresentationIntent?.contains(.code) == true
        let found: [Range<String.Index>] = isCode ? (codeNamesFile(words) ? [words.startIndex..<words.endIndex] : []) : fileWordRanges(words)
        for range in found.reversed() {
            let path: String = String(words[range])
            guard let url: URL = fileReferenceURL(path), let span = attributedRange(range, in: words, run: run.range, of: text) else { continue }
            var name: AttributedString = AttributedString((path as NSString).lastPathComponent)
            name.link = url
            name.underlineStyle = .single
            name.referenceMark = .file(fileSymbol(answerFileKind(path)))
            text.replaceSubrange(span, with: name)
        }
    }
}

/// A web link gets its mark; a bare address (its words are the address) shows its short form instead.
private func markLinks(_ text: inout AttributedString) {
    for run in text.runs.reversed() {
        guard let url: URL = run.link, url.scheme == "http" || url.scheme == "https", let label: InlineLinkLabel = inlineLinkLabel(url) else { continue }
        let words: String = String(text[run.range].characters)
        let mark: ReferenceMark = label.mark == .github ? .github : .site(url)
        if words == url.absoluteString || words.hasPrefix("http://") || words.hasPrefix("https://") {
            var short: AttributedString = AttributedString(label.text, attributes: run.attributes)
            short.link = url
            short.underlineStyle = .single
            short.referenceMark = mark
            text.replaceSubrange(run.range, with: short)
        } else {
            text[run.range].referenceMark = mark
        }
    }
}

/// The part of `text` that `range` of the run's words `words` is.
private func attributedRange(_ range: Range<String.Index>, in words: String, run: Range<AttributedString.Index>, of text: AttributedString)
    -> Range<AttributedString.Index>?
{
    let start: Int = words.distance(from: words.startIndex, to: range.lowerBound)
    let length: Int = words.distance(from: range.lowerBound, to: range.upperBound)
    let lower: AttributedString.Index = text.characters.index(run.lowerBound, offsetBy: start)
    let upper: AttributedString.Index = text.characters.index(lower, offsetBy: length)
    return lower..<upper
}

/// The words of an answer as one `Text`, each reference's mark drawn before it at the size of the words; a site's icon
/// shows once fetched (`SiteIconBook`), a globe until then.
struct ReferenceText: View {
    let text: AttributedString
    let role: TypeRole
    @Environment(AppModel.self) private var model: AppModel
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize: DynamicTypeSize
    @Environment(\.fetchesSiteIcons) private var fetchesSiteIcons: Bool
    private var icons: SiteIconBook { SiteIconBook.shared }

    var body: some View {
        composed.textRole(role)
    }

    /// The runs joined into one `Text` by interpolation (`Text + Text` is deprecated since iOS 26); the key is built
    /// and looked up in a table the app does not have (`Verbatim`), so the String Catalog takes no key of it and it
    /// shows as it is. A reference's words may be several runs (a bold word inside a link, the colour steps of words
    /// fading in): its mark is drawn once, before the first of them (`markStarts`).
    private var composed: Text {
        var parts: [Text] = []
        for (run, mark) in zip(text.runs, markStarts(text)) {
            if let mark {
                parts.append(markText(mark))
                parts.append(Text(verbatim: "\u{2009}"))
            }
            parts.append(Text(AttributedString(text[run.range])))
        }
        var joined: LocalizedStringKey.StringInterpolation = LocalizedStringKey.StringInterpolation(literalCapacity: 0, interpolationCount: parts.count)
        for part in parts { joined.appendInterpolation(part) }
        return Text(LocalizedStringKey(stringInterpolation: joined), tableName: "Verbatim")
    }

    /// The words' size in points at the app's own text size (the environment's, which Settings › Appearance may pin),
    /// as `TypeRoleModifier` reads it.
    private var side: CGFloat {
        let traits: UITraitCollection = UITraitCollection(preferredContentSizeCategory: UIContentSizeCategory(dynamicTypeSize))
        return UIFontMetrics(forTextStyle: role.uiTextStyle).scaledValue(for: role.points.size, compatibleWith: traits)
    }

    /// A reference's mark, decorative to VoiceOver (the words after it say what it is).
    private func markText(_ mark: ReferenceMark) -> Text {
        switch mark {
        case .file(let symbol):
            return Text(decorativeSymbol(symbol, side: side)).foregroundStyle(Tone.secondary).baselineOffset(-side * 0.12)
        case .github:
            return Text(decorativeAsset("plugin-github-icon", side: side)).foregroundStyle(Tone.text).baselineOffset(-side * 0.14)
        case .site(let url):
            if let icon: UIImage = icons.icon(for: url, side: side, fetches: fetchesSiteIcons && model.engine != nil), let drawn: CGImage = icon.cgImage {
                return Text(Image(decorative: drawn, scale: icon.scale)).baselineOffset(-side * 0.16)
            }
            return Text(decorativeSymbol("globe", side: side)).foregroundStyle(Tone.secondary).baselineOffset(-side * 0.12)
        }
    }
}

/// For each run of `text`, the mark drawn before it: a reference's mark before the first run of its words, nil before
/// the others. Neighbouring runs with the same mark and the same link are one reference. Pure.
func markStarts(_ text: AttributedString) -> [ReferenceMark?] {
    var starts: [ReferenceMark?] = []
    var previous: (mark: ReferenceMark, link: URL?)?
    for run in text.runs {
        let current: (mark: ReferenceMark, link: URL?)? = run.referenceMark.map { ($0, run.link) }
        if let current, previous.map({ $0.mark != current.mark || $0.link != current.link }) ?? true {
            starts.append(current.mark)
        } else {
            starts.append(nil)
        }
        previous = current
    }
    return starts
}

/// An image of the app's catalog drawn as a decorative template image `side` points square.
@MainActor private func decorativeAsset(_ name: String, side: CGFloat) -> Image {
    guard let asset: UIImage = UIImage(named: name) else { preconditionFailure("an image the app names is not in its catalog: \(name)") }
    let size: CGSize = CGSize(width: side, height: side)
    let image: UIImage = UIGraphicsImageRenderer(size: size).image { _ in asset.withTintColor(.black).draw(in: CGRect(origin: .zero, size: size)) }
    guard let drawn: CGImage = image.cgImage else { preconditionFailure("a drawn image has no bitmap: \(name)") }
    return Image(decorative: drawn, scale: image.scale).renderingMode(.template)
}

/// An SF Symbol drawn as a decorative template image at `side` points: inside a `Text` a symbol image is read aloud by
/// its name, a decorative image is not.
@MainActor private func decorativeSymbol(_ name: String, side: CGFloat) -> Image {
    let configuration: UIImage.SymbolConfiguration = UIImage.SymbolConfiguration(pointSize: side * 0.85, weight: .regular)
    guard let symbol: UIImage = UIImage(systemName: name, withConfiguration: configuration) else {
        preconditionFailure("an SF Symbol the app names does not exist: \(name)")
    }
    let image: UIImage = UIGraphicsImageRenderer(size: symbol.size).image { _ in symbol.withTintColor(.black).draw(at: .zero) }
    guard let drawn: CGImage = image.cgImage else { preconditionFailure("a drawn symbol has no bitmap: \(name)") }
    return Image(decorative: drawn, scale: image.scale).renderingMode(.template)
}

/// The sites' icons the answers draw, held while the app runs: asked of `SiteIcons` once per site and size, drawn
/// rounded at the words' size. Only a whole answer of a paired Mac's chat asks (`fetches`), so neither a half-written
/// address nor a test reaches a site, and at most `siteIconsAsked` sites a launch: a link planted in what a run read
/// makes the phone ask its site for an icon, so the asking is bounded.
@MainActor @Observable final class SiteIconBook {
    static let shared: SiteIconBook = SiteIconBook()
    static let siteIconsAsked: Int = 48
    private var drawn: [String: UIImage] = [:]
    @ObservationIgnored private var asked: Set<String> = []

    func icon(for url: URL, side: CGFloat, fetches: Bool) -> UIImage? {
        guard let site: String = bareSite(url) else { return nil }
        let key: String = "\(site)@\(Int(side.rounded()))"
        if let held: UIImage = drawn[key] { return held }
        if fetches, !asked.contains(key), asked.count < Self.siteIconsAsked {
            asked.insert(key)
            Task {
                guard let image: UIImage = await SiteIcons.shared.icon(for: url) else { return }
                drawn[key] = rounded(image, side: side)
            }
        }
        return nil
    }

    private func rounded(_ image: UIImage, side: CGFloat) -> UIImage {
        let size: CGSize = CGSize(width: side, height: side)
        return UIGraphicsImageRenderer(size: size).image { _ in
            UIBezierPath(roundedRect: CGRect(origin: .zero, size: size), cornerRadius: side * 0.22).addClip()
            image.draw(in: CGRect(origin: .zero, size: size))
        }
    }
}
