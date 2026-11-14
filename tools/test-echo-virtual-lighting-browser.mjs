import test from 'node:test';
import { runLitSplatsBrowserProof } from './fixtures/apartment-lit-splats-browser-proof.mjs';

test('Echo virtual lighting changes Mesh, Photo and Cloud pixels and isolates aiming from Home Assistant', { timeout: 150000 },
    ({ signal }) => runLitSplatsBrowserProof({ part: 2, site: 'echo', signal }));
