import csv
import json
import sys
from itertools import zip_longest

def main():
    import argparse
    parser = argparse.ArgumentParser(description='Convert CSV to JSON')
    parser.add_argument('csv_file', help='Path to CSV file')
    parser.add_argument('--pretty', action='store_true', help='Produce pretty-printed JSON')
    args = parser.parse_args()

    with open(args.csv_file, newline='') as csvfile:
        reader = csv.reader(csvfile)
        headers = next(reader)
        rows = []
        for row in reader:
            # Pad row with empty strings if shorter than headers
            row = list(row)
            row += [''] * (len(headers) - len(row))
            rows.append({headers[i]: row[i] for i in range(len(headers))})

    json_data = json.dumps(rows, indent=2 if args.pretty else None)

    print(json_data)

if __name__ == '__main__':
    main()