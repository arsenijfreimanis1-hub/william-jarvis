import Foundation

/// Lenient JSON value so the UI never breaks when the API adds or renames fields.
enum JSON: Equatable {
    case string(String)
    case number(Double)
    case bool(Bool)
    case object([String: JSON])
    case array([JSON])
    case null

    init(any: Any) {
        switch any {
        case let s as String: self = .string(s)
        case let b as Bool: self = .bool(b)
        case let n as NSNumber:
            if CFGetTypeID(n) == CFBooleanGetTypeID() { self = .bool(n.boolValue) } else { self = .number(n.doubleValue) }
        case let d as [String: Any]: self = .object(d.mapValues { JSON(any: $0) })
        case let a as [Any]: self = .array(a.map { JSON(any: $0) })
        default: self = .null
        }
    }

    static func parse(_ data: Data) -> JSON {
        guard let obj = try? JSONSerialization.jsonObject(with: data, options: [.fragmentsAllowed]) else { return .null }
        return JSON(any: obj)
    }

    subscript(key: String) -> JSON {
        if case let .object(d) = self { return d[key] ?? .null }
        return .null
    }

    subscript(index: Int) -> JSON {
        if case let .array(a) = self, a.indices.contains(index) { return a[index] }
        return .null
    }

    var string: String? { if case let .string(s) = self { return s }; return nil }
    var double: Double? {
        switch self {
        case let .number(n): return n
        case let .string(s): return Double(s)
        case let .bool(b): return b ? 1 : 0
        default: return nil
        }
    }
    var int: Int? { double.map { Int($0) } }
    var bool: Bool? {
        switch self {
        case let .bool(b): return b
        case let .number(n): return n != 0
        case let .string(s): return s == "true"
        default: return nil
        }
    }
    var array: [JSON] { if case let .array(a) = self { return a }; return [] }
    var object: [String: JSON] { if case let .object(d) = self { return d }; return [:] }
    var isNull: Bool { if case .null = self { return true }; return false }
    var text: String {
        switch self {
        case let .string(s): return s
        case let .number(n): return n == n.rounded() ? String(Int(n)) : String(format: "%.2f", n)
        case let .bool(b): return b ? "true" : "false"
        case .null: return ""
        case .array, .object: return prettyString
        }
    }

    var prettyString: String {
        guard let data = try? JSONSerialization.data(withJSONObject: toAny(), options: [.prettyPrinted, .sortedKeys, .fragmentsAllowed]) else { return "" }
        return String(decoding: data, as: UTF8.self)
    }

    func toAny() -> Any {
        switch self {
        case let .string(s): return s
        case let .number(n): return n
        case let .bool(b): return b
        case let .object(d): return d.mapValues { $0.toAny() }
        case let .array(a): return a.map { $0.toAny() }
        case .null: return NSNull()
        }
    }
}
