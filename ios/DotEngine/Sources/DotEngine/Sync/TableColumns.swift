import Foundation

/// The widths of an answer's table columns (item of 2026-11-26: wrapped cells drew over the next row). Each column wants
/// its widest cell on one line (`ideal`); when they all fit in `fit` they get that. Otherwise the widest columns give
/// way first: they share one cap, the largest that makes the table `fit` wide, so a narrow column keeps its words on one
/// line and the long ones wrap. No column goes under `minimum` (or its own ideal, when that is less); a table that still
/// does not fit is wider than `fit` and scrolls sideways. Pure.
public func tableColumnWidths(ideal: [Double], fit: Double, minimum: Double) -> [Double] {
    guard !ideal.isEmpty, ideal.reduce(0, +) > fit else { return ideal }
    let floors: [Double] = ideal.map { min($0, minimum) }
    // The cap is found where the capped widths sum to `fit`: walk the columns from the narrowest.
    let sorted: [Double] = ideal.sorted()
    var below: Double = 0
    var cap: Double = minimum
    for (index, width) in sorted.enumerated() {
        let rest: Int = sorted.count - index
        let candidate: Double = (fit - below) / Double(rest)
        if candidate <= width {
            cap = candidate
            break
        }
        below += width
    }
    return zip(ideal, floors).map { width, floor in max(floor, min(width, max(cap, minimum))) }
}
