import test from 'node:test';
import { runSetupRailProof } from './fixtures/apartment-setup-rail-proof.mjs';
test('Setup rail opens every existing tool in both homes and leaves the room exactly as it was', { timeout:145000 }, async()=>{
  await runSetupRailProof({phase:'rail'});
});
