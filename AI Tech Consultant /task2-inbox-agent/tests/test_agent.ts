import { classifyEmail } from "../src/email/classifier";
import { emailDataset } from "../src/email/dataset";
import { runEvals } from "../src/evaluation/evals";
import { getLearnedExamples, recordOutcome } from "../src/feedback/learn";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

test("refund classification", () => {
  expect(classifyEmail("Refund", "Please refund")).toBe("refund_cancellation");
});

test("classifies common requests and flags complaints", () => {
  expect(classifyEmail("Setup", "How do I update my logo?")).toBe("how_to");
  expect(classifyEmail("Checkout error", "Payment is not working")).toBe("troubleshooting");
  expect(classifyEmail("New feature", "We need a custom booking integration")).toBe("project_request");
  expect(classifyEmail("Pricing", "How much does this change cost?")).toBe("quote_request");
  expect(classifyEmail("Unacceptable service", "I am very disappointed")).toBe("complaint");
  expect(classifyEmail("Checking in", "Any update on my previous email?")).toBe("follow_up");
});

test("provides 50 realistic cases and evaluates each expected category", () => {
  expect(emailDataset).toHaveLength(50);
  expect(runEvals()).toMatchObject({ total: 50, passed: 50, accuracy: 1, failures: [] });
});

test("requires 20 unchanged quality-passed sends and keeps risky categories human-reviewed", () => {
  const directory = mkdtempSync(join(tmpdir(), "inbox-ledger-"));
  const ledgerPath = join(directory, "outcomes.jsonl");
  try {
    for (let index = 0; index < 19; index += 1) {
      const status = recordOutcome({
        category: "how_to",
        generatedText: "Use the settings page.",
        finalText: "Use the settings page.",
        decision: "sent",
        qualityPassed: true,
      }, ledgerPath);
      expect(status.eligible).toBe(false);
    }
    expect(recordOutcome({
      category: "how_to",
      generatedText: "Use the settings page.",
      finalText: "Use the settings page.",
      decision: "sent",
      qualityPassed: true,
    }, ledgerPath)).toEqual({ consecutiveUnedited: 20, eligible: true });

    for (let index = 0; index < 20; index += 1) {
      recordOutcome({
        category: "refund_cancellation",
        generatedText: "We will refund you.",
        finalText: "We will refund you.",
        decision: "sent",
        qualityPassed: true,
      }, ledgerPath);
    }
    expect(recordOutcome({
      category: "refund_cancellation",
      generatedText: "We will refund you.",
      finalText: "We will refund you.",
      decision: "sent",
      qualityPassed: true,
    }, ledgerPath).eligible).toBe(false);
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});

test("learns from human-edited sends while redacting contact details in the ledger", () => {
  const directory = mkdtempSync(join(tmpdir(), "inbox-feedback-"));
  const ledgerPath = join(directory, "outcomes.jsonl");
  try {
    recordOutcome({
      category: "how_to",
      generatedText: "Hello Alex at alex@example.com, call 555-123-9876 for help.",
      finalText: "Hello, use the account settings page to make this change.",
      decision: "sent",
      qualityPassed: true,
    }, ledgerPath);
    expect(getLearnedExamples("how_to", 3, ledgerPath)).toEqual([{
      generatedText: "Hello Alex at [email], call [phone] for help.",
      correctedText: "Hello, use the account settings page to make this change.",
    }]);
    const ledger = readFileSync(ledgerPath, "utf8");
    expect(ledger).not.toContain("alex@example.com");
    expect(ledger).not.toContain("555-123-9876");
    expect(ledger).toContain('"promptVersion":"inbox-draft-v2"');
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});
