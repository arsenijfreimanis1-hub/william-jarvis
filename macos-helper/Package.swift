// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "JarvisHelper",
    platforms: [.macOS(.v14)],
    targets: [
        .executableTarget(
            name: "JarvisHelper",
            path: "Sources/JarvisHelper"
        ),
        .executableTarget(
            name: "WilliamKiosk",
            path: "Sources/WilliamKiosk"
        ),
        .executableTarget(
            name: "WilliamDesktop",
            path: "Sources/WilliamDesktop"
        ),
        .executableTarget(
            name: "WilliamSystemMap",
            path: "Sources/WilliamSystemMap"
        ),
    ]
)
