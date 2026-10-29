export const EMAIL_CATEGORIES = [
  "how_to",
  "troubleshooting",
  "project_request",
  "refund_cancellation",
  "complaint",
  "quote_request",
  "follow_up",
  "general",
  "filters",
  "home_page",
  "domain_management",
  "header",
  "main_menu",
  "footer",
  "general_settings",
  "design_settings",
  "managers",
] as const;

export type EmailCategory = (typeof EMAIL_CATEGORIES)[number];

export const PRIMARY_CATEGORIES: readonly EmailCategory[] = [
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
  "general",
];

export const AUTO_SEND_CATEGORIES: readonly EmailCategory[] = [
  "how_to",
  "troubleshooting",
  "follow_up",
  "filters",
  "home_page",
  "domain_management",
  "header",
  "main_menu",
  "footer",
  "general_settings",
  "design_settings",
  "managers",
];

export const CATEGORY_LABELS: Record<string, string> = {
  filters: "Filters",
  home_page: "Home Page",
  domain_management: "Domain Management",
  header: "Header",
  main_menu: "Main Menu",
  footer: "Footer",
  general_settings: "General Settings",
  design_settings: "Design Settings",
  managers: "Managers",
  how_to: "How-To",
  troubleshooting: "Troubleshooting",
  project_request: "Project Requests",
  refund_cancellation: "Refunds & Cancellations",
  complaint: "Complaints",
  quote_request: "Quotes",
  follow_up: "Follow-Ups",
  general: "General",
};

export function classifyEmail(
  subject: string,
  body: string,
  enabledCategories?: readonly EmailCategory[],
): EmailCategory {
  const text = `${subject} ${body}`.toLowerCase();

  const isEnabled = (cat: EmailCategory) => !enabledCategories || enabledCategories.includes(cat);

  // If enabled categories are restricted to the new primary categories (no legacy how_to):
  const preferNewCategories = enabledCategories && !enabledCategories.includes("how_to");

  if (preferNewCategories) {
    if (isEnabled("filters") && /\b(filters?|filtering|faceted search|product filter|search filter|price filter|category filter)\b/.test(text)) {
      return "filters";
    }
    if (isEnabled("domain_management") && /\b(domain|cname|dns|ssl|nameservers?|registrar|subdomain|pointing domain)\b/.test(text)) {
      return "domain_management";
    }
    if (isEnabled("header") && /\b(header|top bar|top navigation|header logo|announcement bar|top menu)\b/.test(text)) {
      return "header";
    }
    if (isEnabled("footer") && /\b(footer|copyright|footer links|bottom bar|bottom footer|footer widgets?)\b/.test(text)) {
      return "footer";
    }
    if (isEnabled("main_menu") && /\b(main menu|nav bar|navbar|navigation links?|dropdown menu|menu items?)\b/.test(text)) {
      return "main_menu";
    }
    if (isEnabled("home_page") && /\b(home ?page|homepage|front page|hero banner|landing page|hero image)\b/.test(text)) {
      return "home_page";
    }
    if (isEnabled("managers") && /\b(managers?|admin users?|staff members?|team permissions?|user roles?|invite staff|admin account)\b/.test(text)) {
      return "managers";
    }
    if (isEnabled("design_settings") && /\b(design settings|theme colors?|typography|styling|fonts?|css|palette|look and feel|appearance)\b/.test(text)) {
      return "design_settings";
    }
    if (isEnabled("general_settings") && /\b(general settings|site name|timezone|currency|company information|contact email|basic settings)\b/.test(text)) {
      return "general_settings";
    }
  }

  // Standard evaluation order matching dataset:
  if (isEnabled("complaint") && /\b(complaint|unacceptable|angry|disappointed|broke after|poor service)\b/.test(text)) {
    return "complaint";
  }
  if (isEnabled("refund_cancellation") && /\b(refund(?:ed)?|cancel|cancellation|chargeback|money back)\b/.test(text)) {
    return "refund_cancellation";
  }
  if (isEnabled("quote_request") && /\b(quote|quotation|pricing|how much|cost estimate)\b/.test(text)) {
    return "quote_request";
  }
  if (isEnabled("follow_up") && /\b(follow.?up|checking in|any update|previous email|did you receive)\b/.test(text)) {
    return "follow_up";
  }
  if (isEnabled("troubleshooting") && /\b(error|fail(?:s|ed|ure|ing)?|stopp?ed|not working|broken|slow|cannot|can't|issue|troubleshoot)\b/.test(text)) {
    return "troubleshooting";
  }
  if (isEnabled("project_request") && /\b(project|build(?:ing)?|develop|custom|feature request|integration)\b/.test(text)) {
    return "project_request";
  }

  // Next check specific primary feature categories:
  if (isEnabled("filters") && /\b(filters?|filtering|faceted search|product filter|search filter|price filter|category filter)\b/.test(text)) {
    return "filters";
  }
  if (isEnabled("domain_management") && /\b(domain management|custom domain|cname|dns|nameservers?|registrar|subdomain|pointing domain)\b/.test(text)) {
    return "domain_management";
  }
  if (isEnabled("header") && /\b(header|top bar|top navigation|header logo|announcement bar|top menu)\b/.test(text)) {
    return "header";
  }
  if (isEnabled("footer") && /\b(footer|copyright|footer links|bottom bar|bottom footer|footer widgets?)\b/.test(text)) {
    return "footer";
  }
  if (isEnabled("main_menu") && /\b(main menu|main navigation|nav bar|navbar|dropdown menus?)\b/.test(text)) {
    return "main_menu";
  }
  if (isEnabled("home_page") && /\b(home ?page|homepage|front page|hero banner|landing page|hero image)\b/.test(text)) {
    return "home_page";
  }
  if (isEnabled("managers") && /\b(managers?|admin permissions?|team permissions?|user roles?|admin account)\b/.test(text)) {
    return "managers";
  }
  if (isEnabled("design_settings") && /\b(design settings|theme colors?|typography|styling|fonts?|palette|appearance)\b/.test(text)) {
    return "design_settings";
  }
  if (isEnabled("general_settings") && /\b(general settings|site timezone|site currency|default currency|company information|basic settings)\b/.test(text)) {
    return "general_settings";
  }

  if (isEnabled("how_to") && /\b(how do i|how can i|where do i|steps to|how to|configure|change|update)\b/.test(text)) {
    return "how_to";
  }

  return isEnabled("general") ? "general" : (enabledCategories?.[0] || "general");
}
