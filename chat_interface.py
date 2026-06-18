"""
agents/chat_interface.py — CLI Chat Interface
=============================================
Standalone terminal client for the MainAgent.
"""
import sys
import os

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in [ROOT_DIR, os.path.join(ROOT_DIR, "agents"), os.path.join(ROOT_DIR, "tools"), os.path.join(ROOT_DIR, "core")]:
    if _d not in sys.path:
        sys.path.insert(0, _d)

from main_agent import MainAgent


def main():
    agent = MainAgent()
    print("=" * 50)
    print("TrendOracle CLI — type 'quit' to exit.")
    print("=" * 50)
    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break
        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit", "q"):
            print("Goodbye!")
            break
        if user_input.lower() == "clear":
            agent.clear_chat_history()
            print("Chat history cleared.")
            continue
        response = agent.chat(user_input)
        print(f"\nAgent: {response}\n")


if __name__ == "__main__":
    main()
