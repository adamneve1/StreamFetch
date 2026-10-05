// Self-host only the motion libraries used by the workspace. No CDN/runtime bundler.
import { copyFile, mkdir } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
const require = createRequire(import.meta.url);
const root = dirname(require.resolve('gsap/package.json'));
const destination = new URL('../app/static/vendor/', import.meta.url);
await mkdir(destination, { recursive: true });
for (const file of [
  'gsap.min.js', 'gsap.min.js.map',
  'Flip.min.js', 'Flip.min.js.map',
  'MorphSVGPlugin.min.js', 'MorphSVGPlugin.min.js.map',
  'ScrollTrigger.min.js', 'ScrollTrigger.min.js.map',
  'ScrollSmoother.min.js', 'ScrollSmoother.min.js.map',
]) {
  await copyFile(join(root, 'dist', file), new URL(file, destination));
}
// npm's GSAP package provides licensing information in its README and JS headers.
await copyFile(join(root, 'README.md'), new URL('GSAP-README.md', destination));
