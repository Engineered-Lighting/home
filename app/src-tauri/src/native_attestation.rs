use crate::windows_credentials;
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine as _};
use p256::ecdsa::{signature::Signer, Signature, SigningKey};
use rand_core::OsRng;
use serde::Serialize;
use sha2::{Digest, Sha256};
use url::Url;
use uuid::{Uuid, Variant, Version};
use zeroize::{Zeroize, Zeroizing};

const CREDENTIAL_VERSION: u8 = 1;
const CREDENTIAL_BYTES: usize = 1 + 16 + 32;
const INSTALLATION_CREDENTIAL_TARGET: &str =
    "EngineeredLighting.HomeAgent/installation-attestation/v1";
const NATIVE_PATH_PREFIX: &str = "/api/agent/native/v1/";
const CHALLENGE_PATH: &str = "/api/agent/native/v1/attestation/challenge";
const PROOF_TYPE: &str = "home-agent-native+jwt";

#[derive(Clone, Serialize)]
pub struct PublicJwk {
    pub kty: &'static str,
    pub crv: &'static str,
    pub x: String,
    pub y: String,
    pub kid: String,
}

pub struct InstallationAttestation {
    installation_id: Uuid,
    signing_key: SigningKey,
    audience_origin: String,
}

#[derive(Serialize)]
struct ProofHeader<'a> {
    alg: &'static str,
    typ: &'static str,
    kid: &'a str,
}

#[derive(Serialize)]
struct ProofClaims<'a> {
    v: u8,
    jti: String,
    nonce: &'a str,
    iat: u64,
    exp: u64,
    htm: &'a str,
    htu: &'a str,
    path: &'a str,
    body_sha256: &'a str,
    ath: &'a str,
}

impl InstallationAttestation {
    pub fn load_or_create(audience: &Url) -> Result<Self, String> {
        let audience_origin = exact_audience_origin(audience)?;
        windows_credentials::with_installation_lock(|| {
            if let Some(blob) = windows_credentials::read(INSTALLATION_CREDENTIAL_TARGET)? {
                // A malformed record is never deleted or silently rotated.
                // Recovery requires revoking the registry binding, explicitly
                // deleting this credential, and enrolling the replacement key.
                return Self::decode_credential(&blob, &audience_origin);
            }

            let created = Self::generate(&audience_origin);
            let blob = created.encode_credential();
            windows_credentials::write(INSTALLATION_CREDENTIAL_TARGET, &blob)?;

            // Re-read the durable record before using it. This both verifies
            // persistence and makes the credential the single authority.
            let persisted = windows_credentials::read(INSTALLATION_CREDENTIAL_TARGET)?
                .ok_or_else(|| "native_attestation_credential_unavailable".to_string())?;
            Self::decode_credential(&persisted, &audience_origin)
        })
    }

    pub fn installation_id(&self) -> String {
        self.installation_id.to_string()
    }

    pub fn public_jwk(&self) -> PublicJwk {
        let point = self.signing_key.verifying_key().to_encoded_point(false);
        let x = point.x().expect("uncompressed P-256 point has x");
        let y = point.y().expect("uncompressed P-256 point has y");
        PublicJwk {
            kty: "EC",
            crv: "P-256",
            x: URL_SAFE_NO_PAD.encode(x),
            y: URL_SAFE_NO_PAD.encode(y),
            kid: self.installation_id(),
        }
    }

    pub(crate) fn sign_request_proof(
        &self,
        method: &str,
        path: &str,
        request_url: &Url,
        body_sha256: &str,
        access_token: &str,
        nonce: &str,
        issued_at: u64,
        expires_at: u64,
    ) -> Result<Zeroizing<String>, String> {
        validate_proof_inputs(
            method,
            path,
            request_url,
            &self.audience_origin,
            body_sha256,
            access_token,
            nonce,
            issued_at,
            expires_at,
        )?;
        let kid = self.installation_id();
        let header = serde_json::to_vec(&ProofHeader {
            alg: "ES256",
            typ: PROOF_TYPE,
            kid: &kid,
        })
        .map(Zeroizing::new)
        .map_err(|_| "native_attestation_proof_failed".to_string())?;
        let ath = Zeroizing::new(sha256_urlsafe(access_token.as_bytes()));
        let claims = serde_json::to_vec(&ProofClaims {
            v: 1,
            jti: Uuid::new_v4().to_string(),
            nonce,
            iat: issued_at,
            exp: expires_at,
            htm: method,
            htu: request_url.as_str(),
            path,
            body_sha256,
            ath: ath.as_str(),
        })
        .map(Zeroizing::new)
        .map_err(|_| "native_attestation_proof_failed".to_string())?;
        let encoded_header = Zeroizing::new(URL_SAFE_NO_PAD.encode(&header));
        let encoded_claims = Zeroizing::new(URL_SAFE_NO_PAD.encode(&claims));
        let mut signing_input = Zeroizing::new(format!(
            "{}.{}",
            encoded_header.as_str(),
            encoded_claims.as_str()
        ));
        let signature: Signature = self.signing_key.sign(signing_input.as_bytes());
        let encoded_signature = Zeroizing::new(URL_SAFE_NO_PAD.encode(signature.to_bytes()));
        let proof = Zeroizing::new(format!(
            "{}.{}",
            signing_input.as_str(),
            encoded_signature.as_str()
        ));
        signing_input.zeroize();
        Ok(proof)
    }

    fn generate(audience_origin: &str) -> Self {
        Self {
            installation_id: Uuid::new_v4(),
            signing_key: SigningKey::random(&mut OsRng),
            audience_origin: audience_origin.to_string(),
        }
    }

    fn encode_credential(&self) -> Zeroizing<Vec<u8>> {
        let mut blob = Zeroizing::new(Vec::with_capacity(CREDENTIAL_BYTES));
        blob.push(CREDENTIAL_VERSION);
        blob.extend_from_slice(self.installation_id.as_bytes());
        blob.extend_from_slice(&self.signing_key.to_bytes());
        blob
    }

    fn decode_credential(blob: &[u8], audience_origin: &str) -> Result<Self, String> {
        if blob.len() != CREDENTIAL_BYTES || blob[0] != CREDENTIAL_VERSION {
            return Err("native_attestation_credential_invalid".to_string());
        }
        let installation_id = Uuid::from_slice(&blob[1..17])
            .map_err(|_| "native_attestation_credential_invalid".to_string())?;
        if installation_id.get_version() != Some(Version::Random)
            || installation_id.get_variant() != Variant::RFC4122
        {
            return Err("native_attestation_credential_invalid".to_string());
        }
        let mut scalar = Zeroizing::new([0u8; 32]);
        scalar.copy_from_slice(&blob[17..]);
        let signing_key = SigningKey::from_slice(scalar.as_ref())
            .map_err(|_| "native_attestation_credential_invalid".to_string())?;
        scalar.zeroize();
        Ok(Self {
            installation_id,
            signing_key,
            audience_origin: audience_origin.to_string(),
        })
    }
}

pub fn sha256_urlsafe(bytes: &[u8]) -> String {
    URL_SAFE_NO_PAD.encode(Sha256::digest(bytes))
}

fn validate_proof_inputs(
    method: &str,
    path: &str,
    request_url: &Url,
    audience_origin: &str,
    body_sha256: &str,
    access_token: &str,
    nonce: &str,
    issued_at: u64,
    expires_at: u64,
) -> Result<(), String> {
    if !matches!(method, "GET" | "POST" | "PUT")
        || !path.starts_with(NATIVE_PATH_PREFIX)
        || path == CHALLENGE_PATH
        || path.contains('?')
        || path.contains('#')
        || request_url.scheme() != "https"
        || !request_url.username().is_empty()
        || request_url.password().is_some()
        || request_url.query().is_some()
        || request_url.fragment().is_some()
        || request_url.path() != path
        || request_url.origin().ascii_serialization() != audience_origin
    {
        return Err("native_attestation_request_invalid".to_string());
    }
    let body_digest = URL_SAFE_NO_PAD
        .decode(body_sha256)
        .map_err(|_| "native_attestation_request_invalid".to_string())?;
    let nonce_bytes = URL_SAFE_NO_PAD
        .decode(nonce)
        .map_err(|_| "native_attestation_challenge_invalid".to_string())?;
    if body_sha256.len() != 43
        || body_digest.len() != 32
        || URL_SAFE_NO_PAD.encode(&body_digest) != body_sha256
        || nonce.len() != 43
        || nonce_bytes.len() != 32
        || URL_SAFE_NO_PAD.encode(&nonce_bytes) != nonce
        || access_token.is_empty()
        || expires_at <= issued_at
        || expires_at - issued_at != 60
    {
        return Err("native_attestation_challenge_invalid".to_string());
    }
    Ok(())
}

fn exact_audience_origin(audience: &Url) -> Result<String, String> {
    if audience.scheme() != "https"
        || audience.host_str().is_none()
        || !audience.username().is_empty()
        || audience.password().is_some()
        || audience.path() != "/"
        || audience.query().is_some()
        || audience.fragment().is_some()
    {
        return Err("native_attestation_audience_invalid".to_string());
    }
    Ok(audience.origin().ascii_serialization())
}

#[cfg(test)]
mod tests {
    use super::*;
    use p256::ecdsa::signature::Verifier;
    use serde_json::Value;

    const AUDIENCE_ORIGIN: &str = "https://agent.test";

    fn request_url(path: &str) -> Url {
        Url::parse(AUDIENCE_ORIGIN).unwrap().join(path).unwrap()
    }

    #[test]
    fn credential_round_trip_preserves_installation_and_key() {
        let original = InstallationAttestation::generate(AUDIENCE_ORIGIN);
        let encoded = original.encode_credential();
        assert_eq!(encoded.len(), CREDENTIAL_BYTES);
        let restored =
            InstallationAttestation::decode_credential(&encoded, AUDIENCE_ORIGIN).unwrap();
        assert_eq!(restored.installation_id(), original.installation_id());
        assert_eq!(restored.public_jwk().x, original.public_jwk().x);
        assert_eq!(restored.public_jwk().y, original.public_jwk().y);
    }

    #[test]
    fn corrupt_or_non_v4_credentials_fail_closed() {
        for blob in [vec![], vec![CREDENTIAL_VERSION; CREDENTIAL_BYTES - 1]] {
            assert!(InstallationAttestation::decode_credential(&blob, AUDIENCE_ORIGIN).is_err());
        }
        let original = InstallationAttestation::generate(AUDIENCE_ORIGIN);
        let mut encoded = original.encode_credential();
        encoded[0] = 2;
        assert!(InstallationAttestation::decode_credential(&encoded, AUDIENCE_ORIGIN).is_err());
        encoded[0] = CREDENTIAL_VERSION;
        encoded[7] &= 0x0f;
        assert!(InstallationAttestation::decode_credential(&encoded, AUDIENCE_ORIGIN).is_err());
        let mut invalid_variant = original.encode_credential();
        invalid_variant[9] = (invalid_variant[9] & 0x1f) | 0xe0;
        assert!(
            InstallationAttestation::decode_credential(&invalid_variant, AUDIENCE_ORIGIN).is_err()
        );
    }

    #[test]
    fn compact_es256_proof_has_exact_public_contract_and_valid_raw_signature() {
        let installation = InstallationAttestation::generate(AUDIENCE_ORIGIN);
        let nonce = URL_SAFE_NO_PAD.encode([7u8; 32]);
        let body_sha256 = sha256_urlsafe(br#"{"enabled":true}"#);
        let proof = installation
            .sign_request_proof(
                "PUT",
                "/api/agent/native/v1/preferences/location_memory",
                &request_url("/api/agent/native/v1/preferences/location_memory"),
                &body_sha256,
                "secret-access-token",
                &nonce,
                1_700_000_000,
                1_700_000_060,
            )
            .unwrap();
        let segments: Vec<&str> = proof.split('.').collect();
        assert_eq!(segments.len(), 3);
        let header: Value =
            serde_json::from_slice(&URL_SAFE_NO_PAD.decode(segments[0]).unwrap()).unwrap();
        let claims: Value =
            serde_json::from_slice(&URL_SAFE_NO_PAD.decode(segments[1]).unwrap()).unwrap();
        assert_eq!(header["alg"], "ES256");
        assert_eq!(header["typ"], PROOF_TYPE);
        assert_eq!(header["kid"], installation.installation_id());
        assert_eq!(claims["v"], 1);
        assert_eq!(claims["nonce"], nonce);
        assert_eq!(claims["iat"], 1_700_000_000u64);
        assert_eq!(claims["exp"], 1_700_000_060u64);
        assert_eq!(claims["htm"], "PUT");
        assert_eq!(
            claims["htu"],
            "https://agent.test/api/agent/native/v1/preferences/location_memory"
        );
        assert_eq!(claims["body_sha256"], body_sha256);
        assert_eq!(claims["ath"], sha256_urlsafe(b"secret-access-token"));
        Uuid::parse_str(claims["jti"].as_str().unwrap()).unwrap();

        let raw_signature = URL_SAFE_NO_PAD.decode(segments[2]).unwrap();
        assert_eq!(raw_signature.len(), 64);
        let signature = Signature::from_slice(&raw_signature).unwrap();
        let signing_input = format!("{}.{}", segments[0], segments[1]);
        installation
            .signing_key
            .verifying_key()
            .verify(signing_input.as_bytes(), &signature)
            .unwrap();
    }

    #[test]
    fn proof_rejects_noncanonical_or_overlong_challenges_and_challenge_path() {
        let installation = InstallationAttestation::generate(AUDIENCE_ORIGIN);
        let body_sha256 = sha256_urlsafe(b"");
        let nonce = URL_SAFE_NO_PAD.encode([1u8; 32]);
        assert!(installation
            .sign_request_proof(
                "GET",
                CHALLENGE_PATH,
                &request_url(CHALLENGE_PATH),
                &body_sha256,
                "token",
                &nonce,
                10,
                11,
            )
            .is_err());
        assert!(installation
            .sign_request_proof(
                "GET",
                "/api/agent/native/v1/snapshot",
                &request_url("/api/agent/native/v1/snapshot"),
                &body_sha256,
                "token",
                &nonce,
                10,
                69,
            )
            .is_err());
        assert!(installation
            .sign_request_proof(
                "GET",
                "/api/agent/native/v1/snapshot",
                &request_url("/api/agent/native/v1/snapshot"),
                &body_sha256,
                "token",
                &format!("{nonce}="),
                10,
                11,
            )
            .is_err());
        assert!(installation
            .sign_request_proof(
                "GET",
                "/api/agent/native/v1/snapshot",
                &request_url("/api/agent/native/v1/snapshot"),
                &body_sha256,
                "token",
                &nonce,
                10,
                71,
            )
            .is_err());
    }

    #[test]
    fn proof_refuses_a_well_formed_request_url_from_the_wrong_origin() {
        let installation = InstallationAttestation::generate(AUDIENCE_ORIGIN);
        let evil = Url::parse("https://relay.test/api/agent/native/v1/snapshot").unwrap();
        assert!(installation
            .sign_request_proof(
                "GET",
                "/api/agent/native/v1/snapshot",
                &evil,
                &sha256_urlsafe(b""),
                "token",
                &URL_SAFE_NO_PAD.encode([9u8; 32]),
                10,
                70,
            )
            .is_err());
        assert!(
            exact_audience_origin(&Url::parse("https://agent.test/untrusted-base").unwrap())
                .is_err()
        );
    }
}
