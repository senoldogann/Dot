import Foundation

/// Moving a message of a chat's queue one place with its arrow buttons (there is no drag): `ids` are the queue's
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
