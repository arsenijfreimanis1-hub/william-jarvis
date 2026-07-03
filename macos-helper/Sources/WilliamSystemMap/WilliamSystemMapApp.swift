import AppKit
import SwiftUI
import WebKit

private let defaultURL = URL(string: "http://127.0.0.1:8787/map")!

struct WebContainer: NSViewRepresentable {
    let url: URL

    func makeNSView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.preferences.setValue(true, forKey: "developerExtrasEnabled")
        let webView = WKWebView(frame: .zero, configuration: config)
        webView.setValue(false, forKey: "drawsBackground")
        webView.load(URLRequest(url: url))
        return webView
    }

    func updateNSView(_ webView: WKWebView, context: Context) {
        if webView.url?.absoluteString != url.absoluteString {
            webView.load(URLRequest(url: url))
        }
    }
}

@main
struct WilliamSystemMapApp: App {
    var body: some Scene {
        WindowGroup {
            WebContainer(url: defaultURL)
                .preferredColorScheme(.dark)
                .frame(minWidth: 1200, minHeight: 760)
        }
        .defaultSize(width: 1320, height: 860)
        .commands {
            CommandGroup(replacing: .newItem) {}
        }
    }
}
