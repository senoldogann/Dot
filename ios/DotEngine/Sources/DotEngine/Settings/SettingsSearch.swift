import Foundation

/// One place in Settings the search can find (PLAN §13 "Settings (N2 nit)": search with synonyms): a page or a row of
/// a page, its title in the app's language, and the words that also lead to it (synonyms in English and in the app's
/// language, so "approvals" finds Rules whichever language the phone speaks).
public struct SettingsEntry: Sendable, Hashable, Identifiable {
    public let id: String
    public let title: String
    /// Where it lives, e.g. "Agents › Models" (shown under the title of a result).
    public let path: String
    public let synonyms: [String]

    public init(id: String, title: String, path: String, synonyms: [String]) {
        self.id = id
        self.title = title
        self.path = path
        self.synonyms = synonyms
    }
}

/// The words of a text as the search compares them: lowercased, accents and Turkish dotted/dotless i folded, split on
/// anything that is not a letter or a digit.
public func searchWords(_ text: String) -> [String] {
    let folded: String = text.replacingOccurrences(of: "ı", with: "i").replacingOccurrences(of: "İ", with: "i")
        .folding(options: [.caseInsensitive, .diacriticInsensitive, .widthInsensitive], locale: Locale(identifier: "en_US_POSIX"))
        .lowercased()
    return folded.split(whereSeparator: { !$0.isLetter && !$0.isNumber }).map(String.init)
}

/// The entries a query finds, best first. Every word of the query must begin some word of the entry's title, path or
/// synonyms; an entry whose title holds every word ranks above one found by its synonyms alone, then the order given.
/// An empty query finds nothing (the page shows its own list then).
public func searchSettings(_ query: String, in entries: [SettingsEntry]) -> [SettingsEntry] {
    let wanted: [String] = searchWords(query)
    guard !wanted.isEmpty else { return [] }
    func covers(_ words: [String]) -> Bool {
        wanted.allSatisfy { want in words.contains { $0.hasPrefix(want) } }
    }
    var byTitle: [SettingsEntry] = []
    var bySynonym: [SettingsEntry] = []
    for entry in entries {
        let title: [String] = searchWords(entry.title)
        if covers(title) {
            byTitle.append(entry)
        } else if covers(title + searchWords(entry.path) + entry.synonyms.flatMap(searchWords)) {
            bySynonym.append(entry)
        }
    }
    return byTitle + bySynonym
}

/// Why a settings read or write did not work, as a page says it (STATES-TEMPLATE.md rows 8-13).
public enum SettingsFailure: Sendable, Equatable {
    /// No HTTP answer: the page offers the connection's diagnosis.
    case unreachable
    /// 401: the Mac no longer takes this phone's key.
    case keyRefused
    /// 403 `PhoneRefused`: the daemon's words, said with the Mac's continuation.
    case notForAPhone(String)
    /// 404 on a route this app needs: the Mac's Dot is older than it.
    case daemonTooOld
    /// The Mac answered with what this app cannot read (a Dot of another version): the route's path.
    case unreadable(path: String)
    /// Anything else, with the daemon's words or the code.
    case other(String)
}

/// Reads a failed request into the page's failure (pure).
public func settingsFailure(_ error: DaemonRequestError) -> SettingsFailure {
    switch error {
    case .transport: return .unreachable
    case .undecodable(let path, _): return .unreadable(path: path)
    case .status(_, let code, _):
        if code == 401 { return .keyRefused }
        if error.refusedToAPhone { return .notForAPhone(error.detail) }
        if code == 404 && error.detail == "Not Found" { return .daemonTooOld }
        let words: String = error.detail
        return .other(words.isEmpty ? "HTTP \(code)" : words)
    }
}
