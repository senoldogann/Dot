import Foundation

/// A message the daemon refused before anything started because the model it was to run on cannot answer now (its
/// provider has no key, its program is not installed, the service retired it; item 1350): HTTP 422 whose context names the
/// `model`, the `problem` and the `fallback` the message may go on instead (the default model; `nil` when that cannot
/// answer either). The words stay in the box, and a button sends them on the fallback.
struct ModelRefusal: Equatable, Sendable {
    let message: String
    let model: String
    let fallback: String?

    private struct Body: Decodable {
        struct Context: Decodable {
            let model: String?
            let problem: String?
            let fallback: String?
        }
        let message: String?
        let context: Context?
    }

    /// The refusal `error` is, or `nil` for any other failure (a 422 that does not name a model is not this).
    static func from(_ error: any Error) -> ModelRefusal? {
        guard case DaemonRequestError.status(_, let code, let body) = error, code == 422 else { return nil }
        return parse(body)
    }

    static func parse(_ body: String) -> ModelRefusal? {
        guard let data: Data = body.data(using: .utf8), let read: Body = try? JSONDecoder().decode(Body.self, from: data),
            let model: String = read.context?.model, read.context?.problem != nil
        else { return nil }
        return ModelRefusal(message: read.message ?? "\(model) cannot answer this message.", model: model, fallback: read.context?.fallback)
    }
}
