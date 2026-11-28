#if DEBUG
    import Foundation

    /// The AI services on the fixture daemon (item 2054): what a throwaway daemon's `/api/keys` answers, with OpenAI
    /// unset, OpenRouter saved in Dot and refused at its last check, Anthropic from the environment and Google from the
    /// Keychain. A save answers OpenRouter saved with the tail "Zq7c" (never the key sent), a check passes, a removal
    /// leaves it unset (the fixture keeps no state between requests).
    enum KeyFixture {
        static func answer(method: String, path: String) -> (Int, String)? {
            switch (method, path) {
            case ("GET", "/api/keys"): (200, listed)
            case ("POST", "/api/keys/OPENROUTER_API_KEY"):
                (200, #"{"provider":"openrouter","name":"OPENROUTER_API_KEY","source":"env_file","saved":true,"tail":"Zq7c","check":null}"#)
            case ("DELETE", "/api/keys/OPENROUTER_API_KEY"):
                (200, #"{"provider":"openrouter","name":"OPENROUTER_API_KEY","source":null,"saved":false,"tail":"","check":null}"#)
            case ("POST", "/api/keys/OPENROUTER_API_KEY/check"):
                (200, #"{"name":"openrouter api","ok":true,"required":true,"detail":"OpenRouter took the key: 342 models"}"#)
            default: nil
            }
        }

        private static let listed: String =
            #"{"env_file":"/Users/owner/.dot/.env","keys":[{"provider":"openai","name":"OPENAI_API_KEY","source":null,"saved":false,"tail":"","check":null},{"provider":"anthropic","name":"ANTHROPIC_API_KEY","source":"environment","saved":false,"tail":"Ab12","check":null},{"provider":"opencode_go","name":"OPENCODE_GO_API_KEY","source":null,"saved":false,"tail":"","check":null},{"provider":"openrouter","name":"OPENROUTER_API_KEY","source":"env_file","saved":true,"tail":"7c38","check":{"ok":false,"detail":"401: User not found.","at":1795600000.5}},{"provider":"google","name":"GOOGLE_API_KEY","source":"keychain","saved":false,"tail":"zz99","check":{"ok":true,"detail":"google api answered","at":1795600000.0}},{"provider":"ollama","name":"OLLAMA_API_KEY","source":null,"saved":false,"tail":"","check":null},{"provider":"typesafe","name":"TYPESAFE_API_KEY","source":null,"saved":false,"tail":"","check":null}]}"#
    }
#endif
