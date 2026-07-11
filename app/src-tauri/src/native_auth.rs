use crate::windows_credentials;
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine as _};
use reqwest::blocking::Client;
use reqwest::{Method, StatusCode};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::io::{ErrorKind, Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use tauri::{AppHandle, Emitter};
use url::Url;
use zeroize::{Zeroize, Zeroizing};

const CALLBACK_TIMEOUT: Duration = Duration::from_secs(5 * 60);
const HTTP_TIMEOUT: Duration = Duration::from_secs(12);
const MAX_CALLBACK_BYTES: usize = 8 * 1024;
const MAX_METADATA_BYTES: u64 = 10 * 1024;
const MAX_TOKEN_RESPONSE_BYTES: u64 = 64 * 1024;
const MAX_AGENT_RESPONSE_BYTES: u64 = 1024 * 1024;
const LOCKED_CALLBACK_PORT: u16 = 43_821;

#[derive(Clone)]
struct NativeConfig {
    ha_url: Url,
    agent_bff_url: Url,
    client_id: Url,
    redirect_uri: Url,
    credential_target: String,
    pending_credential_target: String,
}

impl NativeConfig {
    fn from_env() -> Result<Self, String> {
        Self::from_values(
            &std::env::var("HOME_NATIVE_HA_URL").unwrap_or_default(),
            &std::env::var("HOME_NATIVE_AGENT_BFF_URL").unwrap_or_default(),
            &std::env::var("HOME_NATIVE_OAUTH_CLIENT_ID").unwrap_or_default(),
            &std::env::var("HOME_NATIVE_OAUTH_REDIRECT_URI").unwrap_or_default(),
        )
    }

    fn from_values(
        ha_url: &str,
        agent_bff_url: &str,
        client_id: &str,
        redirect_uri: &str,
    ) -> Result<Self, String> {
        let ha_url = secure_origin(ha_url, "native_ha_url_invalid")?;
        let agent_bff_url = secure_origin(agent_bff_url, "native_agent_bff_url_invalid")?;
        let client_id = secure_client_id(client_id)?;
        let redirect_uri = native_redirect(redirect_uri)?;
        let mut digest = Sha256::new();
        digest.update(ha_url.origin().ascii_serialization());
        digest.update(client_id.as_str());
        let target_suffix = digest.finalize();
        let credential_target = format!(
            "EngineeredLighting.HomeAgent/{:02x}{:02x}{:02x}{:02x}{:02x}{:02x}{:02x}{:02x}",
            target_suffix[0],
            target_suffix[1],
            target_suffix[2],
            target_suffix[3],
            target_suffix[4],
            target_suffix[5],
            target_suffix[6],
            target_suffix[7],
        );
        let pending_credential_target = format!("{credential_target}/revocation-pending");
        Ok(Self {
            ha_url,
            agent_bff_url,
            client_id,
            redirect_uri,
            credential_target,
            pending_credential_target,
        })
    }
}

fn secure_origin(input: &str, error: &str) -> Result<Url, String> {
    let value = Url::parse(input).map_err(|_| error.to_string())?;
    if value.scheme() != "https"
        || value.host_str().is_none()
        || !value.username().is_empty()
        || value.password().is_some()
        || value.query().is_some()
        || value.fragment().is_some()
    {
        return Err(error.to_string());
    }
    Ok(value)
}

fn secure_client_id(input: &str) -> Result<Url, String> {
    let value = secure_origin(input, "native_oauth_client_id_invalid")?;
    if value.port().is_some() {
        return Err("native_oauth_client_id_invalid".to_string());
    }
    Ok(value)
}

fn native_redirect(input: &str) -> Result<Url, String> {
    let value = Url::parse(input).map_err(|_| "native_oauth_redirect_invalid".to_string())?;
    if value.scheme() != "http"
        || value.host_str() != Some("127.0.0.1")
        || value.port() != Some(LOCKED_CALLBACK_PORT)
        || value.path() != "/oauth/callback"
        || value.query().is_some()
        || value.fragment().is_some()
        || !value.username().is_empty()
        || value.password().is_some()
    {
        return Err("native_oauth_redirect_invalid".to_string());
    }
    Ok(value)
}

#[derive(Default)]
struct AccessSlot {
    token: Option<Zeroizing<String>>,
    expires_at: Option<Instant>,
}

#[derive(Deserialize)]
struct TokenResponse {
    access_token: String,
    #[serde(default)]
    refresh_token: String,
    expires_in: u64,
}

fn decode_token_response(
    response: reqwest::blocking::Response,
    error: &str,
) -> Result<TokenResponse, String> {
    let mut bytes = Zeroizing::new(Vec::new());
    response
        .take(MAX_TOKEN_RESPONSE_BYTES + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| error.to_string())?;
    if bytes.len() as u64 > MAX_TOKEN_RESPONSE_BYTES {
        return Err(error.to_string());
    }
    serde_json::from_slice(&bytes).map_err(|_| error.to_string())
}

fn login_credential_gate(active: bool, pending: bool) -> Result<(), String> {
    if pending {
        Err("native_logout_revocation_pending".to_string())
    } else if active {
        // Never overwrite an extant refresh token: doing so would orphan the
        // old HA authority because it could no longer be presented to logout.
        Err("native_auth_already_authenticated".to_string())
    } else {
        Ok(())
    }
}

#[derive(Clone, Serialize)]
pub struct NativeAuthStatus {
    pub configured: bool,
    pub login_enabled: bool,
    pub authenticated: bool,
    pub login_in_progress: bool,
    pub reason: Option<String>,
}

#[derive(Clone, Serialize)]
pub struct NativeLoginStarted {
    pub started: bool,
}

#[derive(Clone, Serialize)]
struct NativeAuthEvent {
    authenticated: bool,
    reason: Option<String>,
}

#[derive(Serialize)]
pub struct NativeAgentResponse {
    pub status: u16,
    pub payload: Value,
}

pub enum AgentOperation {
    Snapshot,
    ListInitiatives,
    ClaimInitiative {
        initiative_id: String,
        session_id: String,
    },
    ExplainDescriptor {
        place_id: String,
    },
    QueryParentPresence {
        place_id: String,
    },
    SetPreference {
        key: &'static str,
        enabled: bool,
        artifact_id: String,
    },
    ProposeMemory {
        visit_id: String,
        exact_text: String,
    },
    GetMemory {
        transaction_id: String,
    },
    ConfirmMemory {
        transaction_id: String,
        preview_digest: String,
        artifact_id: String,
    },
    PreviewCorrection {
        fact_id: String,
        exact_text: String,
    },
    ConfirmCorrection {
        transaction_id: String,
        preview_digest: String,
        artifact_id: String,
    },
    PreviewRetraction {
        fact_id: String,
    },
    ConfirmRetraction {
        transaction_id: String,
        preview_digest: String,
        artifact_id: String,
    },
    PreviewForget {
        fact_id: String,
    },
    ConfirmForget {
        request_id: String,
        preview_digest: String,
    },
}

impl AgentOperation {
    fn into_request(self) -> (Method, String, Option<Value>) {
        use serde_json::json;
        match self {
            Self::Snapshot => (Method::GET, "/api/agent/native/v1/snapshot".into(), None),
            Self::ListInitiatives => (Method::GET, "/api/agent/native/v1/initiatives".into(), None),
            Self::ClaimInitiative {
                initiative_id,
                session_id,
            } => (
                Method::POST,
                format!("/api/agent/native/v1/initiatives/{initiative_id}/claim"),
                Some(json!({
                    "session_id": session_id,
                    "surface": "private_tauri",
                })),
            ),
            Self::ExplainDescriptor { place_id } => (
                Method::GET,
                format!("/api/agent/native/v1/places/{place_id}/descriptor-relationship"),
                None,
            ),
            Self::QueryParentPresence { place_id } => (
                Method::GET,
                format!("/api/agent/native/v1/places/{place_id}/parents/current-presence"),
                None,
            ),
            Self::SetPreference {
                key,
                enabled,
                artifact_id,
            } => (
                Method::PUT,
                format!("/api/agent/native/v1/preferences/{key}"),
                Some(json!({
                    "key": key,
                    "enabled": enabled,
                    "confirmation_artifact_id": artifact_id,
                })),
            ),
            Self::ProposeMemory {
                visit_id,
                exact_text,
            } => (
                Method::POST,
                "/api/agent/native/v1/memory-transactions".into(),
                Some(json!({ "visit_id": visit_id, "exact_text": exact_text })),
            ),
            Self::GetMemory { transaction_id } => (
                Method::GET,
                format!("/api/agent/native/v1/memory-transactions/{transaction_id}"),
                None,
            ),
            Self::ConfirmMemory {
                transaction_id,
                preview_digest,
                artifact_id,
            } => (
                Method::POST,
                format!("/api/agent/native/v1/memory-transactions/{transaction_id}/confirm"),
                Some(json!({
                    "preview_digest": preview_digest,
                    "confirmation_artifact_id": artifact_id,
                })),
            ),
            Self::PreviewCorrection {
                fact_id,
                exact_text,
            } => (
                Method::POST,
                format!("/api/agent/native/v1/facts/{fact_id}/correction-preview"),
                Some(json!({ "exact_text": exact_text })),
            ),
            Self::ConfirmCorrection {
                transaction_id,
                preview_digest,
                artifact_id,
            } => (
                Method::POST,
                format!("/api/agent/native/v1/descriptor-corrections/{transaction_id}/confirm"),
                Some(json!({
                    "preview_digest": preview_digest,
                    "confirmation_artifact_id": artifact_id,
                })),
            ),
            Self::PreviewRetraction { fact_id } => (
                Method::POST,
                format!("/api/agent/native/v1/facts/{fact_id}/retraction-preview"),
                Some(json!({})),
            ),
            Self::ConfirmRetraction {
                transaction_id,
                preview_digest,
                artifact_id,
            } => (
                Method::POST,
                format!("/api/agent/native/v1/descriptor-retractions/{transaction_id}/confirm"),
                Some(json!({
                    "preview_digest": preview_digest,
                    "confirmation_artifact_id": artifact_id,
                })),
            ),
            Self::PreviewForget { fact_id } => (
                Method::POST,
                format!("/api/agent/native/v1/facts/{fact_id}/forget-preview"),
                Some(json!({})),
            ),
            Self::ConfirmForget {
                request_id,
                preview_digest,
            } => (
                Method::POST,
                format!("/api/agent/native/v1/erasure-requests/{request_id}/confirm"),
                Some(json!({ "preview_digest": preview_digest })),
            ),
        }
    }
}

pub struct NativeAuthState {
    config: Option<NativeConfig>,
    configuration_reason: Option<String>,
    client: Option<Client>,
    access: Mutex<AccessSlot>,
    logout_lock: Mutex<()>,
    login_in_progress: AtomicBool,
}

impl NativeAuthState {
    pub fn new() -> Self {
        if !cfg!(windows) {
            return Self::unconfigured("native_auth_unsupported");
        }
        let config = match NativeConfig::from_env() {
            Ok(value) => value,
            Err(reason) => return Self::unconfigured(&reason),
        };
        let client = match Client::builder()
            .timeout(HTTP_TIMEOUT)
            .redirect(reqwest::redirect::Policy::none())
            .https_only(true)
            .build()
        {
            Ok(value) => value,
            Err(_) => return Self::unconfigured("native_http_unavailable"),
        };
        Self {
            config: Some(config),
            configuration_reason: None,
            client: Some(client),
            access: Mutex::new(AccessSlot::default()),
            logout_lock: Mutex::new(()),
            login_in_progress: AtomicBool::new(false),
        }
    }

    fn unconfigured(reason: &str) -> Self {
        Self {
            config: None,
            configuration_reason: Some(reason.to_string()),
            client: None,
            access: Mutex::new(AccessSlot::default()),
            logout_lock: Mutex::new(()),
            login_in_progress: AtomicBool::new(false),
        }
    }

    fn ready(&self) -> Result<(&NativeConfig, &Client), String> {
        match (&self.config, &self.client) {
            (Some(config), Some(client)) => Ok((config, client)),
            _ => Err(self
                .configuration_reason
                .clone()
                .unwrap_or_else(|| "native_auth_unconfigured".to_string())),
        }
    }

    pub fn status(&self) -> NativeAuthStatus {
        let configured = self.config.is_some() && self.client.is_some() && cfg!(windows);
        let pending = self
            .config
            .as_ref()
            .and_then(|config| windows_credentials::exists(&config.pending_credential_target).ok())
            .unwrap_or(false);
        let authenticated = !pending
            && self
                .config
                .as_ref()
                .and_then(|config| windows_credentials::exists(&config.credential_target).ok())
                .unwrap_or(false);
        NativeAuthStatus {
            configured,
            login_enabled: configured && !pending,
            authenticated,
            login_in_progress: self.login_in_progress.load(Ordering::SeqCst),
            reason: if pending {
                Some("native_logout_revocation_pending".to_string())
            } else if configured {
                None
            } else {
                self.configuration_reason.clone()
            },
        }
    }

    pub fn start_login(self: &Arc<Self>, app: AppHandle) -> Result<NativeLoginStarted, String> {
        let (config, _) = self.ready()?;
        login_credential_gate(
            windows_credentials::exists(&config.credential_target)?,
            windows_credentials::exists(&config.pending_credential_target)?,
        )?;
        if self.login_in_progress.swap(true, Ordering::SeqCst) {
            return Err("native_login_already_in_progress".to_string());
        }
        let setup = (|| {
            self.verify_client_metadata()?;
            let port = config
                .redirect_uri
                .port()
                .ok_or_else(|| "native_oauth_redirect_invalid".to_string())?;
            let listener = TcpListener::bind(("127.0.0.1", port))
                .map_err(|_| "native_oauth_callback_unavailable".to_string())?;
            listener
                .set_nonblocking(true)
                .map_err(|_| "native_oauth_callback_unavailable".to_string())?;
            let state = Zeroizing::new(random_url_token(32));
            let verifier = Zeroizing::new(random_url_token(48));
            let challenge = pkce_challenge(&verifier);
            let mut authorize = config
                .ha_url
                .join("/auth/authorize")
                .map_err(|_| "native_oauth_authorize_url_invalid".to_string())?;
            authorize
                .query_pairs_mut()
                .append_pair("client_id", config.client_id.as_str())
                .append_pair("redirect_uri", config.redirect_uri.as_str())
                .append_pair("response_type", "code")
                .append_pair("state", &state)
                .append_pair("code_challenge", &challenge)
                .append_pair("code_challenge_method", "S256");
            windows_credentials::open_external(authorize.as_str())?;
            Ok((listener, state, verifier))
        })();
        let (listener, state, verifier) = match setup {
            Ok(value) => value,
            Err(error) => {
                self.login_in_progress.store(false, Ordering::SeqCst);
                return Err(error);
            }
        };
        let owner = Arc::clone(self);
        std::thread::spawn(move || {
            let result = owner.complete_login(listener, &state, &verifier);
            owner.login_in_progress.store(false, Ordering::SeqCst);
            let _ = app.emit(
                "native-auth-changed",
                NativeAuthEvent {
                    authenticated: result.is_ok(),
                    reason: result.err().map(|_| "native_auth_failed".to_string()),
                },
            );
        });
        Ok(NativeLoginStarted { started: true })
    }

    fn verify_client_metadata(&self) -> Result<(), String> {
        let (config, client) = self.ready()?;
        let response = client
            .get(config.client_id.clone())
            .send()
            .map_err(|_| "native_client_metadata_unavailable".to_string())?;
        if !response.status().is_success() {
            return Err("native_client_metadata_unavailable".to_string());
        }
        let mut bytes = Vec::with_capacity(MAX_METADATA_BYTES as usize);
        response
            .take(MAX_METADATA_BYTES)
            .read_to_end(&mut bytes)
            .map_err(|_| "native_client_metadata_unavailable".to_string())?;
        let html = String::from_utf8_lossy(&bytes);
        if !metadata_allows_redirect(&html, config.redirect_uri.as_str()) {
            return Err("native_redirect_metadata_not_approved".to_string());
        }
        Ok(())
    }

    fn complete_login(
        &self,
        listener: TcpListener,
        state: &str,
        verifier: &str,
    ) -> Result<(), String> {
        let code = receive_oauth_callback(listener, state, self.ready()?.0)?;
        self.exchange_authorization_code(&code, verifier)
    }

    fn exchange_authorization_code(&self, code: &str, verifier: &str) -> Result<(), String> {
        let (config, client) = self.ready()?;
        let token_url = config
            .ha_url
            .join("/auth/token")
            .map_err(|_| "native_token_exchange_failed".to_string())?;
        let response = client
            .post(token_url)
            .form(&[
                ("grant_type", "authorization_code"),
                ("code", code),
                ("client_id", config.client_id.as_str()),
                ("redirect_uri", config.redirect_uri.as_str()),
                ("code_verifier", verifier),
            ])
            .send()
            .map_err(|_| "native_token_exchange_failed".to_string())?;
        if !response.status().is_success() {
            return Err("native_token_exchange_failed".to_string());
        }
        let mut payload = decode_token_response(response, "native_token_exchange_failed")?;
        let access = Zeroizing::new(std::mem::take(&mut payload.access_token));
        let refresh = Zeroizing::new(std::mem::take(&mut payload.refresh_token));
        if access.is_empty() || refresh.is_empty() || access.len() > 16_384 || refresh.len() > 2_560
        {
            return Err("native_token_exchange_failed".to_string());
        }
        windows_credentials::write(&config.credential_target, refresh.as_bytes())?;
        self.store_access(access, payload.expires_in)
    }

    fn store_access(&self, token: Zeroizing<String>, expires_in: u64) -> Result<(), String> {
        let mut access = self
            .access
            .lock()
            .map_err(|_| "native_auth_state_unavailable")?;
        access.token = Some(token);
        access.expires_at = Some(Instant::now() + Duration::from_secs(expires_in.clamp(1, 86_400)));
        Ok(())
    }

    fn access_token(&self, force_refresh: bool) -> Result<Zeroizing<String>, String> {
        let (config, client) = self.ready()?;
        let mut access = self
            .access
            .lock()
            .map_err(|_| "native_auth_state_unavailable")?;
        if windows_credentials::exists(&config.pending_credential_target)? {
            access.token = None;
            access.expires_at = None;
            return Err("native_logout_revocation_pending".to_string());
        }
        if !force_refresh
            && access
                .expires_at
                .is_some_and(|deadline| deadline > Instant::now() + Duration::from_secs(60))
        {
            if let Some(token) = &access.token {
                return Ok(Zeroizing::new(token.to_string()));
            }
        }
        access.token = None;
        access.expires_at = None;
        let refresh_bytes = windows_credentials::read(&config.credential_target)?
            .ok_or_else(|| "native_authentication_required".to_string())?;
        let refresh = Zeroizing::new(
            String::from_utf8(refresh_bytes.to_vec())
                .map_err(|_| "native_credential_invalid".to_string())?,
        );
        let token_url = config
            .ha_url
            .join("/auth/token")
            .map_err(|_| "native_token_refresh_failed".to_string())?;
        let response = client
            .post(token_url)
            .form(&[
                ("grant_type", "refresh_token"),
                ("refresh_token", refresh.as_str()),
                ("client_id", config.client_id.as_str()),
            ])
            .send()
            .map_err(|_| "native_token_refresh_failed".to_string())?;
        if matches!(
            response.status(),
            StatusCode::BAD_REQUEST | StatusCode::UNAUTHORIZED | StatusCode::FORBIDDEN
        ) {
            let _ = windows_credentials::delete(&config.credential_target);
            access.token = None;
            access.expires_at = None;
            return Err("native_authentication_required".to_string());
        }
        if !response.status().is_success() {
            return Err("native_token_refresh_failed".to_string());
        }
        let mut payload = decode_token_response(response, "native_token_refresh_failed")?;
        let new_access = Zeroizing::new(std::mem::take(&mut payload.access_token));
        let rotated_refresh = Zeroizing::new(std::mem::take(&mut payload.refresh_token));
        if new_access.is_empty() || new_access.len() > 16_384 {
            return Err("native_token_refresh_failed".to_string());
        }
        if !rotated_refresh.is_empty() {
            if rotated_refresh.len() > 2_560 {
                return Err("native_token_refresh_failed".to_string());
            }
            windows_credentials::write(&config.credential_target, rotated_refresh.as_bytes())?;
        }
        let returned = Zeroizing::new(new_access.to_string());
        access.token = Some(new_access);
        access.expires_at =
            Some(Instant::now() + Duration::from_secs(payload.expires_in.clamp(1, 86_400)));
        Ok(returned)
    }

    pub fn logout(&self) -> Result<NativeAuthStatus, String> {
        self.clear_access()?;
        let _guard = self
            .logout_lock
            .lock()
            .map_err(|_| "native_auth_state_unavailable")?;
        let (config, _) = self.ready()?;
        if !windows_credentials::exists(&config.pending_credential_target)? {
            let Some(active) = windows_credentials::read(&config.credential_target)? else {
                return Ok(self.status());
            };
            // Durable fail-closed transition: pending is written first. From
            // this point onward status/access paths ignore the active slot,
            // including after an abrupt process exit.
            windows_credentials::write(&config.pending_credential_target, &active)?;
            windows_credentials::delete(&config.credential_target)?;
        }
        self.revoke_pending_locked()?;
        Ok(self.status())
    }

    fn revoke_pending_locked(&self) -> Result<(), String> {
        let (config, client) = self.ready()?;
        let pending = windows_credentials::read(&config.pending_credential_target)?
            .ok_or_else(|| "native_logout_revocation_pending".to_string())?;
        let refresh = Zeroizing::new(
            String::from_utf8(pending.to_vec())
                .map_err(|_| "native_credential_invalid".to_string())?,
        );
        let token_url = config
            .ha_url
            .join("/auth/token")
            .map_err(|_| "native_logout_revocation_pending".to_string())?;
        let response = client
            .post(token_url)
            .form(&[("token", refresh.as_str()), ("action", "revoke")])
            .send()
            .map_err(|_| "native_logout_revocation_pending".to_string())?;
        if !response.status().is_success() {
            return Err("native_logout_revocation_pending".to_string());
        }
        windows_credentials::delete(&config.pending_credential_target)?;
        let _ = windows_credentials::delete(&config.credential_target);
        Ok(())
    }

    pub fn start_pending_revocation_retry(self: &Arc<Self>) {
        let Some(config) = self.config.as_ref() else {
            return;
        };
        if !windows_credentials::exists(&config.pending_credential_target).unwrap_or(false) {
            return;
        }
        let owner = Arc::clone(self);
        std::thread::spawn(move || {
            for attempt in 0..8u32 {
                owner.clear_access().ok();
                let result = owner
                    .logout_lock
                    .lock()
                    .map_err(|_| "native_auth_state_unavailable".to_string())
                    .and_then(|_guard| owner.revoke_pending_locked());
                if result.is_ok() {
                    break;
                }
                if attempt < 7 {
                    let delay = 5u64.saturating_mul(2u64.saturating_pow(attempt)).min(300);
                    std::thread::sleep(Duration::from_secs(delay));
                }
            }
        });
    }

    fn clear_access(&self) -> Result<(), String> {
        let mut access = self
            .access
            .lock()
            .map_err(|_| "native_auth_state_unavailable")?;
        access.token = None;
        access.expires_at = None;
        Ok(())
    }

    pub fn agent_request(&self, operation: AgentOperation) -> Result<NativeAgentResponse, String> {
        let (method, path, body) = operation.into_request();
        for attempt in 0..2 {
            let access = self.access_token(attempt > 0)?;
            let response = self.send_agent_once(&method, &path, body.as_ref(), &access)?;
            if response.status != StatusCode::UNAUTHORIZED.as_u16() || attempt == 1 {
                return Ok(response);
            }
            self.clear_access()?;
        }
        Err("native_agent_unavailable".to_string())
    }

    fn send_agent_once(
        &self,
        method: &Method,
        path: &str,
        body: Option<&Value>,
        access: &str,
    ) -> Result<NativeAgentResponse, String> {
        let (config, client) = self.ready()?;
        if !path.starts_with("/api/agent/native/v1/") || path.contains('?') || path.contains('#') {
            return Err("native_agent_route_invalid".to_string());
        }
        let url = config
            .agent_bff_url
            .join(path)
            .map_err(|_| "native_agent_route_invalid".to_string())?;
        let mut request = client
            .request(method.clone(), url)
            .bearer_auth(access)
            .header("Accept", "application/json");
        if let Some(value) = body {
            request = request.json(value);
        }
        let response = request
            .send()
            .map_err(|_| "native_agent_unavailable".to_string())?;
        let status = response.status().as_u16();
        let mut bytes = Vec::new();
        response
            .take(MAX_AGENT_RESPONSE_BYTES + 1)
            .read_to_end(&mut bytes)
            .map_err(|_| "native_agent_unavailable".to_string())?;
        if bytes.len() as u64 > MAX_AGENT_RESPONSE_BYTES {
            bytes.zeroize();
            return Err("native_agent_response_too_large".to_string());
        }
        let payload =
            serde_json::from_slice(&bytes).map_err(|_| "native_agent_response_invalid".to_string());
        bytes.zeroize();
        Ok(NativeAgentResponse {
            status,
            payload: payload?,
        })
    }
}

impl Default for NativeAuthState {
    fn default() -> Self {
        Self::new()
    }
}

fn random_url_token(bytes: usize) -> String {
    let mut value = vec![0u8; bytes];
    for chunk in value.chunks_mut(32) {
        let random: [u8; 32] = rand::random();
        chunk.copy_from_slice(&random[..chunk.len()]);
    }
    let encoded = URL_SAFE_NO_PAD.encode(&value);
    value.zeroize();
    encoded
}

fn pkce_challenge(verifier: &str) -> String {
    URL_SAFE_NO_PAD.encode(Sha256::digest(verifier.as_bytes()))
}

fn constant_time_equal(left: &str, right: &str) -> bool {
    if left.len() != right.len() {
        return false;
    }
    left.as_bytes()
        .iter()
        .zip(right.as_bytes())
        .fold(0u8, |difference, (a, b)| difference | (a ^ b))
        == 0
}

fn receive_oauth_callback(
    listener: TcpListener,
    expected_state: &str,
    config: &NativeConfig,
) -> Result<Zeroizing<String>, String> {
    let deadline = Instant::now() + CALLBACK_TIMEOUT;
    let mut rejected = 0u8;
    while Instant::now() < deadline && rejected < 16 {
        match listener.accept() {
            Ok((mut stream, address)) => {
                if !address.ip().is_loopback() {
                    rejected += 1;
                    continue;
                }
                match callback_from_stream(&mut stream, expected_state, config) {
                    Ok(code) => return Ok(code),
                    Err(_) => {
                        rejected += 1;
                        let _ = write_callback_response(
                            &mut stream,
                            400,
                            "Authentication request rejected.",
                        );
                    }
                }
            }
            Err(error) if error.kind() == ErrorKind::WouldBlock => {
                std::thread::sleep(Duration::from_millis(50));
            }
            Err(_) => return Err("native_oauth_callback_failed".to_string()),
        }
    }
    Err("native_oauth_callback_timeout".to_string())
}

fn callback_from_stream(
    stream: &mut TcpStream,
    expected_state: &str,
    config: &NativeConfig,
) -> Result<Zeroizing<String>, String> {
    stream
        .set_read_timeout(Some(Duration::from_secs(5)))
        .map_err(|_| "native_oauth_callback_failed".to_string())?;
    let mut request = Zeroizing::new(Vec::with_capacity(1024));
    let mut block = [0u8; 1024];
    loop {
        let count = stream
            .read(&mut block)
            .map_err(|_| "native_oauth_callback_failed".to_string())?;
        if count == 0 {
            break;
        }
        request.extend_from_slice(&block[..count]);
        block[..count].zeroize();
        if request.len() > MAX_CALLBACK_BYTES {
            return Err("native_oauth_callback_invalid".to_string());
        }
        if request.windows(4).any(|window| window == b"\r\n\r\n") {
            break;
        }
    }
    block.zeroize();
    let text =
        std::str::from_utf8(&request).map_err(|_| "native_oauth_callback_invalid".to_string())?;
    let line = text
        .lines()
        .next()
        .ok_or_else(|| "native_oauth_callback_invalid".to_string())?;
    let mut parts = line.split_whitespace();
    if parts.next() != Some("GET") {
        return Err("native_oauth_callback_invalid".to_string());
    }
    let target = parts
        .next()
        .ok_or_else(|| "native_oauth_callback_invalid".to_string())?;
    if parts.next() != Some("HTTP/1.1") || parts.next().is_some() {
        return Err("native_oauth_callback_invalid".to_string());
    }
    let parsed = Url::parse(&format!("http://127.0.0.1{target}"))
        .map_err(|_| "native_oauth_callback_invalid".to_string())?;
    if parsed.path() != config.redirect_uri.path() {
        return Err("native_oauth_callback_invalid".to_string());
    }
    let mut state = None;
    let mut code = None;
    for (key, value) in parsed.query_pairs() {
        match key.as_ref() {
            "state" if state.is_none() => state = Some(value.into_owned()),
            "code" if code.is_none() => code = Some(value.into_owned()),
            "state" | "code" => return Err("native_oauth_callback_invalid".to_string()),
            _ => {}
        }
    }
    let mut state =
        Zeroizing::new(state.ok_or_else(|| "native_oauth_callback_invalid".to_string())?);
    if !constant_time_equal(&state, expected_state) {
        return Err("native_oauth_state_invalid".to_string());
    }
    state.zeroize();
    let code = Zeroizing::new(code.ok_or_else(|| "native_oauth_callback_invalid".to_string())?);
    if code.is_empty() || code.len() > 4096 {
        return Err("native_oauth_callback_invalid".to_string());
    }
    write_callback_response(
        stream,
        200,
        "Authentication received. You may return to Home.",
    )?;
    Ok(code)
}

fn write_callback_response(
    stream: &mut TcpStream,
    status: u16,
    message: &str,
) -> Result<(), String> {
    let reason = if status == 200 { "OK" } else { "Bad Request" };
    let body = format!("<!doctype html><meta charset=utf-8><title>Home</title><p>{message}</p>");
    let response = format!(
        "HTTP/1.1 {status} {reason}\r\nContent-Type: text/html; charset=utf-8\r\nContent-Security-Policy: default-src 'none'; style-src 'unsafe-inline'\r\nX-Content-Type-Options: nosniff\r\nCache-Control: no-store\r\nConnection: close\r\nContent-Length: {}\r\n\r\n{body}",
        body.len(),
    );
    stream
        .write_all(response.as_bytes())
        .map_err(|_| "native_oauth_callback_failed".to_string())
}

fn metadata_allows_redirect(html: &str, expected: &str) -> bool {
    let lower = html.to_ascii_lowercase();
    let mut cursor = 0usize;
    while let Some(relative) = lower[cursor..].find("<link") {
        let start = cursor + relative;
        let Some(end_relative) = lower[start..].find('>') else {
            break;
        };
        let end = start + end_relative + 1;
        let tag = &html[start..end];
        let rel = html_attribute(tag, "rel").unwrap_or_default();
        let href = html_attribute(tag, "href").unwrap_or_default();
        if rel
            .split_ascii_whitespace()
            .any(|value| value.eq_ignore_ascii_case("redirect_uri"))
            && href == expected
        {
            return true;
        }
        cursor = end;
    }
    false
}

fn html_attribute(tag: &str, wanted: &str) -> Option<String> {
    let bytes = tag.as_bytes();
    let mut cursor = 0usize;
    while cursor < bytes.len() {
        while cursor < bytes.len()
            && (bytes[cursor].is_ascii_whitespace() || matches!(bytes[cursor], b'<' | b'>' | b'/'))
        {
            cursor += 1;
        }
        let name_start = cursor;
        while cursor < bytes.len()
            && (bytes[cursor].is_ascii_alphanumeric()
                || matches!(bytes[cursor], b'_' | b'-' | b':'))
        {
            cursor += 1;
        }
        if cursor == name_start {
            cursor += 1;
            continue;
        }
        let name = &tag[name_start..cursor];
        while cursor < bytes.len() && bytes[cursor].is_ascii_whitespace() {
            cursor += 1;
        }
        if cursor >= bytes.len() || bytes[cursor] != b'=' {
            continue;
        }
        cursor += 1;
        while cursor < bytes.len() && bytes[cursor].is_ascii_whitespace() {
            cursor += 1;
        }
        if cursor >= bytes.len() {
            return None;
        }
        let (value_start, value_end) = if matches!(bytes[cursor], b'\'' | b'"') {
            let quote = bytes[cursor];
            cursor += 1;
            let start = cursor;
            while cursor < bytes.len() && bytes[cursor] != quote {
                cursor += 1;
            }
            (start, cursor)
        } else {
            let start = cursor;
            while cursor < bytes.len()
                && !bytes[cursor].is_ascii_whitespace()
                && bytes[cursor] != b'>'
            {
                cursor += 1;
            }
            (start, cursor)
        };
        if name.eq_ignore_ascii_case(wanted) {
            return Some(tag[value_start..value_end].to_string());
        }
        cursor = value_end.saturating_add(1);
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn native_config_rejects_non_https_authorities_and_unregistered_callback_shapes() {
        assert!(NativeConfig::from_values(
            "http://ha.test",
            "https://agent.test",
            "https://client.test",
            "http://127.0.0.1:43821/oauth/callback",
        )
        .is_err());
        assert!(NativeConfig::from_values(
            "https://ha.test",
            "https://agent.test",
            "https://client.test",
            "http://localhost:43821/oauth/callback",
        )
        .is_err());
        assert!(NativeConfig::from_values(
            "https://ha.test",
            "https://agent.test",
            "https://client.test",
            "http://127.0.0.1:43821/oauth/callback",
        )
        .is_ok());
    }

    #[test]
    fn login_never_overwrites_active_or_revocation_pending_authority() {
        assert!(login_credential_gate(false, false).is_ok());
        assert_eq!(
            login_credential_gate(true, false).unwrap_err(),
            "native_auth_already_authenticated"
        );
        assert_eq!(
            login_credential_gate(false, true).unwrap_err(),
            "native_logout_revocation_pending"
        );
        assert_eq!(
            login_credential_gate(true, true).unwrap_err(),
            "native_logout_revocation_pending"
        );
    }

    #[test]
    fn client_metadata_requires_the_exact_redirect_link_in_any_attribute_order() {
        let redirect = "http://127.0.0.1:43821/oauth/callback";
        assert!(metadata_allows_redirect(
            &format!("<html><link href=\"{redirect}\" data-x='1' rel='redirect_uri'></html>"),
            redirect,
        ));
        assert!(!metadata_allows_redirect(
            "<link rel='redirect_uri' href='http://127.0.0.1:9999/oauth/callback'>",
            redirect,
        ));
        assert!(!metadata_allows_redirect(
            &format!("<meta content='{redirect}'>"),
            redirect,
        ));
    }

    #[test]
    fn pkce_is_s256_and_state_comparison_is_length_sensitive() {
        assert_eq!(
            pkce_challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"),
            "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
        );
        assert!(constant_time_equal("same-state", "same-state"));
        assert!(!constant_time_equal("same-state", "other-state"));
        assert!(!constant_time_equal("short", "shorter"));
    }

    #[test]
    fn agent_operations_compile_only_fixed_native_paths() {
        let (method, path, _) = AgentOperation::Snapshot.into_request();
        assert_eq!(method, Method::GET);
        assert_eq!(path, "/api/agent/native/v1/snapshot");
        let (_, path, _) = AgentOperation::PreviewForget {
            fact_id: "id".into(),
        }
        .into_request();
        assert_eq!(path, "/api/agent/native/v1/facts/id/forget-preview");
        assert!(!path.contains("initiatives"));
    }
}
