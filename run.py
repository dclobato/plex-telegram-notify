import json
import logging
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional, Dict, Any
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

### Configure logging ###
log_level = os.getenv('LOG_LEVEL', 'WARNING').upper()
logging.basicConfig(
        level=getattr(logging, log_level, logging.WARNING),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
        )
logger = logging.getLogger(__name__)

### Configure your server from environment variables ###
hostName = os.getenv('SERVER_HOST', '0.0.0.0')
serverPort = int(os.getenv('SERVER_PORT', '9000'))
dryRun = os.getenv('DRYRUN', 'false').lower() == 'true'
webhookSecret = os.getenv('WEBHOOK_SECRET')  # Optional webhook authentication

### Configure your telegram bot from environment variables ###
botToken = os.getenv('BOT_TOKEN')
chatID = os.getenv('CHAT_ID')

### Configure Plex server (optional, for thumbnail images) ###
plexUrl = os.getenv('PLEX_URL')
plexToken = os.getenv('PLEX_TOKEN')

# Validate required environment variables
if not botToken or not chatID:
    logger.error("BOT_TOKEN and CHAT_ID environment variables are required")
    raise ValueError("BOT_TOKEN and CHAT_ID environment variables are required")

# Telegram API URLs
TELEGRAM_SEND_MESSAGE_URL = f"https://api.telegram.org/bot{botToken}/sendMessage"
TELEGRAM_SEND_PHOTO_URL = f"https://api.telegram.org/bot{botToken}/sendPhoto"

# Check if Plex integration is enabled
plex_enabled = bool(plexUrl and plexToken)

logger.info(f"Configuration loaded - Server: {hostName}:{serverPort}, Log Level: {log_level}, "
            f"Dry Run: {dryRun}, Plex Images: {plex_enabled}, Webhook Auth: {bool(webhookSecret)}")


def format_media_title(media: Dict[str, Any]) -> str:
    """
    Format media title based on type (episode, movie, track, etc.)

    Args:
        media: Metadata dictionary from Plex webhook

    Returns:
        Formatted media title string
    """
    media_type = media.get("type", "unknown")

    if media_type == "episode":
        # TV Show Episode: "Series (S##E##) - Episode Title"
        series_name = media.get("grandparentTitle", "Unknown Series")
        season_num = media.get("parentIndex", 0)
        episode_num = media.get("index", 0)
        episode_title = media.get("title", "Unknown Episode")

        return f"{series_name} (S{season_num:02d}E{episode_num:02d}) - {episode_title}"

    elif media_type == "movie":
        # Movie: "Movie Title (Year)"
        movie_title = media.get("title", "Unknown Movie")
        year = media.get("year")

        if year:
            return f"{movie_title} ({year})"
        return movie_title

    elif media_type == "track":
        # Music Track: "Artist - Track Title"
        artist = media.get("grandparentTitle", "Unknown Artist")
        track_title = media.get("title", "Unknown Track")
        album = media.get("parentTitle")

        if album:
            return f"{artist} - {track_title} (Album: {album})"
        return f"{artist} - {track_title}"

    else:
        # Fallback for unknown types
        return media.get("title", "Unknown Media")


def get_media_thumbnail(media: Dict[str, Any]) -> Optional[str]:
    """
    Get the best thumbnail path for the media type

    Args:
        media: Metadata dictionary from Plex webhook

    Returns:
        Thumbnail path or None
    """
    media_type = media.get("type", "unknown")

    if media_type == "episode":
        # For episodes, prefer grandparent (series) thumb over episode thumb
        return media.get("grandparentThumb") or media.get("thumb")
    elif media_type == "movie":
        # For movies, use the movie thumb
        return media.get("thumb")
    elif media_type == "track":
        # For music, prefer album art
        return media.get("parentThumb") or media.get("thumb")
    else:
        # Fallback
        return media.get("thumb")


class MyServer(BaseHTTPRequestHandler):

    @staticmethod
    def send_notify(msg: str,
                    image_path: Optional[str] = None) -> None:
        """
        Send notification to Telegram, optionally with image

        Args:
            msg: Message text to send
            image_path: Optional Plex thumbnail path (e.g., "/library/metadata/123/thumb/456")
        """
        if dryRun:
            logger.info(f"[DRY RUN] Would send Telegram notification: {msg}")
            if image_path and plex_enabled:
                logger.info(f"[DRY RUN] Would include image: {plexUrl}{image_path}")
            return

        # Try to send with image if Plex is configured and image path is provided
        if plex_enabled and image_path:
            try:
                # Download image from Plex
                image_url = f"{plexUrl}{image_path}?X-Plex-Token={plexToken}"
                # Mask token in logs for security
                safe_url = f"{plexUrl}{image_path}?X-Plex-Token=***MASKED***"
                logger.debug(f"Downloading image from Plex: {safe_url}")

                image_response = requests.get(image_url, timeout=5)
                image_response.raise_for_status()

                # Send photo with caption to Telegram
                logger.debug(f"Sending Telegram notification with image")
                files = { "photo": ("thumb.jpg", image_response.content, "image/jpeg") }
                data = { "chat_id": chatID, "caption": msg }

                response = requests.post(TELEGRAM_SEND_PHOTO_URL, data=data, files=files, timeout=10)
                response.raise_for_status()
                logger.info(f"Telegram notification with image sent successfully")
                return

            except requests.exceptions.RequestException as e:
                logger.warning(f"Failed to send image, falling back to text-only: {e}")
                # Fall through to send text-only message

        # Send text-only message (fallback or if no image)
        try:
            payload = {
                "chat_id": chatID,
                "text"   : msg
                }
            logger.debug(f"Sending Telegram notification (text-only): {msg}")
            response = requests.post(TELEGRAM_SEND_MESSAGE_URL, json=payload, timeout=10)
            response.raise_for_status()
            logger.info(f"Telegram notification sent successfully")
        except requests.exceptions.RequestException as e:
            # Hybrid approach: fail-fast for config errors, resilient for temporary errors

            # Check if it's a configuration error (fail-fast)
            if hasattr(e, 'response') and e.response is not None:
                status_code = e.response.status_code

                # Configuration errors → Abort application
                if status_code in [400, 401, 403, 404]:
                    logger.error(f"FATAL: Telegram API configuration error (HTTP {status_code})")
                    logger.error(f"Error details: {e}")
                    logger.error("This likely means BOT_TOKEN or CHAT_ID is invalid")
                    logger.error("Aborting application - fix configuration and restart")
                    sys.exit(1)

                # Temporary errors → Log and continue
                logger.warning(f"Temporary error sending Telegram notification (HTTP {status_code}): {e}")
            else:
                # Network errors (timeout, connection refused, etc.) → Log and continue
                logger.warning(f"Network error sending Telegram notification: {e}")

            logger.warning("Service will continue running, but this notification was not sent")
            logger.warning("Check logs and Telegram API status if errors persist")

    def handle_mediaPlay(self, account, player, media):
        account_title = account.get("title", "Unknown User")
        media_title = format_media_title(media)
        player_title = player.get("title", "Unknown Player")
        thumbnail = get_media_thumbnail(media)

        logger.info(f"Media play event - User: {account_title}, Media: {media_title}, Player: "
                    f"{player_title}")
        message = f"{account_title} começou a tocar {media_title} em {player_title}"
        self.send_notify(message, image_path=thumbnail)

    def handle_mediaPause(self, account, player, media):
        account_title = account.get("title", "Unknown User")
        media_title = format_media_title(media)
        player_title = player.get("title", "Unknown Player")

        logger.debug(f"Media pause event - User: {account_title}, Media: {media_title}, Player: "
                     f"{player_title}")

    def handle_mediaResume(self, account, player, media):
        account_title = account.get("title", "Unknown User")
        media_title = format_media_title(media)
        player_title = player.get("title", "Unknown Player")

        logger.debug(f"Media resume event - User: {account_title}, Media: {media_title}, "
                     f"Player: {player_title}")

    def handle_mediaStop(self, account, player, media):
        account_title = account.get("title", "Unknown User")
        media_title = format_media_title(media)
        player_title = player.get("title", "Unknown Player")
        thumbnail = get_media_thumbnail(media)

        logger.info(f"Media stop event - User: {account_title}, Media: {media_title}, Player: "
                    f"{player_title}")
        message = f"{account_title} parou de tocar {media_title} em {player_title}"
        self.send_notify(message, image_path=thumbnail)

    def do_POST(self) -> None:
        try:
            # Validate webhook path secret if configured
            if webhookSecret:
                # Parse URL to get only the path (ignore query strings)
                parsed_url = urlparse(self.path)
                request_path = parsed_url.path.strip('/')

                if request_path != webhookSecret:
                    logger.warning(f"Unauthorized webhook request - invalid path: /{request_path}")
                    # Return 404 to not reveal that the endpoint exists
                    self.send_response(404)
                    self.end_headers()
                    return
                logger.debug(f"Webhook authentication successful - path matched")

            content_length = int(self.headers["Content-Length"])
            logger.debug(f"Received POST request - Content-Length: {content_length}")

            # read post bytes from POST request and decode
            post_data = self.rfile.read(content_length)
            post_data_decode = post_data.decode("utf-8", "ignore")

            # get the boundary from header and split the payload
            header_boundary = self.headers.get_boundary()
            if not header_boundary:
                logger.warning("Missing boundary in multipart request")
                self.send_response(400)
                self.end_headers()
                return

            post_data_list = post_data_decode.split(header_boundary)

            # Validate multipart structure
            if len(post_data_list) < 2:
                logger.warning("Invalid multipart structure - insufficient parts")
                self.send_response(400)
                self.end_headers()
                return

            # trim and parse json payload from second payload object (Plex Webhook sends
            # sometimes an image as third object)
            post_data_payload = post_data_list[1]
            json_start = post_data_payload.find("{")
            json_end = post_data_payload.rfind("}")

            if json_start == -1 or json_end == -1:
                logger.warning("No JSON found in multipart payload")
                self.send_response(400)
                self.end_headers()
                return

            post_payload = post_data_payload[json_start:json_end + 1]

            try:
                payload = json.loads(post_payload)
            except json.JSONDecodeError as e:
                logger.warning(f"Invalid JSON in webhook payload: {e}")
                self.send_response(400)
                self.end_headers()
                return

            logger.debug(f"Parsed webhook payload: "
                         f"{json.dumps(payload, indent=2, ensure_ascii=False)}")

            # Extract and validate required fields
            event = payload.get("event")
            account = payload.get("Account")
            player = payload.get("Player")
            media = payload.get("Metadata")

            # Validate required fields
            if not event:
                logger.warning("Webhook payload missing 'event' field")
                self.send_response(400)
                self.end_headers()
                return

            if not all([account, player, media]):
                logger.warning(f"Webhook payload missing required fields - Account: {bool(
                        account)}, Player: {bool(player)}, Metadata: {bool(media)}")
                self.send_response(400)
                self.end_headers()
                return

            logger.info(f"Received webhook event: {event}")

            # handle playback events
            if event == "media.play":
                self.handle_mediaPlay(account, player, media)
            elif event == "media.resume":
                self.handle_mediaResume(account, player, media)
            elif event == "media.pause":
                self.handle_mediaPause(account, player, media)
            elif event == "media.stop":
                self.handle_mediaStop(account, player, media)
            else:
                logger.warning(f"Unhandled event type: {event}")

            self.send_response(200)
            self.end_headers()

        except KeyError as e:
            logger.error(f"Missing expected field in webhook payload: {e}", exc_info=True)
            self.send_response(400)
            self.end_headers()
        except ValueError as e:
            logger.error(f"Invalid value in webhook payload: {e}", exc_info=True)
            self.send_response(400)
            self.end_headers()
        except Exception as e:
            logger.error(f"Unexpected error processing webhook: {e}", exc_info=True)
            self.send_response(500)
            self.end_headers()


if __name__ == "__main__":
    webServer = ThreadingHTTPServer((hostName, serverPort), MyServer)  # type: ignore[arg-type]
    logger.info(f"Starting Plex webhook server (multi-threaded) on {hostName}:{serverPort}")

    try:
        webServer.serve_forever()
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt, shutting down...")

    webServer.server_close()
    logger.info("Server stopped. Goodbye!")
