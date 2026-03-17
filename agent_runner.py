from app.analysis.agent import FOREX_ANALYST_SYSTEM_PROMPT, ForexAnalystAgent

__all__ = ["FOREX_ANALYST_SYSTEM_PROMPT", "ForexAnalystAgent"]


if __name__ == "__main__":
    print("ForexAnalystAgent module loaded.")
    print("System prompt length:", len(FOREX_ANALYST_SYSTEM_PROMPT), "characters")
    print("\nThis module integrates:")
    print("  Option 1: Deep system prompt (permanent identity + rules)")
    print("  Option 2: RAG pipeline (dynamic knowledge retrieval)")
    print("\nImport and use ForexAnalystAgent in main.py to run full analysis.")
