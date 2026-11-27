#if DEBUG
    import DotEngine
    import Foundation

    /// The chat screen's fixtures (debug builds only): a chat as the daemon's contract would give it, decoded from JSON so
    /// it goes through the same types, for the screenshot harness and the UI tests (`-DotNextScene n2-…`). The shipped
    /// app shows real data only (§20).
    enum ChatFixture: String, CaseIterable {
        /// A finished turn folded, a working turn with tool rows and a screenshot, a plan, helpers, a table and code.
        case work = "n2-chat"
        /// Two asks: an approval with a long command and a question with options.
        case asks = "n2-asks"
        /// Two questions answered with one Submit (PLAN §21).
        case question = "n2-question"
        /// A secret ask.
        case secret = "n2-secret"
        /// A hand-off.
        case handoff = "n2-handoff"
        /// The queue sheet over a working chat.
        case queue = "n2-queue"
        /// A queued message being edited in the composer.
        case editing = "n2-editing"
        /// A run that failed, then one the usage limit stopped.
        case failed = "n2-failed"
        /// The phone offline with nothing saved of the chat.
        case offline = "n2-offline"
        /// No link, with the chat's copy saved on the phone (item 1931: the pill says Offline over it).
        case offlineSaved = "n2-offline-saved"
        /// A chat deleted on the Mac while open.
        case deleted = "n2-deleted"
        /// The agents sheet (§19's one-line titles).
        case agents = "n2-agents"
        /// The run inspector.
        case inspector = "n2-inspector"
        /// The chat's settings sheet: models, options, catalog (PLAN §24).
        case model = "n2-model"
        /// A working chat whose command rows return long output (a test run of 600 lines, a file of 400): the jank check of
        /// opening and closing a row (`UITests/WorkLogJankUITests.swift`).
        case jank = "n2-jank"
        /// A run that wrote its answer and finished with it in slightly other words (item 1996): the answer stands once.
        case restated = "n2-restated"
        /// The main run has stopped and only waits for a helper it delegated to (its newest step's one open call is a
        /// delegate): the capsule says "Waiting on 1 helper" (the owner's report of 2026-11-27). In `n2-chat` the run works
        /// beside its helper and says "Working".
        case helpersWait = "n2-helpers"
        /// The first run a schedule fired (owner 2026-11-27): its message is the routine's compact row, not a bubble.
        case routine = "n2-routine"
        /// A chat with several tasks (item 2013): the capsule's "N tasks" and the tasks sheet.
        case tasks = "n2-tasks"

        static let thread: String = "thr_fixture"
        static let helperThread: String = "thr_h1"

        var panel: ChatPanel? {
            switch self {
            case .queue: .queue
            case .agents: .agents
            case .inspector: .inspector
            case .model: .model
            case .tasks: .tasks
            default: nil
            }
        }

        /// The snapshot the scene draws.
        func snapshot(now: Double) -> EngineSnapshot {
            var snapshot: EngineSnapshot = .empty
            let linked: Bool = self != .offline && self != .offlineSaved
            snapshot.transport = linked ? .up : .offline
            snapshot.online = linked
            if self == .offlineSaved { snapshot.savedAt[Self.thread] = now - 240 }
            let freshness: Freshness = self == .deleted ? .deleted : self == .offline ? .empty : self == .offlineSaved ? .cached : .live
            if self != .offline {
                let view: ThreadView = decode(viewJSON(now: now))
                var chat: ChatProjection = ChatProjection.drawn(view: view, events: decode(eventsJSON(now: now)), freshness: freshness)
                if self == .work { chat = chat.writing("Checking the second **calendar** now", run: "run_b") }
                snapshot.chats[Self.thread] = chat
                // The first helper's own chat, which the Agents sheet opens (item 1880): its run names a parent run of
                // another chat, and the chat shows that run's steps and answer.
                snapshot.chats[Self.helperThread] = ChatProjection.drawn(
                    view: decode(helperViewJSON(now: now)), events: decode(helperEventsJSON(now: now)), freshness: freshness)
            } else {
                snapshot.chats[Self.thread] = ChatProjection(threadId: Self.thread)
            }
            snapshot.list = ListProjection.drawn(rows: decode(rowsJSON(now: now)))
            return snapshot
        }

        private func decode<T: Decodable>(_ json: String) -> T {
            do {
                return try JSONDecoder().decode(T.self, from: Data(json.utf8))
            } catch {
                preconditionFailure("a fixture does not decode: \(error)")
            }
        }

        private var asks: String {
            switch self {
            case .asks:
                #"""
                [{"id":"apr_1","run_id":"run_b","created_at":1,"status":"pending","tool":"shell","severity":"medium",
                  "summary":"Run a command that deletes the build folder and rebuilds the app",
                  "args":{"kind":"shell","target":"rm -rf ~/Projects/site/build -- --force && npm run build -- --mode=production"}},
                 {"id":"apr_2","run_id":"run_b","created_at":2,"status":"pending","tool":"ask_user","severity":"question","summary":"q",
                  "args":{"header":"Appointment","question":"Which day suits you for the dentist?","multiple":false,
                    "options":[{"label":"Tuesday afternoon","description":"14:00 or 15:30 are free","recommended":true},{"label":"Thursday morning"}]}}]
                """#
            case .question:
                #"""
                [{"id":"apr_q1","run_id":"run_b","created_at":1,"status":"pending","tool":"ask_user","severity":"question","summary":"q",
                  "args":{"header":"MCP","question":"Playwright and chrome-devtools start with every launch of Dot and were not used in 14 days. Close them?","multiple":false,
                    "options":[{"label":"Keep them on","description":"Nothing changes."},{"label":"Close both","description":"Dot's own browser tools do the same; launches get faster. One tap in Settings › Connectors turns them back on.","recommended":true}]}},
                 {"id":"apr_q2","run_id":"run_b","created_at":2,"status":"pending","tool":"ask_user","severity":"question","summary":"q",
                  "args":{"header":"Appointment","question":"Which day suits you for the dentist?","options":[{"label":"Tuesday afternoon","recommended":true},{"label":"Thursday morning"}]}}]
                """#
            case .secret:
                #"""
                [{"id":"apr_3","run_id":"run_b","created_at":1,"status":"pending","tool":"ask_secret","severity":"secret","summary":"s",
                  "args":{"name":"CLOUDFLARE_API_TOKEN","label":"Cloudflare API token (DNS edit only)",
                    "why":"To add the TXT record that proves the domain is yours. Make one at dash.cloudflare.com › My Profile › API Tokens."}}]
                """#
            case .handoff:
                #"""
                [{"id":"apr_4","run_id":"run_b","created_at":1,"status":"pending","tool":"browser_click","severity":"handoff",
                  "summary":"Solve the captcha on the sign-in page of example.com","args":{}}]
                """#
            default: "[]"
            }
        }

        private var runB: String {
            switch self {
            case .failed: "failed"
            case .deleted, .restated: "done"
            default: "running"
            }
        }

        private func viewJSON(now: Double) -> String {
            let queue: String =
                self == .queue || self == .editing || self == .work
                ? #"[{"id":"q1","thread_id":"thr_fixture","position":1,"text":"Connect everything to real data, as a Grok-like assistant would, and keep the tool calls visible","created_at":1,"source":"phone"},{"id":"q2","thread_id":"thr_fixture","position":2,"text":"Then send me the screenshots of the new designs","created_at":2,"source":"phone"}]"#
                : "[]"
            let sourceA: String = self == .routine ? "schedule" : "phone"
            let schedules: String =
                self == .routine
                ? #"[{"schedule":{"record":{"id":"sch_1","name":"Site build check","goal":"Find out why the site's build fails on CI and fix it"}},"loop":false}]"#
                : "[]"
            let tasks: String =
                self == .tasks
                ? #"{"chat_id":"thr_fixture","chat_title":"Dentist and the website","cap":5,"tasks":[{"thread_id":"thr_fixture","title":"Dentist and the website","run_id":"run_b","status":"running","state":"working","own":true},{"thread_id":"thr_lane1","title":"Improve my x.com account","run_id":"run_l1","status":"running","state":"working","own":false},{"thread_id":"thr_lane2","title":"Fill in the Gistsmith form","run_id":"run_l2","status":"done","state":"waits","own":false},{"thread_id":"thr_lane3","title":"Book the garage","run_id":"run_l3","status":"cancelled","state":"ended","own":false}]}"#
                : #"{"chat_id":"thr_fixture","chat_title":"Dentist and the website","cap":5,"tasks":[]}"#
            return """
                {"revision":7,
                 "thread":{"id":"thr_fixture","title":"Dentist and the website","source":"phone","created_at":\(now - 3600),"updated_at":\(now),"agent_id":null},
                 "summary":null,
                 "runs":[
                   {"id":"run_a","thread_id":"thr_fixture","goal":"Find out why the site's build fails on CI and fix it","mode":"task","model":"claude_code:opus","status":"done","created_at":\(now - 3000),"finished_at":\(now - 2866),"result":"","parent_run_id":null,"source":"\(sourceA)","effort":"medium","input_tokens":48210,"output_tokens":3120,"approvals":"auto"},
                   {"id":"run_b","thread_id":"thr_fixture","goal":"Book me the dentist for next week, afternoons if you can.","mode":"task","model":"claude_code:opus","status":"\(runB)","created_at":\(now - 2475),"finished_at":null,"result":"","parent_run_id":null,"source":"phone","effort":"medium","input_tokens":3212154,"output_tokens":8132,"approvals":"auto"}
                 ],
                 "older":false,"newest":null,"newest_events":[],"newest_events_cut":false,
                 "queue":{"held":false,"messages":\(queue),"beside_run_id":"run_b","beside_thread_id":null},
                 "asks":\(asks),
                 "helpers":[
                   {"run_id":"run_h1","parent_run_id":"run_b","thread_id":"thr_h1","agent_id":null,"title":"Check the dentist's free slots on their booking page","status":"running","started_at":\(now - 2475),"finished_at":null,"provider":"claude_code","model":"opus","effort":"medium","step":{"tool":"browser_open","target":"https://dentist.example/booking","line":"","at":\(now - 20)},"blocking":\(self == .helpersWait),"stop":"/api/runs/run_h1/cancel"},
                   {"run_id":"run_h2","parent_run_id":"run_b","thread_id":"thr_h2","agent_id":null,"title":"Read the calendar for next week","status":"done","started_at":\(now - 2400),"finished_at":\(now - 2380),"provider":"claude_code","model":"sonnet","effort":"low","step":null,"blocking":false,"stop":"/api/runs/run_h2/cancel"}
                 ],
                 "background":[],
                 "reactions":[{"run_id":"run_a","message":"answer","reaction":"good"}],
                 "drafts":[],
                 "schedules":\(schedules),
                 "tasks":\(tasks)}
                """
        }

        private func eventsJSON(now: Double) -> String {
            let a: Double = now - 3000
            let b: Double = now - 2475
            var events: [String] = [
                #"{"id":1,"run_id":"run_a","ts":\#(a),"kind":"tool_call","data":{"call_id":"a1","tool":"read_file","args":{"path":"~/Projects/site/.github/workflows/ci.yml"}}}"#,
                #"{"id":2,"run_id":"run_a","ts":\#(a + 4),"kind":"tool_result","data":{"call_id":"a1","ok":true,"text":"name: ci\non: push"}}"#,
                #"{"id":3,"run_id":"run_a","ts":\#(a + 5),"kind":"tool_call","data":{"call_id":"a2","tool":"shell","args":{"command":"npm run build"}}}"#,
                #"{"id":4,"run_id":"run_a","ts":\#(a + 60),"kind":"tool_result","data":{"call_id":"a2","ok":true,"text":"built in 41 s"}}"#,
                #"{"id":5,"run_id":"run_a","ts":\#(a + 61),"kind":"tool_call","data":{"call_id":"a3","tool":"read_file","args":{"path":"~/Projects/site/package.json"}}}"#,
                #"{"id":6,"run_id":"run_a","ts":\#(a + 62),"kind":"tool_result","data":{"call_id":"a3","ok":true,"text":"{}"}}"#,
                #"{"id":7,"run_id":"run_a","ts":\#(a + 63),"kind":"tool_call","data":{"call_id":"a4","tool":"shell","args":{"command":"npm test"}}}"#,
                #"{"id":8,"run_id":"run_a","ts":\#(a + 134),"kind":"tool_result","data":{"call_id":"a4","ok":true,"text":"42 passed"}}"#,
                ###"{"id":9,"run_id":"run_a","ts":\###(a + 134),"kind":"run_finished","data":{"status":"done","result":"## Fixed\nThe build failed because **Node 18** was pinned on CI while the lock file needs Node 22.\n\n| Agent | Job |\n|---|---|\n| Invention Scout | Checks a lost invention against two independent sources and lists the Commons images with their licences → `research/<date>-<slug>.md` |\n| Story Scripter | Writes 9 scenes of 130–145 words in English to `script.json` (the hook in the first 3 s, an ironic close) |\n| Channel Analyst | Measures the weekly views |\n\n```yaml\n- uses: actions/setup-node@v4\n  with:\n    node-version: 22\n```\n\nhttps://github.com/example/site/pull/1012 merged, see [Node's release notes](https://nodejs.org/en/blog) for 22. The notes are in `~/Desktop/ci-notes.md`."}}"###,
                #"{"id":20,"run_id":"run_b","ts":\#(b + 2),"kind":"plan","data":{"steps":[{"text":"Read next week's calendar","status":"done"},{"text":"Find free afternoon slots at the dentist","status":"in_progress"},{"text":"Book the slot you choose","status":"pending"}]}}"#,
                #"{"id":21,"run_id":"run_b","ts":\#(b + 3),"kind":"tool_call","data":{"call_id":"b1","tool":"delegate","args":{"goal":"Check the dentist's free slots"}}}"#,
                #"{"id":22,"run_id":"run_b","ts":\#(b + 4),"kind":"tool_result","data":{"call_id":"b1","ok":true,"text":"started"}}"#,
                #"{"id":23,"run_id":"run_b","ts":\#(b + 30),"kind":"tool_call","data":{"call_id":"b2","tool":"browser_click","args":{"url":"https://dentist.example/booking"}}}"#,
                #"{"id":24,"run_id":"run_b","ts":\#(b + 34),"kind":"tool_result","data":{"call_id":"b2","ok":true,"text":"clicked Next week","images":[]}}"#,
                #"{"id":25,"run_id":"run_b","ts":\#(b + 40),"kind":"tool_call","data":{"call_id":"b3","tool":"shell","args":{"command":"icalbuddy eventsFrom:today to:+7"}}}"#,
                #"{"id":26,"run_id":"run_b","ts":\#(b + 41),"kind":"tool_result","data":{"call_id":"b3","ok":false,"text":"icalbuddy: command not found"}}"#,
                #"{"id":27,"run_id":"run_b","ts":\#(b + 42),"kind":"assistant","data":{"text":"Your Tuesday and Thursday afternoons are free; the dentist shows two slots.","thinking":"Compare the calendar with the dentist's free slots before answering.","step":4}}"#,
                #"{"id":28,"run_id":"run_b","ts":\#(b + 43),"kind":"tool_call","data":{"call_id":"b4","tool":"wait_until","args":{"command":"test -f /tmp/slots"}}}"#,
            ]
            if self == .jank { events.insert(contentsOf: jankEvents(at: b), at: events.count - 1) }
            switch self {
            case .work:
                events.append(#"{"id":29,"run_id":"run_b","ts":\#(b + 50),"kind":"steer","data":{"text":"A helper that read the calendar ended: Tuesday 14:00 and Thursday 10:30 are free.","of_dot":true}}"#)
            case .asks:
                events.append(#"{"id":29,"run_id":"run_b","ts":\#(b + 50),"kind":"approval_requested","data":{"approval_id":"apr_1","severity":"medium","summary":"Run a command that deletes the build folder"}}"#)
            case .failed:
                events.append(#"{"id":30,"run_id":"run_b","ts":\#(b + 60),"kind":"run_finished","data":{"status":"failed","result":"","error":{"kind":"model","message":"The model answered HTTP 529: overloaded. Try again in a minute."}}}"#)
            case .deleted:
                events.append(#"{"id":30,"run_id":"run_b","ts":\#(b + 60),"kind":"run_finished","data":{"status":"done","result":"Booked Tuesday 14:00."}}"#)
            case .helpersWait:
                events.append(contentsOf: [
                    #"{"id":29,"run_id":"run_b","ts":\#(b + 44),"kind":"tool_result","data":{"call_id":"b4","ok":true,"text":"ready"}}"#,
                    #"{"id":30,"run_id":"run_b","ts":\#(b + 45),"kind":"assistant","data":{"text":"","step":5,"tool_calls":["delegate"]}}"#,
                    #"{"id":31,"run_id":"run_b","ts":\#(b + 46),"kind":"tool_call","data":{"call_id":"b5","tool":"delegate","args":{"goal":"Check the dentist's free slots"}}}"#,
                ])
            case .restated:
                events.append(contentsOf: [
                    #"{"id":30,"run_id":"run_b","ts":\#(b + 50),"kind":"assistant","data":{"text":"Booked: Tuesday at 14:00 with Dr. Lind. ","step":5,"tool_calls":[]}}"#,
                    #"{"id":31,"run_id":"run_b","ts":\#(b + 52),"kind":"assistant","data":{"text":"","step":6,"tool_calls":["finish"]}}"#,
                    #"{"id":32,"run_id":"run_b","ts":\#(b + 52),"kind":"tool_call","data":{"call_id":"f1","tool":"finish","args":{"answer":"Booked: Tuesday at 14:00 with Dr. Lind."}}}"#,
                    #"{"id":33,"run_id":"run_b","ts":\#(b + 53),"kind":"tool_result","data":{"call_id":"f1","ok":true,"text":"done"}}"#,
                    #"{"id":34,"run_id":"run_b","ts":\#(b + 53),"kind":"run_finished","data":{"status":"done","result":"Booked: Tuesday at 14:00 with Dr. Lind.","restated":[5]}}"#,
                ])
            default:
                break
            }
            return "[" + events.joined(separator: ",") + "]"
        }

        /// Two rows of long output among short ones: 600 lines of test results and 400 of a source file, as a JSON string's
        /// escaped newlines (no quote or backslash in the lines, so the JSON stays valid).
        private func jankEvents(at b: Double) -> [String] {
            let tests: String = (1...600).map { "  PASS  suite \($0) > handles case \($0) in 12 ms, expect(received).toEqual(expected) deep equality holds" }.joined(separator: "\\n")
            let source: String = (1...400).map { "    const value\($0) = await client.fetch(path\($0), { retries: \($0 % 5), timeout: 30_000 }); // line \($0)" }.joined(separator: "\\n")
            // Words of the model between the rows, so that the rows are not read as one group ("Ran 3 commands", item 1909).
            return [
                #"{"id":40,"run_id":"run_b","ts":\#(b + 44),"kind":"tool_call","data":{"call_id":"j1","tool":"shell","args":{"command":"pnpm vitest run --reporter=verbose"}}}"#,
                #"{"id":41,"run_id":"run_b","ts":\#(b + 45),"kind":"tool_result","data":{"call_id":"j1","ok":true,"text":"\#(tests)"}}"#,
                #"{"id":42,"run_id":"run_b","ts":\#(b + 45.5),"kind":"assistant","data":{"text":"The tests pass; reading the client next.","step":5}}"#,
                #"{"id":43,"run_id":"run_b","ts":\#(b + 46),"kind":"tool_call","data":{"call_id":"j2","tool":"read_file","args":{"path":"~/Projects/site/src/client.ts"}}}"#,
                #"{"id":44,"run_id":"run_b","ts":\#(b + 47),"kind":"tool_result","data":{"call_id":"j2","ok":true,"text":"\#(source)"}}"#,
                #"{"id":45,"run_id":"run_b","ts":\#(b + 47.5),"kind":"assistant","data":{"text":"The client looks fine; checking the tree.","step":6}}"#,
                #"{"id":46,"run_id":"run_b","ts":\#(b + 48),"kind":"tool_call","data":{"call_id":"j3","tool":"shell","args":{"command":"git status --short"}}}"#,
                #"{"id":47,"run_id":"run_b","ts":\#(b + 49),"kind":"tool_result","data":{"call_id":"j3","ok":true,"text":" M src/client.ts"}}"#,
            ]
        }

        private func helperViewJSON(now: Double) -> String {
            """
            {"revision":3,
             "thread":{"id":"thr_h1","title":"Check the dentist's free slots on their booking page","source":"agent","created_at":\(now - 2475),"updated_at":\(now - 20),"agent_id":null},
             "summary":null,
             "runs":[
               {"id":"run_h1","thread_id":"thr_h1","goal":"Check the dentist's free slots on their booking page","mode":"task","model":"claude_code:opus","status":"running","created_at":\(now - 2475),"finished_at":null,"result":"","parent_run_id":"run_b","source":"agent","effort":"medium","input_tokens":9000,"output_tokens":400,"approvals":"auto"}
             ],
             "older":false,"newest":null,"newest_events":[],"newest_events_cut":false,
             "queue":{"held":false,"messages":[],"beside_run_id":null,"beside_thread_id":null},
             "asks":[],"helpers":[],"background":[],"reactions":[],"drafts":[]}
            """
        }

        private func helperEventsJSON(now: Double) -> String {
            let h: Double = now - 2475
            return "["
                + #"{"id":1,"run_id":"run_h1","ts":\#(h + 10),"kind":"tool_call","data":{"call_id":"h1","tool":"browser_open","args":{"url":"https://dentist.example/booking"}}},"#
                + #"{"id":2,"run_id":"run_h1","ts":\#(h + 14),"kind":"tool_result","data":{"call_id":"h1","ok":true,"text":"opened"}},"#
                + #"{"id":3,"run_id":"run_h1","ts":\#(h + 40),"kind":"assistant","data":{"text":"The booking page lists two free afternoons next week.","step":2}}"#
                + "]"
        }

        private func rowsJSON(now: Double) -> String {
            """
            [{"summary":{"id":"thr_fixture","title":"Dentist and the website","source":"phone","created_at":\(now - 3600),"updated_at":\(now),"pinned_at":null,"run_count":2,"latest_run_id":"run_b","latest_status":"running","latest_created_at":\(now - 2475),"unread":false,"actor_id":null,"last_message":"Book me the dentist","last_role":"user"},"revision":7,"asks":\(self == .asks || self == .question ? 2 : 0)}]
            """
        }
    }
#endif
