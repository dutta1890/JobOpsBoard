"""
interview_prep.py — STAR+R story bank and interview preparation for Career-Ops.

Stories are persisted to data/star_stories.json and searched by keyword/question type.
Uses the same google-genai SDK and model as the rest of the project.
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

STORIES_FILE = Path("data/star_stories.json")

_STAR_SYSTEM_PROMPT = """You are an executive interview coach helping Aishik Dutta
prepare compelling STAR+R stories for senior performance marketing interviews.

Aishik's key achievements (draw on these):
- HealthHub: 24M impressions, 178K clicks, 200-300 leads/month via SKAG structure
- Gulf Pharmacy: 1.38x ROAS — 100K AED revenue on 72K AED spend
- Géant: 5M impressions, 21M video views, 70% reach across UAE
- Camelicious: $600K engagement, 5-6K Amazon sales/month
- HCG Oncology: 5% conversion, 60+ leads/month
- Managed ₹50L-1Cr monthly budgets, teams in India, UAE, Kuwait, Australia, NZ

Output ONLY valid JSON. No markdown. No explanation."""

_STAR_USER_TEMPLATE = """Generate a STAR+R interview story for:

ACHIEVEMENT: {achievement}
QUESTION TYPE: {question_type}

Return this JSON structure:
{{
  "title": "Short memorable title (5-7 words)",
  "question_types": ["Tell me about a time you...", "Give me an example of..."],
  "situation": "Context and challenge (2-3 sentences)",
  "task": "Your specific responsibility (1-2 sentences)",
  "actions": ["Step 1", "Step 2", "Step 3", "Step 4"],
  "results": ["Quantified outcome 1", "Quantified outcome 2", "Quantified outcome 3"],
  "reflection": "What you learned or would do differently (1-2 sentences)",
  "keywords": ["keyword1", "keyword2", "keyword3", "keyword4", "keyword5"]
}}"""


class InterviewPrep:
    """Manages STAR+R interview stories and interview preparation."""

    def __init__(self):
        self.stories: list[dict] = self._load_stories()

    # ── Public API ─────────────────────────────────────────────────────────────

    def generate_star_story(self, achievement: str, question_type: str) -> dict:
        """Generate a STAR+R story from an achievement and question type.

        Uses Gemini 2.5 Flash. Saves the story to disk on success.
        """
        try:
            from google import genai
            from google.genai import types

            api_key = os.environ.get("GEMINI_API_KEY", "")
            if not api_key:
                return {"error": "GEMINI_API_KEY not configured"}

            client = genai.Client(api_key=api_key)
            prompt = _STAR_USER_TEMPLATE.format(
                achievement=achievement, question_type=question_type
            )

            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=_STAR_SYSTEM_PROMPT,
                    max_output_tokens=2048,
                    temperature=0.5,
                    response_mime_type="application/json",
                ),
            )

            story = self._parse_response(response.text)
            story["achievement"] = achievement
            story["question_type_input"] = question_type
            story["created_at"] = datetime.now().isoformat()

            self.stories.append(story)
            self._save_stories()
            logger.info(f"STAR story generated: {story.get('title', '?')}")
            return story

        except Exception as exc:
            logger.error(f"STAR story generation error: {exc}")
            return {"error": str(exc)}

    def get_stories_for_question(self, question: str) -> list[dict]:
        """Return up to 3 stories most relevant to the interview question."""
        q_lower = question.lower()
        relevant: list[dict] = []

        for story in self.stories:
            # Match on keywords
            if any(kw.lower() in q_lower for kw in story.get("keywords", [])):
                relevant.append(story)
                continue
            # Match on question_types
            for qt in story.get("question_types", []):
                if any(w in q_lower for w in qt.lower().split() if len(w) > 3):
                    relevant.append(story)
                    break

        return relevant[:3]

    def get_all_stories(self) -> list[dict]:
        return list(self.stories)

    # ── Persistence ────────────────────────────────────────────────────────────

    def _load_stories(self) -> list[dict]:
        try:
            if STORIES_FILE.exists():
                with open(STORIES_FILE) as f:
                    return json.load(f)
        except Exception as exc:
            logger.warning(f"Could not load STAR stories: {exc}")
        return []

    def _save_stories(self) -> None:
        try:
            STORIES_FILE.parent.mkdir(exist_ok=True)
            with open(STORIES_FILE, "w") as f:
                json.dump(self.stories, f, indent=2)
        except Exception as exc:
            logger.warning(f"Could not save STAR stories: {exc}")

    def _parse_response(self, text: str) -> dict:
        text = text.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
        return json.loads(text.strip())


# ── Singleton ──────────────────────────────────────────────────────────────────
interview_prep = InterviewPrep()
