#!/usr/bin/env python3
"""
Anna's Archive Search Tool with Semantic Capabilities
"""

import sys
import argparse
import requests
import urllib.parse
from bs4 import BeautifulSoup
import ollama

# Configuration
ANNAS_URL = "https://annas-archive.li"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

def expand_query_with_ollama(query, model="nomic-embed-text"):
    """
    Expands a natural language query into search keywords using Ollama.
    """
    print(f"🧠 Thinking about: '{query}'...")
    
    prompt = f"""
    You are a search expert for a library. 
    Convert the following natural language request into a specific, keyword-based search query optimized for a book database.
    Focus on finding the *best* keywords (author, title, subject). 
    Do not add unrelated words.
    
    Request: "{query}"
    
    Output ONLY the keywords, nothing else. for example:
    Request: "books about space travel by asimov" -> "Asimov space travel"
    Request: "libri tristi sui robot" -> "robot sad fiction"
    
    Keywords:
    """
    
    try:
        # Use a lightweight model if possible, or fall back to user's default
        # Since manual_search.py uses 'nomic-embed-text', we might need a chat model.
        # Let's try 'llama3' or 'mistral' if available, otherwise 'nomic-embed-text' is for embeddings, not chat.
        # We'll use the 'generate' API.
        response = ollama.generate(model='deepseek-r1:7b', prompt=prompt, stream=False)
        if 'response' in response:
            response_text = response['response']
            # Remove <think>...</think> blocks common in reasoning models
            import re
            response_text = re.sub(r'<think>.*?</think>', '', response_text, flags=re.DOTALL).strip()
            
            keywords = response_text.replace('"', '')
            print(f"💡 Expanded to: '{keywords}'")
            return keywords
    except Exception as e:
        print(f"⚠️  Ollama error: {e}. Using original query.")
    
    return query

def search_annas(query, limit=10, lang='', ext='', sort=''):
    """
    Searches Anna's Archive and parses results.
    """
    params = {'q': query}
    if lang: params['lang'] = lang
    if ext: params['ext'] = ext
    if sort: params['sort'] = sort
    
    url = f"{ANNAS_URL}/search"
    print(f"🔎 Searching {url} for '{query}'...")
    
    try:
        response = requests.get(url, params=params, headers={'User-Agent': USER_AGENT}, timeout=15)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Anna's Archive structure varies, but usually results are in main content
        # We look for links that look like /md5/... which are the detail pages
        
        results = []
        # Main results are often in a list with class specifically for results
        # A generic way is to find all 'a' tags with href starting with '/md5/'
        # This usually links to the book details.
        
        seen_md5s = set()
        
        for a_tag in soup.find_all('a', href=True):
            href = a_tag['href']
            if '/md5/' in href:
                try:
                    md5 = href.split('/md5/')[1].split('?')[0]
                except IndexError:
                    continue

                # The first link is usually the cover image (empty text), the second is the title
                text = a_tag.get_text(separator=" ", strip=True)
                if not text or len(text) < 3:
                     continue
                
                if md5 in seen_md5s:
                    continue

                # Found a valid title link
                seen_md5s.add(md5)
                
                # Try to extract author and publisher from siblings
                # The structure is usually: Title <a>, Author <a>, Publisher <a>
                author = "Unknown"
                publisher = ""
                
                # Check next siblings for author
                next_a = a_tag.find_next_sibling('a')
                if next_a and '/search?q=' in next_a.get('href', ''):
                    author = next_a.get_text(strip=True)
                    
                    # Check next sibling for publisher/year
                    next_next_a = next_a.find_next_sibling('a')
                    if next_next_a and '/search?q=' in next_next_a.get('href', ''):
                        publisher = next_next_a.get_text(strip=True)

                description = f"{author} - {publisher}" if publisher else author
                
                results.append({
                    'title': text,
                    'link': f"{ANNAS_URL}{href}",
                    'md5': md5,
                    'description': description
                })
                
                if len(results) >= limit:
                    break
        
        if not results:
            print("⚠️ Parsed 0 results. Dumping HTML for debugging...")
            with open("debug_annas.html", "wb") as f:
                f.write(response.content)
        
        return results

    except Exception as e:
        print(f"❌ Error searching Anna's Archive: {e}")
        return []

def get_details(md5):
    url = f"{ANNAS_URL}/md5/{md5}"
    print(f"🔎 Fetching details for MD5: {md5}...")
    
    try:
        response = requests.get(url, headers={'User-Agent': USER_AGENT}, timeout=15)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        
        details = {
            'md5': md5,
            'title': soup.find('div', class_='text-3xl font-bold').get_text(strip=True) if soup.find('div', class_='text-3xl font-bold') else 'Unknown Title',
            'cover': soup.find('img', alt='cover')['src'] if soup.find('img', alt='cover') else None,
            'download_links': []
        }
        
        # Slow Partner Server links
        for a in soup.find_all('a', href=True):
            if '/slow_download/' in a['href']:
                details['download_links'].append({
                    'type': 'Slow Partner Server',
                    'url': f"{ANNAS_URL}{a['href']}"
                })
            elif 'libgen.li' in a['href']:
                details['download_links'].append({
                    'type': 'Libgen.li Mirror',
                    'url': a['href']
                })
            elif a['href'].startswith('ipfs://'):
                cid = a['href'].replace('ipfs://', '')
                details['download_links'].append({
                    'type': 'IPFS Gateway (ipfs.io)',
                    'url': f"https://ipfs.io/ipfs/{cid}"
                })
                details['download_links'].append({
                    'type': 'IPFS Gateway (dweb.link)',
                    'url': f"https://dweb.link/ipfs/{cid}"
                })
        
        return details

    except Exception as e:
        print(f"❌ Error fetching details: {e}")
        return None

def main():
    parser = argparse.ArgumentParser(description="Search Anna's Archive from CLI")
    parser.add_argument('query', nargs='*', help="Search query or MD5")
    parser.add_argument('--semantic', action='store_true', help="Use Semantics (Ollama)")
    parser.add_argument('--limit', type=int, default=10, help="Max results")
    parser.add_argument('--lang', type=str, default='', help="Language code (e.g. 'it', 'en')")
    parser.add_argument('--ext', type=str, default='', help="File extension (e.g. 'pdf', 'epub')")
    parser.add_argument('--md5', type=str, help="Get download links for a specific MD5")
    
    args = parser.parse_args()
    
    if args.md5:
        details = get_details(args.md5)
        if details:
            print(f"\n📖 Title: {details['title']}")
            print(f"🔑 MD5: {details['md5']}")
            if details['cover']:
                print(f"🖼️  Cover: {details['cover']}")
            
            print("\n⬇️  Download Links:")
            for link in details['download_links']:
                print(f"   🔗 {link['type']}: {link['url']}")
            
            # Suggest opening the first slow link
            slow_links = [l for l in details['download_links'] if 'Slow' in l['type']]
            if slow_links:
                print(f"\n💡 Tip: Provide this URL to your browser: {slow_links[0]['url']}")
        return

    if not args.query:
        print("Please provide a search query or use --md5")
        return

    query = " ".join(args.query)
    
    if args.semantic:
        query = expand_query_with_ollama(query)
        
    results = search_annas(query, limit=args.limit, lang=args.lang, ext=args.ext)
    
    if not results:
        print("\n😔 No results found. Check debug_annas.html if created.")
    else:
        print(f"\n📚 Found {len(results)} results:\n")
        for i, res in enumerate(results):
            print(f"{i+1}. {res['title']}")
            print(f"   🔗 {res['link']}") 
            print(f"   🔑 MD5: {res['md5']}")
            print(f"   📝 {res['description']}")
            print("   " + "-"*40)
        
        while True:
            try:
                choice = input("\n📥 Enter number to download (or 'q' to quit): ").strip()
                if choice.lower() == 'q':
                    break
                
                idx = int(choice) - 1
                if 0 <= idx < len(results):
                    target = results[idx]
                    details = get_details(target['md5'])
                    
                    if not details or not details['download_links']:
                        print("❌ No download links found.")
                        continue
                        
                    print(f"\n🚀 Attempting to download: {details['title']}")
                    
                    # Create output directory
                    output_dir = "alias_books"
                    os.makedirs(output_dir, exist_ok=True)
                    
                    # sanitize filename
                    import re
                    safe_title = re.sub(r'[\\/*?:"<>|]', "", details['title'])
                    # Try to guess extension from the description or default to unknown
                    ext = "epub" # default
                    if "pdf" in target['description'].lower(): ext = "pdf"
                    elif "mobi" in target['description'].lower(): ext = "mobi"
                    
                    filename = f"{safe_title}.{ext}"
                    filepath = os.path.join(output_dir, filename)
                    
                    success = False
                    
                    # Prioritize IPFS gateways as they are most likely to work automated
                    ipfs_links = [l for l in details['download_links'] if 'IPFS' in l['type']]
                    other_links = [l for l in details['download_links'] if 'IPFS' not in l['type']]
                    
                    # Add more public gateways to try
                    if ipfs_links:
                        cid = ipfs_links[0]['url'].split('ipfs/')[-1]
                        gateways = [
                            f"https://ipfs.io/ipfs/{cid}",
                            f"https://dweb.link/ipfs/{cid}",
                            f"https://cloudflare-ipfs.com/ipfs/{cid}",
                            f"https://gateway.pinata.cloud/ipfs/{cid}",
                            f"https://ipfs.eth.aragon.network/ipfs/{cid}"
                        ]
                        # Add constructed gateways to the list of links to try
                        for g in gateways:
                            ipfs_links.append({'type': 'IPFS Gateway (Extra)', 'url': g})

                    all_links = ipfs_links + other_links

                    for link in all_links:
                        print(f"Trying {link['type']}...")
                        try:
                            # Stream download
                            with requests.get(link['url'], stream=True, timeout=15) as r:
                                r.raise_for_status()
                                with open(filepath, 'wb') as f:
                                    for chunk in r.iter_content(chunk_size=8192): 
                                        f.write(chunk)
                            print(f"✅ Downloaded to: {filepath}")
                            success = True
                            break
                        except Exception as e:
                            print(f"   ⚠️ Failed: {e}")
                    
                    if not success:
                        print("❌ All automated download attempts failed.")
                        print("👉 Please try downloading manually from one of these links:")
                        for link in details['download_links']:
                            print(f"   {link['url']}")

                else:
                    print("❌ Invalid number.")
            except ValueError:
                print("❌ Please enter a number.")
            except KeyboardInterrupt:
                break

if __name__ == "__main__":
    main()
