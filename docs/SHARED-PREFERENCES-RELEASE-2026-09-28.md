# Shared preferences release receipt — 2026-09-28

Status: prepared and imported; **not activated or live accepted**.

Release source: `e7a7d62de560a200f641281f19db1b4526f5b390` (merged PR #144).
Hosted web run `36424600309` and Core/PostgreSQL run `36424600299` succeeded.
The workstation verified every artifact subject against the exact main commit,
repository, signer workflow and GitHub-hosted runner. Exact manifests and archive
checksums passed locally and again in encrypted host storage.

Imported images were verified by the existing cross-store identity verifier:

| Component | Immutable local image ID |
| --- | --- |
| Core | `sha256:b8b0975453a00299e8b1c21f1877927231797bd7845358633952ddf4817fabc8` |
| BFF | `sha256:5091abb730b2058ba0b2ffc341d65acfd9ba07c77eec11ec47a4b907548ae031` |
| Origin | `sha256:553e617844190b3b26752b322cd4ad8e866c6bbbf474ef1304372197ac181f52` |

The signed config digests differ from these local containerd manifest digests;
the verifier checked source tags, config bytes, revision labels and rootfs IDs.
The root-owned receipt is under the release's encrypted image-bundle directory
as `import-identity-receipt.json`. Deployment tags and running images were not
changed. Existing Core, BFF and Origin remained healthy after import.

All five prepared listener profiles (two preference, two identity, one linking
coordinator) passed their actual image's profile loader and TLS key/certificate
loading as UID 10001. Checks ran sequentially in network-disabled, read-only,
resource-limited containers. No database connection or listener startup occurred.

Pre-operation host inspection at 13:07 UTC found required containers healthy,
59 GiB free on the encrypted volume, and no matching new kernel faults since
00:00 UTC. Known historical observer and earlier backup-attempt failures were
retained. The other chat reported no deployment collision; live Lab changes
remain outside this release's replacement scope.

## Remaining activation requirements

- Resolve compatible Core rollback at schema 0047 before migration. Archived
  schema-0031 images alone do not establish that compatibility.
- Resolve the restore evidence contract: backup `20260928-123053F` restored and
  passed offline checksums, but the legacy receipt writer rejected revision
  0031 because it accepts only 0006a. This is not a passing legacy receipt.
- Complete scoped migration, dormant-role activation, source registration,
  BFF TLS integration, and backup coverage for new journals and keys.
- Provision the distinct Victoria browser boundary, then obtain fresh owner
  authentication and explicit linking/sharing consent.
- Verify warm preference, cross-home retrieval, neutral correction, retrieval
  after reload, and durable forgetting in authenticated Home.

Lighting and travel defaults remain inactive milestones. Artifact and profile
validation do not constitute deployed shared-preference acceptance.
