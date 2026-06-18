"""
agents/main_agent.py — CLI Main Agent
======================================
Standalone terminal AI agent backed by NVIDIA API + CryptoTools.
"""

import sys
import os

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in [ROOT_DIR, os.path.join(ROOT_DIR, "agents"), os.path.join(ROOT_DIR, "tools"), os.path.join(ROOT_DIR, "core")]:
    if _d not in sys.path:
        sys.path.insert(0, _d)

import json
import requests
from crypto_tools import CryptoTools
from chat_history_manager import ChatHistoryManager

import config as _cfg
NVIDIA_API_KEY = _cfg.NVIDIA_API_KEY
NVIDIA_URL     = _cfg.NVIDIA_URL
MODEL          = _cfg.MODEL


class MainAgent:
    def __init__(self):
        self.tools_handler   = CryptoTools()
        self.history_manager = ChatHistoryManager()

        self.system_prompt = """You are the Lead Crypto Trading Strategist. Your goal is to provide DIRECT, ACTIONABLE advice to the user.
Always use tools to fetch real-time market data, technical signals, news, and point history before giving advice.

STRICT RULES:
1. DO NOT be vague. Tell the user EXACTLY what to do (e.g., "Buy now", "Sell now", "Hold", "Wait for entry at $X").
2. Give CLEAR instructions (e.g., "Buy BTC at $60,000").
3. Use a confident, professional tone.
4. When you receive data from tools, analyze it and translate it into a specific trading action.
5. Response Language: English.

Example Response Style:
- "Buy BTC now. Technical indicators show a strong buy signal."
- "Sell now. Market pressure is increasing."
"""

        self.tools_schema = [
            {
                "type": "function",
                "function": {
                    "name": "get_crypto_history",
                    "description": "Get the last 30 days of price history for a specific cryptocurrency.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "symbol": {"type": "string", "description": "The ticker symbol of the coin (e.g., BTC, ETH)."}
                        },
                        "required": ["symbol"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "get_latest_news",
                    "description": "Get latest analyzed cryptocurrency news from news database.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "symbol": {"type": "string", "description": "Optional: Filter news for specific coin ticker (e.g. BTC, ETH, SOL)."},
                            "limit":  {"type": "integer", "description": "How many news items to return. Default 5, max 50."}
                        }
                    },
                    "required": []
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "fetch_and_analyze_asset_news",
                    "description": "Search and analyze real-time news for a specific cryptocurrency.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "symbol": {"type": "string", "description": "The ticker symbol (e.g., BTC, ETH)."}
                        },
                        "required": ["symbol"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "get_technical_analysis",
                    "description": "Get live technical analysis snapshot for a specific coin.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "symbol": {"type": "string", "description": "Coin ticker (e.g. BTC, ETH, SOL)."}
                        },
                        "required": ["symbol"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "get_all_technicals",
                    "description": "Get technical analysis snapshots for ALL tracked coins at once.",
                    "parameters": {"type": "object", "properties": {}}
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "get_current_points",
                    "description": "Get the master sentiment score for a specific coin.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "symbol": {"type": "string", "description": "The ticker symbol (e.g., BTC, ETH)."}
                        },
                        "required": ["symbol"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "get_whale_data",
                    "description": "Get whale market intelligence — large wallet movements, danger level.",
                    "parameters": {"type": "object", "properties": {}}
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "get_market_summary",
                    "description": "Get comprehensive market overview.",
                    "parameters": {"type": "object", "properties": {}}
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "get_trade_setup",
                    "description": "Generate a complete trade setup for a specific cryptocurrency.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "symbol":    {"type": "string", "description": "Coin ticker (e.g. BTC, ETH, SOL)."},
                            "timeframe": {"type": "string", "description": "Timeframe: 15m, 1h, 4h, 1d. Default: 4h"}
                        },
                        "required": ["symbol"]
                    }
                }
            }
        ]

    def execute_function(self, name: str, args: dict) -> dict:
        print(f"[TOOL CALL] {name} with args: {args}")
        try:
            if name == "get_crypto_history":
                result = self.tools_handler.get_crypto_history(args.get("symbol"))
            elif name == "get_latest_news":
                result = self.tools_handler.get_latest_news(symbol=args.get("symbol"), limit=args.get("limit", 5))
            elif name == "fetch_and_analyze_asset_news":
                result = self.tools_handler.fetch_and_analyze_asset_news(args.get("symbol"))
            elif name == "get_technical_analysis":
                result = self.tools_handler.get_technical_analysis(args.get("symbol"))
            elif name == "get_all_technicals":
                result = self.tools_handler.get_all_technicals()
            elif name == "get_current_points":
                result = self.tools_handler.get_current_points(args.get("symbol"))
            elif name == "get_whale_data":
                result = self.tools_handler.get_whale_data()
            elif name == "get_market_summary":
                result = self.tools_handler.get_market_summary()
            elif name == "get_trade_setup":
                result = self.tools_handler.get_trade_setup(symbol=args.get("symbol"), timeframe=args.get("timeframe", "4h"))
            else:
                result = f"Function '{name}' not found."
            return {"success": True, "tool": name, "arguments": args, "response": result}
        except Exception as e:
            return {"success": False, "tool": name, "arguments": args, "error": str(e)}

    def _build_messages_with_history(self, user_input: str) -> list:
        recent_history = self.history_manager.get_recent_history(15)
        messages = [{"role": "system", "content": self.system_prompt}]

        for h in recent_history:
            if h["role"] == "user":
                messages.append({"role": "user", "content": h["content"]})
            elif h["role"] == "assistant":
                messages.append({"role": "assistant", "content": h["content"]})
            elif h["role"] == "tool_interaction":
                content = h["content"]
                if isinstance(content, str):
                    try:
                        content = json.loads(content)
                    except:
                        pass
                tool_context = f"[System: Previously called {content.get('tool', 'unknown')} tool]"
                messages.append({"role": "system", "content": tool_context})

        messages.append({"role": "user", "content": user_input})
        return messages

    def chat(self, user_input: str) -> str:
        self.history_manager.add_message("user", user_input)
        messages = self._build_messages_with_history(user_input)

        headers = {
            "Authorization": f"Bearer {NVIDIA_API_KEY}",
            "Content-Type":  "application/json"
        }

        try:
            payload = {
                "model":       MODEL,
                "messages":    messages,
                "tools":       self.tools_schema,
                "tool_choice": "auto",
                "temperature": 0.1,
                "max_tokens":  2000
            }

            print(f"[API REQUEST] Sending to NVIDIA...")
            response = requests.post(NVIDIA_URL, headers=headers, json=payload, timeout=120)
            result   = response.json()

            if "error" in result:
                error_msg = f"API Error: {result['error'].get('message', 'Unknown error')}"
                self.history_manager.add_message("system", error_msg)
                return error_msg

            message = result['choices'][0]['message']
            content = message.get("content", "")

            if not message.get("tool_calls") and "<tool_call>" in (content or ""):
                import re
                tool_calls = []
                pattern = r"<tool_call>\s*<function=(\w+)>(.*?)</function>\s*</tool_call>"
                for match in re.finditer(pattern, content, re.DOTALL):
                    func_name = match.group(1)
                    params = {}
                    for p in re.finditer(r"<parameter=(\w+)>(.*?)</parameter>", match.group(2), re.DOTALL):
                        params[p.group(1)] = p.group(2).strip()
                    tool_calls.append({
                        "id": f"call_{len(tool_calls)}",
                        "type": "function",
                        "function": {"name": func_name, "arguments": json.dumps(params)}
                    })
                if tool_calls:
                    message["tool_calls"] = tool_calls

            if message.get("tool_calls"):
                print(f"[TOOL CALLS DETECTED] {len(message['tool_calls'])} tool(s) to execute")

                for tool_call in message["tool_calls"]:
                    self.history_manager.add_message(
                        "tool_request",
                        f"Calling {tool_call['function']['name']}",
                        metadata={"tool": tool_call["function"]["name"], "arguments": tool_call["function"]["arguments"]}
                    )

                messages.append(message)

                for tool_call in message["tool_calls"]:
                    function_name     = tool_call["function"]["name"]
                    function_args     = json.loads(tool_call["function"]["arguments"])
                    function_response = self.execute_function(function_name, function_args)

                    self.history_manager.add_tool_call(
                        tool_name=function_name,
                        arguments=function_args,
                        response=function_response.get("response", function_response.get("error", "No response"))
                    )

                    messages.append({
                        "tool_call_id": tool_call["id"],
                        "role":         "tool",
                        "name":         function_name,
                        "content":      json.dumps(
                            function_response.get("response", function_response.get("error", "")),
                            ensure_ascii=False
                        )
                    })

                print(f"[FINAL REQUEST] Getting AI response with tool results...")
                final_payload = {
                    "model":       MODEL,
                    "messages":    messages,
                    "temperature": 0.7,
                    "max_tokens":  2000
                }
                final_response = requests.post(NVIDIA_URL, headers=headers, json=final_payload, timeout=120)
                final_result   = final_response.json()

                if "error" in final_result:
                    ai_response = f"Error: {final_result['error'].get('message', 'Unknown error')}"
                else:
                    ai_response = final_result['choices'][0]['message']['content']
            else:
                ai_response = message.get('content', '')

            self.history_manager.add_message("assistant", ai_response)
            print(f"[RESPONSE] AI response generated successfully")
            return ai_response

        except requests.exceptions.Timeout:
            error_msg = "Request timeout: NVIDIA API took too long to respond"
            self.history_manager.add_message("system", error_msg)
            return f"Error: {error_msg}"
        except requests.exceptions.ConnectionError:
            error_msg = "Connection error: Cannot reach NVIDIA API"
            self.history_manager.add_message("system", error_msg)
            return f"Error: {error_msg}"
        except Exception as e:
            error_msg = f"Unexpected error: {str(e)}"
            self.history_manager.add_message("system", error_msg)
            return f"Error: {error_msg}"

    def get_chat_history(self) -> list:
        return self.history_manager.get_full_history()

    def clear_chat_history(self):
        self.history_manager.clear_history()
        print("Chat history cleared.")


if __name__ == "__main__":
    agent = MainAgent()
    print("=" * 50)
    print("Main Agent — SQLite-backed tools.")
    print("=" * 50)
