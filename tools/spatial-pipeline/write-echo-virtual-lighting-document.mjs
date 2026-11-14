// Use the browser's fixture selection for the offline Echo lighting bake.
import fs from 'node:fs';
import path from 'node:path';
import Core from '../../app/src/home-equipment-registration.js';
import { createEchoVirtualLightingDocument } from '../../app/src/home-3d/echo-virtual-lighting.mjs';

const [packDirectory, output] = process.argv.slice(2);
if (!packDirectory || !output) throw new Error('Usage: node write-echo-virtual-lighting-document.mjs PACK OUTPUT');
const read = name => JSON.parse(fs.readFileSync(path.join(packDirectory, name), 'utf8'));
const document = createEchoVirtualLightingDocument({
    pack: read('content-pack.json'),
    frame: read('frame.json'),
    devices: read('seed-model.json').devices,
    digest: Core.digest,
});
if (!document) throw new Error('Echo modeled fixture data was refused');
fs.writeFileSync(output, JSON.stringify(document), { flag: 'wx', mode: 0o600 });
console.log('Modeled fixture count:', document.equipment.filter(item => item.kind === 'fixture').length);
