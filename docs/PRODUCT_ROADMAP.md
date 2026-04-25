# SeekApply Product Roadmap

This roadmap captures safe ideas inspired by open-source job automation projects while keeping SeekApply review-first, truthful, and compliant.

## Near-Term Build Ideas

### 1. Browser Assist Extension

Build a Chrome extension that runs on job pages and sends visible job details to the local FastAPI app.

Useful behavior:

- Detect title, company, location, job description, recruiter details, and apply URL from the current page.
- Let the user click `Import to SeekApply`.
- Never read hidden/private data.
- Never bypass login, CAPTCHA, paywalls, or access controls.
- Support LinkedIn, Naukri, Indeed, Wellfound, Instahyre, Cutshort, Hirist, Greenhouse, Lever, Ashby, and company pages through per-site adapters.

Backend additions:

- `POST /browser-assist/import-current-page`
- `GET /browser-assist/site-rules`
- `POST /browser-assist/diagnostics`

Frontend additions:

- Browser Assist page showing recently imported jobs, source site, parser confidence, and missing fields.

### 2. Application Answer Bank

Store reusable, truthful answers for common application questions.

Examples:

- Expected CTC
- Notice period
- Work authorization
- Willingness to relocate
- Why this company
- Why this role
- Current CTC
- Sponsorship requirements

Rules:

- Sensitive answers require explicit user review.
- The app may suggest answers but should not submit them automatically.
- Every answer should have a source: user profile, saved answer, resume, or user-provided one-time response.

Backend additions:

- `application_answers` table
- `POST /answers`
- `GET /answers/suggest?job_id=...`
- `POST /answers/{id}/approve`

### 3. Dry-Run Application Packet

Before applying, generate a complete packet the user can inspect.

Packet contents:

- Job summary
- Match score and explanation
- Safety warnings
- Tailored resume version
- Suggested form answers
- Outreach email draft
- Follow-up schedule
- Manual application checklist

Backend additions:

- `POST /jobs/{job_id}/prepare-application-packet`
- `application_packets` table

### 4. Job Source Connectors

Add connectors in layers from safest to riskiest.

Recommended order:

- Public company career pages
- Public ATS pages: Greenhouse, Lever, Ashby, SmartRecruiters
- Free APIs: Adzuna, Remotive, Arbeitnow, The Muse
- Browser Assist for LinkedIn/Naukri/Indeed/Instahyre/Cutshort/Hirist
- Optional unofficial LinkedIn public-results connector with low rate limits and clear warnings

Every connector should report:

- Source type
- Auth required or not
- Terms/compliance risk
- Parser confidence
- Rate-limit policy
- Whether auto-apply is allowed, which should default to no

### 5. Run History And Recovery

Track every agent action so jobs are not duplicated and failed operations are recoverable.

Add:

- Run history dashboard
- Per-agent logs
- Retry failed imports
- Duplicate detection explanations
- Dry-run mode for all discovery sources
- Export/import local database backup

### 6. Resume Claim Ledger

Create a structured ledger of user-approved facts.

Fact types:

- Skill
- Project
- Work experience
- Metric
- Certification
- Education
- Achievement
- GitHub repository ownership

Resume tailoring should only use facts in the ledger. New project ideas must be saved as `recommended_project`, not resume experience.

### 7. Recruiter Outreach Controls

Make outreach safer and less spammy.

Add:

- Contact confidence scoring
- Email source citation
- Daily outreach cap
- Duplicate recruiter detection
- Follow-up cooldown
- Do-not-contact company/person list
- Draft-only default

## What Not To Build

Do not build:

- CAPTCHA bypass
- Stealth browser evasion
- Hidden scraping of private pages
- Mass auto-submit on protected portals
- Fake answers or fabricated resume claims
- Automatic recruiter spam

## Recommended Next Implementation

Build these in order:

1. Browser Assist import endpoint and Chrome extension scaffold.
2. Application Answer Bank.
3. Application Packet generator.
4. Run History dashboard.
5. Resume Claim Ledger.

This sequence gives SeekApply the practical power of job automation tools while keeping the user in control.
