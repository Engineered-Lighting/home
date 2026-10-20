import test from 'node:test';
import { runSetupRailProof } from './fixtures/apartment-setup-rail-proof.mjs';
test('Setup phone light theme retains readable labels, sheet layout and coarse touch targets', { timeout:145000 }, async()=>{
  await runSetupRailProof({phase:'phone'});
});
