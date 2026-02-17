import json
import logging
import os
import sys
from email import message_from_bytes
from email.policy import default as email_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

### Configure logging ###
log_level = os.getenv('LOG_LEVEL', 'WARNING').upper()
logging.basicConfig(level=getattr(logging, log_level, logging.WARNING),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S')
logger = logging.getLogger(__name__)

### Configure your server from environment variables ###
hostName = os.getenv('SERVER_HOST', '0.0.0.0')
serverPort = int(os.getenv('SERVER_PORT', '9000'))
dryRun = os.getenv('DRYRUN', 'false').lower() == 'true'
webhookSecret = os.getenv('WEBHOOK_SECRET')  # Optional webhook authentication

### Configure your telegram bot from environment variables ###
botToken = os.getenv('BOT_TOKEN')
chatID = os.getenv('CHAT_ID')

# Validate required environment variables
if not botToken or not chatID:
    logger.error("BOT_TOKEN and CHAT_ID environment variables are required")
    raise ValueError("BOT_TOKEN and CHAT_ID environment variables are required")

# Telegram API URLs
TELEGRAM_SEND_MESSAGE_URL = f"https://api.telegram.org/bot{botToken}/sendMessage"
TELEGRAM_SEND_PHOTO_URL = f"https://api.telegram.org/bot{botToken}/sendPhoto"

logger.info(f"Configuration loaded - Server: {hostName}:{serverPort}, Log Level: {log_level}, "
            f"Dry Run: {dryRun}, Webhook Auth: {bool(webhookSecret)}")


def extract_event_data(account: Dict[str, Any],
                       player: Dict[str, Any],
                       media: Dict[str, Any]) -> tuple[str, str, str]:
    """
    Extract and format common data from webhook event

    Args:
        account: Account dictionary from Plex webhook
        player: Player dictionary from Plex webhook
        media: Metadata dictionary from Plex webhook

    Returns:
        tuple: (account_title, media_title, player_title)
    """
    # Handle guest users (empty title)
    account_title = account.get("title", "").strip() or "Um usuário visitante"
    media_title = format_media_title(media)
    player_title = player.get("title", "Unknown Player")

    return account_title, media_title, player_title


def format_media_title(media: Dict[str, Any]) -> str:
    """
    Format media title based on type (episode, movie, track, etc.)

    Args:
        media: Metadata dictionary from Plex webhook

    Returns:
        Formatted media title string
    """
    media_type = media.get("type", "unknown")

    match media_type:
        case "episode":
            # TV Show Episode: "Series (S##E##) - Episode Title"
            series_name = media.get("grandparentTitle", "Unknown Series")
            season_num = media.get("parentIndex", 0)
            episode_num = media.get("index", 0)
            episode_title = media.get("title", "Unknown Episode")
            return f"{series_name} (S{season_num:02d}E{episode_num:02d}) - {episode_title}"

        case "movie":
            # Movie: "Movie Title (Year)"
            movie_title = media.get("title", "Unknown Movie")
            year = media.get("year")
            if year:
                return f"{movie_title} ({year})"
            return movie_title

        case "track":
            # Music Track: "Artist - Track Title"
            artist = media.get("grandparentTitle", "Unknown Artist")
            track_title = media.get("title", "Unknown Track")
            album = media.get("parentTitle")
            if album:
                return f"{artist} - {track_title} (Album: {album})"
            return f"{artist} - {track_title}"

        case _:
            # Fallback for unknown types
            return media.get("title", "Unknown Media")


class MyServer(BaseHTTPRequestHandler):

    @staticmethod
    def send_notify(msg: str,
                    image_data: Optional[bytes] = None,
                    image_type: str = "image/jpeg") -> None:
        """
        Send notification to Telegram, optionally with image

        Args:
            msg: Message text to send
            image_data: Optional image data as bytes (from Plex webhook multipart)
            image_type: MIME type of the image (e.g., "image/jpeg", "image/png")
        """
        if dryRun:
            logger.info(f"[DRY RUN] Would send Telegram notification: {msg}")
            if image_data:
                logger.info(f"[DRY RUN] Would include image ({len(image_data)} bytes, "
                            f"{image_type})")
            return

        # Try to send with image if image data is provided
        if image_data:
            try:
                logger.debug(f"Sending Telegram notification with image ({len(image_data)} bytes)")
                files = { "photo": ("thumb.jpg", image_data, image_type) }
                data = { "chat_id": chatID, "caption": msg }

                response = requests.post(TELEGRAM_SEND_PHOTO_URL,
                                         data=data,
                                         files=files,
                                         timeout=10)
                response.raise_for_status()
                logger.info(f"Telegram notification with image sent successfully")
                return

            except requests.exceptions.RequestException as e:
                logger.warning(f"Failed to send image, falling back to text-only: {e}")  # Fall
                # through to send text-only message

        # Send text-only message (fallback or if no image)
        try:
            payload = {
                "chat_id": chatID, "text": msg
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
                logger.warning(f"Temporary error sending Telegram notification (HTTP "
                               f"{status_code}): {e}")
            else:
                # Network errors (timeout, connection refused, etc.) → Log and continue
                logger.warning(f"Network error sending Telegram notification: {e}")

            logger.warning("Service will continue running, but this notification was not sent")
            logger.warning("Check logs and Telegram API status if errors persist")

    def handle_mediaPlay(self,
                         account,
                         player,
                         media,
                         thumbnail_data=None,
                         thumbnail_type="image/jpeg"):
        account_title, media_title, player_title = extract_event_data(account, player, media)

        logger.info(f"Media play event - User: {account_title}, Media: {media_title}, Player: {
        player_title}")
        message = f"{account_title} começou a tocar {media_title} em {player_title}"
        self.send_notify(message, image_data=thumbnail_data, image_type=thumbnail_type)

    def handle_mediaPause(self,
                          account,
                          player,
                          media):
        account_title, media_title, player_title = extract_event_data(account, player, media)

        logger.debug(f"Media pause event - User: {account_title}, Media: {media_title}, Player: "
                     f"{player_title}")

    def handle_mediaResume(self,
                           account,
                           player,
                           media):
        account_title, media_title, player_title = extract_event_data(account, player, media)

        logger.debug(f"Media resume event - User: {account_title}, Media: {media_title}, Player: {player_title}")

    def handle_mediaStop(self,
                         account,
                         player,
                         media,
                         thumbnail_data=None,
                         thumbnail_type="image/jpeg"):
        account_title, media_title, player_title = extract_event_data(account, player, media)

        logger.info(f"Media stop event - User: {account_title}, Media: {media_title}, Player: "
                    f"{player_title}")
        message = f"{account_title} parou de tocar {media_title} em {player_title}"
        self.send_notify(message, image_data=thumbnail_data, image_type=thumbnail_type)

    def do_GET(self) -> None:
        """Handle GET requests for healthcheck endpoint"""
        # Parse URL path
        parsed_url = urlparse(self.path)
        request_path = parsed_url.path.strip('/')

        if request_path == 'health':
            logger.debug("Healthcheck request received")
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            response = json.dumps({"status": "healthy", "service": "plex-telegram-notify"})
            self.wfile.write(response.encode('utf-8'))
        else:
            # Return 404 for other GET requests
            self.send_response(404)
            self.end_headers()

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

            # Read raw POST data (binary)
            post_data = self.rfile.read(content_length)

            # Parse multipart/form-data correctly using email.message
            # This preserves binary data (images) and properly handles the structure
            content_type = self.headers.get('Content-Type', '')

            if not content_type.startswith('multipart/'):
                logger.warning(f"Unexpected Content-Type: {content_type}")
                self.send_response(400)
                self.end_headers()
                return

            # Construct proper email headers for parsing
            headers_bytes = f"Content-Type: {content_type}\r\n\r\n".encode('utf-8')
            full_message = headers_bytes + post_data

            # Parse multipart message
            try:
                msg = message_from_bytes(full_message, policy=email_policy)
            except Exception as e:
                logger.warning(f"Failed to parse multipart message: {e}")
                self.send_response(400)
                self.end_headers()
                return

            # Extract JSON payload and thumbnail image from multipart parts
            payload = None
            thumbnail_data = None
            thumbnail_type = "image/jpeg"

            for part in msg.iter_parts():
                content_type = part.get_content_type()
                logger.debug(f"Found multipart part with content-type: {content_type}")

                # Look for the JSON payload (usually application/json or text/plain)
                if content_type in ['application/json', 'text/plain']:
                    try:
                        content = part.get_content()

                        # Handle both string and bytes
                        if isinstance(content, bytes):
                            content = content.decode('utf-8')

                        # Try to parse as JSON
                        payload = json.loads(content)
                        logger.debug("Successfully extracted JSON from multipart")
                    except (json.JSONDecodeError, UnicodeDecodeError) as e:
                        logger.debug(f"Part is not valid JSON, trying next part: {e}")
                        continue

                # Look for thumbnail image
                elif content_type.startswith('image/'):
                    try:
                        thumbnail_data = part.get_content()
                        thumbnail_type = content_type
                        logger.debug(f"Successfully extracted thumb"
                                     f"nail image ({len(thumbnail_data)} bytes, {content_type})")
                    except Exception as e:
                        logger.debug(f"Failed to extract image: {e}")
                        continue

            # Validate that we found a JSON payload
            if payload is None:
                logger.warning("No valid JSON payload found in multipart message")
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
            match event:
                case "media.play":
                    self.handle_mediaPlay(account, player, media, thumbnail_data, thumbnail_type)
                case "media.resume":
                    self.handle_mediaResume(account, player, media)
                case "media.pause":
                    self.handle_mediaPause(account, player, media)
                case "media.stop":
                    self.handle_mediaStop(account, player, media)
                case _:
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
