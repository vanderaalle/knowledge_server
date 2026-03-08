#!/usr/bin/env python3
"""Download IRCAM Fundamental Books from Anna's Archive"""
import sys
sys.path.insert(0, '~/Desktop/python/neworchestra2/knowledge_server')
from search_annas import download_book

# Books to download with their MD5 hashes
books_to_download = [
    # Grisey - Fondements d'une écriture
    ("1e58481ec827982cea9620af351c71fb", "Grisey_Fondements_ecriture.pdf"),
    # Boulez - Penser la musique aujourd'hui
    ("a268862bf9274e58918f6375911e0b47", "Boulez_Penser_musique.pdf"),
    # Curtis Roads - The Computer Music Tutorial (2nd edition 2023)
    ("28b70543eec3efcb3f74203838b45a76", "Roads_Computer_Music_Tutorial_2nd.pdf"),
    # Miranda - Computer Sound Design
    ("bb8131099aa0c24bf31fc8a3657222c0", "Miranda_Computer_Sound_Design.pdf"),
    # Benade - Fundamentals of Musical Acoustics
    ("f53136e5332585f1e416f286e1055169", "Benade_Fundamentals_Musical_Acoustics.pdf"),
    # Bregman - Auditory Scene Analysis
    ("3b626acf310760bf918c2790158f0efd", "Bregman_Auditory_Scene_Analysis.pdf"),
    # Gardner Read - Music Notation
    ("ba82746584d4ec32e5a5e11eb38964e2", "Read_Music_Notation.pdf"),
]

output_dir = "~/Desktop/python/neworchestra2/alias_books/IRCAM_Fundamentals"

import os
os.makedirs(output_dir, exist_ok=True)

print("=" * 60)
print("📚 Downloading IRCAM Fundamental Books")
print("=" * 60)

for md5, filename in books_to_download:
    print(f"\n📥 Downloading: {filename}")
    print(f"   MD5: {md5}")
    result = download_book(md5, output_dir)
    if result:
        print(f"   ✅ Success: {result}")
    else:
        print(f"   ❌ Failed")

print("\n" + "=" * 60)
print("✅ Download batch complete!")
print(f"📁 Files saved to: {output_dir}")
print("=" * 60)
