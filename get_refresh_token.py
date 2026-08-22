"""
Bu script SADECE senin kendi bilgisayarında, BİR KERE çalıştırılır.
Amacı: Google Drive'a erişim için gereken "refresh_token" değerini almak.

Kullanımı:
1) Google Cloud Console'dan indirdiğin OAuth istemci dosyasını bu klasöre koy
   ve adını "client_secret.json" yap.
2) Terminalde:  pip install google-auth-oauthlib
3) Terminalde:  python get_refresh_token.py
4) Açılan tarayıcı sayfasında kendi Google hesabınla giriş yap ve izin ver.
5) Terminalde çıkan REFRESH TOKEN, CLIENT ID ve CLIENT SECRET değerlerini
   kopyalayıp Railway'deki ortam değişkenlerine yapıştıracaksın.
"""

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/drive"]

flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)

# access_type="offline" + prompt="consent" -> refresh_token'ın kesin dönmesini sağlar
creds = flow.run_local_server(
    port=0,
    access_type="offline",
    prompt="consent",
)

print("\n\n================ SONUÇLAR ================")
print("GOOGLE_CLIENT_ID     =", creds.client_id)
print("GOOGLE_CLIENT_SECRET =", creds.client_secret)
print("GOOGLE_REFRESH_TOKEN =", creds.refresh_token)
print("============================================\n")
print("Bu 3 değeri Railway'deki ortam değişkenlerine ekle.")
