import test from 'node:test';
import { runSetupRailProof } from './fixtures/apartment-setup-rail-proof.mjs';
test('Setup preserves default, collapsed and reopened camera dock presentations', { timeout:145000 }, async()=>{
  await runSetupRailProof({phase:'dock-collapsed'});
});
