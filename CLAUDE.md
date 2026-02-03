# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is `confluence-markdown-exporter`, a Python CLI tool that exports Confluence pages to Markdown format. It supports various target platforms including Docusaurus, Obsidian, Azure DevOps Wikis, and more. The tool uses the Atlassian API to fetch pages and convert them to Markdown with proper formatting, attachments, and metadata.

## Development Commands

### Setup

```bash
# Install uv (Python package manager)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install dependencies and set up virtual environment
uv sync --all-groups

# Verify installation
uv run cf-export --help
```

### Running the Application

```bash
# Export pages (various commands)
uv run cf-export pages <page-id>
uv run cf-export pages-with-descendants <page-id>
uv run cf-export spaces <space-key>
uv run cf-export all-spaces

# Interactive configuration
uv run cf-export config

# Or use virtual environment directly
source .venv/bin/activate
cf-export pages <page-id>
```

### Testing

```bash
# Run all tests
uv run pytest

# Run with verbose output
uv run pytest -v

# Run specific test file
uv run pytest tests/test_basic.py

# Run specific test
uv run pytest tests/test_basic.py::test_package_imports
```

### Linting and Code Quality

```bash
# Check code quality
uv run ruff check

# Auto-fix issues
uv run ruff check --fix

# Check specific directories
uv run ruff check confluence_markdown_exporter/
uv run ruff check tests/
```

### Building

```bash
# Test build
uv build --no-sources
```

### Debugging

```bash
# Enable debug mode (exports HTML files and prints debug info)
DEBUG=True uv run cf-export pages <page-id>
```

### Python Cache Management

When testing code changes, clear Python bytecode cache:

```bash
find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
find . -name "*.pyc" -delete 2>/dev/null || true
```

## Architecture

### Core Components

**Main Entry Point (`main.py`)**
- CLI built with Typer
- Commands: `pages`, `pages-with-descendants`, `spaces`, `all-spaces`, `config`, `version`
- Each command creates Page/Space/Organization objects and calls `.export()`

**Core Conversion Logic (`confluence.py`)**
- **Organization**: Represents the entire Confluence instance
- **Space**: Represents a Confluence space with pages
- **Page**: Represents a single page with conversion logic
  - `Page.Converter`: Nested class that extends `markdownify.MarkdownConverter`
  - Handles HTML → Markdown conversion with custom handlers for Confluence elements
  - Contains MDX escaping logic for Docusaurus compatibility
  - Generates frontmatter, breadcrumbs, and processes attachments
- **Attachment**: Handles file attachments and images
- **CategoryFileGenerator**: Generates `_category_.json` files for Docusaurus

**API Integration (`api_clients.py`)**
- `get_confluence_instance()`: Returns configured Confluence API client
- `get_jira_instance()`: Returns configured Jira API client
- Uses `atlassian-python-api` library with retry/backoff logic

**Configuration (`utils/app_data_store.py`)**
- Pydantic models for type-safe configuration:
  - `ConfigModel`: Top-level config
  - `ExportConfig`: Export settings (paths, breadcrumbs, etc.)
  - `DocusaurusConfig`: Docusaurus-specific settings
  - `AuthConfig`: API credentials for Confluence/Jira
  - `ConnectionConfig`: Retry and SSL settings
- Config stored in JSON file (platform-specific or `CME_CONFIG_PATH` env var)
- Functions: `get_settings()`, `set_setting()`, `save_app_data()`, `load_app_data()`

**Settings are loaded globally** at module import time:
```python
settings = get_settings()  # Global singleton
```

### Conversion Flow

1. **Fetch**: API call retrieves page HTML and metadata
2. **Convert**: `Page.Converter.convert()` transforms HTML to Markdown
   - Uses `markdownify` library as base
   - Custom handlers for Confluence-specific elements (macros, tables, alerts)
   - Handles images, attachments, and internal links
3. **MDX Escaping** (Docusaurus mode):
   - Escapes `<`, `>`, `{`, `}` outside code blocks
   - Simple line-by-line approach tracking only triple-backtick code blocks
   - Avoids inline code detection to prevent state tracking bugs
4. **Frontmatter**: Generates YAML frontmatter
   - Standard: labels, breadcrumbs, properties
   - Docusaurus: id, title, description, sidebar_label, sidebar_position, tags
5. **Export**: Writes markdown file to disk with proper path structure

### Docusaurus Mode

When `settings.docusaurus.enabled = True`:

- **Path Structure**:
  - Pages → `docs/space-name/.../page-slug.md`
  - Images → `static/img/space-name/...`
  - Files → `static/files/space-name/...`
- **MDX Compatibility**: All special characters (`<`, `>`, `{`, `}`) escaped outside code blocks
- **Admonitions**: Confluence panels → `:::note`, `:::tip`, `:::info`, `:::warning`
- **Category Files**: Auto-generates `_category_.json` for sidebar organization
- **Frontmatter**: Full Docusaurus metadata with slugified IDs

### Key Architectural Patterns

**Caching**: `Page.from_id()` uses `@functools.lru_cache(maxsize=1000)` to avoid refetching pages

**Template Variables**: Path templates use Python `string.Template` with variables like:
- `{space_key}`, `{space_name}`
- `{page_id}`, `{page_title}`
- `{ancestor_titles}`, `{ancestor_ids}`
- `{attachment_file_id}`, `{attachment_extension}`

**Recursion Handling**: Set `sys.setrecursionlimit(5000)` to handle deeply nested HTML; catches `RecursionError` and creates placeholder files

**Global State**:
- `settings`: Loaded once at module import
- `confluence`: Global API client instance
- `_category_generator`: Singleton for collecting category data during export

## Critical Code Sections

### MDX Escaping Logic (`confluence.py:574-619`)

**IMPORTANT**: The MDX escaping in `Converter.markdown` property uses a simple approach:
- Tracks only triple-backtick code blocks (toggle `in_code_block` on `\`\`\``)
- Escapes ALL `<`, `>`, `{`, `}` on lines outside code blocks
- Does NOT attempt inline code detection (backticks within lines) to avoid state tracking bugs
- This is aggressive but guaranteed to work; HTML entities render correctly even in inline code

**Do not** add inline code tracking (checking for single backticks) as it's error-prone with:
- Unbalanced backticks in text
- Escaped backticks from markdownify
- Complex nested structures

### Configuration Reloading

Settings are loaded **once** at module import. To reload after config changes:
1. Clear Python cache: `find . -name "*.pyc" -delete`
2. Re-import or restart application

### Custom Conversion Handlers

The `Page.Converter` class overrides markdownify methods:
- `convert_alert()`: Confluence macros → Docusaurus admonitions or GitHub alerts
- `convert_page_link()`: Internal page links using cached page data
- `convert_img()`: Images with attachment path resolution
- `convert_a()`: Links with Confluence-specific patterns
- `convert_table()`: Special handling for Jira tables (CQL queries)

## Code Style

- **Line length**: 100 characters max
- **Docstrings**: Google convention
- **Imports**: One per line (enforced by ruff)
- **Type hints**: Required for function signatures
- **Indentation**: 4 spaces (configured in ruff)
- **Quotes**: Double quotes (ruff setting)

## Configuration Notes

The interactive config menu (`cf-export config`) provides a TUI for managing settings. Use `--show` flag to view current config as JSON.

Key settings for different platforms:
- **Docusaurus**: Set `docusaurus.enabled = true`
- **Obsidian**: Disable breadcrumbs and document title
- **Azure DevOps**: Use absolute attachment paths with `.attachments/` template

## Testing Philosophy

When testing conversion logic:
1. Export a small test page with diverse content (tables, images, macros, code blocks)
2. Check both the `.md` file output and Docusaurus build (if applicable)
3. Clear Python cache between code changes
4. Use `DEBUG=True` to export HTML and see debug output

## Common Pitfalls

1. **Settings not reloading**: Clear Python cache and restart
2. **MDX errors in Docusaurus**: Check for unescaped `<`, `>`, `{`, `}` outside code blocks
3. **Missing attachments**: Confluence Server may not provide `file_id`; use `{attachment_id}` or `{attachment_title}` in path template
4. **Recursion errors**: Deeply nested pages hit Python recursion limit; tool creates placeholder files
5. **Personal spaces on Windows**: `~` prefix expands to home directory; tool handles this with `handle_powershell_tilde_expansion()`
