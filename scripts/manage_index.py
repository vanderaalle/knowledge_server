#!/usr/bin/env python3
"""
CLI tool for managing the knowledge base index.
This script can be used to index large collections of PDFs from the command line.
"""

import argparse
import sys
import os
import asyncio

# Add the knowledge_server directory to the path so we can import server modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import _index_directory

def main():
    parser = argparse.ArgumentParser(description="Manage the knowledge base index")
    parser.add_argument(
        "action",
        choices=["index"],
        help="Action to perform"
    )
    parser.add_argument(
        "--path",
        "-p",
        required=True,
        help="Path to the directory containing PDF files to index"
    )
    parser.add_argument(
        "--delete-all",
        action="store_true",
        help="Delete existing index before starting"
    )
    
    parser.add_argument(
        "--ocr",
        action="store_true",
        help="Enable OCR for scanned PDFs (slower)"
    )
    
    args = parser.parse_args()
    
    if args.action == "index":
        try:
            # _index_directory is synchronous in the optimized server.py
            count, skipped, errors, ocr = _index_directory(args.path, use_ocr=args.ocr, delete_all=args.delete_all)
            print(f"Successfully indexed {count} documents from {args.path}")
            print(f"Skipped: {skipped}, Errors: {errors}, OCR Skipped: {ocr}")
        except Exception as e:
            print(f"Error indexing documents: {e}")
            sys.exit(1)

if __name__ == "__main__":
    main()
