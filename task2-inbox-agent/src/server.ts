import { createServer, IncomingMessage, ServerResponse } from "node:http";
import { draftReply } from "./drafting/draft";
import { recordOutcome } from "./feedback/learn";

const port = Number(process.env.PORT ?? 8012);
const allowedOrigins = new Set(["http://127.0.0.1:8081", "http://localhost:8081"]);

async function handleRequest(request: IncomingMessage, response: ServerResponse) {
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

  try {
    if (request.method === "GET" && request.url === "/health") {
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ status: "ok" }));
      return;
    }

    if (request.method === "POST" && request.url === "/feedback") {
      const body = await readBody(request);
      const outcome = JSON.parse(body);
      if (
        typeof outcome.category !== "string" ||
        typeof outcome.generatedText !== "string" ||
        typeof outcome.finalText !== "string" ||
        !["sent", "rejected"].includes(String(outcome.decision)) ||
        typeof outcome.qualityPassed !== "boolean"
      ) {
        response.writeHead(422, { "Content-Type": "application/json" });
        response.end(JSON.stringify({ error: "Invalid review outcome" }));
        return;
      }
      const status = recordOutcome(outcome);
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end(JSON.stringify(status));
      return;
    }

    if (request.method === "POST" && request.url === "/draft") {
      const body = await readBody(request);
      const input = JSON.parse(body);
      if (typeof input.subject !== "string" || typeof input.body !== "string" || !input.body.trim()) {
        response.writeHead(422, { "Content-Type": "application/json" });
        response.end(JSON.stringify({ error: "subject and non-empty body are required" }));
        return;
      }
      const result = await draftReply(input.body, input.subject);
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end(JSON.stringify(result));
      return;
    }

    response.writeHead(404, { "Content-Type": "application/json" });
    response.end(JSON.stringify({ error: "Not found" }));
  } catch (err) {
    console.error("Server error:", err);
    response.writeHead(500, { "Content-Type": "application/json" });
    response.end(JSON.stringify({ error: "Internal server error" }));
  }
}

async function readBody(request: IncomingMessage): Promise<string> {
  let body = "";
  for await (const chunk of request) {
    body += chunk.toString();
    if (body.length > 64000) throw new Error("Request body too large");
  }
  return body;
}

const server = createServer(handleRequest);

// ✅ For local development
if (process.env.NODE_ENV !== "production") {
  server.listen(port, () => {
    console.log(`Inbox Agent API listening at http://localhost:${port}`);
  });
}

// ✅ For Vercel serverless compatibility
export default server;
