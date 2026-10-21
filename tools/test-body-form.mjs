import {register} from 'node:module';
register('./fixtures/body-form-three-loader.mjs',import.meta.url);
await import('../app/src/home-3d/body-form-model.test.mjs');
await import('../app/src/home-3d/body-form-renderer.test.mjs');
await import('../app/src/home-3d/fast-tracking-layer.test.mjs');
