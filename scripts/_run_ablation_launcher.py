"""Launcher: loads .env, forces LLM_PROVIDER=gemini, then runs the ablation."""
import os
from pathlib import Path

# Load .env from project root before any other imports
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass  # dotenv not installed — rely on env vars already set

# Override provider to Gemini (Gemini key is present, OpenAI key is not)
os.environ.setdefault("LLM_PROVIDER", "gemini")

# Now run the real ablation
from scripts.run_retrieval_ablation import main
main()
