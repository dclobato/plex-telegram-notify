# plex-telegram-notify

## Introduction
Simple python script for plex web hooks to send notifications over telegram. Configure your settings using environment variables and your telegram-bot (created with botfather). You can run the script directly with python or with the provided docker files as a container.

## Requirements

- **Python 3.10+** (required for `match/case` statements)
- **Plex Pass** subscription (for webhook feature)
- **Telegram Bot** (create with @BotFather)

## Configuration

### Environment Variables
The application uses the following environment variables:

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `SERVER_HOST` | Server hostname/IP to bind to | `0.0.0.0` | No |
| `SERVER_PORT` | Server port | `9000` | No |
| `LOG_LEVEL` | Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL) | `WARNING` | No |
| `DRYRUN` | Dry run mode - only log notifications without sending (`true`/`false`) | `false` | No |
| `WEBHOOK_SECRET` | Secret path for webhook endpoint (e.g., `my-secret-xyz123`) | - | No |
| `BOT_TOKEN` | Telegram bot token from BotFather | - | Yes |
| `CHAT_ID` | Telegram chat ID to send notifications | - | Yes |

### Setup Instructions

1. **Create a `.env` file** from the example:
   ```bash
   cp .env.example .env
   ```

2. **Edit the `.env` file** with your values:
   ```env
   SERVER_HOST=0.0.0.0
   SERVER_PORT=9000
   LOG_LEVEL=INFO
   DRYRUN=false
   BOT_TOKEN=your-telegram-bot-token
   CHAT_ID=your-telegram-chat-id

   # Optional: Enable webhook path security (recommended for production)
   WEBHOOK_SECRET=my-super-secret-endpoint-xyz123
   ```

### Getting Telegram Bot Token and Chat ID

1. **Create a Telegram Bot**:
   - Open Telegram and search for `@BotFather`
   - Send `/newbot` and follow the instructions
   - Save the bot token provided (this is your `BOT_TOKEN`)

2. **Get your Chat ID**:
   - Send a message to your bot
   - Open this URL in your browser: `https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates`
   - Look for `"chat":{"id":123456789}` in the response
   - The number is your `CHAT_ID`

### Thumbnail Images in Notifications

The application **automatically includes thumbnail images** in Telegram notifications!

Plex webhooks include the media thumbnail as part of the multipart POST request. The application extracts this image and sends it directly to Telegram - no additional configuration needed.

**Features**:
- ✅ Automatic thumbnail extraction from Plex webhook
- ✅ No additional configuration required (no PLEX_URL or PLEX_TOKEN needed)
- ✅ Works with all media types (movies, TV shows, music)
- ✅ Faster than downloading from Plex server (uses thumbnail from POST)
- ✅ More reliable (works even if Plex server isn't accessible by URL)

### Configuring Plex Webhook

**Requirements**: This feature requires a Plex Pass subscription.

1. **Access Plex Settings**:
   - Open your Plex Web App
   - Go to Settings → Your Account → Webhooks

2. **Add Webhook URL**:
   - Click on "Add Webhook"
   - Enter the webhook URL based on your setup:

   **Without webhook security** (basic setup):
   ```
   http://localhost:9000
   ```
   or if on different machine:
   ```
   http://192.168.1.100:9000
   ```

   **With webhook security** (recommended):
   ```
   http://localhost:9000/my-super-secret-endpoint-xyz123
   ```
   or if on different machine:
   ```
   http://192.168.1.100:9000/my-super-secret-endpoint-xyz123
   ```
   *(Replace the path with your `WEBHOOK_SECRET` value)*

3. **Save the webhook** and test it by playing something on Plex

**Note**: Make sure the webhook URL is accessible from your Plex Media Server. If using Docker, ensure the port is properly exposed.

### Webhook Security (Recommended for Production)

To prevent unauthorized access to your webhook endpoint, use a secret path:

1. **Set a random webhook secret**:
   ```env
   WEBHOOK_SECRET=my-super-secret-endpoint-xyz123
   ```

   **Tips for choosing a good secret**:
   - Use a long random string (20+ characters)
   - Mix letters, numbers, and hyphens
   - Don't use spaces or special characters that need URL encoding
   - Example generators:
     ```bash
     # Linux/Mac
     openssl rand -hex 16
     # Output: a3f8c9d2e1b4f6a8c3d9e2b7f1a4c8d3
     ```

2. **Configure Plex webhook URL**:
   ```
   http://192.168.1.100:9000/a3f8c9d2e1b4f6a8c3d9e2b7f1a4c8d3
   ```

3. **Security benefits**:
   - ✅ Only requests to the secret path are accepted
   - ✅ Invalid paths return 404 (hiding the endpoint existence)
   - ✅ No reverse proxy needed
   - ✅ Works directly with Plex

**Note**: If `WEBHOOK_SECRET` is set, requests to any other path will be rejected with 404.

### Dry Run Mode

For testing purposes, you can enable dry run mode to see what notifications would be sent without actually sending them to Telegram:

```bash
DRYRUN=true LOG_LEVEL=INFO python run.py
```

This will log messages like:
```
2026-02-16 10:30:45 - __main__ - INFO - [DRY RUN] Would send Telegram notification: User started playing Movie on Chrome
```

## Install

### Using Docker Compose (Recommended)
```bash
docker build -t plex-webhook .
docker compose up -d
```

### Using Docker Run
```bash
docker build -t plex-webhook .
docker run -d \
  --name plex-webhook \
  --network host \
  -e SERVER_HOST=0.0.0.0 \
  -e SERVER_PORT=9000 \
  -e BOT_TOKEN=your-bot-token \
  -e CHAT_ID=your-chat-id \
  --restart unless-stopped \
  plex-webhook
```

### Running Directly with Python
```bash
pip install -r requirements.txt
python run.py
```
