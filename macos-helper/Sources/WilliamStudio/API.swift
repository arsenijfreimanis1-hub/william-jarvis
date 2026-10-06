import Foundation

/// HTTP + SSE client for a JarvisCore (local MacBook core by default; Mini directly as fallback).
actor StudioAPI {
    var base: URL
    var token: String

    private let session: URLSession = {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 60
        config.waitsForConnectivity = false
        config.requestCachePolicy = .reloadIgnoringLocalCacheData
        config.urlCache = nil
        return URLSession(configuration: config)
    }()

    init(base: URL, token: String) {
        self.base = base
        self.token = token
    }

    func configure(base: URL, token: String) {
        self.base = base
        self.token = token
    }

    private func request(_ path: String, method: String = "GET", body: Any? = nil, query: [String: String] = [:]) -> URLRequest {
        var comps = URLComponents(url: base.appendingPathComponent(path), resolvingAgainstBaseURL: false)!
        if !query.isEmpty { comps.queryItems = query.map { URLQueryItem(name: $0.key, value: $0.value) } }
        var req = URLRequest(url: comps.url!)
        req.httpMethod = method
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if !token.isEmpty { req.setValue(token, forHTTPHeaderField: "X-Jarvis-Fleet-Token") }
        if let body { req.httpBody = try? JSONSerialization.data(withJSONObject: body, options: [.fragmentsAllowed]) }
        return req
    }

    func get(_ path: String, query: [String: String] = [:]) async throws -> JSON {
        let (data, resp) = try await session.data(for: request(path, query: query))
        try Self.check(resp, data)
        return JSON.parse(data)
    }

    func post(_ path: String, _ body: Any = [String: String](), query: [String: String] = [:]) async throws -> JSON {
        let (data, resp) = try await session.data(for: request(path, method: "POST", body: body, query: query))
        try Self.check(resp, data)
        return JSON.parse(data)
    }

    func rawText(_ path: String) async throws -> String {
        let (data, resp) = try await session.data(for: request(path))
        try Self.check(resp, data)
        return String(decoding: data, as: UTF8.self)
    }

    private static func check(_ resp: URLResponse, _ data: Data) throws {
        guard let http = resp as? HTTPURLResponse else { return }
        if http.statusCode >= 400 {
            let body = String(decoding: data.prefix(300), as: UTF8.self)
            throw APIError.http(http.statusCode, body)
        }
    }

    /// Server-sent events from /api/activity/stream. Yields parsed `data:` payloads.
    nonisolated func events(base: URL, token: String) -> AsyncThrowingStream<JSON, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                var req = URLRequest(url: base.appendingPathComponent("activity/stream"))
                req.timeoutInterval = 3600
                if !token.isEmpty { req.setValue(token, forHTTPHeaderField: "X-Jarvis-Fleet-Token") }
                do {
                    let (bytes, _) = try await URLSession.shared.bytes(for: req)
                    var buffer = ""
                    for try await line in bytes.lines {
                        if line.hasPrefix("data:") {
                            buffer += String(line.dropFirst(5)).trimmingCharacters(in: .whitespaces)
                        } else if line.isEmpty, !buffer.isEmpty {
                            if let data = buffer.data(using: .utf8) { continuation.yield(JSON.parse(data)) }
                            buffer = ""
                        }
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }
}

enum APIError: LocalizedError {
    case http(Int, String)
    var errorDescription: String? {
        switch self { case let .http(code, body): return "HTTP \(code): \(body)" }
    }
}
