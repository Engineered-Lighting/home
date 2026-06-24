// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use tauri::Manager;

fn main() {
    tauri::Builder::default()
        // Single-instance plugin must be registered FIRST so subsequent
        // launches reach the handler before any window-create logic runs.
        // When a second home.exe starts, this handler fires on the original
        // instance with the new launch's argv + cwd. We just bring the
        // existing window to front and exit the second instance.
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            if let Some(win) = app.get_webview_window("main") {
                let _ = win.show();
                let _ = win.set_focus();
                let _ = win.unminimize();
            }
        }))
        .plugin(tauri_plugin_http::init())
        .plugin(tauri_plugin_shell::init())
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

#[cfg(test)]
mod tests {
    use serde_json::Value;

    const MAIN_RS: &str = include_str!("main.rs");
    const CARGO_TOML: &str = include_str!("../Cargo.toml");
    const TAURI_CONF: &str = include_str!("../tauri.conf.json");
    const DEFAULT_CAPABILITY: &str = include_str!("../capabilities/default.json");

    fn json(text: &str) -> Value {
        serde_json::from_str(text).expect("fixture should parse as JSON")
    }

    #[test]
    fn single_instance_plugin_is_first_and_refocuses_main_window() {
        let single = MAIN_RS
            .find(".plugin(tauri_plugin_single_instance::init")
            .expect("single-instance plugin should be registered");
        let http = MAIN_RS
            .find(".plugin(tauri_plugin_http::init())")
            .expect("HTTP plugin should be registered");
        let shell = MAIN_RS
            .find(".plugin(tauri_plugin_shell::init())")
            .expect("shell plugin should be registered");

        assert!(
            single < http && single < shell,
            "single-instance plugin must stay first so second launches focus the original window"
        );

        let handler = &MAIN_RS[single..http];
        assert!(handler.contains("get_webview_window(\"main\")"));
        assert!(handler.contains("win.show()"));
        assert!(handler.contains("win.set_focus()"));
        assert!(handler.contains("win.unminimize()"));
        assert!(
            MAIN_RS.contains("#![cfg_attr(not(debug_assertions), windows_subsystem = \"windows\")]"),
            "release builds should not spawn a console window on Windows"
        );
    }

    #[test]
    fn cargo_manifest_declares_required_tauri_plugins() {
        for dependency in [
            "tauri-plugin-single-instance",
            "tauri-plugin-http",
            "tauri-plugin-shell",
            "serde_json",
        ] {
            assert!(
                CARGO_TOML.contains(dependency),
                "Cargo.toml should declare {dependency}"
            );
        }
        assert!(
            CARGO_TOML.contains("features = [\"protocol-asset\", \"devtools\"]"),
            "Tauri should keep protocol-asset and devtools enabled"
        );
        assert!(
            CARGO_TOML.contains("panic = \"abort\"")
                && CARGO_TOML.contains("lto = true")
                && CARGO_TOML.contains("strip = true"),
            "release profile should stay compact and deterministic"
        );
    }

    #[test]
    fn tauri_manifest_pins_main_window_and_bundle_shape() {
        let conf = json(TAURI_CONF);
        assert_eq!(conf["productName"], "Home");
        assert_eq!(conf["identifier"], "com.engineeredlighting.home");
        assert_eq!(conf["build"]["frontendDist"], "../src");

        let windows = conf["app"]["windows"]
            .as_array()
            .expect("windows should be an array");
        assert_eq!(windows.len(), 1, "Home should remain a single-window shell");
        let main = &windows[0];
        assert_eq!(main["label"], "main");
        assert_eq!(main["title"], "Home");
        assert_eq!(main["resizable"], true);
        assert_eq!(main["decorations"], false);
        assert_eq!(main["transparent"], false);
        assert_eq!(main["fullscreen"], false);
        assert_eq!(main["width"], 820);
        assert_eq!(main["height"], 900);
        assert_eq!(main["minWidth"], 360);
        assert_eq!(main["minHeight"], 480);

        assert_eq!(conf["bundle"]["active"], true);
        let targets: Vec<&str> = conf["bundle"]["targets"]
            .as_array()
            .expect("bundle targets should be an array")
            .iter()
            .map(|item| item.as_str().expect("bundle target should be a string"))
            .collect();
        assert_eq!(targets, vec!["msi", "dmg"]);
    }

    #[test]
    fn asset_protocol_scope_stays_limited_to_apartment_data() {
        let conf = json(TAURI_CONF);
        let asset = &conf["app"]["security"]["assetProtocol"];
        assert_eq!(asset["enable"], true);
        let scope: Vec<&str> = asset["scope"]
            .as_array()
            .expect("asset scope should be an array")
            .iter()
            .map(|item| item.as_str().expect("asset scope should be a string"))
            .collect();
        assert_eq!(scope, vec!["C:/Claude/home/app/data/apartment/**"]);
    }

    #[test]
    fn default_capability_matches_main_window_and_runtime_plugins() {
        let capability = json(DEFAULT_CAPABILITY);
        assert_eq!(capability["identifier"], "default");

        let windows: Vec<&str> = capability["windows"]
            .as_array()
            .expect("capability windows should be an array")
            .iter()
            .map(|item| item.as_str().expect("window label should be a string"))
            .collect();
        assert_eq!(windows, vec!["main"]);

        let permissions = capability["permissions"]
            .as_array()
            .expect("permissions should be an array");
        for permission in [
            "core:default",
            "core:window:allow-close",
            "core:window:allow-minimize",
            "core:window:allow-maximize",
            "core:window:allow-unmaximize",
            "core:window:allow-start-dragging",
            "core:window:allow-set-title",
            "core:webview:allow-internal-toggle-devtools",
            "shell:default",
        ] {
            assert!(
                permissions.iter().any(|item| item.as_str() == Some(permission)),
                "default capability should include {permission}"
            );
        }

        let http = permissions
            .iter()
            .find(|item| item.get("identifier").and_then(Value::as_str) == Some("http:default"))
            .expect("default capability should include http:default permission");
        let urls: Vec<&str> = http["allow"]
            .as_array()
            .expect("http allow list should be an array")
            .iter()
            .map(|item| item["url"].as_str().expect("allow entry should have a URL"))
            .collect();
        assert_eq!(
            urls,
            vec!["http://*:*/*", "https://*:*/*", "ws://*:*/*", "wss://*:*/*"]
        );
    }
}
