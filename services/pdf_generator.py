"""
pdf_generator.py — ATS-optimized PDF CV generation for Career-Ops.

Uses Playwright (already a project dependency) to render HTML → PDF.
generate_cv() in gemini_client returns: summary, keywords_injected,
experience_bullets, skills_to_highlight, cover_letter_opening.
This module maps that structure onto a clean, ATS-friendly HTML template.
"""

import logging
import os
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path("output/pdfs")

# Aishik's static contact block
_CONTACT = "+91-8741-03-4164 | dutta1890@gmail.com | linkedin.com/in/aishikduttaoct18 | Mumbai, India"

# Full experience history (static; bullets are overridden by AI-tailored content)
_DEFAULT_EXPERIENCE = [
    {
        "title": "Senior Performance Marketing Manager",
        "company": "HealthHub (Confidential Client)",
        "dates": "2022 – Present",
        "bullets": [
            "Drove 24M impressions and 178K clicks via SKAG campaign restructure",
            "Grew inbound leads to 200–300/month from near-zero baseline",
            "Managed ₹50L+ monthly media budget across Google, Meta, and programmatic",
        ],
    },
    {
        "title": "Performance Marketing Lead",
        "company": "Gulf Pharmacy | Dubai, UAE",
        "dates": "2021 – 2022",
        "bullets": [
            "Achieved 1.38× ROAS — 100K AED revenue on 72K AED spend",
            "Scaled e-commerce revenue 3× YoY through Google Shopping + Meta retargeting",
            "Led cross-border campaigns across UAE, KSA, and Kuwait",
        ],
    },
    {
        "title": "Digital Marketing Manager",
        "company": "Géant Hypermarkets | Dubai, UAE",
        "dates": "2020 – 2021",
        "bullets": [
            "Delivered 5M impressions and 21M video views at 70% audience reach",
            "Managed integrated brand + performance campaigns across 6 markets",
        ],
    },
    {
        "title": "Performance Marketing Specialist",
        "company": "Camelicious",
        "dates": "2019 – 2020",
        "bullets": [
            "Generated $600K engagement value; scaled Amazon sales to 5–6K units/month",
            "Built full-funnel strategy from awareness to repeat purchase",
        ],
    },
    {
        "title": "Digital Marketing Executive",
        "company": "HCG Oncology",
        "dates": "2018 – 2019",
        "bullets": [
            "Achieved 5% conversion rate and 60+ qualified leads/month via SKAG structure",
            "Reduced CPL by 38% through iterative A/B testing and landing page optimisation",
        ],
    },
]

_DEFAULT_SKILLS = [
    "Google Ads", "Meta Ads", "Programmatic", "DV360", "Trade Desk",
    "Amazon Ads", "Performance Marketing", "SKAG Structure",
    "Budget Management (₹1Cr+)", "Team Leadership", "Analytics",
    "Data Studio / Looker", "A/B Testing", "LTV/CAC Optimisation",
    "International Campaigns", "E-commerce Growth",
]

_DEFAULT_EDUCATION = "B.Com (Hons) | University of Calcutta"
_DEFAULT_CERTIFICATIONS = [
    "Google Ads Certified", "Meta Blueprint Certified",
    "HubSpot Inbound Marketing", "Amazon Advertising Accredited",
]


class PDFGenerator:
    """Generates ATS-optimized PDF CVs tailored to specific job descriptions."""

    def __init__(self):
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    def generate_cv(self, cv_data: dict, company: str, role: str) -> str:
        """Render tailored CV to PDF and return the file path.

        cv_data keys (from gemini_client.generate_cv):
            summary              — professional summary paragraph
            experience_bullets   — list of tailored achievement bullets
            skills_to_highlight  — list of skills strings
            keywords_injected    — extra ATS keywords (shown in skills)
        """
        html = self._build_html(cv_data)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        slug = "".join(c if c.isalnum() else "-" for c in company.lower())[:20]
        filename = f"Aishik_Dutta_CV_{slug}_{timestamp}.pdf"
        output_path = OUTPUT_DIR / filename

        try:
            import shutil
            from playwright.sync_api import sync_playwright

            _chromium = "/nix/store/qa9cnw4v5xkxyip6mb9kxqfq1z4x2dx1-chromium-138.0.7204.100/bin/chromium"
            _launch_kwargs: dict = {"headless": True}
            if os.path.exists(_chromium):
                _launch_kwargs["executable_path"] = _chromium
            elif shutil.which("chromium"):
                _launch_kwargs["executable_path"] = shutil.which("chromium")

            with sync_playwright() as p:
                browser = p.chromium.launch(**_launch_kwargs)
                page = browser.new_page()
                page.set_content(html, wait_until="networkidle")
                page.pdf(
                    path=str(output_path),
                    format="A4",
                    print_background=True,
                    margin={"top": "20mm", "right": "18mm", "bottom": "20mm", "left": "18mm"},
                )
                browser.close()
        except Exception as exc:
            logger.error(f"PDF generation failed: {exc}")
            raise

        logger.info(f"PDF generated: {output_path}")
        return str(output_path)

    # ── HTML builder ──────────────────────────────────────────────────────────

    def _build_html(self, cv_data: dict) -> str:
        summary = cv_data.get("summary", "")
        tailored_bullets = cv_data.get("experience_bullets", [])
        skills = list(cv_data.get("skills_to_highlight", _DEFAULT_SKILLS))
        extra_kw = cv_data.get("keywords_injected", [])
        for kw in extra_kw:
            if kw not in skills:
                skills.append(kw)

        # Merge tailored bullets into experience (top role gets them)
        experience = [dict(e) for e in _DEFAULT_EXPERIENCE]
        if tailored_bullets and experience:
            experience[0]["bullets"] = tailored_bullets[:6]

        skills_html = "".join(f'<span class="skill">{s}</span>' for s in skills)
        jobs_html = "".join(self._render_job(j) for j in experience)
        certs = ", ".join(_DEFAULT_CERTIFICATIONS)

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: -apple-system, 'Helvetica Neue', Arial, sans-serif;
    font-size: 10.5pt;
    line-height: 1.55;
    color: #1a1a1a;
  }}
  .header {{
    text-align: center;
    margin-bottom: 16px;
    border-bottom: 2px solid #2563eb;
    padding-bottom: 12px;
  }}
  .name {{ font-size: 22pt; font-weight: 700; color: #1e3a5f; }}
  .contact {{ font-size: 9.5pt; color: #4b5563; margin-top: 6px; }}
  .section {{ margin-bottom: 14px; }}
  .section-title {{
    font-size: 10pt; font-weight: 700; color: #2563eb;
    text-transform: uppercase; letter-spacing: 0.8px;
    border-bottom: 1px solid #e5e7eb;
    padding-bottom: 3px; margin-bottom: 8px;
  }}
  .summary {{ font-size: 10pt; color: #374151; }}
  .job {{ margin-bottom: 10px; }}
  .job-header {{ display: flex; justify-content: space-between; align-items: baseline; }}
  .job-title {{ font-weight: 600; }}
  .job-company {{ color: #4b5563; }}
  .job-date {{ color: #6b7280; font-size: 9.5pt; white-space: nowrap; }}
  ul {{ padding-left: 16px; margin-top: 4px; }}
  li {{ margin-bottom: 2px; font-size: 10pt; }}
  .skills {{ display: flex; flex-wrap: wrap; gap: 6px; }}
  .skill {{
    background: #eff6ff; color: #1e40af;
    padding: 3px 8px; border-radius: 3px; font-size: 9pt;
  }}
  .edu {{ font-size: 10pt; color: #374151; }}
</style>
</head>
<body>
  <div class="header">
    <div class="name">Aishik Dutta</div>
    <div class="contact">{_CONTACT}</div>
  </div>

  <div class="section">
    <div class="section-title">Professional Summary</div>
    <div class="summary">{summary}</div>
  </div>

  <div class="section">
    <div class="section-title">Professional Experience</div>
    {jobs_html}
  </div>

  <div class="section">
    <div class="section-title">Core Skills</div>
    <div class="skills">{skills_html}</div>
  </div>

  <div class="section">
    <div class="section-title">Education &amp; Certifications</div>
    <p class="edu">{_DEFAULT_EDUCATION}</p>
    <p class="edu" style="margin-top:4px;">{certs}</p>
  </div>
</body>
</html>"""

    def _render_job(self, job: dict) -> str:
        bullets = "".join(f"<li>{b}</li>" for b in job.get("bullets", []))
        return f"""<div class="job">
  <div class="job-header">
    <div>
      <span class="job-title">{job.get('title', '')}</span>
      <span class="job-company"> | {job.get('company', '')}</span>
    </div>
    <span class="job-date">{job.get('dates', '')}</span>
  </div>
  <ul>{bullets}</ul>
</div>"""


# ── Singleton ─────────────────────────────────────────────────────────────────
pdf_generator = PDFGenerator()
