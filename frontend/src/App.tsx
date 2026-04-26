import { useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  AlertTriangle,
  ArrowDownWideNarrow,
  BellRing,
  Bot,
  Bug,
  CheckCircle2,
  Clock3,
  ClipboardCheck,
  Download,
  Eye,
  ExternalLink,
  FileText,
  Gauge,
  History,
  Linkedin,
  ListChecks,
  MailPlus,
  Play,
  RefreshCcw,
  Save,
  Send,
  Shield,
  Sparkles,
  Upload,
  Workflow,
  X,
  Zap
} from "lucide-react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, API_URL, AgentCatalogItem, AgentPipelineStatus, AgentRunResult, Analytics, Answer, ApplyQueueTask, ApplyTaskDebug, Claim, CurrentResume, DiscoveryPreferences, JobDebug, JobRow, LinkedInPlan, ResumeLab, ResumePreview, ResumeVersion, TrackerRow } from "./lib/api";
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
  match_threshold: 60
};

export default function App() {
  const [active, setActive] = useState<SectionId>("cockpit");
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
              <div className="section-kicker">01 / 09 Agent Pipeline</div>
              <h1 className="hero-title mt-3 text-ink">Agent Cockpit for Supervised Applications</h1>
              <p className="mt-3 max-w-3xl text-sm leading-6 text-[#5f574b]">
                Run named agents for resume intake, job discovery, JD import, scoring, resume building, preview, questions, supervised apply, and tracking. Every stage writes trace output for debugging.
              </p>
            </div>
            <div className="rounded-[18px] border border-[#2f2f2f] bg-[#111111] p-4 text-[#f7f2e8] shadow-float fade-slide-up">
              <div className="section-kicker text-[#b7ff29]">Run Index</div>
              <div className="mt-2 text-xs leading-5">
                <div>02 / Find Jobs</div>
                <div>03 / Score Match</div>
                <div>04 / Build Resume</div>
                <div>05 / Supervised Apply</div>
              </div>
            </div>
          </div>
          <div className="mt-5 flex items-center gap-3">
            <Button variant="primary" onClick={() => refresh().catch((error) => setNotice(error.message))}>
              <RefreshCcw size={16} /> Refresh Data
            </Button>
            <span className="rounded-full border border-[#d6eba0] bg-[#effbcf] px-3 py-1 text-xs font-semibold text-[#2f3f13]">
              Supervised apply · submit stays manual
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
              {active === "cockpit" && <AgentCockpit onNotice={setNotice} onRefresh={refresh} />}
              {active === "resume" && <ResumeIntake onNotice={setNotice} />}
              {active === "onboarding" && <Onboarding onNotice={setNotice} onRefresh={refresh} />}
              {active === "search" && <JobSearch onNotice={setNotice} />}
              {active === "linkedin" && <LinkedInAssist onNotice={setNotice} onGoReview={() => setActive("review")} />}
              {active === "browser" && <BrowserAssist onNotice={setNotice} />}
              {active === "review" && <MatchReview onNotice={setNotice} onRefresh={refresh} />}
              {active === "apply" && <ApplyQueue onNotice={setNotice} onRefresh={refresh} />}
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

function AgentCockpit({ onNotice, onRefresh }: { onNotice: (message: string) => void; onRefresh: () => Promise<void> }) {
  const [catalog, setCatalog] = useState<AgentCatalogItem[]>([]);
  const [status, setStatus] = useState<AgentPipelineStatus | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [latestRun, setLatestRun] = useState<AgentRunResult | null>(null);
  const [debugRun, setDebugRun] = useState<AgentRunResult | null>(null);
  const [previewMode, setPreviewMode] = useState<"pdf" | "diff" | "before" | "after">("pdf");

  const load = async () => {
    const [catalogData, pipelineData] = await Promise.all([api.agentCatalog(), api.agentPipelineStatus()]);
    setCatalog(catalogData.agents);
    setStatus(pipelineData);
  };

  useEffect(() => {
    load().catch((error) => onNotice(error.message));
  }, []);

  const contextPayload = () => ({
    job_id: status?.latest_job_id ?? undefined,
    task_id: status?.latest_task_id ?? undefined,
    resume_version_id: status?.selected_resume_version_id ?? undefined,
  });

  const runNext = async () => {
    setBusy("pipeline");
    try {
      const result = await api.runPipeline(contextPayload());
      setLatestRun(result.result);
      setStatus(result.pipeline);
      onNotice(`${result.result.agent_label}: ${result.result.message}`);
      await onRefresh();
    } finally {
      setBusy(null);
    }
  };

  const runAgent = async (agentKey: string, extra: Record<string, unknown> = {}) => {
    setBusy(agentKey);
    try {
      const result = await api.runAgent(agentKey, { ...contextPayload(), ...extra });
      setLatestRun(result);
      await load();
      onNotice(`${result.agent_label}: ${result.message}`);
      await onRefresh();
    } finally {
      setBusy(null);
    }
  };

  const loadRun = async (runId: number) => {
    setDebugRun(await api.agentRun(runId));
  };

  const statusClass = (value: string) => {
    if (value === "completed") return "bg-emerald-100 text-emerald-700";
    if (value === "ready") return "bg-blue-100 text-cobalt";
    if (value === "running") return "bg-indigo-100 text-indigo-700";
    if (value === "needs_user_action" || value === "needs_answers" || value === "needs_login" || value === "ready_for_submit") return "bg-amber-100 text-amber-700";
    if (value === "failed") return "bg-red-100 text-red-700";
    return "bg-slate-100 text-slate-600";
  };

  const preview = latestRun?.artifacts?.preview as ResumePreview | undefined;
  const builderResume = latestRun?.artifacts?.resume_version as ResumeVersion | undefined;
  const builderComparison = latestRun?.artifacts?.comparison as
    | {
        original_match_score?: number;
        tailored_resume_score?: number;
        score_delta?: number;
        resume_changes?: string[];
      }
    | undefined;

  return (
    <div className="grid gap-4">
      <Panel className="p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="flex items-center gap-2 text-base font-semibold">
              <Workflow size={18} className="text-cobalt" /> Agent Cockpit
            </h2>
            <div className="mt-1 max-w-3xl text-sm text-slate-500">
              Run one lane at a time, inspect trace output, and keep browser apply supervised. The Apply Agent will not submit applications.
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" onClick={() => load().catch((error) => onNotice(error.message))}>
              <RefreshCcw size={15} /> Refresh
            </Button>
            <Button disabled={busy === "pipeline"} onClick={() => runNext().catch((error) => onNotice(error.message))}>
              <Play size={15} /> {busy === "pipeline" ? "Running..." : "Run Next Safe Step"}
            </Button>
          </div>
        </div>
        {status && (
          <div className="mt-4 grid gap-3 text-xs md:grid-cols-4">
            <div className="rounded border border-line bg-field p-3">
              <div className="font-semibold uppercase text-slate-500">Latest Job</div>
              <div className="mt-1 text-sm font-semibold text-ink">{status.latest_job_id ? `#${status.latest_job_id}` : "None"}</div>
            </div>
            <div className="rounded border border-line bg-field p-3">
              <div className="font-semibold uppercase text-slate-500">Resume Version</div>
              <div className="mt-1 text-sm font-semibold text-ink">{status.selected_resume_version_id ? `#${status.selected_resume_version_id}` : "Not selected"}</div>
            </div>
            <div className="rounded border border-line bg-field p-3">
              <div className="font-semibold uppercase text-slate-500">Apply Task</div>
              <div className="mt-1 text-sm font-semibold text-ink">{status.latest_task_id ? `#${status.latest_task_id}` : "Not queued"}</div>
            </div>
            <div className="rounded border border-line bg-field p-3">
              <div className="font-semibold uppercase text-slate-500">Threshold</div>
              <div className="mt-1 text-sm font-semibold text-ink">{status.threshold}/100</div>
            </div>
          </div>
        )}
      </Panel>

      <div className="grid gap-3">
        {(status?.lanes ?? catalog.map((agent) => ({ ...agent, status: "not_started", message: agent.description, artifacts: {}, next_actions: agent.actions }))).map((lane) => {
          const isApply = lane.key === "apply_agent";
          const isTracker = lane.key === "tracker_agent";
          const canPreview = Boolean(status?.selected_resume_version_id) && ["resume_builder", "resume_reviewer"].includes(lane.key);
          return (
            <Panel key={lane.key} className="p-5">
              <div className="grid gap-4 lg:grid-cols-[1fr_auto]">
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="rounded bg-field px-2 py-1 text-[11px] font-semibold uppercase text-slate-500">{lane.lane}</span>
                    <span className={`rounded px-2 py-1 text-xs font-semibold ${statusClass(lane.status)}`}>{lane.status}</span>
                    <span className="rounded border border-line bg-white px-2 py-1 text-[11px] text-slate-500">{lane.safety_mode}</span>
                  </div>
                  <h3 className="mt-3 flex items-center gap-2 text-base font-semibold text-ink">
                    <Bot size={17} className="text-cobalt" /> {lane.label}
                  </h3>
                  <p className="mt-1 text-sm leading-6 text-slate-600">{lane.message || lane.description}</p>
                  <div className="mt-3 flex flex-wrap gap-1">
                    {(lane.next_actions || []).slice(0, 5).map((action) => (
                      <span key={action} className="rounded border border-line bg-white px-2 py-1 text-[11px] text-slate-500">
                        {action.replace(/_/g, " ")}
                      </span>
                    ))}
                  </div>
                </div>
                <div className="flex flex-wrap items-start gap-2 lg:justify-end">
                  <Button disabled={busy === lane.key} onClick={() => runAgent(lane.key).catch((error) => onNotice(error.message))}>
                    <Play size={15} /> {busy === lane.key ? "Running..." : "Run"}
                  </Button>
                  {isApply && status?.latest_task_id && (
                    <Button
                      variant="secondary"
                      disabled={busy === lane.key}
                      onClick={() => runAgent(lane.key, { task_id: status.latest_task_id, action: "start", start_browser: true }).catch((error) => onNotice(error.message))}
                    >
                      <Linkedin size={15} /> Start Browser
                    </Button>
                  )}
                  {isTracker && status?.latest_task_id && (
                    <Button
                      variant="secondary"
                      disabled={busy === lane.key}
                      onClick={() => runAgent(lane.key, { task_id: status.latest_task_id, action: "mark_submitted" }).catch((error) => onNotice(error.message))}
                    >
                      <CheckCircle2 size={15} /> Mark Submitted
                    </Button>
                  )}
                  {canPreview && (
                    <Button
                      variant="secondary"
                      disabled={busy === "resume_reviewer"}
                      onClick={() => runAgent("resume_reviewer").catch((error) => onNotice(error.message))}
                    >
                      <Eye size={15} /> Preview
                    </Button>
                  )}
                  {latestRun?.agent_key === lane.key && latestRun.run_id && (
                    <Button variant="secondary" onClick={() => loadRun(latestRun.run_id!).catch((error) => onNotice(error.message))}>
                      <Bug size={15} /> Debug
                    </Button>
                  )}
                </div>
              </div>

              {lane.key === "resume_builder" && (
                <div className="mt-4 grid gap-3 rounded border border-line bg-white p-3 text-xs md:grid-cols-3">
                  <div>
                    <div className="font-semibold uppercase text-slate-500">Base Score</div>
                    <div className="mt-1 text-lg font-semibold text-ink">{builderComparison?.original_match_score ?? "Run builder"}</div>
                  </div>
                  <div>
                    <div className="font-semibold uppercase text-slate-500">Tailored Score</div>
                    <div className="mt-1 text-lg font-semibold text-ink">{builderComparison?.tailored_resume_score ?? "Pending"}</div>
                  </div>
                  <div>
                    <div className="font-semibold uppercase text-slate-500">Selected Resume</div>
                    <div className="mt-1 text-lg font-semibold text-ink">{builderResume?.id ? `#${builderResume.id}` : status?.selected_resume_version_id ? `#${status.selected_resume_version_id}` : "None"}</div>
                  </div>
                  {builderComparison?.resume_changes && builderComparison.resume_changes.length > 0 && (
                    <div className="md:col-span-3">
                      <div className="font-semibold uppercase text-slate-500">Changes</div>
                      <div className="mt-2 flex flex-wrap gap-2">
                        {compactResumeChanges(builderComparison.resume_changes).slice(0, 5).map((change) => (
                          <span key={change} className="rounded bg-field px-2 py-1 text-slate-700">{change}</span>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </Panel>
          );
        })}
      </div>

      {latestRun && (
        <Panel className="p-5">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h3 className="text-base font-semibold">Latest Run: {latestRun.agent_label}</h3>
              <div className="mt-1 text-sm text-slate-500">{latestRun.message}</div>
            </div>
            <span className={`rounded px-2 py-1 text-xs font-semibold ${statusClass(latestRun.status)}`}>{latestRun.status}</span>
          </div>
          {latestRun.trace.length > 0 && (
            <div className="mt-4 grid gap-2">
              {latestRun.trace.slice(-8).map((step, index) => (
                <div key={`${step.name}-${index}`} className="rounded border border-line bg-field p-3 text-xs">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="font-semibold text-ink">{step.name}</span>
                    <span className="rounded bg-white px-2 py-0.5 text-slate-600">{step.status}</span>
                  </div>
                  <div className="mt-1 text-slate-700">{step.message}</div>
                </div>
              ))}
            </div>
          )}
        </Panel>
      )}

      {preview && (
        <Panel className="p-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h3 className="text-base font-semibold">Resume Review Preview</h3>
              <div className="mt-1 text-sm text-slate-500">
                Version #{preview.version.id} · {preview.version.tailored_score ?? "?"}/100
              </div>
            </div>
            <div className="flex flex-wrap gap-2">
              {(["pdf", "diff", "before", "after"] as const).map((mode) => (
                <button
                  key={mode}
                  onClick={() => setPreviewMode(mode)}
                  className={`rounded border px-3 py-1.5 text-xs font-semibold ${previewMode === mode ? "border-cobalt bg-cobalt text-white" : "border-line bg-white text-ink hover:bg-field"}`}
                >
                  {mode === "pdf" ? "PDF" : mode === "diff" ? "Difference" : mode === "before" ? "Before Text" : "After Text"}
                </button>
              ))}
            </div>
          </div>
          {previewMode === "pdf" ? (
            <PdfPreviewGrid preview={preview} />
          ) : previewMode === "diff" ? (
            <ResumeDifferenceView preview={preview} />
          ) : (
            <pre className="mt-4 max-h-[52vh] overflow-auto rounded border border-line bg-white p-3 text-xs leading-5 text-slate-800">
              {previewMode === "before" ? preview.base_preview : preview.tailored_preview}
            </pre>
          )}
        </Panel>
      )}

      {debugRun && (
        <div className="fixed inset-0 z-50 grid place-items-center bg-black/40 p-4">
          <div className="flex max-h-[88vh] w-full max-w-5xl flex-col rounded-[18px] border border-line bg-[#fffdf7] p-5 shadow-float">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h3 className="text-base font-semibold">Agent Run Debug</h3>
                <div className="mt-1 text-sm text-slate-500">{debugRun.agent_label} · run #{debugRun.run_id}</div>
              </div>
              <button className="text-sm text-slate-500 hover:text-ink" onClick={() => setDebugRun(null)}>Close</button>
            </div>
            <pre className="mt-4 max-h-[70vh] overflow-auto rounded border border-line bg-white p-3 text-xs leading-5 text-slate-800">
              {JSON.stringify(debugRun, null, 2)}
            </pre>
          </div>
        </div>
      )}
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
    experience_years: number;
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
        experience_years: data.profile.experience_years ?? 0,
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
    add("experience_years", "How many years of professional experience should be used for job eligibility?", String(extracted.experience_years ?? 0), false);
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
        experience_years: Number(extracted.experience_years) || 0,
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
          <div className="relative mt-2">
            <input
              id="resume-upload"
              type="file"
              accept=".pdf,.docx,.txt,.md"
              disabled={busy}
              className="absolute inset-0 z-10 h-full w-full cursor-pointer opacity-0"
              onChange={(event) => upload(event.target.files?.[0]).catch((error) => onNotice(error.message))}
            />
            <label
              htmlFor="resume-upload"
              className={`flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed ${busy ? 'border-slate-300 bg-slate-50' : 'border-[#c6d79e] bg-[#f8fcf0] hover:bg-[#effbcf]'} px-6 py-10 transition-colors`}
            >
              <div className={`rounded-full p-3 ${busy ? 'bg-slate-200 text-slate-500' : 'bg-[#d6eba0] text-[#3f5a0a]'}`}>
                {busy ? <RefreshCcw size={24} className="animate-spin" /> : <Upload size={24} />}
              </div>
              <div className={`mt-4 font-semibold ${busy ? 'text-slate-600' : 'text-[#2f3f13]'}`}>
                {busy ? "Extracting profile and questions..." : "Click or drag to upload base resume"}
              </div>
              <div className="mt-1 text-sm text-[#5f574b]">
                PDF, DOCX, TXT, or Markdown
              </div>
            </label>
          </div>
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
          ) : null}
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
                <Field label="Years of experience">
                  <input
                    className={inputClass}
                    type="number"
                    min={0}
                    step={0.1}
                    value={extracted.experience_years ?? 0}
                    onChange={(e) => setExtracted({ ...extracted, experience_years: Number(e.target.value) || 0 })}
                  />
                </Field>
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
  const [experienceYears, setExperienceYears] = useState(defaultProfile.experience_years);
  const [skills, setSkills] = useState(defaultProfile.skills.join(", "));
  const [targetRoles, setTargetRoles] = useState(defaultPreferences.target_roles.join(", "));
  const [excluded, setExcluded] = useState("");

  const save = async () => {
    const payload = {
      profile: { ...defaultProfile, name, email, experience_years: experienceYears, skills: split(skills) },
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
          <Field label="Years of experience">
            <input className={inputClass} type="number" min={0} step={0.1} value={experienceYears} onChange={(e) => setExperienceYears(Number(e.target.value) || 0)} />
          </Field>
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
    onNotice(`${result.message} Job ID: ${result.job_id}. Confidence: ${result.parser_confidence}. ${result.experience_requirement?.message || ""}`.trim());
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

function LinkedInAssist({ onNotice, onGoReview }: { onNotice: (message: string) => void; onGoReview: () => void }) {
  const [keywords, setKeywords] = useState("AI Engineer, Generative AI Engineer, ML Engineer, AI Scientist");
  const [location, setLocation] = useState("India");
  const [workMode, setWorkMode] = useState("Hybrid");
  const [dateSincePosted, setDateSincePosted] = useState("past_week");
  const [easyApply, setEasyApply] = useState("any");
  const [limit, setLimit] = useState(6);
  const [maxPages, setMaxPages] = useState(5);
  const [plans, setPlans] = useState<LinkedInPlan[]>([]);
  const [checklist, setChecklist] = useState<string[]>([]);
  const [jobUrl, setJobUrl] = useState("https://www.linkedin.com/jobs/view/123456789");
  const [visibleText, setVisibleText] = useState(
    "Generative AI Engineer\nExampleAI\nRemote India\nBuild LLM applications with Python, FastAPI, RAG, vector databases, evaluation, and production APIs."
  );
  const [importedJobs, setImportedJobs] = useState<JobRow[]>([]);
  const [importBusy, setImportBusy] = useState(false);
  const [importResult, setImportResult] = useState<string[]>([]);
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
    loadImportedJobs().catch((error) => onNotice(error.message));
  }, []);

  const loadImportedJobs = async () => {
    const data = await api.listJobs();
    setImportedJobs(
      data.jobs.filter((job) => job.source.includes("linkedin") || job.source.includes("browser_assist")).slice(0, 25)
    );
  };

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

  const runSupervisedImport = async () => {
    setImportBusy(true);
    setImportResult(["Opening supervised browser. Complete login or prompts there if LinkedIn asks."]);
    try {
      const saved = await api.saveLinkedinPreferences(preferencePayload());
      applyDiscoveryPreferences(saved.preferences);
      const result = await api.supervisedLinkedinImport({
        max_jobs: Math.max(1, Math.min(limit, 50)),
        include_descriptions: true,
        wait_seconds: 90,
        max_pages: Math.max(1, Math.min(maxPages, 8)),
        skip_existing: true
      });
      setImportResult([
        `${result.status}: ${result.message}`,
        `Found ${result.jobs_found}; added ${result.jobs_added}; already saved ${result.jobs_deduped}.`,
        result.pages_scanned ? `Pages scanned: ${result.pages_scanned}. Saved jobs skipped before opening details: ${result.jobs_skipped_existing ?? 0}.` : "",
        ...(result.action_required ? [result.action_required] : []),
        ...result.errors
      ].filter(Boolean));
      await loadImportedJobs();
      onNotice(`LinkedIn import finished: ${result.jobs_added} new job(s), ${result.jobs_deduped} duplicate(s).`);
    } catch (error: any) {
      setImportResult([error.message]);
      onNotice(error.message);
    } finally {
      setImportBusy(false);
    }
  };

  const importVisible = async () => {
    const result = await api.linkedinImportVisible({ job_url: jobUrl, visible_text: visibleText });
    await loadImportedJobs();
    onNotice(`${result.message} Job ID: ${result.job_id}. ${result.experience_requirement?.message || ""}`.trim());
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
          <Field label="Pages per search">
            <input className={inputClass} type="number" min={1} max={8} value={maxPages} onChange={(e) => setMaxPages(Number(e.target.value) || 1)} />
          </Field>
          <div className="flex flex-wrap gap-2">
            <Button onClick={() => buildPlans().catch((error) => onNotice(error.message))}>
              <Linkedin size={16} /> Generate And Save
            </Button>
            <Button disabled={importBusy} onClick={() => runSupervisedImport()}>
              <Sparkles size={16} /> {importBusy ? "Importing..." : "Auto Import Jobs"}
            </Button>
            <Button variant="secondary" onClick={() => savePreferences().catch((error) => onNotice(error.message))}>
              <Save size={16} /> Save Preferences
            </Button>
          </div>
        </div>
        {importResult.length > 0 && (
          <div className="mt-5 grid gap-2">
            {importResult.map((line, index) => (
              <div key={`${line}-${index}`} className="rounded border border-line bg-field px-3 py-2 text-xs text-slate-700">
                {line}
              </div>
            ))}
          </div>
        )}
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
              title="Drag this to your bookmarks bar, then click it on a LinkedIn search results page or job page."
            >
              <ClipboardCheck size={16} /> Save Visible Jobs to SeekApply
            </a>
            <Button variant="secondary" onClick={() => copyBookmarklet().catch((error) => onNotice(error.message))}>
              <Save size={16} /> {copied ? "Copied" : "Copy"}
            </Button>
          </div>
          <div className="rounded border border-line bg-field p-3 text-xs leading-5 text-slate-600">
            Add this once to your bookmarks bar. Open a LinkedIn search page, scroll to load jobs, then click it to save visible cards. Auto Import scans later result pages, skips jobs already saved, and uses a visible browser with conservative delays.
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
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-base font-semibold">Imported Jobs</h2>
            <div className="flex flex-wrap gap-2">
              <Button variant="secondary" onClick={() => loadImportedJobs().catch((error) => onNotice(error.message))}>
                <RefreshCcw size={15} /> Refresh
              </Button>
              <Button onClick={onGoReview}>
                <Gauge size={15} /> Match &amp; Resume
              </Button>
            </div>
          </div>
          {importedJobs.length ? (
            <div className="grid gap-3">
              {importedJobs.map((job) => (
                <div key={job.id} className="rounded border border-line bg-white p-3 text-sm">
                  <div className="font-semibold text-ink">{job.title}</div>
                  <div className="mt-1 text-slate-600">{job.company}{job.location ? ` · ${job.location}` : ""}</div>
                  <div className="mt-1 text-xs text-slate-500">{job.status} · score {job.match_score ?? "-"}</div>
                  <div className={`mt-2 inline-flex max-w-full rounded border px-2 py-0.5 text-xs font-medium ${experienceFitClass(job.experience_fit?.status)}`}>
                    {experienceFitShort(job)}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="rounded border border-line bg-field p-3 text-sm text-slate-500">
              No imported LinkedIn jobs yet. Open a search link, scroll the results, then click the bookmarklet.
            </div>
          )}
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
    onNotice(`${result.message} Job ID: ${result.job_id}. ${result.experience_requirement?.message || ""}`.trim());
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

function DebugModal({ title, data, onClose }: { title: string; data: JobDebug | ApplyTaskDebug; onClose: () => void }) {
  const trace = "trace" in data ? data.trace : data.queue_tasks.flatMap((task) => task.trace ?? []);
  const diagnosis = data.diagnosis ?? [];
  const runs = data.agent_runs ?? [];
  const fillReport = "fill_report" in data ? data.fill_report : null;
  const automationDebug = fillReport && Array.isArray(fillReport.automation_debug) ? fillReport.automation_debug : [];
  const debugSummary = (value: unknown) => {
    const text = JSON.stringify(value, null, 2) || "";
    return text.length > 1600 ? `${text.slice(0, 1600)}\n...` : text;
  };
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/40 p-4">
      <div className="flex max-h-[88vh] w-full max-w-5xl flex-col rounded-[18px] border border-line bg-[#fffdf7] p-5 shadow-float">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h3 className="text-base font-semibold">{title}</h3>
            <div className="mt-1 text-sm text-slate-500">
              {"task" in data ? `${data.task.job.company} · ${data.task.job.title}` : `${data.job.company} · ${data.job.title}`}
            </div>
          </div>
          <button className="text-sm text-slate-500 hover:text-ink" onClick={onClose}>Close</button>
        </div>

        <div className="mt-4 grid gap-3 overflow-auto">
          <div className="rounded border border-line bg-field p-3">
            <div className="text-xs font-semibold uppercase text-slate-500">Diagnosis</div>
            <div className="mt-2 grid gap-1 text-sm text-slate-700">
              {diagnosis.map((item) => <div key={item}>{item}</div>)}
            </div>
          </div>

          {fillReport && (
            <div className="rounded border border-line bg-white p-3">
              <div className="text-xs font-semibold uppercase text-slate-500">Browser Snapshot</div>
              <div className="mt-2 grid gap-2 text-xs text-slate-700">
                {typeof fillReport.page_title === "string" && fillReport.page_title && (
                  <div><span className="font-semibold text-ink">Title:</span> {fillReport.page_title}</div>
                )}
                {typeof fillReport.current_url === "string" && fillReport.current_url && (
                  <div className="break-all"><span className="font-semibold text-ink">URL:</span> {fillReport.current_url}</div>
                )}
                {typeof fillReport.last_click_blocker === "string" && fillReport.last_click_blocker && (
                  <div><span className="font-semibold text-ink">Last click blocker:</span> {fillReport.last_click_blocker}</div>
                )}
                {Array.isArray(fillReport.disabled_buttons_seen) && fillReport.disabled_buttons_seen.length > 0 && (
                  <div>
                    <div className="font-semibold text-ink">Disabled buttons</div>
                    <div className="mt-1 flex flex-wrap gap-1">
                      {fillReport.disabled_buttons_seen.slice(0, 12).map((button: unknown, index: number) => (
                        <span key={`${String(button)}-${index}`} className="rounded bg-amber-50 px-2 py-1 text-amber-800">{String(button)}</span>
                      ))}
                    </div>
                  </div>
                )}
                {Array.isArray(fillReport.buttons_seen) && fillReport.buttons_seen.length > 0 && (
                  <div>
                    <div className="font-semibold text-ink">Visible buttons</div>
                    <div className="mt-1 flex flex-wrap gap-1">
                      {fillReport.buttons_seen.slice(0, 16).map((button: unknown, index: number) => (
                        <span key={`${String(button)}-${index}`} className="rounded bg-field px-2 py-1">{String(button)}</span>
                      ))}
                    </div>
                  </div>
                )}
                {Array.isArray(fillReport.fields_seen) && fillReport.fields_seen.length > 0 && (
                  <div>
                    <div className="font-semibold text-ink">Visible fields</div>
                    <div className="mt-1 flex flex-wrap gap-1">
                      {fillReport.fields_seen.slice(0, 16).map((field: unknown, index: number) => (
                        <span key={`${String(field)}-${index}`} className="rounded bg-field px-2 py-1">{String(field)}</span>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}

          {automationDebug.length > 0 && (
            <div className="rounded border border-line bg-white p-3">
              <div className="text-xs font-semibold uppercase text-slate-500">Automation Debug Timeline</div>
              <div className="mt-2 grid gap-2">
                {automationDebug.slice(-12).map((entry: unknown, index: number) => {
                  const item = entry as { event?: string; data?: unknown };
                  return (
                    <details key={`${item.event || "event"}-${index}`} className="rounded border border-line bg-field p-2 text-xs">
                      <summary className="cursor-pointer font-semibold text-ink">{item.event || "automation_event"}</summary>
                      <pre className="mt-2 max-h-52 overflow-auto whitespace-pre-wrap text-slate-700">{debugSummary(item.data ?? item)}</pre>
                    </details>
                  );
                })}
              </div>
            </div>
          )}

          {trace.length > 0 && (
            <div className="rounded border border-line bg-white p-3">
              <div className="text-xs font-semibold uppercase text-slate-500">Agent Trace</div>
              <div className="mt-2 grid gap-2">
                {trace.map((step, index) => (
                  <div key={`${step.name}-${index}`} className="grid gap-1 rounded border border-line bg-field p-2 text-xs">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="font-semibold text-ink">{step.name}</span>
                      <span className="rounded bg-white px-2 py-0.5 text-slate-600">{step.status}</span>
                    </div>
                    <div className="text-slate-700">{step.message}</div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {runs.length > 0 && (
            <div className="rounded border border-line bg-white p-3">
              <div className="text-xs font-semibold uppercase text-slate-500">Agent Runs</div>
              <div className="mt-2 grid gap-2">
                {runs.slice(0, 8).map((run) => (
                  <div key={run.id} className="rounded border border-line bg-field p-2 text-xs text-slate-700">
                    <div className="font-semibold text-ink">{run.agent_name} · {run.status}</div>
                    <div>{run.output_summary}</div>
                  </div>
                ))}
              </div>
            </div>
          )}

          <details className="rounded border border-line bg-white p-3 text-xs">
            <summary className="cursor-pointer font-semibold text-ink">Raw Debug JSON</summary>
            <pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap text-slate-700">{JSON.stringify(data, null, 2)}</pre>
          </details>
        </div>
      </div>
    </div>
  );
}

type ApplyRunSnapshot = {
  task: ApplyQueueTask;
  status: string;
  message: string;
  action_required: string | null;
  missing_questions: string[];
  fill_report: Record<string, unknown>;
  steps: string[];
  errors: string[];
};

function applyNeedsIntervention(status: string) {
  return ["needs_login", "needs_answers", "needs_user_action", "ready_for_submit", "failed"].includes(status);
}

function applySnapshotFromTask(task: ApplyQueueTask): ApplyRunSnapshot {
  return {
    task,
    status: task.status,
    message: task.message || "Apply agent is waiting for the next action.",
    action_required: null,
    missing_questions: task.missing_questions || [],
    fill_report: task.fill_report || {},
    steps: task.steps || [],
    errors: task.last_error ? [task.last_error] : [],
  };
}

function interventionTitle(status: string) {
  if (status === "needs_login") return "User Input Required: Login or Verification";
  if (status === "needs_answers") return "User Input Required: New Questions";
  if (status === "ready_for_submit") return "Ready For Final Review";
  if (status === "failed") return "Apply Agent Needs Debugging";
  return "User Input Required";
}

function ApplyInterventionModal({
  snapshot,
  busy,
  onClose,
  onAnswer,
  onResume,
  onMarkSubmitted,
  onDebug,
}: {
  snapshot: ApplyRunSnapshot;
  busy?: boolean;
  onClose: () => void;
  onAnswer: () => void;
  onResume: () => void;
  onMarkSubmitted: () => void;
  onDebug: () => void;
}) {
  const report = snapshot.fill_report || {};
  const buttons = Array.isArray(report.buttons_seen) ? report.buttons_seen.slice(0, 10) : [];
  const fields = Array.isArray(report.fields_seen) ? report.fields_seen.slice(0, 10) : [];
  const currentUrl = typeof report.current_url === "string" ? report.current_url : typeof report.active_browser_url === "string" ? report.active_browser_url : "";
  const canAnswer = snapshot.missing_questions.length > 0 || snapshot.status === "needs_answers";
  const canSubmit = ["ready_for_submit", "needs_user_action", "failed"].includes(snapshot.status);
  const canResume = ["needs_login", "needs_user_action", "failed"].includes(snapshot.status);

  return (
    <div className="fixed inset-0 z-[60] grid place-items-center bg-black/45 p-4">
      <div className="flex max-h-[90vh] w-full max-w-3xl flex-col rounded-[18px] border border-line bg-[#fffdf7] shadow-float">
        <div className="flex flex-wrap items-start justify-between gap-3 border-b border-line p-5">
          <div>
            <div className="inline-flex items-center gap-2 rounded bg-amber-100 px-2 py-1 text-xs font-semibold text-amber-800">
              <BellRing size={14} /> {snapshot.status.replace(/_/g, " ")}
            </div>
            <h3 className="mt-3 text-lg font-semibold text-ink">{interventionTitle(snapshot.status)}</h3>
            <div className="mt-1 text-sm text-slate-600">
              {snapshot.task.job.company} · {snapshot.task.job.title}
            </div>
          </div>
          <button className="rounded p-2 text-slate-500 hover:bg-field hover:text-ink" onClick={onClose} aria-label="Close">
            <X size={18} />
          </button>
        </div>
        <div className="grid gap-4 overflow-auto p-5">
          <div className="rounded border border-blue-200 bg-blue-50 p-3 text-xs leading-5 text-blue-900">
            SeekApply opens only the saved job URL, clicks the top-card apply action, fills visible fields from your profile/KB, uploads the selected resume, and tracks the visible browser. After you submit in the browser, the next run can detect the submitted confirmation and release the session.
          </div>
          <div className="rounded border border-line bg-field p-3 text-sm leading-6 text-slate-700">
            {snapshot.action_required || snapshot.message}
          </div>

          {currentUrl && (
            <div className="break-all rounded border border-line bg-white p-3 text-xs text-slate-600">
              <span className="font-semibold text-ink">Browser URL:</span> {currentUrl}
            </div>
          )}

          {snapshot.missing_questions.length > 0 && (
            <div className="grid gap-2 rounded border border-amber-200 bg-amber-50 p-3">
              <div className="text-xs font-semibold uppercase text-amber-800">Questions to answer and save</div>
              {snapshot.missing_questions.map((question) => (
                <div key={question} className="rounded border border-amber-200 bg-white px-3 py-2 text-sm text-slate-700">
                  {question}
                </div>
              ))}
            </div>
          )}

          {(buttons.length > 0 || fields.length > 0) && (
            <div className="grid gap-3 md:grid-cols-2">
              {buttons.length > 0 && (
                <div className="rounded border border-line bg-white p-3">
                  <div className="text-xs font-semibold uppercase text-slate-500">Agent saw buttons</div>
                  <div className="mt-2 flex flex-wrap gap-1">
                    {buttons.map((button, index) => (
                      <span key={`${String(button)}-${index}`} className="rounded bg-field px-2 py-1 text-xs text-slate-600">{String(button)}</span>
                    ))}
                  </div>
                </div>
              )}
              {fields.length > 0 && (
                <div className="rounded border border-line bg-white p-3">
                  <div className="text-xs font-semibold uppercase text-slate-500">Agent saw fields</div>
                  <div className="mt-2 flex flex-wrap gap-1">
                    {fields.map((field, index) => (
                      <span key={`${String(field)}-${index}`} className="rounded bg-field px-2 py-1 text-xs text-slate-600">{String(field)}</span>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {snapshot.steps.length > 0 && (
            <div className="rounded border border-line bg-white p-3">
              <div className="text-xs font-semibold uppercase text-slate-500">Latest agent steps</div>
              <div className="mt-2 grid gap-1 text-xs text-slate-700">
                {snapshot.steps.slice(-6).map((step, index) => (
                  <div key={`${step}-${index}`} className="flex gap-2">
                    <span className="text-slate-400">{index + 1}</span>
                    <span>{step}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
        <div className="flex flex-wrap justify-end gap-2 border-t border-line p-5">
          <Button variant="secondary" onClick={onDebug}>
            <Bug size={15} /> Debug
          </Button>
          {canAnswer && (
            <Button onClick={onAnswer}>
              <ListChecks size={15} /> Answer Questions
            </Button>
          )}
          {canResume && (
            <Button disabled={busy} onClick={onResume}>
              <RefreshCcw size={15} /> {busy ? "Resuming..." : "Resume Agent"}
            </Button>
          )}
          {canSubmit && (
            <Button onClick={onMarkSubmitted}>
              <CheckCircle2 size={15} /> Mark Submitted
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}

function ApplyAgentProgress({ task }: { task: ApplyQueueTask }) {
  const report = task.fill_report || {};
  const hasBrowser = Boolean(report.current_url || task.trace?.some((step) => step.name === "browser_agent"));
  const filledCount = Number(report.profile_fields_filled || 0) + Number(report.answers_filled || 0);
  const resumeUploaded = Boolean(report.resume_uploaded);
  const finalReady = task.status === "ready_for_submit" || task.status === "submitted_by_user";
  const needsInput = applyNeedsIntervention(task.status) && !finalReady;
  const steps = [
    { label: "Resume", detail: task.resume ? `#${task.resume.id}` : "Preparing", done: Boolean(task.resume) },
    { label: "Browser", detail: hasBrowser ? applyModeLabelFromTask(task) : "Not opened", done: hasBrowser },
    { label: "Fill", detail: resumeUploaded || filledCount ? `${resumeUploaded ? "resume" : ""}${resumeUploaded && filledCount ? " + " : ""}${filledCount || ""} field${filledCount === 1 ? "" : "s"}` : "Waiting", done: resumeUploaded || filledCount > 0 },
    { label: "Input", detail: needsInput ? "Required" : "Clear", done: !needsInput },
    { label: "Submit", detail: task.status === "submitted_by_user" ? "Confirmed" : finalReady ? "Review" : "Awaiting confirm", done: finalReady },
  ];

  return (
    <div className="mt-4 grid gap-2 rounded border border-line bg-white p-3 md:grid-cols-5">
      {steps.map((step, index) => (
        <div key={step.label} className="flex min-w-0 items-center gap-2">
          <div className={`grid h-7 w-7 shrink-0 place-items-center rounded-full text-xs font-semibold ${step.done ? "bg-emerald-100 text-emerald-700" : index === 3 && needsInput ? "bg-amber-100 text-amber-700" : "bg-field text-slate-500"}`}>
            {step.done ? <CheckCircle2 size={14} /> : <Clock3 size={14} />}
          </div>
          <div className="min-w-0">
            <div className="truncate text-xs font-semibold text-ink">{step.label}</div>
            <div className="truncate text-[11px] text-slate-500">{step.detail}</div>
          </div>
        </div>
      ))}
    </div>
  );
}

function applyModeLabelFromTask(task: ApplyQueueTask) {
  const mode = String(task.fill_report?.mode || task.source || "");
  if (mode === "linkedin_easy_apply") return "Easy Apply";
  if (mode === "external_from_linkedin") return "External link";
  if (mode === "external_site") return "External site";
  return mode.replace(/_/g, " ") || "Browser";
}

function previewUrl(path: string | null | undefined) {
  if (!path) return null;
  if (path.startsWith("http://") || path.startsWith("https://")) return path;
  return `${API_URL}${path}`;
}

function resumePdfStatusLabel(mode?: string | null) {
  if (mode === "docx_converter") return "Tailored PDF converted from Word";
  if (mode === "latex_compiler") return "Tailored PDF compiled from LaTeX";
  if (mode === "base_pdf_fallback") return "Legacy uploaded PDF fallback";
  if (mode === "styled_pdf_fallback") return "Updated ATS PDF generated";
  return "PDF pending";
}

function resumePdfStatusNote(mode?: string | null, fallback?: string | null) {
  if (mode === "docx_converter") return "The visual PDF preview includes the latest tailored Word resume edits.";
  if (mode === "latex_compiler") return "The visual PDF preview includes the latest tailored LaTeX edits.";
  if (mode === "base_pdf_fallback") {
    return "This older version used the uploaded PDF fallback. Download PDF again to regenerate an updated ATS PDF, or install a local LaTeX compiler for exact Overleaf rendering.";
  }
  if (mode === "styled_pdf_fallback") return fallback || "SeekApply generated an updated ATS PDF fallback because no layout-preserving converter was available.";
  return fallback || "Run Resume Decision or Auto Refine to prepare a resume PDF.";
}

function resumePdfStatusClass(mode?: string | null) {
  if (mode === "docx_converter") return "border-emerald-200 bg-emerald-50 text-emerald-800";
  if (mode === "latex_compiler") return "border-emerald-200 bg-emerald-50 text-emerald-800";
  if (mode === "base_pdf_fallback") return "border-amber-200 bg-amber-50 text-amber-800";
  if (mode === "styled_pdf_fallback") return "border-sky-200 bg-sky-50 text-sky-800";
  return "border-line bg-field text-slate-700";
}

function compactResumeChange(change: string) {
  const clean = change.replace(/^Changed:\s*/i, "").replace(/\.$/, "").trim();
  if (/Kept the uploaded Word resume template/i.test(clean)) return "Kept your Word layout";
  if (/Updated the Profile\/Summary section in the Word resume/i.test(clean)) return "Retargeted Word Profile/Summary";
  if (/Added a compact Targeted Focus line in the Word resume/i.test(clean)) return "Updated Word Targeted Focus";
  if (/Kept the uploaded LaTeX resume template/i.test(clean)) return "Kept your Overleaf layout";
  if (/Updated the Profile\/Summary section/i.test(clean)) return "Retargeted Profile/Summary";
  if (/Added a compact Targeted Focus line/i.test(clean)) return "Updated Targeted Focus skills";
  if (/Generated a clean ATS resume/i.test(clean)) return "Generated a clean ATS resume";
  if (/Generated a clean LaTeX resume/i.test(clean)) return "Generated a LaTeX resume template";
  if (/Emphasized verified overlap:/i.test(clean)) return clean.replace("Emphasized verified overlap:", "Emphasized verified skills:");
  if (/Kept verified skills visible:/i.test(clean)) return clean.replace("Kept verified skills visible:", "Kept verified skills:");
  if (/Auto-refined from the job description using verified overlap:/i.test(clean)) {
    return clean.replace("Auto-refined from the job description using verified overlap:", "Auto-refined from JD:");
  }
  if (/Auto-refined from the job description/i.test(clean)) return "Auto-refined from JD without unsupported skills";
  if (/Applied your refinement comments/i.test(clean)) return clean.replace("Applied your refinement comments where they matched verified skills:", "Applied your notes:");
  if (/Preserved the Word Projects section exactly/i.test(clean)) return "Preserved Word Projects exactly";
  return clean;
}

function compactResumeChanges(changes?: string[] | null) {
  const seen = new Set<string>();
  return (changes || [])
    .map(compactResumeChange)
    .filter((change) => {
      const key = change.toLowerCase();
      if (!change || seen.has(key)) return false;
      seen.add(key);
      return true;
    });
}

function parseGithubProjectEvidence(text: string) {
  return text
    .split(/\n+/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const url = line.match(/https?:\/\/[^\s)]+/i)?.[0] || "";
      let afterUrl = url ? line.replace(url, "").trim() : line;
      while (afterUrl && "-:|,".includes(afterUrl[0])) {
        afterUrl = afterUrl.slice(1).trim();
      }
      const rawName = url ? url.replace(/\/$/, "").split("/").pop() || afterUrl : afterUrl.split(" - ")[0].split(":")[0].split("|")[0];
      const name = rawName.replace(/[-_]/g, " ").trim() || "GitHub Project";
      const skillsMatch = line.match(/\[(.*?)\]|\((.*?)\)/);
      const skills = (skillsMatch?.[1] || skillsMatch?.[2] || "")
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean);
      return {
        name,
        url: url || null,
        summary: afterUrl || line,
        skills,
      };
    });
}

function PdfPreviewGrid({ preview }: { preview: ResumePreview }) {
  const baseUrl = previewUrl(preview.pdf_preview?.base_pdf_url);
  const tailoredUrl = previewUrl(preview.pdf_preview?.tailored_pdf_url);
  const mode = preview.pdf_preview?.pdf_generation ?? preview.version.pdf_generation;
  const usesUploadedPdf = mode === "base_pdf_fallback";
  const wordFallback = preview.version.source_format === "docx_template" && mode === "styled_pdf_fallback";
  const baseLabel = preview.base_source_type === "uploaded_word_template" ? "Original Word Resume" : "Original Uploaded PDF";
  const docxUrl = previewUrl(preview.version.download_urls?.docx);
  return (
    <div className="mt-4 grid gap-3">
      <div className={`rounded border p-3 text-xs leading-5 ${resumePdfStatusClass(mode)}`}>
        <div className="font-semibold">{wordFallback ? "Edited Word resume ready" : resumePdfStatusLabel(mode)}</div>
        <div className="mt-1">
          {wordFallback
            ? "The Word document is the layout-preserving resume. The PDF fallback is not shown here because it does not preserve your Word format."
            : resumePdfStatusNote(mode, preview.pdf_preview?.note)}
        </div>
      </div>
      <div className="grid gap-3 xl:grid-cols-2">
        <div className="grid gap-2">
          <div className="text-xs font-semibold uppercase text-slate-500">{baseLabel}</div>
          {baseUrl ? (
            <iframe className="h-[62vh] w-full rounded border border-line bg-white" src={baseUrl} title="Uploaded resume PDF preview" />
          ) : (
            <div className="grid h-[62vh] place-items-center rounded border border-line bg-white p-6 text-center text-sm text-slate-500">
              {preview.base_source_type === "uploaded_word_template"
                ? "The Word resume is the editable source. PDF preview is available after DOCX conversion or ATS PDF fallback."
                : "No uploaded base PDF is available. Upload the original resume PDF if you want side-by-side visual comparison."}
            </div>
          )}
        </div>
        <div className="grid gap-2">
          <div className="flex items-center justify-between gap-2">
            <div className="text-xs font-semibold uppercase text-slate-500">
              {usesUploadedPdf ? "Tailored LaTeX Preview" : "Tailored PDF"}
            </div>
            {usesUploadedPdf && <span className="rounded bg-amber-100 px-2 py-0.5 text-[11px] font-semibold text-amber-800">Compile LaTeX to render PDF</span>}
          </div>
          {wordFallback ? (
            <div className="grid h-[62vh] gap-3 overflow-auto rounded border border-line bg-white p-4 text-sm leading-6 text-slate-700">
              <div>
                <div className="font-semibold text-ink">Use the tailored DOCX for the preserved layout.</div>
                <div className="mt-1 text-xs text-slate-500">
                  Microsoft Word/LibreOffice conversion is required to create a matching PDF. Until then, SeekApply will upload the edited DOCX for apply flows when the portal accepts it.
                </div>
              </div>
              {docxUrl && (
                <a href={docxUrl} className="inline-flex w-fit items-center gap-2 rounded bg-moss px-3 py-2 text-xs font-semibold text-white">
                  <Download size={13} /> Download Tailored DOCX
                </a>
              )}
              <div className="rounded border border-line bg-field p-3 whitespace-pre-wrap text-xs leading-5">
                {preview.tailored_preview || "Tailored Word resume text is not ready yet. Run Auto Build Resume first."}
              </div>
            </div>
          ) : usesUploadedPdf ? (
            <div className="h-[62vh] overflow-auto rounded border border-line bg-white p-4 font-mono text-xs leading-5 text-slate-800">
              {preview.tailored_preview || "Tailored LaTeX is not ready yet. Run Auto Refine first."}
            </div>
          ) : tailoredUrl ? (
            <iframe className="h-[62vh] w-full rounded border border-line bg-white" src={tailoredUrl} title="Tailored resume PDF preview" />
          ) : (
            <div className="grid h-[62vh] place-items-center rounded border border-line bg-white p-6 text-center text-sm text-slate-500">
              Tailored PDF is not ready yet. Run Resume Decision or Auto Refine first.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function scoreValue(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function experienceFitClass(status?: string | null) {
  if (status === "meets") return "border-emerald-200 bg-emerald-50 text-emerald-800";
  if (status === "stretch") return "border-amber-200 bg-amber-50 text-amber-800";
  if (status === "below") return "border-red-200 bg-red-50 text-red-800";
  return "border-sky-200 bg-sky-50 text-sky-800";
}

function experienceFitShort(job: { experience_fit?: { status?: string; label?: string; message?: string; min_years?: number | null } | null; experience_required?: string | null }) {
  const fit = job.experience_fit;
  if (fit?.status === "meets") return fit.label || "Experience meets JD";
  if (fit?.status === "stretch") return "Stretch: " + (fit.label || "experience requirement");
  if (fit?.status === "below") return "Below minimum: " + (fit.label || "experience requirement");
  return job.experience_required || fit?.message || "No minimum mentioned; you can apply";
}

function ResumeDifferenceView({ preview }: { preview: ResumePreview }) {
  const report = preview.metadata.score_report || {};
  const baseScore = scoreValue(preview.version.base_score) ?? scoreValue(report.base_score) ?? scoreValue(report.original_match_score);
  const tailoredScore = scoreValue(preview.version.tailored_score) ?? scoreValue(report.tailored_score) ?? scoreValue(report.tailored_resume_score);
  const delta = scoreValue(preview.version.score_delta) ?? scoreValue(report.score_delta);
  const changes = compactResumeChanges(preview.metadata.resume_changes);
  const mode = preview.pdf_preview?.pdf_generation ?? preview.version.pdf_generation;

  const diffClass = (line: string) => {
    if (line.startsWith("+++") || line.startsWith("---") || line.startsWith("@@")) return "bg-slate-100 text-slate-600";
    if (line.startsWith("+")) return "bg-emerald-50 text-emerald-800";
    if (line.startsWith("-")) return "bg-red-50 text-red-800";
    return "bg-white text-slate-700";
  };

  return (
    <div className="grid gap-3">
      {mode === "base_pdf_fallback" && (
        <div className="rounded border border-amber-200 bg-amber-50 p-3 text-xs leading-5 text-amber-800">
          The PDF panes look the same because no local LaTeX compiler is installed. These differences show the actual saved LaTeX edits that will render into a tailored PDF once LaTeX compilation is available.
        </div>
      )}
      {preview.version.source_format === "docx_template" && mode === "styled_pdf_fallback" && (
        <div className="rounded border border-sky-200 bg-sky-50 p-3 text-xs leading-5 text-sky-800">
          The tailored DOCX is updated from your Word resume. Install LibreOffice if you want the PDF to preserve the exact Word layout; otherwise SeekApply uses the ATS PDF fallback.
        </div>
      )}
      <div className="grid gap-2 md:grid-cols-3">
        <div className="rounded border border-line bg-field p-3">
          <div className="text-[11px] font-semibold uppercase text-slate-500">Uploaded Resume Score</div>
          <div className="mt-1 text-lg font-semibold text-ink">{baseScore !== null ? `${baseScore}/100` : "Not scored"}</div>
        </div>
        <div className="rounded border border-line bg-field p-3">
          <div className="text-[11px] font-semibold uppercase text-slate-500">Tailored Resume Score</div>
          <div className="mt-1 text-lg font-semibold text-ink">{tailoredScore !== null ? `${tailoredScore}/100` : "Not scored"}</div>
        </div>
        <div className="rounded border border-line bg-field p-3">
          <div className="text-[11px] font-semibold uppercase text-slate-500">Score Change</div>
          <div className={`mt-1 text-lg font-semibold ${delta !== null && delta > 0 ? "text-emerald-700" : delta !== null && delta < 0 ? "text-red-700" : "text-ink"}`}>
            {delta !== null ? `${delta >= 0 ? "+" : ""}${delta}` : "Pending"}
          </div>
        </div>
      </div>
      {changes.length > 0 && (
        <div className="rounded border border-line bg-field p-3 text-xs text-slate-700">
          <div className="mb-2 text-[11px] font-semibold uppercase text-slate-500">What changed</div>
          <div className="flex flex-wrap gap-2">
            {changes.map((change) => (
              <span key={change} className="rounded border border-line bg-white px-2 py-1">{change}</span>
            ))}
          </div>
        </div>
      )}
      <div className="overflow-hidden rounded border border-line bg-white">
        <div className="border-b border-line bg-field px-3 py-2 text-xs font-semibold uppercase text-slate-500">
          Detailed {preview.tailored_source_type === "docx" ? "Word" : "LaTeX"} Difference
        </div>
        {preview.diff.length ? (
          <div className="max-h-[52vh] overflow-auto font-mono text-xs leading-5">
            {preview.diff.map((line, index) => (
              <div key={`${index}-${line}`} className={`px-3 ${diffClass(line)}`}>{line || " "}</div>
            ))}
          </div>
        ) : (
          <div className="p-4 text-sm text-slate-500">No textual difference was detected for this resume version.</div>
        )}
      </div>
    </div>
  );
}

function MatchReview({ onNotice, onRefresh }: { onNotice: (message: string) => void; onRefresh: () => Promise<void> }) {
  const [jobs, setJobs] = useState<JobRow[]>([]);
  const [busy, setBusy] = useState<Record<number, boolean>>({});
  const [scoreAllBusy, setScoreAllBusy] = useState(false);
  const [sortMode, setSortMode] = useState<"recent" | "score_desc">("recent");
  const [results, setResults] = useState<Record<number, { lines: string[]; versionId?: number; aiGenerated?: boolean }>>({});
  const [labs, setLabs] = useState<Record<number, ResumeLab>>({});
  const [selectedVersions, setSelectedVersions] = useState<Record<number, number>>({});
  const [refineNotes, setRefineNotes] = useState<Record<number, string>>({});
  const [repoEvidence, setRepoEvidence] = useState<Record<number, string>>({});
  const [preview, setPreview] = useState<ResumePreview | null>(null);
  const [previewMode, setPreviewMode] = useState<"pdf" | "diff" | "before" | "after">("pdf");
  const [answerTask, setAnswerTask] = useState<ApplyQueueTask | null>(null);
  const [applyIntervention, setApplyIntervention] = useState<ApplyRunSnapshot | null>(null);
  const [applyAnswers, setApplyAnswers] = useState<Record<string, string>>({});
  const [debugData, setDebugData] = useState<JobDebug | ApplyTaskDebug | null>(null);
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

  const loadLab = async (jobId: number) => {
    const lab = await api.resumeLab(jobId);
    setLabs((prev) => ({ ...prev, [jobId]: lab }));
    if (lab.selected_resume_version_id) {
      setSelectedVersions((prev) => ({ ...prev, [jobId]: lab.selected_resume_version_id! }));
    }
    return lab;
  };

  const handleScore = async (job: JobRow) => {
    setBusyFor(job.id, true);
    try {
      const score = await api.scoreJob(job.id);
      setJobs((prev) => prev.map((item) => item.id === job.id ? { ...item, match_score: score.match_score } : item));
      setResultFor(job.id, [
        `${score.match_score}/100 — ${score.recommendation}`,
        score.experience_requirement?.message ? `Experience: ${score.experience_requirement.message}` : "",
        ...score.reason,
        ...score.concerns,
      ].filter(Boolean));
      await loadLab(job.id);
      await loadJobs();
      await onRefresh();
    } finally {
      setBusyFor(job.id, false);
    }
  };

  const handleScoreAll = async () => {
    setScoreAllBusy(true);
    let scored = 0;
    try {
      for (const job of jobs) {
        setBusyFor(job.id, true);
        try {
          const score = await api.scoreJob(job.id);
          scored += 1;
          setJobs((prev) => prev.map((item) => item.id === job.id ? { ...item, match_score: score.match_score } : item));
          setResultFor(job.id, [
            `${score.match_score}/100 — ${score.recommendation}`,
            score.experience_requirement?.message ? `Experience: ${score.experience_requirement.message}` : "",
            ...score.reason.slice(0, 4),
            ...score.concerns.slice(0, 3),
          ].filter(Boolean));
        } finally {
          setBusyFor(job.id, false);
        }
      }
      setSortMode("score_desc");
      await loadJobs();
      await onRefresh();
      onNotice(`Scored ${scored} job${scored !== 1 ? "s" : ""} and sorted highest first.`);
    } finally {
      setScoreAllBusy(false);
    }
  };

  const handleDecision = async (job: JobRow) => {
    setBusyFor(job.id, true);
    try {
      const decision = await api.resumeDecision(job.id);
      const actionLabels: Record<string, string> = {
        use_base_resume: "Use uploaded base resume",
        tailored_resume_created: "Tailored resume created",
        blocked: "Blocked by safety settings",
      };
      const lines = [
        `Match score: ${decision.match_score}/100. Queue threshold: ${decision.threshold}/100.`,
        `Resume decision: ${actionLabels[decision.action] ?? decision.action}`,
        decision.original_match_score !== undefined ? `Original resume score: ${decision.original_match_score}/100` : "",
        decision.tailored_resume_score !== undefined
          ? `Tailored resume score: ${decision.tailored_resume_score}/100 (${decision.score_delta && decision.score_delta > 0 ? "+" : ""}${decision.score_delta ?? 0})`
          : "",
        decision.pdf_generation ? `PDF: ${resumePdfStatusLabel(decision.pdf_generation)}` : "",
        decision.message,
        decision.resume_path ? `Base resume: ${decision.resume_path}` : "",
        decision.ai_generated ? "AI-generated tailored resume" : "",
        ...compactResumeChanges(decision.resume_changes).map((change) => `Changed: ${change}`),
        ...(decision.reasons ?? []),
        ...(decision.concerns ?? []),
      ].filter(Boolean);
      setResultFor(job.id, lines, decision.resume_version_id, decision.ai_generated);
      const lab = await loadLab(job.id);
      const versionId = decision.resume_version_id ?? lab.selected_resume_version_id;
      if (versionId) setSelectedVersions((prev) => ({ ...prev, [job.id]: versionId }));
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

  const handleRefine = async (job: JobRow) => {
    const instructions = (refineNotes[job.id] || "").trim();
    const github_repositories = parseGithubProjectEvidence(repoEvidence[job.id] || "");
    setBusyFor(job.id, true);
    try {
      const result = await api.refineResume(job.id, {
        instructions: instructions || null,
        github_repositories,
        force_new_version: true,
      });
      setLabs((prev) => ({ ...prev, [job.id]: result.lab }));
      setSelectedVersions((prev) => ({ ...prev, [job.id]: result.resume_version_id }));
      setResultFor(job.id, [
        result.message,
        result.auto_refined ? "Refinement source: job description and verified resume evidence." : "Refinement source: your notes and verified resume evidence.",
        result.github_project_evidence_added ? `Added GitHub project evidence: ${result.github_project_evidence_added}` : "",
        `Original resume score: ${result.comparison.original_match_score}/100`,
        `Refined resume score: ${result.comparison.tailored_resume_score}/100 (${result.comparison.score_delta > 0 ? "+" : ""}${result.comparison.score_delta})`,
        result.comparison.pdf_generation ? `PDF: ${resumePdfStatusLabel(result.comparison.pdf_generation)}` : "",
        ...compactResumeChanges(result.comparison.resume_changes).map((change) => `Changed: ${change}`),
      ], result.resume_version_id);
      setRefineNotes((prev) => ({ ...prev, [job.id]: "" }));
      setRepoEvidence((prev) => ({ ...prev, [job.id]: "" }));
      await loadJobs();
      await onRefresh();
    } finally {
      setBusyFor(job.id, false);
    }
  };

  const handlePreview = async (versionId: number) => {
    const data = await api.resumePreview(versionId);
    setPreview(data);
    setPreviewMode(data.pdf_preview?.pdf_generation === "base_pdf_fallback" ? "diff" : "pdf");
  };

  const handleDebug = async (jobId: number) => {
    setDebugData(await api.jobDebug(jobId));
  };

  const handleQueue = async (job: JobRow) => {
    setBusyFor(job.id, true);
    try {
      const selectedResumeId = selectedVersions[job.id] ?? results[job.id]?.versionId ?? job.resume_version_id;
      const result = await api.buildApplyQueue({
        job_ids: [job.id],
        max_items: 1,
        force: true,
        resume_version_id: selectedResumeId ?? undefined,
      });
      const task = result.tasks[0];
      const startResult = task ? await api.startApplyTask(task.id) : null;
      if (startResult?.missing_questions.length) {
        setAnswerTask(startResult.task);
        setApplyAnswers(Object.fromEntries(startResult.missing_questions.map((question) => [question, ""])));
      } else if (startResult && applyNeedsIntervention(startResult.status)) {
        setApplyIntervention({
          task: startResult.task,
          status: startResult.status,
          message: startResult.message,
          action_required: startResult.action_required,
          missing_questions: startResult.missing_questions,
          fill_report: startResult.fill_report,
          steps: startResult.steps,
          errors: startResult.errors,
        });
      }
      setResultFor(job.id, [
        result.message,
        task ? `Apply task ${task.id}: ${task.status}` : "Job was not eligible for the supervised LinkedIn queue.",
        task?.message || "",
        startResult ? `Auto apply status: ${startResult.status}` : "",
        startResult?.message || "",
        startResult?.action_required ? `Action required: ${startResult.action_required}` : "",
        result.skipped.length ? `Skipped: ${JSON.stringify(result.skipped)}` : "",
        task?.resume?.id ? `Queued resume version: ${task.resume.id}` : "",
        task?.resume?.pdf_generation ? `PDF: ${resumePdfStatusLabel(task.resume.pdf_generation)}` : "",
      ].filter(Boolean));
      onNotice(startResult?.action_required || startResult?.message || (task ? `Started supervised apply for ${job.title}.` : "Job was not queued. Check source details."));
      await loadJobs();
      await onRefresh();
    } finally {
      setBusyFor(job.id, false);
    }
  };

  const saveApplyAnswers = async () => {
    if (!answerTask) return;
    const items = answerTask.missing_questions
      .map((question) => ({
        question_text: question,
        answer_text: applyAnswers[question] || "",
        source: "match_resume_auto_apply_missing_question",
        sensitive: true,
        approved: true,
      }))
      .filter((item) => item.answer_text.trim());
    if (!items.length) {
      onNotice("Add at least one answer before saving.");
      return;
    }
    await api.bulkAnswers({ answers: items });
    const result = await api.resumeApplyTask(answerTask.id);
    setAnswerTask(null);
    setApplyAnswers({});
    if (applyNeedsIntervention(result.status)) {
      setApplyIntervention({
        task: result.task,
        status: result.status,
        message: result.message,
        action_required: result.action_required,
        missing_questions: result.missing_questions,
        fill_report: result.fill_report,
        steps: result.steps,
        errors: result.errors,
      });
    }
    onNotice(result.action_required || result.message);
    await loadJobs();
    await onRefresh();
  };

  const resumeIntervention = async () => {
    if (!applyIntervention) return;
    const result = await api.resumeApplyTask(applyIntervention.task.id);
    if (result.missing_questions.length) {
      setAnswerTask(result.task);
      setApplyAnswers(Object.fromEntries(result.missing_questions.map((question) => [question, ""])));
      setApplyIntervention(null);
    } else if (applyNeedsIntervention(result.status)) {
      setApplyIntervention({
        task: result.task,
        status: result.status,
        message: result.message,
        action_required: result.action_required,
        missing_questions: result.missing_questions,
        fill_report: result.fill_report,
        steps: result.steps,
        errors: result.errors,
      });
    } else {
      setApplyIntervention(null);
    }
    onNotice(result.action_required || result.message);
    await loadJobs();
    await onRefresh();
  };

  const markInterventionSubmitted = async () => {
    if (!applyIntervention) return;
    const result = await api.markApplySubmitted(applyIntervention.task.id);
    setApplyIntervention(null);
    onNotice(`Application ${result.application_id} marked as ${result.status}.`);
    await loadJobs();
    await onRefresh();
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

  const effectiveScore = (job: JobRow) => {
    const lab = labs[job.id];
    const selectedVersionId = selectedVersions[job.id] ?? lab?.selected_resume_version_id ?? results[job.id]?.versionId ?? job.resume_version_id ?? null;
    const selectedVersion = lab?.versions.find((version) => version.id === selectedVersionId) ?? lab?.versions[0];
    return selectedVersion?.tailored_score ?? job.match_score ?? -1;
  };

  const sortedJobs = useMemo(() => {
    const list = [...jobs];
    if (sortMode === "score_desc") {
      list.sort((a, b) => {
        const scoreDiff = effectiveScore(b) - effectiveScore(a);
        if (scoreDiff !== 0) return scoreDiff;
        return a.title.localeCompare(b.title);
      });
    }
    return list;
  }, [jobs, labs, selectedVersions, results, sortMode]);

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
            disabled={scoreAllBusy}
            onClick={() => handleScoreAll().catch((err) => onNotice(err.message))}
            className="flex items-center gap-1 rounded border border-line bg-white px-3 py-1.5 text-sm font-medium text-slate-600 hover:bg-field disabled:opacity-50"
          >
            <Gauge size={14} /> {scoreAllBusy ? "Scoring..." : "Score All"}
          </button>
          <button
            onClick={() => setSortMode((current) => current === "score_desc" ? "recent" : "score_desc")}
            className={`flex items-center gap-1 rounded border px-3 py-1.5 text-sm font-medium ${sortMode === "score_desc" ? "border-cobalt bg-cobalt text-white" : "border-line bg-white text-slate-600 hover:bg-field"}`}
          >
            <ArrowDownWideNarrow size={14} /> {sortMode === "score_desc" ? "Highest First" : "Sort by Score"}
          </button>
          <button
            onClick={() => loadJobs().catch((err) => onNotice(err.message))}
            className="flex items-center gap-1 text-sm text-slate-500 hover:text-cobalt"
          >
            <RefreshCcw size={14} /> Refresh
          </button>
        </div>
      </div>

      {sortedJobs.map((job) => {
        const res = results[job.id];
        const isBusy = busy[job.id] ?? false;
        const lab = labs[job.id];
        const selectedVersionId = selectedVersions[job.id] ?? lab?.selected_resume_version_id ?? res?.versionId ?? job.resume_version_id ?? null;
        const selectedVersion = lab?.versions.find((version) => version.id === selectedVersionId) ?? lab?.versions[0];
        const selectedScore = selectedVersion?.tailored_score ?? null;
        const scoreDelta = selectedVersion?.score_delta ?? null;

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
                  <span title={job.experience_fit?.message || job.experience_required || ""} className={`max-w-full truncate rounded border px-2 py-0.5 font-medium ${experienceFitClass(job.experience_fit?.status)}`}>
                    {experienceFitShort(job)}
                  </span>
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
                {job.description && (
                  <details className="mt-3 max-w-4xl rounded border border-line bg-white px-3 py-2 text-xs text-slate-700">
                    <summary className="cursor-pointer font-semibold text-ink">Job description</summary>
                    <div className="mt-2 max-h-56 overflow-auto whitespace-pre-wrap leading-5">
                      {job.description}
                    </div>
                  </details>
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
                onClick={() => loadLab(job.id).catch((e) => onNotice(e.message))}
                className="flex items-center gap-1.5 rounded border border-line bg-white px-3 py-1.5 text-xs font-medium hover:bg-field disabled:opacity-50"
              >
                <History size={13} /> Compare
              </button>
              <button
                disabled={isBusy}
                onClick={() => handleRefine(job).catch((e) => onNotice(e.message))}
                className="flex items-center gap-1.5 rounded border border-line bg-white px-3 py-1.5 text-xs font-medium hover:bg-field disabled:opacity-50"
                title="Automatically refine from this job description using only verified resume evidence."
              >
                <Sparkles size={13} /> Auto Build Resume
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
              <button
                disabled={isBusy}
                onClick={() => handleQueue(job).catch((e) => onNotice(e.message))}
                className="flex items-center gap-1.5 rounded border border-line bg-white px-3 py-1.5 text-xs font-medium hover:bg-field disabled:opacity-50"
              >
                <Send size={13} /> Queue + Auto Apply
              </button>
              <button
                disabled={isBusy}
                onClick={() => handleDebug(job.id).catch((e) => onNotice(e.message))}
                className="flex items-center gap-1.5 rounded border border-line bg-white px-3 py-1.5 text-xs font-medium hover:bg-field disabled:opacity-50"
              >
                <Shield size={13} /> Debug
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
            {(selectedVersionId || res?.versionId || job.resume_version_id) && (
              <div className="mt-3 flex flex-wrap items-center gap-2">
                {res?.aiGenerated && (
                  <span className="flex items-center gap-1 text-xs font-medium text-cobalt">
                    <Sparkles size={12} /> AI-tailored
                  </span>
                )}
                <button
                  onClick={() => api.downloadResumePdf(selectedVersionId ?? res?.versionId ?? job.resume_version_id!).catch((e) => onNotice(e.message))}
                  className="flex items-center gap-1.5 rounded bg-moss px-3 py-1.5 text-xs font-medium text-white hover:opacity-90"
                >
                  <Download size={12} /> Download PDF
                </button>
                <button
                  onClick={() => api.downloadResumeDocx(selectedVersionId ?? res?.versionId ?? job.resume_version_id!).catch((e) => onNotice(e.message))}
                  className="flex items-center gap-1.5 rounded border border-moss px-3 py-1.5 text-xs font-medium text-moss hover:bg-field"
                >
                  <Download size={12} /> Download DOCX
                </button>
                {selectedVersion?.source_format !== "docx_template" && (
                  <button
                    onClick={() => api.downloadResumeTex(selectedVersionId ?? res?.versionId ?? job.resume_version_id!).catch((e) => onNotice(e.message))}
                    className="flex items-center gap-1.5 rounded border border-indigo-400 px-3 py-1.5 text-xs font-medium text-indigo-600 hover:bg-indigo-50"
                  >
                    <Download size={12} /> Download LaTeX
                  </button>
                )}
                <button
                  onClick={() => handlePreview(selectedVersionId ?? res?.versionId ?? job.resume_version_id!).catch((e) => onNotice(e.message))}
                  className="flex items-center gap-1.5 rounded border border-line bg-white px-3 py-1.5 text-xs font-medium hover:bg-field"
                >
                  <FileText size={12} /> Preview PDF
                </button>
              </div>
            )}

            {lab && (
              <div className="mt-3 grid gap-3 rounded border border-line bg-white p-3">
                <div className="grid gap-2 md:grid-cols-3">
                  <div className="rounded border border-line bg-field p-3">
                    <div className="text-[11px] font-semibold uppercase text-slate-500">Uploaded Resume</div>
                    <div className="mt-1 text-lg font-semibold text-ink">{lab.base.score}/100</div>
                    <div className="text-xs text-slate-500">{lab.base.recommendation}</div>
                  </div>
                  <div className="rounded border border-line bg-field p-3">
                    <div className="text-[11px] font-semibold uppercase text-slate-500">Selected Version</div>
                    <div className="mt-1 text-lg font-semibold text-ink">
                      {selectedScore !== null ? `${selectedScore}/100` : "Not generated"}
                    </div>
                    <div className="text-xs text-slate-500">
                      {scoreDelta !== null ? `${scoreDelta >= 0 ? "+" : ""}${scoreDelta} after tailoring` : "Run Resume Decision or Refine"}
                    </div>
                  </div>
                  <div className="rounded border border-line bg-field p-3">
                    <div className="text-[11px] font-semibold uppercase text-slate-500">Queue Threshold</div>
                    <div className="mt-1 text-lg font-semibold text-ink">{lab.threshold}/100</div>
                    <div className="text-xs text-slate-500">{lab.pdf_note}</div>
                  </div>
                </div>

                {selectedVersion && (
                  <div className="rounded border border-line bg-[#fffdf7] p-3">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div>
                        <div className="text-xs font-semibold uppercase text-slate-500">Resume Ready For This Job</div>
                        <div className="mt-1 text-sm font-semibold text-ink">#{selectedVersion.id} · {selectedVersion.role}</div>
                      </div>
                      <span className={`rounded border px-2 py-1 text-xs font-semibold ${resumePdfStatusClass(selectedVersion.pdf_generation)}`}>
                        {resumePdfStatusLabel(selectedVersion.pdf_generation)}
                      </span>
                    </div>
                    <div className="mt-2 text-xs leading-5 text-slate-600">
                      {resumePdfStatusNote(selectedVersion.pdf_generation, selectedVersion.pdf_note)}
                    </div>
                    {selectedVersion.resume_changes && selectedVersion.resume_changes.length > 0 && (
                      <div className="mt-2 grid gap-1">
                        <div className="text-[11px] font-semibold uppercase text-slate-500">What changed</div>
                        {compactResumeChanges(selectedVersion.resume_changes).slice(0, 5).map((change) => (
                          <div key={change} className="text-xs text-slate-700">Changed: {change}</div>
                        ))}
                      </div>
                    )}
                  </div>
                )}

                {lab.versions.length > 0 && (
                  <div className="grid gap-2">
                    <div className="text-xs font-semibold uppercase text-slate-500">Resume History / Reuse</div>
                    {lab.versions.map((version) => {
                      const isSelected = selectedVersionId === version.id;
                      return (
                        <div key={version.id} className={`flex flex-wrap items-center justify-between gap-2 rounded border p-2 text-xs ${isSelected ? "border-cobalt bg-blue-50" : "border-line bg-[#fffdf7]"}`}>
                          <div>
                            <div className="font-semibold text-ink">
                              #{version.id} · {version.tailored_score ?? "?"}/100
                              {version.score_delta !== null && version.score_delta !== undefined ? ` (${version.score_delta >= 0 ? "+" : ""}${version.score_delta})` : ""}
                            </div>
                            <div className="text-slate-500">
                              {version.reusable_for_current_job ? "Reusable from another similar job" : "Created for this job"} · {version.skills_emphasized.slice(0, 6).join(", ") || "verified skills"}
                            </div>
                          </div>
                          <button
                            type="button"
                            onClick={() => setSelectedVersions((prev) => ({ ...prev, [job.id]: version.id }))}
                            className={`rounded px-2 py-1 font-semibold ${isSelected ? "bg-cobalt text-white" : "border border-line bg-white text-ink hover:bg-field"}`}
                          >
                            {isSelected ? "Selected" : "Select"}
                          </button>
                          <button
                            type="button"
                            onClick={() => handlePreview(version.id).catch((e) => onNotice(e.message))}
                            className="rounded border border-line bg-white px-2 py-1 font-semibold text-ink hover:bg-field"
                          >
                            Preview
                          </button>
                        </div>
                      );
                    })}
                  </div>
                )}

                <div className="grid gap-2">
                  <Field label="Manual tweak request (optional)">
                    <textarea
                      className={textareaClass}
                      placeholder="Leave blank for automatic JD-based refinement. Add notes only when you want a specific truthful emphasis."
                      value={refineNotes[job.id] || ""}
                      onChange={(event) => setRefineNotes((prev) => ({ ...prev, [job.id]: event.target.value }))}
                    />
                  </Field>
                  <Field label="GitHub project evidence (optional)">
                    <textarea
                      className={textareaClass}
                      placeholder="Paste one repo per line. Example: https://github.com/you/rag-agent - RAG agent with FastAPI, LangGraph, Docker [Python, FastAPI, LangGraph, Docker]"
                      value={repoEvidence[job.id] || ""}
                      onChange={(event) => setRepoEvidence((prev) => ({ ...prev, [job.id]: event.target.value }))}
                    />
                  </Field>
                  <div className="flex flex-wrap gap-2">
                    <Button disabled={isBusy} onClick={() => handleRefine(job).catch((e) => onNotice(e.message))}>
                      <Sparkles size={15} /> Auto Build Best Resume
                    </Button>
                    <Button variant="secondary" disabled={isBusy} onClick={() => handleQueue(job).catch((e) => onNotice(e.message))}>
                      <Send size={15} /> Queue + Auto Apply
                    </Button>
                  </div>
                </div>
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
      {preview && (
        <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-4">
          <div className="flex max-h-[92vh] w-full max-w-7xl flex-col rounded-[18px] border border-line bg-[#fffdf7] p-5 shadow-float">
            <div className="sticky top-0 z-10 flex flex-wrap items-start justify-between gap-3 border-b border-line bg-[#fffdf7] pb-3">
              <div>
                <h3 className="flex items-center gap-2 text-base font-semibold">
                  <Eye size={17} className="text-cobalt" /> Resume PDF Preview
                </h3>
                <div className="mt-1 flex flex-wrap items-center gap-2 text-sm text-slate-500">
                  <span>Version #{preview.version.id}</span>
                  <span>·</span>
                  <span>{preview.version.role}</span>
                  <span>·</span>
                  <span>{preview.version.tailored_score ?? "?"}/100</span>
                  {preview.version.score_delta !== null && preview.version.score_delta !== undefined && (
                    <span className={preview.version.score_delta >= 0 ? "text-emerald-700" : "text-red-700"}>
                      ({preview.version.score_delta >= 0 ? "+" : ""}{preview.version.score_delta})
                    </span>
                  )}
                </div>
              </div>
              <button className="text-sm text-slate-500 hover:text-ink" onClick={() => setPreview(null)}>Close</button>
            </div>
            <div className="mt-4 flex flex-wrap gap-2">
              {(["pdf", "diff", "before", "after"] as const).map((mode) => (
                <button
                  key={mode}
                  onClick={() => setPreviewMode(mode)}
                  className={`rounded border px-3 py-1.5 text-xs font-semibold ${previewMode === mode ? "border-cobalt bg-cobalt text-white" : "border-line bg-white text-ink hover:bg-field"}`}
                >
                  {mode === "pdf" ? "PDF" : mode === "diff" ? "Difference" : mode === "before" ? "Before Text" : "After Text"}
                </button>
              ))}
            </div>
            <div className="mt-4 grid gap-2 overflow-auto">
              {compactResumeChanges(preview.metadata.resume_changes).length > 0 && (
                <div className="rounded border border-line bg-field p-3 text-xs text-slate-700">
                  <div className="mb-2 text-[11px] font-semibold uppercase text-slate-500">What changed</div>
                  <div className="flex flex-wrap gap-2">
                    {compactResumeChanges(preview.metadata.resume_changes).map((change) => (
                      <span key={change} className="rounded border border-line bg-white px-2 py-1">{change}</span>
                    ))}
                  </div>
                </div>
              )}
              {previewMode === "pdf" ? (
                <PdfPreviewGrid preview={preview} />
              ) : previewMode === "diff" ? (
                <ResumeDifferenceView preview={preview} />
              ) : (
                <pre className="max-h-[56vh] overflow-auto rounded border border-line bg-white p-3 text-xs leading-5 text-slate-800">
                  {previewMode === "before" ? preview.base_preview : preview.tailored_preview}
                </pre>
              )}
              {preview.diff_truncated && <div className="text-xs text-slate-500">Diff preview truncated. Download LaTeX for the full file.</div>}
            </div>
          </div>
        </div>
      )}
      {debugData && (
        <DebugModal title="Job Debug" data={debugData} onClose={() => setDebugData(null)} />
      )}
      {applyIntervention && (
        <ApplyInterventionModal
          snapshot={applyIntervention}
          busy={busy[applyIntervention.task.id]}
          onClose={() => setApplyIntervention(null)}
          onAnswer={() => {
            setAnswerTask(applyIntervention.task);
            setApplyAnswers(Object.fromEntries(applyIntervention.missing_questions.map((question) => [question, ""])));
            setApplyIntervention(null);
          }}
          onResume={() => resumeIntervention().catch((error) => onNotice(error.message))}
          onMarkSubmitted={() => markInterventionSubmitted().catch((error) => onNotice(error.message))}
          onDebug={() => api.applyTaskDebug(applyIntervention.task.id).then(setDebugData).catch((error) => onNotice(error.message))}
        />
      )}
      {answerTask && (
        <div className="fixed inset-0 z-50 grid place-items-center bg-black/40 p-4">
          <div className="w-full max-w-2xl rounded-[18px] border border-line bg-[#fffdf7] p-5 shadow-float">
            <div className="mb-4 flex items-center justify-between gap-3">
              <div>
                <div className="inline-flex items-center gap-2 rounded bg-amber-100 px-2 py-1 text-xs font-semibold text-amber-800">
                  <BellRing size={14} /> Agent paused
                </div>
                <h3 className="mt-3 text-base font-semibold">Answer New Application Questions</h3>
                <div className="mt-1 text-sm text-slate-500">Answers are saved to the Knowledge Base and reused on later applications.</div>
              </div>
              <button className="text-sm text-slate-500 hover:text-ink" onClick={() => setAnswerTask(null)}>Close</button>
            </div>
            <div className="grid max-h-[60vh] gap-4 overflow-auto pr-1">
              {answerTask.missing_questions.map((question) => (
                <Field key={question} label={question}>
                  <textarea
                    className={textareaClass}
                    value={applyAnswers[question] || ""}
                    onChange={(event) => setApplyAnswers((prev) => ({ ...prev, [question]: event.target.value }))}
                  />
                </Field>
              ))}
            </div>
            <div className="mt-5 flex flex-wrap justify-end gap-2">
              <Button variant="secondary" onClick={() => setAnswerTask(null)}>Cancel</Button>
              <Button onClick={() => saveApplyAnswers().catch((error) => onNotice(error.message))}>
                <Save size={15} /> Save to KB &amp; Resume Apply
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function ApplyQueue({ onNotice, onRefresh }: { onNotice: (message: string) => void; onRefresh: () => Promise<void> }) {
  const [tasks, setTasks] = useState<ApplyQueueTask[]>([]);
  const [busy, setBusy] = useState<Record<number, boolean>>({});
  const [queueBusy, setQueueBusy] = useState(false);
  const [browserResetBusy, setBrowserResetBusy] = useState(false);
  const [answerTask, setAnswerTask] = useState<ApplyQueueTask | null>(null);
  const [intervention, setIntervention] = useState<ApplyRunSnapshot | null>(null);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [debugData, setDebugData] = useState<ApplyTaskDebug | null>(null);

  const load = async () => {
    const data = await api.applyQueue();
    setTasks(data.tasks);
  };

  useEffect(() => {
    load().catch((error) => onNotice(error.message));
  }, []);

  const setBusyFor = (id: number, value: boolean) => setBusy((prev) => ({ ...prev, [id]: value }));

  const buildQueue = async () => {
    const result = await api.buildApplyQueue({});
    setTasks(result.tasks);
    onNotice(`${result.message} Threshold: ${result.threshold}. Skipped: ${result.skipped.length}.`);
    await onRefresh();
  };

  const resetApplyBrowser = async () => {
    setBrowserResetBusy(true);
    try {
      const result = await api.resetApplyBrowser();
      onNotice(result.message);
      await load();
    } finally {
      setBrowserResetBusy(false);
    }
  };

  const runQueue = async () => {
    setQueueBusy(true);
    try {
      const latest = await api.applyQueue();
      const runnable = latest.tasks.filter((task) =>
        ["queued", "needs_login", "needs_user_action", "failed"].includes(task.status)
      );
      if (!runnable.length) {
        onNotice("No queued tasks are ready to auto-run. Answer missing questions or build the queue first.");
        return;
      }
      for (const task of runnable) {
        setBusyFor(task.id, true);
        try {
          const result = ["needs_login", "needs_user_action", "failed"].includes(task.status)
            ? await api.resumeApplyTask(task.id)
            : await api.startApplyTask(task.id);
          if (result.missing_questions.length) {
            setAnswerTask(result.task);
            setAnswers(Object.fromEntries(result.missing_questions.map((question) => [question, ""])));
          } else if (applyNeedsIntervention(result.status)) {
            setIntervention({
              task: result.task,
              status: result.status,
              message: result.message,
              action_required: result.action_required,
              missing_questions: result.missing_questions,
              fill_report: result.fill_report,
              steps: result.steps,
              errors: result.errors,
            });
          }
          if (["ready_for_submit", "needs_login", "needs_answers", "needs_user_action", "failed"].includes(result.status)) {
            onNotice(result.action_required || result.message);
            break;
          }
        } finally {
          setBusyFor(task.id, false);
          await load();
          await onRefresh();
        }
      }
    } finally {
      setQueueBusy(false);
    }
  };

  const runTask = async (task: ApplyQueueTask, mode: "start" | "resume") => {
    setBusyFor(task.id, true);
    try {
      const result = mode === "start" ? await api.startApplyTask(task.id) : await api.resumeApplyTask(task.id);
      onNotice(result.action_required || result.message);
      if (result.missing_questions.length) {
        setAnswerTask(result.task);
        setAnswers(Object.fromEntries(result.missing_questions.map((question) => [question, ""])));
      } else if (applyNeedsIntervention(result.status)) {
        setIntervention({
          task: result.task,
          status: result.status,
          message: result.message,
          action_required: result.action_required,
          missing_questions: result.missing_questions,
          fill_report: result.fill_report,
          steps: result.steps,
          errors: result.errors,
        });
      }
      await load();
      await onRefresh();
    } finally {
      setBusyFor(task.id, false);
    }
  };

  const markSubmitted = async (task: ApplyQueueTask) => {
    setBusyFor(task.id, true);
    try {
      const result = await api.markApplySubmitted(task.id);
      onNotice(`Application ${result.application_id} marked as ${result.status}.`);
      await load();
      await onRefresh();
    } finally {
      setBusyFor(task.id, false);
    }
  };

  const debugTask = async (task: ApplyQueueTask) => {
    setDebugData(await api.applyTaskDebug(task.id));
  };

  const saveMissingAnswers = async () => {
    if (!answerTask) return;
    const items = answerTask.missing_questions
      .map((question) => ({
        question_text: question,
        answer_text: answers[question] || "",
        source: "supervised_apply_missing_question",
        sensitive: true,
        approved: true,
      }))
      .filter((item) => item.answer_text.trim());
    if (!items.length) {
      onNotice("Add at least one answer before saving.");
      return;
    }
    await api.bulkAnswers({ answers: items });
    onNotice(`Saved ${items.length} answer(s). Resuming supervised apply.`);
    const task = answerTask;
    setAnswerTask(null);
    setAnswers({});
    await runTask(task, "resume");
  };

  const resumeIntervention = async () => {
    if (!intervention) return;
    const task = intervention.task;
    setIntervention(null);
    await runTask(task, "resume");
  };

  const markInterventionSubmitted = async () => {
    if (!intervention) return;
    const task = intervention.task;
    setIntervention(null);
    await markSubmitted(task);
  };

  const statusClass = (status: string) => {
    if (status === "ready_for_submit") return "bg-emerald-100 text-emerald-700";
    if (status === "needs_answers" || status === "needs_login" || status === "needs_user_action") return "bg-amber-100 text-amber-700";
    if (status === "failed") return "bg-red-100 text-red-700";
    if (status === "submitted_by_user") return "bg-cobalt text-white";
    return "bg-slate-100 text-slate-600";
  };

  const applyModeLabel = (task: ApplyQueueTask) => {
    const mode = String(task.fill_report?.mode || task.source || "");
    if (mode === "linkedin_easy_apply") return "LinkedIn Easy Apply";
    if (mode === "external_from_linkedin") return "LinkedIn external apply";
    if (mode === "external_site") return "External site";
    return mode.replace(/_/g, " ") || "Apply agent";
  };

  const easyApplyDetection = (task: ApplyQueueTask) => {
    const report = task.fill_report?.easy_apply_detection;
    if (!report || typeof report !== "object") return null;
    return report as { clicked?: boolean; reason?: string; clicked_text?: string; visible_apply_buttons?: string[] };
  };

  const attentionTasks = tasks.filter((task) => applyNeedsIntervention(task.status));

  return (
    <div className="grid gap-4">
      <Panel className="p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-base font-semibold">Supervised Apply Queue</h2>
            <div className="mt-1 text-sm text-slate-500">LinkedIn Easy Apply first, then supervised external-site fallback. SeekApply fills known fields and keeps the browser session visible.</div>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" onClick={() => load().catch((error) => onNotice(error.message))}>
              <RefreshCcw size={15} /> Refresh
            </Button>
            <Button variant="secondary" disabled={browserResetBusy || queueBusy} onClick={() => resetApplyBrowser().catch((error) => onNotice(error.message))}>
              <X size={15} /> {browserResetBusy ? "Resetting..." : "Reset Browser"}
            </Button>
            <Button onClick={() => buildQueue().catch((error) => onNotice(error.message))}>
              <Send size={15} /> Build Queue
            </Button>
            <Button disabled={queueBusy} onClick={() => runQueue().catch((error) => onNotice(error.message))}>
              <Play size={15} /> {queueBusy ? "Running..." : "Run Queue"}
            </Button>
          </div>
        </div>
      </Panel>

      {attentionTasks.length > 0 && (
        <div className="rounded border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2 font-semibold">
              <BellRing size={16} /> {attentionTasks.length} apply agent task{attentionTasks.length !== 1 ? "s" : ""} need attention
            </div>
            <Button variant="secondary" onClick={() => setIntervention(applySnapshotFromTask(attentionTasks[0]))}>
              Open Prompt
            </Button>
          </div>
        </div>
      )}

      {tasks.length ? (
        <div className="grid gap-3">
          {tasks.map((task) => {
            const isBusy = busy[task.id] ?? false;
            const canStart = ["queued"].includes(task.status);
            const canResume = ["needs_login", "needs_answers", "needs_user_action", "failed", "opening_browser"].includes(task.status);
            return (
              <Panel key={task.id} className="p-5">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <div className="font-semibold text-ink">{task.job.title}</div>
                    <div className="mt-1 text-sm text-slate-600">
                      {task.job.company}{task.job.location ? ` · ${task.job.location}` : ""} · score {task.job.match_score ?? "-"}
                    </div>
                    <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
                      <span className={`rounded px-2 py-1 font-semibold ${statusClass(task.status)}`}>{task.status}</span>
                      <span className="text-slate-500">{task.application_status || "No application yet"}</span>
                      <span className="rounded bg-slate-100 px-2 py-1 font-semibold text-slate-600">{applyModeLabel(task)}</span>
                      {task.resume && (
                        <span className="text-slate-500">
                          Resume #{task.resume.id}
                          {task.resume.tailored_score !== null && task.resume.tailored_score !== undefined ? ` · ${task.resume.tailored_score}/100` : ""}
                          {task.resume.pdf_generation ? ` · ${resumePdfStatusLabel(task.resume.pdf_generation)}` : ""}
                        </span>
                      )}
                      <a href={task.job.job_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-cobalt hover:underline">
                        <ExternalLink size={12} /> Job
                      </a>
                      {task.job.apply_url && task.job.apply_url !== task.job.job_url && (
                        <a href={task.job.apply_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-cobalt hover:underline">
                          <ExternalLink size={12} /> Apply Link
                        </a>
                      )}
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <Button disabled={isBusy || !canStart || task.status === "submitted_by_user"} onClick={() => runTask(task, "start").catch((error) => onNotice(error.message))}>
                      <Linkedin size={15} /> {isBusy ? "Working..." : "Start"}
                    </Button>
                    <Button variant="secondary" disabled={isBusy || !canResume} onClick={() => runTask(task, "resume").catch((error) => onNotice(error.message))}>
                      <RefreshCcw size={15} /> Resume Agent
                    </Button>
                    <Button variant="secondary" disabled={isBusy || !["ready_for_submit", "needs_user_action", "failed", "needs_login", "opening_browser"].includes(task.status)} onClick={() => markSubmitted(task).catch((error) => onNotice(error.message))}>
                      <CheckCircle2 size={15} /> Mark Submitted
                    </Button>
                    {applyNeedsIntervention(task.status) && (
                      <Button variant="secondary" disabled={isBusy} onClick={() => setIntervention(applySnapshotFromTask(task))}>
                        <BellRing size={15} /> Agent Prompt
                      </Button>
                    )}
                    <Button variant="secondary" disabled={isBusy} onClick={() => debugTask(task).catch((error) => onNotice(error.message))}>
                      <Shield size={15} /> Debug
                    </Button>
                  </div>
                </div>
                <ApplyAgentProgress task={task} />
                {task.message && <div className="mt-3 rounded border border-line bg-field px-3 py-2 text-sm text-slate-700">{task.message}</div>}
                {easyApplyDetection(task) && !easyApplyDetection(task)?.clicked && (
                  <div className="mt-3 rounded border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
                    LinkedIn Apply detector: {easyApplyDetection(task)?.reason?.replace(/_/g, " ") || "not opened"}.
                    {easyApplyDetection(task)?.visible_apply_buttons?.length
                      ? ` Visible apply actions: ${easyApplyDetection(task)?.visible_apply_buttons?.join(", ")}.`
                      : " No visible LinkedIn Apply action was found on the loaded LinkedIn page."}
                  </div>
                )}
                {typeof task.fill_report?.manual_review_reason === "string" && (
                  <div className="mt-3 rounded border border-line bg-white px-3 py-2 text-sm text-slate-700">
                    Manual review reason: {task.fill_report.manual_review_reason}
                  </div>
                )}
                {task.trace && task.trace.length > 0 && (
                  <details className="mt-3 rounded border border-line bg-white px-3 py-2 text-xs text-slate-700">
                    <summary className="cursor-pointer font-semibold text-ink">Agent trace</summary>
                    <div className="mt-2 grid gap-1">
                      {task.trace.slice(-6).map((step, index) => (
                        <div key={`${step.name}-${index}`} className="flex flex-wrap items-center gap-2">
                          <span className="font-semibold">{step.name}</span>
                          <span className="text-slate-500">{step.status}</span>
                          <span>{step.message}</span>
                        </div>
                      ))}
                    </div>
                  </details>
                )}
                {task.missing_questions.length > 0 && (
                  <div className="mt-3 grid gap-2">
                    <div className="text-xs font-semibold uppercase text-slate-500">Missing questions</div>
                    {task.missing_questions.map((question) => (
                      <div key={question} className="rounded border border-line bg-white px-3 py-2 text-sm text-slate-700">{question}</div>
                    ))}
                    <div>
                      <Button variant="secondary" onClick={() => { setAnswerTask(task); setAnswers(Object.fromEntries(task.missing_questions.map((q) => [q, ""]))); }}>
                        <ListChecks size={15} /> Answer Questions
                      </Button>
                    </div>
                  </div>
                )}
              </Panel>
            );
          })}
        </div>
      ) : (
        <Panel className="p-8 text-center text-sm text-slate-500">
          No apply queue items yet. Build the queue from imported jobs that pass your match threshold.
        </Panel>
      )}

      {intervention && (
        <ApplyInterventionModal
          snapshot={intervention}
          busy={busy[intervention.task.id]}
          onClose={() => setIntervention(null)}
          onAnswer={() => {
            setAnswerTask(intervention.task);
            setAnswers(Object.fromEntries(intervention.missing_questions.map((question) => [question, ""])));
            setIntervention(null);
          }}
          onResume={() => resumeIntervention().catch((error) => onNotice(error.message))}
          onMarkSubmitted={() => markInterventionSubmitted().catch((error) => onNotice(error.message))}
          onDebug={() => debugTask(intervention.task).catch((error) => onNotice(error.message))}
        />
      )}

      {answerTask && (
        <div className="fixed inset-0 z-50 grid place-items-center bg-black/40 p-4">
          <div className="w-full max-w-2xl rounded-[18px] border border-line bg-[#fffdf7] p-5 shadow-float">
            <div className="mb-4 flex items-center justify-between gap-3">
              <div>
                <div className="inline-flex items-center gap-2 rounded bg-amber-100 px-2 py-1 text-xs font-semibold text-amber-800">
                  <BellRing size={14} /> Agent paused
                </div>
                <h3 className="mt-3 text-base font-semibold">Answer Application Questions</h3>
                <div className="mt-1 text-sm text-slate-500">These answers are approved into the KB, then the apply agent resumes automatically.</div>
              </div>
              <button className="text-sm text-slate-500 hover:text-ink" onClick={() => setAnswerTask(null)}>Close</button>
            </div>
            <div className="grid max-h-[60vh] gap-4 overflow-auto pr-1">
              {answerTask.missing_questions.map((question) => (
                <Field key={question} label={question}>
                  <textarea
                    className={textareaClass}
                    value={answers[question] || ""}
                    onChange={(event) => setAnswers((prev) => ({ ...prev, [question]: event.target.value }))}
                  />
                </Field>
              ))}
            </div>
            <div className="mt-5 flex flex-wrap justify-end gap-2">
              <Button variant="secondary" onClick={() => setAnswerTask(null)}>Cancel</Button>
              <Button onClick={() => saveMissingAnswers().catch((error) => onNotice(error.message))}>
                <Save size={15} /> Save &amp; Resume
              </Button>
            </div>
          </div>
        </div>
      )}
      {debugData && (
        <DebugModal title="Apply Task Debug" data={debugData} onClose={() => setDebugData(null)} />
      )}
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
  const script = `(function(){function txt(root,sel){var e=root.querySelector(sel);return e&&e.innerText?e.innerText.trim():""}function cleanUrl(u){try{var x=new URL(u,location.href);x.search="";return x.href}catch(e){return u||location.href}}var host=location.hostname.replace(/^www\\./,"");var seen={};var jobs=[];var cards=[].slice.call(document.querySelectorAll("li.jobs-search-results__list-item,.job-card-container,.base-card,[data-job-id]"));cards.forEach(function(card){var a=card.querySelector('a[href*="/jobs/view/"],a.base-card__full-link,a[href*="/jobs/"]');var url=cleanUrl(a?a.getAttribute("href"):"");if(!url||seen[url])return;seen[url]=true;var lines=(card.innerText||"").split("\\n").map(function(x){return x.trim()}).filter(Boolean);var title=txt(card,'.job-card-list__title,.base-search-card__title,.artdeco-entity-lockup__title,a[href*="/jobs/view/"]')||lines[0]||document.title;var company=txt(card,'.job-card-container__primary-description,.base-search-card__subtitle,.artdeco-entity-lockup__subtitle')||lines[1]||"";var loc=txt(card,'.job-card-container__metadata-item,.job-search-card__location,.artdeco-entity-lockup__caption')||lines[2]||"";jobs.push({page_url:url,source_site:host,title:title,company:company,location:loc,description:(card.innerText||"").slice(0,4000),visible_text:(card.innerText||"").slice(0,4000),apply_url:url});});if(!jobs.length){var title=txt(document,".job-details-jobs-unified-top-card__job-title,.topcard__title,[data-test-job-title],h1")||document.title;var company=txt(document,".job-details-jobs-unified-top-card__company-name,.topcard__org-name-link,[data-test-job-company-name]");var loc=txt(document,".job-details-jobs-unified-top-card__primary-description-container,.topcard__flavor--bullet,[data-test-job-location]");var desc=txt(document,".jobs-description-content__text,.description__text,.jobs-box__html-content,[data-test-job-description],main")||document.body.innerText;jobs=[{page_url:location.href,source_site:host,title:title,company:company,location:loc,description:desc.slice(0,12000),visible_text:document.body.innerText.slice(0,12000),apply_url:location.href}];}var payload={page_url:location.href,source_site:host,jobs:jobs,visible_text:document.body.innerText.slice(0,12000)};var f=document.createElement("form");f.method="POST";f.action=${JSON.stringify(endpoint)};f.target="_blank";var i=document.createElement("input");i.type="hidden";i.name="payload";i.value=JSON.stringify(payload);f.appendChild(i);document.body.appendChild(f);f.submit();setTimeout(function(){f.remove()},1000);})();`;
  return `javascript:${script}`;
}
