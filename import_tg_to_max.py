#!/usr/bin/env python3
import argparse
import json
import mimetypes
import os
import re
import time
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen


MAX_TEXT_LIMIT = 4000


def load_env(path: Path = Path(".env")):
    """Загружает переменные из .env файла, если он существует."""
    if not path.exists():
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


@dataclass
class Message:
    source_file: str
    message_id: str
    timestamp: Optional[str] = None
    text: str = ""
    attachments: List[str] = field(default_factory=list)


class TelegramExportParser(HTMLParser):
    def __init__(self, source_file: str):
        super().__init__(convert_charrefs=True)
        self.source_file = source_file
        self.messages: List[Message] = []

        self._div_depth = 0
        self._message_depth = 0
        self._current_message: Optional[Message] = None
        self._in_service_message = False

        self._in_text_div = False
        self._text_div_depth = 0
        self._text_parts: List[str] = []

        self._in_date_div = False
        self._date_div_depth = 0

    @staticmethod
    def _class_tokens(attrs):
        class_attr = attrs.get("class", "")
        return class_attr.split()

    def handle_starttag(self, tag, attrs_list):
        attrs = dict(attrs_list)
        if tag == "div":
            self._div_depth += 1
            class_tokens = self._class_tokens(attrs)
            class_attr = attrs.get("class", "")

            # Enter message root
            if (
                self._current_message is None
                and class_tokens[:1] == ["message"]
                and "default" in class_tokens
                and "clearfix" in class_tokens
            ):
                message_id = attrs.get("id", "")
                self._current_message = Message(source_file=self.source_file, message_id=message_id)
                self._message_depth = self._div_depth
                self._in_service_message = False

            elif (
                self._current_message is None
                and class_tokens[:1] == ["message"]
                and "service" in class_tokens
            ):
                self._in_service_message = True

            if self._current_message is not None:
                if "text" in class_tokens and class_attr.strip() == "text":
                    self._in_text_div = True
                    self._text_div_depth = self._div_depth
                    self._text_parts = []

                if "date" in class_tokens and "details" in class_tokens:
                    self._in_date_div = True
                    self._date_div_depth = self._div_depth
                    title = attrs.get("title")
                    if title:
                        self._current_message.timestamp = title

        elif self._current_message is not None:
            if tag == "br" and self._in_text_div:
                self._text_parts.append("\n")
            elif tag == "a":
                href = attrs.get("href", "")
                if href and not href.startswith("http") and not href.startswith("#") and not href.startswith("mailto:"):
                    # Local file reference from Telegram export
                    self._current_message.attachments.append(href)

    def handle_data(self, data):
        if self._current_message is not None and self._in_text_div:
            self._text_parts.append(data)

    def handle_endtag(self, tag):
        if tag == "div":
            # Exit date div
            if self._in_date_div and self._div_depth == self._date_div_depth:
                self._in_date_div = False
                self._date_div_depth = 0

            # Exit text div
            if self._in_text_div and self._div_depth == self._text_div_depth:
                raw_text = "".join(self._text_parts)
                text = normalize_text(raw_text)
                if text:
                    self._current_message.text = text
                self._in_text_div = False
                self._text_div_depth = 0
                self._text_parts = []

            # Exit message root
            if self._current_message is not None and self._div_depth == self._message_depth:
                # Deduplicate attachments while preserving order
                seen = set()
                deduped = []
                for att in self._current_message.attachments:
                    if att not in seen:
                        seen.add(att)
                        deduped.append(att)
                self._current_message.attachments = deduped

                if self._current_message.text or self._current_message.attachments:
                    self.messages.append(self._current_message)

                self._current_message = None
                self._message_depth = 0
                self._in_service_message = False

            if self._in_service_message and self._current_message is None and self._div_depth == 1:
                self._in_service_message = False

            self._div_depth -= 1


def normalize_text(text: str) -> str:
    text = unescape(text)
    text = text.replace("\xa0", " ")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    return text.strip()


def split_text(text: str, limit: int = MAX_TEXT_LIMIT) -> List[str]:
    if len(text) <= limit:
        return [text]

    chunks: List[str] = []
    remaining = text
    while len(remaining) > limit:
        split_at = remaining.rfind("\n\n", 0, limit)
        if split_at < int(limit * 0.5):
            split_at = remaining.rfind("\n", 0, limit)
        if split_at < int(limit * 0.5):
            split_at = remaining.rfind(" ", 0, limit)
        if split_at <= 0:
            split_at = limit

        chunk = remaining[:split_at].strip()
        if chunk:
            chunks.append(chunk)
        remaining = remaining[split_at:].strip()

    if remaining:
        chunks.append(remaining)

    return chunks


def parse_export_files(base_dir: Path, files: List[str]) -> List[Message]:
    messages: List[Message] = []
    for name in files:
        path = base_dir / name
        if not path.exists():
            continue
        parser = TelegramExportParser(source_file=name)
        parser.feed(path.read_text(encoding="utf-8"))
        messages.extend(parser.messages)
    return messages


def http_json(method: str, url: str, token: str, body: Optional[dict] = None):
    data = None
    headers = {"Authorization": token}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = Request(url=url, data=data, method=method, headers=headers)
    with urlopen(req, timeout=30) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw)


def upload_file(token: str, path: Path):
    ext = path.suffix.lower()
    mime, _ = mimetypes.guess_type(str(path))
    if ext in {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".heic", ".webp"}:
        upload_type = "image"
    elif ext in {".mp4", ".mov", ".mkv", ".webm", ".matroska"}:
        upload_type = "video"
    elif ext in {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac"}:
        upload_type = "audio"
    else:
        upload_type = "file"

    init_url = f"https://platform-api.max.ru/uploads?{urlencode({'type': upload_type})}"
    init_resp = http_json("POST", init_url, token)
    upload_url = init_resp["url"]
    token_from_init = init_resp.get("token")

    boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
    file_name = path.name
    file_bytes = path.read_bytes()
    content_type = mime or "application/octet-stream"

    pre = (
        f"--{boundary}\r\n"
        f"Content-Disposition: form-data; name=\"data\"; filename=\"{file_name}\"\r\n"
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode("utf-8")
    post = f"\r\n--{boundary}--\r\n".encode("utf-8")
    body = pre + file_bytes + post

    req = Request(
        url=upload_url,
        data=body,
        method="POST",
        headers={
            "Authorization": token,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )

    with urlopen(req, timeout=120) as resp:
        raw = resp.read().decode("utf-8")
        upload_resp = json.loads(raw)

    token_value = None
    if isinstance(upload_resp, dict):
        token_value = upload_resp.get("token")
        if token_value is None and isinstance(upload_resp.get("payload"), dict):
            token_value = upload_resp["payload"].get("token")

    if token_value is None:
        token_value = token_from_init

    if token_value is None:
        raise RuntimeError(f"Не удалось получить token после загрузки {path}")

    return {"type": upload_type, "payload": {"token": token_value}}


def send_message(token: str, chat_id: int, text: str, attachments: Optional[List[dict]] = None):
    query = urlencode({"chat_id": chat_id})
    url = f"https://platform-api.max.ru/messages?{query}"
    body = {"text": text}
    if attachments:
        body["attachments"] = attachments
    return http_json("POST", url, token, body=body)


def main():
    load_env()

    ap = argparse.ArgumentParser(description="Import Telegram export posts to MAX channel")
    ap.add_argument("--token", default=os.environ.get("MAX_BOT_TOKEN"), help="Токен бота MAX (или задайте MAX_BOT_TOKEN в .env)")
    ap.add_argument("--chat-id", type=int, default=int(os.environ["MAX_CHAT_ID"]) if os.environ.get("MAX_CHAT_ID") else None, help="ID канала MAX (или задайте MAX_CHAT_ID в .env)")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--pause", type=float, default=0.6)
    ap.add_argument("--base-dir", default=".")
    ap.add_argument("--files", nargs="*", default=["messages.html", "messages2.html"])
    args = ap.parse_args()

    if not args.token:
        ap.error("Укажите токен через --token или задайте MAX_BOT_TOKEN в файле .env")
    if not args.chat_id:
        ap.error("Укажите ID канала через --chat-id или задайте MAX_CHAT_ID в файле .env")

    base_dir = Path(args.base_dir).resolve()
    all_messages = parse_export_files(base_dir, args.files)

    selected = all_messages[args.offset : args.offset + args.limit]
    if not selected:
        print("Нет сообщений для отправки")
        return

    print(f"Найдено сообщений: {len(all_messages)}. Выбрано: {len(selected)} (offset={args.offset}, limit={args.limit})")

    sent_count = 0
    for idx, msg in enumerate(selected, start=1):
        parts = split_text(msg.text or "")
        attachments = []

        abs_attachments = []
        for rel in msg.attachments:
            p = (base_dir / rel).resolve()
            if p.exists():
                abs_attachments.append(p)

        print(
            f"[{idx}] id={msg.message_id} date='{msg.timestamp or ''}' text_len={len(msg.text)} "
            f"parts={len(parts)} attachments={len(abs_attachments)}"
        )

        if args.dry_run:
            preview = (msg.text[:140] + "...") if len(msg.text) > 140 else msg.text
            print(f"    preview: {preview!r}")
            if abs_attachments:
                print("    files:")
                for p in abs_attachments:
                    print(f"      - {p.name}")
            continue

        for p in abs_attachments:
            att = upload_file(args.token, p)
            attachments.append(att)
            time.sleep(0.3)

        # Large files may require processing time.
        if attachments:
            time.sleep(1.2)

        for part_index, chunk in enumerate(parts, start=1):
            body_text = chunk
            chunk_attachments = attachments if part_index == 1 else None
            send_message(args.token, args.chat_id, body_text, chunk_attachments)
            time.sleep(args.pause)

        sent_count += 1

    if not args.dry_run:
        print(f"Готово. Отправлено сообщений: {sent_count}")


if __name__ == "__main__":
    main()
