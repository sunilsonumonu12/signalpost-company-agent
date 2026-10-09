# SignalPost Hackathon - Norwegian Company Agent

This project collects data for Norwegian companies using their organization numbers. It gathers information from official records, company websites, and other relevant sources, then generates the final output.

---

## ⚡ Quick Start

### Step 1: Install All Dependencies

**Windows:**

```bash
setup.bat
```

Or manually:

```bash
pip install -r requirements.txt
```

### Step 2: Run the Project

**Windows, using the default 1000-company input:**

```bash
run.bat
```

Or manually specify your input file:

```bash
python run.py --organisations 1000-companies.jsonl
```

To include annual-report workforce OCR:

```bash
python run.py --organisations entry-companies.jsonl --include-workforce-ocr
```

## 📋 Requirements

- **Python:** 3.10 or higher
  ```bash
  python --version
  ```
- **Internet Connection:** Required because the agent fetches data from company websites and online sources.

---

## 📂 Where Is the Output?

| File/Folder | Description |
|---|---|
| `result/envelopes.jsonl` | ✅ **FINAL OUTPUT** - One envelope object per company, one JSON object per line |
| `out/latest-run/` | All intermediate files and reports from the run |
| `out/latest-run/run-summary.json` | Run summary, including successes and failures |
| `out/latest-run/progress.json` | Progress tracking information |

After the run is complete, check:

```text
result/envelopes.jsonl
```

---

## 🔧 Custom Run Options

### Use Your Own Company File

```bash
python run.py --organisations your-file.jsonl
```

### Show Progress Every 10 Seconds

The default interval is 30 seconds:

```bash
python run.py --organisations 1000-companies.jsonl --progress-interval 10
```

### Generate Results Directly from Existing Envelopes

This skips data collection:

```bash
python run.py --source-envelopes out/latest-run/envelopes.jsonl
```

---

## 📝 Input File Format

The company input file can use either of these two formats.

### Format 1: JSON Array

Example:

```json
[
  "997831365",
  "951161845",
  "816959012"
]
```

### Format 2: JSONL

One organization number per line:

```text
997831365
951161845
816959012
```

---

## 🚀 What Happens During a Run?

The agent processes each company through several stages:

1. **Identity**  
   Fetches official company information using the organization number from Brønnøysundregistrene.

2. **Website**  
   Finds the company's website and extracts relevant information.

3. **Signals**  
   Extracts information such as news, events, careers, and financial data.

4. **Contract**  
   Converts the collected company profiles into one envelope JSONL:

```text
result/envelopes.jsonl
```

The detailed intermediate files and reports remain under `out/latest-run/`.

### Progress Monitoring

The terminal displays progress every 30 seconds by default:

```text
[15:45:20] PROGRESS | companies=250/1000 | completed=250 | success=248 | failed=2 | active=0 | rate=2.35 companies/sec | elapsed=106s
```

---

## ❌ Common Issues

### `"pip install"` Fails

First check your Python version:

```bash
python --version
```

Make sure you have Python 3.10 or higher.

You can also create a fresh virtual environment:

```bash
python -m venv venv
```

Activate it:

```bash
venv\Scripts\activate
```

Then run:

```bash
setup.bat
```

---

### `"Company file not found"`

Make sure the company input file is located in the project root directory.

Alternatively, provide the full path:

```bash
python run.py --organisations "C:\path\to\file.jsonl"
```

---

### Run Seems Slow

This is expected. Processing 1,000 companies can take approximately **15–30 minutes**, depending on internet speed and external website response times.

Each company can require multiple HTTP requests to collect and verify the required data.

  "810034882",
  "810059672",
  "810094532",
  "810098252",
  "810105372",
  "810130822",
  "810182482",
  "810202572",
  "810274042",
  "810324562",
  "810359862",
  "810363142",
  "810392312",
  "810393572",
  "810412402",