import Foundation
import XCTest

@testable import DotNext

/// The owner reads Turkish only (lead's order, 2026-11-24): every key of the String Catalog has a Turkish translation,
/// a translator comment and is not stale, and the built app shows the Turkish words. `scripts/check_strings.sh` adds
/// the other half: no string of the code is missing from the catalog.
final class StringCatalogTests: XCTestCase {
    private struct Catalog: Decodable {
        let sourceLanguage: String
        let strings: [String: Entry]
    }

    private struct Entry: Decodable {
        let comment: String?
        let extractionState: String?
        let localizations: [String: Localization]?
    }

    private struct Localization: Decodable {
        let stringUnit: StringUnit?
    }

    private struct StringUnit: Decodable {
        let state: String
        let value: String
    }

    private func catalog() throws -> Catalog {
        let url: URL = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("Resources/Localizable.xcstrings")
        return try JSONDecoder().decode(Catalog.self, from: Data(contentsOf: url))
    }

    func testEveryKeyHasATurkishTranslationAndAComment() throws {
        let catalog: Catalog = try catalog()
        XCTAssertEqual(catalog.sourceLanguage, "en")
        XCTAssertGreaterThan(catalog.strings.count, 100)
        var problems: [String] = []
        for (key, entry) in catalog.strings.sorted(by: { $0.key < $1.key }) {
            let turkish: StringUnit? = entry.localizations?["tr"]?.stringUnit
            if turkish?.state != "translated" || (turkish?.value ?? "").isEmpty { problems.append("no Turkish: \(key)") }
            if (entry.comment ?? "").isEmpty { problems.append("no comment: \(key)") }
            if entry.extractionState == "stale" { problems.append("stale: \(key)") }
        }
        XCTAssertEqual(problems, [], problems.joined(separator: "\n"))
    }

    func testTheBuiltAppSpeaksTurkish() throws {
        let path: String = try XCTUnwrap(Bundle(for: AppBundleAnchor.self).path(forResource: "tr", ofType: "lproj"))
        let turkish: Bundle = try XCTUnwrap(Bundle(path: path))
        XCTAssertEqual(turkish.localizedString(forKey: "Send", value: nil, table: nil), "Gönder")
        XCTAssertEqual(turkish.localizedString(forKey: "Waiting on you", value: nil, table: nil), "Seni bekliyor")
    }
}
