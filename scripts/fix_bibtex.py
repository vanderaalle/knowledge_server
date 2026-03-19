#!/usr/bin/env python3
"""
Robust BibTeX fixer: split on @ markers, clean each entry.
"""
import re
import sys

KEEP_FIELDS = {'title', 'author', 'year', 'journal', 'booktitle', 'volume',
               'number', 'pages', 'publisher', 'doi', 'isbn', 'editor',
               'school', 'institution', 'howpublished', 'month', 'series',
               'edition', 'chapter', 'issn', 'url'}


def clean_key(raw):
    raw = raw.strip().rstrip(',').strip()
    raw = re.sub(r'\s+', '', raw)
    raw = re.sub(r'[^a-zA-Z0-9_]', '', raw)
    return raw or ''


def make_key(field_dict):
    """Generate a key from author+year or title."""
    author = field_dict.get('author', '')
    year = field_dict.get('year', '')
    title = field_dict.get('title', '')

    yr = re.search(r'\d{4}', year)
    yr = yr.group() if yr else ''

    if author:
        surname = re.split(r'[,\s]', author.strip())[0]
        surname = re.sub(r'[^a-zA-Z]', '', surname)
        if surname and yr:
            return f"{surname}{yr}"
        elif surname:
            return surname

    if title:
        words = re.sub(r'[^a-zA-Z ]', '', title).split()
        return ''.join(w[:4].capitalize() for w in words[:3])

    return 'UNKNOWN'


def extract_field_value(text, start):
    """Extract value starting at position (after =), return (value, end_pos)."""
    i = start
    n = len(text)
    # skip whitespace
    while i < n and text[i] in ' \t':
        i += 1
    if i >= n:
        return '', i

    if text[i] == '{':
        depth = 1
        i += 1
        val_start = i
        while i < n and depth > 0:
            if text[i] == '{':
                depth += 1
            elif text[i] == '}':
                depth -= 1
            i += 1
        return text[val_start:i-1].strip(), i
    elif text[i] == '"':
        i += 1
        val_start = i
        while i < n and text[i] != '"':
            if text[i] == '\\':
                i += 1
            i += 1
        val = text[val_start:i].strip()
        if i < n:
            i += 1
        return val, i
    else:
        # unquoted
        val_start = i
        while i < n and text[i] not in ',\n}':
            i += 1
        return text[val_start:i].strip(), i


def parse_entry_fields(body):
    """Parse fields from entry body text."""
    fields = {}
    i = 0
    n = len(body)

    while i < n:
        # skip whitespace/commas
        while i < n and body[i] in ' \t\n\r,':
            i += 1
        if i >= n:
            break

        # read field name (up to = or whitespace)
        name_start = i
        while i < n and body[i] not in '=\n\r \t{}':
            i += 1
        fname = body[name_start:i].strip().lower()

        if not fname:
            i += 1
            continue

        # skip to =
        while i < n and body[i] != '=' and body[i] != '\n':
            i += 1

        if i >= n or body[i] == '\n':
            # no = found, not a valid field
            continue

        i += 1  # skip =

        fval, i = extract_field_value(body, i)

        if fname and fval:
            fields[fname] = fval

    return fields


def fix_entry(comments, etype, raw_body):
    """Returns clean BibTeX string or None."""
    # raw_body starts after @type{
    lines = raw_body.split('\n')

    # First line should be the key
    key_line = lines[0].strip().rstrip(',')
    body = '\n'.join(lines[1:])

    # Remove closing } from body
    body = body.rstrip()
    while body.endswith('}') or body.endswith(')'):
        body = body[:-1].rstrip()

    # Determine key
    key = ''
    if '=' not in key_line:
        key = clean_key(key_line)
    else:
        # key line is actually a field — prepend to body
        body = key_line + '\n' + body
        key = ''

    # Parse fields
    fields = parse_entry_fields(body)

    # Clean field values
    clean_fields = {}
    for fname, fval in fields.items():
        if fname not in KEEP_FIELDS:
            continue
        # Remove soft hyphens, control chars
        fval = fval.replace('\u00ad', '-')
        fval = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', fval)
        # Skip obvious garbage (e.g. OCR noise in author)
        if fname == 'author':
            alpha = re.sub(r'[^a-zA-Z]', '', fval)
            if len(alpha) < 2:
                continue
        clean_fields[fname] = fval.strip()

    # Must have title and author
    if 'title' not in clean_fields or 'author' not in clean_fields:
        return None

    # Generate key if missing/bad
    if not key or key == 'UNKNOWN':
        key = make_key(clean_fields)

    # Build output
    out = []
    if comments:
        out.append(comments)
    out.append(f'@{etype}{{{key},')
    for fname in ['author', 'title', 'year', 'journal', 'booktitle',
                  'publisher', 'volume', 'number', 'pages',
                  'doi', 'isbn', 'issn', 'editor', 'series',
                  'school', 'institution', 'howpublished', 'month',
                  'edition', 'chapter', 'url']:
        if fname in clean_fields:
            val = clean_fields[fname]
            out.append(f'  {fname} = {{{val}}},')
    out.append('}')
    return '\n'.join(out)


def main():
    infile = sys.argv[1] if len(sys.argv) > 1 else '/home/andrea/qdrant_generated.bib'
    outfile = sys.argv[2] if len(sys.argv) > 2 else '/home/andrea/qdrant_generated_fixed.bib'

    with open(infile, 'r', encoding='utf-8') as f:
        text = f.read()

    # Split on @entry_type{ boundaries, preserving preceding % comments
    # Strategy: split text into segments at each '@'
    # Each segment: (comments_before, @type{...until_next_@)

    # Find all @ positions (start of entries)
    at_positions = [m.start() for m in re.finditer(r'^@', text, re.MULTILINE)]

    segments = []
    for idx, pos in enumerate(at_positions):
        end = at_positions[idx + 1] if idx + 1 < len(at_positions) else len(text)
        segment = text[pos:end]

        # Find comments immediately before this @
        # Look backwards from pos for % lines
        pre_start = pos
        while pre_start > 0 and text[pre_start - 1] != '\n':
            pre_start -= 1
        # Go back further collecting % lines
        comment_lines = []
        scan = pre_start - 1
        while scan > 0:
            line_end = scan
            scan -= 1
            while scan > 0 and text[scan] != '\n':
                scan -= 1
            line = text[scan+1:line_end+1].strip()
            if line.startswith('%'):
                comment_lines.insert(0, line)
            else:
                break

        comments = '\n'.join(comment_lines)
        segments.append((comments, segment))

    print(f"Found {len(segments)} entries")

    seen_sources = {}
    out_lines = ['% Fixed BibTeX\n']
    written = skipped = dupes = 0

    for comments, segment in segments:
        # Parse entry type and body
        m = re.match(r'@(\w+)\s*[{(](.*)', segment, re.DOTALL)
        if not m:
            skipped += 1
            continue

        etype = m.group(1).lower()
        raw_body = m.group(2)

        # Get source/method from comments
        source_m = re.search(r'source:\s*(.+)', comments)
        method_m = re.search(r'method:\s*(.+)', comments)
        source = source_m.group(1).strip() if source_m else None
        method = method_m.group(1).strip() if method_m else 'llm'

        # Dedup
        if source:
            if source in seen_sources:
                prev = seen_sources[source]
                if prev.startswith('crossref') or not method.startswith('crossref'):
                    dupes += 1
                    continue
            seen_sources[source] = method

        # Handle crossref entries (already well-formed, just clean them)
        if method.startswith('crossref'):
            # Keep as-is but strip trailing whitespace and junk
            entry = segment.strip()
            # Ensure it ends with }
            if not entry.endswith('}'):
                entry += '\n}'
            if comments:
                out_lines.append(comments + '\n' + entry + '\n')
            else:
                out_lines.append(entry + '\n')
            written += 1
            continue

        fixed = fix_entry(comments, etype, raw_body)
        if fixed:
            out_lines.append(fixed + '\n')
            written += 1
        else:
            skipped += 1

    with open(outfile, 'w', encoding='utf-8') as f:
        f.write('\n'.join(out_lines))

    print(f"Written: {written} | Skipped: {skipped} | Dupes: {dupes}")
    print(f"Output: {outfile}")


if __name__ == '__main__':
    main()
