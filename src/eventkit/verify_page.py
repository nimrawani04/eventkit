"""Self-contained offline certificate verification page."""

from __future__ import annotations

import json
from html import escape
from typing import Any


def render_verify_page(manifest: dict[str, Any]) -> bytes:
    """Render a network-free page that checks code hashes with Web Crypto."""

    payload = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    # This is a raw-text script element, so HTML entities would remain
    # literal text and break JSON.parse.  Escape markup delimiters as JSON
    # unicode escapes instead, which keeps the document safe and parseable.
    escaped_payload = (
        payload.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )
    title = escape(str(manifest.get("event_name", "Event certificate verification")), quote=True)
    document = f'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Verify certificate — {title}</title>
  <style>
    body {{ max-width: 42rem; margin: 4rem auto; padding: 0 1rem; font-family: system-ui, sans-serif; color: #17202a; }}
    h1 {{ color: #1e3a5f; }} input {{ width: 100%; box-sizing: border-box; padding: .75rem; font-size: 1.1rem; }}
    button {{ margin-top: 1rem; padding: .7rem 1.2rem; }} #result {{ margin-top: 1.5rem; font-weight: 700; }}
    .valid {{ color: #176b3a; }} .invalid {{ color: #b42318; }}
  </style>
</head>
<body>
  <h1>Verify certificate</h1>
  <p>{title}</p>
  <input id="code" autocomplete="off" placeholder="XXXX-XXXX-XXXX">
  <button id="verify">Verify offline</button>
  <div id="result" role="status"></div>
  <script type="application/json" id="manifest">{escaped_payload}</script>
  <script>
    const manifest = JSON.parse(document.getElementById('manifest').textContent);
    const result = document.getElementById('result');
    function canonical(value) {{ return value.replace(/[\\s-]/g, '').toUpperCase().replace(/[ILO]/g, c => c === 'O' ? '0' : '1'); }}
    async function digest(value) {{
      const bytes = new TextEncoder().encode(value);
      const hash = await crypto.subtle.digest('SHA-256', bytes);
      return Array.from(new Uint8Array(hash)).map(b => b.toString(16).padStart(2, '0')).join('');
    }}
    document.getElementById('verify').addEventListener('click', async () => {{
      const code = canonical(document.getElementById('code').value);
      const hash = await digest(code);
      const entry = manifest.certificates.find(item => item.code_sha256 === hash);
      result.className = entry ? 'valid' : 'invalid';
      result.textContent = entry ? `VALID — ${{entry.name || 'Certificate holder'}} — ${{entry.role}}` : 'INVALID';
    }});
  </script>
</body>
</html>
'''
    return document.encode("utf-8")


__all__ = ["render_verify_page"]
