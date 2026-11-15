import { DurableObject } from "cloudflare:workers";

const SLEEP_AFTER_MS = 3 * 60 * 1000;
const CONTAINER_PORT = 8765;

/**
 * Build explicit container environment variables and fail-closed production auth flags.
 */
export function buildContainerEnv(env = {}) {
  const containerEnv = {
    AUTO_EXTRACT_ON_INGEST: String(env.AUTO_EXTRACT_ON_INGEST || "true"),
    PUBLIC_BASE_URL: String(env.PUBLIC_BASE_URL || ""),
    TEACHER_NAME: String(env.TEACHER_NAME || "ครูพิ้งกี้"),
    LINE_CHANNEL_SECRET: String(env.LINE_CHANNEL_SECRET || ""),
    LINE_CHANNEL_ACCESS_TOKEN: String(env.LINE_CHANNEL_ACCESS_TOKEN || ""),
    ALLOWED_LINE_USER_IDS: String(env.ALLOWED_LINE_USER_IDS || ""),
    LINE_ALLOW_GROUP_CHATS: String(env.LINE_ALLOW_GROUP_CHATS || "false"),
    LLM_PROVIDER: String((env.LLM_PROVIDER || "").trim() || "gemini"),
    LLM_MODEL: String((env.LLM_MODEL || "").trim() || "gemini-4-hydrogen"),
    LLM_API_KEY: String(env.LLM_API_KEY || ""),
    APP_ENV: "production",
    AUTH_REQUIRED: "true",
    ALLOWED_EMAILS: String(env.ALLOWED_EMAILS || ""),
    GOOGLE_CLIENT_ID: String(env.GOOGLE_CLIENT_ID || ""),
    GOOGLE_CLIENT_SECRET: String(env.GOOGLE_CLIENT_SECRET || ""),
    AUTH_SESSION_SECRET: String(env.AUTH_SESSION_SECRET || ""),
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
 * Durable Object wrapper for Nong ChokChuay FastAPI + OpenThai 2.0 ONNX container.
 * Starts the container on demand, keeps it warm for 3 minutes via Durable Object alarms,
 * and destroys it on idle to stay inside Cloudflare Workers Paid ($5/month).
 */
export class NongChokChuayOCR extends DurableObject {
  async ensureContainerReady(container) {
    const run = async () => {
      const containerEnv = buildContainerEnv(this.env);
      const fingerprint = await computeConfigFingerprint(containerEnv);
      const previousFingerprint = await this.ctx.storage.get("configFingerprint");
      const configChanged = previousFingerprint !== fingerprint;

      if (configChanged && container.running) {
        await container.destroy();
      }
      if (!container.running || configChanged) {
        container.start({
          enableInternet: true,
          env: containerEnv,
        });
        await this.ctx.storage.put("configFingerprint", fingerprint);
      }
    };
    this._readyLock = (this._readyLock || Promise.resolve()).then(run, run);
    return this._readyLock;
  }

  async fetch(request) {
    await this.ctx.storage.setAlarm(Date.now() + SLEEP_AFTER_MS);

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

    const forwardedHeaders = new Headers(request.headers);
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
  }

  async alarm() {
    if (this.ctx.container && this.ctx.container.running) {
      await this.ctx.container.destroy();
    }
  }
}

export default {
  async fetch(request, env) {
    const id = env.NONG_CHOK_CONTAINER.idFromName("singleton");
    return env.NONG_CHOK_CONTAINER.get(id).fetch(request);
  },
};
