# Architecture diagrams

These diagrams are rendered with the Graphviz `dot` layout engine via `@viz-js/viz` (WebAssembly), not a hand-positioned SVG or a Markdown-only diagram.

| Source | Rendered artifact | Scope |
|---|---|---|
| [mcp-architecture.dot](mcp-architecture.dot) | [mcp-architecture.svg](mcp-architecture.svg) | MCP adapter, local web worker, policy/queue DB, Windows capture, transcript store and optional read-only external viewer |
| [policy-lifecycle.dot](policy-lifecycle.dot) | [policy-lifecycle.svg](policy-lifecycle.svg) | Policy IDs, job IDs, per-policy queue cancellation, capture and error handling |

Rebuild from the repository root:

```powershell
npm ci --prefix tools/architecture
npm run render --prefix tools/architecture
```

The Python runtime does not need Node.js. Commit generated SVGs and PNG previews alongside their DOT sources. PNG previews use `@resvg/resvg-js`; Graphviz performs the graph layout. Dashed external-access edges denote the implemented optional path; real Cloudflare configuration and Pickle login have not been verified. No runtime data, local paths, room names or secrets are used in these diagrams.
