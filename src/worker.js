// HopDrop: Cloudflare Worker + R2.
// Serves the static site (public/) and a small API for resumable big-file uploads.
// Files are stored in R2 and expire after 7 days (also enforced by an R2 lifecycle rule).

const PART_SIZE = 32 * 1024 * 1024;               // every part except the last must be exactly this size
const EXPIRE_MS = 7 * 24 * 60 * 60 * 1000;
const ALPHA = 'abcdefghijkmnpqrstuvwxyz23456789';  // 32 chars, no l / o / 0 / 1
const ID_RE = /^[a-z2-9]{12}$/;
const DEFAULT_MAX = 12 * 1024 * 1024 * 1024;

const rand = n => Array.from(crypto.getRandomValues(new Uint8Array(n)), b => ALPHA[b % 32]).join('');

const json = (obj, status = 200) =>
  new Response(JSON.stringify(obj), {
    status,
    headers: { 'content-type': 'application/json', 'cache-control': 'no-store' },
  });

const maxBytes = env => Number(env.MAX_BYTES) || DEFAULT_MAX;

function cleanName(raw) {
  let n = String(raw || 'file').replace(/\\/g, '/').split('/').pop();
  n = n.replace(/[\u0000-\u001f<>:"|?*]/g, '_').trim().replace(/^\.+/, '');
  return (n || 'file').slice(0, 180);
}

function disposition(name) {
  const ascii = name.replace(/[^\x20-\x7e]/g, '_').replace(/["\\]/g, '_');
  return `attachment; filename="${ascii}"; filename*=UTF-8''${encodeURIComponent(name)}`;
}

const keyOf = id => 'f/' + id;

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const p = url.pathname;
    try {
      if (p.startsWith('/api/')) return await handleApi(request, env, url);
      if (p.startsWith('/d/')) {
        // pretty download links: /d/<id> shows the download page
        return env.ASSETS.fetch(new Request(new URL('/download', url), request));
      }
      return env.ASSETS.fetch(request);
    } catch (e) {
      return json({ error: (e && e.message) || 'Server error' }, 500);
    }
  },
};

async function handleApi(request, env, url) {
  const p = url.pathname;
  const m = request.method;

  if (p === '/api/config' && m === 'GET') {
    return json({ maxBytes: maxBytes(env), expireDays: 7, partSize: PART_SIZE });
  }

  // ---- start an upload -------------------------------------------------
  if (p === '/api/upload/create' && m === 'POST') {
    const body = await request.json().catch(() => ({}));
    const size = Number(body.size);
    if (!Number.isFinite(size) || size <= 0) return json({ error: 'Empty or invalid file size' }, 400);
    if (size > maxBytes(env)) return json({ error: 'File is larger than the ' + (maxBytes(env) / 1073741824) + ' GB limit' }, 413);

    const name = cleanName(body.name);
    const id = rand(12);
    const deleteToken = rand(24);
    const expires = Date.now() + EXPIRE_MS;
    const mp = await env.BUCKET.createMultipartUpload(keyOf(id), {
      customMetadata: { name, size: String(size), expires: String(expires), token: deleteToken },
    });
    return json({ id, uploadId: mp.uploadId, partSize: PART_SIZE, expires, deleteToken });
  }

  // ---- upload one part -------------------------------------------------
  if (p === '/api/upload/part' && m === 'PUT') {
    const id = url.searchParams.get('id') || '';
    const uploadId = url.searchParams.get('uploadId') || '';
    const n = Number(url.searchParams.get('n'));
    const len = Number(request.headers.get('content-length'));
    if (!ID_RE.test(id) || !uploadId || !Number.isInteger(n) || n < 1 || n > 10000) return json({ error: 'Bad request' }, 400);
    if (!len || len > PART_SIZE || !request.body) return json({ error: 'Bad part size' }, 400);
    let part;
    try {
      const mp = env.BUCKET.resumeMultipartUpload(keyOf(id), uploadId);
      part = await mp.uploadPart(n, request.body);
    } catch (e) {
      return json({ error: 'Upload session not found. Please start again.' }, 404);
    }
    return json({ n, etag: part.etag });
  }

  // ---- finish an upload ------------------------------------------------
  if (p === '/api/upload/complete' && m === 'POST') {
    const body = await request.json().catch(() => ({}));
    const { id, uploadId, parts } = body;
    if (!ID_RE.test(String(id)) || !uploadId || !Array.isArray(parts) || !parts.length) return json({ error: 'Bad request' }, 400);
    let obj;
    try {
      const mp = env.BUCKET.resumeMultipartUpload(keyOf(id), uploadId);
      obj = await mp.complete(parts.map(x => ({ partNumber: Number(x.n), etag: String(x.etag) })));
    } catch (e) {
      return json({ error: 'Could not finish the upload: ' + ((e && e.message) || 'unknown error') }, 400);
    }
    const meta = obj.customMetadata || {};
    if (Number(meta.size) !== obj.size) {
      await env.BUCKET.delete(keyOf(id));
      return json({ error: 'Uploaded size did not match. Please try again.' }, 400);
    }
    return json({ id, expires: Number(meta.expires) });
  }

  // ---- file info / delete ----------------------------------------------
  let mt = p.match(/^\/api\/file\/([a-z2-9]{12})$/);
  if (mt) {
    const id = mt[1];
    const o = await env.BUCKET.head(keyOf(id));
    if (!o) return json({ error: 'File not found' }, 404);
    const meta = o.customMetadata || {};
    if (Number(meta.expires) < Date.now()) {
      await env.BUCKET.delete(keyOf(id));
      return json({ error: 'This file has expired' }, 410);
    }
    if (m === 'GET') return json({ name: meta.name, size: o.size, expires: Number(meta.expires) });
    if (m === 'DELETE') {
      if (url.searchParams.get('token') !== meta.token) return json({ error: 'Not allowed' }, 403);
      await env.BUCKET.delete(keyOf(id));
      return json({ ok: true });
    }
  }

  // ---- download (supports Range, so browsers can resume) ---------------
  mt = p.match(/^\/api\/dl\/([a-z2-9]{12})$/);
  if (mt && (m === 'GET' || m === 'HEAD')) return download(request, env, mt[1], m === 'HEAD');

  return json({ error: 'Not found' }, 404);
}

async function download(request, env, id, headOnly) {
  const key = keyOf(id);
  const baseHeaders = (o, meta) => {
    const h = new Headers();
    h.set('content-type', 'application/octet-stream');      // never let the browser render uploads
    h.set('x-content-type-options', 'nosniff');
    h.set('content-disposition', disposition(meta.name || 'file'));
    h.set('accept-ranges', 'bytes');
    h.set('etag', o.httpEtag);
    h.set('cache-control', 'private, max-age=0');
    return h;
  };

  if (headOnly) {
    const o = await env.BUCKET.head(key);
    if (!o) return json({ error: 'File not found' }, 404);
    const meta = o.customMetadata || {};
    if (Number(meta.expires) < Date.now()) return json({ error: 'This file has expired' }, 410);
    const h = baseHeaders(o, meta);
    h.set('content-length', String(o.size));
    return new Response(null, { status: 200, headers: h });
  }

  const o = await env.BUCKET.get(key, { range: request.headers, onlyIf: request.headers });
  if (!o) return json({ error: 'File not found' }, 404);
  const meta = o.customMetadata || {};
  if (Number(meta.expires) < Date.now()) {
    if (o.body) await o.body.cancel();
    await env.BUCKET.delete(key);
    return json({ error: 'This file has expired' }, 410);
  }
  const h = baseHeaders(o, meta);
  if (!('body' in o) || !o.body) return new Response(null, { status: 304, headers: h });

  let status = 200;
  if (request.headers.has('range') && o.range) {
    let { offset, length, suffix } = o.range;
    if (suffix !== undefined) { length = Math.min(suffix, o.size); offset = o.size - length; }
    else { offset = offset || 0; length = Math.min(length === undefined ? o.size - offset : length, o.size - offset); }
    status = 206;
    h.set('content-range', `bytes ${offset}-${offset + length - 1}/${o.size}`);
    h.set('content-length', String(length));
  } else {
    h.set('content-length', String(o.size));
  }
  return new Response(o.body, { status, headers: h });
}
