import { createServer, IncomingMessage, ServerResponse } from "node:http";
import { createHmac, timingSafeEqual } from "node:crypto";
import { draftReply } from "./drafting/draft";
import { getLearnedInsights, hasSentMessage, recordOutcome } from "./feedback/learn";
import {
  completeGmailAuthorization,
  connectDemoGmail,
  disconnectGmail,
  getConnectedGmailAccountIds,
  getGmailAuthorizationUrl,
  getGmailStatus,
  isGmailConfigured,
  listGmailMessages,
  sendGmailMessage,
  sendGmailReply,
  GmailMessage,
} from "./gmail/client";
import {
  getInboxCategoryStatus,
  getInboxSettings,
  saveInboxSettings,
  InboxSettings,
} from "./settings/inbox";
import { EMAIL_CATEGORIES } from "./email/classifier";

const port = Number(process.env.PORT ?? 8012);
const allowedOrigins = new Set(["http://127.0.0.1:8081", "http://localhost:8081"]);
const AUTO_SEND_POLL_INTERVAL_MS = 60_000;

type CategorizedGmailMessage = GmailMessage & { category?: string | null };

function authenticatedAccountId(request: IncomingMessage): string | null {
  const secret = process.env.GMAIL_SESSION_SECRET;
  if (!secret || secret.length < 32) return null;
  const authorization = request.headers.authorization;
  if (!authorization?.startsWith("Bearer ")) return null;
  const token = authorization.slice("Bearer ".length);
  const [payload, signature] = token.split(".");
  if (!payload || !signature) return null;
  const expected = createHmac("sha256", secret).update(payload).digest("base64url");
  const actualBytes = Buffer.from(signature);
  const expectedBytes = Buffer.from(expected);
  if (actualBytes.length !== expectedBytes.length || !timingSafeEqual(actualBytes, expectedBytes)) return null;
  try {
    const claims = JSON.parse(Buffer.from(payload, "base64url").toString("utf8")) as {
      sub?: unknown;
      exp?: unknown;
    };
    return typeof claims.sub === "string" && typeof claims.exp === "number" && claims.exp > Date.now()
      ? claims.sub
      : null;
  } catch {
    return null;
  }
}

async function processInboxMessage(accountId: string, email: GmailMessage, settings: InboxSettings) {
  if (hasSentMessage(accountId, email.id)) {
    return { ...email, category: null, draft: null, sent: true, draftError: null };
  }
  try {
    const draft = await draftReply(email.body || email.snippet, email.subject, settings.categories, accountId);
    const policy = getInboxCategoryStatus(accountId).find((status) => status.category === draft.category);
    const autoSendAfter = settings.autoSendSince?.[draft.category] ?? Number.POSITIVE_INFINITY;
    if (
      draft.reply
      && draft.status === "draft_ready"
      && draft.risk === "low"
      && policy?.eligible
      && policy.autoSendEnabled
      && email.internalDate > autoSendAfter
    ) {
      const sent = await sendGmailReply(accountId, email.id, draft.reply);
      recordOutcome({
        accountId,
        messageId: email.id,
        category: draft.category,
        generatedText: draft.reply,
        finalText: draft.reply,
        decision: "sent",
        qualityPassed: true,
      }, undefined, settings.approvalThreshold);
      return { ...email, category: draft.category, draft, sent: true, sentMessageId: sent.id, draftError: null };
    }
    return { ...email, category: draft.category, draft, sent: false, draftError: null };
  } catch (error) {
    return {
      ...email,
      category: null,
      draft: null,
      sent: false,
      draftError: error instanceof Error ? error.message : "Draft generation failed.",
    };
  }
}

async function processNewMailForAutoSend(): Promise<void> {
  for (const accountId of getConnectedGmailAccountIds()) {
    const settings = getInboxSettings(accountId);
    const eligibleCategories = getInboxCategoryStatus(accountId)
      .filter((category) => category.eligible && category.autoSendEnabled);
    if (!eligibleCategories.length) continue;
    const cutoff = Math.min(...eligibleCategories.map((category) =>
      settings.autoSendSince?.[category.category] ?? Number.POSITIVE_INFINITY));
    if (!Number.isFinite(cutoff)) continue;
    const messages = await listGmailMessages(accountId, 50, "INBOX");
    for (const email of messages) {
      if (email.internalDate <= cutoff || hasSentMessage(accountId, email.id)) continue;
      await processInboxMessage(accountId, email, settings);
    }
  }
}

async function handleRequest(request: IncomingMessage, response: ServerResponse) {
  const origin = request.headers.origin;
  if (origin && allowedOrigins.has(origin)) {
    response.setHeader("Access-Control-Allow-Origin", origin);
    response.setHeader("Vary", "Origin");
  }
  response.setHeader("Access-Control-Allow-Headers", "Authorization, Content-Type");
  response.setHeader("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS");

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

    const requestUrl = new URL(request.url ?? "/", `http://${request.headers.host ?? "localhost"}`);
    if (request.method === "GET" && requestUrl.pathname === "/gmail/status") {
      const accountId = authenticatedAccountId(request);
      if (!accountId) {
        response.writeHead(401, { "Content-Type": "application/json" });
        response.end(JSON.stringify({ error: "Sign in to the workbench to access your Gmail connection." }));
        return;
      }
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end(JSON.stringify({
        ...getGmailStatus(accountId),
        configured: isGmailConfigured(),
      }));
      return;
    }

    if (request.method === "POST" && requestUrl.pathname === "/gmail/auth-url") {
      const accountId = authenticatedAccountId(request);
      if (!accountId) {
        response.writeHead(401, { "Content-Type": "application/json" });
        response.end(JSON.stringify({ error: "Sign in to the workbench to connect Gmail." }));
        return;
      }
      if (!isGmailConfigured()) {
        response.writeHead(503, { "Content-Type": "application/json" });
        response.end(JSON.stringify({ error: "Gmail OAuth is not configured on this server." }));
        return;
      }
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ url: getGmailAuthorizationUrl(accountId) }));
      return;
    }

    if (request.method === "GET" && requestUrl.pathname === "/gmail/callback") {
      const appUrl = new URL("/console.html", process.env.APP_BASE_URL || "http://127.0.0.1:8081");
      const error = requestUrl.searchParams.get("error");
      if (error) {
        appUrl.searchParams.set("gmail_error", "authorization_denied");
      } else {
        const code = requestUrl.searchParams.get("code");
        const state = requestUrl.searchParams.get("state");
        if (!code || !state) {
          appUrl.searchParams.set("gmail_error", "invalid_callback");
        } else {
          try {
            await completeGmailAuthorization(code, state);
            appUrl.searchParams.set("gmail_connected", "1");
          } catch (callbackError) {
            console.error("Gmail authorization failed:", callbackError);
            appUrl.searchParams.set("gmail_error", "authorization_failed");
          }
        }
      }
      response.writeHead(302, { Location: appUrl.toString() });
      response.end();
      return;
    }

    if (request.method === "GET" && requestUrl.pathname === "/gmail/messages") {
      const accountId = authenticatedAccountId(request);
      if (!accountId) {
        response.writeHead(401, { "Content-Type": "application/json" });
        response.end(JSON.stringify({ error: "Sign in to the workbench to access Gmail." }));
        return;
      }
      const requestedCount = Number(requestUrl.searchParams.get("maxResults") ?? 25);
      if (!Number.isInteger(requestedCount) || requestedCount < 1 || requestedCount > 100) {
        response.writeHead(422, { "Content-Type": "application/json" });
        response.end(JSON.stringify({ error: "maxResults must be between 1 and 100" }));
        return;
      }
      const folder = requestUrl.searchParams.get("folder") ?? "INBOX";
      if (!["INBOX", "DRAFT", "SENT", "SPAM", "TRASH", "STARRED", "SNOOZED"].includes(folder)) {
        response.writeHead(422, { "Content-Type": "application/json" });
        response.end(JSON.stringify({ error: "Unsupported Gmail folder" }));
        return;
      }
      const query = requestUrl.searchParams.get("q") ?? "";
      if (query.length > 2000) {
        response.writeHead(422, { "Content-Type": "application/json" });
        response.end(JSON.stringify({ error: "Gmail search query must be 2000 characters or fewer." }));
        return;
      }
      const messages: CategorizedGmailMessage[] = await listGmailMessages(
        accountId,
        requestedCount,
        folder as "INBOX" | "DRAFT" | "SENT" | "SPAM" | "TRASH" | "STARRED" | "SNOOZED",
        query,
      );
      if (folder === "INBOX") {
        const settings = getInboxSettings(accountId);
        for (let index = 0; index < messages.length; index += 3) {
          const batch = messages.slice(index, index + 3);
          const processed = await Promise.all(batch.map((email) => processInboxMessage(accountId, email, settings)));
          messages.splice(index, processed.length, ...processed);
        }
      }
      const categoryParam = requestUrl.searchParams.get("category");
      const resultMessages = categoryParam
        ? messages.filter((m) => m.category === categoryParam)
        : messages;
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ messages: resultMessages }));
      return;
    }

    if (request.method === "POST" && requestUrl.pathname === "/gmail/demo/connect") {
      const accountId = authenticatedAccountId(request);
      if (!accountId) {
        response.writeHead(401, { "Content-Type": "application/json" });
        response.end(JSON.stringify({ error: "Sign in to the workbench to connect Gmail." }));
        return;
      }
      connectDemoGmail(accountId);
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ connected: true, email: "user.demo@gmail.com", sendEnabled: true, isDemo: true }));
      return;
    }

    if (request.method === "GET" && requestUrl.pathname === "/gmail/learning-history") {
      const accountId = authenticatedAccountId(request);
      if (!accountId) {
        response.writeHead(401, { "Content-Type": "application/json" });
        response.end(JSON.stringify({ error: "Sign in to the workbench to view learning history." }));
        return;
      }
      const category = requestUrl.searchParams.get("category") || undefined;
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end(JSON.stringify({
        insights: getLearnedInsights(category, 50, undefined, accountId),
      }));
      return;
    }

    if (request.method === "GET" && requestUrl.pathname === "/gmail/settings") {
      const accountId = authenticatedAccountId(request);
      if (!accountId) {
        response.writeHead(401, { "Content-Type": "application/json" });
        response.end(JSON.stringify({ error: "Sign in to the workbench to manage Inbox settings." }));
        return;
      }
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end(JSON.stringify({
        settings: getInboxSettings(accountId),
        availableCategories: EMAIL_CATEGORIES,
        categories: getInboxCategoryStatus(accountId),
        learnedInsights: getLearnedInsights(undefined, 20, undefined, accountId),
      }));
      return;
    }

    if (request.method === "PUT" && requestUrl.pathname === "/gmail/settings") {
      const accountId = authenticatedAccountId(request);
      if (!accountId) {
        response.writeHead(401, { "Content-Type": "application/json" });
        response.end(JSON.stringify({ error: "Sign in to the workbench to manage Inbox settings." }));
        return;
      }
      const input = JSON.parse(await readBody(request)) as {
        categories?: unknown;
        approvalThreshold?: unknown;
        autoSendCategories?: unknown;
      };
      try {
        const settings = saveInboxSettings(accountId, input as {
          categories: import("./email/classifier").EmailCategory[];
          approvalThreshold: number;
          autoSendCategories: import("./email/classifier").EmailCategory[];
        });
        response.writeHead(200, { "Content-Type": "application/json" });
        response.end(JSON.stringify({ settings, categories: getInboxCategoryStatus(accountId) }));
      } catch (error) {
        response.writeHead(422, { "Content-Type": "application/json" });
        response.end(JSON.stringify({ error: error instanceof Error ? error.message : "Invalid Inbox settings." }));
      }
      return;
    }

    if (request.method === "POST" && requestUrl.pathname === "/gmail/send") {
      const accountId = authenticatedAccountId(request);
      if (!accountId) {
        response.writeHead(401, { "Content-Type": "application/json" });
        response.end(JSON.stringify({ error: "Sign in to the workbench to send Gmail replies." }));
        return;
      }
      const input = JSON.parse(await readBody(request)) as {
        messageId?: unknown;
        category?: unknown;
        generatedText?: unknown;
        finalText?: unknown;
        qualityPassed?: unknown;
      };
      if (
        typeof input.messageId !== "string"
        || typeof input.category !== "string"
        || typeof input.generatedText !== "string"
        || typeof input.finalText !== "string"
        || input.qualityPassed !== true
      ) {
        response.writeHead(422, { "Content-Type": "application/json" });
        response.end(JSON.stringify({ error: "Confirm the quality review and provide the message, category, and final reply." }));
        return;
      }
      if (!getInboxSettings(accountId).categories.includes(input.category as import("./email/classifier").EmailCategory)) {
        response.writeHead(422, { "Content-Type": "application/json" });
        response.end(JSON.stringify({ error: "The message category is not enabled for this account." }));
        return;
      }
      if (hasSentMessage(accountId, input.messageId)) {
        response.writeHead(409, { "Content-Type": "application/json" });
        response.end(JSON.stringify({ error: "A reply has already been sent for this message." }));
        return;
      }
      const settings = getInboxSettings(accountId);
      const sent = await sendGmailReply(accountId, input.messageId, input.finalText);
      const status = recordOutcome({
        accountId,
        messageId: input.messageId,
        category: input.category,
        generatedText: input.generatedText,
        finalText: input.finalText,
        decision: "sent",
        qualityPassed: true,
      }, undefined, settings.approvalThreshold);
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ ...status, messageId: sent.id }));
      return;
    }

    if (request.method === "POST" && requestUrl.pathname === "/gmail/compose") {
      const accountId = authenticatedAccountId(request);
      if (!accountId) {
        response.writeHead(401, { "Content-Type": "application/json" });
        response.end(JSON.stringify({ error: "Sign in to the workbench to send Gmail messages." }));
        return;
      }
      const input = JSON.parse(await readBody(request)) as {
        to?: unknown;
        subject?: unknown;
        body?: unknown;
      };
      if (
        typeof input.to !== "string"
        || typeof input.subject !== "string"
        || typeof input.body !== "string"
        || !input.to.trim()
        || !input.subject.trim()
        || !input.body.trim()
      ) {
        response.writeHead(422, { "Content-Type": "application/json" });
        response.end(JSON.stringify({ error: "Recipient, subject, and message are required." }));
        return;
      }
      const sent = await sendGmailMessage(accountId, input.to, input.subject, input.body);
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ sent: true, messageId: sent.id, threadId: sent.threadId }));
      return;
    }

    if (request.method === "POST" && requestUrl.pathname === "/gmail/disconnect") {
      const accountId = authenticatedAccountId(request);
      if (!accountId) {
        response.writeHead(401, { "Content-Type": "application/json" });
        response.end(JSON.stringify({ error: "Sign in to the workbench to manage Gmail." }));
        return;
      }
      disconnectGmail(accountId);
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ connected: false }));
      return;
    }

    if (request.method === "POST" && request.url === "/feedback") {
      const accountId = authenticatedAccountId(request);
      if (!accountId) {
        response.writeHead(401, { "Content-Type": "application/json" });
        response.end(JSON.stringify({ error: "Sign in to record Inbox feedback." }));
        return;
      }
      const body = await readBody(request);
      const outcome = JSON.parse(body);
      if (
        typeof outcome.category !== "string" ||
        typeof outcome.generatedText !== "string" ||
        typeof outcome.finalText !== "string" ||
        outcome.decision !== "rejected" ||
        typeof outcome.qualityPassed !== "boolean"
      ) {
        response.writeHead(422, { "Content-Type": "application/json" });
        response.end(JSON.stringify({ error: "Invalid review outcome" }));
        return;
      }
      const settings = getInboxSettings(accountId);
      const status = recordOutcome({ ...outcome, accountId }, undefined, settings.approvalThreshold);
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end(JSON.stringify(status));
      return;
    }

    if (request.method === "POST" && request.url === "/draft") {
      const accountId = authenticatedAccountId(request);
      if (!accountId) {
        response.writeHead(401, { "Content-Type": "application/json" });
        response.end(JSON.stringify({ error: "Sign in to prepare Inbox drafts." }));
        return;
      }
      const body = await readBody(request);
      const input = JSON.parse(body);
      if (typeof input.subject !== "string" || typeof input.body !== "string" || !input.body.trim()) {
        response.writeHead(422, { "Content-Type": "application/json" });
        response.end(JSON.stringify({ error: "subject and non-empty body are required" }));
        return;
      }
      const result = await draftReply(input.body, input.subject, getInboxSettings(accountId).categories, accountId);
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
  const autoSendTimer = setInterval(() => {
    void processNewMailForAutoSend().catch((error) => {
      console.error("Automatic Gmail processing failed:", error);
    });
  }, AUTO_SEND_POLL_INTERVAL_MS);
  autoSendTimer.unref();
}

// ✅ For Vercel serverless compatibility
export default server;
