import logging
from config import ANTHROPIC_API_KEY, MODEL, MAX_TOKENS

logger = logging.getLogger(__name__)


def get_claude_client():
    try:
        import anthropic
        if not ANTHROPIC_API_KEY:
            raise ValueError("ANTHROPIC_API_KEY is not set")
        return anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    except ImportError:
        logger.error("anthropic package not installed")
        raise
    except Exception as e:
        logger.error(f"Failed to initialize Claude client: {e}")
        raise


def evaluate_job(jd_text: str, profile: dict) -> dict:
    try:
        client = get_claude_client()

        profile_summary = f"""
Name: {profile.get('name', 'N/A')}
Experience: {profile.get('experience', 'N/A')}
Skills: {', '.join(profile.get('skills', []))}
Target roles: {', '.join(profile.get('target_roles', []))}
"""

        prompt = f"""You are a career advisor specializing in performance marketing roles.

Evaluate this job description against the candidate profile and provide:
1. A fit score from 0-100
2. Key matching skills/requirements
3. Gaps or concerns
4. A recommendation (apply / skip / reach)
5. A brief personalized talking point for the cover letter

CANDIDATE PROFILE:
{profile_summary}

JOB DESCRIPTION:
{jd_text}

Respond in JSON format:
{{
  "score": <0-100>,
  "match_highlights": ["..."],
  "gaps": ["..."],
  "recommendation": "apply|skip|reach",
  "talking_point": "...",
  "summary": "..."
}}"""

        message = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )

        import json
        content = message.content[0].text
        start = content.find("{")
        end = content.rfind("}") + 1
        result = json.loads(content[start:end])
        return {"success": True, "data": result}

    except Exception as e:
        logger.error(f"Claude evaluation failed: {e}")
        return {"success": False, "error": str(e)}
