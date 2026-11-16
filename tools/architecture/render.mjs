import { readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { instance } from '@viz-js/viz';
import { Resvg } from '@resvg/resvg-js';

const directory = new URL('../../docs/diagrams/', import.meta.url);
const viz = await instance();
for (const name of ['mcp-architecture', 'policy-lifecycle']) {
  const dot = await readFile(new URL(`${name}.dot`, directory), 'utf8');
  const result = viz.render(dot, { format: 'svg', engine: 'dot' });
  if (result.status !== 'success') {
    throw new Error(JSON.stringify(result.errors));
  }
  // A description makes the standalone SVG accessible, including on GitHub.
  const svg = result.output.replace(/<svg\s/, '<svg role="img" aria-label="KakaoTalk monitoring architecture" ');
  await writeFile(new URL(`${name}.svg`, directory), svg, 'utf8');
  const raster = new Resvg(svg, { fitTo: { mode: 'width', value: 1800 } });
  await writeFile(new URL(`${name}.png`, directory), raster.render().asPng());
  console.log(`Graphviz ${viz.graphvizVersion}: ${fileURLToPath(new URL(`${name}.svg`, directory))}`);
}
