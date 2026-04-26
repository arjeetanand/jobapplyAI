export const API_URL = import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8000";

export type Analytics = {
  total_jobs: number;
  total_applications: number;
  status_counts: Record<string, number>;
  average_match_score: number;
  followups_due_soon: number;
};

export type TrackerRow = {
  id: number;
  company: string;
  role: string;
  job_url: string;
  source: string;
  application_date: string | null;
  resume_version: string | null;
  match_score: number | null;
  salary: string | null;
  location: string | null;
  status: string;
  follow_up_date: string | null;
  notes: string | null;
};

export type ResumeVersion = {
  id: number;
  company: string;
  role: string;
  docx_path: string;
  pdf_path: string;
  metadata_path: string;
  skills_emphasized: string[];
  truthfulness_status: string;
  created_at: string;
};

export type LinkedInPlan = {
  url: string;
  keyword: string;
  location: string;
  filters: Record<string, string>;
  safety_notes: string[];
};

export type DiscoveryPreferences = {
  user_id: number | null;
  keywords: string[];
  location: string | null;
  date_since_posted: string;
  work_mode: string;
  easy_apply: string;
  limit: number;
};

export type Answer = {
  id: number;
  question_key: string;
  question_text: string;
  answer_text: string;
  source: string;
  sensitive: boolean;
  approved: boolean;
};

export type CurrentResume = {
  user_id: number | null;
  base_resume: {
    filename: string;
    path: string;
    exists: boolean;
    size_bytes: number | null;
    download_url: string | null;
    text_preview: string;
    uploaded_at: string;
  } | null;
  profile: {
    name: string;
    email: string;
    phone: string | null;
    location: string | null;
    linkedin_url: string | null;
    github_url: string | null;
    work_authorization: string | null;
    skills: string[];
    notice_period: string | null;
  } | null;
  preferences: {
    preferred_salary: string | null;
    preferred_locations: string[];
    remote_preference: string;
    excluded_companies: string[];
    match_threshold: number;
    auto_apply_enabled: boolean;
    auto_email_enabled: boolean;
  } | null;
  missing_questions: string[];
  answers: Answer[];
};

export type Claim = {
  id: number;
  claim_type: string;
  claim_text: string;
  source: string;
  approved: boolean;
};

export type JobRow = {
  id: number;
  title: string;
  company: string;
  location: string | null;
  work_mode: string | null;
  salary: string | null;
  source: string;
  status: string;
  match_score: number | null;
  score_reasons: string[];
  score_concerns: string[];
  job_url: string;
  skills: string[];
  created_at: string;
  application_id: number | null;
  application_status: string | null;
  resume_version_id: number | null;
};

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    headers: { "Content-Type": "application/json", ...(options?.headers ?? {}) },
    ...options
  });
  if (!response.ok) {
    let detail = "";
    try {
      const data = await response.json();
      if (typeof data?.detail === "string") detail = data.detail;
      else detail = JSON.stringify(data);
    } catch {
      detail = await response.text();
    }
    if (response.status === 404 && path === "/resumes/profile") {
      throw new Error("Profile save endpoint is unavailable. Restart backend to load the latest API routes.");
    }
    throw new Error(detail || response.statusText);
  }
  return response.json();
}

export const api = {
  health: () => request<{ status: string }>("/health"),
  analytics: () => request<Analytics>("/analytics"),
  tracker: () => request<{ applications: TrackerRow[] }>("/tracker"),
  resumes: () => request<{ resume_versions: ResumeVersion[] }>("/resume-versions"),
  safety: () => request<{ rules: string[]; auto_apply_enabled: boolean; auto_email_enabled: boolean }>(
    "/settings/safety"
  ),
  oci: () =>
    request<{
      configured: boolean;
      message: string;
      config_file: string;
      profile: string;
      region: string | null;
      compartment_configured: boolean;
      model_or_endpoint_configured: boolean;
    }>("/settings/oci"),
  onboarding: (payload: unknown) =>
    request<{ user_id: number; message: string }>("/onboarding/profile", {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  currentResume: () => request<CurrentResume>("/resumes/current"),
  uploadBaseResume: async (file: File) => {
    const data = new FormData();
    data.append("file", file);
    const response = await fetch(`${API_URL}/resumes/upload-base`, {
      method: "POST",
      body: data
    });
    if (!response.ok) {
      throw new Error(await response.text());
    }
    return response.json() as Promise<{
      user_id: number;
      base_resume_path: string;
      base_resume: CurrentResume["base_resume"];
      extracted: { name: string; email: string; phone: string | null; linkedin_url: string | null; github_url: string | null; skills: string[] };
      missing_questions: string[];
    }>;
  },
  downloadBaseResume: async (): Promise<void> => {
    const response = await fetch(`${API_URL}/resumes/base/download`);
    if (!response.ok) throw new Error(`Download failed: ${response.statusText}`);
    const blob = await response.blob();
    const cd = response.headers.get("content-disposition") ?? "";
    const match = cd.match(/filename="?([^"]+)"?/);
    const filename = match?.[1] ?? "base_resume";
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  },
  updateResumeProfile: (payload: unknown) =>
    request<{ user_id: number; message: string; profile: Record<string, unknown> }>("/resumes/profile", {
      method: "PATCH",
      body: JSON.stringify(payload)
    }),
  importJob: (payload: unknown) =>
    request<{ job_id: number; message: string }>("/jobs/import-url", {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  linkedinAssist: (payload: unknown) =>
    request<{ mode: string; preferences: DiscoveryPreferences; plans: LinkedInPlan[]; checklist: string[] }>("/linkedin/assist/search", {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  linkedinPreferences: () => request<DiscoveryPreferences>("/linkedin/assist/preferences"),
  saveLinkedinPreferences: (payload: unknown) =>
    request<{ message: string; preferences: DiscoveryPreferences }>("/linkedin/assist/preferences", {
      method: "PATCH",
      body: JSON.stringify(payload)
    }),
  linkedinImportVisible: (payload: unknown) =>
    request<{ job_id: number; message: string; parsed: { title: string; company: string; skills: string[] } }>(
      "/linkedin/assist/import-visible",
      {
        method: "POST",
        body: JSON.stringify(payload)
      }
    ),
  browserImport: (payload: unknown) =>
    request<{ job_id: number; message: string; parser_confidence: string; missing_fields: string[] }>(
      "/browser-assist/import-current-page",
      {
        method: "POST",
        body: JSON.stringify(payload)
      }
    ),
  siteRules: () => request<{ rules: string[]; supported_sites: string[] }>("/browser-assist/site-rules"),
  createAnswer: (payload: unknown) =>
    request<{ answer_id: number; message: string }>("/answers", {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  bulkAnswers: (payload: unknown) =>
    request<{ user_id: number; saved_count: number; answers: Answer[]; missing_questions: string[]; message: string }>(
      "/answers/bulk",
      {
        method: "POST",
        body: JSON.stringify(payload)
      }
    ),
  answers: () => request<{ answers: Answer[] }>("/answers"),
  approveAnswer: (answerId: number) =>
    request<{ answer_id: number; approved: boolean }>(`/answers/${answerId}/approve`, { method: "POST" }),
  createClaim: (payload: unknown) =>
    request<{ claim_id: number; message: string }>("/claim-ledger", {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  claims: () => request<{ claims: Claim[] }>("/claim-ledger"),
  preparePacket: (jobId: number) =>
    request<{ packet_id: number; packet: Record<string, unknown>; missing_items: string[] }>(
      `/jobs/${jobId}/prepare-application-packet`,
      { method: "POST" }
    ),
  runHistory: () =>
    request<{
      agent_runs: Array<{ id: number; agent_name: string; input_summary: string; output_summary: string; status: string }>;
      browser_imports: Array<{ id: number; job_id: number; source_site: string; page_url: string; parser_confidence: string; missing_fields: string[] }>;
    }>("/run-history"),
  scoreJob: (jobId: number) =>
    request<{
      job_title: string;
      company: string;
      match_score: number;
      reason: string[];
      concerns: string[];
      recommendation: string;
    }>(`/jobs/${jobId}/score`, { method: "POST" }),
  tailorResume: (jobId: number) =>
    request<{ resume_version_id: number; pdf_path: string; docx_path: string; reused: boolean }>(
      `/jobs/${jobId}/tailor-resume`,
      { method: "POST" }
    ),
  resumeDecision: (jobId: number) =>
    request<{
      job_id: number;
      match_score: number;
      threshold: number;
      action: string;
      message: string;
      resume_path?: string;
      resume_version_id?: number;
      pdf_path?: string;
      docx_path?: string;
      ai_generated?: boolean;
      reasons?: string[];
      concerns?: string[];
    }>(`/jobs/${jobId}/resume-decision`, { method: "POST" }),
  requiredQuestions: (jobId: number) =>
    request<{ job_id: number; questions: Array<{ question: string; status: string }>; saved_answers: unknown[] }>(
      `/jobs/${jobId}/required-questions`
    ),
  draftEmail: (jobId: number) =>
    request<{ email_id: number; subject: string; body: string; status: string }>(`/jobs/${jobId}/draft-email`, {
      method: "POST"
    }),
  listJobs: () => request<{ jobs: JobRow[] }>("/jobs"),
  discoverJobs: (query: string, location: string) =>
    request<{ message: string; jobs_found: number; jobs_added: number }>("/jobs/discover", {
      method: "POST",
      body: JSON.stringify({ query, location, limit: 5 })
    }),
  autoApply: (jobId: number) =>
    request<{ status: string; message: string; steps: string[]; fill_plan?: any }>(`/jobs/${jobId}/auto-apply`, {
      method: "POST"
    }),
  updateApplicationStatus: (applicationId: number, status: string, notes?: string) =>
    request<{ application_id: number; status: string }>(
      `/applications/${applicationId}/status`,
      { method: "PATCH", body: JSON.stringify({ status, notes: notes ?? null, follow_up_date: null }) }
    ),
  downloadResumePdf: async (versionId: number): Promise<void> => {
    const response = await fetch(`${API_URL}/resume-versions/${versionId}/download/pdf`);
    if (!response.ok) throw new Error(`Download failed: ${response.statusText}`);
    const blob = await response.blob();
    const cd = response.headers.get("content-disposition") ?? "";
    const match = cd.match(/filename="?([^"]+)"?/);
    const filename = match?.[1] ?? `resume_${versionId}.pdf`;
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  },
  downloadResumeDocx: async (versionId: number): Promise<void> => {
    const response = await fetch(`${API_URL}/resume-versions/${versionId}/download/docx`);
    if (!response.ok) throw new Error(`Download failed: ${response.statusText}`);
    const blob = await response.blob();
    const cd = response.headers.get("content-disposition") ?? "";
    const match = cd.match(/filename="?([^"]+)"?/);
    const filename = match?.[1] ?? `resume_${versionId}.docx`;
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  },
  downloadResumeTex: async (versionId: number): Promise<void> => {
    const response = await fetch(`${API_URL}/resume-versions/${versionId}/download/tex`);
    if (!response.ok) throw new Error(`Download failed: ${response.statusText}`);
    const blob = await response.blob();
    const cd = response.headers.get("content-disposition") ?? "";
    const match = cd.match(/filename="?([^"]+)"?/);
    const filename = match?.[1] ?? `resume_${versionId}.tex`;
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  },
};
