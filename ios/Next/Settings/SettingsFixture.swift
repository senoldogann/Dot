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
        case voice = "settings-voice"
        case models = "settings-models"
        case caps = "settings-caps"
        case capsRefused = "settings-caps-refused"
        case schedules = "settings-schedules"
        case scheduleEdit = "settings-schedule-edit"
        case rules = "settings-rules"
        case memory = "settings-memory"
        case agents = "settings-agents"
        case campaigns = "settings-campaigns"
        case campaign = "settings-campaign"
        case system = "settings-system"
        case stopped = "settings-stopped"
        case offline = "settings-offline"
        case keyRefused = "settings-key-refused"
        case oldDaemon = "settings-old-daemon"
        case maintenance = "settings-maintenance"
        case connectApps = "settings-connect-apps"
        case connectAppsDetail = "settings-connect-apps-detail"
        case connectAppsOffline = "settings-connect-apps-offline"
        /// Usage on a Mac that never answers: the page's loading state, measured to stand centred (item 1904).
        case loading = "settings-loading"
        case pairing = "pairing"
        case pairingLink = "pairing-link"

        static let host: DaemonHost = DaemonHost(name: "macbook-air.tail0a1b2.ts.net", origin: URL(string: "https://macbook-air.tail0a1b2.ts.net")!)

        /// What the fixture daemon does with every request.
        var behaviour: FixtureBehaviour {
            switch self {
            case .offline, .connectAppsOffline: .unreachable
            case .keyRefused: .keyRefused
            case .oldDaemon: .old
            case .stopped: .stopped
            case .capsRefused: .phoneRefused
            case .loading: .silent
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
            case .voice: .voice
            case .models: .models
            case .caps, .capsRefused, .oldDaemon: .caps
            case .schedules, .scheduleEdit: .schedules
            case .rules: .rules
            case .memory: .memory
            case .agents: .agents
            case .campaigns: .campaigns
            case .campaign: .campaign("cmp_fixture")
            case .system: .system
            case .maintenance: .maintenance
            case .loading: .usage
            case .pairing, .pairingLink, .connectApps, .connectAppsDetail, .connectAppsOffline: nil
            }
        }

        /// Whether the scene opens Connect apps (item 1862), and on which service.
        var connectAppsOpening: ConnectAppsOpening {
            switch self {
            case .connectApps, .connectAppsOffline: .list
            case .connectAppsDetail: .detail("notion")
            default: .closed
            }
        }

        var start: SettingsStart {
            SettingsStart(path: page.map { [$0] } ?? [], query: self == .search ? "approvals" : "", editsFirstSchedule: self == .scheduleEdit, triesRaise: self == .capsRefused, connectApps: connectAppsOpening)
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
            case .offline, .connectAppsOffline: .offline
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
        /// A read of a page never ends: its answer trickles a space every two seconds, so it never times out either (the
        /// health and proof checks are answered).
        case silent
    }

    /// Serves the fixture daemon. Every answer is the JSON the daemon's routes give (`server/views.py`).
    final class FixtureDaemonProtocol: URLProtocol, @unchecked Sendable {
        nonisolated(unsafe) static var behaviour: FixtureBehaviour = .normal

        override class func canInit(with request: URLRequest) -> Bool { true }
        override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
        /// The `silent` behaviour's keep-alive: a space every two seconds, so the request never times out and never ends.
        private var trickle: DispatchSourceTimer?

        override func stopLoading() {
            trickle?.cancel()
            trickle = nil
        }

        override func startLoading() {
            let path: String = request.url?.path() ?? ""
            let method: String = request.httpMethod ?? "GET"
            let behaviour: FixtureBehaviour = Self.behaviour
            if behaviour == .unreachable {
                client?.urlProtocol(self, didFailWithError: URLError(.cannotFindHost))
                return
            }
            if behaviour == .silent, path != "/api/health", path != "/api/proof" {
                let response: HTTPURLResponse = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: "HTTP/1.1", headerFields: ["Content-Type": "application/json"])!
                client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
                let timer: DispatchSourceTimer = DispatchSource.makeTimerSource(queue: .global())
                timer.schedule(deadline: .now() + 2, repeating: 2)
                timer.setEventHandler { [weak self] in
                    guard let self else { return }
                    self.client?.urlProtocol(self, didLoad: Data(" ".utf8))
                }
                timer.resume()
                trickle = timer
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
            case ("POST", "/api/models/main"), ("POST", "/api/models/roles"), ("PUT", "/api/models/kinds"), ("PUT", "/api/models/standby"):
                return (200, modelsConfig)
            case ("GET", "/api/models/learning"), ("PUT", "/api/models/learning"):
                return (200, #"{"proposer":"","composer":"claude_code:opus","selector":"","experience":"","review":"claude_code:opus"}"#)
            case ("GET", "/api/initiative"), ("PUT", "/api/initiative/look-model"):
                return (200, #"{"enabled":true,"look_model":"","items":[]}"#)
            case ("GET", "/api/repair"), ("PUT", "/api/repair/worker"):
                return (200, #"{"worker":{"model":"opus","effort":"high","fast":false},"worker_lost":false}"#)
            case ("GET", "/api/code-review"):
                return (
                    200,
                    #"{"model":"claude_code:opus","effort":"medium","fallback":["opencode_go:deepseek-v4.1-flash"],"fallback_effort":"high","small_model":"","small_max_lines":150,"max_minutes":15}"#
                )
            case ("PUT", "/api/code-review"):
                return (
                    200,
                    #"{"model":"claude_code:opus","effort":"high","fallback":["opencode_go:deepseek-v4.1-flash"],"fallback_effort":"high","small_model":"","small_max_lines":150,"max_minutes":15}"#
                )
            case ("GET", "/api/policy/free"): return (200, #"{"free":false}"#)
            case ("GET", "/api/memories"): return (200, memories(now: now))
            case ("GET", "/api/agents"): return (200, agents(now: now))
            case ("GET", _) where path.hasPrefix("/api/agents/"):
                return (200, #"{"record":\#(agent("ag_scout", "Scout", "Finds leads and checks them", now: now)),"runs":[],"messages":[],"learned":{"lessons":[],"scores":{},"skills":[]},"usage":null,"work":null,"home":null}"#)
            case ("GET", "/api/budget"): return (200, budget)
            case ("PUT", "/api/budget") where behaviour != .phoneRefused: return (200, budget)
            case ("PUT", "/api/budget"):
                return (403, #"{"message":"a phone cannot raise or take off a cap of what the tasks spend: do it on your Mac","context":{"phone":true}}"#)
            case ("GET", "/api/usage"): return (200, usage(now: now))
            case ("GET", "/api/schedules"): return (200, schedules(now: now))
            case ("GET", "/api/rules"): return (200, rules)
            case ("GET", "/api/memories/proposed"): return (200, proposed(now: now))
            case ("GET", "/api/memories/search"): return (200, "[]")
            case ("GET", "/api/plugins"): return (200, connectPlugins)
            case ("GET", "/api/connections"): return (200, connectConnections)
            case ("GET", "/api/mcp/servers"): return (200, connectServers)
            case ("GET", "/api/vault"):
                return (200, #"{"cli":"/opt/homebrew/bin/op","app":true,"connected":true,"vaults":[{"id":"v1","name":"Shared with Dot","logins":12}],"error":null}"#)
            case ("GET", "/api/accounts"):
                return (200, #"[{"id":"google","name":"Google","configured":true,"client_names":["Dot"],"accounts":[{"address":"senol@example.com","connected_at":\#(now - 86400)}]},{"id":"microsoft","name":"Microsoft","configured":false,"client_names":[],"accounts":[]}]"#)
            case ("POST", let route) where route.hasPrefix("/api/connections/"):
                return (200, #"{"id":"\#(route.split(separator: "/").last ?? "")","kind":"mac","name":"Mail","does":"x","state":"not_asked","step":"ask","waiting":true}"#)
            case ("DELETE", let route) where route.hasPrefix("/api/mcp/servers/"): return (200, connectServers)
            case ("GET", "/api/reactions/settings"):
                return (200, #"{"follow_ups":true,"follow_ups_per_hour":20,"hold_minutes":30,"good":"feedback","love":"feedback","wrong":"follow_up","again":"follow_up","explain":"follow_up","hold":"follow_up"}"#)
            case ("GET", "/api/diagnostics/programs"):
                return (
                    200,
                    #"{"programs":[{"name":"Claude Code","command":"claude","path":"/opt/homebrew/bin/claude","version":"2.1.331","support":"supported","why":"measured with Dot (items 1580 to 1582)"},{"name":"OpenCode","command":"opencode","path":"/opt/homebrew/bin/opencode","version":"1.19.5","support":"unknown","why":"Dot keeps no list of the OpenCode versions it was measured on"}]}"#
                )
            case ("PUT", "/api/agent/resume"): return (200, #"{"resume":"off","resume_within_s":3600}"#)
            default: return CampaignFixture.answer(method: method, path: path) ?? (404, #"{"detail":"Not Found"}"#)
            }
        }

        /// A part of the daemon's real catalogue (`connections/directory.py`): its sections in its order, Featured full.
        private var connectPlugins: String {
            let rows: [(String, String, String, String?, String?, [String], String)] = [
                ("google", "Google", "popular", "google-icon.svg", nil, ["web:google"], "Gmail, Google Calendar and Drive, in Dot's browser."),
                ("notion", "Notion", "popular", "notion-icon.svg", nil, ["mcp:notion", "web:notion"], "Search, read and write your Notion pages and databases."),
                ("github", "GitHub", "popular", "github-icon.svg", nil, ["mcp:github", "web:github"], "Read and work on your repositories, issues, pull requests and actions."),
                ("slack", "Slack", "popular", "slack-icon.svg", nil, ["web:slack"], "Your workspaces' channels and messages, in Dot's browser."),
                ("linear", "Linear", "popular", "linear-icon.svg", nil, ["mcp:linear"], "Find, create and update issues, projects and comments in Linear."),
                ("microsoft-365", "Microsoft 365", "popular", "microsoft-icon.svg", nil, ["web:microsoft"], "Outlook, OneDrive and Office on the web, in Dot's browser."),
                ("mail", "Mail", "popular", nil, "mail", ["mac:mail"], "Reads, sorts and writes your email."),
                ("calendar", "Calendar", "popular", nil, "calendar", ["mac:calendar"], "Reads your events and adds new ones."),
                ("1password", "1Password", "passwords", "1password.svg", nil, ["vault:1password"], "Fills your logins into pages on their own sites."),
                ("sentry", "Sentry", "code", "sentry-icon.svg", nil, ["mcp:sentry"], "Look into errors, issues and releases in Sentry."),
                ("cloudflare", "Cloudflare", "code", "cloudflare-icon.svg", nil, ["mcp:cloudflare"], "Work with your Cloudflare account: Workers, DNS, logs and the rest."),
                ("vercel", "Vercel", "code", "vercel-icon.svg", nil, ["mcp:vercel"], "Look into your Vercel projects, their deployments and logs."),
                ("figma", "Figma", "design", "figma.svg", nil, ["mcp:figma"], "Read your Figma files and their frames, components and comments."),
                ("linkedin", "LinkedIn", "communication", "linkedin-icon.svg", nil, ["web:linkedin"], "Your profile, messages and posts, in Dot's browser."),
                ("todoist", "Todoist", "productivity", "todoist-icon.svg", nil, ["mcp:todoist"], "Read and manage your tasks and projects in Todoist."),
                ("granola", "Granola", "productivity", nil, nil, ["mcp:granola"], "Read your Granola meeting notes and transcripts."),
                ("notes", "Notes", "mac", nil, "pencil", ["mac:notes"], "Finds, writes and files your notes."),
                ("reminders", "Reminders", "mac", nil, "list", ["mac:reminders"], "Keeps your to-dos and lists."),
            ]
            let listings: [String] = rows.map { id, name, section, logo, glyph, ways, does in
                let shown: [String] = ways.map { way in
                    let parts: [Substring] = way.split(separator: ":")
                    return #"{"kind":"\#(parts[0])","ref":"\#(parts[1])","how":"\#(wayHow(String(parts[0])))","data":"Stays between your Mac and \#(name).","does":"\#(does)"}"#
                }
                return #"{"id":"\#(id)","name":"\#(name)","section":"\#(section)","does":"\#(does)","logo":\#(logo.map { "\"\($0)\"" } ?? "null"),"glyph":\#(glyph.map { "\"\($0)\"" } ?? "null"),"ways":[\#(shown.joined(separator: ","))]}"#
            }
            return #"{"plugins":[\#(listings.joined(separator: ","))]}"#
        }

        private func wayHow(_ kind: String) -> String {
            switch kind {
            case "mcp": "A connected app: Dot signs in to the service's own server."
            case "mac": "Dot drives the app on your Mac, once macOS allows it."
            case "web": "Dot's browser, where you sign in once."
            case "vault": "1Password's command line, with a service account that only reads."
            default: "A folder kept in sync on your Mac."
            }
        }

        private var connectConnections: String {
            let app: (String, String, String, String?) -> String = { id, kind, state, step in
                #"{"id":"\#(id)","kind":"\#(kind)","name":"\#(id)","does":"x","state":"\#(state)","step":\#(step.map { "\"\($0)\"" } ?? "null"),"waiting":false}"#
            }
            let apps: [String] = [
                app("mail", "mac", "allowed", nil), app("calendar", "mac", "not_asked", "ask"), app("notes", "mac", "allowed", nil), app("reminders", "mac", "refused", "settings"),
                app("google", "web", "signed_out", "sign_in"), app("notion", "web", "signed_out", "sign_in"), app("github", "web", "signed_in", nil),
                app("slack", "web", "signed_out", "sign_in"), app("microsoft", "web", "signed_out", "sign_in"), app("linkedin", "web", "signed_out", "sign_in"),
            ]
            return #"{"apps":[\#(apps.joined(separator: ","))],"browser_mode":"own","headless":false,"runner":"Dot"}"#
        }

        private var connectServers: String {
            #"{"servers":[{"name":"notion","origin":"connected","address":"https://mcp.notion.com/mcp","service":"notion","state":"ready","tools":["search","fetch"],"error":null,"link":null,"signs_in":true,"mark":null,"picks_tools":true,"plugin":null},"#
                + #"{"name":"sentry","origin":"connected","address":"https://mcp.sentry.dev/mcp","service":"sentry","state":"signing_in","tools":[],"error":null,"link":null,"signs_in":true,"mark":null,"picks_tools":false,"plugin":null}],"#
                + #""services":[{"id":"notion","added":false},{"id":"linear","added":false},{"id":"sentry","added":false}]}"#
        }

        private var modelsConfig: String {
            #"{"main":"claude_code:sonnet","vision":"claude_code:sonnet","fast":"opencode_go:deepseek-v4.1-flash","review":"claude_code:opus","search":"","effort":"medium","helpers":[],"standby":["ollama:gemma4:cloud"],"helper_cooldown_s":60,"context":{},"kinds":{"security":"claude_code:opus"}}"#
        }

        private func status(engaged: Bool) -> String {
            #"{"version":"0.2.0","uptime_s":93600,"active_runs":["run_a","run_b"],"pending_approvals":1,"scheduler_running":\#(!engaged),"failsafe_engaged":\#(engaged),"approvals":"yolo","models":\#(modelsConfig),"resume":{"resume":"safe","resume_within_s":3600}}"#
        }

        private var models: String {
            let option: (String, String) -> String = { ref, label in
                #"{"ref":"\#(ref)","label":"\#(label)","vision":true,"tools":true,"thinking":true,"context_length":200000,"efforts":["low","medium","high"],"default_effort":"medium","fast_mode":\#(ref == "claude_code:opus"),"free":\#(ref.hasPrefix("ollama"))}"#
            }
            return "["
                + #"{"provider":"claude_code","models":[\#(option("claude_code:sonnet", "Sonnet 5.5")),\#(option("claude_code:opus", "Opus 5.5"))],"error":null},"#
                + #"{"provider":"opencode_go","models":[\#(option("opencode_go:deepseek-v4.1-flash", "DeepSeek V4.1 Flash")),\#(option("opencode_go:kimi-k3", "Kimi K3"))],"error":null},"#
                + #"{"provider":"ollama","models":[\#(option("ollama:gemma4:cloud", "Gemma 4 Cloud")),\#(option("ollama:qwen3:cloud", "Qwen 3 Cloud"))],"error":null}"#
                + "]"
        }

        private func note(_ id: String, _ content: String, _ tags: String, now: Double, used: Int) -> String {
            #"{"id":"\#(id)","content":"\#(content)","tags":\#(tags),"created_at":\#(now - 900000),"updated_at":\#(now - 90000),"source_run":null,"agent_id":null,"used_count":\#(used),"last_used_at":\#(now - 7200),"expires_at":null,"replaced_by":null,"tainted":false,"merged_into":null,"merged_from":[],"pinned_at":null,"rested_at":null,"edited_by":"owner","proposed":null}"#
        }

        private func memories(now: Double) -> String {
            let items: [String] = [
                note("mem_1", "Şenol prefers short answers in Turkish.", "[\"owner\",\"preference\"]", now: now, used: 40),
                note("mem_2", "Invoices live in ~/Documents/Invoices.", "[\"work\"]", now: now, used: 12),
                note("mem_3", "Oulu cafés list their hours on Facebook, not a site.", "[\"lesson\"]", now: now, used: 4),
                note("mem_4", "The CI gate runs with -n 4 on this Mac.", "[\"lesson\",\"dot\"]", now: now, used: 9),
            ]
            return #"{"total":4,"items":[\#(items.joined(separator: ","))],"resting":[],"rested":{},"scores":{},"agents":{}}"#
        }


        private func agent(_ id: String, _ name: String, _ role: String, now: Double) -> String {
            #"{"id":"\#(id)","name":"\#(name)","role":"\#(role)","instructions":"Work carefully and say what you checked.","model":"","effort":null,"approvals":null,"tools":[],"created_at":\#(now - 864000),"updated_at":\#(now - 86400),"made_by":null,"look":null,"paused_at":null,"refuses":""}"#
        }


        private func agents(now: Double) -> String {
            let one: (String, String, String) -> String = { id, name, role in
                #"{"record":\#(self.agent(id, name, role, now: now)),"latest":null,"latest_title":null,"home":null}"#
            }
            return "[" + [one("dot", "Dot", ""), one("ag_scout", "Scout", "Finds leads and checks them"), one("ag_writer", "Writer", "Drafts the mails")].joined(separator: ",") + "]"
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
