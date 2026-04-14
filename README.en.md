# Telegram to MAX Channel Import

This script imports posts from a Telegram channel HTML export into a [MAX](https://max.ru) channel. Supports text messages, images, videos, audio, and files.

[Русская версия](README.md)

---

## Requirements

- Python 3.8+
- MAX bot token
- Telegram channel history exported in HTML format

---

## Step 1 — Export history from Telegram

1. Open **Telegram Desktop** (the mobile app does not support export)
2. Navigate to the channel you want to export
3. Click the three-dot menu in the top right corner → **Export chat history**
4. Select **HTML** format
5. Choose the date range and media types you need
6. Click **Export** — files will be saved to a folder (e.g. `ChatExport_2024-01-01`)
7. The folder will contain `messages.html`, `messages2.html`, etc., along with media subfolders

---

## Step 2 — Create a MAX bot and grant admin rights

The import is done via a MAX platform bot. You need to create a bot and make it an administrator of your channel.

1. Open the **MAX** app and find the bot **@MaxBotAPI** via search
2. Message it — the bot will give you a **token** (a long string like `xxxxxxxx.xxxxxxxxx`)
3. Save the token — you will need it in the next step
4. Go to your **channel settings** in MAX → **Administrators** → add your bot as an administrator with **post messages** permission
5. Get your **channel ID**: send any message to the channel via the bot, or use `GET /chats` with your token — the ID is a numeric value (e.g. `123456789`)

---

## Step 3 — Setup

1. Clone the repository:
   ```bash
   git clone https://github.com/YOUR_USERNAME/tg-to-max.git
   cd tg-to-max
   ```

2. Copy the environment variables example file:
   ```bash
   cp .env.example .env
   ```

3. Open `.env` and fill in your values:
   ```
   MAX_BOT_TOKEN=your_bot_token
   MAX_CHAT_ID=your_channel_id
   ```

   - **MAX_BOT_TOKEN** — bot token received from @MaxBotAPI
   - **MAX_CHAT_ID** — numeric MAX channel ID (the bot must be an admin of the channel)

---

## Step 4 — Running

Dry run without sending anything (recommended first):
```bash
python import_tg_to_max.py --base-dir /path/to/export/folder --dry-run
```

Basic run (reads credentials from `.env`):
```bash
python import_tg_to_max.py --base-dir /path/to/export/folder
```

Import first 50 messages:
```bash
python import_tg_to_max.py --base-dir /path/to/export/folder --limit 50
```

Import starting from message 100:
```bash
python import_tg_to_max.py --base-dir /path/to/export/folder --offset 100 --limit 50
```

Pass credentials directly (without `.env`):
```bash
python import_tg_to_max.py --token your_token --chat-id 123456789 --base-dir /path/to/export
```

---

## CLI Parameters

| Parameter | Default | Description |
|---|---|---|
| `--token` | from `.env` | MAX bot token |
| `--chat-id` | from `.env` | MAX channel ID |
| `--base-dir` | `.` | Telegram export folder path |
| `--files` | `messages.html messages2.html` | HTML export files |
| `--limit` | `10` | Number of messages to send |
| `--offset` | `0` | Skip first N messages |
| `--pause` | `0.6` | Pause between messages (sec) |
| `--dry-run` | off | Test mode without sending |

---

## Important

- The MAX bot must be a **channel administrator** with posting rights
- The `.env` file is listed in `.gitignore` — never commit it
- For large channels use `--offset` and `--limit` to import in batches

---

# Migration Scripts Comparison

| Criteria | migrate_to_max.py | import_tg_to_max.py |
|----------|-------------------|----------------------|
| **Dependencies** | Requires `pip install requests beautifulsoup4` | Python standard library only |
| **HTML parser** | BeautifulSoup (external library) | Built-in HTMLParser |
| **IDE launch** | Just press Run — no configuration needed | Requires arguments (`--limit`, `--offset`, etc.) |
| **Batch control** | Processes ALL messages at once | Supports `--limit` and `--offset` (batches) |
| **Dry-run mode** | No | Has `--dry-run` — test without sending |
| **Attachment types** | Only photos and videos | Photos, videos, audio, any files |
| **API endpoint** | `botapi.max.ru` | `platform-api.max.ru` |
| **Resume** | `python migrate_to_max.py 50` (from message number) | `--offset 50` |

---

## Which one to use from IDE?

For **regular IDE launch** (PyCharm, VS Code) — **`migrate_to_max.py`**

✅ Just press Run  
✅ No required arguments  
✅ Reads `.env`, takes all `messages*.html` and sends everything  

> ⚠️ But you need to install the dependency first:
> ```bash
> pip install requests beautifulsoup4
> ```

---

## Alternative

If you **don't want to install dependencies** — use `import_tg_to_max.py`, but then you need to configure launch arguments in IDE, for example:

```bash
--limit 9999 --files messages.html messages2.html
