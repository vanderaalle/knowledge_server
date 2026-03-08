import requests
from bs4 import BeautifulSoup

ANNAS_URL = "https://annas-archive.li"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

def verify_link():
    # The slow download link from the debug file
    # /slow_download/04706fde3dfdcd6e40c342c615649d8b/0/0
    # Note: These links might expire or be session-specific, but let's try.
    # Actually, the link contains /0/0 which looks like an index, maybe stable.
    
    # First, let's hit the detail page to get a fresh link just in case
    md5 = "04706fde3dfdcd6e40c342c615649d8b"  # Le città invisibili
    url = f"{ANNAS_URL}/md5/{md5}"
    print(f"Fetching detail page: {url}")
    
    session = requests.Session()
    session.headers.update({
        'User-Agent': USER_AGENT,
        'Referer': url
    })
    
    try:
        response = session.get(url, timeout=15)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Find IPFS CID
        ipfs_cid = None
        for button in soup.find_all('button', onclick=True):
             # onclick="if (navigator.clipboard) { navigator.clipboard.writeText('bafykbzacedqspjqioffutjuwmcgxdetirwcjt4oqkyif4y3ioqp2aqxr6uire')...
             if 'navigator.clipboard.writeText' in button['onclick']:
                 text_content = button.get_text(strip=True)
                 # CIDs usually start with Qm (v0) or bafy (v1)
                 # But we can also look at the aria-label or just the text
                 # In debug_detail, the CID is in the text of the button's span? 
                 # Actually the text is inside: <span class="...">bafykbzacedqspjqioffutjuwmcgxdetirwcjt4oqkyif4y3ioqp2aqxr6uire</span>
                 
                 # Let's look for the link "ipfs://"
                 pass

        # Better: find the link with href starting with ipfs://
        for a in soup.find_all('a', href=True):
            if a['href'].startswith('ipfs://'):
                ipfs_cid = a['href'].replace('ipfs://', '')
                print(f"Found IPFS CID: {ipfs_cid}")
                break
        
        if not ipfs_cid:
            print("Could not find an IPFS CID.")
            return

        # Try a few gateways
        gateways = [
            "https://ipfs.io/ipfs/",
            "https://dweb.link/ipfs/",
            "https://cloudflare-ipfs.com/ipfs/"
        ]
        
        for gateway in gateways:
            full_dl_url = f"{gateway}{ipfs_cid}"
            print(f"Attempting to fetch via gateway: {full_dl_url}")
            
            try:
                # STREAMING request to avoid downloading the whole file if it's huge
                dl_response = requests.get(full_dl_url, stream=True, timeout=10)
                
                print(f"Response URL: {dl_response.url}")
                print(f"Status Code: {dl_response.status_code}")
                
                if dl_response.status_code == 200:
                    print(f"SUCCESS! Found working gateway: {gateway}")
                    # Check headers for content type/disposition
                    print(f"Headers: {dl_response.headers}")
                    break
                else:
                    print(f"Gateway failed with status {dl_response.status_code}")
            except Exception as e:
                print(f"Gateway failed with error: {e}")
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    verify_link()
