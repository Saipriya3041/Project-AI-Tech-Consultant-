import { appendFileSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { mkdirSync } from "node:fs";

export interface DraftOutcome {
  category: string;
  generatedText: string;
  finalText: string;
  decision: "sent" | "rejected";
  qualityPassed: boolean;
  modelVersion?: string;
  promptVersion?: string;
}

export interface AutonomyStatus {
  consecutiveUnedited: number;
  eligible: boolean;
}

export interface LearnedExample {
  generatedText: string;
  correctedText: string;
}

const safeAutonomyCategories = new Set(["how_to", "troubleshooting", "follow_up"]);

function normalize(text: string): string {
  return text.trim().replace(/\s+/g, " ");
}

function redactPersonalDetails(text: string): string {
  return text
    .replace(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi, "[email]")
    .replace(/(?:\+?\d[\d ().-]{7,}\d)/g, "[phone]")
    .replace(/\b(?:\d[ -]*?){13,19}\b/g, "[number]");
}

export function recordOutcome(
  outcome: DraftOutcome,
  ledgerPath = process.env.APPROVAL_LEDGER_PATH ?? join(process.cwd(), "data", "approval-ledger.jsonl"),
): AutonomyStatus {
  const isUneditedSend = outcome.decision === "sent"
    && normalize(outcome.generatedText) === normalize(outcome.finalText)
    && outcome.qualityPassed;
  const entry = {
    ...outcome,
    generatedText: redactPersonalDetails(outcome.generatedText),
    finalText: redactPersonalDetails(outcome.finalText),
    modelVersion: outcome.modelVersion ?? process.env.ANTHROPIC_MODEL ?? "deterministic-offline",
    promptVersion: outcome.promptVersion ?? "inbox-draft-v2",
    isUneditedSend,
    recordedAt: new Date().toISOString(),
  };
  mkdirSync(dirname(ledgerPath), { recursive: true });
  appendFileSync(ledgerPath, `${JSON.stringify(entry)}\n`, "utf8");

  let consecutiveUnedited = 0;
  try {
    const records = readFileSync(ledgerPath, "utf8").trim().split(/\r?\n/).filter(Boolean);
    for (const line of records.reverse()) {
      const prior = JSON.parse(line) as typeof entry;
      if (prior.category !== outcome.category) continue;
      if (!prior.isUneditedSend) break;
      consecutiveUnedited += 1;
    }
  } catch {
    consecutiveUnedited = 0;
  }

  return {
    consecutiveUnedited,
    eligible: safeAutonomyCategories.has(outcome.category) && consecutiveUnedited >= 20,
  };
}

export function getLearnedExamples(
  category: string,
  limit = 3,
  ledgerPath = process.env.APPROVAL_LEDGER_PATH ?? join(process.cwd(), "data", "approval-ledger.jsonl"),
): LearnedExample[] {
  try {
    return readFileSync(ledgerPath, "utf8")
      .split(/\r?\n/)
      .filter(Boolean)
      .map((line) => JSON.parse(line) as DraftOutcome)
      .filter((entry) => entry.category === category && entry.decision === "sent"
        && Boolean(entry.finalText) && normalize(entry.generatedText) !== normalize(entry.finalText))
      .slice(-limit)
      .map((entry) => ({ generatedText: entry.generatedText, correctedText: entry.finalText }));
  } catch {
    return [];
  }
}
