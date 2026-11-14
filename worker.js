import { DurableObject } from "cloudflare:workers";

const SLEEP_AFTER_MS = 3 * 60 * 1000;
const CONTAINER_PORT = 8765;

/**
 * Durable Object wrapper for Nong ChokChuay FastAPI + OpenThai 2.0 ONNX container.
 * Starts the container on demand, keeps it warm for 3 minutes via Durable Object alarms,
 * and destroys it on idle to stay inside Cloudflare Workers Paid ($5/month).
 */
export class NongChokChuayOCR extends DurableObject {
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

    if (!container.running) {
      container.start({
        enableInternet: true,
        env: {
          AUTO_EXTRACT_ON_INGEST: String(this.env.AUTO_EXTRACT_ON_INGEST || "true"),
          PUBLIC_BASE_URL: String(this.env.PUBLIC_BASE_URL || ""),
          LINE_CHANNEL_SECRET: String(this.env.LINE_CHANNEL_SECRET || ""),
          LINE_CHANNEL_ACCESS_TOKEN: String(this.env.LINE_CHANNEL_ACCESS_TOKEN || ""),
          LLM_API_KEY: String(this.env.LLM_API_KEY || ""),
        },
      });
    }

    const incomingUrl = new URL(request.url);
    const targetUrl = `http://container${incomingUrl.pathname}${incomingUrl.search}`;
    const hasBody = request.method !== "GET" && request.method !== "HEAD";
    const bodyBuffer = hasBody ? await request.arrayBuffer() : undefined;

    const port = container.getTcpPort(CONTAINER_PORT);
    for (let attempt = 0; attempt < 25; attempt += 1) {
      try {
        const forwarded = new Request(targetUrl, {
          method: request.method,
          headers: request.headers,
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
    const key =
      env.LINE_CHANNEL_SECRET && env.LINE_CHANNEL_ACCESS_TOKEN
        ? "singleton-line"
        : "singleton";
    const id = env.NONG_CHOK_CONTAINER.idFromName(key);
    return env.NONG_CHOK_CONTAINER.get(id).fetch(request);
  },
};
