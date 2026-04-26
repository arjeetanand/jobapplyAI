const DEFAULT_API_URL = "http://127.0.0.1:8000";

const apiInput = document.getElementById("apiUrl");
const statusEl = document.getElementById("status");
const fillButton = document.getElementById("fill");
const saveButton = document.getElementById("save");

function setStatus(message) {
  statusEl.textContent = message;
}

async function getApiUrl() {
  const stored = await chrome.storage.local.get(["apiUrl"]);
  return stored.apiUrl || DEFAULT_API_URL;
}

async function saveApiUrl() {
  const value = apiInput.value.trim() || DEFAULT_API_URL;
  await chrome.storage.local.set({ apiUrl: value });
  setStatus(`Saved backend URL:\n${value}`);
}

async function activeTab() {
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  return tabs[0];
}

async function sendAutofillMessage(tabId, packet) {
  try {
    return await chrome.tabs.sendMessage(tabId, { type: "SEEKAPPLY_AUTOFILL", packet });
  } catch (_error) {
    await chrome.scripting.executeScript({ target: { tabId }, files: ["content.js"] });
    return chrome.tabs.sendMessage(tabId, { type: "SEEKAPPLY_AUTOFILL", packet });
  }
}

async function autofillCurrentPage() {
  fillButton.disabled = true;
  setStatus("Loading SeekApply knowledge base...");
  try {
    const apiUrl = apiInput.value.trim() || DEFAULT_API_URL;
    await chrome.storage.local.set({ apiUrl });
    const response = await fetch(`${apiUrl.replace(/\/$/, "")}/resumes/current`);
    if (!response.ok) throw new Error(`SeekApply backend returned ${response.status}.`);
    const packet = await response.json();
    if (!packet.user_id || !packet.profile) {
      throw new Error("No SeekApply profile found. Upload a resume or complete onboarding first.");
    }
    const tab = await activeTab();
    if (!tab?.id) throw new Error("No active browser tab found.");
    const result = await sendAutofillMessage(tab.id, packet);
    setStatus(
      [
        `Filled profile fields: ${result.profile_fields_filled || 0}`,
        `Filled KB answers: ${result.answers_filled || 0}`,
        `Resume upload controls: ${result.resume_uploads || 0}`,
        `Missing required fields: ${(result.missing_questions || []).length}`,
        result.final_submit_detected ? "Final submit button detected. Review manually before clicking it." : "No final submit button detected yet."
      ].join("\n")
    );
  } catch (error) {
    setStatus(error.message || String(error));
  } finally {
    fillButton.disabled = false;
  }
}

getApiUrl().then((url) => {
  apiInput.value = url;
});

saveButton.addEventListener("click", () => saveApiUrl().catch((error) => setStatus(error.message)));
fillButton.addEventListener("click", () => autofillCurrentPage());
