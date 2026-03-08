#!/usr/bin/env python3
"""
Update Knowledge Base Index Paths after Reorganization
"""

import os
import sys
import json
import hashlib
import argparse
from pathlib import Path
from qdrant_client.models import PointStruct, Filter, FieldCondition, MatchValue
from qdrant_client.http import models

# Add current directory to sys.path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from server import get_db, COLLECTION_NAME, _calculate_file_hash

def update_index(map_file):
    map_path = Path(map_file)
    if not map_path.exists():
        print(f"Map file not found: {map_path}")
        return

    with open(map_path, "r") as f:
        moved_files = json.load(f)

    db_client = get_db()
    client = db_client.client
    
    print(f"Updating index for {len(moved_files)} moved files...")
    
    updated_count = 0
    errors = 0
    
    for old_path, new_path in moved_files.items():
        try:
            # 1. Verify file exists at new path
            if not os.path.exists(new_path):
                print(f"Skipping {old_path}: New file {new_path} not found.")
                continue
            
            # 2. Get points for OLD path
            # We filter by 'source_path' metadata
            scroll_filter = Filter(
                must=[
                    FieldCondition(
                        key="metadata.source_path",
                        match=MatchValue(value=old_path)
                    )
                ]
            )
            
            points_to_move = []
            offset = None
            
            while True:
                result, next_offset = client.scroll(
                    collection_name=COLLECTION_NAME,
                    scroll_filter=scroll_filter,
                    limit=100,
                    offset=offset,
                    with_payload=True,
                    with_vectors=True
                )
                points_to_move.extend(result)
                if not next_offset:
                    break
                offset = next_offset
            
            if not points_to_move:
                print(f"No index entries found for {os.path.basename(old_path)}")
                continue
                
            print(f"Moving {len(points_to_move)} chunks for {os.path.basename(new_path)}...")
            
            new_points = []
            old_point_ids = []
            
            relative_path = os.path.relpath(new_path, os.path.dirname(os.path.dirname(new_path))) 
            # This relative path logic might need adjustment depending on where 'root' is. 
            # server.py uses os.path.relpath(file_path, directory_path).
            # We will approximate it or just use the parent folder name.
            
            for point in points_to_move:
                old_point_ids.append(point.id)
                
                payload = point.payload
                metadata = payload.get("metadata", {})
                
                # Update Metadata
                metadata["source_path"] = new_path
                # Try to keep 'source' somewhat valid (relative)
                metadata["source"] = str(Path(new_path).name) 
                
                chunk_index = metadata.get("chunk_index", 0)
                
                # Calculate New ID
                # ID = md5(path + chunk_index)
                new_id = hashlib.md5(
                    (str(new_path) + str(chunk_index)).encode()
                ).hexdigest()
                
                new_points.append(
                    PointStruct(
                        id=new_id,
                        vector=point.vector,
                        payload=payload
                    )
                )
            
            # Atomic-ish parameters: Upsert then Delete
            if new_points:
                client.upsert(
                    collection_name=COLLECTION_NAME,
                    points=new_points
                )
                client.delete(
                    collection_name=COLLECTION_NAME,
                    points_selector=models.PointIdsList(points=old_point_ids)
                )
                updated_count += 1
                
        except Exception as e:
            print(f"Error updating {old_path}: {e}")
            errors += 1

    print(f"Update Complete. Updated: {updated_count}, Errors: {errors}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Update Index Paths.")
    parser.add_argument("map_file", help="Path to 'moved_files_map.json'")
    
    args = parser.parse_args()
    
    update_index(args.map_file)
