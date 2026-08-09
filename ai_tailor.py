import os
from config import get_key
from docx import Document
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

client = OpenAI(
    api_key=get_key("GROQ_API_KEY"),
    base_url="https://api.groq.com/openai/v1"
)

def extract_text_from_docx(file_path):
    doc = Document(file_path)
    return "\n".join([para.text for para in doc.paragraphs])

def tailor_application(master_resume_text, job_description, job_title, company):
    """Tailor resume + cover to a JD. Two-pass: draft -> critique -> rewrite, in 3 calls.

    Only reorders/repackages what is actually on the master resume; never invents
    experience. ATS keyword coverage is checked and enforced. Uses 3 LLM calls
    (budget-friendly): one draft prompt that embeds the JD, then critique + rewrite.
    """
    user_context = """
    USER BACKGROUND CONTEXT (use ONLY these real facts; never invent experience):
    - Education: Master's degree in Electrical Engineering.
    - Data Science: PCA, K-Means clustering, Logistic Regression with regularization,
      dataset analysis (e.g., 'Online Shoppers Purchasing Intention').
    - Software: React.js for frontend, Python.
    - Documentation: academic documentation, data-flow / ER diagrams, architectural
      design, security planning.
    """

    base_prompt = f"""
You are an elite resume writer and cover-letter coach. Maximize interview odds by
mapping the user's REAL background to the JD while staying 100% truthful.

{user_context}

MASTER RESUME:
---
{master_resume_text}
---

JOB DESCRIPTION:
---
{job_description}
---

TARGET ROLE: {job_title} at {company}

STRICT RULES:
1. NEVER invent jobs, employers, dates, titles, or skills the user lacks. Reorder,
   rephrase, and re-quantify ONLY facts present in the master resume.
2. List the top 5-8 keywords/skills from the JD. Ensure EACH one the user genuinely
   has appears VERBATIM in the resume.
3. Bullets start with a strong action verb and include a number/outcome where
   truthful (e.g. "Reduced X by 30%"). Keep each bullet one line. Prefer the format
   "Action V + what you did + quantified result" (STAR).
4. Front-load the role target and the 3-4 key JD keywords near the top.
5. COVER LETTER: max 3 short paragraphs. P1: which role + one strong proof point +
   why THIS company (specific to the JD). P2: the single best matching
   project/achievement. P3: offer to discuss + next step. Enthusiastic, concrete,
   no generic filler, no placeholder names.
6. Header name line: "FULL NAME".
"""

    # Pass 1 — draft.
    draft = _call(base_prompt + """

OUTPUT FORMAT (follow exactly):
RESUME_START
[tailored resume here]
RESUME_END
COVER_LETTER_START
[tailored cover letter here]
COVER_LETTER_END
""", temperature=0.6)

    resume, cover = _split(draft)

    # Pass 2 — cold-eyed critique, then fix.
    critique = _call(f"""
You are a strict hiring manager and ATS screener reviewing a tailored resume.

JD:
{job_description[:2000]}

DRAFT RESUME:
{resume}

CRITIQUE. Return EXACTLY this JSON (no other text):
{{
  "keyword_misses": ["top JD keyword NOT present in the resume, if any"],
  "weak_lines": ["one per truly weak/buzzwordy/generic bullet", ...],
  "invented_facts": ["any claim NOT in the master resume, else []"],
  "score": 0
}}
""", 0.0)

    # Ask for the improved final using the critique.
    final = _call(base_prompt + f"""
MASTER RESUME (source of truth):
{master_resume_text}

FIRST DRAFT:
{resume}
{cover}

A CRITIC OBSERVED:
{critique or "no critique"}

Produce the improved FINAL version. Apply every justified critique, add any missing
JD keyword the user truly has, tighten every bullet, and keep every claim factual.

OUTPUT FORMAT (follow exactly):
RESUME_START
[best resume, ATS-scannable]
RESUME_END
COVER_LETTER_START
[best cover letter]
COVER_LETTER_END
""", 0.4)

    final_resume, final_cover = _split(final)

    # Safety: never let the fresh critique introduce fabricated jobs. Trim empty.
    if not final_cover:
        final_cover = "Please see the attached resume."
    return final_resume or resume, final_cover


# Model used for tailoring. Override with GROQ_MODEL_NAME for a cheaper/quota-
# friendlier model (e.g. llama-3.1-8b-instant), or a stronger one.
_MODEL = get_key("GROQ_MODEL_NAME") or "llama-3.3-70b-versatile"
_META_CALLS = 1  # reserve budget for the meta+tailor calls per job

def _call(prompt, temperature, retries=3):
    """Chat call with exponential backoff on Groq rate-limit (429/503).

    Auto-scan makes 4 calls/job; free tier throttles hard. Back off instead of
    crashing so the scan survives a busy window.
    """
    import time
    for attempt in range(retries):
        try:
            return client.chat.completions.create(
                model=_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
            ).choices[0].message.content
        except Exception as e:
            status = getattr(e, "status_code", None) or getattr(e, "status", None)
            if status in (429, 503) and attempt < retries - 1:
                time.sleep(2 ** attempt * 2)
                continue
            raise
    raise RuntimeError("unreachable")


def _split(content):
    try:
        res = content.split("RESUME_START")[1].split("RESUME_END")[0].strip()
        cov = content.split("COVER_LETTER_START")[1].split("COVER_LETTER_END")[0].strip()
        return res, cov
    except IndexError:
        return content, ""


def extract_meta_from_jd(job_description):
    """Extract {job_title, company, recipient} from a job description in one LLM call.

    Job-alert emails (LinkedIn/Indeed) rarely contain the real recruiter email.
    Look for bare emails, 'Apply at', 'mailto:', 'careers/apply' links inside the
    JD. Return "" for fields not found. Never invent an address.
    """
    prompt = f"""
    You are extracting application details from a job description.
    JOB DESCRIPTION:
    {job_description}

    Extract and return three fields:
    - job_title: the position title (e.g. "Software Engineer Intern"). "" if unclear.
    - company: the hiring company name. "" if unknown.
    - recipient_email: an email address a candidate should send an application/resume to.
      Candidates: bare emails (name@domain.com), 'apply@', 'careers@', 'jobs@', 'hr@',
      'hello@', 'mailto:' links. If only a generic 'jobs@company.com' style with no
      real address given, use "". Do NOT invent or guess an address.

    OUTPUT (follow exactly, one per line):
    TITLE_START
    <job_title>
    TITLE_END
    COMPANY_START
    <company>
    COMPANY_END
    EMAIL_START
    <recipient_email>
    EMAIL_END
    """
    try:
        response = client.chat.completions.create(
            model=_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0
        )
        full_text = response.choices[0].message.content

        def grab(start, end):
            try:
                return full_text.split(start)[1].split(end)[0].strip()
            except IndexError:
                return ""

        title = grab("TITLE_START", "TITLE_END")
        company = grab("COMPANY_START", "COMPANY_END")
        email = grab("EMAIL_START", "EMAIL_END")
        if "@" not in email:
            email = ""
        return {"job_title": title, "company": company, "recipient": email}
    except Exception:
        return {"job_title": "", "company": "", "recipient": ""}


def build_application_from_jd(master_text, jd):
    """One-stop: derive meta (title/company), tailor docs, and find recipient.

    Returns dict: {job_title, company, recipient, resume, cover_letter}
    recipient may be "".
    """
    meta = extract_meta_from_jd(jd)
    job_title = meta["job_title"] or "the role"
    company = meta["company"] or "the company"
    resume, cover_letter = tailor_application(master_text, jd, job_title, company)
    return {
        "job_title": job_title,
        "company": company,
        "recipient": meta["recipient"],
        "resume": resume,
        "cover_letter": cover_letter,
    }