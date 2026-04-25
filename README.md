# SeekApply - Resume-First Job Application Assistant

SeekApply is now focused on one practical workflow:

1. Upload a resume.
2. Extract profile fields and verified skills from it.
3. Ask only the missing questions needed for job applications.
4. Search/import jobs from LinkedIn-style flows.
5. Read job descriptions and score them against the resume/profile.
6. If the score is `85%` or higher, use the base resume.
7. If the score is below `85%`, truthfully tailor the resume and save a new version.
8. Track logs, application status, company questions, and saved answers in the knowledge base.

The app is local, single-user, and review-first. It helps prepare applications, but the user remains in control.

## Safety Rules

SeekApply does **not**:

- fabricate resume claims
- invent projects, employment, education, certifications, or achievements
- claim GitHub projects unless they belong to the user
- auto-submit job applications
- auto-send emails
- bypass CAPTCHA, login protections, or portal restrictions
- scrape hidden/private data

SeekApply does:

- extract visible resume and job information
- generate LinkedIn search links
- import visible job-page text
- score jobs against the user profile
- tailor resumes truthfully
- ask the user for missing answers
- save application questions and answers for reuse
- log decisions and application history

## Main Product Flow

### 1. Resume Intake

Start here.

Frontend tab:

```text
Resume Intake
```

Upload:

- PDF
- DOCX
- TXT
- Markdown

Backend endpoint:

```text
POST /resumes/upload-base
```

The backend extracts:

- name
- email
- phone
- LinkedIn URL
- GitHub URL
- skills
- raw resume text
- missing application questions

If no user exists yet, the app creates a local user from the resume extraction.

The resume becomes the verified source for matching and tailoring.

### 2. Missing Questions

After resume upload, the app returns questions such as:

- What phone number should be used for job applications?
- What is your LinkedIn profile URL?
- What is your notice period?
- What is your expected compensation?
- What locations or remote preferences should be used?
- Are there companies or industries that must be excluded?

These answers are saved in the knowledge base.

Frontend tab:

```text
Questions KB
```

Backend endpoints:

```text
POST /answers
GET  /answers
GET  /answers/suggest?job_id=...
POST /answers/{answer_id}/approve
```

### 3. Find Jobs

Frontend tab:

```text
Find Jobs
```

This currently uses LinkedIn Browser Assist:

- generates LinkedIn search URLs from keywords, location, and work mode
- user opens LinkedIn manually
- user reviews jobs
- user imports visible job text and URL into SeekApply

Backend endpoints:

```text
POST /linkedin/assist/search
POST /linkedin/assist/import-visible
```

The app does not perform hidden LinkedIn scraping or auto-apply actions.

### 4. Page Import

Frontend tab:

```text
Page Import
```

Use this for any job portal page where the user can see the job details.

Supported style:

- LinkedIn
- Naukri
- Indeed
- Wellfound
- Instahyre
- Cutshort
- Hirist
- Greenhouse
- Lever
- Ashby
- SmartRecruiters
- company career pages

Backend endpoints:

```text
GET  /browser-assist/site-rules
POST /browser-assist/import-current-page
```

The import stores:

- source site
- page URL
- role
- company
- description
- extracted skills
- parser confidence
- missing fields

### 5. Match And Resume Decision

Frontend tab:

```text
Match & Resume
```

Primary backend endpoint:

```text
POST /jobs/{job_id}/resume-decision
```

This endpoint does the main logic:

1. Scores the job against the uploaded resume/profile.
2. Uses the configured threshold, default `85`.
3. If score is `>= 85` and the base resume exists:
   - action: `use_base_resume`
   - no unnecessary resume version is created
   - application is shortlisted for manual review
4. If score is below `85`:
   - action: `tailored_resume_created`
   - creates a truthful tailored resume
   - saves DOCX, PDF, and JSON metadata
   - links the version to the application
5. If safety rules block the job:
   - action: `blocked`
   - logs the reason
   - does not tailor or proceed

Supporting endpoints:

```text
POST /jobs/{job_id}/score
GET  /jobs/{job_id}/required-questions
POST /jobs/{job_id}/draft-email
```

### 6. Resume Versions

When a resume needs tailoring, SeekApply saves:

```text
storage/resume_versions/
storage/metadata/
```

Each generated resume version has metadata:

- company
- role
- job URL
- match score
- emphasized skills
- base resume ID
- truthfulness status
- recommended projects to build, if any

Resume tailoring rules:

- reorder and emphasize existing truthful skills
- rewrite summaries and bullets based on existing information
- do not invent missing experience
- missing skills may become “recommended project to build”

### 7. Application Questions Knowledge Base

Frontend tab:

```text
Questions KB
```

When a company portal asks a new question, save it here.

Examples:

- Expected CTC
- Current CTC
- Notice period
- Work authorization
- Willingness to relocate
- Why this company?
- Why this role?

The app can reuse approved answers later, but sensitive answers stay review-first.

### 8. Application Tracking

Frontend tab:

```text
Applications
```

Backend endpoints:

```text
GET   /tracker
PATCH /applications/{application_id}/status
```

The tracker stores:

- company
- role
- job URL
- source
- application date
- resume version used
- match score
- salary
- location
- status
- notes
- follow-up date

### 9. Logs

Frontend tab:

```text
Logs
```

Backend endpoint:

```text
GET /run-history
```

Logs include:

- resume extraction runs
- browser imports
- resume decisions
- blocked jobs
- tailored resume creation
- missing fields

## Backend API Summary

Resume:

```text
POST /resumes/upload-base
GET  /resume-versions
```

Jobs:

```text
POST /jobs/import-url
POST /jobs/search
POST /jobs/{job_id}/score
POST /jobs/{job_id}/resume-decision
GET  /jobs/{job_id}/required-questions
POST /jobs/{job_id}/draft-email
POST /jobs/{job_id}/prepare-application-packet
```

LinkedIn and browser assist:

```text
POST /linkedin/assist/search
POST /linkedin/assist/import-visible
GET  /browser-assist/site-rules
POST /browser-assist/import-current-page
```

Knowledge base:

```text
POST /answers
GET  /answers
GET  /answers/suggest?job_id=...
POST /answers/{answer_id}/approve
POST /claim-ledger
GET  /claim-ledger
```

Tracking and logs:

```text
GET   /tracker
PATCH /applications/{application_id}/status
GET   /run-history
GET   /analytics
```

Settings:

```text
GET /settings/safety
GET /settings/oci
```

## Project Structure

```text
seekApply/
  backend/
    app/
      api/              FastAPI routes
      core/             settings
      db/               SQLAlchemy setup
      models/           SQLite models
      schemas/          request/response schemas
      services/
        resume_extraction.py
        matching.py
        resume.py
        linkedin_assist.py
        application_packet.py
        safety.py
        email_outreach.py
        oci_genai.py
    tests/
    requirements.txt
    .env.example
  frontend/
    src/
      App.tsx
      components/
      lib/api.ts
  storage/
    base_resumes/
    resume_versions/
    metadata/
    vector_index/
```

## Local Setup

### Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --port 8000
```

Health check:

```bash
curl -sS http://127.0.0.1:8000/health
```

Expected:

```json
{"status":"ok","review_first":true,"auto_apply":false,"auto_email":false}
```

### Frontend

```bash
cd frontend
npm install
npm run dev -- --host 127.0.0.1 --port 5173
```

Open:

```text
http://127.0.0.1:5173/
```

## Testing

Backend:

```bash
cd backend
.venv/bin/python -m pytest
```

Frontend:

```bash
cd frontend
npm run build
```

## OCI Generative AI

OCI configuration is optional for the deterministic MVP workflow.

The app reads OCI config/profile auth:

```bash
OCI_CONFIG_FILE=~/.oci/config
OCI_PROFILE=DEFAULT
OCI_REGION=us-chicago-1
OCI_COMPARTMENT_OCID=ocid1.compartment...
OCI_GENAI_MODEL_ID=...
# or OCI_GENAI_ENDPOINT_ID=...
```

Private key contents are not stored in the database.

## What Was Removed From The Main Workflow

The earlier app had many broad dashboard pages. The main navigation is now intentionally smaller:

- Resume Intake
- Find Jobs
- Match & Resume
- Questions KB
- Applications
- Logs
- Page Import

Some older helper endpoints still exist internally, but the product surface is now centered on the resume-first job application flow.
