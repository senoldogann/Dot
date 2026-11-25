import Foundation

/// A request to the daemon that did not succeed. Moved from the old app (`ios/Dot/Adapters/DaemonClient.swift`).
public enum DaemonRequestError: Error, CustomStringConvertible, Equatable {
    /// The daemon answered with a status other than 2xx.
    case status(path: String, code: Int, body: String)
    /// The request did not reach the daemon or got no HTTP answer.
    case transport(path: String, underlying: URLError)
    /// The answer came but could not be read as the contract says.
    case undecodable(path: String, detail: String)

    public var description: String {
        switch self {
        case .status(let path, let code, let body): "\(path) answered \(code): \(body.prefix(300))"
        case .transport(let path, let underlying): "\(path) failed: \(underlying.code.rawValue) \(underlying.localizedDescription)"
        case .undecodable(let path, let detail): "\(path) answered what the app cannot read: \(detail.prefix(300))"
        }
    }

    /// 4xx other than 408 and 429 will not change on a retry; neither will an answer the app cannot read.
    public var isRetryable: Bool {
        switch self {
        case .status(_, let code, _): code >= 500 || code == 408 || code == 429
        case .transport: true
        case .undecodable: false
        }
    }

    public var statusCode: Int? {
        if case .status(_, let code, _) = self { return code }
        return nil
    }

    /// The daemon's own words of a refusal, at most 160 characters; empty when it gave none. Dot's errors carry
    /// `{"message": ..., "context": ...}` (`server/app.py`); FastAPI's own (an unknown route) carry `{"detail": ...}`.
    public var detail: String {
        guard case .status(_, _, let body) = self, let data: Data = body.data(using: .utf8),
            let answer: ErrorBody = try? JSONDecoder().decode(ErrorBody.self, from: data)
        else { return "" }
        return String((answer.message ?? answer.detail ?? "").prefix(160))
    }

    /// Whether the daemon refused this because the caller is a phone (`PhoneRefused`: 403 with `context.phone`).
    public var refusedToAPhone: Bool {
        guard case .status(_, 403, let body) = self, let data: Data = body.data(using: .utf8),
            let answer: ErrorBody = try? JSONDecoder().decode(ErrorBody.self, from: data)
        else { return false }
        return answer.context?.phone == true
    }
}

private struct ErrorBody: Decodable {
    let message: String?
    let detail: String?
    let context: ErrorContext?
}

private struct ErrorContext: Decodable {
    let phone: Bool?
}
