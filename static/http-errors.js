(function(root) {
  'use strict';
  function message(status, body) {
    const defaults = {
      401: 'Your session has expired. Sign in again.',
      403: 'Access was refused. Check the panel hostname and your access permissions.',
      500: 'ServerCP encountered an internal error. Check the servercp service logs.',
      502: 'Gateway error: the proxy could not complete the request to ServerCP. Check the servercp service and the tunnel route.',
      503: 'ServerCP is temporarily unavailable. Check the servercp service.',
      504: 'The server took too long to respond. Check the panel and selected server connection.',
      524: 'The server took too long to respond. Check the panel and selected server connection.'
    };
    const fallback = defaults[status] || `Request failed (HTTP ${status}).`;
    let detail;
    try { detail = JSON.parse(body).detail; } catch {}
    if(Array.isArray(detail)) detail=detail.map(x=>`${x.loc?.at(-1)||'Field'}: ${x.msg}`).join('; ');
    // Preserve useful JSON errors from ServerCP (including SSH failures), but
    // never render a proxy's HTML document or arbitrary server traceback.
    if(typeof detail==='string' && detail.trim() && !/^\s*</.test(detail)) return detail.trim().slice(0,500);
    if(!defaults[status] && typeof body==='string' && body.trim() && body.length<=500 && !/^\s*</.test(body)) return body.trim();
    return fallback;
  }
  if(typeof module==='object' && module.exports) module.exports={message};
  else root.ServerCPHttp={message};
})(typeof globalThis==='object'?globalThis:this);
