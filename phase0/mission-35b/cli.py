#!/usr/bin/env python3
"""csv2json: Convert a CSV file to a JSON array of row objects."""

import argparse
import csv
import json
import sys


def main():
    parser = argparse.ArgumentParser(
        description="Convert a CSV file to a JSON array."
    )
    parser.add_argument("csv_file", help="Path to the input CSV file")
    parser.add_argument(
        "--pretty",
        action="store_true",
        default=False,
        help="Produce indented (pretty-printed) JSON output.",
    )

    args = parser.parse_args()

    with open(args.csv_file, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = []
        for row in reader:
            # DictReader returns strings; missing columns become None.
            # Convert any None to empty string per contract.
            cleaned = {}
            for key, value in row.items():
                if key is not None:
                    cleaned[key] = "" if value is None else value
            rows.append(cleaned)

    indent = 2 if args.pretty else None
    print(json.dumps(rows, indent=indent))


if __name__ == "__main__":
    main()
