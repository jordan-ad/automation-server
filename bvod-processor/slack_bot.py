import os
import sys
import tempfile
import shutil
from pathlib import Path

import requests
from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from process_video import process_file, load_config, SUPPORTED_EXTENSIONS

load_dotenv()

app = App(token=os.environ["SLACK_BOT_TOKEN"])


@app.event("app_mention")
def handle_mention(event, client):
    files = event.get("files", [])
    channel = event["channel"]
    thread_ts = event.get("thread_ts") or event["ts"]

    if not files:
        client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text="Attach a video file to your message and I'll process it for BVOD."
        )
        return

    for file_info in files:
        _handle_file(client, file_info, channel, thread_ts)


@app.event("message")
def handle_message(event, client):
    if event.get("channel_type") != "im":
        return
    if event.get("bot_id"):
        return
    subtype = event.get("subtype")
    if subtype and subtype != "file_share":
        return

    files = event.get("files", [])
    channel = event["channel"]
    thread_ts = event["ts"]

    if not files:
        client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text="Send me a video file and I'll process it for BVOD."
        )
        return

    for file_info in files:
        _handle_file(client, file_info, channel, thread_ts)


def _friendly_error(filename, error):
    e = error.lower()
    if "not a supported video format" in e:
        supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        return f"❌ *{filename}* — unsupported format. Please send one of: {supported}"
    if "not a valid length" in e or "not a valid" in e:
        return f"❌ *{filename}* — wrong duration.\n{error}"
    if "no video stream" in e:
        return f"❌ *{filename}* — no video stream found. Is this a valid video file?"
    if any(x in e for x in ["cannot allocate memory", "out of memory", "enomem", "killed"]):
        return f"❌ *{filename}* — ran out of memory. Try a lower resolution version."
    if "file not found" in e:
        return f"❌ *{filename}* — file could not be found or downloaded."
    return f"❌ *{filename}* — processing failed. Try a different file or lower resolution."


def _handle_file(client, file_info, channel, thread_ts):
    filename = file_info.get("name", "video")
    ext = Path(filename).suffix.lower()

    if ext not in SUPPORTED_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=f"❌ *{filename}* — unsupported format `{ext}`.\nSupported formats: {supported}"
        )
        return

    # Warn if file is very large (>500 MB)
    file_size = file_info.get("size", 0)
    if file_size > 500 * 1024 * 1024:
        client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=f"⚠️ *{filename}* is {file_size // (1024*1024)} MB — this may take a while or fail on large files."
        )

    client.chat_postMessage(
        channel=channel,
        thread_ts=thread_ts,
        text=f"⏳ Processing *{filename}*..."
    )

    download_url = file_info.get("url_private_download") or file_info.get("url_private")
    if not download_url:
        info = client.files_info(file=file_info["id"])
        f = info["file"]
        download_url = f.get("url_private_download") or f.get("url_private")

    tmp_dir = tempfile.mkdtemp()
    try:
        tmp_input = os.path.join(tmp_dir, filename)
        _download_file(download_url, tmp_input)

        titlecard_path, suffix = load_config()
        output_path, error = process_file(tmp_input, titlecard_path, suffix)

        if error:
            client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text=_friendly_error(filename, error)
            )
        else:
            client.files_upload_v2(
                channel=channel,
                thread_ts=thread_ts,
                file=output_path,
                filename=Path(output_path).name,
                initial_comment=f"✅ Done! *{Path(output_path).name}* is ready for review."
            )

    except Exception as e:
        client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=_friendly_error(filename, str(e))
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _download_file(url, dest_path):
    token = os.environ["SLACK_BOT_TOKEN"]
    response = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        stream=True,
        timeout=120
    )
    response.raise_for_status()
    with open(dest_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)


if __name__ == "__main__":
    handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    print("Bot is running. Waiting for videos...")
    handler.start()
