# Internship Hunt — n8n Workflow & Backend

An automated job application pipeline powered by **n8n**, **Gemini AI**, **PostgreSQL (pgvector)**, and a **LaTeX compilation service**. Accepts job descriptions (PDF or text), scores them against a candidate profile, generates tailored ATS-optimized resumes, and optionally creates cover letters.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                           n8n Workflow                              │
│                                                                     │
│  Webhook → Parse JD → Visa Check → Score (Gemini) → Resume (Gemini)│
│                                     ↓                    ↓          │
│                              Save Low Score     Build LaTeX Resume   │
│                                                      ↓              │
│                                              Compile to PDF (LaTeX) │
│                                                      ↓              │
│                                              Save to Postgres       │
│                                                      ↓              │
│                                         Cover Letter? (Gemini, opt) │
│                                                      ↓              │
│                                              Log to Google Sheets   │
└─────────────────────────────────────────────────────────────────────┘
```

## Docker Services

| Service | Container | Port | Image |
|---------|-----------|------|-------|
| n8n | `n8n-local` | 5678 | `docker.n8n.io/n8nio/n8n:latest` |
| PostgreSQL + pgvector | `postgres-local` | 5432 | `pgvector/pgvector:pg16` |
| LaTeX compiler | `latex-local` | 3001 | Custom (`./latex-service`) |

### Quick Start

```bash
docker-compose up -d
# n8n UI: http://localhost:5678
# LaTeX API: http://localhost:3001
```

### Environment Variables

| Variable | Description |
|----------|-------------|
| `n8nAPIKEY` | n8n public API key (in `.env`) |
| `POSTGRES_USER` | `applai_user` |
| `POSTGRES_PASSWORD` | `applai_random` |
| `POSTGRES_DB` | `applai_db` |

## Database Schema

### `jobs` table
Stores every processed job application with scoring, resume paths, and versioning.

| Column | Type | Description |
|--------|------|-------------|
| `id` | SERIAL PK | Auto-increment ID |
| `user_email` | VARCHAR(255) | User identifier |
| `profile_name` | VARCHAR(50) | Profile used (e.g., "general") |
| `source` | VARCHAR(50) | Where JD came from (linkedin, nuworks, direct) |
| `source_url` | TEXT | URL of the job posting |
| `company` | VARCHAR(255) | Company name |
| `role` | VARCHAR(255) | Job title |
| `location` | VARCHAR(255) | Job location |
| `job_description` | TEXT | Full JD text |
| `score` | INTEGER | AI match score (0-100) |
| `match_level` | VARCHAR(50) | EXCELLENT/GOOD/MODERATE/LOW |
| `reasoning` | JSONB | `{strengths: [], gaps: [], key_alignments: []}` |
| `hook` | TEXT | AI-generated outreach message |
| `resume_file_path` | TEXT | Path to .tex file (e.g., `/files/output/ByteDance/v1/Resume_ByteDance.tex`) |
| `status` | VARCHAR(50) | `scored`, `resume_generated`, `ineligible`, `applied`, `interviewing`, `accepted`, `rejected`, `no_response`, `pass` |
| `cover_letter_text` | TEXT | AI-generated cover letter (if enabled) |
| `version` | INTEGER | Resume version number (default 1) |
| `created_at` | TIMESTAMP | When job was processed |
| `updated_at` | TIMESTAMP | Last modification |

**Unique Constraint**: `(user_email, company, role, version)` — allows multiple versions per job.

### `profiles` table
Stores candidate profiles used for resume tailoring.

| Column | Type | Description |
|--------|------|-------------|
| `id` | SERIAL PK | Auto-increment ID |
| `user_email` | VARCHAR(255) | User identifier |
| `profile_name` | VARCHAR(50) | Profile name (e.g., "general") |
| `profile_json` | JSONB | Full candidate data (see structure below) |
| `resume_text` | TEXT | Raw resume text |
| `file_path` | TEXT | Path to uploaded resume file |
| `embedding` | VECTOR(768) | pgvector embedding of the profile |

**Unique Constraint**: `(user_email, profile_name)`

#### `profile_json` Structure
```json
{
  "identity": { "name": "...", "email": "..." },
  "profile": {
    "name": "...",
    "contact": { "email": "...", "phone": "...", "location": "...", "linkedin": "...", "github": "..." },
    "education": [{ "institution": "...", "degree": "...", "gpa": "...", "grad_date": "..." }],
    "skills": {
      "languages": ["Python", "Java", ...],
      "frameworks": ["React", "Django", ...],
      "tools": ["Docker", "Git", ...]
    }
  },
  "experience": [{
    "id": "exp_...",
    "role": "Software Engineer",
    "company": "Acme Corp",
    "project_name": "Widget Builder",
    "dates": "Jun 2024 – Aug 2024",
    "summary_sentence": "...",
    "tech_stack": ["Python", "Flask"],
    "bullet_points_pool": ["Built X using Y", "..."],
    "category": ["backend", "ml"]
  }],
  "projects": [{ "...same structure as experience..." }],
  "skills": { "...aggregated skills..." },
  "config": {
    "min_score": 65,
    "generate_cover_letter": true
  }
}
```

## n8n Workflow: "Internship Hunt"

**Workflow ID**: `yXuLWV6WgvLWnNFb`  
**Total Nodes**: 67  
**Trigger**: Webhook at `POST /webhook/add-job`

### Webhook Input (multipart/form-data)

| Field | Required | Description |
|-------|----------|-------------|
| `profile_name` | Yes | Which profile to use (e.g., "general") |
| `user_email` | Yes | User email |
| `source` | Yes | Source platform (linkedin, nuworks, etc.) |
| `jd_file` | No* | PDF file of the job description |
| `jd_text` | No* | Raw text of the job description |
| `source_url` | No | URL of the job posting |

*One of `jd_file` or `jd_text` is required.

### Pipeline Stages

#### Stage 1: Input Processing
```
Job Input Webhook → Has PDF?
  → [YES] Extract JD from PDF → Prepare JD Text
  → [NO]  Direct Text Handler → Prepare JD Text
```
Accepts PDF uploads or raw text. Extracts text from PDFs using n8n's built-in file extraction.

#### Stage 2: Job Parsing (Gemini AI)
```
Prepare JD Text → AI: Parse Job Details → Normalize Job Data
```
Gemini extracts structured data: `company`, `role`, `location`, `constraints` (visa/citizenship requirements), `jd_text`.

#### Stage 3: Eligibility & Scoring
```
Normalize Job Data → Eligible? (Visa Check)
  → [ELIGIBLE] Fetch Profile → Profile Found? → Wait3 (30s) → AI: Score Job Match → Parse Score
  → [INELIGIBLE] Save Ineligible Job
```
- Checks visa/citizenship constraints
- Fetches candidate profile from Postgres
- Gemini scores the match (0-100) with reasoning, strengths, gaps, and an outreach hook

#### Stage 4: Score Threshold & Versioning
```
Parse Score → Score > Config Score?
  → [ABOVE THRESHOLD] Get Next Version → Wait1 (30s) → AI: Generate Resume2
  → [BELOW THRESHOLD] Save Low Score Job → Log Low to Sheet
```
- Compares score against `config.min_score` from profile (default: 65)
- **Get Next Version** queries `MAX(version) + 1` for this company+role to support multiple resume versions
- Low-scoring jobs are saved to DB and logged to Google Sheets

#### Stage 5: Resume Generation (Gemini AI)
```
AI: Generate Resume2 → Read Master Template → Extract Template Text → Build Resume
```
- Gemini generates tailored LaTeX sections: `experience_latex`, `projects_latex`, `skills_latex`
- Master LaTeX template is read from disk, placeholders replaced with AI-generated content
- **Build Resume** (Code node) parses the JSON, sanitizes LaTeX escaping, generates final `.tex` file

#### Stage 6: File Compilation
```
Build Resume → Convert Resume to File → Save Resume File → Compile to PDF
```
- Saves `.tex` file to `output/{CompanyName}/v{version}/Resume_{CompanyName}.tex`
- Sends to LaTeX service (`http://latex-local:3001/compile`) to generate PDF
- File paths are versioned: `output/ByteDance/v1/`, `output/ByteDance/v2/`, etc.

#### Stage 7: Save & Cover Letter
```
Compile to PDF → Save High Score Job → Cover Letter? → Log High to Sheet
                                         ↓ [if enabled]
                                    Wait (60s) → AI: Generate Cover Letter → Build Cover Letter → Update Cover Letter
```
- **Save High Score Job** INSERTs to Postgres (job saved FIRST, before cover letter)
- If `config.generate_cover_letter` is `true` in profile:
  - Waits 60s to avoid Gemini rate limits
  - Generates a tailored cover letter
  - UPDATEs the job row with `cover_letter_text`
- `AI: Generate Cover Letter` has `continueOnFail: true` — rate limit failures don't crash the pipeline

### Key Design Decisions
1. **Job saved before cover letter** — resume/score/hook are persisted regardless of cover letter success
2. **Wait nodes** (30-60s each) between Gemini calls to avoid rate limiting
3. **Version auto-increment** — `Get Next Version` queries `MAX(version) + 1` per company+role
4. **LaTeX sanitization** in Build Resume code handles special characters (`%`, `&`, `_`, `#`, `$`)
5. **Profile-driven config** — `min_score` and `generate_cover_letter` are stored in `profile_json.config`

## File Structure

```
n8n-local/
├── docker-compose.yml          # 3 services: n8n, postgres, latex
├── .env                        # n8n API key
├── latex-service/              # LaTeX compilation microservice
│   ├── server.py               # Flask server on port 3001
│   └── Dockerfile
├── local_files/
│   ├── output/                 # Generated resumes (versioned)
│   │   ├── ByteDance/
│   │   │   └── v1/
│   │   │       ├── Resume_ByteDance.tex
│   │   │       └── Resume_ByteDance.pdf
│   │   └── ... (24 companies)
│   └── master_template.tex     # LaTeX resume template
├── Job Descriptions/
│   └── New/                    # PDF job descriptions for testing
├── n8n_data/                   # n8n persistent data (workflows, credentials)
└── postgres_data/              # PostgreSQL data directory
```

## LaTeX Service API

**`POST /compile`**
```json
{ "file_path": "output/ByteDance/v1/Resume_ByteDance.tex" }
```
Returns: `{ "status": "success", "pdf_path": "output/ByteDance/v1/Resume_ByteDance.pdf" }`

Compiles `.tex` to `.pdf` using `pdflatex`. Files are accessed via the shared `/files` Docker volume.

## n8n API

The n8n instance exposes a REST API for workflow management:

```bash
# List workflows
curl -H "X-N8N-API-KEY: $n8nAPIKEY" http://localhost:5678/api/v1/workflows

# Get workflow details
curl -H "X-N8N-API-KEY: $n8nAPIKEY" http://localhost:5678/api/v1/workflows/yXuLWV6WgvLWnNFb

# List executions
curl -H "X-N8N-API-KEY: $n8nAPIKEY" http://localhost:5678/api/v1/executions?limit=5

# Submit a job
curl -X POST http://localhost:5678/webhook/add-job \
  -F "profile_name=general" \
  -F "user_email=jenilmahy25@gmail.com" \
  -F "source=linkedin" \
  -F "jd_file=@path/to/job.pdf"
```

## Credentials Required in n8n

| Credential | Used By |
|------------|---------|
| Google Gemini (PaLM) API | All AI nodes (Parse, Score, Resume, Cover Letter) |
| PostgreSQL | All database read/write nodes |
| Google Sheets | Logging nodes (optional) |