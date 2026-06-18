"""
core/chat_history_manager.py — CLI Chat History Manager
=========================================================
Saves/loads chat history JSON from the project root.
Used only by the standalone main_agent CLI, not the web app.
"""
import json
import os
from datetime import datetime

ROOT_DIR     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HISTORY_FILE = os.path.join(ROOT_DIR, "history.json")


class ChatHistoryManager:
    def __init__(self, file_path=HISTORY_FILE):
        self.file_path = file_path
        self.history   = self._load_history()

    def _load_history(self):
        if os.path.exists(self.file_path):
            try:
                with open(self.file_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                print(f"Error loading chat history: {e}")
        return []

    def add_message(self, role, content, metadata=None):
        entry = {
            "timestamp": datetime.now().isoformat(),
            "role":      role,
            "content":   content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, indent=2)
        }
        if metadata:
            entry["metadata"] = metadata
        self.history.append(entry)
        self._save_history()

    def add_tool_call(self, tool_name, arguments, response):
        entry = {
            "timestamp": datetime.now().isoformat(),
            "role":      "tool_interaction",
            "content":   {
                "tool":      tool_name,
                "arguments": arguments,
                "response":  response
            }
        }
        self.history.append(entry)
        self._save_history()

    def _save_history(self):
        try:
            with open(self.file_path, 'w', encoding='utf-8') as f:
                json.dump(self.history, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"Error saving chat history: {e}")

    def get_recent_history(self, limit=10):
        return self.history[-limit:]

    def get_full_history(self):
        return self.history

    def clear_history(self):
        self.history = []
        self._save_history()


if __name__ == "__main__":
    chm = ChatHistoryManager()
    chm.add_message("user", "Hello!")
    print("Chat History Manager initialized and tested.")
