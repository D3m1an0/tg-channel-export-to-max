#!/usr/bin/env python3
"""
Migrate Telegram channel export to MAX messenger channel.
Processes all messages*.html files in order.
"""

import os
import re
import sys
import time
import json
import requests
from bs4 import BeautifulSoup

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_env():
    env_path = os.path.join(BASE_DIR, ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value

_load_env()

TOKEN = os.environ.get("MAX_BOT_TOKEN", "")
_chat_id_raw = os.environ.get("MAX_CHAT_ID", "")
CHAT_ID = int(_chat_id_raw) if _chat_id_raw else None

if not TOKEN:
    sys.exit("Ошибка: задайте MAX_BOT_TOKEN в файле .env")
if CHAT_ID is None:
    sys.exit("Ошибка: задайте MAX_CHAT_ID в файле .env")

API_BASE = "https://botapi.max.ru"

HEADERS = {"Authorization": TOKEN}
MAX_TEXT_LEN = 4000


def get_text_content(text_div):
    if not text_div:
        return ""
    for br in text_div.find_all("br"):
        br.replace_with("\n")
    for a in text_div.find_all("a"):
        a.replace_with(a.get_text())
    return text_div.get_text().strip()


def get_media_path(media_wrap):
    if not media_wrap:
        return None, None

    # Photo
    photo_link = media_wrap.find("a", class_="photo_wrap")
    if photo_link and photo_link.get("href"):
        return "photo", os.path.join(BASE_DIR, photo_link["href"])

    # Video
    video_link = media_wrap.find("a", class_="video_file_wrap")
    if video_link and video_link.get("href"):
        return "video", os.path.join(BASE_DIR, video_link["href"])

    return None, None


def upload_photo(file_path):
    """Two-step photo upload: get signed URL, then upload file."""
    try:
        # Step 1: get signed upload URL
        with open(file_path, "rb") as f:
            resp = requests.post(
                f"{API_BASE}/uploads?type=image",
                headers=HEADERS,
                files={"data": f},
                timeout=60,
            )
        resp.raise_for_status()
        step1 = resp.json()
        upload_url = step1.get("url")
        if not upload_url:
            print(f"  No upload URL: {step1}")
            return None

        # Step 2: upload to signed URL
        with open(file_path, "rb") as f:
            resp2 = requests.post(upload_url, files={"data": f}, timeout=60)
        resp2.raise_for_status()
        photos = resp2.json().get("photos", {})
        if photos:
            token = photos[list(photos.keys())[0]].get("token")
            return token
        print(f"  No token in response: {resp2.json()}")
        return None
    except Exception as e:
        print(f"  Photo upload error: {e}")
        return None


def upload_video(file_path):
    """Two-step video upload."""
    try:
        # Step 1
        with open(file_path, "rb") as f:
            resp = requests.post(
                f"{API_BASE}/uploads?type=video",
                headers=HEADERS,
                files={"data": f},
                timeout=600,
            )
        resp.raise_for_status()
        step1 = resp.json()

        # Direct token returned
        if "token" in step1:
            return step1["token"]

        upload_url = step1.get("url")
        if not upload_url:
            print(f"  No upload URL: {step1}")
            return None

        # Step 2
        with open(file_path, "rb") as f:
            resp2 = requests.post(upload_url, files={"data": f}, timeout=600)
        resp2.raise_for_status()
        data = resp2.json()
        return data.get("token")
    except Exception as e:
        print(f"  Video upload error: {e}")
        return None


def send_message(text, attachments=None):
    """Send to MAX channel, auto-splitting text > 4000 chars. Retries on timeout."""
    url = f"{API_BASE}/messages?chat_id={CHAT_ID}"
    text = text or ""

    def _post(t, att, retries=3):
        payload = {}
        if t:
            payload["text"] = t
        if att:
            payload["attachments"] = att
        for attempt in range(retries):
            try:
                r = requests.post(url, headers=HEADERS, json=payload, timeout=60)
                try:
                    return r.status_code, r.json()
                except Exception:
                    return r.status_code, {"raw": r.text}
            except requests.exceptions.Timeout:
                print(f"  Timeout on attempt {attempt+1}, retrying...", flush=True)
                time.sleep(3)
            except Exception as e:
                print(f"  Request error on attempt {attempt+1}: {e}, retrying...", flush=True)
                time.sleep(3)
        return 500, {"error": "max retries exceeded"}

    if not text and not attachments:
        return 400, {"error": "nothing to send"}

    if len(text) <= MAX_TEXT_LEN:
        return _post(text or None, attachments)

    # Split into chunks, send first with attachments
    chunks = [text[i:i+MAX_TEXT_LEN] for i in range(0, len(text), MAX_TEXT_LEN)]
    status, data = _post(chunks[0], attachments)
    for chunk in chunks[1:]:
        time.sleep(0.5)
        status, data = _post(chunk, None)
    return status, data


def parse_html_file(html_path):
    """Parse one messages HTML file and return list of message dicts."""
    with open(html_path, "r", encoding="utf-8") as f:
        soup = BeautifulSoup(f.read(), "html.parser")

    result = []
    for msg in soup.find_all("div", class_="message default clearfix"):
        msg_id = msg.get("id", "")
        date_div = msg.find("div", class_="pull_right date details")
        date_str = date_div.get("title", "") if date_div else ""
        text_div = msg.find("div", class_="text")
        text = get_text_content(text_div)
        media_wrap = msg.find("div", class_="media_wrap clearfix")
        media_type, media_path = get_media_path(media_wrap)
        result.append({
            "id": msg_id,
            "date": date_str,
            "text": text,
            "media_type": media_type,
            "media_path": media_path,
        })
    return result


def parse_all_messages():
    """Collect messages from all messages*.html files in order."""
    import glob
    # Find all files: messages.html, messages2.html, messages3.html ...
    pattern = os.path.join(BASE_DIR, "messages*.html")
    files = sorted(glob.glob(pattern))
    print(f"Found HTML files: {[os.path.basename(f) for f in files]}")
    all_msgs = []
    for f in files:
        msgs = parse_html_file(f)
        print(f"  {os.path.basename(f)}: {len(msgs)} messages")
        all_msgs.extend(msgs)
    return all_msgs


def migrate(start_from=1):
    print("Parsing all messages from Telegram export...")
    messages = parse_all_messages()
    total = len(messages)
    print(f"\nTotal messages to send: {total}")
    if start_from > 1:
        print(f"Resuming from message #{start_from}\n")
    else:
        print()

    errors = []

    for i, msg in enumerate(messages):
        if i + 1 < start_from:
            continue
        print(f"[{i+1}/{total}] {msg['id']} | {msg['date']}", flush=True)

        text = msg["text"]
        attachments = []

        if msg["media_type"] == "photo" and msg["media_path"]:
            path = msg["media_path"]
            if os.path.exists(path):
                print(f"  Uploading photo: {os.path.basename(path)}", flush=True)
                token = upload_photo(path)
                if token:
                    attachments.append({"type": "image", "payload": {"token": token}})
                    print(f"  Photo OK", flush=True)
                else:
                    print(f"  Photo upload FAILED — sending text only", flush=True)
            else:
                print(f"  Photo not found: {path}", flush=True)

        elif msg["media_type"] == "video" and msg["media_path"]:
            path = msg["media_path"]
            if os.path.exists(path):
                size_mb = os.path.getsize(path) / 1024 / 1024
                print(f"  Uploading video: {os.path.basename(path)} ({size_mb:.1f} MB)", flush=True)
                token = upload_video(path)
                if token:
                    attachments.append({"type": "video", "payload": {"token": token}})
                    print(f"  Video OK", flush=True)
                else:
                    print(f"  Video upload FAILED — sending text only", flush=True)
            else:
                print(f"  Video not found: {path}", flush=True)

        status, resp = send_message(text, attachments if attachments else None)
        if status == 200:
            print(f"  Sent OK | {len(text)} chars", flush=True)
        else:
            print(f"  ERROR {status}: {resp}", flush=True)
            errors.append({"index": i+1, "id": msg["id"], "status": status, "resp": resp})

        time.sleep(0.8)

    print(f"\n{'='*50}")
    print(f"Done! {total - len(errors)}/{total} sent successfully.")
    if errors:
        print(f"\n{len(errors)} errors:")
        for e in errors:
            print(f"  [{e['index']}] {e['id']}: {e['status']} {e['resp']}")


if __name__ == "__main__":
    start = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    migrate(start_from=start)
