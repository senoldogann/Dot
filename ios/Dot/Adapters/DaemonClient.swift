import Foundation
import os

/// The body of `POST /api/phone/apns`: what Apple gave this phone. Both tokens are sent each time.
struct APNsRegistration: Encodable, Sendable, Equatable {
    let deviceToken: String
    let startToken: String?
    let environment: String

    enum CodingKeys: String, CodingKey {
        case deviceToken = "device_token"
        case startToken = "start_token"
        case environment
    }

    /// Writes `start_token` as `null` when there is none, rather than leaving the key out.
    func encode(to encoder: any Encoder) throws {
        var container: KeyedEncodingContainer<CodingKeys> = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(deviceToken, forKey: .deviceToken)
        try container.encode(startToken, forKey: .startToken)
        try container.encode(environment, forKey: .environment)
    }
}

/// The body of `POST /api/phone/apns/activity`: the update token of one run's Live Activity.
struct ActivityRegistration: Encodable, Sendable, Equatable {
    let runId: String
    let token: String

    enum CodingKeys: String, CodingKey {
        case runId = "run_id"
        case token
    }
}

/// The body of `POST /api/approvals/{id}/decision` from the lock screen (item 1008): a yes or a no, no note, and no
/// "don't ask again", which is the app's page's to give.
struct ApprovalDecision: Encodable, Sendable, Equatable {
    let approve: Bool
    let note: String
    let remember: Bool
}

/// A file for a message to come, as `POST /api/attachments` takes it (item 1009).
struct AttachmentUpload: Encodable, Sendable {
    let name: String
    let mediaType: String
    let dataB64: String

    enum CodingKeys: String, CodingKey {
        case name
        case mediaType = "media_type"
        case dataB64 = "data_b64"
    }
}

/// The id of a kept file, all the share sheet reads of the daemon's answer.
struct StoredAttachmentId: Decodable, Sendable {
    let id: String
}

/// A new chat, as `POST /api/runs` takes it from the share sheet: its goal, the files kept for it, and the share's key,
/// with which the daemon starts it once however often the request is sent again (item 1009, as the page's sends do).
struct ChatStart: Encodable, Sendable {
    let goal: String
    let attachments: [String]
    let key: String
}

/// A request to the daemon that did not succeed.
enum DaemonRequestError: Error, CustomStringConvertible {
    /// The daemon answered with a status other than 2xx.
    case status(path: String, code: Int, body: String)
    /// The request did not reach the daemon or got no HTTP answer.
    case transport(path: String, underlying: URLError)

    var description: String {
        switch self {
        case .status(let path, let code, let body):
            "POST \(path) answered \(code): \(body.prefix(300))"
        case .transport(let path, let underlying):
            "POST \(path) failed: \(underlying.code.rawValue) \(underlying.localizedDescription)"
        }
    }

    /// 4xx other than 408 and 429 will not change on a retry.
    var isRetryable: Bool {
        switch self {
        case .status(_, let code, _):
            code >= 500 || code == 408 || code == 429
        case .transport:
            true
        }
    }
}

/// The APNs environment this build's tokens belong to, read from the `aps-environment` its entitlement was signed with
/// (`APS_ENVIRONMENT`, written into the Info.plist as `DotApsEnvironment`): `production` for `production`, the
/// sandbox otherwise. Debug or Release does not decide it: a Release build from Xcode still gets sandbox tokens.
func apnsEnvironment() -> String {
    let entitled: String? = Bundle.main.object(forInfoDictionaryKey: "DotApsEnvironment") as? String
    return entitled == "production" ? "production" : "sandbox"
}

/// Posts what the app registers with the daemon, authenticated with the phone's key.
///
/// Each call is retried with exponential backoff (1, 2, 4, 8 s) on a transport error or a retryable status, with a
/// warning logged per failed attempt; the last error is thrown. Neither the key nor a full token is logged.
struct DaemonClient: Sendable {
    let session: URLSession
    let maxAttempts: Int
    private let logger: Logger = Logger(subsystem: "dev.dotagent.phone", category: "daemon")

    func registerAPNs(_ registration: APNsRegistration, host: DaemonHost, key: String) async throws {
        try await post(path: "/api/phone/apns", body: registration, host: host, key: key)
    }

    func registerActivity(_ registration: ActivityRegistration, host: DaemonHost, key: String) async throws {
        try await post(path: "/api/phone/apns/activity", body: registration, host: host, key: key)
    }

    /// Answers the approval `approvalId` (an id `isApprovalId` took) with the phone's key, as the app's page does.
    func decide(approvalId: String, approve: Bool, host: DaemonHost, key: String) async throws {
        try await post(
            path: "/api/approvals/\(approvalId)/decision",
            body: ApprovalDecision(approve: approve, note: "", remember: false), host: host, key: key)
    }

    /// Keeps a file for the message to come (`POST /api/attachments`) and returns its id (item 1009).
    func uploadAttachment(_ upload: AttachmentUpload, host: DaemonHost, key: String) async throws -> String {
        let data: Data = try await post(path: "/api/attachments", body: upload, host: host, key: key)
        return try JSONDecoder().decode(StoredAttachmentId.self, from: data).id
    }

    /// Starts a chat with `goal` and the files kept for it (`POST /api/runs`), as the app's page does (item 1009).
    func startChat(_ start: ChatStart, host: DaemonHost, key: String) async throws {
        _ = try await post(path: "/api/runs", body: start, host: host, key: key)
    }

    @discardableResult
    private func post(path: String, body: some Encodable & Sendable, host: DaemonHost, key: String) async throws -> Data {
        var request: URLRequest = URLRequest(url: host.url(path: path))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("Bearer \(key)", forHTTPHeaderField: "Authorization")
        request.httpBody = try JSONEncoder().encode(body)
        request.timeoutInterval = 20

        var attempt: Int = 1
        while true {
            do {
                let answer: Data = try await send(request, path: path)
                logger.info("posted \(path, privacy: .public) on attempt \(attempt)")
                return answer
            } catch {
                logger.warning(
                    "post failed: path=\(path, privacy: .public) attempt=\(attempt) of=\(maxAttempts) error=\(error.description, privacy: .public)"
                )
                guard error.isRetryable, attempt < maxAttempts else {
                    throw error
                }
                try await Task.sleep(for: .seconds(1 << (attempt - 1)))
                attempt += 1
            }
        }
    }

    private func send(_ request: URLRequest, path: String) async throws(DaemonRequestError) -> Data {
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: request)
        } catch let error as URLError {
            throw .transport(path: path, underlying: error)
        } catch {
            throw .transport(path: path, underlying: URLError(.unknown, userInfo: [NSUnderlyingErrorKey: error]))
        }
        guard let http: HTTPURLResponse = response as? HTTPURLResponse else {
            throw .transport(path: path, underlying: URLError(.badServerResponse))
        }
        guard (200..<300).contains(http.statusCode) else {
            throw .status(path: path, code: http.statusCode, body: String(decoding: data, as: UTF8.self))
        }
        return data
    }
}
