"""
CC0 Royalty-Free Ses Dosyaları İndirme Scripti
Kaynak: Pixabay.com (Lisans: CC0 / Royalty-Free)

Kullanım:
  cd backend/sounds
  python download_sounds.py

Dosyalar bu klasöre indirilir. Bir kez çalıştırmanız yeterli.
"""

import urllib.request
import os
import sys

SOUNDS_DIR = os.path.dirname(os.path.abspath(__file__))

# Pixabay CC0/Royalty-Free ses efektleri
# Not: URL'ler Pixabay ücretsiz lisansı kapsamındadır (ticari kullanım dahil)
SOUND_URLS = {
    "laugh.wav":         "https://cdn.pixabay.com/download/audio/2022/01/18/audio_d0c34c7a1a.mp3",
    "applause.wav":      "https://cdn.pixabay.com/download/audio/2022/03/15/audio_1a609c5d68.mp3",
    "airhorn.wav":       "https://cdn.pixabay.com/download/audio/2021/08/04/audio_c6ccf3232f.mp3",
    "whoosh.wav":        "https://cdn.pixabay.com/download/audio/2022/03/10/audio_270f49b8d4.mp3",
    "sad_trombone.wav":  "https://cdn.pixabay.com/download/audio/2021/08/09/audio_dc39bde808.mp3",
    "drum_hit.wav":      "https://cdn.pixabay.com/download/audio/2022/01/20/audio_d7378b97b1.mp3",
    "bell.wav":          "https://cdn.pixabay.com/download/audio/2022/01/18/audio_4166aedc3d.mp3",
    "pop.wav":           "https://cdn.pixabay.com/download/audio/2021/08/04/audio_12b0c7443c.mp3",
    "beep.wav":          "https://cdn.pixabay.com/download/audio/2022/03/24/audio_c73d70b6d3.mp3",
    "crowd_cheer.wav":   "https://cdn.pixabay.com/download/audio/2022/03/15/audio_f05c8dd3c8.mp3",
}


def download(filename: str, url: str) -> bool:
    out_path = os.path.join(SOUNDS_DIR, filename)
    if os.path.isfile(out_path):
        print(f"  ✓ {filename} zaten mevcut, atlandı.")
        return True
    try:
        print(f"  ↓ {filename} indiriliyor...")
        headers = {"User-Agent": "Mozilla/5.0 (Clipla Sound Downloader)"}
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as r, open(out_path, "wb") as f:
            f.write(r.read())
        size_kb = os.path.getsize(out_path) // 1024
        print(f"  ✓ {filename} indirildi ({size_kb} KB)")
        return True
    except Exception as e:
        print(f"  ✗ {filename} indirilemedi: {e}")
        if os.path.isfile(out_path):
            os.remove(out_path)
        return False


if __name__ == "__main__":
    os.makedirs(SOUNDS_DIR, exist_ok=True)
    print(f"\nSes efektleri indiriliyor → {SOUNDS_DIR}\n")
    ok = 0
    for fname, url in SOUND_URLS.items():
        if download(fname, url):
            ok += 1
    print(f"\n{ok}/{len(SOUND_URLS)} dosya hazır.")
    if ok < len(SOUND_URLS):
        print("Eksik dosyalar için FFmpeg sentezi devreye girecek.")
    print()
