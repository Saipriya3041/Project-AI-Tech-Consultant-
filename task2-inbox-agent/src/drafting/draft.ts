import { askKnowledgeBrain } from "../knowledge-client/client";
import { classifyEmail, EmailCategory } from "../email/classifier";
import { draftWithClaude } from "./claude";
import { getLearnedExamples } from "../feedback/learn";

export interface EmailDraft {
  category: EmailCategory;
  summary: string;
  reply: string;
  sources: { title?: string; url: string; relevance?: number }[];
  risk: "low" | "moderate" | "high";
  status: "draft_ready" | "review_required" | "knowledge_unavailable" | "model_unavailable";
  requiresApproval: boolean;
  nextAction: string;
}

const DEFAULT_CATEGORY_REPLIES: Record<string, string> = {
  filters: "Hello,\n\nThank you for reaching out regarding site filters. You can configure and manage your website search filters, price ranges, and category facets in the Admin Portal under Search Settings > Filter Options. If you would like custom filter attributes added, our team can help you enable them.\n\nBest regards,\nSupport Team",
  home_page: "Hello,\n\nThank you for contacting us regarding your home page. You can customize the homepage hero banner, featured listings, and layout sections in the Page Designer under Design Settings > Home Page Layout.\n\nBest regards,\nSupport Team",
  domain_management: "Hello,\n\nThank you for contacting us regarding your domain. To connect your custom domain, navigate to Domain Management in the settings panel and set your domain's DNS A-Record to point to the server IP address, with a CNAME record for 'www'. SSL certificates are provisioned automatically once DNS propagates.\n\nBest regards,\nSupport Team",
  header: "Hello,\n\nThank you for reaching out. You can update your website header, logo dimensions, top navigation bar, and announcement banners from the Header Settings section in the design editor.\n\nBest regards,\nSupport Team",
  main_menu: "Hello,\n\nThank you for contacting us. To add, edit, or reorder your navigation menu links and dropdowns, please navigate to Main Menu Manager under Website Settings.\n\nBest regards,\nSupport Team",
  footer: "Hello,\n\nThank you for contacting us. Your website footer layout, copyright details, custom links, and footer widget columns can be managed under Footer Settings in the site builder.\n\nBest regards,\nSupport Team",
  general_settings: "Hello,\n\nThank you for your question. Your website name, business contact email, default currency, and timezone can be updated under General Settings in your dashboard.\n\nBest regards,\nSupport Team",
  design_settings: "Hello,\n\nThank you for reaching out. You can adjust website brand colors, typography, theme styles, and custom CSS in the Design Settings panel under Appearance.\n\nBest regards,\nSupport Team",
  managers: "Hello,\n\nThank you for your inquiry. To invite new team managers, assign admin permissions, or manage existing administrator accounts, visit the Managers section under Account & Team Settings.\n\nBest regards,\nSupport Team",
  how_to: "Hello,\n\nThank you for reaching out. Here are the steps to update your configuration: navigate to your admin dashboard, select the relevant settings section, apply your changes, and click Save.\n\nBest regards,\nSupport Team",
  troubleshooting: "Hello,\n\nThank you for reporting this issue. We have logged the error details. Please try clearing your browser cache and verifying your connection settings. Our engineering team is also investigating the diagnostic logs.\n\nBest regards,\nSupport Team",
  quote_request: "Hello,\n\nThank you for your request for pricing and quote information. Our solutions team is reviewing your project details and will prepare a tailored estimate shortly.\n\nBest regards,\nSupport Team",
  refund_cancellation: "Hello,\n\nThank you for contacting our billing department. We have received your request regarding your account and billing. A customer care representative is reviewing your account history and will follow up shortly.\n\nBest regards,\nSupport Team",
  project_request: "Hello,\n\nThank you for sharing your project requirements. A technical consultant will review the scope and reach out to schedule an architecture discussion.\n\nBest regards,\nSupport Team",
  follow_up: "Hello,\n\nThank you for following up with us. We are currently working on your request and will provide a status update as soon as the review is complete.\n\nBest regards,\nSupport Team",
  complaint: "Hello,\n\nWe sincerely apologize for the inconvenience and frustration you experienced. We take your feedback seriously and a manager has been assigned to personally review this matter.\n\nBest regards,\nSupport Team",
  general: "Hello,\n\nThank you for contacting us. We have received your message and our team is reviewing your inquiry.\n\nBest regards,\nSupport Team",
};

export async function draftReply(
  email: string,
  subject = "",
  enabledCategories?: readonly EmailCategory[],
  accountId?: string,
): Promise<EmailDraft> {
  const classifiedCategory = classifyEmail(subject, email);
  const category = enabledCategories?.includes(classifiedCategory)
    ? classifiedCategory
    : enabledCategories?.includes("general")
      ? "general"
      : classifiedCategory;
  const risk = ["refund_cancellation", "complaint", "project_request", "quote_request"].includes(category)
    ? "high"
    : category === "troubleshooting" ? "moderate" : "low";

  const learnedExamples = getLearnedExamples(category, 3, undefined, accountId);

  let fallbackReply = DEFAULT_CATEGORY_REPLIES[category] || DEFAULT_CATEGORY_REPLIES.general;
  if (learnedExamples.length > 0) {
    const recent = learnedExamples[learnedExamples.length - 1];
    if (recent.correctedText && recent.correctedText.length > 20) {
      fallbackReply = recent.correctedText;
    }
  }

  try {
    const knowledge = await askKnowledgeBrain(`${subject}\n${email}`.trim());
    if (knowledge.status === "answered" && !knowledge.needs_human && knowledge.sources.length > 0) {
      let modelDraft: string | null = null;
      try {
        modelDraft = await draftWithClaude(
          email,
          knowledge.answer,
          knowledge.sources.map((source) => source.url),
          learnedExamples,
        );
      } catch {
        // Fall back to knowledge grounded answer
      }

      const reply = modelDraft ?? `Hello,\n\n${knowledge.answer}\n\nBest regards,\nSupport Team`;
      return {
        category,
        summary: email.trim(),
        reply,
        sources: knowledge.sources,
        risk,
        status: "draft_ready",
        requiresApproval: true,
        nextAction: "Review the evidence-grounded draft before sending.",
      };
    }

    return {
      category,
      summary: email.trim(),
      reply: fallbackReply,
      sources: knowledge.sources || [],
      risk,
      status: "draft_ready",
      requiresApproval: true,
      nextAction: "Review and send draft directly, or edit before sending.",
    };
  } catch {
    return {
      category,
      summary: email.trim(),
      reply: fallbackReply,
      sources: [],
      risk,
      status: "draft_ready",
      requiresApproval: true,
      nextAction: "Review and send draft directly, or edit before sending.",
    };
  }
}

if (require.main === module) {
  (async () => {
    const sampleEmail = "How do I update the domain DNS records?";
    const reply = await draftReply(sampleEmail, "Domain question");
    console.log(JSON.stringify(reply, null, 2));
  })().catch((error) => {
    console.error(error);
  });
}
