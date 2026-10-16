"""Deprecated launcher alias. Local Chatterbox installation is left untouched."""
from run_app import main

if __name__ == "__main__":
    print("The Chatterbox product launcher is retired. Use python run_app.py for OpenAI narration.")
    raise SystemExit(main())
