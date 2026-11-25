#if DEBUG
    import DotEngine
    import Foundation

    /// Settings' fixtures (debug builds only): a daemon served from memory through a `URLProtocol`, so the pages read
    /// it with the real `DaemonAPI` and the real decoding (the contract's JSON), for the screenshot harness and the UI
    /// tests (`-DotNextScene settings-…`). The shipped app reads the paired Mac only (§20).
    enum SettingsFixture: String, CaseIterable {
        case root = "settings-home"
        case search = "settings-search"
        case connection = "settings-mac"
        case diagnosis = "settings-diagnosis"
        case notifications = "settings-notifications"
        case general = "settings-general"
        case appearance = "settings-appearance"
        case chat = "settings-chat"
        case models = "settings-models"
        case caps = "settings-caps"
        case capsRefused = "settings-caps-refused"
        case schedules = "settings-schedules"
        case scheduleEdit = "settings-schedule-edit"
        case rules = "settings-rules"
        case memory = "settings-memory"
        case system = "settings-system"
        case stopped = "settings-stopped"
        case offline = "settings-offline"
        case keyRefused = "settings-key-refused"
        case oldDaemon = "settings-old-daemon"
        case maintenance = "settings-maintenance"
        case pairing = "pairing"
        case pairingLink = "pairing-link"

        static let host: DaemonHost = DaemonHost(name: "macbook-air.tail0a1b2.ts.net", origin: URL(string: "https://macbook-air.tail0a1b2.ts.net")!)

        /// What the fixture daemon does with every request.
        var behaviour: FixtureBehaviour {
            switch self {
            case .offline: .unreachable
            case .keyRefused: .keyRefused
            case .oldDaemon: .old
            case .stopped: .stopped
            case .capsRefused: .phoneRefused
            default: .normal
            }
        }

        /// The page the scene opens in Settings' stack.
        var page: SettingsPage? {
            switch self {
            case .root, .search, .stopped: nil
            case .connection, .diagnosis, .offline, .keyRefused: .mac
            case .notifications: .notifications
            case .general: .general
            case .appearance: .appearance
            case .chat: .chat
            case .models: .models
            case .caps, .capsRefused, .oldDaemon: .caps
            case .schedules, .scheduleEdit: .schedules
            case .rules: .rules
            case .memory: .memory
            case .system: .system
            case .maintenance: .maintenance
            case .pairing, .pairingLink: nil
            }
        }

        var start: SettingsStart {
            SettingsStart(path: page.map { [$0] } ?? [], query: self == .search ? "approvals" : "", editsFirstSchedule: self == .scheduleEdit, triesRaise: self == .capsRefused)
        }

        func api() -> DaemonAPI {
            let configuration: URLSessionConfiguration = URLSessionConfiguration.ephemeral
            configuration.protocolClasses = [FixtureDaemonProtocol.self]
            FixtureDaemonProtocol.behaviour = behaviour
            let session: URLSession = URLSession(configuration: configuration)
            return DaemonAPI(host: Self.host, key: "phone.0123456789ab." + String(repeating: "A", count: 43), session: session, streamSession: session)
        }

        var transport: Transport {
            switch self {
            case .offline: .offline
            case .keyRefused: .keyRefused
            default: .up
            }
        }
    }

    enum FixtureBehaviour: Sendable {
        case normal
        case unreachable
        case keyRefused
        case old
        case stopped
        /// Every write is refused as a phone's (403 `PhoneRefused`).
        case phoneRefused
    }

    /// Serves the fixture daemon. Every answer is the JSON the daemon's routes give (`server/views.py`).
    final class FixtureDaemonProtocol: URLProtocol, @unchecked Sendable {
        nonisolated(unsafe) static var behaviour: FixtureBehaviour = .normal

        override class func canInit(with request: URLRequest) -> Bool { true }
        override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
        override func stopLoading() {}

        override func startLoading() {
            let path: String = request.url?.path() ?? ""
            let method: String = request.httpMethod ?? "GET"
            let behaviour: FixtureBehaviour = Self.behaviour
            if behaviour == .unreachable {
                client?.urlProtocol(self, didFailWithError: URLError(.cannotFindHost))
                return
            }
            let (code, body): (Int, String) = answer(method: method, path: path, behaviour: behaviour)
            let response: HTTPURLResponse = HTTPURLResponse(url: request.url!, statusCode: code, httpVersion: "HTTP/1.1", headerFields: ["Content-Type": "application/json"])!
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: Data(body.utf8))
            client?.urlProtocolDidFinishLoading(self)
        }

        private func answer(method: String, path: String, behaviour: FixtureBehaviour) -> (Int, String) {
            if path == "/api/health" { return (200, #"{"ok":true,"version":"0.2.0","notifications":"ok","awake":"holding"}"#) }
            if path == "/api/proof" {
                let nonce: String = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems?.first { $0.name == "nonce" }?.value ?? ""
                return (200, #"{"proof":"\#(expectedProof(key: "phone.0123456789ab." + String(repeating: "A", count: 43), nonce: nonce))"}"#)
            }
            if behaviour == .keyRefused { return (401, #"{"message":"a valid bearer token is required","context":{}}"#) }
            if behaviour == .old && path == "/api/budget" { return (404, #"{"detail":"Not Found"}"#) }
            let now: Double = Date().timeIntervalSince1970
            switch (method, path) {
            case ("GET", "/api/status"): return (200, status(engaged: behaviour == .stopped))
            case ("GET", "/api/failsafe"): return (200, #"{"engaged":\#(behaviour == .stopped),"cancelled_runs":[]}"#)
            case ("DELETE", "/api/failsafe"): return (200, #"{"engaged":false,"cancelled_runs":[]}"#)
            case ("GET", "/api/assistant"), ("PUT", "/api/assistant"):
                return (200, #"{"name":"Dot","owner":"Şenol","mac_name":"MacBook Air","introduced":true}"#)
            case ("GET", "/api/phone"):
                return (
                    200,
                    #"{"addresses":["https://macbook-air.tail0a1b2.ts.net/"],"tailnet":{"installed":true,"running":true,"name":"macbook-air.tail0a1b2.ts.net","served":true,"reached":true,"written":true,"problem":null},"keep_awake":true}"#
                )
            case ("GET", "/api/phone/push-settings"), ("PUT", "/api/phone/push-settings"):
                return (200, #"{"needs_you":true,"ready":true,"done":false}"#)
            case ("GET", "/api/models"): return (200, models)
            case ("POST", "/api/models/main"), ("POST", "/api/models/roles"): return (200, modelsConfig)
            case ("GET", "/api/code-review"):
                return (
                    200,
                    #"{"model":"claude_code:opus","effort":"medium","fallback":["opencode_go:deepseek-v4.1-flash"],"fallback_effort":"high","small_model":"","small_max_lines":150,"max_minutes":15}"#
                )
            case ("GET", "/api/budget"): return (200, budget)
            case ("PUT", "/api/budget") where behaviour != .phoneRefused: return (200, budget)
            case ("PUT", "/api/budget"):
                return (403, #"{"message":"a phone cannot raise or take off a cap of what the tasks spend: do it on your Mac","context":{"phone":true}}"#)
            case ("GET", "/api/usage"): return (200, usage(now: now))
            case ("GET", "/api/schedules"): return (200, schedules(now: now))
            case ("GET", "/api/rules"): return (200, rules)
            case ("GET", "/api/memories/proposed"): return (200, proposed(now: now))
            case ("GET", "/api/memories/search"): return (200, "[]")
            case ("GET", "/api/reactions/settings"):
                return (200, #"{"follow_ups":true,"follow_ups_per_hour":20,"hold_minutes":30,"good":"feedback","love":"feedback","wrong":"follow_up","again":"follow_up","explain":"follow_up","hold":"follow_up"}"#)
            case ("GET", "/api/diagnostics/programs"):
                return (
                    200,
                    #"{"programs":[{"name":"Claude Code","command":"claude","path":"/opt/homebrew/bin/claude","version":"2.1.331","support":"supported","why":"measured with Dot (items 1580 to 1582)"},{"name":"OpenCode","command":"opencode","path":"/opt/homebrew/bin/opencode","version":"1.19.5","support":"unknown","why":"Dot keeps no list of the OpenCode versions it was measured on"}]}"#
                )
            case ("PUT", "/api/agent/resume"): return (200, #"{"resume":"off","resume_within_s":3600}"#)
            default: return (404, #"{"detail":"Not Found"}"#)
            }
        }

        private var modelsConfig: String {
            #"{"main":"claude_code:sonnet","vision":"claude_code:sonnet","fast":"opencode_go:deepseek-v4.1-flash","review":"claude_code:opus","search":"","effort":"medium","helpers":[],"standby":["ollama:gemma4:cloud"],"helper_cooldown_s":60,"context":{}}"#
        }

        private func status(engaged: Bool) -> String {
            #"{"version":"0.2.0","uptime_s":93600,"active_runs":["run_a","run_b"],"pending_approvals":1,"scheduler_running":\#(!engaged),"failsafe_engaged":\#(engaged),"approvals":"yolo","models":\#(modelsConfig),"resume":{"resume":"safe","resume_within_s":3600}}"#
        }

        private var models: String {
            let option: (String, String) -> String = { ref, label in
                #"{"ref":"\#(ref)","label":"\#(label)","vision":true,"tools":true,"thinking":true,"context_length":200000,"efforts":["low","medium","high"],"default_effort":"medium"}"#
            }
            return "["
                + #"{"provider":"claude_code","models":[\#(option("claude_code:sonnet", "Sonnet 5.5")),\#(option("claude_code:opus", "Opus 5.5"))],"error":null},"#
                + #"{"provider":"opencode_go","models":[\#(option("opencode_go:deepseek-v4.1-flash", "DeepSeek V4.1 Flash"))],"error":null}"#
                + "]"
        }

        private var budget: String {
            #"{"caps":{"run_tokens":500000000,"day_tokens":4000000000,"ui_day_tokens":0,"schedule_day_tokens":500000000,"initiative_day_tokens":500000000,"review_day_tokens":1000000000,"delegate_day_tokens":0},"today":1312000000,"by_source":[{"source":"delegate","tokens":801000000},{"source":"review","tokens":397000000},{"source":"ui","tokens":114000000}]}"#
        }

        private func usage(now: Double) -> String {
            let days: [String] = (0..<7).map { offset in
                let day: String = Date(timeIntervalSince1970: now - Double(6 - offset) * 86400).formatted(.iso8601.year().month().day())
                let tokens: Int = [55, 234, 668, 1058, 2423, 1800, 1312][offset] * 1_000_000
                return #"{"day":"\#(day)","runs":\#(20 + offset * 7),"steps":\#(400 + offset * 90),"input_tokens":\#(tokens - tokens / 40),"output_tokens":\#(tokens / 40),"minutes":\#(60 + offset * 30)}"#
            }
            return #"{"days":[\#(days.joined(separator: ","))],"windows":[{"kind":"five_hour","status":"allowed","utilization":0.62,"resets_at":\#(now + 5400),"seen_at":\#(now - 60)},{"kind":"seven_day","status":"allowed","utilization":0.41,"resets_at":\#(now + 3 * 86400),"seen_at":\#(now - 60)}]}"#
        }

        private func schedules(now: Double) -> String {
            let one: (String, String, String?, Bool, Double?, Double?, String, Int, String) -> String = { id, name, cron, enabled, next, last, status, failed, when in
                let record: String =
                    #"{"id":"\#(id)","name":"\#(name)","cron":\#(cron.map { "\"\($0)\"" } ?? "null"),"run_at":null,"goal":"\#(name): look and tell me what changed.","mode":"task","enabled":\#(enabled),"created_at":\#(now - 864000),"last_run_at":\#(last.map { String($0) } ?? "null"),"next_run_at":\#(next.map { String($0) } ?? "null"),"thread_id":null,"last_status":"\#(status)","options":{"model":null,"effort":null,"speed":null,"approvals":null,"goal_mode":null,"timezone":null,"history_runs":null,"same_chat":null,"stop_after_failures":null,"notify":null,"agent_id":null,"skills":[],"from_outside":false},"runs_failed_in_row":\#(failed),"skipped_in_row":0}"#
                return #"{"record":\#(record),"when":"\#(when)","next_local":null,"last_local":null,"run_at_input":null,"zone":"Europe/Helsinki","agent":null,"brief":false,"ran_once":false}"#
            }
            return "["
                + [
                    one("sch_brief", "Morning brief", "0 8 * * *", true, now + 3240, now - 83160, "done", 0, "Every day at 08:00"),
                    one("sch_mail", "Client mail check", "0 */2 * * *", true, now + 1800, now - 5400, "failed", 2, "Every 2 hours"),
                    one("sch_ci", "CI follow-up", "30 9 * * 1-5", false, nil, now - 3 * 86400, "done", 0, "Weekdays at 09:30"),
                    one("sch_week", "Weekly review", "0 18 * * 5", true, now + 2 * 86400, now - 5 * 86400, "done", 0, "Fridays at 18:00"),
                ].joined(separator: ",") + "]"
        }

        private var rules: String {
            let one: (String, Int, Bool, String, String, String) -> String = { id, position, enabled, behavior, glob, description in
                #"{"id":"\#(id)","position":\#(position),"enabled":\#(enabled),"behavior":"\#(behavior)","tool_glob":"\#(glob)","match_regex":"","description":"\#(description)","created_at":0,"never_decided_by":null}"#
            }
            return "["
                + [
                    one("r1", 1, true, "ask", "mail_send", "Ask before sending any mail"),
                    one("r2", 2, true, "allow", "browser_*", "Use the browser without asking"),
                    one("r3", 3, true, "handoff", "pay_*", "Hand payments to me"),
                    one("r4", 4, false, "preapproved", "shell", "Run tests in ~/Desktop/Dot"),
                ].joined(separator: ",") + "]"
        }

        private func proposed(now: Double) -> String {
            #"[{"id":"mem_1","content":"Şenol prefers short answers in Turkish.","tags":["owner"],"created_at":\#(now - 900000),"updated_at":\#(now - 90000),"source_run":null,"agent_id":null,"used_count":40,"last_used_at":null,"expires_at":null,"replaced_by":null,"tainted":false,"merged_into":null,"merged_from":[],"pinned_at":\#(now - 900000),"rested_at":null,"edited_by":"owner","proposed":{"content":"Şenol prefers short answers in Turkish, with the commands written out in full.","tags":["owner"],"run_id":"run_q","tainted":true,"at":\#(now - 3600)}}]"#
        }
    }
#endif
