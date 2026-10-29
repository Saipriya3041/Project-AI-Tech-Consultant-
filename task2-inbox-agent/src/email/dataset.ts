import { EmailCategory } from "./classifier";

export interface EmailCase {
  id: string;
  subject: string;
  body: string;
  expectedCategory: EmailCategory;
  risk: "low" | "moderate" | "high";
}

export const emailDataset: EmailCase[] = [
  { id: "IN-001", subject: "Change our logo", body: "How do I update the logo shown on our website?", expectedCategory: "how_to", risk: "low" },
  { id: "IN-002", subject: "Add a staff member", body: "Where do I invite another staff user?", expectedCategory: "how_to", risk: "low" },
  { id: "IN-003", subject: "Edit business hours", body: "How can I change the opening hours displayed on our listing?", expectedCategory: "how_to", risk: "low" },
  { id: "IN-004", subject: "Update profile text", body: "What are the steps to edit our company description?", expectedCategory: "how_to", risk: "low" },
  { id: "IN-005", subject: "Set a new password", body: "Where do I change my account password?", expectedCategory: "how_to", risk: "low" },
  { id: "IN-006", subject: "Change the contact email", body: "How do I update the email address on our account?", expectedCategory: "how_to", risk: "low" },
  { id: "IN-007", subject: "Add a photo", body: "How can I upload a new photo to our profile?", expectedCategory: "how_to", risk: "low" },
  { id: "IN-008", subject: "Edit a page title", body: "Where can I change the title shown in the browser tab?", expectedCategory: "how_to", risk: "low" },
  { id: "IN-009", subject: "Change a menu link", body: "Can you send me the steps to update one of our navigation links?", expectedCategory: "how_to", risk: "low" },
  { id: "IN-010", subject: "Add a location", body: "How do I add another office location to our business profile?", expectedCategory: "how_to", risk: "low" },
  { id: "IN-011", subject: "Update a form", body: "Where do I edit the fields in our contact form?", expectedCategory: "how_to", risk: "low" },
  { id: "IN-012", subject: "Hide a page", body: "How can I temporarily hide a page from visitors?", expectedCategory: "how_to", risk: "low" },
  { id: "IN-013", subject: "Payment keeps failing", body: "Checkout returns an error each time a customer pays. Can you help troubleshoot?", expectedCategory: "troubleshooting", risk: "moderate" },
  { id: "IN-014", subject: "Images will not upload", body: "The profile image upload is failing even though the file is small.", expectedCategory: "troubleshooting", risk: "moderate" },
  { id: "IN-015", subject: "Login issue", body: "I cannot sign in after resetting my password; the page says error.", expectedCategory: "troubleshooting", risk: "moderate" },
  { id: "IN-016", subject: "Search results missing", body: "Our search is not working and several listings do not appear.", expectedCategory: "troubleshooting", risk: "moderate" },
  { id: "IN-017", subject: "Email notifications stopped", body: "Order notification emails are not arriving this morning.", expectedCategory: "troubleshooting", risk: "moderate" },
  { id: "IN-018", subject: "Mobile layout broken", body: "The page is broken on phones after we changed the theme.", expectedCategory: "troubleshooting", risk: "moderate" },
  { id: "IN-019", subject: "Form submission fails", body: "The contact form fails when we submit it from Safari.", expectedCategory: "troubleshooting", risk: "moderate" },
  { id: "IN-020", subject: "Domain connection error", body: "Our custom domain is not working and shows a configuration error.", expectedCategory: "troubleshooting", risk: "moderate" },
  { id: "IN-021", subject: "Slow dashboard", body: "The admin dashboard has become very slow since yesterday.", expectedCategory: "troubleshooting", risk: "moderate" },
  { id: "IN-022", subject: "Calendar integration failed", body: "The calendar sync is not working and displays an error.", expectedCategory: "troubleshooting", risk: "moderate" },
  { id: "IN-023", subject: "Add booking feature", body: "We want to build a custom booking flow for our customers.", expectedCategory: "project_request", risk: "high" },
  { id: "IN-024", subject: "Connect our CRM", body: "Can your team develop an integration with our CRM?", expectedCategory: "project_request", risk: "high" },
  { id: "IN-025", subject: "New member portal", body: "We would like a project to build a private member portal.", expectedCategory: "project_request", risk: "high" },
  { id: "IN-026", subject: "Add product filters", body: "Could you build custom filters for our product catalog?", expectedCategory: "project_request", risk: "high" },
  { id: "IN-027", subject: "Automate onboarding", body: "We need a custom onboarding workflow developed for new clients.", expectedCategory: "project_request", risk: "high" },
  { id: "IN-028", subject: "New reporting feature", body: "Can we discuss building a reporting feature for our managers?", expectedCategory: "project_request", risk: "high" },
  { id: "IN-029", subject: "Please refund last month", body: "I was charged last month and would like my money back.", expectedCategory: "refund_cancellation", risk: "high" },
  { id: "IN-030", subject: "Cancel subscription", body: "Please cancel our plan at the end of this billing period.", expectedCategory: "refund_cancellation", risk: "high" },
  { id: "IN-031", subject: "Duplicate charge", body: "I see two charges and want one refunded.", expectedCategory: "refund_cancellation", risk: "high" },
  { id: "IN-032", subject: "Close our account", body: "How do I cancel and make sure billing stops?", expectedCategory: "refund_cancellation", risk: "high" },
  { id: "IN-033", subject: "Refund for annual plan", body: "We no longer need the service. Can you refund the annual payment?", expectedCategory: "refund_cancellation", risk: "high" },
  { id: "IN-034", subject: "Stop renewal", body: "Please cancel the renewal before our next charge.", expectedCategory: "refund_cancellation", risk: "high" },
  { id: "IN-035", subject: "Very disappointed", body: "I am disappointed that support has not fixed this issue.", expectedCategory: "complaint", risk: "high" },
  { id: "IN-036", subject: "Unacceptable outage", body: "Our site was unavailable and this level of service is unacceptable.", expectedCategory: "complaint", risk: "high" },
  { id: "IN-037", subject: "Your update broke our page", body: "A recent update broke our site and we are angry about the downtime.", expectedCategory: "complaint", risk: "high" },
  { id: "IN-038", subject: "Poor service", body: "We have had poor service and no one replied to our last three emails.", expectedCategory: "complaint", risk: "high" },
  { id: "IN-039", subject: "Need a quote", body: "Could you provide a quote for a custom booking page?", expectedCategory: "quote_request", risk: "high" },
  { id: "IN-040", subject: "Pricing for integration", body: "How much would it cost to connect our payment provider?", expectedCategory: "quote_request", risk: "high" },
  { id: "IN-041", subject: "Estimate for redesign", body: "Can we get a cost estimate for a mobile redesign?", expectedCategory: "quote_request", risk: "high" },
  { id: "IN-042", subject: "Price for extra storage", body: "Please send pricing for adding more storage to our plan.", expectedCategory: "quote_request", risk: "high" },
  { id: "IN-043", subject: "Quote request", body: "We need a quotation for a reporting dashboard project.", expectedCategory: "quote_request", risk: "high" },
  { id: "IN-044", subject: "Following up on ticket 184", body: "I am checking in on the issue I emailed about last week.", expectedCategory: "follow_up", risk: "low" },
  { id: "IN-045", subject: "Any update?", body: "Do you have any update on the domain issue?", expectedCategory: "follow_up", risk: "low" },
  { id: "IN-046", subject: "Did you receive my files", body: "Following up to see whether you received the screenshots I sent.", expectedCategory: "follow_up", risk: "low" },
  { id: "IN-047", subject: "Checking in on proposal", body: "I wanted to check in on the proposal from our previous email.", expectedCategory: "follow_up", risk: "low" },
  { id: "IN-048", subject: "Follow-up on support request", body: "Any update from the team about my previous request?", expectedCategory: "follow_up", risk: "low" },
  { id: "IN-049", subject: "No response to urgent request", body: "I am angry that nobody replied to my urgent support email.", expectedCategory: "complaint", risk: "high" },
  { id: "IN-050", subject: "Unacceptable support delay", body: "The delay is unacceptable and has caused us to miss a customer deadline.", expectedCategory: "complaint", risk: "high" },
];