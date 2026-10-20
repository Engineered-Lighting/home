import test from 'node:test';
import { runSetupRailProof } from './fixtures/apartment-setup-rail-proof.mjs';
test('Setup preserves every camera dock presentation across entering and leaving Setup', { timeout:145000 }, async()=>{
  await runSetupRailProof({phase:'dock'});
});
