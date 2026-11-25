import Foundation
import XCTest

@testable import DotEngine

/// Settings › System › Maintenance (item 1790) decodes what the daemon answers (its own JSON, from the smoke test's
/// stand-ins), and asks to resume or not in the body the daemon reads.
final class MaintenanceModelsTests: XCTestCase {
    func testTheProviderProgramsDecode() throws {
        let view: ProgramsView = try JSONDecoder().decode(
            ProgramsView.self,
            from: Data(
                #"{"programs":[{"name":"Claude Code","command":"claude","path":"/usr/local/bin/claude","version":"2.1.331","support":"supported","why":"measured with Dot (items 1580 to 1582)"},{"name":"OpenCode","command":"opencode","path":null,"version":null,"support":"missing","why":"not installed"}]}"#.utf8
            ))
        XCTAssertEqual(view.programs.map(\.support), ["supported", "missing"])
        XCTAssertEqual(view.programs[0].version, "2.1.331")
        XCTAssertNil(view.programs[1].path)
    }
}
