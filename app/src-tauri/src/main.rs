// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod native_attestation;
mod native_auth;
mod windows_credentials;

use native_auth::{
    AgentOperation, NativeAgentResponse, NativeAuthState, NativeAuthStatus, NativeLoginStarted,
};
use serde_json::Value;
use std::sync::{Arc, OnceLock};
use tauri::Manager;
use uuid::Uuid;

const AGENT_WINDOW_LABEL: &str = "agent";
static NATIVE_AGENT_SESSION_ID: OnceLock<String> = OnceLock::new();

fn native_agent_session_id() -> String {
    NATIVE_AGENT_SESSION_ID
        .get_or_init(|| Uuid::new_v4().to_string())
        .clone()
}

fn native_authority_window(label: &str) -> bool {
    label == AGENT_WINDOW_LABEL
}

fn require_agent_window(window: &tauri::WebviewWindow) -> Result<(), String> {
    if native_authority_window(window.label()) {
        Ok(())
    } else {
        Err("native_agent_window_not_authorized".to_string())
    }
}

#[tauri::command]
fn open_agent_window(window: tauri::WebviewWindow, app: tauri::AppHandle) -> Result<(), String> {
    if window.label() != "main" && window.label() != AGENT_WINDOW_LABEL {
        return Err("native_agent_window_not_authorized".to_string());
    }
    let agent = app
        .get_webview_window(AGENT_WINDOW_LABEL)
        .ok_or_else(|| "native_agent_window_unavailable".to_string())?;
    agent
        .show()
        .map_err(|_| "native_agent_window_unavailable".to_string())?;
    agent
        .unminimize()
        .map_err(|_| "native_agent_window_unavailable".to_string())?;
    agent
        .set_focus()
        .map_err(|_| "native_agent_window_unavailable".to_string())
}

#[tauri::command]
fn close_agent_window(window: tauri::WebviewWindow, app: tauri::AppHandle) -> Result<(), String> {
    require_agent_window(&window)?;
    window
        .hide()
        .map_err(|_| "native_agent_window_unavailable".to_string())?;
    let main = app
        .get_webview_window("main")
        .ok_or_else(|| "native_main_window_unavailable".to_string())?;
    main.show()
        .map_err(|_| "native_main_window_unavailable".to_string())?;
    main.unminimize()
        .map_err(|_| "native_main_window_unavailable".to_string())?;
    main.set_focus()
        .map_err(|_| "native_main_window_unavailable".to_string())
}

fn validated_uuid(value: String) -> Result<String, String> {
    Uuid::parse_str(&value)
        .map(|parsed| parsed.to_string())
        .map_err(|_| "native_agent_identifier_invalid".to_string())
}

fn validated_text(value: String) -> Result<String, String> {
    let trimmed = value.trim();
    if trimmed.is_empty() || trimmed.len() > 4_096 {
        return Err("native_agent_text_invalid".to_string());
    }
    Ok(trimmed.to_string())
}

fn validated_digest(value: String) -> Result<String, String> {
    if value.len() != 64 || !value.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err("native_agent_preview_digest_invalid".to_string());
    }
    Ok(value.to_ascii_lowercase())
}

fn validated_locality_name(value: String) -> Result<String, String> {
    let value = validated_text(value)?;
    if value.len() > 255 || value.chars().any(char::is_control) {
        return Err("native_agent_locality_invalid".to_string());
    }
    Ok(value)
}

fn validated_locality_geometry(
    latitude: f64,
    longitude: f64,
    radius_m: u32,
) -> Result<(f64, f64, u32), String> {
    if !latitude.is_finite()
        || !longitude.is_finite()
        || !(-90.0..=90.0).contains(&latitude)
        || !(-180.0..=180.0).contains(&longitude)
        || !(1..=50_000).contains(&radius_m)
    {
        return Err("native_agent_locality_invalid".to_string());
    }
    Ok((latitude, longitude, radius_m))
}

async fn run_agent(
    state: Arc<NativeAuthState>,
    operation: AgentOperation,
) -> Result<NativeAgentResponse, String> {
    tauri::async_runtime::spawn_blocking(move || state.agent_request(operation))
        .await
        .map_err(|_| "native_agent_unavailable".to_string())?
}

#[tauri::command]
fn native_auth_status(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, Arc<NativeAuthState>>,
) -> Result<NativeAuthStatus, String> {
    require_agent_window(&window)?;
    Ok(state.status())
}

#[tauri::command]
async fn native_auth_login(
    window: tauri::WebviewWindow,
    app: tauri::AppHandle,
    state: tauri::State<'_, Arc<NativeAuthState>>,
) -> Result<NativeLoginStarted, String> {
    require_agent_window(&window)?;
    let owner = Arc::clone(state.inner());
    tauri::async_runtime::spawn_blocking(move || owner.start_login(app))
        .await
        .map_err(|_| "native_login_failed".to_string())?
}

#[tauri::command]
async fn native_auth_logout(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, Arc<NativeAuthState>>,
) -> Result<NativeAuthStatus, String> {
    require_agent_window(&window)?;
    let owner = Arc::clone(state.inner());
    tauri::async_runtime::spawn_blocking(move || owner.logout())
        .await
        .map_err(|_| "native_logout_failed".to_string())?
}

#[tauri::command]
async fn native_agent_snapshot(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, Arc<NativeAuthState>>,
) -> Result<NativeAgentResponse, String> {
    require_agent_window(&window)?;
    run_agent(Arc::clone(state.inner()), AgentOperation::Snapshot).await
}

#[tauri::command]
async fn native_agent_list_initiatives(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, Arc<NativeAuthState>>,
) -> Result<NativeAgentResponse, String> {
    require_agent_window(&window)?;
    run_agent(
        Arc::clone(state.inner()),
        AgentOperation::ListInitiatives,
    )
    .await
}

#[tauri::command]
async fn native_agent_claim_initiative(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, Arc<NativeAuthState>>,
    initiative_id: String,
) -> Result<NativeAgentResponse, String> {
    require_agent_window(&window)?;
    run_agent(
        Arc::clone(state.inner()),
        AgentOperation::ClaimInitiative {
            initiative_id: validated_uuid(initiative_id)?,
            session_id: native_agent_session_id(),
        },
    )
    .await
}

#[tauri::command]
async fn native_agent_list_private_localities(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, Arc<NativeAuthState>>,
) -> Result<NativeAgentResponse, String> {
    require_agent_window(&window)?;
    run_agent(
        Arc::clone(state.inner()),
        AgentOperation::ListPrivateLocalities,
    )
    .await
}

#[tauri::command]
async fn native_agent_preview_private_locality(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, Arc<NativeAuthState>>,
    canonical_name: String,
    latitude: f64,
    longitude: f64,
    radius_m: u32,
    travel_greeting_eligible: bool,
) -> Result<NativeAgentResponse, String> {
    require_agent_window(&window)?;
    let (latitude, longitude, radius_m) =
        validated_locality_geometry(latitude, longitude, radius_m)?;
    let confirmation_nonce = Uuid::new_v4().to_string();
    let mut response = run_agent(
        Arc::clone(state.inner()),
        AgentOperation::PreviewPrivateLocality {
            canonical_name: validated_locality_name(canonical_name)?,
            latitude,
            longitude,
            radius_m,
            travel_greeting_eligible,
            confirmation_nonce: confirmation_nonce.clone(),
        },
    )
    .await?;
    if (200..300).contains(&response.status) {
        let Value::Object(payload) = &mut response.payload else {
            return Err("native_agent_response_invalid".to_string());
        };
        payload.insert(
            "confirmation_nonce".to_string(),
            Value::String(confirmation_nonce),
        );
    }
    Ok(response)
}

#[tauri::command]
async fn native_agent_confirm_private_locality(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, Arc<NativeAuthState>>,
    canonical_name: String,
    latitude: f64,
    longitude: f64,
    radius_m: u32,
    travel_greeting_eligible: bool,
    confirmation_nonce: String,
    preview_digest: String,
) -> Result<NativeAgentResponse, String> {
    require_agent_window(&window)?;
    let (latitude, longitude, radius_m) =
        validated_locality_geometry(latitude, longitude, radius_m)?;
    run_agent(
        Arc::clone(state.inner()),
        AgentOperation::ConfirmPrivateLocality {
            canonical_name: validated_locality_name(canonical_name)?,
            latitude,
            longitude,
            radius_m,
            travel_greeting_eligible,
            confirmation_nonce: validated_uuid(confirmation_nonce)?,
            preview_digest: validated_digest(preview_digest)?,
        },
    )
    .await
}

#[tauri::command]
async fn native_agent_explain_descriptor(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, Arc<NativeAuthState>>,
    place_id: String,
) -> Result<NativeAgentResponse, String> {
    require_agent_window(&window)?;
    run_agent(
        Arc::clone(state.inner()),
        AgentOperation::ExplainDescriptor {
            place_id: validated_uuid(place_id)?,
        },
    )
    .await
}

#[tauri::command]
async fn native_agent_query_parent_presence(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, Arc<NativeAuthState>>,
    place_id: String,
) -> Result<NativeAgentResponse, String> {
    require_agent_window(&window)?;
    run_agent(
        Arc::clone(state.inner()),
        AgentOperation::QueryParentPresence {
            place_id: validated_uuid(place_id)?,
        },
    )
    .await
}

#[tauri::command]
async fn native_agent_set_preference(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, Arc<NativeAuthState>>,
    key: String,
    enabled: bool,
) -> Result<NativeAgentResponse, String> {
    require_agent_window(&window)?;
    let key = match key.as_str() {
        "location_memory" => "location_memory",
        "travel_greetings" => "travel_greetings",
        _ => return Err("native_agent_preference_invalid".to_string()),
    };
    run_agent(
        Arc::clone(state.inner()),
        AgentOperation::SetPreference {
            key,
            enabled,
            artifact_id: Uuid::new_v4().to_string(),
        },
    )
    .await
}

#[tauri::command]
async fn native_agent_propose_memory(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, Arc<NativeAuthState>>,
    visit_id: String,
    exact_text: String,
) -> Result<NativeAgentResponse, String> {
    require_agent_window(&window)?;
    run_agent(
        Arc::clone(state.inner()),
        AgentOperation::ProposeMemory {
            visit_id: validated_uuid(visit_id)?,
            exact_text: validated_text(exact_text)?,
        },
    )
    .await
}

#[tauri::command]
async fn native_agent_get_memory(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, Arc<NativeAuthState>>,
    transaction_id: String,
) -> Result<NativeAgentResponse, String> {
    require_agent_window(&window)?;
    run_agent(
        Arc::clone(state.inner()),
        AgentOperation::GetMemory {
            transaction_id: validated_uuid(transaction_id)?,
        },
    )
    .await
}

#[tauri::command]
async fn native_agent_confirm_memory(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, Arc<NativeAuthState>>,
    transaction_id: String,
    preview_digest: String,
) -> Result<NativeAgentResponse, String> {
    require_agent_window(&window)?;
    run_agent(
        Arc::clone(state.inner()),
        AgentOperation::ConfirmMemory {
            transaction_id: validated_uuid(transaction_id)?,
            preview_digest: validated_digest(preview_digest)?,
            artifact_id: Uuid::new_v4().to_string(),
        },
    )
    .await
}

#[tauri::command]
async fn native_agent_preview_correction(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, Arc<NativeAuthState>>,
    fact_id: String,
    exact_text: String,
) -> Result<NativeAgentResponse, String> {
    require_agent_window(&window)?;
    run_agent(
        Arc::clone(state.inner()),
        AgentOperation::PreviewCorrection {
            fact_id: validated_uuid(fact_id)?,
            exact_text: validated_text(exact_text)?,
        },
    )
    .await
}

#[tauri::command]
async fn native_agent_confirm_correction(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, Arc<NativeAuthState>>,
    transaction_id: String,
    preview_digest: String,
) -> Result<NativeAgentResponse, String> {
    require_agent_window(&window)?;
    run_agent(
        Arc::clone(state.inner()),
        AgentOperation::ConfirmCorrection {
            transaction_id: validated_uuid(transaction_id)?,
            preview_digest: validated_digest(preview_digest)?,
            artifact_id: Uuid::new_v4().to_string(),
        },
    )
    .await
}

#[tauri::command]
async fn native_agent_preview_retraction(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, Arc<NativeAuthState>>,
    fact_id: String,
) -> Result<NativeAgentResponse, String> {
    require_agent_window(&window)?;
    run_agent(
        Arc::clone(state.inner()),
        AgentOperation::PreviewRetraction {
            fact_id: validated_uuid(fact_id)?,
        },
    )
    .await
}

#[tauri::command]
async fn native_agent_confirm_retraction(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, Arc<NativeAuthState>>,
    transaction_id: String,
    preview_digest: String,
) -> Result<NativeAgentResponse, String> {
    require_agent_window(&window)?;
    run_agent(
        Arc::clone(state.inner()),
        AgentOperation::ConfirmRetraction {
            transaction_id: validated_uuid(transaction_id)?,
            preview_digest: validated_digest(preview_digest)?,
            artifact_id: Uuid::new_v4().to_string(),
        },
    )
    .await
}

#[tauri::command]
async fn native_agent_preview_forget(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, Arc<NativeAuthState>>,
    fact_id: String,
) -> Result<NativeAgentResponse, String> {
    require_agent_window(&window)?;
    run_agent(
        Arc::clone(state.inner()),
        AgentOperation::PreviewForget {
            fact_id: validated_uuid(fact_id)?,
        },
    )
    .await
}

#[tauri::command]
async fn native_agent_confirm_forget(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, Arc<NativeAuthState>>,
    request_id: String,
    preview_digest: String,
) -> Result<NativeAgentResponse, String> {
    require_agent_window(&window)?;
    run_agent(
        Arc::clone(state.inner()),
        AgentOperation::ConfirmForget {
            request_id: validated_uuid(request_id)?,
            preview_digest: validated_digest(preview_digest)?,
        },
    )
    .await
}

fn main() {
    let native_auth = Arc::new(NativeAuthState::new());
    native_auth.start_pending_revocation_retry();
    tauri::Builder::default()
        .manage(native_auth)
        // Single-instance plugin must be registered first so a second launch
        // can focus the existing shell. OAuth callbacks use a Rust loopback
        // listener and never pass authorization codes through argv/webview.
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            if let Some(win) = app.get_webview_window("main") {
                let _ = win.show();
                let _ = win.set_focus();
                let _ = win.unminimize();
            }
        }))
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                if window.label() == AGENT_WINDOW_LABEL {
                    // Keep the authority label bound to the reviewed local
                    // document for the process lifetime. Closing the Agent
                    // surface hides it instead of destroying/recreating or
                    // navigating a privileged webview.
                    api.prevent_close();
                    let _ = window.hide();
                } else if window.label() == "main" {
                    // The hidden Agent window must not keep the process alive
                    // after the main shell closes.
                    if let Some(agent) = window.app_handle().get_webview_window(AGENT_WINDOW_LABEL)
                    {
                        let _ = agent.destroy();
                    }
                }
            }
        })
        .invoke_handler(tauri::generate_handler![
            open_agent_window,
            close_agent_window,
            native_auth_status,
            native_auth_login,
            native_auth_logout,
            native_agent_snapshot,
            native_agent_list_initiatives,
            native_agent_claim_initiative,
            native_agent_list_private_localities,
            native_agent_preview_private_locality,
            native_agent_confirm_private_locality,
            native_agent_explain_descriptor,
            native_agent_query_parent_presence,
            native_agent_set_preference,
            native_agent_propose_memory,
            native_agent_get_memory,
            native_agent_confirm_memory,
            native_agent_preview_correction,
            native_agent_confirm_correction,
            native_agent_preview_retraction,
            native_agent_confirm_retraction,
            native_agent_preview_forget,
            native_agent_confirm_forget,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

#[cfg(test)]
mod tests {
    use super::native_authority_window;
    use serde_json::Value;

    const MAIN_RS: &str = include_str!("main.rs");
    const NATIVE_ATTESTATION_RS: &str = include_str!("native_attestation.rs");
    const NATIVE_AUTH_RS: &str = include_str!("native_auth.rs");
    const WINDOWS_CREDENTIALS_RS: &str = include_str!("windows_credentials.rs");
    const CARGO_TOML: &str = include_str!("../Cargo.toml");
    const TAURI_CONF: &str = include_str!("../tauri.conf.json");
    const DEFAULT_CAPABILITY: &str = include_str!("../capabilities/default.json");

    fn json(text: &str) -> Value {
        serde_json::from_str(text).expect("fixture should parse as JSON")
    }

    #[test]
    fn bearer_ingress_commands_are_removed_and_native_commands_are_narrow() {
        for forbidden in
            ["store", "has", "clear"].map(|verb| format!("auth_{verb}_{}", "ephemeral"))
        {
            assert!(!MAIN_RS.contains(&forbidden));
        }
        for command in [
            "native_auth_status",
            "native_auth_login",
            "native_auth_logout",
            "native_agent_snapshot",
            "native_agent_list_initiatives",
            "native_agent_claim_initiative",
            "native_agent_list_private_localities",
            "native_agent_preview_private_locality",
            "native_agent_confirm_private_locality",
            "native_agent_explain_descriptor",
            "native_agent_query_parent_presence",
            "native_agent_set_preference",
            "native_agent_propose_memory",
            "native_agent_get_memory",
            "native_agent_confirm_memory",
            "native_agent_preview_correction",
            "native_agent_confirm_correction",
            "native_agent_preview_retraction",
            "native_agent_confirm_retraction",
            "native_agent_preview_forget",
            "native_agent_confirm_forget",
        ] {
            assert!(MAIN_RS.contains(command), "missing {command}");
        }
        assert!(!MAIN_RS.contains(&["fn native_agent_", "request"].concat()));
        assert!(MAIN_RS.contains(&["native_agent_list_", "initiatives"].concat()));
        assert!(MAIN_RS.contains(&["native_agent_claim_", "initiative"].concat()));
        assert!(MAIN_RS.contains("NATIVE_AGENT_SESSION_ID"));
    }

    #[test]
    fn native_tokens_never_cross_the_command_return_boundary() {
        assert!(NATIVE_AUTH_RS.contains("Zeroizing<String>"));
        assert!(NATIVE_AUTH_RS.contains("windows_credentials::write"));
        assert!(WINDOWS_CREDENTIALS_RS.contains("CredWriteW"));
        assert!(WINDOWS_CREDENTIALS_RS.contains("CredReadW"));
        assert!(WINDOWS_CREDENTIALS_RS.contains("CredDeleteW"));
        assert!(WINDOWS_CREDENTIALS_RS
            .contains("Global\\\\EngineeredLighting.HomeAgent.InstallationAttestation.v1"));
        assert!(!MAIN_RS.contains(&["access_", "token"].concat()));
        assert!(!MAIN_RS.contains(&["refresh_", "token"].concat()));
        assert!(!MAIN_RS.contains("DPoP"));
        assert!(!MAIN_RS.contains("private_key"));
        assert!(NATIVE_ATTESTATION_RS.contains("InstallationAttestation"));
        assert!(NATIVE_ATTESTATION_RS.contains("windows_credentials::write"));
        assert!(NATIVE_ATTESTATION_RS.contains("Zeroizing<String>"));
        assert!(!NATIVE_ATTESTATION_RS.contains("pub fn sign_request_proof"));
    }

    #[test]
    fn browser_http_plugin_and_universal_network_capability_are_absent() {
        assert!(!CARGO_TOML.contains(&["tauri-plugin", "-http"].concat()));
        assert!(!MAIN_RS.contains(&["tauri_plugin", "_http"].concat()));
        assert!(!DEFAULT_CAPABILITY.contains("http:default"));
        assert!(!DEFAULT_CAPABILITY.contains("http://*:*/*"));
        assert!(!DEFAULT_CAPABILITY.contains("https://*:*/*"));
    }

    #[test]
    fn native_oauth_uses_external_browser_bound_loopback_and_windows_credentials() {
        assert!(!NATIVE_AUTH_RS.contains("code_challenge"));
        assert!(!NATIVE_AUTH_RS.contains("code_verifier"));
        assert!(NATIVE_AUTH_RS.contains("metadata_allows_redirect"));
        assert!(NATIVE_AUTH_RS.contains("constant_time_equal"));
        assert!(NATIVE_AUTH_RS.contains("127.0.0.1"));
        assert!(WINDOWS_CREDENTIALS_RS.contains("ShellExecuteW"));
    }

    #[test]
    fn native_authority_is_denied_to_main_and_allowed_only_to_agent() {
        assert!(!native_authority_window("main"));
        assert!(!native_authority_window("unknown"));
        assert!(native_authority_window("agent"));
        assert!(MAIN_RS.contains("require_agent_window(&window)?"));
    }

    #[test]
    fn tauri_manifest_isolates_agent_from_the_legacy_main_window() {
        let conf = json(TAURI_CONF);
        assert_eq!(conf["productName"], "Home");
        assert_eq!(conf["identifier"], "com.engineeredlighting.home");
        let windows = conf["app"]["windows"].as_array().expect("windows array");
        assert_eq!(windows.len(), 2);
        assert_eq!(windows[0]["label"], "main");
        assert_eq!(windows[1]["label"], "agent");
        assert_eq!(windows[1]["url"], "home-agent/index.html");
        assert_eq!(windows[1]["visible"], false);
    }
}
