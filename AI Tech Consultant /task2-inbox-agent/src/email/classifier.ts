export type EmailCategory =
  | "how_to"
  | "troubleshooting"
  | "project_request"
  | "refund_cancellation"
  | "complaint"
  | "quote_request"
  | "follow_up"
  | "general";

export function classifyEmail(subject: string, body: string): EmailCategory {
  const text = `${subject} ${body}`.toLowerCase();

  if (/\b(complaint|unacceptable|angry|disappointed|broke after|poor service)\b/.test(text)) {
    return "complaint";
  }
  if (/\b(refund(?:ed)?|cancel|cancellation|chargeback|money back)\b/.test(text)) {
    return "refund_cancellation";
  }
  if (/\b(quote|quotation|pricing|how much|cost estimate)\b/.test(text)) {
    return "quote_request";
  }
  if (/\b(follow.?up|checking in|any update|previous email|did you receive)\b/.test(text)) {
    return "follow_up";
  }
  if (/\b(error|fail(?:s|ed|ure|ing)?|stopp?ed|not working|broken|slow|cannot|can't|issue|troubleshoot)\b/.test(text)) {
    return "troubleshooting";
  }
  if (/\b(project|build(?:ing)?|develop|custom|feature request|integration)\b/.test(text)) {
    return "project_request";
  }
  if (/\b(how do i|how can i|where do i|steps to|how to|configure|change|update)\b/.test(text)) {
    return "how_to";
  }
  return "general";
}
