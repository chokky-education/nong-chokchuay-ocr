import { DurableObject, WorkerEntrypoint } from "cloudflare:workers";

const SLEEP_AFTER_MS = 3 * 60 * 1000;
const CONTAINER_PORT = 8765;
const MAX_SNAPSHOT_BYTES = 100 * 1024 * 1024;
const MAX_UPLOAD_BYTES = 50 * 1024 * 1024;
const SNAPSHOT_NAME_RE = /^rev-\d{8}-[a-f0-9]{12}\.db$/;
const UPLOAD_KEY_RE = /^[a-f0-9-]{8,64}\.[a-z0-9]{1,10}$/;

function stripEtagQuotes(etag) {
  const raw = String(etag || "").trim();
  if (raw.startsWith('W/"') && raw.endsWith('"')) {
    return raw.slice(3, -1);
  }
  if (raw.startsWith('"') && raw.endsWith('"') && raw.length >= 2) {
    return raw.slice(1, -1);
  }
  return raw;
}

function generateRandomHex(byteLength = 32) {
  const bytes = new Uint8Array(byteLength);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

async function sha256Hex(bufferOrString) {
  const bytes =
    typeof bufferOrString === "string"
      ? new TextEncoder().encode(bufferOrString)
      : new Uint8Array(bufferOrString);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

async function timingSafeEqualNonEmpty(a, b) {
  const strA = String(a || "").trim();
  const strB = String(b || "").trim();
  if (!strA || !strB) {
    return false;
  }
  const [hashA, hashB] = await Promise.all([
    crypto.subtle.digest("SHA-256", new TextEncoder().encode(strA)),
    crypto.subtle.digest("SHA-256", new TextEncoder().encode(strB)),
  ]);
  const viewA = new Uint8Array(hashA);
  const viewB = new Uint8Array(hashB);
  let diff = 0;
  for (let i = 0; i < viewA.length; i += 1) {
    diff |= viewA[i] ^ viewB[i];
  }
  return diff === 0;
}

/**
 * Build explicit container environment variables and fail-closed production auth flags.
 */
export function buildContainerEnv(env = {}) {
  const hasBucket = Boolean(env.VAULT_BUCKET);
  const containerEnv = {
    AUTO_EXTRACT_ON_INGEST: String(env.AUTO_EXTRACT_ON_INGEST || "true"),
    PUBLIC_BASE_URL: String(env.PUBLIC_BASE_URL || ""),
    TEACHER_NAME: String(env.TEACHER_NAME || "ครูพิ้งกี้"),
    LINE_CHANNEL_SECRET: String(env.LINE_CHANNEL_SECRET || ""),
    LINE_CHANNEL_ACCESS_TOKEN: String(env.LINE_CHANNEL_ACCESS_TOKEN || ""),
    ALLOWED_LINE_USER_IDS: String(env.ALLOWED_LINE_USER_IDS || ""),
    LINE_ALLOW_GROUP_CHATS: String(env.LINE_ALLOW_GROUP_CHATS || "false"),
    LLM_PROVIDER: String(env.LLM_PROVIDER || "gemini"),
    LLM_MODEL: String(env.LLM_MODEL || "gemini-2.5-flash"),
    LLM_API_KEY: String(env.LLM_API_KEY || ""),
    APP_ENV: "production",
    AUTH_REQUIRED: "true",
    ALLOWED_EMAILS: String(env.ALLOWED_EMAILS || ""),
    GOOGLE_CLIENT_ID: String(env.GOOGLE_CLIENT_ID || ""),
    GOOGLE_CLIENT_SECRET: String(env.GOOGLE_CLIENT_SECRET || ""),
    AUTH_SESSION_SECRET: String(env.AUTH_SESSION_SECRET || ""),
    R2_GATEWAY_URL: hasBucket ? "http://r2.internal" : "",
    R2_REQUIRED: hasBucket ? "true" : "false",
    R2_BOOTSTRAP_IF_EMPTY: String(env.R2_BOOTSTRAP_IF_EMPTY || "false"),
    SNAPSHOT_RETENTION_COUNT: String(env.SNAPSHOT_RETENTION_COUNT || "5"),
  };
  if (env.AUTH_SESSION_TTL_SECONDS) {
    containerEnv.AUTH_SESSION_TTL_SECONDS = String(env.AUTH_SESSION_TTL_SECONDS);
  }
  return containerEnv;
}

/**
 * Compute deterministic SHA-256 hex digest of container configuration so rotated secrets
 * restart the container cleanly inside the single Durable Object without losing DO routing.
 */
export async function computeConfigFingerprint(containerEnv) {
  const sorted = Object.keys(containerEnv)
    .sort()
    .reduce((acc, key) => {
      acc[key] = String(containerEnv[key] ?? "");
      return acc;
    }, {});
  const bytes = new TextEncoder().encode(JSON.stringify(sorted));
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

/**
 * Outbound HTTP gateway allowing the Python container to read/write private R2 objects
 * via http://r2.internal without S3 credentials or Durable Object re-entrancy deadlocks.
 */
export class VaultR2Gateway extends WorkerEntrypoint {
  async fetch(request) {
    const props = this.ctx?.props || {};
    const expectedToken = String(props.internalToken || "").trim();
    const expectedEpoch = String(props.containerEpoch || "").trim();
    const reqToken = request.headers.get("X-Vault-Internal-Token") || "";
    const reqEpoch = request.headers.get("X-Vault-Container-Epoch") || "";

    const [tokenOk, epochOk] = await Promise.all([
      timingSafeEqualNonEmpty(reqToken, expectedToken),
      timingSafeEqualNonEmpty(reqEpoch, expectedEpoch),
    ]);
    if (!tokenOk || !epochOk) {
      return Response.json({ error: "forbidden" }, { status: 403 });
    }

    const bucket = this.env?.VAULT_BUCKET;
    if (!bucket) {
      return Response.json({ error: "r2_bucket_not_configured" }, { status: 500 });
    }

    const url = new URL(request.url);
    const pathname = url.pathname;

    if (pathname === "/manifest") {
      return this._handleManifest(request, bucket);
    }
    if (pathname.startsWith("/snapshots/")) {
      const name = pathname.slice("/snapshots/".length);
      if (!SNAPSHOT_NAME_RE.test(name)) {
        return Response.json({ error: "invalid_snapshot_name" }, { status: 400 });
      }
      return this._handleObject(request, bucket, `vault/snapshots/${name}`, MAX_SNAPSHOT_BYTES, {
        contentType: "application/octet-stream",
        extraMetadata: {
          revision: String(request.headers.get("X-Vault-Revision") || ""),
          schema_version: String(request.headers.get("X-Vault-Schema-Version") || ""),
        },
      });
    }
    if (pathname.startsWith("/uploads/")) {
      const key = pathname.slice("/uploads/".length);
      if (!UPLOAD_KEY_RE.test(key)) {
        return Response.json({ error: "invalid_upload_key" }, { status: 400 });
      }
      return this._handleObject(request, bucket, `vault/uploads/${key}`, MAX_UPLOAD_BYTES, {
        contentType: request.headers.get("Content-Type") || "application/octet-stream",
      });
    }

    return Response.json({ error: "not_found" }, { status: 404 });
  }

  async _handleManifest(request, bucket) {
    if (request.method === "GET") {
      const obj = await bucket.get("vault/manifest.json");
      if (!obj) {
        const listed = await bucket.list({ prefix: "vault/", limit: 1 });
        if ((listed?.objects?.length || 0) > 0) {
          return Response.json({ error: "manifest_missing_on_non_empty_bucket" }, { status: 409 });
        }
        return Response.json({ status: "empty_bucket" }, { status: 404 });
      }
      const body = await obj.text();
      const etag = stripEtagQuotes(obj.etag || obj.httpEtag);
      return new Response(body, {
        status: 200,
        headers: {
          "Content-Type": "application/json",
          "X-R2-Etag": etag,
        },
      });
    }

    if (request.method === "PUT") {
      const ifMatch = stripEtagQuotes(request.headers.get("If-Match") || "");
      const ifNoneMatch = (request.headers.get("If-None-Match") || "").trim();
      let onlyIf;
      if (ifNoneMatch === "*" && !ifMatch) {
        const existing = await bucket.head("vault/manifest.json");
        if (existing) {
          return Response.json({ error: "manifest_already_exists" }, { status: 412 });
        }
        onlyIf = { etagDoesNotMatch: "*" };
      } else if (ifMatch) {
        onlyIf = { etagMatches: ifMatch };
      } else {
        return Response.json({ error: "precondition_header_required" }, { status: 400 });
      }

      const rawBytes = await request.arrayBuffer();
      if (rawBytes.byteLength === 0 || rawBytes.byteLength > 1024 * 1024) {
        return Response.json({ error: "invalid_manifest_size" }, { status: 400 });
      }
      let parsed;
      try {
        parsed = JSON.parse(new TextDecoder().decode(rawBytes));
      } catch (_err) {
        return Response.json({ error: "invalid_manifest_json" }, { status: 400 });
      }
      if (!parsed || typeof parsed !== "object" || typeof parsed.revision !== "number") {
        return Response.json({ error: "invalid_manifest_fields" }, { status: 400 });
      }

      const putRes = await bucket.put("vault/manifest.json", rawBytes, {
        onlyIf,
        httpMetadata: { contentType: "application/json" },
      });
      if (!putRes) {
        return Response.json({ error: "manifest_etag_conflict" }, { status: 412 });
      }
      const newEtag = stripEtagQuotes(putRes.etag || putRes.httpEtag);
      return Response.json({ ok: true, etag: newEtag }, { status: 200 });
    }

    return Response.json({ error: "method_not_allowed" }, { status: 405 });
  }

  async _handleObject(request, bucket, objectKey, maxBytes, options = {}) {
    if (request.method === "HEAD") {
      const meta = await bucket.head(objectKey);
      if (!meta) {
        return Response.json({ error: "not_found" }, { status: 404 });
      }
      return new Response(null, {
        status: 200,
        headers: {
          "Content-Length": String(meta.size || 0),
          "X-Content-Sha256": String(meta.customMetadata?.sha256 || ""),
          "X-R2-Etag": stripEtagQuotes(meta.etag || meta.httpEtag),
        },
      });
    }

    if (request.method === "GET") {
      const obj = await bucket.get(objectKey);
      if (!obj) {
        return Response.json({ error: "not_found" }, { status: 404 });
      }
      const headers = new Headers();
      headers.set("Content-Type", obj.httpMetadata?.contentType || "application/octet-stream");
      headers.set("Content-Length", String(obj.size || 0));
      headers.set("X-Content-Sha256", String(obj.customMetadata?.sha256 || ""));
      headers.set("X-R2-Etag", stripEtagQuotes(obj.etag || obj.httpEtag));
      return new Response(obj.body, { status: 200, headers });
    }

    if (request.method === "PUT") {
      const declaredLength = Number(request.headers.get("Content-Length") || "0");
      if (declaredLength > maxBytes) {
        return Response.json({ error: "payload_too_large" }, { status: 413 });
      }
      const bodyBytes = await request.arrayBuffer();
      if (bodyBytes.byteLength === 0 || bodyBytes.byteLength > maxBytes) {
        return Response.json(
          { error: bodyBytes.byteLength === 0 ? "empty_payload" : "payload_too_large" },
          { status: bodyBytes.byteLength === 0 ? 400 : 413 }
        );
      }

      const expectedSha = (request.headers.get("X-Content-Sha256") || "").trim().toLowerCase();
      if (!/^[a-f0-9]{64}$/.test(expectedSha)) {
        return Response.json({ error: "missing_or_invalid_sha256" }, { status: 400 });
      }
      const actualSha = await sha256Hex(bodyBytes);
      if (actualSha !== expectedSha) {
        return Response.json({ error: "sha256_mismatch" }, { status: 400 });
      }

      const putRes = await bucket.put(objectKey, bodyBytes, {
        httpMetadata: { contentType: options.contentType || "application/octet-stream" },
        customMetadata: {
          sha256: actualSha,
          size: String(bodyBytes.byteLength),
          ...(options.extraMetadata || {}),
        },
      });
      return Response.json(
        {
          ok: true,
          key: objectKey,
          size: bodyBytes.byteLength,
          sha256: actualSha,
          etag: stripEtagQuotes(putRes?.etag || putRes?.httpEtag || ""),
        },
        { status: 200 }
      );
    }

    if (request.method === "DELETE") {
      await bucket.delete(objectKey);
      return Response.json({ ok: true, deleted: objectKey }, { status: 200 });
    }

    return Response.json({ error: "method_not_allowed" }, { status: 405 });
  }
}

/**
 * Durable Object wrapper for Nong ChokChuay FastAPI + OpenThai 2.0 ONNX container.
 * Starts the container on demand, keeps it warm for 3 minutes via Durable Object alarms,
 * and destroys it on idle to reduce runtime costs.
 */
export class NongChokChuayOCR extends DurableObject {
  async ensureContainerReady(container) {
    const run = async () => {
      const containerEnv = buildContainerEnv(this.env);
      const fingerprint = await computeConfigFingerprint(containerEnv);
      const previousFingerprint = await this.ctx.storage.get("configFingerprint");
      let internalToken = await this.ctx.storage.get("internalSyncToken");
      let containerEpoch = await this.ctx.storage.get("containerEpoch");
      const configChanged = previousFingerprint !== fingerprint;
      const missingAuthContext = !internalToken || !containerEpoch;

      if ((configChanged || missingAuthContext) && container.running) {
        await container.destroy();
      }

      const needsStart = !container.running || configChanged || missingAuthContext;
      if (needsStart) {
        internalToken = generateRandomHex(32);
        containerEpoch = crypto.randomUUID();
        await this.ctx.storage.put("configFingerprint", fingerprint);
        await this.ctx.storage.put("internalSyncToken", internalToken);
        await this.ctx.storage.put("containerEpoch", containerEpoch);
        this._interceptEpoch = "";
      }

      if (
        this.env?.VAULT_BUCKET &&
        typeof container.interceptOutboundHttp === "function" &&
        this.ctx?.exports?.VaultR2Gateway &&
        this._interceptEpoch !== containerEpoch
      ) {
        await container.interceptOutboundHttp(
          "r2.internal",
          this.ctx.exports.VaultR2Gateway({
            props: { internalToken, containerEpoch },
          })
        );
        this._interceptEpoch = containerEpoch;
      }

      if (needsStart) {
        container.start({
          enableInternet: true,
          env: {
            ...containerEnv,
            INTERNAL_SYNC_TOKEN: internalToken,
            CONTAINER_EPOCH: containerEpoch,
          },
        });
      }
    };
    this._readyLock = (this._readyLock || Promise.resolve()).then(run, run);
    return this._readyLock;
  }

  async fetch(request) {
    this._activeRequests = (this._activeRequests || 0) + 1;
    await this.ctx.storage.setAlarm(Date.now() + SLEEP_AFTER_MS);

    try {
      const container = this.ctx.container;
      if (!container) {
        return Response.json(
          {
            status: "starting",
            brand: "น้องโชคช่วย (Nong ChokChuay)",
            message: "Container is starting up, please retry in a few seconds.",
          },
          { status: 503 }
        );
      }

      await this.ensureContainerReady(container);

      const incomingUrl = new URL(request.url);
      const targetUrl = `http://container${incomingUrl.pathname}${incomingUrl.search}`;
      const hasBody = request.method !== "GET" && request.method !== "HEAD";
      const bodyBuffer = hasBody ? await request.arrayBuffer() : undefined;

      const forwardedHeaders = new Headers();
      for (const [key, value] of request.headers.entries()) {
        const lower = key.toLowerCase();
        if (lower.startsWith("x-vault-") || lower.startsWith("x-internal-")) {
          continue;
        }
        forwardedHeaders.set(key, value);
      }
      forwardedHeaders.set("X-Forwarded-Proto", incomingUrl.protocol.replace(/:$/, "") || "https");
      forwardedHeaders.set("X-Forwarded-Host", incomingUrl.host);

      const port = container.getTcpPort(CONTAINER_PORT);
      for (let attempt = 0; attempt < 25; attempt += 1) {
        try {
          const forwarded = new Request(targetUrl, {
            method: request.method,
            headers: forwardedHeaders,
            body: bodyBuffer,
            redirect: "manual",
          });
          return await port.fetch(forwarded);
        } catch (_err) {
          await new Promise((resolve) => setTimeout(resolve, 500));
        }
      }

      return Response.json(
        {
          status: "starting",
          brand: "น้องโชคช่วย (Nong ChokChuay)",
          message: "Container is starting up, please retry in a few seconds.",
        },
        { status: 503 }
      );
    } finally {
      this._activeRequests = Math.max(0, (this._activeRequests || 1) - 1);
      await this.ctx.storage.setAlarm(Date.now() + SLEEP_AFTER_MS);
    }
  }

  async alarm() {
    if (!this.ctx.container || !this.ctx.container.running) {
      return;
    }
    if ((this._activeRequests || 0) > 0) {
      await this.ctx.storage.setAlarm(Date.now() + SLEEP_AFTER_MS);
      return;
    }

    try {
      const port = this.ctx.container.getTcpPort(CONTAINER_PORT);
      const healthRes = await port.fetch(new Request("http://container/api/health", { method: "GET" }));
      if (healthRes) {
        const health = await healthRes.json();
        if (health && (health.busy === true || health.storage_state === "RESTORING")) {
          await this.ctx.storage.setAlarm(Date.now() + SLEEP_AFTER_MS);
          return;
        }
      }
    } catch (_err) {
      // Ignore non-JSON or unreachable health responses when idle alarm fires.
    }

    await this.ctx.container.destroy();
  }
}

export default {
  async fetch(request, env) {
    const id = env.NONG_CHOK_CONTAINER.idFromName("singleton");
    return env.NONG_CHOK_CONTAINER.get(id).fetch(request);
  },
};
