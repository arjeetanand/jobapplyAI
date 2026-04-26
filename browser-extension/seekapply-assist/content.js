(function () {
  if (window.__seekApplyAssistLoaded) return;
  window.__seekApplyAssistLoaded = true;

  const aliasMap = {
    notice_period: ["notice period", "when can you join", "available to start", "how soon can you join", "start date", "joining date"],
    expected_ctc: ["expected ctc", "expected salary", "expected compensation", "salary expectation", "desired salary"],
    current_ctc: ["current ctc", "current salary", "current compensation"],
    work_authorization: ["work authorization", "authorized to work", "legally authorized", "visa status", "sponsorship", "require sponsorship"],
    relocation: ["willing to relocate", "relocation", "can you relocate"],
    preferred_locations: ["preferred location", "location preference", "work location", "remote preference"],
    linkedin_url: ["linkedin profile", "linkedin url"],
    github_url: ["github profile", "github url"],
    portfolio_url: ["portfolio", "personal website", "website"]
  };

  function normalize(value) {
    return String(value || "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
  }

  function tokens(value) {
    return normalize(value).split(" ").filter((token) => token.length > 2);
  }

  function tokenMatch(a, b) {
    const left = new Set(tokens(a));
    const right = new Set(tokens(b));
    if (!left.size || !right.size) return false;
    let overlap = 0;
    for (const token of left) if (right.has(token)) overlap += 1;
    const smaller = Math.min(left.size, right.size);
    return overlap >= 2 && overlap / smaller >= 0.55;
  }

  function textFor(el) {
    return [
      el.getAttribute("aria-label"),
      el.getAttribute("title"),
      el.getAttribute("name"),
      el.getAttribute("placeholder"),
      el.value,
      el.innerText,
      el.textContent
    ].filter(Boolean).join(" ").replace(/\s+/g, " ").trim();
  }

  function visible(el) {
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.visibility !== "hidden" && style.display !== "none" && rect.width > 0 && rect.height > 0;
  }

  function labelFor(el) {
    const id = el.getAttribute("id");
    const label = id ? document.querySelector(`label[for="${CSS.escape(id)}"]`) : null;
    const parentLabel = el.closest("label");
    const wrapper = el.closest(
      "label, fieldset, .form-group, .field, .input, .application-question, .fb-dash-form-element, .jobs-easy-apply-form-section__grouping, .artdeco-text-input--container"
    );
    return [
      el.getAttribute("aria-label"),
      el.getAttribute("placeholder"),
      el.getAttribute("name"),
      label && label.innerText,
      parentLabel && parentLabel.innerText,
      wrapper && wrapper.innerText
    ].filter(Boolean).join(" ").replace(/\s+/g, " ").trim();
  }

  function optionLabelFor(el) {
    const id = el.getAttribute("id");
    const label = id ? document.querySelector(`label[for="${CSS.escape(id)}"]`) : null;
    const parentLabel = el.closest("label");
    return [el.getAttribute("aria-label"), label && label.innerText, parentLabel && parentLabel.innerText, el.value]
      .filter(Boolean).join(" ").replace(/\s+/g, " ").trim();
  }

  function buildAnswerLookup(answers) {
    const lookup = {};
    for (const answer of answers || []) {
      if (!answer.approved || !String(answer.answer_text || "").trim() || answer.answer_text === "[NEEDS HUMAN REVIEW]") continue;
      const value = answer.answer_text;
      const key = normalize(answer.question_key);
      const text = normalize(answer.question_text);
      lookup[key] = value;
      lookup[text] = value;
      const combined = `${key} ${text}`;
      for (const [canonical, aliases] of Object.entries(aliasMap)) {
        if (combined.includes(normalize(canonical)) || aliases.some((alias) => combined.includes(normalize(alias)))) {
          for (const alias of [canonical, ...aliases]) lookup[normalize(alias)] = value;
        }
      }
    }
    return lookup;
  }

  function profileValue(label, profile) {
    const n = normalize(label);
    if (n.includes("email")) return profile.email || "";
    if (n.includes("country code") || n.includes("phone code")) {
      if (String(profile.location || "").toLowerCase().includes("india") || String(profile.phone || "").startsWith("+91")) return "India (+91)";
      return "";
    }
    if (n.includes("phone") || n.includes("mobile")) {
      const raw = String(profile.phone || "").trim();
      const digits = raw.replace(/\D/g, "");
      if (String(profile.location || "").toLowerCase().includes("india") && digits.length > 10 && digits.startsWith("91")) return digits.slice(-10);
      return digits || raw;
    }
    if (n.includes("first name")) return String(profile.name || "").split(/\s+/)[0] || "";
    if (n.includes("last name")) return String(profile.name || "").split(/\s+/).slice(1).join(" ") || "";
    if (n === "name" || n.includes("full name")) return profile.name || "";
    if (n.includes("city") || n.includes("location")) return profile.location || "";
    if (n.includes("linkedin")) return profile.linkedin_url || "";
    if (n.includes("github")) return profile.github_url || "";
    if (n.includes("website") || n.includes("portfolio")) return profile.portfolio_url || profile.github_url || "";
    if (n.includes("notice") || n.includes("join") || n.includes("available to start")) return profile.notice_period || "";
    if (n.includes("authorization") || n.includes("visa") || n.includes("sponsor")) return profile.work_authorization || "";
    if (n.includes("year") && n.includes("experience")) return profile.experience_years !== null && profile.experience_years !== undefined ? String(profile.experience_years) : "";
    return "";
  }

  function answerValue(label, lookup) {
    const n = normalize(label);
    for (const [key, value] of Object.entries(lookup)) {
      if (key && (n.includes(key) || key.includes(n) || tokenMatch(n, key))) return value;
    }
    return "";
  }

  function setValue(el, value) {
    el.focus();
    if (el.isContentEditable) el.innerText = value;
    else el.value = value;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function highlight(el, color) {
    el.style.outline = `3px solid ${color}`;
    el.style.outlineOffset = "2px";
  }

  function fillPage(packet) {
    const profile = packet.profile || {};
    const lookup = buildAnswerLookup(packet.answers || []);
    const report = {
      profile_fields_filled: 0,
      answers_filled: 0,
      resume_uploads: 0,
      missing_questions: [],
      final_submit_detected: false
    };

    const fields = Array.from(document.querySelectorAll('input:not([type="hidden"]):not([type="file"]):not([type="checkbox"]):not([type="radio"]), textarea, [contenteditable="true"]'))
      .filter((el) => visible(el) && !el.disabled && !el.readOnly);
    for (const el of fields) {
      const currentValue = el.isContentEditable ? el.innerText || "" : el.value || "";
      if (currentValue.trim()) continue;
      const label = labelFor(el);
      const profileFill = profileValue(label, profile);
      const answerFill = answerValue(label, lookup);
      const value = profileFill || answerFill;
      if (!value) continue;
      setValue(el, value);
      highlight(el, profileFill ? "#16a34a" : "#2563eb");
      if (profileFill) report.profile_fields_filled += 1;
      else report.answers_filled += 1;
    }

    for (const el of Array.from(document.querySelectorAll("select")).filter((item) => visible(item) && !item.disabled)) {
      const selected = el.options[el.selectedIndex];
      const selectedText = normalize(selected ? selected.innerText || selected.label || selected.value : "");
      const hasRealSelection = String(el.value || "").trim() && el.selectedIndex > 0 && !/(select|choose|please select|--)/i.test(selectedText);
      if (hasRealSelection) continue;
      const label = labelFor(el);
      const value = answerValue(label, lookup) || profileValue(label, profile);
      if (!value) continue;
      const wanted = normalize(value);
      const option = Array.from(el.options).find((opt) => {
        const text = normalize(opt.innerText || opt.label || opt.value);
        return text && (text === wanted || text.includes(wanted) || wanted.includes(text) || tokenMatch(text, wanted));
      });
      if (!option) continue;
      el.value = option.value;
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
      highlight(el, "#2563eb");
      if (answerValue(label, lookup)) report.answers_filled += 1;
      else report.profile_fields_filled += 1;
    }

    const radioNames = Array.from(new Set(Array.from(document.querySelectorAll('input[type="radio"]')).map((el) => el.name).filter(Boolean)));
    for (const name of radioNames) {
      if (document.querySelector(`input[name="${CSS.escape(name)}"]:checked`)) continue;
      const radios = Array.from(document.querySelectorAll(`input[name="${CSS.escape(name)}"]`)).filter((el) => visible(el) && !el.disabled);
      if (!radios.length) continue;
      const label = labelFor(radios[0]);
      const value = answerValue(label, lookup);
      if (!value) continue;
      const wanted = normalize(value);
      const radio = radios.find((el) => {
        const optionText = normalize(optionLabelFor(el));
        return optionText && (optionText.includes(wanted) || wanted.includes(optionText) || optionText === wanted);
      });
      if (!radio) continue;
      radio.click();
      highlight(radio, "#2563eb");
      report.answers_filled += 1;
    }

    for (const el of Array.from(document.querySelectorAll('input[type="checkbox"]')).filter((item) => visible(item) && !item.disabled && !item.checked)) {
      const label = labelFor(el);
      const value = answerValue(label, lookup);
      const n = normalize(value);
      if (!value || !/(yes|true|agree|available|willing|authorized|i agree)/i.test(n)) continue;
      el.click();
      highlight(el, "#2563eb");
      report.answers_filled += 1;
    }

    const fileInputs = Array.from(document.querySelectorAll('input[type="file"]')).filter(visible);
    report.resume_uploads = fileInputs.length;
    for (const input of fileInputs) highlight(input, "#f59e0b");

    for (const el of Array.from(document.querySelectorAll('input:not([type="hidden"]):not([type="file"]), textarea, select, [contenteditable="true"]')).filter(visible)) {
      const required = el.required || el.getAttribute("aria-required") === "true";
      if (!required) continue;
      const value = el.isContentEditable ? el.innerText || "" : el.value || "";
      const empty = el.tagName === "SELECT" ? !value || el.selectedIndex <= 0 : !String(value).trim();
      if (!empty) continue;
      const label = labelFor(el).replace(/\s+/g, " ").trim();
      if (label && !report.missing_questions.includes(label)) {
        report.missing_questions.push(label.slice(0, 400));
        highlight(el, "#dc2626");
      }
    }

    const submitButtons = Array.from(document.querySelectorAll('button, input[type="submit"], [role="button"]')).filter((el) =>
      visible(el) && /(submit application|submit|send application|finish application)/i.test(textFor(el))
    );
    report.final_submit_detected = submitButtons.length > 0;
    for (const button of submitButtons) highlight(button, "#dc2626");

    showOverlay(report, packet);
    return report;
  }

  function showOverlay(report, packet) {
    document.getElementById("seekapply-assist-overlay")?.remove();
    const box = document.createElement("div");
    box.id = "seekapply-assist-overlay";
    box.style.cssText = [
      "position:fixed",
      "right:18px",
      "bottom:18px",
      "z-index:2147483647",
      "width:min(380px,calc(100vw - 36px))",
      "max-height:70vh",
      "overflow:auto",
      "background:#fffdf7",
      "color:#172033",
      "border:1px solid #d0d5dd",
      "box-shadow:0 20px 45px rgba(15,23,42,.18)",
      "font:13px/1.45 system-ui,-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif",
      "padding:14px"
    ].join(";");
    const missing = report.missing_questions.length
      ? `<div style="margin-top:10px;font-weight:700;color:#92400e">Needs answers</div><ul>${report.missing_questions.map((q) => `<li>${escapeHtml(q)}</li>`).join("")}</ul>`
      : "";
    const resume = report.resume_uploads
      ? `<div style="margin-top:10px;color:#92400e">Resume upload field found. Browser security requires you to choose the file manually.</div>`
      : "";
    const submit = report.final_submit_detected
      ? `<div style="margin-top:10px;color:#991b1b;font-weight:700">Final submit is visible. Review everything manually before clicking.</div>`
      : "";
    box.innerHTML = `
      <div style="display:flex;justify-content:space-between;gap:8px;align-items:start">
        <div>
          <div style="font-weight:800">SeekApply Assist</div>
          <div style="color:#667085">Profile: ${escapeHtml(packet.profile?.name || "current user")}</div>
        </div>
        <button id="seekapply-assist-close" style="border:1px solid #d0d5dd;background:white;color:#172033;padding:3px 8px;cursor:pointer">Close</button>
      </div>
      <div style="margin-top:10px;display:grid;gap:5px">
        <div>Profile fields filled: <b>${report.profile_fields_filled}</b></div>
        <div>KB answers filled: <b>${report.answers_filled}</b></div>
        <div>Resume upload controls: <b>${report.resume_uploads}</b></div>
        <div>Missing required fields: <b>${report.missing_questions.length}</b></div>
      </div>
      ${missing}
      ${resume}
      ${submit}
    `;
    document.documentElement.appendChild(box);
    document.getElementById("seekapply-assist-close").addEventListener("click", () => box.remove());
  }

  function escapeHtml(value) {
    return String(value || "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  }

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type !== "SEEKAPPLY_AUTOFILL") return false;
    try {
      sendResponse(fillPage(message.packet || {}));
    } catch (error) {
      sendResponse({ error: error.message || String(error), profile_fields_filled: 0, answers_filled: 0, resume_uploads: 0, missing_questions: [] });
    }
    return true;
  });
})();
