import os
import time
import asyncio
import logging

from telethon import TelegramClient, events
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
DRIVE_FOLDER_ID = os.environ.get("DRIVE_FOLDER_ID", "").strip() or None

# Opsiyonel ama önerilir: sadece senin kullanabilmen için Telegram user id'lerin
# virgülle ayrılmış şekilde. Boş bırakılırsa bot HERKESE açık olur (önerilmez).
ALLOWED_USER_IDS = {
    int(x) for x in os.environ.get("ALLOWED_USER_IDS", "").split(",") if x.strip()
}

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
    # İstemezsen bu bloğu silebilirsin, o zaman link sadece senin Drive hesabında görünür olur.
    service.permissions().create(
        fileId=file_id,
        body={"role": "reader", "type": "anyone"},
    ).execute()

    return response.get("webViewLink", f"https://drive.google.com/file/d/{file_id}/view")


# ---------------------------------------------------------------------------
# Telegram bot
# ---------------------------------------------------------------------------

client = TelegramClient(StringSession(), API_ID, API_HASH)


def human_size(num_bytes: int) -> str:
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
    await event.respond(
        "Merhaba! Bana bir dosya (belge, video, ses, foto) gönder, "
        "Google Drive'ına yükleyip linkini sana geri göndereyim.\n\n"
        "20MB gibi bir sınır yok, Telegram'ın izin verdiği en büyük dosyayı bile deneyebilirsin."
    )


@client.on(events.NewMessage())
async def file_handler(event):
    if not event.file:
        return  # dosya değilse (düz metin vs.) yoksay

    if not is_allowed(event.sender_id):
        await event.respond("Bu botu kullanma yetkin yok.")
        return

    filename = event.file.name or f"telegram_dosya_{int(time.time())}"
    total_size = event.file.size or 0

    status = await event.respond(
        f"📥 İndiriliyor: {filename} ({human_size(total_size)})"
    )

    local_path = os.path.join(DOWNLOAD_DIR, filename)

    last_edit = {"t": 0.0}

    async def progress(current, total):
        now = time.time()
        # Telegram flood limitine takılmamak için en fazla 3 saniyede bir güncelle
        if now - last_edit["t"] < 3 and current != total:
            return
        last_edit["t"] = now
        percent = (current / total * 100) if total else 0
        try:
            await status.edit(
                f"📥 İndiriliyor: {filename}\n{human_size(current)} / {human_size(total)} ({percent:.0f}%)"
            )
        except Exception:
            pass

    try:
        await client.download_media(event.message, file=local_path, progress_callback=progress)

        await status.edit(f"☁️ Google Drive'a yükleniyor: {filename}")

        loop = asyncio.get_event_loop()
        link = await loop.run_in_executor(None, upload_to_drive, local_path, filename)

        await status.edit(f"✅ Yüklendi: {filename}\n{link}")

    except Exception as e:
        log.exception("Dosya işlenirken hata oluştu")
        await status.edit(f"❌ Bir hata oluştu: {e}")

    finally:
        if os.path.exists(local_path):
            os.remove(local_path)


async def main():
    await client.start(bot_token=BOT_TOKEN)
    me = await client.get_me()
    log.info(f"Bot başladı: @{me.username}")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
