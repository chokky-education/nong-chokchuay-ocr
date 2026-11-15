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
        source: 'export class DurableObject { constructor(ctx, env) { this.ctx = ctx; this.env = env; } }',
      };
    }
    return nextLoad(url, context);
  }
`;

register(`data:text/javascript,${encodeURIComponent(cloudflareMockHook)}`);

const workerMod = await import('../worker.js');

function createMockDurableObjectNamespace() {
  const storageMap = new Map();
  const alarms = [];
  const startCalls = [];
  let destroyCount = 0;
  const forwardedRequests = [];

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
    getTcpPort(_port) {
      return {
        async fetch(forwardedReq) {
          forwardedRequests.push(forwardedReq);
          return new Response('ok', { status: 200 });
        },
      };
    },
  };

  const ctx = {
    container,
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

  let doInstance = null;
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
    if (!doInstance) {
      doInstance = new workerMod.NongChokChuayOCR(ctx, fullEnv);
    } else {
      doInstance.env = fullEnv;
    }
    return fullEnv;
  }

  return {
    makeEnv,
    startCalls,
    get destroyCount() {
      return destroyCount;
    },
    forwardedRequests,
    idFromNameCalls,
    storageMap,
    container,
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
  assert.equal(envOut.LLM_PROVIDER, 'gemini');
  assert.equal(envOut.LLM_MODEL, 'gemini-4-hydrogen');

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
