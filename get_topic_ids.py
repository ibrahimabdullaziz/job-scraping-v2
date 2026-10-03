#!/usr/bin/env python3
"""
Helper script to discover Telegram Group ID and Topic Thread IDs.

Run this script and post a message to each topic in your Telegram supergroup.
The script will listen and print the exact configuration to put in your .env or Railway.
"""

import os
import re
import sys
import time
import requests

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

def load_bot_token() -> str:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if token:
        return token.strip().strip("'\"`")
    
    env_file = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_file):
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                match = re.search(r"TELEGRAM_BOT_TOKEN[\s=`'\"*]*([0-9]{8,12}:[a-zA-Z0-9_-]{30,40})", line)
                if match:
                    return match.group(1).strip()
    return ""

def main():
    token = load_bot_token()
    if not token:
        print("\n[!] TELEGRAM_BOT_TOKEN not found in environment or .env file.")
        token = input("Enter your Telegram Bot Token: ").strip().strip("'\"`")
    
    if not token:
        print("Error: No bot token provided.")
        sys.exit(1)

    base_url = f"https://api.telegram.org/bot{token}"

    print(f"\n[*] Checking bot connection...")
    try:
        r = requests.get(f"{base_url}/getMe", timeout=10)
        res = r.json()
        if not res.get("ok"):
            print(f"[X] Bot token is invalid: {res.get('description')}")
            sys.exit(1)
        bot_info = res["result"]
        print(f"[OK] Connected as @{bot_info.get('username')} ({bot_info.get('first_name')})")
    except Exception as e:
        print(f"[X] Network error connecting to Telegram: {e}")
        sys.exit(1)

    print("\n" + "="*60)
    print("INSTRUCTIONS:")
    print("1. Ensure your bot is added to your Telegram supergroup as an ADMIN.")
    print("2. Send a test message into each topic in your supergroup.")
    print("3. Watch this console - detected thread IDs will appear below.")
    print("   (Press Ctrl+C when you have recorded all topic IDs)")
    print("="*60 + "\n", flush=True)

    discovered_topics = {}
    last_update_id = 0
    group_id = None

    # First check past updates
    try:
        r = requests.get(f"{base_url}/getUpdates?limit=100", timeout=10)
        data = r.json().get("result", [])
        for u in data:
            last_update_id = max(last_update_id, u.get("update_id", 0))
            msg = u.get("message") or u.get("channel_post")
            if not msg:
                continue
            chat = msg.get("chat", {})
            thread_id = msg.get("message_thread_id")
            if thread_id and str(chat.get("id", "")).startswith("-100"):
                group_id = chat.get("id")
                topic_title = msg.get("forum_topic_created", {}).get("name") or msg.get("text", f"Thread {thread_id}")
                discovered_topics[thread_id] = {
                    "title": topic_title,
                    "chat_id": group_id,
                    "chat_title": chat.get("title", "Supergroup")
                }
    except Exception as e:
        print(f"Warning checking initial updates: {e}")

    if discovered_topics:
        print(f"[OK] Found {len(discovered_topics)} topic(s) from recent messages:")
        for tid, info in discovered_topics.items():
            print(f"    - Thread ID: {tid} | Supergroup ID: {info['chat_id']} | Text/Topic: {info['title']}")
        print()

    print("[*] Listening for new messages in topics... (Send a message now to any topic)")

    try:
        while True:
            offset = last_update_id + 1 if last_update_id else 0
            url = f"{base_url}/getUpdates?timeout=15&offset={offset}"
            try:
                resp = requests.get(url, timeout=20)
                updates = resp.json().get("result", [])
            except Exception:
                time.sleep(2)
                continue

            for u in updates:
                last_update_id = max(last_update_id, u.get("update_id", 0))
                msg = u.get("message") or u.get("channel_post")
                if not msg:
                    continue
                
                chat = msg.get("chat", {})
                c_id = chat.get("id")
                thread_id = msg.get("message_thread_id")
                
                # Check for forum topic created event
                topic_created = msg.get("forum_topic_created")
                if topic_created:
                    name = topic_created.get("name")
                    print(f"\n[+] NEW TOPIC CREATED: '{name}'")
                    print(f"    Chat ID: {c_id}")
                    print(f"    message_thread_id: {msg.get('message_id')}")
                    discovered_topics[msg.get('message_id')] = {
                        "title": name,
                        "chat_id": c_id,
                        "chat_title": chat.get("title", "")
                    }
                    continue

                if thread_id:
                    text_preview = msg.get("text", "(no text)")[:30]
                    print(f"\n[+] DETECTED TOPIC MESSAGE:")
                    print(f"    Chat Title: {chat.get('title', 'Unknown')}")
                    print(f"    TELEGRAM_GROUP_ID: {c_id}")
                    print(f"    Topic Thread ID (message_thread_id): {thread_id}")
                    print(f"    Message: \"{text_preview}\"")
                    
                    group_id = c_id
                    discovered_topics[thread_id] = {
                        "title": text_preview,
                        "chat_id": c_id,
                        "chat_title": chat.get("title", "")
                    }
                else:
                    if str(c_id).startswith("-100"):
                        print(f"\n[i] Message received in group '{chat.get('title')}' (General / No Thread): Chat ID is {c_id}")
            
            time.sleep(1)

    except KeyboardInterrupt:
        print("\n\nStopped listening.")

    print("\n" + "="*60)
    print("SUMMARY OF DETECTED TOPICS:")
    if group_id:
        print(f"TELEGRAM_GROUP_ID={group_id}")
    for tid, info in discovered_topics.items():
        print(f"# Topic (last text: {info['title']})")
        print(f"TOPIC_...={tid}")
    print("="*60 + "\n")

if __name__ == "__main__":
    main()
