import { askKnowledgeBrain } from "../knowledge-client/client";
import { classifyEmail, EmailCategory } from "../email/classifier";
import { draftWithClaude } from "./claude";
import { getLearnedExamples } from "../feedback/learn";

export interface EmailDraft {
  category: EmailCategory;
  summary: string;
  reply: string | null;
  sources: { title?: string; url: string; relevance?: number }[];
  risk: "low" | "moderate" | "high";
  status: "draft_ready" | "review_required" | "knowledge_unavailable" | "model_unavailable";
  requiresApproval: boolean;
  nextAction: string;
}

export async function draftReply(email: string, subject = ""): Promise<EmailDraft> {
  const category = classifyEmail(subject, email);
  const risk = ["refund_cancellation", "complaint", "project_request", "quote_request"].includes(category)
    ? "high"
    : category === "troubleshooting" ? "moderate" : "low";

  try {
    const knowledge = await askKnowledgeBrain(`${subject}\n${email}`.trim());
    if (knowledge.status !== "answered" || knowledge.needs_human || knowledge.sources.length === 0) {
      return {
        category,
        summary: email.trim(),
        reply: null,
        sources: knowledge.sources,
        risk,
        status: "review_required",
        requiresApproval: true,
        nextAction: "Route to a support specialist; the knowledge service has no verified evidence.",
      };
    }

    let modelDraft: string | null;
    try {
      modelDraft = await draftWithClaude(
        email,
        knowledge.answer,
        knowledge.sources.map((source) => source.url),
        getLearnedExamples(category),
      );
    } catch {
      return {
        category, summary: email.trim(), reply: null, sources: knowledge.sources, risk,
        status: "model_unavailable", requiresApproval: true,
        nextAction: "Claude could not be reached; a human must write or review the response.",
      };
    }
    if (process.env.ANTHROPIC_API_KEY && process.env.ANTHROPIC_MODEL && !modelDraft) {
      return {
        category, summary: email.trim(), reply: null, sources: knowledge.sources, risk,
        status: "review_required", requiresApproval: true,
        nextAction: "Claude returned no grounded draft; write or review a response manually.",
      };
    }

    return {
      category,
      summary: email.trim(),
      reply: modelDraft ?? `Hello,\n\n${knowledge.answer}\n\nBest regards,\nSupport Team`,
      sources: knowledge.sources,
      risk,
      status: "draft_ready",
      requiresApproval: true,
      nextAction: "Review the cited answer before sending.",
    };
  } catch {
    return {
      category,
      summary: email.trim(),
      reply: null,
      sources: [],
      risk,
      status: "knowledge_unavailable",
      requiresApproval: true,
      nextAction: "Retry the knowledge request or route to a support specialist.",
    };
  }
}

if (require.main === module) {
  (async () => {
    const sampleEmail = "I need a refund for my plan.";
    const reply = await draftReply(sampleEmail);
    console.log(JSON.stringify(reply, null, 2));
  })().catch((error) => {
    console.error("Draft generation failed:", error);
    process.exitCode = 1;
  });
}
