import { appendFileSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { mkdirSync } from "node:fs";

export interface DraftOutcome {
  accountId?: string;
  messageId?: string;
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

export interface LearnedInsight {
  category: string;
  generatedText: string;
  finalText: string;
  diffSummary: string;
  recordedAt: string;
}

const safeAutonomyCategories = new Set([
  "filters",
  "home_page",
  "domain_management",
  "header",
  "main_menu",
  "footer",
  "general_settings",
  "design_settings",
  "managers",
  "how_to",
  "troubleshooting",
  "follow_up",
]);

function normalize(text: string): string {
  return text.trim().replace(/\s+/g, " ");
}

function redactPersonalDetails(text: string): string {
  return text
    .replace(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi, "[email]")
    .replace(/(?:\+?\d[\d ().-]{7,}\d)/g, "[phone]")
    .replace(/\b(?:\d[ -]*?){13,19}\b/g, "[number]");
}

export function summarizeDiff(original: string, final: string): string {
  const normOrig = original.trim();
  const normFinal = final.trim();
  if (normalize(normOrig) === normalize(normFinal)) return "Approved directly without edits";

  const origLines = normOrig.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
  const finalLines = normFinal.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);

  const added = finalLines.filter((line) => !origLines.includes(line));
  const removed = origLines.filter((line) => !finalLines.includes(line));

  const parts: string[] = [];
  if (added.length) {
    const snippet = added[0].length > 60 ? `${added[0].slice(0, 60)}...` : added[0];
    parts.push(`Added: "${snippet}"`);
  }
  if (removed.length) {
    const snippet = removed[0].length > 60 ? `${removed[0].slice(0, 60)}...` : removed[0];
    parts.push(`Removed: "${snippet}"`);
  }
  if (!parts.length) {
    parts.push("Refined phrasing / tone");
  }
  return parts.join("; ");
}

export function recordOutcome(
  outcome: DraftOutcome,
  ledgerPath = process.env.APPROVAL_LEDGER_PATH ?? join(process.cwd(), "data", "approval-ledger.jsonl"),
  approvalThreshold = 20,
): AutonomyStatus {
  const isUneditedSend = outcome.decision === "sent"
    && normalize(outcome.generatedText) === normalize(outcome.finalText)
    && outcome.qualityPassed;
  const redactedGenerated = redactPersonalDetails(outcome.generatedText);
  const redactedFinal = redactPersonalDetails(outcome.finalText);
  const diffSummary = redactPersonalDetails(summarizeDiff(redactedGenerated, redactedFinal));
  const entry = {
    ...outcome,
    generatedText: redactedGenerated,
    finalText: redactedFinal,
    modelVersion: outcome.modelVersion ?? process.env.ANTHROPIC_MODEL ?? "deterministic-offline",
    promptVersion: outcome.promptVersion ?? "inbox-draft-v2",
    isUneditedSend,
    diffSummary,
    recordedAt: new Date().toISOString(),
  };
  mkdirSync(dirname(ledgerPath), { recursive: true });
  appendFileSync(ledgerPath, `${JSON.stringify(entry)}\n`, "utf8");

  let consecutiveUnedited = 0;
  try {
    const records = readFileSync(ledgerPath, "utf8").trim().split(/\r?\n/).filter(Boolean);
    for (const line of records.reverse()) {
      const prior = JSON.parse(line) as typeof entry;
      if (prior.accountId !== outcome.accountId) continue;
      if (prior.category !== outcome.category) continue;
      if (!prior.isUneditedSend) break;
      consecutiveUnedited += 1;
    }
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") {
      throw new Error("Could not read the Inbox approval ledger.");
    }
  }

  return {
    consecutiveUnedited,
    eligible: safeAutonomyCategories.has(outcome.category) && consecutiveUnedited >= approvalThreshold,
  };
}

export function getCategoryApprovalCounts(
  accountId: string,
  ledgerPath = process.env.APPROVAL_LEDGER_PATH ?? join(process.cwd(), "data", "approval-ledger.jsonl"),
): Record<string, number> {
  let records: { accountId?: string; category?: string; isUneditedSend?: boolean }[];
  try {
    records = readFileSync(ledgerPath, "utf8")
      .split(/\r?\n/)
      .filter(Boolean)
      .map((line) => JSON.parse(line) as { accountId?: string; category?: string; isUneditedSend?: boolean });
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return {};
    throw new Error("Could not read the Inbox approval ledger.");
  }
  const counts: Record<string, number> = {};
  const brokenStreak = new Set<string>();
  for (const entry of [...records].reverse()) {
    if (entry.accountId !== accountId || !entry.category) continue;
    if (brokenStreak.has(entry.category)) continue;
    if (!entry.isUneditedSend) {
      brokenStreak.add(entry.category);
      if (!(entry.category in counts)) counts[entry.category] = 0;
      continue;
    }
    counts[entry.category] = (counts[entry.category] ?? 0) + 1;
  }
  return counts;
}

export function hasSentMessage(
  accountId: string,
  messageId: string,
  ledgerPath = process.env.APPROVAL_LEDGER_PATH ?? join(process.cwd(), "data", "approval-ledger.jsonl"),
): boolean {
  try {
    return readFileSync(ledgerPath, "utf8")
      .split(/\r?\n/)
      .filter(Boolean)
      .some((line) => {
        const entry = JSON.parse(line) as DraftOutcome;
        return entry.accountId === accountId && entry.messageId === messageId && entry.decision === "sent";
      });
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return false;
    throw new Error("Could not read the Inbox message approval history.");
  }
}

export function getLearnedExamples(
  category: string,
  limit = 5,
  ledgerPath = process.env.APPROVAL_LEDGER_PATH ?? join(process.cwd(), "data", "approval-ledger.jsonl"),
  accountId?: string,
): LearnedExample[] {
  try {
    return readFileSync(ledgerPath, "utf8")
      .split(/\r?\n/)
      .filter(Boolean)
      .map((line) => JSON.parse(line) as DraftOutcome & { diffSummary?: string })
      .filter((entry) => entry.category === category && (accountId === undefined || entry.accountId === accountId)
        && entry.decision === "sent"
        && Boolean(entry.finalText) && normalize(entry.generatedText) !== normalize(entry.finalText))
      .slice(-limit)
      .map((entry) => ({
        generatedText: entry.generatedText,
        correctedText: entry.finalText,
      }));
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return [];
    throw new Error("Could not read category-specific Inbox learning examples.");
  }
}

export function getLearnedInsights(
  category?: string,
  limit = 20,
  ledgerPath = process.env.APPROVAL_LEDGER_PATH ?? join(process.cwd(), "data", "approval-ledger.jsonl"),
  accountId?: string,
): LearnedInsight[] {
  try {
    return readFileSync(ledgerPath, "utf8")
      .split(/\r?\n/)
      .filter(Boolean)
      .map((line) => JSON.parse(line) as DraftOutcome & { diffSummary?: string; recordedAt?: string })
      .filter((entry) => (category === undefined || entry.category === category)
        && (accountId === undefined || entry.accountId === accountId)
        && entry.decision === "sent"
        && Boolean(entry.finalText)
        && normalize(entry.generatedText) !== normalize(entry.finalText))
      .slice(-limit)
      .map((entry) => ({
        category: entry.category,
        generatedText: entry.generatedText,
        finalText: entry.finalText,
        diffSummary: entry.diffSummary || summarizeDiff(entry.generatedText, entry.finalText),
        recordedAt: entry.recordedAt || new Date().toISOString(),
      }));
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return [];
    return [];
  }
}
