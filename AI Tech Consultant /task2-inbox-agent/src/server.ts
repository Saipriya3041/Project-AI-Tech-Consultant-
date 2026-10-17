import { createServer } from "node:http";
import { draftReply } from "./drafting/draft";
import { recordOutcome } from "./feedback/learn";

const port = Number(process.env.PORT ?? 8012);
const allowedOrigins = new Set(["http://127.0.0.1:8081", "http://localhost:8081"]);

const server = createServer(async (request, response) => {
  const origin = request.headers.origin;
  if (origin && allowedOrigins.has(origin)) {
    response.setHeader("Access-Control-Allow-Origin", origin);
    response.setHeader("Vary", "Origin");
  }
  response.setHeader("Access-Control-Allow-Headers", "Content-Type");
  response.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");

  if (request.method === "OPTIONS") {
    response.writeHead(204).end();
    return;
  }
  if (request.method === "GET" && request.url === "/health") {
    response.writeHead(200, { "Content-Type": "application/json" }).end(JSON.stringify({ status: "ok" }));
    return;
  }
  if (request.method === "POST" && request.url === "/feedback") {
    try {
      let body = "";
      for await (const chunk of request) {
        body += chunk.toString();
        if (body.length > 64000) {
          response.writeHead(413, { "Content-Type": "application/json" }).end(JSON.stringify({ error: "Request body too large" }));
          return;
        }
      }
      const outcome = JSON.parse(body) as {
        category?: unknown;
        generatedText?: unknown;
        finalText?: unknown;
        decision?: unknown;
        qualityPassed?: unknown;
      };
      if (typeof outcome.category !== "string" || typeof outcome.generatedText !== "string"
        || typeof outcome.finalText !== "string" || !["sent", "rejected"].includes(String(outcome.decision))
        || typeof outcome.qualityPassed !== "boolean") {
        response.writeHead(422, { "Content-Type": "application/json" }).end(JSON.stringify({ error: "Invalid review outcome" }));
        return;
      }
      const status = recordOutcome(outcome as Parameters<typeof recordOutcome>[0]);
      response.writeHead(200, { "Content-Type": "application/json" }).end(JSON.stringify(status));
    } catch {
      response.writeHead(400, { "Content-Type": "application/json" }).end(JSON.stringify({ error: "Invalid JSON request" }));
    }
    return;
  }
  if (request.method !== "POST" || request.url !== "/draft") {
    response.writeHead(404, { "Content-Type": "application/json" }).end(JSON.stringify({ error: "Not found" }));
    return;
  }

  try {
    let body = "";
    for await (const chunk of request) {
      body += chunk.toString();
      if (body.length > 64000) {
        response.writeHead(413, { "Content-Type": "application/json" }).end(JSON.stringify({ error: "Request body too large" }));
        return;
      }
    }
    const input = JSON.parse(body) as { subject?: unknown; body?: unknown };
    if (typeof input.subject !== "string" || typeof input.body !== "string" || !input.body.trim()) {
      response.writeHead(422, { "Content-Type": "application/json" }).end(JSON.stringify({ error: "subject and non-empty body are required" }));
      return;
    }
    const result = await draftReply(input.body, input.subject);
    response.writeHead(200, { "Content-Type": "application/json" }).end(JSON.stringify(result));
  } catch {
    response.writeHead(400, { "Content-Type": "application/json" }).end(JSON.stringify({ error: "Invalid JSON request" }));
  }
});

server.listen(port, "127.0.0.1", () => {
  console.log(`Inbox Agent API listening at http://127.0.0.1:${port}`);
});