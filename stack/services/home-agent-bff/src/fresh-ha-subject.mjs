import { QualifiedHaAuth } from "./qualified-ha-auth.mjs";
import { HaRevocationOutbox } from "./ha-revocation-outbox.mjs";

// Internal orchestration after the HA login ceremony. This yields an issuer-
// qualified subject, not an owner link or a governed authentication proof.
export async function authenticateFreshHaCode({ auth, outbox, code }) {
  if (!(auth instanceof QualifiedHaAuth) || !(outbox instanceof HaRevocationOutbox) ||
      outbox.binding !== auth.binding) throw new Error("fresh_auth_binding_mismatch");
  let tokens, id;
  try {
    tokens = await auth.exchange(code);
    id = outbox.retain(tokens.refreshToken, { verifying: true });
    const principal = await auth.verify(tokens.accessToken);
    if (!outbox.finishVerification(id)) throw new Error();
    if (!await outbox.revoke(id)) throw new Error();
    return principal;
  } catch {
    if (id) outbox.abandonVerification(id);
    // No immediate duplicate revoke: retained records belong to outbox recovery.
    if (!id && tokens?.refreshToken?.length) {
      // HA issuance and local retention cannot be atomic. If the first durable
      // write fails, attempt revocation and never produce a verified subject.
      await auth.revoke(tokens.refreshToken).catch(() => {});
    }
    throw new Error("fresh_auth_subject_unavailable");
  } finally {
    tokens?.accessToken.fill(0);
    tokens?.refreshToken.fill(0);
  }
}
