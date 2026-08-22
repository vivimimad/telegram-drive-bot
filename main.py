import os
import time
import asyncio
import logging

from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("tg-drive-bot")

# ---------------------------------------------------------------------------
# Ortam değişkenleri (Railway -> Variables kısmında ayarlanacak)
# ---------------------------------------------------------------------------
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]

GOOGLE_CLIENT_ID = os.environ["GOOGLE_CLIENT_ID"]
GOOGLE_CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]
GOOGLE_REFRESH_TOKEN = os.environ["GOOGLE_REFRESH_TOKEN"]

# Opsiyonel: belirli bir klasöre yüklemek istersen Drive klasör ID'sini buraya koy.
# /myfiles komutu da bu klasördeki dosyaları listeler. Bo\u015f b\u0131rak\u0131rsan "root" (My Drive) kullan\u0131l\u0131r.
DRIVE_FOLDER_ID = os.environ.get("DRIVE_FOLDER_ID", "").strip() or None

# Opsiyonel ama önerilir: sadece senin kullanabilmen için Telegram user id'lerin
# virgülle ayrılmış şekilde. Boş bırakılırsa bot HERKESE açık olur (önerilmez).
ALLOWED_USER_IDS = {
    int(x) for x in os.environ.get("ALLOWED_USER_IDS", "").split(",") if x.strip()
}

# /myfiles listesinde en fazla kaç dosya gösterilsin
LIST_LIMIT = 25

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

GOOGLE_SCOPES = ["https://www.googleapis.com/auth/drive"]

# ---------------------------------------------------------------------------
# Google Drive yardımcı fonksiyonları
# ---------------------------------------------------------------------------

def get_drive_service():
    creds = Credentials(
        token=None,
        refresh_token=GOOGLE_REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        scopes=GOOGLE_SCOPES,
    )
    creds.refresh(Request())
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def upload_to_drive(local_path: str, filename: str) -> str:
    service = get_drive_service()

    file_metadata = {"name": filename}
    if DRIVE_FOLDER_ID:
        file_metadata["parents"] = [DRIVE_FOLDER_ID]

    media = MediaFileUpload(local_path, resumable=True, chunksize=10 * 1024 * 1024)

    request = service.files().create(
        body=file_metadata,
        media_body=media,
        fields="id, webViewLink",
        supportsAllDrives=True,
    )

    response = None
    while response is None:
        status, response = request.next_chunk()

    file_id = response["id"]

    # Linke tıklayan herkes (linki bilen) görüntüleyebilsin diye paylaşım izni.
    service.permissions().create(
        fileId=file_id,
        body={"role": "reader", "type": "anyone"},
    ).execute()

    return response.get("webViewLink", f"https://drive.google.com/file/d/{file_id}/view")


def list_drive_files():
    service = get_drive_service()
    parent = DRIVE_FOLDER_ID or "root"
    results = (
        service.files()
        .list(
            q=f"'{parent}' in parents and trashed=false",
            orderBy="createdTime desc",
            pageSize=LIST_LIMIT,
            fields="files(id, name, size, webViewLink)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        )
        .execute()
    )
    return results.get("files", [])


def get_drive_file_link(file_id: str) -> str:
    service = get_drive_service()
    f = service.files().get(fileId=file_id, fields="webViewLink").execute()
    return f.get("webViewLink", f"https://drive.google.com/file/d/{file_id}/view")


def delete_drive_file(file_id: str):
    service = get_drive_service()
    service.files().delete(fileId=file_id, supportsAllDrives=True).execute()


def rename_drive_file(file_id: str, new_name: str):
    service = get_drive_service()
    service.files().update(
        fileId=file_id, body={"name": new_name}, supportsAllDrives=True
    ).execute()


# ---------------------------------------------------------------------------
# Telegram bot
# ---------------------------------------------------------------------------

client = TelegramClient(StringSession(), API_ID, API_HASH)

# user_id -> {str(index): {"id": drive_file_id, "name": filename}}
pending_lists: dict[int, dict] = {}

# user_id -> drive_file_id (waiting for the user to type a new name)
pending_renames: dict[int, str] = {}


def human_size(num_bytes) -> str:
    try:
        num_bytes = float(num_bytes)
    except (TypeError, ValueError):
        return "?"
    step = 1024.0
    for unit in ["B", "KB", "MB", "GB"]:
        if num_bytes < step:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= step
    return f"{num_bytes:.1f} TB"


def is_allowed(user_id: int) -> bool:
    if not ALLOWED_USER_IDS:
        return True
    return user_id in ALLOWED_USER_IDS


@client.on(events.NewMessage(pattern="/start"))
async def start_handler(event):
    if not is_allowed(event.sender_id):
        return
    await event.respond(
        "Hi! Send me any file (document, video, audio, photo) and I'll upload it "
        "to your Google Drive and send you the link back.\n\n"
        "There's no 20MB limit, you can even try the biggest file Telegram allows.\n\n"
        "Send /myfiles to see the files you've uploaded."
    )


@client.on(events.NewMessage(pattern="/myfiles"))
async def myfiles_handler(event):
    if not is_allowed(event.sender_id):
        await event.respond("You're not allowed to use this bot.")
        return

    status = await event.respond("📂 Fetching files...")

    loop = asyncio.get_event_loop()
    try:
        files = await loop.run_in_executor(None, list_drive_files)
    except Exception as e:
        log.exception("Error while fetching file list")
        await status.edit(f"❌ Couldn't fetch the list: {e}")
        return

    if not files:
        await status.edit("There are no files in the folder.")
        return

    mapping = {}
    lines = ["📂 Your files (newest first):\n"]
    for i, f in enumerate(files, start=1):
        mapping[str(i)] = {"id": f["id"], "name": f["name"]}
        size_txt = human_size(f.get("size")) if f.get("size") else "-"
        lines.append(f"{i}. {f['name']} ({size_txt})")

    lines.append("\nTo take action on a file, just send its number. Example: 2")

    pending_lists[event.sender_id] = mapping
    await status.edit("\n".join(lines))


@client.on(events.NewMessage())
async def rename_reply_handler(event):
    if event.sender_id not in pending_renames:
        return
    if not is_allowed(event.sender_id):
        return

    file_id = pending_renames.pop(event.sender_id)
    new_name = (event.raw_text or "").strip()

    if not new_name:
        await event.respond("Empty name, rename cancelled.")
        raise events.StopPropagation

    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, rename_drive_file, file_id, new_name)
        await event.respond(f"✏️ Renamed to: {new_name}")
    except Exception as e:
        await event.respond(f"❌ Couldn't rename it: {e}")

    raise events.StopPropagation


@client.on(events.NewMessage())
async def number_reply_handler(event):
    text = (event.raw_text or "").strip()

    if not text.isdigit():
        return
    if event.sender_id not in pending_lists:
        return
    if not is_allowed(event.sender_id):
        return

    mapping = pending_lists[event.sender_id]
    choice = mapping.get(text)
    if not choice:
        await event.respond("There's no file with that number. Send /myfiles to get the list again.")
        return

    await event.respond(
        f"📄 {choice['name']}\nWhat would you like to do?",
        buttons=[
            [Button.inline("🔗 Get link", data=f"link:{choice['id']}")],
            [Button.inline("✏️ Rename", data=f"renask:{choice['id']}")],
            [Button.inline("🗑 Delete", data=f"delask:{choice['id']}")],
            [Button.inline("❌ Cancel", data="cancel")],
        ],
    )


@client.on(events.CallbackQuery())
async def callback_handler(event):
    if not is_allowed(event.sender_id):
        await event.answer("You're not allowed to do this.", alert=True)
        return

    data = event.data.decode()

    if data == "cancel":
        pending_renames.pop(event.sender_id, None)
        await event.edit("Cancelled.", buttons=None)
        return

    action, _, file_id = data.partition(":")

    loop = asyncio.get_event_loop()

    if action == "link":
        try:
            link = await loop.run_in_executor(None, get_drive_file_link, file_id)
            await event.edit(f"🔗 {link}", buttons=None)
        except Exception as e:
            await event.edit(f"❌ Couldn't get the link: {e}", buttons=None)

    elif action == "delask":
        await event.edit(
            "⚠️ Are you sure you want to delete this file? This can't be undone.",
            buttons=[
                [Button.inline("✅ Yes, delete", data=f"delyes:{file_id}")],
                [Button.inline("❌ Never mind", data="cancel")],
            ],
        )

    elif action == "delyes":
        try:
            await loop.run_in_executor(None, delete_drive_file, file_id)
            await event.edit("🗑 File deleted.", buttons=None)
        except Exception as e:
            await event.edit(f"❌ Couldn't delete it: {e}", buttons=None)

    elif action == "renask":
        pending_renames[event.sender_id] = file_id
        await event.edit(
            "✏️ Send the new file name (with extension, e.g. video.mp4) as a message.",
            buttons=[[Button.inline("❌ Cancel", data="cancel")]],
        )


@client.on(events.NewMessage())
async def file_handler(event):
    if not event.file:
        return  # dosya değilse (düz metin vs.) yoksay

    if not is_allowed(event.sender_id):
        await event.respond("You're not allowed to use this bot.")
        return

    filename = event.file.name or f"telegram_file_{int(time.time())}"
    total_size = event.file.size or 0

    status = await event.respond(
        f"📥 Downloading: {filename} ({human_size(total_size)})"
    )

    local_path = os.path.join(DOWNLOAD_DIR, filename)

    last_edit = {"t": 0.0}

    async def progress(current, total):
        now = time.time()
        if now - last_edit["t"] < 3 and current != total:
            return
        last_edit["t"] = now
        percent = (current / total * 100) if total else 0
        try:
            await status.edit(
                f"📥 Downloading: {filename}\n{human_size(current)} / {human_size(total)} ({percent:.0f}%)"
            )
        except Exception:
            pass

    try:
        await client.download_media(event.message, file=local_path, progress_callback=progress)

        await status.edit(f"☁️ Uploading to Google Drive: {filename}")

        loop = asyncio.get_event_loop()
        link = await loop.run_in_executor(None, upload_to_drive, local_path, filename)

        await status.edit(f"✅ Uploaded: {filename}\n{link}")

    except Exception as e:
        log.exception("Error while processing the file")
        await status.edit(f"❌ Something went wrong: {e}")

    finally:
        if os.path.exists(local_path):
            os.remove(local_path)


async def main():
    await client.start(bot_token=BOT_TOKEN)
    me = await client.get_me()
    log.info(f"Bot started: @{me.username}")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
