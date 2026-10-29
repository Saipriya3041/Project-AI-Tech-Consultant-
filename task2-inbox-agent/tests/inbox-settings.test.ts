import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { recordOutcome } from "../src/feedback/learn";
import { getInboxCategoryStatus, getInboxSettings, saveInboxSettings } from "../src/settings/inbox";

test("persists configurable categories, threshold, and per-user low-risk auto-send opt-ins", () => {
  const directory = mkdtempSync(join(tmpdir(), "inbox-settings-"));
  const previousSettingsPath = process.env.INBOX_SETTINGS_PATH;
  const previousLedgerPath = process.env.APPROVAL_LEDGER_PATH;
  process.env.INBOX_SETTINGS_PATH = join(directory, "settings.json");
  process.env.APPROVAL_LEDGER_PATH = join(directory, "ledger.jsonl");
  try {
    const settings = saveInboxSettings("account-1", {
      categories: ["how_to", "general"],
      approvalThreshold: 3,
      autoSendCategories: ["how_to"],
    });
    expect(settings).toMatchObject({
      categories: ["how_to", "general"],
      approvalThreshold: 3,
      autoSendCategories: ["how_to"],
    });
    expect(settings.autoSendSince.how_to).toEqual(expect.any(Number));
    expect(getInboxSettings("account-2").approvalThreshold).toBe(20);

    for (let index = 0; index < 3; index += 1) {
      recordOutcome({
        accountId: "account-1",
        category: "how_to",
        generatedText: "Use the settings page.",
        finalText: "Use the settings page.",
        decision: "sent",
        qualityPassed: true,
      });
    }
    const status = getInboxCategoryStatus("account-1");
    expect(status[0]).toMatchObject({
      category: "how_to",
      enabled: true,
      consecutiveUnedited: 3,
      approvalThreshold: 3,
      eligible: true,
      autoSendEnabled: true,
    });
    expect(status.find((item) => item.category === "general")).toMatchObject({
      enabled: true,
      eligible: false,
      autoSendEnabled: false,
    });
    expect(status.find((item) => item.category === "complaint")).toMatchObject({
      enabled: false,
      eligible: false,
      autoSendEnabled: false,
    });
  } finally {
    if (previousSettingsPath === undefined) delete process.env.INBOX_SETTINGS_PATH;
    else process.env.INBOX_SETTINGS_PATH = previousSettingsPath;
    if (previousLedgerPath === undefined) delete process.env.APPROVAL_LEDGER_PATH;
    else process.env.APPROVAL_LEDGER_PATH = previousLedgerPath;
    rmSync(directory, { recursive: true, force: true });
  }
});

test("rejects unsafe auto-send categories and invalid approval thresholds", () => {
  expect(() => saveInboxSettings("account-1", {
    categories: ["refund_cancellation"],
    approvalThreshold: 20,
    autoSendCategories: ["refund_cancellation"],
  })).toThrow("Auto-send can only be enabled for selected low-risk categories");
  expect(() => saveInboxSettings("account-1", {
    categories: ["how_to"],
    approvalThreshold: 0,
    autoSendCategories: [],
  })).toThrow("approval threshold must be a whole number");
});
