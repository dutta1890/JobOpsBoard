import os
from dotenv import load_dotenv

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
MODEL = "gemini-2.5-flash"
MAX_TOKENS = 4096
DATABASE_URI = "sqlite:///career_ops.db"

class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key-change-in-prod")
    SQLALCHEMY_DATABASE_URI = DATABASE_URI
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    GEMINI_API_KEY = GEMINI_API_KEY
    MODEL = MODEL
    MAX_TOKENS = MAX_TOKENS
