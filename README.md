# MCP Buste Paga

An [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) server that parses Italian INAZ payslip PDFs and stores them in a local SQLite database. Connect it to any MCP-compatible AI client to analyze your salary history, search payslip items, and get detailed breakdowns — all with your data staying on your machine.

## Installation

**Requirements:** Python 3.10+, [uv](https://docs.astral.sh/uv/)

Clone the repository and install dependencies:

```bash
git clone https://github.com/morettimarco/MCP-Buste-Paga.git
cd MCP-Buste-Paga
uv sync
```

Verify it runs:

```bash
uv run mcp-buste-paga
```

## Ingesting payslips

Once the server is connected to an AI client (see below), ask the assistant to ingest your payslip PDFs:

> "Ingest my payslips from ~/Documents/Buste"

The `ingest_payslips` tool will recursively scan the directory for `.pdf` files, parse each one, and store the data. Duplicates are automatically skipped via SHA-256 hashing.

## Database location

All data is stored in a local SQLite database at:

```
~/.mcp-buste-paga/buste_paga.db
```

The directory is created automatically on first run. The database contains four tables:

| Table | Description |
|---|---|
| `aziende` | Company information (name, fiscal code, INPS/INAIL codes) |
| `dipendenti` | Employee profile (name, fiscal code, hire date, role, contract) |
| `buste_paga` | Monthly payslip summaries (gross, net, taxes, TFR, etc.) |
| `voci_corpo_busta` | Individual payslip line items (base pay, overtime, deductions, etc.) |

## Available tools

| Tool | Description |
|---|---|
| `ingest_payslips` | Scan a directory for PDF payslips, parse and store them. Returns a summary of ingested/skipped/failed files. |
| `get_employee_summary` | Get employee profile and company details, plus the number of payslips stored. |
| `get_salary_history_tool` | Get salary history (net pay, gross, deductions) ordered by most recent month. Optionally filter by year. |
| `get_payslip_details_tool` | Get the full breakdown of a specific payslip by month and year, including all line items. |
| `search_payslip_items` | Search payslip line items by keyword (e.g. "Straordinario", "Ferie", "Ticket") with per-month and grand totals. |

## Connecting to AI clients

### Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "buste-paga": {
      "command": "/full/path/to/uv",
      "args": [
        "--directory",
        "/full/path/to/MCP-Buste-Paga",
        "run",
        "mcp-buste-paga"
      ]
    }
  }
}
```

> **Note:** Use absolute paths. Find your `uv` path with `which uv`.

Restart Claude Desktop. A hammer icon in the chat input confirms the server is connected.

### Claude Code (CLI)

Add to your project's `.mcp.json` or run:

```bash
claude mcp add buste-paga -- uv --directory /full/path/to/MCP-Buste-Paga run mcp-buste-paga
```

### ChatGPT Desktop

ChatGPT Desktop supports MCP servers via its settings. Go to **Settings > Beta features > MCP Servers**, click **Add**, and configure:

- **Name:** buste-paga
- **Command:** `/full/path/to/uv`
- **Arguments:** `--directory /full/path/to/MCP-Buste-Paga run mcp-buste-paga`

### Cursor

Add to `.cursor/mcp.json` in your project root:

```json
{
  "mcpServers": {
    "buste-paga": {
      "command": "/full/path/to/uv",
      "args": [
        "--directory",
        "/full/path/to/MCP-Buste-Paga",
        "run",
        "mcp-buste-paga"
      ]
    }
  }
}
```

### Windsurf

Add to `~/.codeium/windsurf/mcp_config.json`:

```json
{
  "mcpServers": {
    "buste-paga": {
      "command": "/full/path/to/uv",
      "args": [
        "--directory",
        "/full/path/to/MCP-Buste-Paga",
        "run",
        "mcp-buste-paga"
      ]
    }
  }
}
```

## Privacy

All payslip data is parsed and stored locally on your machine. No data is sent to external services. The AI client only accesses the data through the MCP tools above.

## License

MIT
