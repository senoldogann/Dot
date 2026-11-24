import Foundation

/// Moving a message of a chat's queue one place (the context menu's "Move up" and "Move down"): `ids` are the queue's
/// message ids, the next one first, and the answer is what `PUT /api/threads/{id}/queue` takes, or `nil` when the
/// message is not there or is already at that end.
func queueOrder(_ ids: [String], moving id: String, by step: Int) -> [String]? {
    guard let from: Int = ids.firstIndex(of: id) else { return nil }
    let to: Int = from + step
    guard step != 0, ids.indices.contains(to) else { return nil }
    var moved: [String] = ids
    moved.swapAt(from, to)
    return moved
}

/// Moving messages of the queue by dragging their handles (item 1643), as a list's `onMove` says it: the rows at `offsets`
/// go before the row that stood at `destination`. The answer is the new order, or `nil` when nothing moved (an offset that is
/// not a row, or a drop where the row already stood).
func queueOrder(_ ids: [String], moving offsets: IndexSet, to destination: Int) -> [String]? {
    guard !offsets.isEmpty, offsets.allSatisfy({ ids.indices.contains($0) }), destination >= 0, destination <= ids.count else { return nil }
    var moved: [String] = ids
    moved.move(fromOffsets: offsets, toOffset: destination)
    return moved == ids ? nil : moved
}
