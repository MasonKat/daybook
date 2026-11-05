# Vendored KaTeX (live-v2)

- Version: **0.16.11**, from the npm package `katex@0.16.11` (`dist/katex.min.js`, `dist/katex.min.css`, `dist/fonts/*.woff2`).
- License: MIT (Khan Academy and contributors), see `LICENSE.txt` next to this file.
- Self-hosted: no CDN at runtime. The CSS keeps only the woff2 font sources (every supported phone browser reads woff2).
- Loaded only by the student page while an AMP Live room shows a question (`static/live_phone.js`), never by other pages.
- To upgrade: `npm pack katex@<version>`, copy the same files, strip the woff/ttf `url()` alternatives from the CSS, update this file.
