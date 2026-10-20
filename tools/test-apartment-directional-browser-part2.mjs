// Directional lighting, part 2 of 2: stages 1-2 and 7-13 of the authored
// real-shell proof (tools/fixtures/apartment-directional-browser-proof.mjs),
// on its own touched shard so neither part runs near the 145 s deadline.
import test from 'node:test';
import { runDirectionalBrowserProof } from './fixtures/apartment-directional-browser-proof.mjs';

test('real authored directional controls preserve native input, aim and output boundaries (part 2: stages 1-2, 7-13)', { timeout: 145000 },
  () => runDirectionalBrowserProof({ part: 2 }));
