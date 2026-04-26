import { useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  AlertTriangle,
  CheckCircle2,
  ClipboardCheck,
  Download,
  ExternalLink,
  FileText,
  Gauge,
  History,
  Linkedin,
  ListChecks,
  MailPlus,
  RefreshCcw,
  Save,
  Send,
  Shield,
  Sparkles,
  Upload,
  Zap
} from "lucide-react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, API_URL, Analytics, Answer, Claim, CurrentResume, DiscoveryPreferences, JobRow, LinkedInPlan, ResumeVersion, TrackerRow } from "./lib/api";
import { Button, Field, inputClass, Panel, textareaClass } from "./components/ui";
import { SectionId, Sidebar } from "./components/Sidebar";
import { StatCard } from "./components/StatCard";

const defaultProfile = {
  name: "Arjeet Anand",
  email: "arjeet@example.com",
  phone: "",
  location: "India",
  linkedin_url: "",
  github_url: "",
  portfolio_url: "",
  current_company: "",
  current_role: "AI Engineer",
  experience_years: 3,
  notice_period: "Immediate",
  work_authorization: "India",
  skills: ["Python", "FastAPI", "LLMs", "RAG", "Vector DB", "SQL", "React"],
  projects: [
    { name: "RAG Knowledge Assistant", summary: "Retrieval augmented question answering over documents.", skills: ["RAG", "LLMs", "Python", "FastAPI"] }
  ],
  experience: [],
  education: [],
  certifications: [],
  achievements: [],
  github_repositories: [],
  preferred_resume_template: "ats-clean",
  preferred_tone: "ATS-optimized",
  base_resume_text: "AI engineer with experience building truthful LLM applications, APIs, RAG workflows, and analytics tools."
};

const defaultPreferences = {
  target_roles: ["AI Engineer", "ML Engineer", "Generative AI Engineer"],
  similar_roles: ["MLOps Engineer", "Applied Scientist", "Backend Engineer"],
  minimum_salary: 1200000,
  preferred_salary: "12-25 LPA",
  preferred_locations: ["India", "Remote", "Bengaluru"],
  remote_preference: "remote",
  preferred_company_types: ["AI companies", "SaaS", "Product companies"],
  excluded_companies: [],
  excluded_industries: [],
  excluded_locations: [],
  auto_apply_enabled: false,
  auto_email_enabled: false,
  max_applications_per_day: 10,
  match_threshold: 75
};

export default function App() {
  const [active, setActive] = useState<SectionId>("resume");
  const [analytics, setAnalytics] = useState<Analytics | null>(null);
  const [tracker, setTracker] = useState<TrackerRow[]>([]);
  const [resumes, setResumes] = useState<ResumeVersion[]>([]);
  const [safety, setSafety] = useState<string[]>([]);
  const [oci, setOci] = useState<string>("Checking OCI provider...");
  const [notice, setNotice] = useState<string>("");

  const refresh = async () => {
    const [analyticsData, trackerData, resumeData, safetyData, ociData] = await Promise.all([
      api.analytics(),
      api.tracker(),
      api.resumes(),
      api.safety(),
      api.oci()
    ]);
    setAnalytics(analyticsData);
    setTracker(trackerData.applications);
    setResumes(resumeData.resume_versions);
    setSafety(safetyData.rules);
    setOci(ociData.message);
  };

  useEffect(() => {
    refresh().catch((error) => setNotice(error.message));
  }, []);

  return (
    <div className="grid min-h-screen grid-cols-[280px_1fr] bg-field max-lg:grid-cols-1">
      <Sidebar active={active} setActive={setActive} />
      <main className="min-w-0">
        <header className="border-b border-line bg-[#fffaf0] px-6 py-8 max-lg:px-4">
          <div className="grid items-end gap-6 lg:grid-cols-[1fr_280px]">
            <div className="fade-slide-up">
              <div className="section-kicker">01 / 13 Workflow System</div>
              <h1 className="hero-title mt-3 text-ink">Resume-First Job Application Playbook</h1>
              <p className="mt-3 max-w-3xl text-sm leading-6 text-[#5f574b]">
                Upload once, extract verified profile data, match jobs against an 85% threshold, reuse or tailor resume versions, and preserve every portal question in your knowledge base.
              </p>
            </div>
            <div className="rounded-[18px] border border-[#2f2f2f] bg-[#111111] p-4 text-[#f7f2e8] shadow-float fade-slide-up">
              <div className="section-kicker text-[#b7ff29]">Run Index</div>
              <div className="mt-2 text-xs leading-5">
                <div>02 / Extract Profile</div>
                <div>03 / Find Jobs</div>
                <div>04 / Decide Resume</div>
                <div>05 / Track Questions</div>
              </div>
            </div>
          </div>
          <div className="mt-5 flex items-center gap-3">
            <Button variant="primary" onClick={() => refresh().catch((error) => setNotice(error.message))}>
              <RefreshCcw size={16} /> Refresh Data
            </Button>
            <span className="rounded-full border border-[#d6eba0] bg-[#effbcf] px-3 py-1 text-xs font-semibold text-[#2f3f13]">
              Threshold: 85%
            </span>
          </div>
        </header>
        {notice && (
          <div className="border-b border-[#d6eba0] bg-[#f5ffd8] px-6 py-3 text-sm text-[#3f5a0a]">
            {notice}
          </div>
        )}
        <div className="p-6 max-lg:p-4">
          <AnimatePresence mode="wait">
            <motion.div
              key={active}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              transition={{ duration: 0.16 }}
              className="fade-slide-up"
            >
              {active === "resume" && <ResumeIntake onNotice={setNotice} />}
              {active === "onboarding" && <Onboarding onNotice={setNotice} onRefresh={refresh} />}
              {active === "search" && <JobSearch onNotice={setNotice} />}
              {active === "linkedin" && <LinkedInAssist onNotice={setNotice} />}
              {active === "browser" && <BrowserAssist onNotice={setNotice} />}
              {active === "review" && <MatchReview onNotice={setNotice} onRefresh={refresh} />}
              {(active === "answers" || active === "questions") && <AnswerBank onNotice={setNotice} />}
              {active === "packets" && <ApplicationPackets onNotice={setNotice} />}
              {active === "claims" && <ClaimLedger onNotice={setNotice} />}
              {(active === "history" || active === "logs") && <RunHistory onNotice={setNotice} />}
              {active === "tracker" && <Tracker rows={tracker} analytics={analytics} />}
              {active === "resumes" && <ResumeVersions rows={resumes} />}
              {active === "outreach" && <Outreach onNotice={setNotice} />}
              {active === "analytics" && <AnalyticsView analytics={analytics} />}
              {active === "settings" && <SettingsView safety={safety} oci={oci} />}
            </motion.div>
          </AnimatePresence>
        </div>
      </main>
    </div>
  );
}

function ResumeIntake({ onNotice }: { onNotice: (message: string) => void }) {
  const [userId, setUserId] = useState<number | null>(null);
  const [baseResume, setBaseResume] = useState<CurrentResume["base_resume"]>(null);
  const [extracted, setExtracted] = useState<{
    name: string;
    email: string;
    phone: string | null;
    location: string | null;
    linkedin_url: string | null;
    github_url: string | null;
    work_authorization: string | null;
    skills: string[];
  } | null>(null);
  const [skillsText, setSkillsText] = useState("");
  const [noticePeriod, setNoticePeriod] = useState("");
  const [workAuthorization, setWorkAuthorization] = useState("");
  const [preferredSalary, setPreferredSalary] = useState("");
  const [preferredLocations, setPreferredLocations] = useState("");
  const [remotePreference, setRemotePreference] = useState("remote");
  const [excludedCompanies, setExcludedCompanies] = useState("");
  const [missing, setMissing] = useState<string[]>([]);
  const [missingAnswers, setMissingAnswers] = useState<Record<string, string>>({});
  const [missingModalOpen, setMissingModalOpen] = useState(false);
  const [answers, setAnswers] = useState<Answer[]>([]);
  const [busy, setBusy] = useState(false);
  const [saveStatus, setSaveStatus] = useState<string>("");

  const applyCurrentResume = (data: CurrentResume) => {
    setUserId(data.user_id);
    setBaseResume(data.base_resume);
    setMissing(data.missing_questions);
    setAnswers(data.answers);
    if (data.profile) {
      setExtracted({
        name: data.profile.name || "",
        email: data.profile.email || "",
        phone: data.profile.phone,
        location: data.profile.location,
        linkedin_url: data.profile.linkedin_url,
        github_url: data.profile.github_url,
        work_authorization: data.profile.work_authorization,
        skills: data.profile.skills || []
      });
      setSkillsText((data.profile.skills || []).join(", "));
      setNoticePeriod(data.profile.notice_period || "");
      setWorkAuthorization(data.profile.work_authorization || "");
    }
    if (data.preferences) {
      setPreferredSalary(data.preferences.preferred_salary || "");
      setPreferredLocations((data.preferences.preferred_locations || []).join(", "));
      setRemotePreference(data.preferences.remote_preference || "remote");
      setExcludedCompanies((data.preferences.excluded_companies || []).join(", "));
    }
  };

  const loadCurrent = async () => {
    const data = await api.currentResume();
    applyCurrentResume(data);
  };

  useEffect(() => {
    loadCurrent().catch((error) => onNotice(error.message));
  }, []);

  const openMissingModal = (questions: string[]) => {
    const nextAnswers: Record<string, string> = {};
    for (const question of questions) {
      const saved = answers.find((answer) => answer.question_text === question);
      nextAnswers[question] = saved?.answer_text === "[NEEDS HUMAN REVIEW]" ? "" : saved?.answer_text ?? "";
    }
    setMissingAnswers(nextAnswers);
    setMissingModalOpen(questions.length > 0);
  };

  const upload = async (file: File | undefined) => {
    if (!file) return;
    setBusy(true);
    try {
      const result = await api.uploadBaseResume(file);
      const current = await api.currentResume();
      applyCurrentResume(current);
      openMissingModal(current.missing_questions.length ? current.missing_questions : result.missing_questions);
      onNotice(`Resume extracted and saved for user ${result.user_id}.`);
    } finally {
      setBusy(false);
    }
  };

  const profileAnswerItems = () => {
    if (!extracted) return [];
    const items: Array<{ question_key: string; question_text: string; answer_text: string; approved: boolean; sensitive: boolean; source: string }> = [];
    const add = (question_key: string, question_text: string, answer_text: string | null | undefined, sensitive = true) => {
      const clean = (answer_text || "").trim();
      if (clean) {
        items.push({
          question_key,
          question_text,
          answer_text: clean,
          approved: true,
          sensitive,
          source: "resume_intake_profile_save"
        });
      }
    };
    add("phone", "What phone number should be used for job applications?", extracted.phone);
    add("linkedin_url", "What is your LinkedIn profile URL?", extracted.linkedin_url);
    add("github_url", "What is your GitHub profile URL, if relevant?", extracted.github_url);
    add("verified_skills", "Which skills should be treated as verified for matching?", skillsText, false);
    add("notice_period", "What is your notice period?", noticePeriod);
    add("work_authorization", "What work authorization answer should be used?", workAuthorization);
    add("expected_ctc", "What is your expected compensation?", preferredSalary);
    add("preferred_locations", "What locations or remote preferences should be used?", preferredLocations);
    add("excluded_companies", "Are there companies or industries that must be excluded?", excludedCompanies);
    return items;
  };

  const saveCorrections = async () => {
    if (!extracted || !userId) return;
    setBusy(true);
    setSaveStatus("");
    try {
      const result = await api.updateResumeProfile({
        user_id: userId,
        ...extracted,
        name: extracted.name.trim(),
        email: extracted.email.trim(),
        phone: extracted.phone?.trim() || null,
        location: extracted.location?.trim() || null,
        linkedin_url: extracted.linkedin_url?.trim() || null,
        github_url: extracted.github_url?.trim() || null,
        work_authorization: workAuthorization.trim() || null,
        skills: split(skillsText),
        notice_period: noticePeriod,
        preferred_salary: preferredSalary,
        preferred_locations: split(preferredLocations),
        remote_preference: remotePreference,
        excluded_companies: split(excludedCompanies)
      });
      const answerItems = profileAnswerItems();
      if (answerItems.length) {
        await api.bulkAnswers({ user_id: userId, answers: answerItems });
      }
      await loadCurrent();
      setSaveStatus("Saved");
      onNotice(result.message);
    } finally {
      setBusy(false);
    }
  };

  const saveMissingAnswers = async () => {
    if (!userId) return;
    const answerItems = missing
      .map((question) => ({
        question_text: question,
        answer_text: missingAnswers[question] || "",
        approved: true,
        sensitive: true,
        source: "resume_intake_missing_question"
      }))
      .filter((item) => item.answer_text.trim());

    if (!answerItems.length) {
      setMissingModalOpen(false);
      return;
    }

    setBusy(true);
    try {
      const result = await api.bulkAnswers({ user_id: userId, answers: answerItems });
      await loadCurrent();
      setMissingModalOpen(false);
      onNotice(result.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid gap-4">
      <div className="grid gap-4 lg:grid-cols-[420px_1fr]">
        <Panel className="p-5">
          <div className="mb-4 flex items-center gap-2">
            <Upload size={18} className="text-cobalt" />
            <h2 className="text-base font-semibold">Upload Resume</h2>
          </div>
          <input
            className={inputClass}
            type="file"
            accept=".pdf,.docx,.txt,.md"
            disabled={busy}
            onChange={(event) => upload(event.target.files?.[0]).catch((error) => onNotice(error.message))}
          />
          {baseResume ? (
            <div className="mt-4 grid gap-3 border-t border-line pt-4 text-sm">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="min-w-0">
                  <div className="font-semibold text-ink">{baseResume.filename}</div>
                  <div className="text-xs text-slate-500">{formatBytes(baseResume.size_bytes)} · verified base resume</div>
                </div>
                <Button variant="secondary" onClick={() => api.downloadBaseResume().catch((error) => onNotice(error.message))}>
                  <Download size={15} /> Download
                </Button>
              </div>
              {baseResume.text_preview && (
                <pre className="max-h-[300px] overflow-auto rounded border border-line bg-field p-3 text-xs leading-5 text-slate-700 whitespace-pre-wrap">
                  {baseResume.text_preview}
                </pre>
              )}
              {missing.length > 0 && (
                <Button disabled={busy} onClick={() => openMissingModal(missing)}>
                  <ListChecks size={15} /> Answer Missing Questions
                </Button>
              )}
            </div>
          ) : (
            <div className="mt-4 rounded border border-line bg-field p-3 text-sm text-slate-500">
              Upload a resume to create the verified base profile.
            </div>
          )}
        </Panel>
        <Panel className="p-5">
          <h2 className="mb-4 text-base font-semibold">Extracted Profile And Preferences</h2>
          {extracted ? (
            <div className="grid gap-4 text-sm">
              <div className="grid gap-4 md:grid-cols-2">
                <Field label="Name"><input className={inputClass} value={extracted.name} onChange={(e) => setExtracted({ ...extracted, name: e.target.value })} /></Field>
                <Field label="Email"><input className={inputClass} value={extracted.email} onChange={(e) => setExtracted({ ...extracted, email: e.target.value })} /></Field>
                <Field label="Phone"><input className={inputClass} value={extracted.phone || ""} onChange={(e) => setExtracted({ ...extracted, phone: e.target.value })} /></Field>
                <Field label="Location"><input className={inputClass} value={extracted.location || ""} onChange={(e) => setExtracted({ ...extracted, location: e.target.value })} /></Field>
                <Field label="LinkedIn"><input className={inputClass} value={extracted.linkedin_url || ""} onChange={(e) => setExtracted({ ...extracted, linkedin_url: e.target.value })} /></Field>
                <Field label="GitHub"><input className={inputClass} value={extracted.github_url || ""} onChange={(e) => setExtracted({ ...extracted, github_url: e.target.value })} /></Field>
                <Field label="Notice period"><input className={inputClass} value={noticePeriod} onChange={(e) => setNoticePeriod(e.target.value)} placeholder="Immediate / 30 days / 60 days" /></Field>
                <Field label="Work authorization"><input className={inputClass} value={workAuthorization} onChange={(e) => setWorkAuthorization(e.target.value)} placeholder="Country / visa / sponsorship status" /></Field>
                <Field label="Expected compensation"><input className={inputClass} value={preferredSalary} onChange={(e) => setPreferredSalary(e.target.value)} placeholder="Example: 18-28 LPA" /></Field>
                <Field label="Remote preference">
                  <select className={inputClass} value={remotePreference} onChange={(e) => setRemotePreference(e.target.value)}>
                    <option value="remote">Remote</option>
                    <option value="hybrid">Hybrid</option>
                    <option value="onsite">Onsite</option>
                    <option value="any">Any</option>
                  </select>
                </Field>
              </div>
              <Field label="Skills"><textarea className={textareaClass} value={skillsText} onChange={(e) => setSkillsText(e.target.value)} /></Field>
              <Field label="Preferred locations"><input className={inputClass} value={preferredLocations} onChange={(e) => setPreferredLocations(e.target.value)} placeholder="India, Remote, Bengaluru" /></Field>
              <Field label="Excluded companies"><input className={inputClass} value={excludedCompanies} onChange={(e) => setExcludedCompanies(e.target.value)} placeholder="Company names separated by commas" /></Field>
              <div className="flex items-center gap-3">
                <Button disabled={busy} onClick={() => saveCorrections().catch((error) => { setBusy(false); setSaveStatus(""); onNotice(error.message); })}>
                  <Save size={16} /> {busy ? "Saving..." : "Save Profile And KB"}
                </Button>
                {saveStatus && <span className="text-sm text-moss">{saveStatus}</span>}
              </div>
            </div>
          ) : (
            <div className="text-sm text-slate-500">Upload a resume to see extracted fields and missing questions.</div>
          )}
        </Panel>
      </div>
      <Panel className="p-5">
        <div className="mb-4 flex items-center gap-2">
          <ListChecks size={18} className="text-moss" />
          <h2 className="text-base font-semibold">Shared Application Answers</h2>
        </div>
        {answers.length ? (
          <div className="grid gap-3 md:grid-cols-2">
            {answers.slice(0, 8).map((answer) => (
              <div key={answer.id} className="rounded border border-line bg-white p-3 text-sm">
                <div className="font-medium text-ink">{answer.question_text}</div>
                <div className="mt-1 text-slate-600">{answer.answer_text}</div>
              </div>
            ))}
          </div>
        ) : (
          <div className="rounded border border-line bg-field p-3 text-sm text-slate-500">
            Saved answers will appear here after missing questions are completed.
          </div>
        )}
      </Panel>
      {missingModalOpen && (
        <div className="fixed inset-0 z-50 grid place-items-center bg-black/40 p-4">
          <div className="w-full max-w-2xl rounded-lg border border-line bg-white p-5 shadow-float">
            <div className="mb-4 flex items-center justify-between gap-3">
              <div className="flex items-center gap-2">
                <ListChecks size={18} className="text-cobalt" />
                <h2 className="text-base font-semibold">Missing Application Questions</h2>
              </div>
              <button className="text-sm font-medium text-slate-500 hover:text-ink" onClick={() => setMissingModalOpen(false)}>
                Skip for now
              </button>
            </div>
            <div className="max-h-[60vh] overflow-auto pr-1">
              <div className="grid gap-4">
                {missing.map((question) => (
                  <Field key={question} label={question}>
                    <textarea
                      className={textareaClass}
                      value={missingAnswers[question] || ""}
                      onChange={(e) => setMissingAnswers((prev) => ({ ...prev, [question]: e.target.value }))}
                    />
                  </Field>
                ))}
              </div>
            </div>
            <div className="mt-5 flex justify-end gap-2">
              <Button variant="secondary" disabled={busy} onClick={() => setMissingModalOpen(false)}>Cancel</Button>
              <Button disabled={busy} onClick={() => saveMissingAnswers().catch((error) => { setBusy(false); onNotice(error.message); })}>
                <Save size={16} /> {busy ? "Saving..." : "Save Answers"}
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function Onboarding({ onNotice, onRefresh }: { onNotice: (message: string) => void; onRefresh: () => Promise<void> }) {
  const [name, setName] = useState(defaultProfile.name);
  const [email, setEmail] = useState(defaultProfile.email);
  const [skills, setSkills] = useState(defaultProfile.skills.join(", "));
  const [targetRoles, setTargetRoles] = useState(defaultPreferences.target_roles.join(", "));
  const [excluded, setExcluded] = useState("");

  const save = async () => {
    const payload = {
      profile: { ...defaultProfile, name, email, skills: split(skills) },
      preferences: { ...defaultPreferences, target_roles: split(targetRoles), excluded_companies: split(excluded) }
    };
    const result = await api.onboarding(payload);
    onNotice(result.message);
    await onRefresh();
  };

  return (
    <div className="grid gap-4">
      <Panel className="p-5">
        <div className="mb-4 flex items-center gap-2">
          <Shield size={18} className="text-moss" />
          <h2 className="text-base font-semibold">Profile And Preferences</h2>
        </div>
        <div className="grid gap-4 md:grid-cols-2">
          <Field label="Full name"><input className={inputClass} value={name} onChange={(e) => setName(e.target.value)} /></Field>
          <Field label="Email"><input className={inputClass} value={email} onChange={(e) => setEmail(e.target.value)} /></Field>
          <Field label="Skills"><textarea className={textareaClass} value={skills} onChange={(e) => setSkills(e.target.value)} /></Field>
          <Field label="Target roles"><textarea className={textareaClass} value={targetRoles} onChange={(e) => setTargetRoles(e.target.value)} /></Field>
          <Field label="Excluded companies"><textarea className={textareaClass} value={excluded} onChange={(e) => setExcluded(e.target.value)} placeholder="Company names separated by commas" /></Field>
        </div>
        <div className="mt-4"><Button onClick={() => save().catch((error) => onNotice(error.message))}><Save size={16} /> Save</Button></div>
      </Panel>
    </div>
  );
}

function BrowserAssist({ onNotice }: { onNotice: (message: string) => void }) {
  const [pageUrl, setPageUrl] = useState("https://www.linkedin.com/jobs/view/123456789");
  const [sourceSite, setSourceSite] = useState("linkedin.com");
  const [title, setTitle] = useState("Generative AI Engineer");
  const [company, setCompany] = useState("ExampleAI");
  const [description, setDescription] = useState("Build LLM applications with Python, FastAPI, RAG, vector databases, and production APIs.");
  const [rules, setRules] = useState<string[]>([]);

  useEffect(() => {
    api.siteRules().then((data) => setRules(data.rules)).catch(() => undefined);
  }, []);

  const importPage = async () => {
    const result = await api.browserImport({
      page_url: pageUrl,
      source_site: sourceSite,
      title,
      company,
      description,
      visible_text: `${title}\n${company}\n${description}`
    });
    onNotice(`${result.message} Job ID: ${result.job_id}. Confidence: ${result.parser_confidence}`);
  };

  return (
    <div className="grid gap-4 xl:grid-cols-[420px_1fr]">
      <Panel className="p-5">
        <div className="mb-4 flex items-center gap-2">
          <ClipboardCheck size={18} className="text-cobalt" />
          <h2 className="text-base font-semibold">Visible Page Import</h2>
        </div>
        <div className="grid gap-4">
          <Field label="Page URL"><input className={inputClass} value={pageUrl} onChange={(e) => setPageUrl(e.target.value)} /></Field>
          <Field label="Source site"><input className={inputClass} value={sourceSite} onChange={(e) => setSourceSite(e.target.value)} /></Field>
          <Field label="Role"><input className={inputClass} value={title} onChange={(e) => setTitle(e.target.value)} /></Field>
          <Field label="Company"><input className={inputClass} value={company} onChange={(e) => setCompany(e.target.value)} /></Field>
          <Field label="Visible description"><textarea className={textareaClass} value={description} onChange={(e) => setDescription(e.target.value)} /></Field>
          <Button onClick={() => importPage().catch((error) => onNotice(error.message))}><FileText size={16} /> Import</Button>
        </div>
      </Panel>
      <Panel className="p-5">
        <h2 className="mb-4 text-base font-semibold">Site Rules</h2>
        <div className="grid gap-2">{rules.map((rule) => <div key={rule} className="border border-line bg-field p-3 text-sm">{rule}</div>)}</div>
      </Panel>
    </div>
  );
}

function AnswerBank({ onNotice }: { onNotice: (message: string) => void }) {
  const [questionKey, setQuestionKey] = useState("notice_period");
  const [questionText, setQuestionText] = useState("What is your notice period?");
  const [answerText, setAnswerText] = useState("Immediate");
  const [answers, setAnswers] = useState<Answer[]>([]);
  const load = () => api.answers().then((data) => setAnswers(data.answers)).catch((error) => onNotice(error.message));
  useEffect(() => {
    load();
  }, []);
  const save = async () => {
    const result = await api.createAnswer({ question_key: questionKey, question_text: questionText, answer_text: answerText, approved: true });
    onNotice(result.message);
    load();
  };
  return (
    <div className="grid gap-4 lg:grid-cols-[420px_1fr]">
      <Panel className="p-5">
        <div className="mb-4 flex items-center gap-2"><ListChecks size={18} className="text-moss" /><h2 className="text-base font-semibold">Saved Answer</h2></div>
        <div className="grid gap-4">
          <Field label="Question key"><input className={inputClass} value={questionKey} onChange={(e) => setQuestionKey(e.target.value)} /></Field>
          <Field label="Question"><input className={inputClass} value={questionText} onChange={(e) => setQuestionText(e.target.value)} /></Field>
          <Field label="Answer"><textarea className={textareaClass} value={answerText} onChange={(e) => setAnswerText(e.target.value)} /></Field>
          <Button onClick={() => save().catch((error) => onNotice(error.message))}><Save size={16} /> Save Answer</Button>
        </div>
      </Panel>
      <Panel className="p-5">
        <h2 className="mb-4 text-base font-semibold">Approved Answers</h2>
        <div className="grid gap-3">{answers.map((answer) => <div key={answer.id} className="border border-line p-3 text-sm"><div className="font-medium">{answer.question_text}</div><div className="mt-1 text-slate-600">{answer.answer_text}</div></div>)}</div>
      </Panel>
    </div>
  );
}

function ApplicationPackets({ onNotice }: { onNotice: (message: string) => void }) {
  const [jobId, setJobId] = useState("1");
  const [packet, setPacket] = useState<string>("");
  const prepare = async () => {
    const result = await api.preparePacket(Number(jobId));
    setPacket(JSON.stringify({ packet_id: result.packet_id, missing_items: result.missing_items, packet: result.packet }, null, 2));
    onNotice(`Prepared packet ${result.packet_id}`);
  };
  return (
    <Panel className="p-5">
      <div className="mb-4 flex items-center gap-2"><ClipboardCheck size={18} className="text-cobalt" /><h2 className="text-base font-semibold">Application Packet</h2></div>
      <div className="mb-4 flex max-w-md gap-2">
        <input className={inputClass} value={jobId} onChange={(e) => setJobId(e.target.value)} />
        <Button onClick={() => prepare().catch((error) => onNotice(error.message))}>Prepare</Button>
      </div>
      <pre className="max-h-[520px] overflow-auto border border-line bg-field p-4 text-xs text-slate-700">{packet || "Prepare a packet to review resume, answers, email, and safety notes."}</pre>
    </Panel>
  );
}

function ClaimLedger({ onNotice }: { onNotice: (message: string) => void }) {
  const [claimType, setClaimType] = useState("skill");
  const [claimText, setClaimText] = useState("Built RAG workflows using Python, FastAPI, LLMs, and vector databases.");
  const [claims, setClaims] = useState<Claim[]>([]);
  const load = () => api.claims().then((data) => setClaims(data.claims)).catch((error) => onNotice(error.message));
  useEffect(() => {
    load();
  }, []);
  const save = async () => {
    const result = await api.createClaim({ claim_type: claimType, claim_text: claimText, approved: true });
    onNotice(result.message);
    load();
  };
  return (
    <div className="grid gap-4 lg:grid-cols-[420px_1fr]">
      <Panel className="p-5">
        <div className="mb-4 flex items-center gap-2"><Shield size={18} className="text-moss" /><h2 className="text-base font-semibold">Truthful Claim</h2></div>
        <div className="grid gap-4">
          <Field label="Claim type"><input className={inputClass} value={claimType} onChange={(e) => setClaimType(e.target.value)} /></Field>
          <Field label="Claim text"><textarea className={textareaClass} value={claimText} onChange={(e) => setClaimText(e.target.value)} /></Field>
          <Button onClick={() => save().catch((error) => onNotice(error.message))}><Save size={16} /> Save Claim</Button>
        </div>
      </Panel>
      <Panel className="p-5">
        <h2 className="mb-4 text-base font-semibold">Claim Ledger</h2>
        <div className="grid gap-3">{claims.map((claim) => <div key={claim.id} className="border border-line p-3 text-sm"><div className="font-medium">{claim.claim_type}</div><div className="mt-1 text-slate-600">{claim.claim_text}</div></div>)}</div>
      </Panel>
    </div>
  );
}

function RunHistory({ onNotice }: { onNotice: (message: string) => void }) {
  const [runs, setRuns] = useState<Array<{ id: number; agent_name: string; input_summary: string; output_summary: string; status: string }>>([]);
  const [imports, setImports] = useState<Array<{ id: number; job_id: number; source_site: string; page_url: string; parser_confidence: string; missing_fields: string[] }>>([]);
  const load = () => api.runHistory().then((data) => { setRuns(data.agent_runs); setImports(data.browser_imports); }).catch((error) => onNotice(error.message));
  useEffect(() => {
    load();
  }, []);
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <Panel className="p-5">
        <div className="mb-4 flex items-center gap-2"><History size={18} className="text-cobalt" /><h2 className="text-base font-semibold">Agent Runs</h2></div>
        <div className="grid gap-3">{runs.map((run) => <div key={run.id} className="border border-line p-3 text-sm"><div className="font-medium">{run.agent_name} · {run.status}</div><div className="mt-1 text-slate-500">{run.input_summary}</div><div className="mt-1 text-slate-700">{run.output_summary}</div></div>)}</div>
      </Panel>
      <Panel className="p-5">
        <h2 className="mb-4 text-base font-semibold">Browser Imports</h2>
        <div className="grid gap-3">{imports.map((item) => <div key={item.id} className="border border-line p-3 text-sm"><div className="font-medium">{item.source_site} · job {item.job_id}</div><div className="mt-1 text-slate-500">{item.page_url}</div><div className="mt-1 text-slate-700">Confidence: {item.parser_confidence}</div></div>)}</div>
      </Panel>
    </div>
  );
}

function LinkedInAssist({ onNotice }: { onNotice: (message: string) => void }) {
  const [keywords, setKeywords] = useState("AI Engineer, Generative AI Engineer, ML Engineer");
  const [location, setLocation] = useState("India");
  const [workMode, setWorkMode] = useState("remote");
  const [dateSincePosted, setDateSincePosted] = useState("past_week");
  const [easyApply, setEasyApply] = useState("any");
  const [limit, setLimit] = useState(6);
  const [plans, setPlans] = useState<LinkedInPlan[]>([]);
  const [checklist, setChecklist] = useState<string[]>([]);
  const [jobUrl, setJobUrl] = useState("https://www.linkedin.com/jobs/view/123456789");
  const [visibleText, setVisibleText] = useState(
    "Generative AI Engineer\nExampleAI\nRemote India\nBuild LLM applications with Python, FastAPI, RAG, vector databases, evaluation, and production APIs."
  );
  const [copied, setCopied] = useState(false);
  const bookmarkletHref = useMemo(() => buildBrowserAssistBookmarklet(`${API_URL}/browser-assist/import-bookmarklet`), []);

  const applyDiscoveryPreferences = (preferences: DiscoveryPreferences) => {
    if (preferences.keywords.length) setKeywords(preferences.keywords.join(", "));
    if (preferences.location) setLocation(preferences.location);
    setWorkMode(preferences.work_mode || "any");
    setDateSincePosted(preferences.date_since_posted || "past_week");
    setEasyApply(preferences.easy_apply || "any");
    setLimit(preferences.limit || 6);
  };

  useEffect(() => {
    api.linkedinPreferences().then(applyDiscoveryPreferences).catch((error) => onNotice(error.message));
  }, []);

  const preferencePayload = () => ({
    keywords: split(keywords),
    location,
    work_mode: workMode,
    date_since_posted: dateSincePosted,
    easy_apply: easyApply,
    limit
  });

  const buildPlans = async () => {
    const result = await api.linkedinAssist(preferencePayload());
    applyDiscoveryPreferences(result.preferences);
    setPlans(result.plans);
    setChecklist(result.checklist);
    onNotice(`Saved preferences and created ${result.plans.length} LinkedIn searches.`);
  };

  const savePreferences = async () => {
    const result = await api.saveLinkedinPreferences(preferencePayload());
    applyDiscoveryPreferences(result.preferences);
    onNotice(result.message);
  };

  const importVisible = async () => {
    const result = await api.linkedinImportVisible({ job_url: jobUrl, visible_text: visibleText });
    onNotice(`${result.message} Job ID: ${result.job_id}`);
  };

  const copyBookmarklet = async () => {
    await navigator.clipboard.writeText(bookmarkletHref);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1800);
  };

  return (
    <div className="grid gap-4 xl:grid-cols-[420px_1fr]">
      <Panel className="p-5">
        <div className="mb-4 flex items-center gap-2">
          <Linkedin size={18} className="text-cobalt" />
          <h2 className="text-base font-semibold">LinkedIn Browser Assist</h2>
        </div>
        <div className="grid gap-4">
          <Field label="Keywords"><textarea className={textareaClass} value={keywords} onChange={(e) => setKeywords(e.target.value)} /></Field>
          <Field label="Location"><input className={inputClass} value={location} onChange={(e) => setLocation(e.target.value)} /></Field>
          <Field label="Work mode">
            <select className={inputClass} value={workMode} onChange={(e) => setWorkMode(e.target.value)}>
              <option value="any">Any</option>
              <option value="remote">Remote</option>
              <option value="hybrid">Hybrid</option>
              <option value="onsite">Onsite</option>
            </select>
          </Field>
          <Field label="Date posted">
            <select className={inputClass} value={dateSincePosted} onChange={(e) => setDateSincePosted(e.target.value)}>
              <option value="past_24_hours">Past 24 hours</option>
              <option value="past_week">Past week</option>
              <option value="past_month">Past month</option>
              <option value="any">Any</option>
            </select>
          </Field>
          <Field label="Apply type">
            <select className={inputClass} value={easyApply} onChange={(e) => setEasyApply(e.target.value)}>
              <option value="any">Any</option>
              <option value="easy_apply">Easy Apply</option>
            </select>
          </Field>
          <Field label="Search count">
            <input className={inputClass} type="number" min={1} max={12} value={limit} onChange={(e) => setLimit(Number(e.target.value) || 1)} />
          </Field>
          <div className="flex flex-wrap gap-2">
            <Button onClick={() => buildPlans().catch((error) => onNotice(error.message))}>
              <Linkedin size={16} /> Generate And Save
            </Button>
            <Button variant="secondary" onClick={() => savePreferences().catch((error) => onNotice(error.message))}>
              <Save size={16} /> Save Preferences
            </Button>
          </div>
        </div>
        <div className="mt-5 grid gap-3 border-t border-line pt-5">
          <div className="flex flex-wrap items-center gap-2">
            <a
              className="focus-ring floating-lift inline-flex h-10 items-center justify-center gap-2 rounded-xl border border-cobalt bg-cobalt px-4 text-sm font-semibold text-white transition hover:opacity-90"
              href={bookmarkletHref}
              onClick={(event) => {
                event.preventDefault();
                copyBookmarklet().catch((error) => onNotice(error.message));
              }}
              draggable
              title="Drag this to your bookmarks bar, then click it on an open job page."
            >
              <ClipboardCheck size={16} /> Save Visible Job to SeekApply
            </a>
            <Button variant="secondary" onClick={() => copyBookmarklet().catch((error) => onNotice(error.message))}>
              <Save size={16} /> {copied ? "Copied" : "Copy"}
            </Button>
          </div>
          <div className="rounded border border-line bg-field p-3 text-xs leading-5 text-slate-600">
            Add this once to your bookmarks bar. On a LinkedIn job page, click it and the visible job details are saved here automatically.
          </div>
        </div>
        <div className="mt-5 grid gap-2">
          {checklist.map((item) => (
            <div key={item} className="border border-line bg-field p-3 text-sm text-slate-700">{item}</div>
          ))}
        </div>
      </Panel>
      <div className="grid gap-4">
        <Panel className="p-5">
          <h2 className="mb-4 text-base font-semibold">Search Links</h2>
          <div className="grid gap-3">
            {plans.map((plan) => (
              <div key={`${plan.keyword}-${plan.location}`} className="border border-line p-4">
                <div className="font-medium">{plan.keyword} · {plan.location}</div>
                <div className="mt-1 text-sm text-slate-500">{Object.entries(plan.filters).map(([key, value]) => `${key}: ${value}`).join(" · ")}</div>
                <a className="mt-3 inline-flex items-center gap-2 text-sm font-medium text-cobalt hover:underline" href={plan.url} target="_blank" rel="noreferrer">
                  <ExternalLink size={15} /> Open LinkedIn Search
                </a>
              </div>
            ))}
          </div>
        </Panel>
        <Panel className="p-5">
          <h2 className="mb-4 text-base font-semibold">Manual Fallback</h2>
          <div className="grid gap-4">
            <Field label="LinkedIn job URL"><input className={inputClass} value={jobUrl} onChange={(e) => setJobUrl(e.target.value)} /></Field>
            <Field label="Copied visible job text"><textarea className={textareaClass} value={visibleText} onChange={(e) => setVisibleText(e.target.value)} /></Field>
            <Button variant="secondary" onClick={() => importVisible().catch((error) => onNotice(error.message))}>
              <FileText size={16} /> Import For Review
            </Button>
          </div>
        </Panel>
      </div>
    </div>
  );
}

function JobSearch({ onNotice }: { onNotice: (message: string) => void }) {
  const [url, setUrl] = useState("https://example.com/jobs/genai-engineer");
  const [title, setTitle] = useState("Generative AI Engineer");
  const [company, setCompany] = useState("ExampleAI");
  const [description, setDescription] = useState("Build LLM applications with Python, FastAPI, RAG, vector databases, evaluation, and production APIs.");
  const [skills, setSkills] = useState("Python, FastAPI, LLMs, RAG, Vector DB");

  const importJob = async () => {
    const result = await api.importJob({
      job_url: url,
      title,
      company,
      location: "Remote India",
      work_mode: "remote",
      salary: "18-28 LPA",
      salary_min: 1800000,
      description,
      skills: split(skills),
      source: "manual"
    });
    onNotice(`${result.message} Job ID: ${result.job_id}`);
  };

  return (
    <Panel className="p-5">
      <h2 className="mb-4 text-base font-semibold">Manual Job Import</h2>
      <div className="grid gap-4 md:grid-cols-2">
        <Field label="Job URL"><input className={inputClass} value={url} onChange={(e) => setUrl(e.target.value)} /></Field>
        <Field label="Company"><input className={inputClass} value={company} onChange={(e) => setCompany(e.target.value)} /></Field>
        <Field label="Role"><input className={inputClass} value={title} onChange={(e) => setTitle(e.target.value)} /></Field>
        <Field label="Skills"><input className={inputClass} value={skills} onChange={(e) => setSkills(e.target.value)} /></Field>
        <Field label="Description"><textarea className={textareaClass} value={description} onChange={(e) => setDescription(e.target.value)} /></Field>
      </div>
      <div className="mt-4"><Button onClick={() => importJob().catch((error) => onNotice(error.message))}><FileText size={16} /> Import</Button></div>
    </Panel>
  );
}

function MatchReview({ onNotice, onRefresh }: { onNotice: (message: string) => void; onRefresh: () => Promise<void> }) {
  const [jobs, setJobs] = useState<JobRow[]>([]);
  const [busy, setBusy] = useState<Record<number, boolean>>({});
  const [results, setResults] = useState<Record<number, { lines: string[]; versionId?: number; aiGenerated?: boolean }>>({});
  const [appStatus, setAppStatus] = useState<Record<number, string>>({});

  const loadJobs = async () => {
    const data = await api.listJobs();
    setJobs(data.jobs);
  };

  useEffect(() => {
    loadJobs().catch((err) => onNotice(err.message));
  }, []);

  const setBusyFor = (id: number, state: boolean) =>
    setBusy((prev) => ({ ...prev, [id]: state }));

  const setResultFor = (id: number, lines: string[], versionId?: number, aiGenerated?: boolean) =>
    setResults((prev) => ({ ...prev, [id]: { lines, versionId, aiGenerated } }));

  const handleScore = async (job: JobRow) => {
    setBusyFor(job.id, true);
    try {
      const score = await api.scoreJob(job.id);
      setResultFor(job.id, [
        `${score.match_score}/100 — ${score.recommendation}`,
        ...score.reason,
        ...score.concerns,
      ]);
      await loadJobs();
      await onRefresh();
    } finally {
      setBusyFor(job.id, false);
    }
  };

  const handleDecision = async (job: JobRow) => {
    setBusyFor(job.id, true);
    try {
      const decision = await api.resumeDecision(job.id);
      const lines = [
        `${decision.match_score}/100 — threshold ${decision.threshold}`,
        `Action: ${decision.action}`,
        decision.message,
        decision.resume_path ? `Base resume: ${decision.resume_path}` : "",
        decision.ai_generated ? "✨ AI-generated tailored resume" : "",
        ...(decision.reasons ?? []),
        ...(decision.concerns ?? []),
      ].filter(Boolean);
      setResultFor(job.id, lines, decision.resume_version_id, decision.ai_generated);
      await loadJobs();
      await onRefresh();
    } finally {
      setBusyFor(job.id, false);
    }
  };

  const handleQuestions = async (job: JobRow) => {
    setBusyFor(job.id, true);
    try {
      const data = await api.requiredQuestions(job.id);
      setResultFor(job.id, [
        `Questions for job ${data.job_id}:`,
        ...data.questions.map((q) => `• ${q.question}`),
        `Saved approved answers: ${data.saved_answers.length}`,
      ]);
    } finally {
      setBusyFor(job.id, false);
    }
  };

  const handleStatusUpdate = async (job: JobRow, newStatus: string) => {
    if (!job.application_id) {
      onNotice("Run 'Score' or 'Resume Decision' first to create an application record.");
      return;
    }
    setBusyFor(job.id, true);
    try {
      await api.updateApplicationStatus(job.application_id, newStatus);
      setAppStatus((prev) => ({ ...prev, [job.id]: newStatus }));
      onNotice(`Application status updated to "${newStatus}".`);
      await loadJobs();
      await onRefresh();
    } catch (e: any) {
      onNotice(e.message);
    } finally {
      setBusyFor(job.id, false);
    }
  };

  const handleEmail = async (job: JobRow) => {
    setBusyFor(job.id, true);
    try {
      const draft = await api.draftEmail(job.id);
      setResultFor(job.id, [draft.subject, "", draft.body]);
      await onRefresh();
    } finally {
      setBusyFor(job.id, false);
    }
  };

  const scoreColor = (score: number | null) => {
    if (score === null) return "bg-slate-100 text-slate-500";
    if (score >= 85) return "bg-emerald-100 text-emerald-700";
    if (score >= 60) return "bg-amber-100 text-amber-700";
    return "bg-red-100 text-red-700";
  };

  const statusColor = (status: string) => {
    if (status === "Resume tailored") return "text-cobalt";
    if (status === "Shortlisted for review") return "text-moss";
    if (status === "Found") return "text-slate-500";
    return "text-slate-500";
  };

  if (jobs.length === 0) {
    return (
      <Panel className="p-8 text-center text-slate-500 text-sm">
        No jobs imported yet. Use <strong>Find Jobs</strong>, <strong>LinkedIn Assist</strong>, or <strong>Page Import</strong> to add jobs.
      </Panel>
    );
  }

  return (
    <div className="grid gap-4">
      <div className="flex items-center justify-between">
        <h2 className="text-base font-semibold flex items-center gap-2">
          <Zap size={18} className="text-cobalt" />
          Match &amp; Resume — {jobs.length} job{jobs.length !== 1 ? "s" : ""}
        </h2>
        <div className="flex items-center gap-3">
          <button
            onClick={() => loadJobs().catch((err) => onNotice(err.message))}
            className="flex items-center gap-1 text-sm text-slate-500 hover:text-cobalt"
          >
            <RefreshCcw size={14} /> Refresh
          </button>
        </div>
      </div>

      {jobs.map((job) => {
        const res = results[job.id];
        const isBusy = busy[job.id] ?? false;

        return (
          <Panel key={job.id} className="p-5">
            {/* Header row */}
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <div className="flex items-center gap-2">
                  <span className="font-semibold text-ink">{job.title}</span>
                  <span className="text-slate-400">·</span>
                  <span className="text-slate-600">{job.company}</span>
                  {job.location && <span className="text-xs text-slate-400">({job.location})</span>}
                </div>
                <div className="mt-1 flex flex-wrap items-center gap-2 text-xs">
                  <span className={`font-medium ${statusColor(job.status)}`}>{job.status}</span>
                  {job.work_mode && <span className="text-slate-400">{job.work_mode}</span>}
                  {job.salary && <span className="text-slate-400">{job.salary}</span>}
                  <a href={job.job_url} target="_blank" rel="noreferrer" className="flex items-center gap-1 text-cobalt hover:underline">
                    <ExternalLink size={12} /> View Job
                  </a>
                </div>
                {job.skills && job.skills.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-1">
                    {job.skills.slice(0, 8).map((skill) => (
                      <span key={skill} className="rounded bg-field px-2 py-0.5 text-xs text-slate-600 border border-line">
                        {skill}
                      </span>
                    ))}
                  </div>
                )}
              </div>

              {/* Score badge */}
              <div className={`rounded-lg px-3 py-1.5 text-center font-semibold text-sm min-w-[56px] ${scoreColor(job.match_score)}`}>
                {job.match_score !== null ? `${job.match_score}` : "—"}
                <div className="text-[10px] font-normal opacity-70">/100</div>
              </div>
            </div>

            {/* Action buttons */}
            <div className="mt-4 flex flex-wrap gap-2">
              <button
                disabled={isBusy}
                onClick={() => handleScore(job).catch((e) => onNotice(e.message))}
                className="flex items-center gap-1.5 rounded border border-line bg-white px-3 py-1.5 text-xs font-medium hover:bg-field disabled:opacity-50"
              >
                <Gauge size={13} /> Score
              </button>
              <button
                disabled={isBusy}
                onClick={() => handleDecision(job).catch((e) => onNotice(e.message))}
                className="flex items-center gap-1.5 rounded border border-cobalt bg-cobalt px-3 py-1.5 text-xs font-medium text-white hover:opacity-90 disabled:opacity-50"
              >
                {isBusy ? (
                  <span className="animate-pulse">Processing…</span>
                ) : (
                  <><Sparkles size={13} /> Resume Decision</>
                )}
              </button>
              <button
                disabled={isBusy}
                onClick={() => handleQuestions(job).catch((e) => onNotice(e.message))}
                className="flex items-center gap-1.5 rounded border border-line bg-white px-3 py-1.5 text-xs font-medium hover:bg-field disabled:opacity-50"
              >
                <ListChecks size={13} /> Questions
              </button>
              <button
                disabled={isBusy}
                onClick={() => handleEmail(job).catch((e) => onNotice(e.message))}
                className="flex items-center gap-1.5 rounded border border-line bg-white px-3 py-1.5 text-xs font-medium hover:bg-field disabled:opacity-50"
              >
                <MailPlus size={13} /> Draft Email
              </button>
            </div>

            {/* Application status controls — shown once an application record exists */}
            {job.application_id && (
              <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-line pt-3">
                <span className="text-xs text-slate-500 font-medium">Application:</span>
                <select
                  disabled={isBusy}
                  value={appStatus[job.id] ?? job.application_status ?? "Shortlisted for review"}
                  onChange={(e) => handleStatusUpdate(job, e.target.value).catch(() => undefined)}
                  className="rounded border border-line bg-white px-2 py-1 text-xs font-medium text-slate-700 focus:outline-none focus:ring-1 focus:ring-cobalt disabled:opacity-50"
                >
                  <option value="Shortlisted for review">Shortlisted for review</option>
                  <option value="Resume tailored">Resume tailored</option>
                  <option value="Email drafted">Email drafted</option>
                  <option value="Applied">Applied ✓</option>
                  <option value="Interview scheduled">Interview scheduled</option>
                  <option value="Offer received">Offer received</option>
                  <option value="Rejected">Rejected</option>
                  <option value="Withdrawn">Withdrawn</option>
                </select>
                <button
                  disabled={isBusy}
                  onClick={() => handleStatusUpdate(job, "Applied").catch(() => undefined)}
                  className="flex items-center gap-1.5 rounded bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-emerald-700 disabled:opacity-50"
                >
                  <CheckCircle2 size={13} /> Mark as Applied
                </button>
              </div>
            )}

            {/* Download buttons — from session result OR from existing resume version on the job */}
            {(res?.versionId || job.resume_version_id) && (
              <div className="mt-3 flex flex-wrap items-center gap-2">
                {res?.aiGenerated && (
                  <span className="flex items-center gap-1 text-xs font-medium text-cobalt">
                    <Sparkles size={12} /> AI-tailored
                  </span>
                )}
                <button
                  onClick={() => api.downloadResumePdf(res?.versionId ?? job.resume_version_id!).catch((e) => onNotice(e.message))}
                  className="flex items-center gap-1.5 rounded bg-moss px-3 py-1.5 text-xs font-medium text-white hover:opacity-90"
                >
                  <Download size={12} /> Download PDF
                </button>
                <button
                  onClick={() => api.downloadResumeDocx(res?.versionId ?? job.resume_version_id!).catch((e) => onNotice(e.message))}
                  className="flex items-center gap-1.5 rounded border border-moss px-3 py-1.5 text-xs font-medium text-moss hover:bg-field"
                >
                  <Download size={12} /> Download DOCX
                </button>
                <button
                  onClick={() => api.downloadResumeTex(res?.versionId ?? job.resume_version_id!).catch((e) => onNotice(e.message))}
                  className="flex items-center gap-1.5 rounded border border-indigo-400 px-3 py-1.5 text-xs font-medium text-indigo-600 hover:bg-indigo-50"
                >
                  <Download size={12} /> Download LaTeX
                </button>
              </div>
            )}

            {/* Result notes */}
            {res?.lines && res.lines.length > 0 && (
              <div className="mt-3 grid gap-1.5">
                {res.lines.map((line, index) => (
                  <div key={`${line}-${index}`} className="rounded border border-line bg-field px-3 py-2 text-xs text-slate-700">
                    {line}
                  </div>
                ))}
              </div>
            )}
          </Panel>
        );
      })}
    </div>
  );
}

function Tracker({ rows, analytics }: { rows: TrackerRow[]; analytics: Analytics | null }) {
  return (
    <div className="grid gap-4">
      <div className="grid gap-4 md:grid-cols-4">
        <StatCard label="Jobs" value={analytics?.total_jobs ?? 0} icon={<FileText size={18} />} />
        <StatCard label="Applications" value={analytics?.total_applications ?? 0} icon={<Send size={18} />} />
        <StatCard label="Avg score" value={analytics?.average_match_score ?? 0} icon={<Gauge size={18} />} />
        <StatCard label="Follow-ups" value={analytics?.followups_due_soon ?? 0} icon={<AlertTriangle size={18} />} />
      </div>
      <Panel className="overflow-hidden">
        <table className="w-full min-w-[880px] border-collapse text-sm">
          <thead className="bg-ink text-left text-white">
            <tr><th className="p-3">Company</th><th>Role</th><th>Status</th><th>Score</th><th>Source</th><th>Location</th><th>Resume</th></tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id} className="border-t border-line">
                <td className="p-3 font-medium">{row.company}</td><td>{row.role}</td><td>{row.status}</td><td>{row.match_score ?? "-"}</td><td>{row.source}</td><td>{row.location ?? "-"}</td><td>{row.resume_version ? "Ready" : "Missing"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
    </div>
  );
}

function ResumeVersions({ rows }: { rows: ResumeVersion[] }) {
  return (
    <Panel className="p-5">
      <h2 className="mb-4 text-base font-semibold">Resume Versions</h2>
      <div className="grid gap-3">
        {rows.map((row) => (
          <div key={row.id} className="grid gap-2 border border-line p-4 md:grid-cols-[1fr_auto]">
            <div><div className="font-medium">{row.role} at {row.company}</div><div className="text-sm text-slate-500">{row.skills_emphasized.join(", ")}</div></div>
            <div className="flex items-center gap-2 text-sm text-moss"><CheckCircle2 size={16} /> {row.truthfulness_status}</div>
          </div>
        ))}
      </div>
    </Panel>
  );
}

function Outreach({ onNotice }: { onNotice: (message: string) => void }) {
  const [jobId, setJobId] = useState("1");
  return (
    <Panel className="p-5">
      <h2 className="mb-4 text-base font-semibold">Email Outreach</h2>
      <div className="flex max-w-md gap-2">
        <input className={inputClass} value={jobId} onChange={(e) => setJobId(e.target.value)} />
        <Button onClick={() => api.draftEmail(Number(jobId)).then((res) => onNotice(`Drafted: ${res.subject}`)).catch((error) => onNotice(error.message))}><MailPlus size={16} /> Draft</Button>
      </div>
    </Panel>
  );
}

function AnalyticsView({ analytics }: { analytics: Analytics | null }) {
  const data = useMemo(
    () => Object.entries(analytics?.status_counts ?? {}).map(([name, value]) => ({ name, value })),
    [analytics]
  );
  return (
    <Panel className="h-[420px] p-5">
      <h2 className="mb-4 text-base font-semibold">Status Analytics</h2>
      <ResponsiveContainer width="100%" height="85%">
        <BarChart data={data}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="name" />
          <YAxis allowDecimals={false} />
          <Tooltip />
          <Bar dataKey="value" fill="#2557d6" />
        </BarChart>
      </ResponsiveContainer>
    </Panel>
  );
}

function SettingsView({ safety, oci }: { safety: string[]; oci: string }) {
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Panel className="p-5">
        <h2 className="mb-4 text-base font-semibold">Safety Defaults</h2>
        <div className="grid gap-2">{safety.map((rule) => <div key={rule} className="border border-line bg-field p-3 text-sm">{rule}</div>)}</div>
      </Panel>
      <Panel className="p-5">
        <h2 className="mb-4 text-base font-semibold">OCI Generative AI</h2>
        <div className="border border-line bg-field p-3 text-sm">{oci}</div>
      </Panel>
    </div>
  );
}

function split(value: string) {
  return value.split(",").map((item) => item.trim()).filter(Boolean);
}

function formatBytes(value: number | null | undefined) {
  if (!value) return "0 B";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${Math.round(value / 1024)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function buildBrowserAssistBookmarklet(endpoint: string) {
  const script = `(function(){function q(s){var e=document.querySelector(s);return e&&e.innerText?e.innerText.trim():""}var href=window.location.href;var host=window.location.hostname.replace(/^www\\./,"");var title=q(".job-details-jobs-unified-top-card__job-title,.topcard__title,[data-test-job-title],h1")||document.title;var company=q(".job-details-jobs-unified-top-card__company-name,.topcard__org-name-link,[data-test-job-company-name]");var loc=q(".job-details-jobs-unified-top-card__primary-description-container,.topcard__flavor--bullet,[data-test-job-location]");var desc=q(".jobs-description-content__text,.description__text,.jobs-box__html-content,[data-test-job-description],main")||document.body.innerText;var payload={page_url:href,source_site:host,title:title,company:company,location:loc,description:desc.slice(0,12000),visible_text:document.body.innerText.slice(0,12000),apply_url:href};var f=document.createElement("form");f.method="POST";f.action=${JSON.stringify(endpoint)};f.target="_blank";var i=document.createElement("input");i.type="hidden";i.name="payload";i.value=JSON.stringify(payload);f.appendChild(i);document.body.appendChild(f);f.submit();setTimeout(function(){f.remove()},1000);})();`;
  return `javascript:${script}`;
}
