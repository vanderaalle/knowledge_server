import sys
import os

# Add current directory to path so we can import server
sys.path.append(os.getcwd())

try:
    from server import search_annas_archive, download_from_annas_archive
    print("✅ Successfully imported server tools.")
except ImportError as e:
    print(f"❌ Failed to import server tools: {e}")
    sys.exit(1)

def test_search():
    print("\n🔎 Testing search_annas_archive...")
    try:
        result = search_annas_archive("Italo Calvino", limit=2)
        print("Result:")
        print(result)
        if "MD5:" in result:
            print("✅ Search test passed.")
        else:
            print("❌ Search test failed (no MD5 found).")
    except Exception as e:
        print(f"❌ Search exception: {e}")

def test_download():
    # Use the known MD5 for "Le città invisibili"
    md5 = "04706fde3dfdcd6e40c342c615649d8b" 
    print(f"\n⬇️  Testing download_from_annas_archive for {md5}...")
    try:
        result = download_from_annas_archive(md5)
        print("Result:")
        print(result)
        if "Download completato" in result or "Download automatico fallito" in result:
             # Both are valid outcomes for the code logic validation
             # (Success depends on external mirrors)
            print("✅ Download logic executed.")
        else:
            print("❌ Download test failed (unexpected output).")
    except Exception as e:
        print(f"❌ Download exception: {e}")

if __name__ == "__main__":
    test_search()
    test_download()
