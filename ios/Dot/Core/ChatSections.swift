import Foundation

/// The chats the list keeps apart from the owner's own (item 1664): the pre-push hook's code reviews (`dotx run
/// --review`, a chat whose `source` is `review`) stand in a folded "Code reviews" section at the foot of the list, as on
/// the web, never among the owner's chats, whether they work, wait or rest.
enum ChatSections {
    static let reviewSource: String = "review"

    static func isReview(_ thread: ThreadSummary) -> Bool { thread.source == reviewSource }

    /// The owner's chats: every chat but the code reviews.
    static func owners(_ threads: [ThreadSummary]) -> [ThreadSummary] { threads.filter { !isReview($0) } }

    /// The code reviews, in the list's order.
    static func reviews(_ threads: [ThreadSummary]) -> [ThreadSummary] { threads.filter(isReview) }
}
