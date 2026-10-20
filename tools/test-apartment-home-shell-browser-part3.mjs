// Authored Apartment shell, part 3 of 3: the classic URL override and the
// classic card from a fresh page (tools/fixtures/apartment-home-shell-browser-proof.mjs).
import test from 'node:test';
import { runShellBrowserProof } from './fixtures/apartment-home-shell-browser-proof.mjs';

test('unified shell keeps the real room, private controls and one chat controller through responsive navigation (part 3)', { timeout: 145000 },
  () => runShellBrowserProof({ part: 3 }));
