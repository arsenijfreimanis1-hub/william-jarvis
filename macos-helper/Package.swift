// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "JarvisHelper",
    platforms: [.macOS(.v14)],
    dependencies: [
        // Terminal emulator for William Studio's IDE (MIT).
        .package(url: "https://github.com/migueldeicaza/SwiftTerm.git", from: "1.2.0"),
    ],
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
        .executableTarget(
            name: "WilliamStudio",
            dependencies: [
                .product(name: "SwiftTerm", package: "SwiftTerm"),
            ],
            path: "Sources/WilliamStudio"
        ),
    ]
)
