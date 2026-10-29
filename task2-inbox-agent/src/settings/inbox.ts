import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join, resolve } from "node:path";
import {
  AUTO_SEND_CATEGORIES,
  CATEGORY_LABELS,
  EMAIL_CATEGORIES,
  EmailCategory,
} from "../email/classifier";
import { getCategoryApprovalCounts } from "../feedback/learn";

export interface InboxSettings {
  categories: EmailCategory[];
  approvalThreshold: number;
  autoSendCategories: EmailCategory[];
  autoSendSince: Partial<Record<EmailCategory, number>>;
}

export const DEFAULT_INBOX_SETTINGS: InboxSettings = {
  categories: [...EMAIL_CATEGORIES],
  approvalThreshold: 20,
  autoSendCategories: [],
  autoSendSince: {},
};

const allowedCategories = new Set<string>(EMAIL_CATEGORIES);
const safeAutoSendCategories = new Set<string>(AUTO_SEND_CATEGORIES);

function settingsPath(): string {
  return resolve(process.env.INBOX_SETTINGS_PATH ?? join(homedir(), ".ai-tech-consultant", "inbox-settings.json"));
}

function readSettingsFile(): Record<string, InboxSettings> {
  try {
    return JSON.parse(readFileSync(settingsPath(), "utf8")) as Record<string, InboxSettings>;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return {};
    throw new Error("Could not read Inbox settings. Check the local settings file.");
  }
}

export function getInboxSettings(accountId: string): InboxSettings {
  return readSettingsFile()[accountId] ?? { ...DEFAULT_INBOX_SETTINGS };
}

export function saveInboxSettings(
  accountId: string,
  input: Partial<InboxSettings>,
): InboxSettings {
  const categories = input.categories;
  const threshold = input.approvalThreshold;
  const resolvedThreshold = threshold ?? 20;
  const autoSendCategories = input.autoSendCategories ?? [];
  if (!Array.isArray(categories) || categories.length === 0
    || categories.some((category) => !allowedCategories.has(category))
    || new Set(categories).size !== categories.length) {
    throw new Error("Select one or more supported email categories.");
  }
  if (!Number.isInteger(resolvedThreshold) || resolvedThreshold < 1 || resolvedThreshold > 1000) {
    throw new Error("The approval threshold must be a whole number between 1 and 1000.");
  }
  if (!Array.isArray(autoSendCategories)
    || autoSendCategories.some((category) => !safeAutoSendCategories.has(category) || !categories.includes(category))) {
    throw new Error("Auto-send can only be enabled for selected low-risk categories.");
  }
  const settings: InboxSettings = {
    categories,
    approvalThreshold: resolvedThreshold,
    autoSendCategories: [...new Set(autoSendCategories)],
    autoSendSince: {},
  };
  const allSettings = readSettingsFile();
  const previous = allSettings[accountId] ?? DEFAULT_INBOX_SETTINGS;
  for (const category of settings.autoSendCategories) {
    settings.autoSendSince[category] = previous.autoSendCategories.includes(category)
      ? previous.autoSendSince?.[category] ?? Date.now()
      : Date.now();
  }
  const path = settingsPath();
  allSettings[accountId] = settings;
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, JSON.stringify(allSettings, null, 2), { mode: 0o600 });
  return settings;
}

export function getInboxCategoryStatus(accountId: string) {
  const settings = getInboxSettings(accountId);
  const counts = getCategoryApprovalCounts(accountId);
  return EMAIL_CATEGORIES.map((category) => {
    const consecutiveUnedited = counts[category] ?? 0;
    const eligible = AUTO_SEND_CATEGORIES.includes(category)
      && consecutiveUnedited >= settings.approvalThreshold;
    const autoSendEnabled = settings.autoSendCategories.includes(category)
      && consecutiveUnedited >= settings.approvalThreshold;

    return {
      category,
      label: CATEGORY_LABELS[category] || category,
      enabled: settings.categories.includes(category),
      consecutiveUnedited,
      approvalThreshold: settings.approvalThreshold,
      confidenceScore: Math.min(100, Math.round((consecutiveUnedited / settings.approvalThreshold) * 100)),
      eligible,
      autoSendEnabled,
    };
  });
}

export function isAutoSendEnabled(accountId: string, category: EmailCategory): boolean {
  const status = getInboxCategoryStatus(accountId).find((item) => item.category === category);
  return Boolean(status?.autoSendEnabled);
}
