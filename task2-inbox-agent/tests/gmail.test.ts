import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  completeGmailAuthorization,
  disconnectGmail,
  extractGmailBody,
  getGmailAuthorizationUrl,
  getGmailStatus,
  isGmailConfigured,
  listGmailMessages,
  sendGmailMessage,
  sendGmailReply,
} from "../src/gmail/client";

test("extracts readable text from nested Gmail MIME bodies", () => {
  const encoded = (text: string) => Buffer.from(text).toString("base64url");
  expect(extractGmailBody({
    mimeType: "multipart/alternative",
    parts: [
      { mimeType: "text/html", body: { data: encoded("<p>Hi&nbsp;A&#x26;B</p><script>hidden()</script><p>Order &#35;42</p>") } },
      { mimeType: "multipart/related", parts: [
        { mimeType: "image/png" },
        { mimeType: "text/plain", body: { data: encoded("Hi A&B\nOrder #42") } },
      ] },
    ],
  })).toBe("Hi A&B\nOrder #42");
  expect(extractGmailBody({
    mimeType: "text/html",
    body: { data: encoded("<p>Hi&nbsp;A&#x26;B</p><p>Order &#35;42</p><script>hidden()</script>") },
  })).toBe("Hi A&B\nOrder #42");
  expect(extractGmailBody({ mimeType: "multipart/mixed", parts: [{ mimeType: "application/pdf" }] }, "Message preview"))
    .toBe("Message preview");
});

test("does not treat example OAuth credentials as configured", () => {
  const previous = {
    GMAIL_CLIENT_ID: process.env.GMAIL_CLIENT_ID,
    GMAIL_CLIENT_SECRET: process.env.GMAIL_CLIENT_SECRET,
    GMAIL_REDIRECT_URI: process.env.GMAIL_REDIRECT_URI,
    GMAIL_TOKEN_ENCRYPTION_KEY: process.env.GMAIL_TOKEN_ENCRYPTION_KEY,
  };
  process.env.GMAIL_CLIENT_ID = "xxxxxxxx.apps.googleusercontent.com";
  process.env.GMAIL_CLIENT_SECRET = "GOCSPX-xxxxxxxxxxxx";
  process.env.GMAIL_REDIRECT_URI = "http://127.0.0.1:8012/gmail/callback";
  process.env.GMAIL_TOKEN_ENCRYPTION_KEY = "test-encryption-key-with-at-least-32-characters";
  try {
    expect(isGmailConfigured()).toBe(false);
  } finally {
    for (const [key, value] of Object.entries(previous)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  }
});

test("authorizes Gmail read and send, isolates encrypted connections, and fetches folder messages", async () => {
  const directory = mkdtempSync(join(tmpdir(), "gmail-oauth-"));
  const previousEnvironment = {
    GMAIL_CLIENT_ID: process.env.GMAIL_CLIENT_ID,
    GMAIL_CLIENT_SECRET: process.env.GMAIL_CLIENT_SECRET,
    GMAIL_REDIRECT_URI: process.env.GMAIL_REDIRECT_URI,
    GMAIL_TOKEN_ENCRYPTION_KEY: process.env.GMAIL_TOKEN_ENCRYPTION_KEY,
    GMAIL_TOKEN_PATH: process.env.GMAIL_TOKEN_PATH,
  };
  process.env.GMAIL_CLIENT_ID = "test-client";
  process.env.GMAIL_CLIENT_SECRET = "test-client-secret";
  process.env.GMAIL_REDIRECT_URI = "http://127.0.0.1:8012/gmail/callback";
  process.env.GMAIL_TOKEN_ENCRYPTION_KEY = "test-encryption-key-with-at-least-32-characters";
  process.env.GMAIL_TOKEN_PATH = join(directory, "tokens.enc");
  const fetch = jest.spyOn(global, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes("oauth2.googleapis.com/token")) {
      return new Response(JSON.stringify({
        access_token: "sensitive-access-token",
        refresh_token: "sensitive-refresh-token",
        expires_in: 3600,
        scope: "https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/gmail.send",
      }), { status: 200 });
    }
    if (url.endsWith("/profile")) {
      return new Response(JSON.stringify({ emailAddress: "customer@gmail.com" }), { status: 200 });
    }
    if (url.includes("/messages?")) {
      return new Response(JSON.stringify({ messages: [{ id: "mail-1", threadId: "thread-1" }] }), { status: 200 });
    }
    if (url.includes("/messages/mail-1?")) {
      return new Response(JSON.stringify({
        id: "mail-1",
        threadId: "thread-1",
        snippet: "A real customer message",
        internalDate: "1792756800000",
        payload: { headers: [
          { name: "From", value: "customer@example.com" },
          { name: "Subject", value: "Help with billing" },
          { name: "Date", value: "Fri, 23 Oct 2026 12:00:00 +0000" },
          { name: "Message-ID", value: "<original@example.com>" },
        ], mimeType: "text/plain", body: { data: Buffer.from("I need help with my billing.").toString("base64url") } },
      }), { status: 200 });
    }
    if (url.endsWith("/messages/send")) {
      return new Response(JSON.stringify({ id: "sent-1", threadId: "thread-1" }), { status: 200 });
    }
    throw new Error(`Unexpected URL ${url}`);
  });

  try {
    const authorizationUrl = new URL(getGmailAuthorizationUrl("account-1"));
    expect(authorizationUrl.searchParams.get("scope")).toContain("https://www.googleapis.com/auth/gmail.readonly");
    expect(authorizationUrl.searchParams.get("scope")).toContain("https://www.googleapis.com/auth/gmail.send");
    expect(authorizationUrl.searchParams.get("scope")).not.toContain("gmail.modify");
    await completeGmailAuthorization(
      "authorization-code",
      authorizationUrl.searchParams.get("state") ?? "",
    );

    expect(getGmailStatus("account-1")).toEqual({ connected: true, email: "customer@gmail.com", sendEnabled: true });
    expect(getGmailStatus("account-2")).toEqual({ connected: false, email: null, sendEnabled: false });
    await expect(listGmailMessages("account-1")).resolves.toEqual([{
      id: "mail-1",
      threadId: "thread-1",
      from: "customer@example.com",
      subject: "Help with billing",
      date: "Fri, 23 Oct 2026 12:00:00 +0000",
      snippet: "A real customer message",
      body: "I need help with my billing.",
      messageId: "<original@example.com>",
      internalDate: 1792756800000,
    }]);
    await expect(listGmailMessages("account-1", 25, "SPAM")).resolves.toHaveLength(1);
    await expect(listGmailMessages("account-1", 25, "INBOX", "from:customer@example.com"))
      .resolves.toHaveLength(1);
    expect(fetch.mock.calls.some(([input]) =>
      String(input).includes("labelIds=INBOX") && String(input).includes("q=from%3Acustomer%40example.com"),
    )).toBe(true);
    await expect(sendGmailReply("account-1", "mail-1", "Here is the reviewed answer."))
      .resolves.toEqual({ id: "sent-1", threadId: "thread-1" });
    await expect(sendGmailMessage("account-1", "recipient@example.com", "A new subject", "A new message."))
      .resolves.toEqual({ id: "sent-1", threadId: "thread-1" });
    const sentRequest = fetch.mock.calls[fetch.mock.calls.length - 1];
    const rawMessage = JSON.parse(String(sentRequest[1]?.body)).raw;
    const decodedMessage = Buffer.from(rawMessage, "base64url").toString("utf8");
    expect(decodedMessage).toContain("To: recipient@example.com");
    expect(decodedMessage).toContain("Subject: =?UTF-8?B?");
    expect(decodedMessage).toContain("A new message.");
    await expect(sendGmailMessage("account-1", "not-an-email", "Subject", "Body"))
      .rejects.toThrow("valid sender address");

    const encryptedTokens = readFileSync(process.env.GMAIL_TOKEN_PATH, "utf8");
    expect(encryptedTokens).not.toContain("sensitive-access-token");
    expect(encryptedTokens).not.toContain("sensitive-refresh-token");
    disconnectGmail("account-1");
    expect(getGmailStatus("account-1")).toEqual({ connected: false, email: null, sendEnabled: false });
  } finally {
    fetch.mockRestore();
    for (const [key, value] of Object.entries(previousEnvironment)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
    rmSync(directory, { recursive: true, force: true });
  }
});
