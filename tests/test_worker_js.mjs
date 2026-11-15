import assert from 'node:assert/strict';
import { register } from 'node:module';
import test from 'node:test';

const cloudflareMockHook = `
  export async function resolve(specifier, context, nextResolve) {
    if (specifier === 'cloudflare:workers') {
      return { shortCircuit: true, url: 'mock:cloudflare-workers' };
    }
    return nextResolve(specifier, context);
  }
  export async function load(url, context, nextLoad) {
    if (url === 'mock:cloudflare-workers') {
      return {
        shortCircuit: true,
        format: 'module',
        source: [
          'export class DurableObject { constructor(ctx, env) { this.ctx = ctx; this.env = env; } }',
          'export class WorkerEntrypoint { constructor(ctx, env) { this.ctx = ctx; this.env = env; } }',
        ].join('\\n'),
      };
    }
    return nextLoad(url, context);
  }
`;

register(`data:text/javascript,${encodeURIComponent(cloudflareMockHook)}`);

const workerMod = await import('../worker.js');

function createMockR2Bucket() {
  const objects = new Map();
  let seq = 0;

  function toObjectRecord(entry) {
    return {
      etag: entry.etag,
      httpEtag: `"${entry.etag}"`,
      size: entry.bytes.byteLength,
      httpMetadata: entry.httpMetadata || {},
      customMetadata: entry.customMetadata || {},
      body: entry.bytes,
      async text() {
        return new TextDecoder().decode(entry.bytes);
      },
      async arrayBuffer() {
        return entry.bytes.buffer.slice(entry.bytes.byteOffset, entry.bytes.byteOffset + entry.bytes.byteLength);
      },
    };
  }

  return {
    objects,
    async head(key) {
      const entry = objects.get(key);
      if (!entry) return null;
      return {
        etag: entry.etag,
        httpEtag: `"${entry.etag}"`,
        size: entry.bytes.byteLength,
        httpMetadata: entry.httpMetadata || {},
        customMetadata: entry.customMetadata || {},
      };
    },
    async get(key) {
      const entry = objects.get(key);
      if (!entry) return null;
      return toObjectRecord(entry);
    },
    async put(key, value, opts = {}) {
      const existing = objects.get(key);
      if (opts.onlyIf?.etagDoesNotMatch === '*' && existing) {
        return null;
      }
      if (opts.onlyIf?.etagMatches !== undefined) {
        if (!existing || existing.etag !== opts.onlyIf.etagMatches) {
          return null;
        }
      }
      const bytes =
        value instanceof Uint8Array
          ? value
          : value instanceof ArrayBuffer
            ? new Uint8Array(value)
            : new TextEncoder().encode(String(value));
      seq += 1;
      const etag = `mock-etag-${seq}`;
      const entry = {
        etag,
        bytes,
        httpMetadata: opts.httpMetadata || {},
        customMetadata: opts.customMetadata || {},
      };
      objects.set(key, entry);
      return { etag, httpEtag: `"${etag}"`, size: bytes.byteLength };
    },
    async delete(key) {
      objects.delete(key);
    },
    async list({ prefix = '', limit = 1000 } = {}) {
      const matched = [];
      for (const [key, entry] of objects.entries()) {
        if (key.startsWith(prefix)) {
          matched.push({ key, size: entry.bytes.byteLength, etag: entry.etag });
          if (matched.length >= limit) break;
        }
      }
      return { objects: matched, truncated: false };
    },
  };
}

async function sha256Hex(bytes) {
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0')).join('');
}

function createMockDurableObjectNamespace() {
  const storageMap = new Map();
  const alarms = [];
  const startCalls = [];
  const interceptCalls = [];
  let destroyCount = 0;
  const forwardedRequests = [];
  let healthResponsePayload = { status: 'ok', busy: false, storage_state: 'READY' };
  let fetchHandler = async (forwardedReq) => {
    forwardedRequests.push(forwardedReq);
    if (new URL(forwardedReq.url).pathname === '/api/health') {
      return Response.json(healthResponsePayload, { status: 200 });
    }
    return new Response('ok', { status: 200 });
  };

  const container = {
    running: false,
    start(opts) {
      this.running = true;
      startCalls.push(opts);
    },
    async destroy() {
      this.running = false;
      destroyCount += 1;
    },
    async interceptOutboundHttp(host, stub) {
      interceptCalls.push({ host, stub });
    },
    getTcpPort(_port) {
      return {
        async fetch(forwardedReq) {
          return fetchHandler(forwardedReq);
        },
      };
    },
  };

  let doInstance = null;
  let latestEnv = null;
  const ctx = {
    container,
    exports: {
      VaultR2Gateway({ props }) {
        return {
          props,
          async fetch(request) {
            const gw = new workerMod.VaultR2Gateway({ props }, latestEnv);
            return gw.fetch(request);
          },
        };
      },
    },
    storage: {
      async get(key) {
        return storageMap.get(key);
      },
      async put(key, val) {
        storageMap.set(key, val);
      },
      async setAlarm(ts) {
        alarms.push(ts);
      },
    },
  };

  const idFromNameCalls = [];
  const envBase = {
    NONG_CHOK_CONTAINER: {
      idFromName(name) {
        idFromNameCalls.push(name);
        return `id:${name}`;
      },
      get(_id) {
        return {
          async fetch(request) {
            return doInstance.fetch(request);
          },
        };
      },
    },
  };

  function makeEnv(overrides = {}) {
    const fullEnv = { ...envBase, ...overrides };
    latestEnv = fullEnv;
    if (!doInstance) {
      doInstance = new workerMod.NongChokChuayOCR(ctx, fullEnv);
    } else {
      doInstance.env = fullEnv;
    }
    return fullEnv;
  }

  return {
    makeEnv,
    recreateDO(overrides = {}) {
      const fullEnv = { ...envBase, ...overrides };
      latestEnv = fullEnv;
      doInstance = new workerMod.NongChokChuayOCR(ctx, fullEnv);
      return doInstance;
    },
    get doInstance() {
      return doInstance;
    },
    startCalls,
    interceptCalls,
    get destroyCount() {
      return destroyCount;
    },
    forwardedRequests,
    idFromNameCalls,
    storageMap,
    alarms,
    container,
    setHealthPayload(payload) {
      healthResponsePayload = payload;
    },
    setFetchHandler(fn) {
      fetchHandler = fn;
    },
  };
}

test('buildContainerEnv forces AUTH_REQUIRED=true and APP_ENV=production', () => {
  assert.equal(typeof workerMod.buildContainerEnv, 'function');
  const envOut = workerMod.buildContainerEnv({
    APP_ENV: 'development',
    AUTH_REQUIRED: 'false',
    GOOGLE_CLIENT_ID: 'cid-123',
    GOOGLE_CLIENT_SECRET: 'csec-456',
    AUTH_SESSION_SECRET: 'sess-789',
    ALLOWED_EMAILS: 'cgikchik07@gmail.com,ppksaruenee@gmail.com',
    ALLOWED_LINE_USER_IDS: 'U1,U2',
    LINE_ALLOW_GROUP_CHATS: 'false',
  });
  assert.equal(envOut.APP_ENV, 'production');
  assert.equal(envOut.AUTH_REQUIRED, 'true');
  assert.equal(envOut.GOOGLE_CLIENT_ID, 'cid-123');
  assert.equal(envOut.GOOGLE_CLIENT_SECRET, 'csec-456');
  assert.equal(envOut.AUTH_SESSION_SECRET, 'sess-789');
  assert.equal(envOut.ALLOWED_EMAILS, 'cgikchik07@gmail.com,ppksaruenee@gmail.com');
  assert.equal(envOut.ALLOWED_LINE_USER_IDS, 'U1,U2');
  assert.equal(envOut.LINE_ALLOW_GROUP_CHATS, 'false');
  assert.equal(envOut.R2_REQUIRED, 'false');
  assert.equal(envOut.R2_GATEWAY_URL, '');
  assert.equal(envOut.LLM_PROVIDER, 'gemini');
  assert.equal(envOut.LLM_MODEL, 'gemini-4-hydrogen');

  const envWithR2 = workerMod.buildContainerEnv({
    VAULT_BUCKET: {},
    R2_BOOTSTRAP_IF_EMPTY: 'true',
    SNAPSHOT_RETENTION_COUNT: '7',
  });
  assert.equal(envWithR2.R2_GATEWAY_URL, 'http://r2.internal');
  assert.equal(envWithR2.R2_REQUIRED, 'true');
  assert.equal(envWithR2.R2_BOOTSTRAP_IF_EMPTY, 'true');
  assert.equal(envWithR2.SNAPSHOT_RETENTION_COUNT, '7');

  const blankModelOut = workerMod.buildContainerEnv({ LLM_PROVIDER: '   ', LLM_MODEL: '   ' });
  assert.equal(blankModelOut.LLM_PROVIDER, 'gemini');
  assert.equal(blankModelOut.LLM_MODEL, 'gemini-4-hydrogen');

  const openaiBlankModelOut = workerMod.buildContainerEnv({ LLM_PROVIDER: 'openai', LLM_MODEL: '   ' });
  assert.equal(openaiBlankModelOut.LLM_PROVIDER, 'openai');
  assert.equal(openaiBlankModelOut.LLM_MODEL, 'gpt-4o-mini');
});

test('computeConfigFingerprint changes when LLM_MODEL changes', async () => {
  const fpOld = await workerMod.computeConfigFingerprint(
    workerMod.buildContainerEnv({ LLM_MODEL: 'gemini-2.5-flash' }),
  );
  const fpDefault = await workerMod.computeConfigFingerprint(workerMod.buildContainerEnv({}));
  assert.notEqual(fpOld, fpDefault);
});

test('Worker uses singleton DO, forwards proto/host, reuses warm container, and restarts on secret rotation', async () => {
  const harness = createMockDurableObjectNamespace();
  const baseSecrets = {
    PUBLIC_BASE_URL: 'https://nong-chokchuay-ocr.entrace-space.workers.dev',
    ALLOWED_EMAILS: 'cgikchik07@gmail.com,ppksaruenee@gmail.com',
    GOOGLE_CLIENT_ID: 'cid-1',
    GOOGLE_CLIENT_SECRET: 'csec-1',
    AUTH_SESSION_SECRET: 'secret-v1',
    LINE_CHANNEL_SECRET: 'line-sec-1',
    ALLOWED_LINE_USER_IDS: 'U-1',
  };

  // Request 1: cold start with v1 secrets + spoofed incoming forwarded headers
  const env1 = harness.makeEnv(baseSecrets);
  const req1 = new Request('https://nong-chokchuay-ocr.entrace-space.workers.dev/api/health', {
    headers: {
      'X-Forwarded-Proto': 'http',
      'X-Forwarded-Host': 'evil.attacker.invalid',
    },
  });
  const res1 = await workerMod.default.fetch(req1, env1);
  assert.equal(res1.status, 200);
  assert.deepEqual(harness.idFromNameCalls, ['singleton']);
  assert.equal(harness.startCalls.length, 1);
  assert.equal(harness.destroyCount, 0);
  assert.equal(harness.startCalls[0].env.AUTH_SESSION_SECRET, 'secret-v1');
  assert.equal(harness.forwardedRequests[0].headers.get('X-Forwarded-Proto'), 'https');
  assert.equal(
    harness.forwardedRequests[0].headers.get('X-Forwarded-Host'),
    'nong-chokchuay-ocr.entrace-space.workers.dev',
  );

  // Request 2: same secrets -> warm reuse (no destroy, no duplicate start)
  const req2 = new Request('https://nong-chokchuay-ocr.entrace-space.workers.dev/api/auth/me');
  const res2 = await workerMod.default.fetch(req2, env1);
  assert.equal(res2.status, 200);
  assert.deepEqual(harness.idFromNameCalls, ['singleton', 'singleton']);
  assert.equal(harness.startCalls.length, 1);
  assert.equal(harness.destroyCount, 0);

  // Request 3: rotated AUTH_SESSION_SECRET -> SAME singleton DO destroys & restarts container with v2 secret
  const env2 = harness.makeEnv({ ...baseSecrets, AUTH_SESSION_SECRET: 'secret-v2' });
  const req3 = new Request('https://nong-chokchuay-ocr.entrace-space.workers.dev/api/auth/me');
  const res3 = await workerMod.default.fetch(req3, env2);
  assert.equal(res3.status, 200);
  assert.deepEqual(harness.idFromNameCalls, ['singleton', 'singleton', 'singleton']);
  assert.equal(harness.destroyCount, 1);
  assert.equal(harness.startCalls.length, 2);
  assert.equal(harness.startCalls[1].env.AUTH_SESSION_SECRET, 'secret-v2');
});

test('DO persists internal auth across DO re-instantiation, strips internal headers, and checks busy/RESTORING in alarm', async () => {
  const harness = createMockDurableObjectNamespace();
  const bucket = createMockR2Bucket();
  const secrets = {
    PUBLIC_BASE_URL: 'https://nong-chokchuay-ocr.entrace-space.workers.dev',
    AUTH_SESSION_SECRET: 'secret-v1',
    VAULT_BUCKET: bucket,
    R2_BOOTSTRAP_IF_EMPTY: 'true',
  };
  const env1 = harness.makeEnv(secrets);

  // Request with spoofed internal/vault headers -> stripped before reaching container
  const req1 = new Request('https://nong-chokchuay-ocr.entrace-space.workers.dev/api/documents', {
    headers: {
      'X-Vault-Internal-Token': 'spoofed-token',
      'X-Vault-Container-Epoch': 'spoofed-epoch',
      'X-Internal-Sync': 'evil',
      Authorization: 'Bearer ok',
    },
  });
  const res1 = await workerMod.default.fetch(req1, env1);
  assert.equal(res1.status, 200);
  assert.equal(harness.startCalls.length, 1);
  assert.equal(harness.interceptCalls.length, 1);
  assert.equal(harness.interceptCalls[0].host, 'r2.internal');
  const firstStartEnv = harness.startCalls[0].env;
  assert.ok(firstStartEnv.INTERNAL_SYNC_TOKEN.length >= 32);
  assert.ok(firstStartEnv.CONTAINER_EPOCH.length >= 16);
  const forwarded = harness.forwardedRequests[0];
  assert.equal(forwarded.headers.get('X-Vault-Internal-Token'), null);
  assert.equal(forwarded.headers.get('X-Vault-Container-Epoch'), null);
  assert.equal(forwarded.headers.get('X-Internal-Sync'), null);
  assert.equal(forwarded.headers.get('Authorization'), 'Bearer ok');

  // Re-instantiate DO object while container stays running -> reuses stored token/epoch & rebinds outbound without restart
  const newDo = harness.recreateDO(secrets);
  const res2 = await newDo.fetch(new Request('https://nong-chokchuay-ocr.entrace-space.workers.dev/api/health'));
  assert.equal(res2.status, 200);
  assert.equal(harness.destroyCount, 0);
  assert.equal(harness.startCalls.length, 1);
  assert.equal(harness.interceptCalls.length, 2);

  // alarm() keeps container alive when storage_state === RESTORING
  harness.setHealthPayload({ status: 'ok', storage_state: 'RESTORING', busy: true });
  await newDo.alarm();
  assert.equal(harness.destroyCount, 0);
  assert.equal(harness.container.running, true);

  // alarm() keeps container alive when busy === true
  harness.setHealthPayload({ status: 'ok', storage_state: 'READY', busy: true });
  await newDo.alarm();
  assert.equal(harness.destroyCount, 0);

  // alarm() keeps container alive when _activeRequests > 0
  let releaseInFlight;
  const gate = new Promise((resolve) => {
    releaseInFlight = resolve;
  });
  harness.setFetchHandler(async () => {
    await gate;
    return new Response('slow-ok', { status: 200 });
  });
  const slowReqPromise = newDo.fetch(new Request('https://nong-chokchuay-ocr.entrace-space.workers.dev/api/ocr'));
  await new Promise((r) => setTimeout(r, 10));
  await newDo.alarm();
  assert.equal(harness.destroyCount, 0);
  releaseInFlight();
  await slowReqPromise;

  // alarm() does NOT destroy container if a new request arrives while awaiting /api/health
  let releaseHealth;
  const healthGate = new Promise((resolve) => {
    releaseHealth = resolve;
  });
  let releaseOcr;
  const ocrGate = new Promise((resolve) => {
    releaseOcr = resolve;
  });
  harness.setFetchHandler(async (req) => {
    harness.forwardedRequests.push(req);
    const reqUrl = new URL(req.url);
    if (reqUrl.pathname === '/api/health') {
      await healthGate;
      return Response.json({ status: 'ok', storage_state: 'READY', busy: false }, { status: 200 });
    }
    if (reqUrl.pathname === '/api/ocr') {
      await ocrGate;
      return Response.json({ success: true, doc_id: 'doc-race-1' }, { status: 200 });
    }
    return new Response('ok', { status: 200 });
  });
  const alarmPromise = newDo.alarm();
  await new Promise((r) => setTimeout(r, 10));
  const raceOcrPromise = newDo.fetch(new Request('https://nong-chokchuay-ocr.entrace-space.workers.dev/api/ocr'));
  releaseHealth();
  await alarmPromise;
  assert.equal(harness.destroyCount, 0);
  assert.equal(harness.container.running, true);
  releaseOcr();
  const raceRes = await raceOcrPromise;
  assert.equal(raceRes.status, 200);

  // alarm() destroys container once idle & healthy
  harness.setFetchHandler(async (req) => {
    harness.forwardedRequests.push(req);
    return Response.json({ status: 'ok', storage_state: 'READY', busy: false }, { status: 200 });
  });
  await newDo.alarm();
  assert.equal(harness.destroyCount, 1);
  assert.equal(harness.container.running, false);
});

test('VaultR2Gateway enforces auth, key validation, SHA-256, empty/non-empty manifest, and conditional ETags', async () => {
  const bucket = createMockR2Bucket();
  const token = 'gateway-secret-token-123456';
  const epoch = 'epoch-abc-123';
  const gw = new workerMod.VaultR2Gateway(
    { props: { internalToken: token, containerEpoch: epoch } },
    { VAULT_BUCKET: bucket },
  );
  const authHeaders = {
    'X-Vault-Internal-Token': token,
    'X-Vault-Container-Epoch': epoch,
  };

  // Unauthorized or mismatched token/epoch -> 403
  assert.equal((await gw.fetch(new Request('http://r2.internal/manifest'))).status, 403);
  assert.equal(
    (
      await gw.fetch(
        new Request('http://r2.internal/manifest', {
          headers: { 'X-Vault-Internal-Token': 'wrong', 'X-Vault-Container-Epoch': epoch },
        }),
      )
    ).status,
    403,
  );

  // Empty bucket -> GET /manifest returns 404
  const emptyManifestRes = await gw.fetch(new Request('http://r2.internal/manifest', { headers: authHeaders }));
  assert.equal(emptyManifestRes.status, 404);

  // Put valid upload (rejects traversal/bad keys and bad SHA-256)
  const badKeyRes = await gw.fetch(
    new Request('http://r2.internal/uploads/..%2Fevil.png', { method: 'PUT', headers: authHeaders, body: 'abc' }),
  );
  assert.equal(badKeyRes.status, 400);

  const fileBytes = new TextEncoder().encode('hello-thai-vault');
  const fileSha = await sha256Hex(fileBytes);
  const badShaRes = await gw.fetch(
    new Request('http://r2.internal/uploads/d0c12345-abcd.png', {
      method: 'PUT',
      headers: { ...authHeaders, 'X-Content-Sha256': '0'.repeat(64) },
      body: fileBytes,
    }),
  );
  assert.equal(badShaRes.status, 400);

  const okUploadRes = await gw.fetch(
    new Request('http://r2.internal/uploads/d0c12345-abcd.png', {
      method: 'PUT',
      headers: { ...authHeaders, 'Content-Type': 'image/png', 'X-Content-Sha256': fileSha },
      body: fileBytes,
    }),
  );
  assert.equal(okUploadRes.status, 200);

  // Non-empty bucket without manifest -> 409 manifest_missing_on_non_empty_bucket
  const missingManifestRes = await gw.fetch(new Request('http://r2.internal/manifest', { headers: authHeaders }));
  assert.equal(missingManifestRes.status, 409);

  // Snapshot upload + HEAD + GET + DELETE + oversized check
  const snapBytes = new TextEncoder().encode('sqlite-snapshot-bytes');
  const snapSha = await sha256Hex(snapBytes);
  const snapKey = 'rev-00000001-0123456789ab.db';
  const putSnapRes = await gw.fetch(
    new Request(`http://r2.internal/snapshots/${snapKey}`, {
      method: 'PUT',
      headers: { ...authHeaders, 'X-Content-Sha256': snapSha, 'X-Vault-Revision': '1' },
      body: snapBytes,
    }),
  );
  assert.equal(putSnapRes.status, 200);

  const headSnapRes = await gw.fetch(
    new Request(`http://r2.internal/snapshots/${snapKey}`, { method: 'HEAD', headers: authHeaders }),
  );
  assert.equal(headSnapRes.status, 200);
  assert.equal(headSnapRes.headers.get('X-Content-Sha256'), snapSha);

  const tooBigRes = await gw.fetch(
    new Request(`http://r2.internal/uploads/d0c12345-abcd.png`, {
      method: 'PUT',
      headers: { ...authHeaders, 'Content-Length': String(60 * 1024 * 1024), 'X-Content-Sha256': fileSha },
      body: fileBytes,
    }),
  );
  assert.equal(tooBigRes.status, 413);

  // Conditional manifest PUT: If-None-Match: * creates rev 1, rejects duplicate, then If-Match updates & rejects stale ETag
  const manifest1 = { schema_version: 3, revision: 1, snapshot_key: snapKey, snapshot_sha256: snapSha };
  const putM1 = await gw.fetch(
    new Request('http://r2.internal/manifest', {
      method: 'PUT',
      headers: { ...authHeaders, 'Content-Type': 'application/json', 'If-None-Match': '*' },
      body: JSON.stringify(manifest1),
    }),
  );
  assert.equal(putM1.status, 200);
  const m1Json = await putM1.json();
  assert.ok(m1Json.etag);

  const dupCreate = await gw.fetch(
    new Request('http://r2.internal/manifest', {
      method: 'PUT',
      headers: { ...authHeaders, 'Content-Type': 'application/json', 'If-None-Match': '*' },
      body: JSON.stringify(manifest1),
    }),
  );
  assert.equal(dupCreate.status, 412);

  // Quoted If-Match header also normalizes cleanly
  const putM2 = await gw.fetch(
    new Request('http://r2.internal/manifest', {
      method: 'PUT',
      headers: { ...authHeaders, 'Content-Type': 'application/json', 'If-Match': `"${m1Json.etag}"` },
      body: JSON.stringify({ ...manifest1, revision: 2 }),
    }),
  );
  assert.equal(putM2.status, 200);

  // Stale ETag rejected 412
  const stalePut = await gw.fetch(
    new Request('http://r2.internal/manifest', {
      method: 'PUT',
      headers: { ...authHeaders, 'Content-Type': 'application/json', 'If-Match': m1Json.etag },
      body: JSON.stringify({ ...manifest1, revision: 3 }),
    }),
  );
  assert.equal(stalePut.status, 412);

  const delUpload = await gw.fetch(
    new Request('http://r2.internal/uploads/d0c12345-abcd.png', { method: 'DELETE', headers: authHeaders }),
  );
  assert.equal(delUpload.status, 200);
});
