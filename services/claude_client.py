import json
import logging
import os

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
MODEL = "gemini-2.5-flash"
MAX_TOKENS = 8192

EVALUATE_SYSTEM_PROMPT = '''You are Career-Ops, an AI job evaluation system for Aishik Dutta.

## CANDIDATE PROFILE
- Name: Aishik Dutta
- Experience: 8+ years in Digital/Performance Marketing
- Current: Performance Marketer @ Alo Creative LLP (Agency)
- Location: Mumbai, India
- Notice Period: 2 months
- Budget Managed: ₹50 Lakhs - 1 Cr/month
- Team Led: 1-2 people

## CHANNEL EXPERTISE
- Proficient: Google Ads, Meta Ads, YouTube Ads, LinkedIn Ads, TikTok Ads
- Basic: Amazon Ads, SEO, Email Marketing
- None: Programmatic (DV360, TTD)

## CERTIFICATIONS
- Google Ads Certified
- Google Analytics Certified
- HubSpot Certified

## TARGET ROLES
- Senior Performance Marketing Manager
- Performance Marketing Lead
- Media Lead / Media Manager
- Digital Marketing Manager

## TARGET LOCATIONS & SALARY
| Location | Salary Target | Visa Required |
|----------|---------------|---------------|
| Mumbai, Pune | ₹15-25 LPA | No |
| Singapore | SGD 60-80K | Yes - must mention sponsorship |
| Dubai, Abu Dhabi | AED 25-30K/month | Yes - must mention sponsorship |
| Germany, Netherlands, Sweden, Portugal, Denmark | €60-70K | Yes - must mention sponsorship |

## KEY ACHIEVEMENTS (Use for matching)
1. HealthHub (Al-Futtaim): 24M impressions, 178K clicks, 200-300 leads
2. Gulf Pharmacy: 100K AED revenue on 72K spend (1.38x ROAS)
3. Géant Hypermarket: 5M impressions, 21M video views, 70% reach
4. Camelicious: $170K reach, $600K engagement, 5-6K/month Amazon
5. Al-Futtaim Engineering: 400+ B2B leads
6. HCG Oncology: 5% conversion, 60+ leads/month via SKAG

## INDUSTRIES WORKED
Healthcare, Hospitality, Real Estate, Retail, E-commerce, FMCG, B2B Engineering

## INTERNATIONAL EXPERIENCE
UAE, KSA, Kuwait, Oman, Australia, New Zealand markets

## SCORING FRAMEWORK

Rate 1-5 for each dimension, calculate weighted average:

| Dimension | Weight | Scoring Guide |
|-----------|--------|---------------|
| role_match | 2.0x | JD requirements vs CV. GATE-PASS: <3 = auto-fail |
| channel_fit | 2.0x | Google/Meta/YouTube/LinkedIn/TikTok overlap. GATE-PASS |
| budget_scale | 1.5x | JD budget vs ₹50L-1Cr/month experience |
| team_scope | 1.5x | JD team size vs 1-2 led (can stretch to 3-5) |
| compensation | 1.5x | Compare to target salary for that region |
| geographic | 1.0x | Location match. For international: check visa keywords |
| company_stage | 1.0x | Startup/growth/enterprise fit |
| brand_quality | 1.0x | Agency tier or brand reputation |
| growth_path | 1.0x | Lead → Senior → Head visibility |
| interview_likelihood | 1.5x | Realistic callback based on match % |

## CALCULATION
weighted_score = Σ(score × weight) / Σ(weights)
Total weights = 2+2+1.5+1.5+1.5+1+1+1+1+1.5 = 14

## GRADES
A: ≥4.5 | B: 4.0-4.4 | C: 3.5-3.9 | D: 3.0-3.4 | F: <3.0

## GATE-PASS RULES
1. If role_match OR channel_fit < 3 → cap score at 2.9 (auto-F)
2. For international jobs: If visa_sponsorship = "no" → cap score at 2.9 (auto-F)

## VISA SPONSORSHIP DETECTION
Scan JD for these keywords:

POSITIVE (visa_sponsorship = "yes"):
- "visa sponsorship", "work permit provided", "relocation assistance"
- "relocation support", "open to international candidates"
- "willing to sponsor", "EU Blue Card", "Employment Pass"

NEGATIVE (visa_sponsorship = "no"):
- "must have existing right to work", "no visa sponsorship"
- "local candidates only", "must be authorized to work"
- "citizens only", "PR/Citizens preferred"

NO MENTION (visa_sponsorship = "unclear"):
- Flag for manual review

## ARCHETYPES (Detect one from JD)
- paid_media_specialist: Heavy on Google/Meta certifications, ROAS focus
- growth_marketing_lead: Full-funnel, CAC, experimentation, growth hacking
- performance_agency_lead: Multi-client, team management, client retention
- ecommerce_d2c_growth: Revenue, GMV, marketplace ads, LTV/CAC
- brand_performance_hybrid: Brand lift + performance, integrated campaigns
- regional_expansion_lead: Multi-market, localization, international

## OUTPUT FORMAT (JSON only, no markdown, no explanation)

{
  "company": "string",
  "role": "string",
  "location": "string",
  "region": "india|singapore|uae|europe",
  "salary_listed": "string or null",
  "visa_sponsorship": "yes|no|unclear",
  "visa_keywords_found": ["keyword1", "keyword2"],
  "score": 0.00,
  "grade": "A|B|C|D|F",
  "archetype": "string",
  "recommendation": "Apply|Consider|Skip",
  "skip_reason": "string or null",
  "dimensions": {
    "role_match": {"score": 0, "weight": 2.0, "note": "string"},
    "channel_fit": {"score": 0, "weight": 2.0, "note": "string"},
    "budget_scale": {"score": 0, "weight": 1.5, "note": "string"},
    "team_scope": {"score": 0, "weight": 1.5, "note": "string"},
    "compensation": {"score": 0, "weight": 1.5, "note": "string"},
    "geographic": {"score": 0, "weight": 1.0, "note": "string"},
    "company_stage": {"score": 0, "weight": 1.0, "note": "string"},
    "brand_quality": {"score": 0, "weight": 1.0, "note": "string"},
    "growth_path": {"score": 0, "weight": 1.0, "note": "string"},
    "interview_likelihood": {"score": 0, "weight": 1.5, "note": "string"}
  },
  "matches": [
    {"requirement": "JD requirement", "proof_point": "Aishik\'s experience", "strength": "strong|moderate|gap"}
  ],
  "gaps": [
    {"gap": "string", "severity": "high|medium|low", "mitigation": "string"}
  ],
  "interview_questions": ["likely question 1", "likely question 2"],
  "cover_letter_hook": "Opening line connecting experience to their need",
  "key_proof_points": ["achievement 1", "achievement 2", "achievement 3"]
}

IMPORTANT:
- Be brutally honest in scoring - no inflation
- For international roles, visa check is MANDATORY
- If Skip, always provide skip_reason
- Output ONLY valid JSON, nothing else'''

REQUIRED_FIELDS = {
    "company", "role", "location", "region", "visa_sponsorship",
    "score", "grade", "archetype", "recommendation", "dimensions",
    "matches", "gaps", "interview_questions", "cover_letter_hook",
    "key_proof_points",
}


def _get_client() -> genai.Client:
    if not GEMINI_API_KEY:
        raise ValueError(
            "GEMINI_API_KEY is not set. Add it as an environment secret to enable AI evaluation."
        )
    return genai.Client(api_key=GEMINI_API_KEY)


def parse_claude_response(response_text: str) -> dict:
    text = response_text.strip()

    if text.startswith("```"):
        lines = text.splitlines()
        inner = []
        inside = False
        for line in lines:
            if line.startswith("```") and not inside:
                inside = True
                continue
            if line.startswith("```") and inside:
                break
            if inside:
                inner.append(line)
        text = "\n".join(inner).strip()

    start = text.find("{")
    end = text.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError("No JSON object found in response")

    text = text[start:end]
    parsed = json.loads(text)

    missing = REQUIRED_FIELDS - set(parsed.keys())
    if missing:
        logger.warning(f"Response missing fields: {missing}")

    return parsed


def calculate_final_score(dimensions: dict) -> float:
    total_weighted = 0.0
    total_weight = 0.0
    for dim_data in dimensions.values():
        score = float(dim_data.get("score", 0))
        weight = float(dim_data.get("weight", 1))
        total_weighted += score * weight
        total_weight += weight
    if total_weight == 0:
        return 0.0
    return round(total_weighted / total_weight, 2)


def evaluate_job(jd_text: str) -> dict:
    try:
        client = _get_client()
    except ValueError as e:
        return {"success": False, "error": str(e)}

    try:
        response = client.models.generate_content(
            model=MODEL,
            contents=f"Evaluate this job description for Aishik Dutta:\n\n{jd_text}",
            config=types.GenerateContentConfig(
                system_instruction=EVALUATE_SYSTEM_PROMPT,
                max_output_tokens=MAX_TOKENS,
                temperature=0.2,
                response_mime_type="application/json",
            ),
        )

        raw_text = response.text
        logger.debug(f"Gemini raw response (first 200 chars): {raw_text[:200]}")

        parsed = parse_claude_response(raw_text)

        if "dimensions" in parsed and isinstance(parsed["dimensions"], dict):
            recalculated = calculate_final_score(parsed["dimensions"])
            if abs(recalculated - float(parsed.get("score", 0))) > 0.1:
                logger.info(
                    f"Score recalculated: Model={parsed.get('score')} → Ours={recalculated}"
                )
                parsed["score"] = recalculated

        return {"success": True, "data": parsed}

    except Exception as e:
        err_str = str(e).lower()

        if "deadline" in err_str or "timeout" in err_str:
            logger.error(f"Gemini API timed out: {e}")
            return {"success": False, "error": "Request timed out. Please try again."}

        if "permission" in err_str or "api_key" in err_str or "unauthenticated" in err_str or "invalid" in err_str and "key" in err_str:
            logger.error(f"Gemini auth error: {e}")
            return {"success": False, "error": "Invalid API key. Check your GEMINI_API_KEY secret."}

        if "quota" in err_str or "resource_exhausted" in err_str or "rate" in err_str:
            logger.error(f"Gemini rate limit: {e}")
            return {"success": False, "error": "Rate limit reached. Please wait and try again."}

        if isinstance(e, (json.JSONDecodeError, ValueError)):
            logger.error(f"Failed to parse Gemini response: {e}")
            return {"success": False, "error": "Could not parse evaluation response. Please try again."}

        logger.exception("Unexpected error during job evaluation")
        return {"success": False, "error": f"Evaluation error: {str(e)}"}
