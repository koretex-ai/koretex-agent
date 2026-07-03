# csv2json

A Python CLI tool that converts a CSV file to a JSON array of row objects. Uses only the Python standard library (`argparse`, `csv`, `json`).

## Usage

```bash
python3 cli.py <csv_file> [--pretty]
```

### Arguments

- `csv_file` — Path to the input CSV file (positional).
- `--pretty` — Optional flag to produce indented (2-space) JSON output.

### Examples

**Basic conversion:**

```bash
python3 cli.py data.csv
```

Output:
```json
[{"name": "John", "age": "30"}, {"name": "Jane", "age": "25"}]
```

**Pretty-printed output:**

```bash
python3 cli.py --pretty data.csv
```

Output:
```json
[
  {
    "name": "John",
    "age": "30"
  },
  {
    "name": "Jane",
    "age": "25"
  }
]
```

## Features

- **Quoted fields:** Fields containing commas inside quotes are parsed as a single value.
- **Missing values:** Empty cells or missing columns become empty strings (`""`) in the JSON output.
- **Standard library only:** No external dependencies required.

## Running Tests

```bash
python3 -m pytest tests/ -v
```
