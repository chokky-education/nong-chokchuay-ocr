import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import vm from 'node:vm';

const APP_JS_PATH = path.resolve(import.meta.dirname, '..', 'static', 'app.js');
const APP_JS_SOURCE = fs.readFileSync(APP_JS_PATH, 'utf8');

function createMockElement(id = '') {
  const listeners = new Map();
  const classes = new Set();
  return {
    id,
    hidden: false,
    disabled: false,
    checked: false,
    value: '',
    textContent: '',
    innerHTML: '',
    href: '',
    src: '',
    dataset: {},
    style: {},
    files: [],
    classList: {
      add: (...items) => items.forEach((c) => classes.add(c)),
      remove: (...items) => items.forEach((c) => classes.delete(c)),
      toggle: (item, force) => {
        if (force === undefined) {
          if (classes.has(item)) {
            classes.delete(item);
            return false;
          }
          classes.add(item);
          return true;
        }
        if (force) classes.add(item);
        else classes.delete(item);
        return Boolean(force);
      },
      contains: (item) => classes.has(item),
    },
    addEventListener(type, fn) {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push(fn);
    },
    async trigger(type, eventObj = {}) {
      const handlers = listeners.get(type) || [];
      for (const h of handlers) {
        await h({ target: this, preventDefault() {}, ...eventObj });
      }
    },
    click() {
      return this.trigger('click');
    },
    appendChild() {},
    querySelectorAll: () => [],
    querySelector: () => null,
  };
}

function jsonResponse(status, body) {
  return {
    status,
    ok: status >= 200 && status < 300,
    headers: {
      get: (name) => (String(name).toLowerCase() === 'content-type' ? 'application/json' : null),
    },
    async json() {
      return body;
    },
    async text() {
      return JSON.stringify(body);
    },
  };
}

function flushMicrotasks() {
  return new Promise((resolve) => setTimeout(resolve, 20));
}

function runFrontendApp({ search = '', userAgent = 'Mozilla/5.0 Chrome/120.0', fetchImpl }) {
  const elements = new Map();
  const getEl = (id) => {
    if (!elements.has(id)) {
      elements.set(id, createMockElement(id));
    }
    return elements.get(id);
  };

  const body = createMockElement('body');
  body.dataset.authState = 'checking';

  const docListeners = new Map();
  const documentMock = {
    readyState: 'complete',
    body,
    getElementById: getEl,
    querySelectorAll: () => [],
    querySelector: () => null,
    addEventListener(type, fn) {
      if (!docListeners.has(type)) docListeners.set(type, []);
      docListeners.get(type).push(fn);
    },
    createElement: (tag) => createMockElement(tag),
  };

  const replaceStateCalls = [];
  const winListeners = new Map();
  let currentHref = `https://nongchok.example.com/${search}`;
  const windowMock = {
    location: {
      search,
      get href() {
        return currentHref;
      },
    },
    history: {
      replaceState(_state, _title, nextUrl) {
        replaceStateCalls.push(nextUrl);
        currentHref = nextUrl;
      },
    },
    navigator: {
      userAgent,
      clipboard: {
        copied: [],
        async writeText(val) {
          this.copied.push(val);
        },
      },
    },
    addEventListener(type, fn) {
      if (!winListeners.has(type)) winListeners.set(type, []);
      winListeners.get(type).push(fn);
    },
  };

  const context = {
    window: windowMock,
    document: documentMock,
    navigator: windowMock.navigator,
    fetch: fetchImpl,
    URL,
    URLSearchParams,
    console,
    setTimeout,
    clearTimeout,
    FormData: class FakeFormData {
      constructor() {
        this.entries = [];
      }
      append(k, v) {
        this.entries.push([k, v]);
      }
    },
  };
  vm.runInNewContext(APP_JS_SOURCE, context, { filename: 'static/app.js' });
  return { elements, getEl, body, windowMock, replaceStateCalls };
}

test('unauthenticated init calls zero data APIs, renders auth_error via textContent, and detects LINE WebView', async () => {
  const fetchCalls = [];
  const { getEl, body, replaceStateCalls } = runFrontendApp({
    search: '?auth_error=unauthorized_email',
    userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) Line/14.2.0',
    fetchImpl: async (url, options = {}) => {
      fetchCalls.push({ url, options });
      if (url === '/api/auth/me') {
        return jsonResponse(200, {
          authenticated: false,
          auth_required: true,
          oauth_configured: true,
          csrf_token: null,
          user: null,
        });
      }
      throw new Error(`Unexpected unauthenticated fetch to ${url}`);
    },
  });

  await flushMicrotasks();

  assert.deepEqual(
    fetchCalls.map((c) => c.url),
    ['/api/auth/me'],
  );
  assert.equal(body.dataset.authState, 'unauthenticated');
  assert.equal(getEl('auth-gate').hidden, false);
  assert.equal(getEl('auth-user-pill').hidden, true);
  assert.equal(getEl('auth-error-banner').hidden, false);
  assert.match(getEl('auth-error-banner').textContent, /ไม่ได้รับอนุญาต/);
  assert.equal(getEl('auth-webview-warning').hidden, false);
  assert.equal(replaceStateCalls.length, 1);
  assert.ok(!replaceStateCalls[0].includes('auth_error='));

  // Backend not_configured code also maps to specific OAuth config Thai message
  const notCfgRun = runFrontendApp({
    search: '?auth_error=not_configured',
    fetchImpl: async (url) => {
      if (url === '/api/auth/me') {
        return jsonResponse(200, { authenticated: false, auth_required: true, csrf_token: null, user: null });
      }
      throw new Error(`Unexpected fetch to ${url}`);
    },
  });
  await flushMicrotasks();
  assert.match(notCfgRun.getEl('auth-error-banner').textContent, /Google OAuth/);
});

test('authenticated session attaches X-CSRF-Token on POST and wipes DOM/state on 401', async () => {
  const fetchCalls = [];
  let force401 = false;
  const { getEl, body } = runFrontendApp({
    search: '?doc=doc-secret-1',
    fetchImpl: async (url, options = {}) => {
      fetchCalls.push({ url, options });
      if (url === '/api/auth/me') {
        return jsonResponse(200, {
          authenticated: true,
          auth_required: true,
          oauth_configured: true,
          csrf_token: 'csrf-secret-999',
          user: {
            email: 'cgikchik07@gmail.com',
            name: 'Owner',
            picture: '',
          },
        });
      }
      if (force401) {
        return jsonResponse(401, { detail: 'Authentication required' });
      }
      if (url === '/api/system') {
        return jsonResponse(200, {
          database: {
            total_documents: 3,
            pending_review_count: 1,
            pending_reimbursement_total: 250,
            total_students: 2,
            allergy_student_count: 1,
          },
          teacher_name: 'ครูพิ้งกี้',
        });
      }
      if (url === '/api/watcher/status') {
        return jsonResponse(200, { inbox_pending_count: 2 });
      }
      if (url === '/api/documents/doc-secret-1') {
        return jsonResponse(200, {
          document: {
            id: 'doc-secret-1',
            filename: 'mali_allergy.png',
            text: 'เด็กหญิงมะลิ แพ้นมวัว โทร 081-234-5678',
            category: 'student',
            review_status: 'pending_review',
            lines: [],
          },
        });
      }
      if (url === '/api/documents/doc-secret-1/review') {
        return jsonResponse(200, {
          document: {
            id: 'doc-secret-1',
            filename: 'mali_allergy.png',
            text: 'เด็กหญิงมะลิ แพ้นมวัว โทร 081-234-5678',
            category: 'student',
            review_status: 'reviewed',
            lines: [],
          },
        });
      }
      throw new Error(`Unexpected fetch to ${url}`);
    },
  });

  await flushMicrotasks();

  assert.equal(body.dataset.authState, 'authenticated');
  assert.equal(getEl('auth-gate').hidden, true);
  assert.equal(getEl('auth-user-pill').hidden, false);
  assert.equal(getEl('auth-user-email').textContent, 'cgikchik07@gmail.com');
  assert.match(getEl('ocr-text-result').value, /มะลิ/);
  assert.equal(getEl('file-badge').textContent, 'mali_allergy.png');
  assert.equal(getEl('kpi-pending-reimb-val').textContent, '250.00 ฿');

  // State-changing POST must attach X-CSRF-Token
  await getEl('btn-confirm-review').click();
  await flushMicrotasks();

  const reviewCall = fetchCalls.find((c) => c.url === '/api/documents/doc-secret-1/review');
  assert.ok(reviewCall, 'Expected POST /api/documents/doc-secret-1/review');
  assert.equal(reviewCall.options.method, 'POST');
  assert.equal(reviewCall.options.headers['X-CSRF-Token'], 'csrf-secret-999');

  // When session expires (401), UI state & DOM must be wiped and auth-gate shown
  force401 = true;
  await getEl('btn-scan-inbox').click();
  await flushMicrotasks();

  assert.equal(body.dataset.authState, 'unauthenticated');
  assert.equal(getEl('auth-gate').hidden, false);
  assert.equal(getEl('auth-user-pill').hidden, true);
  assert.equal(getEl('auth-user-email').textContent, '');
  assert.equal(getEl('ocr-text-result').value, '');
  assert.equal(getEl('file-badge').textContent, '');
  assert.equal(getEl('kpi-pending-reimb-val').textContent, '0.00 ฿');
  assert.ok(!getEl('footer-doc-id').textContent.includes('doc-secret-1'));
});

test('logout button posts with X-CSRF-Token and clears workspace', async () => {
  const fetchCalls = [];
  const { getEl, body } = runFrontendApp({
    fetchImpl: async (url, options = {}) => {
      fetchCalls.push({ url, options });
      if (url === '/api/auth/me') {
        return jsonResponse(200, {
          authenticated: true,
          auth_required: true,
          oauth_configured: true,
          csrf_token: 'csrf-logout-123',
          user: {
            email: 'ppksaruenee@gmail.com',
            name: 'Teacher',
            picture: '',
          },
        });
      }
      if (url === '/api/system') return jsonResponse(200, { database: {} });
      if (url === '/api/watcher/status') return jsonResponse(200, { inbox_pending_count: 0 });
      if (url === '/api/auth/logout') return jsonResponse(200, { success: true });
      throw new Error(`Unexpected fetch to ${url}`);
    },
  });

  await flushMicrotasks();
  assert.equal(body.dataset.authState, 'authenticated');
  assert.equal(getEl('auth-user-pill').hidden, false);
  assert.equal(getEl('auth-user-email').textContent, 'ppksaruenee@gmail.com');

  await getEl('btn-logout').click();
  await flushMicrotasks();

  const logoutCall = fetchCalls.find((c) => c.url === '/api/auth/logout');
  assert.ok(logoutCall);
  assert.equal(logoutCall.options.method, 'POST');
  assert.equal(logoutCall.options.headers['X-CSRF-Token'], 'csrf-logout-123');
  assert.equal(body.dataset.authState, 'unauthenticated');
  assert.equal(getEl('auth-gate').hidden, false);
  assert.equal(getEl('auth-user-pill').hidden, true);
});

test('local dev auth_required=false enters workspace without user pill and unknown auth_error falls back safely', async () => {
  const fetchCalls = [];
  const { getEl, body } = runFrontendApp({
    search: '?auth_error=<script>alert(1)</script>',
    fetchImpl: async (url, options = {}) => {
      fetchCalls.push({ url, options });
      if (url === '/api/auth/me') {
        return jsonResponse(200, { authenticated: false, auth_required: false, csrf_token: null });
      }
      if (url === '/api/system') return jsonResponse(200, { database: { total_documents: 1 } });
      if (url === '/api/watcher/status') return jsonResponse(200, { inbox_pending_count: 0 });
      throw new Error(`Unexpected fetch to ${url}`);
    },
  });

  await flushMicrotasks();

  assert.equal(body.dataset.authState, 'authenticated');
  assert.equal(getEl('auth-gate').hidden, true);
  assert.equal(getEl('auth-user-pill').hidden, true);
  assert.ok(fetchCalls.some((c) => c.url === '/api/system'));
  assert.equal(getEl('auth-error-banner').textContent, 'เข้าสู่ระบบไม่สำเร็จ กรุณาลองใหม่อีกครั้ง');
});

test('logout network error or 403 keeps user in workspace, shows retry warning, and succeeds on retry', async () => {
  for (const failureMode of ['network', '403']) {
    let attempt = 0;
    const { getEl, body } = runFrontendApp({
      fetchImpl: async (url) => {
        if (url === '/api/auth/me') {
          return jsonResponse(200, {
            authenticated: true,
            auth_required: true,
            oauth_configured: true,
            csrf_token: 'csrf-retry-1',
            user: { email: 'cgikchik07@gmail.com', name: 'Owner', picture: '' },
          });
        }
        if (url === '/api/system') {
          return jsonResponse(200, { database: { pending_reimbursement_total: 120 } });
        }
        if (url === '/api/watcher/status') return jsonResponse(200, { inbox_pending_count: 0 });
        if (url === '/api/auth/logout') {
          attempt += 1;
          if (attempt === 1) {
            if (failureMode === 'network') throw new TypeError('Failed to fetch');
            return jsonResponse(403, { detail: 'Invalid CSRF token' });
          }
          return jsonResponse(200, { success: true });
        }
        throw new Error(`Unexpected fetch to ${url}`);
      },
    });

    await flushMicrotasks();
    assert.equal(body.dataset.authState, 'authenticated');
    assert.equal(getEl('kpi-pending-reimb-val').textContent, '120.00 ฿');

    // First logout attempt fails -> MUST NOT fake logout; MUST warn and allow retry
    await getEl('btn-logout').click();
    await flushMicrotasks();

    assert.equal(body.dataset.authState, 'authenticated', `Mode ${failureMode} must stay authenticated`);
    assert.equal(getEl('auth-gate').hidden, true);
    assert.equal(getEl('auth-user-pill').hidden, false);
    assert.equal(getEl('btn-logout').disabled, false);
    assert.equal(getEl('kpi-pending-reimb-val').textContent, '120.00 ฿');
    assert.equal(getEl('toast').classList.contains('show'), true);
    assert.match(getEl('toast-message').textContent, /ยังออกจากระบบไม่สำเร็จ/);
    assert.match(getEl('toast-message').textContent, /ลองใหม่อีกครั้ง/);

    // Second logout attempt succeeds -> revokes & clears workspace
    await getEl('btn-logout').click();
    await flushMicrotasks();

    assert.equal(attempt, 2);
    assert.equal(body.dataset.authState, 'unauthenticated');
    assert.equal(getEl('auth-gate').hidden, false);
    assert.equal(getEl('auth-user-pill').hidden, true);
    assert.equal(getEl('kpi-pending-reimb-val').textContent, '0.00 ฿');
  }
});

test('logout returning 401 switches to auth-gate and preserves session-expired notice', async () => {
  const { getEl, body } = runFrontendApp({
    fetchImpl: async (url) => {
      if (url === '/api/auth/me') {
        return jsonResponse(200, {
          authenticated: true,
          auth_required: true,
          oauth_configured: true,
          csrf_token: 'csrf-401-logout',
          user: { email: 'ppksaruenee@gmail.com', name: 'Teacher', picture: '' },
        });
      }
      if (url === '/api/system') return jsonResponse(200, { database: {} });
      if (url === '/api/watcher/status') return jsonResponse(200, { inbox_pending_count: 0 });
      if (url === '/api/auth/logout') return jsonResponse(401, { detail: 'Session expired' });
      throw new Error(`Unexpected fetch to ${url}`);
    },
  });

  await flushMicrotasks();
  await getEl('btn-logout').click();
  await flushMicrotasks();

  assert.equal(body.dataset.authState, 'unauthenticated');
  assert.equal(getEl('auth-gate').hidden, false);
  assert.equal(getEl('auth-error-banner').hidden, false);
  assert.match(getEl('auth-error-banner').textContent, /เซสชันหมดอายุ/);
});
