import json
import re
from typing import Any, Dict, List, Tuple

import pandas as pd

# Regex to salvage completed JSON block objects from an incomplete array
BLOCK_REGEX = re.compile(
    r'\{\s*"text"\s*:\s*"(?P<text>(?:\\.|[^"\\])*)"\s*,\s*'
    r'"label"\s*:\s*"(?P<label>[^"]+)"\s*,\s*'
    r'"confidence"\s*:\s*(?P<confidence>[0-9.]+)\s*\}'
)

def salvage_blocks(content_str: str) -> Tuple[List[Dict[str, Any]], str]:
    """
    Extracts all fully closed block objects and isolates the remaining trailing text.
    """
    blocks = []
    last_match_end = 0
    
    for match in BLOCK_REGEX.finditer(content_str):
        data = match.groupdict()
        try:
            # Decode escaped characters properly
            clean_text = json.loads(f'"{data["text"]}"')
            blocks.append({
                "text": clean_text,
                "label": data["label"],
                "confidence": float(data["confidence"])
            })
            last_match_end = match.end()
        except (json.JSONDecodeError, ValueError):
            continue
            
    trailing_fragment = content_str[last_match_end:].strip()
    return blocks, trailing_fragment

def build_dataset_with_recovery(
    input_file: str,
    output_valid_csv: str,
    output_review_jsonl: str,
    confidence_threshold: float = 0.85
) -> None:
    valid_records: List[Dict[str, Any]] = []
    review_queue: List[Dict[str, Any]] = []
    
    total_jobs = 0
    total_salvaged_jobs = 0

    with open(input_file, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
                
            total_jobs += 1
            row = json.loads(line)
            custom_id = row.get("custom_id", "unknown_id")
            
            # Skip failed API requests without a response body
            if row.get("error") or not row.get("response", {}).get("body"):
                continue
                
            choice = row["response"]["body"]["choices"][0]
            finish_reason = choice.get("finish_reason")
            content_str = choice.get("message", {}).get("content", "")
            
            blocks = []
            is_corrupted = finish_reason in ["content_filter", "length"]
            trailing_raw = ""

            if finish_reason == "stop":
                try:
                    parsed = json.loads(content_str)
                    blocks = parsed.get("blocks", [])
                except json.JSONDecodeError:
                    is_corrupted = True
                    blocks, trailing_raw = salvage_blocks(content_str)
            else:
                blocks, trailing_raw = salvage_blocks(content_str)

            # Route complete valid blocks to the dataset
            for b in blocks:
                conf = b.get("confidence", 0.0)
                if conf >= confidence_threshold:
                    valid_records.append({
                        "source_id": custom_id,
                        "text": b.get("text", "").strip(),
                        "label": b.get("label", "UNKNOWN"),
                        "confidence": conf
                    })

            # If truncated or flagged, push to the review queue for manual filling
            if is_corrupted:
                total_salvaged_jobs += 1
                review_queue.append({
                    "custom_id": custom_id,
                    "finish_reason": finish_reason,
                    "salvaged_block_count": len(blocks),
                    "salvaged_blocks": blocks,
                    "truncated_trailing_raw": trailing_raw
                })

    # Save outputs
    df_valid = pd.DataFrame(valid_records)
    df_valid.to_csv(output_valid_csv, index=False, encoding="utf-8")
    
    with open(output_review_jsonl, "w", encoding="utf-8") as f_out:
        for item in review_queue:
            f_out.write(json.dumps(item, ensure_ascii=False) + "\n")

    print("=== Extraction Summary ===")
    print(f"Total jobs processed: {total_jobs}")
    print(f"Clean blocks saved: {len(df_valid)}")
    print(f"Jobs needing review/fill: {len(review_queue)}")

if __name__ == "__main__":
    build_dataset_with_recovery(
        input_file="ml/batch_6a8f0c7066648190b47946bfa09d66d5_output.jsonl",
        output_valid_csv="dataset_valid.csv",
        output_review_jsonl="corrupted_review_queue.jsonl",
        confidence_threshold=0.85
    )