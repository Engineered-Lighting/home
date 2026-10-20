// Directional lighting, part 1 of 2: stages 1-6 of the authored real-shell
// proof (tools/fixtures/apartment-directional-browser-proof.mjs). Part 2 runs
// stages 1-2 and 7-13 on another touched shard.
import test from 'node:test';
import { runDirectionalBrowserProof } from './fixtures/apartment-directional-browser-proof.mjs';

test('real authored directional controls preserve native input, aim and output boundaries (part 1: stages 1-6)', { timeout: 145000 },
  () => runDirectionalBrowserProof({ part: 1 }));
