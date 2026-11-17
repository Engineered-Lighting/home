// Bind Echo's offline capture-only bake to the scan, without live placements.
import fs from 'node:fs';
import path from 'node:path';
import Core from '../../app/src/home-equipment-registration.js';
import { createEchoVirtualLightingBakeDocument } from '../../app/src/home-3d/echo-virtual-lighting.mjs';

const [packDirectory, output] = process.argv.slice(2);
if (!packDirectory || !output) throw new Error('Usage: node write-echo-virtual-lighting-document.mjs PACK OUTPUT');
const read = name => JSON.parse(fs.readFileSync(path.join(packDirectory, name), 'utf8'));
const document = createEchoVirtualLightingBakeDocument({
    pack: read('content-pack.json'),
    frame: read('frame.json'),
    digest: Core.digest,
});
if (!document) throw new Error('Echo capture pack was refused');
fs.writeFileSync(output, JSON.stringify(document), { flag: 'wx', mode: 0o600 });
