<p align="center">
  <a href="https://github.com/Spenhouet/confluence-markdown-exporter"><img src="https://raw.githubusercontent.com/Spenhouet/confluence-markdown-exporter/b8caaba935eea7e7017b887c86a740cb7bf99708/logo.png" alt="confluence-markdown-exporter"></a>
</p>
<p align="center">
    <em>The confluence-markdown-exporter exports Confluence pages in Markdown format. This exporter helps in migrating content from Confluence to platforms that support Markdown e.g. Docusaurus, Obsidian, Gollum, Azure DevOps (ADO), Foam, Dendron and more.</em>
</p>
<p align="center">
  <a href="https://github.com/Spenhouet/confluence-markdown-exporter/actions/workflows/ci.yml"><img src="https://github.com/Spenhouet/confluence-markdown-exporter/actions/workflows/ci.yml/badge.svg" alt="Test, Lint and Build"></a>
  <a href="https://github.com/Spenhouet/confluence-markdown-exporter/actions/workflows/release.yml"><img src="https://github.com/Spenhouet/confluence-markdown-exporter/actions/workflows/release.yml/badge.svg" alt="Build and publish to PyPI"></a>
  <a href="https://pypi.org/project/confluence-markdown-exporter" target="_blank">
    <img src="https://img.shields.io/pypi/v/confluence-markdown-exporter?color=%2334D058&label=PyPI%20package" alt="Package version">
   </a>
</p>

## Features

- Converts Confluence pages to Markdown format.
- Uses the Atlassian API to export individual pages, pages including children, and whole spaces.
- Supports various Confluence elements such as headings, paragraphs, lists, tables, and more.
- Retains formatting such as bold, italic, and underline.
- Converts Confluence macros to equivalent Markdown syntax where possible.
- Handles images and attachments by linking them appropriately in the Markdown output.
- Supports extended Markdown features like tasks, alerts, and front matter.
- Supports Confluence add-ons: [draw.io](https://marketplace.atlassian.com/apps/1210933/draw-io-diagrams-uml-bpmn-aws-erd-flowcharts), [PlantUML](https://marketplace.atlassian.com/apps/1222993/flowchart-plantuml-diagrams-for-confluence)

## Supported Markdown Elements

- **Headings**: Converts Confluence headings to Markdown headings.
- **Paragraphs**: Converts Confluence paragraphs to Markdown paragraphs.
- **Lists**: Supports both ordered and unordered lists.
- **Tables**: Converts Confluence tables to Markdown tables.
- **Formatting**: Supports bold, italic, and underline text.
- **Links**: Converts Confluence links to Markdown links.
- **Images**: Converts Confluence images to Markdown images with appropriate links.
- **Code Blocks**: Converts Confluence code blocks to Markdown code blocks.
- **Tasks**: Converts Confluence tasks to Markdown task lists.
- **Alerts**: Converts Confluence info panels to Markdown alert blocks.
- **Front Matter**: Adds front matter to the Markdown files for metadata like page properties and page labels.
- **Mermaid**: Converts Mermaid diagrams embedded in draw.io diagrams to Mermaid code blocks.
- **PlantUML**: Converts PlantUML diagrams to Markdown code blocks.

## Usage

To use the confluence-markdown-exporter, follow these steps:

### 1. Installation

Install python package via pip.

```sh
pip install confluence-markdown-exporter
```

#### Installing from Source

To install a modified or development version from a local clone:

```sh
cd confluence-markdown-exporter
pip install -e . --force-reinstall --no-deps
```

### 2. Exporting

Run the exporter with the desired Confluence page ID or space key. Execute the console application by typing `confluence-markdown-exporter` and one of the commands `pages`, `pages-with-descendants`, `spaces`, `all-spaces` or `config`. If a command is unclear, you can always add `--help` to get additional information.

> [!TIP]
> Instead of `confluence-markdown-exporter` you can also use the shorthand `cf-export`.

#### 2.1. Export Page

Export a single Confluence page by ID:

```sh
confluence-markdown-exporter pages <page-id e.g. 645208921> <output path e.g. ./output_path/>
```

or by URL:

```sh
confluence-markdown-exporter pages <page-url e.g. https://company.atlassian.net/MySpace/My+Page+Title> <output path e.g. ./output_path/>
```

#### 2.2. Export Page with Descendants

Export a Confluence page and all its descendant pages by page ID:

```sh
confluence-markdown-exporter pages-with-descendants <page-id e.g. 645208921> <output path e.g. ./output_path/>
```

or by URL:

```sh
confluence-markdown-exporter pages-with-descendants <page-url e.g. https://company.atlassian.net/MySpace/My+Page+Title> <output path e.g. ./output_path/>
```

#### 2.3. Export Space

Export all Confluence pages of a single Space:

```sh
confluence-markdown-exporter spaces <space-key e.g. MYSPACE> <output path e.g. ./output_path/>
```

#### 2.3. Export all Spaces

Export all Confluence pages across all spaces:

```sh
confluence-markdown-exporter all-spaces <output path e.g. ./output_path/>
```

### 3. Output

The exported Markdown file(s) will be saved in the specified `output` directory e.g.:

```sh
output_path/
└── MYSPACE/
   ├── MYSPACE.md
   └── MYSPACE/
      ├── My Confluence Page.md
      └── My Confluence Page/
            ├── My nested Confluence Page.md
            └── Another one.md
```

## Configuration

All configuration and authentication is stored in a single JSON file managed by the application. You do not need to manually edit this file.

### Interactive Configuration

To interactively view and change configuration, run:

```sh
confluence-markdown-exporter config
```

This will open a menu where you can:

- See all config options and their current values
- Select a config to change (including authentication)
- Reset all config to defaults
- Navigate directly to any config section (e.g. `auth.confluence`)

### Available Configuration Options

| Key                                   | Description                                                                                                           | Default                                                             |
| ------------------------------------- | --------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------- |
| export.output_path                    | The directory where all exported files and folders will be written. Used as the base for relative and absolute links. | ./ (current working directory)                                      |
| export.page_href                      | How to generate links to pages in Markdown. Options: "relative" (default) or "absolute".                              | relative                                                            |
| export.page_path                      | Path template for exported pages                                                                                      | {space_name}/{homepage_title}/{ancestor_titles}/{page_title}.md     |
| export.attachment_href                | How to generate links to attachments in Markdown. Options: "relative" (default) or "absolute".                        | relative                                                            |
| export.attachment_path                | Path template for attachments                                                                                         | {space_name}/attachments/{attachment_file_id}{attachment_extension} |
| export.page_breadcrumbs               | Whether to include breadcrumb links at the top of the page.                                                           | True                                                                |
| export.filename_encoding              | Character mapping for filename encoding.                                                                              | Default mappings for forbidden characters.                          |
| export.filename_length                | Maximum length of filenames.                                                                                          | 255                                                                 |
| export.include_document_title         | Whether to include the document title in the exported markdown file.                                                  | True                                                                |
| export.wiki_js_mode                   | Enable Wiki.js compatibility mode. Pages with children become `folder/home.md`.                                       | False                                                               |
| export.tables_as_html                 | Export tables as HTML instead of markdown (supports complex content in cells).                                        | False                                                               |
| export.include_toc                    | Whether to include the table of contents (TOC) in exported pages.                                                     | True                                                                |
| connection_config.backoff_and_retry   | Enable automatic retry with exponential backoff                                                                       | True                                                                |
| connection_config.backoff_factor      | Multiplier for exponential backoff                                                                                    | 2                                                                   |
| connection_config.max_backoff_seconds | Maximum seconds to wait between retries                                                                               | 60                                                                  |
| connection_config.max_backoff_retries | Maximum number of retry attempts                                                                                      | 5                                                                   |
| connection_config.retry_status_codes  | HTTP status codes that trigger a retry                                                                                | \[413, 429, 502, 503, 504\]                                         |
| connection_config.verify_ssl          | Whether to verify SSL certificates for HTTPS requests.                                                                | True                                                                |
| auth.confluence.url                   | Confluence instance URL                                                                                               | ""                                                                  |
| auth.confluence.username              | Confluence username/email                                                                                             | ""                                                                  |
| auth.confluence.api_token             | Confluence API token                                                                                                  | ""                                                                  |
| auth.confluence.pat                   | Confluence Personal Access Token                                                                                      | ""                                                                  |
| auth.jira.url                         | Jira instance URL                                                                                                     | ""                                                                  |
| auth.jira.username                    | Jira username/email                                                                                                   | ""                                                                  |
| auth.jira.api_token                   | Jira API token                                                                                                        | ""                                                                  |
| auth.jira.pat                         | Jira Personal Access Token                                                                                            | ""                                                                  |
| docusaurus.enabled                    | Enable Docusaurus-compatible export format                                                                            | False                                                               |
| docusaurus.docs_folder                | Output folder name for documentation files                                                                            | docs                                                                |
| docusaurus.static_folder              | Output folder name for static assets                                                                                  | static                                                              |
| docusaurus.image_path                 | Absolute path prefix for images in markdown                                                                           | /img                                                                |
| docusaurus.attachments_path           | Absolute path prefix for non-image attachments                                                                        | /files                                                              |
| docusaurus.generate_category_files    | Generate \_category_.json files for sidebar organization                                                              | True                                                                |
| docusaurus.auto_sidebar_position      | Auto-generate sidebar_position in frontmatter                                                                         | True                                                                |
| docusaurus.sidebar_position_increment | Increment value for sidebar positions                                                                                 | 10                                                                  |
| docusaurus.default_admonition_type    | Default admonition type for unrecognized Confluence panels                                                            | note                                                                |

You can always view and change the current config with the interactive menu above.

### Configuration for Target Systems

Some platforms have specific requirements for Markdown formatting, file structure, or metadata. You can adjust the export configuration to optimize output for your target system. Below are some common examples:

#### Docusaurus

The exporter includes full support for [Docusaurus](https://docusaurus.io/), a modern static site generator. When Docusaurus mode is enabled, the exporter will:

- Generate Docusaurus-compatible frontmatter (id, title, description, sidebar_label, sidebar_position, tags)
- Organize files into `docs/` and `static/` folders
- Convert Confluence admonitions to Docusaurus syntax (`:::note`, `:::tip`, `:::info`, `:::warning`)
- Generate `_category_.json` files for sidebar organization
- Use absolute paths for static assets (`/img/...`, `/files/...`)

**Configuration**:

To enable Docusaurus mode, set the following configuration:

```json
{
  "docusaurus": {
    "enabled": true,
    "docs_folder": "docs",
    "static_folder": "static",
    "image_path": "/img",
    "attachments_path": "/files",
    "generate_category_files": true,
    "auto_sidebar_position": true,
    "sidebar_position_increment": 10,
    "default_admonition_type": "note"
  }
}
```

**Output Structure**:

```
output/
├── docs/
│   └── my-space/
│       ├── _category_.json
│       ├── overview.md
│       └── guides/
│           ├── _category_.json
│           ├── getting-started.md
│           └── advanced-topics.md
└── static/
    ├── img/
    │   └── my-space/
    │       ├── diagram1.png
    │       └── screenshot.jpg
    └── files/
        └── my-space/
            └── attachment.pdf
```

**Frontmatter Example**:

```yaml
---
id: getting-started
title: Getting Started Guide
description: Learn how to get started with our platform
sidebar_label: Getting Started
sidebar_position: 10
tags: [tutorial, beginner, guide]
---
```

**Using with Docusaurus**:

After exporting, integrate with your Docusaurus site:

1. Export your Confluence content:
   ```sh
   confluence-markdown-exporter spaces MYSPACE ./output
   ```

2. Copy the exported content to your Docusaurus project:
   ```sh
   cp -r output/docs/* docusaurus-site/docs/
   cp -r output/static/* docusaurus-site/static/
   ```

3. Build your Docusaurus site:
   ```sh
   cd docusaurus-site
   npm run build
   ```

#### Wiki.js

The exporter includes support for [Wiki.js](https://js.wiki/), a modern wiki engine. When Wiki.js mode is enabled, the exporter will:

- Rename parent pages with children to `home.md` inside their folder (e.g., `Parent.md` becomes `Parent/home.md`)
- Generate Wiki.js-compatible frontmatter (title, description, published, editor, dateCreated, dateModified, tags)
- Use relative paths with `./` prefix for internal links
- Handle `/display/` style Confluence links

**Configuration**:

To enable Wiki.js mode, set the following configuration:

```json
{
  "export": {
    "wiki_js_mode": true
  }
}
```

**Output Structure**:

```sh
output/
└── my-space/
    ├── home.md
    ├── guides/
    │   ├── home.md
    │   ├── getting-started.md
    │   └── advanced-topics.md
    └── attachments/
        └── diagram.png
```

**Frontmatter Example**:

```yaml
---
title: Getting Started Guide
description: Learn how to get started with our platform
published: true
editor: markdown
dateCreated: 2024-01-15T10:30:00.000Z
dateModified: 2024-06-20T14:45:00.000Z
tags:
  - tutorial
  - beginner
---
```

#### Obsidian

- **Document Title**: Obsidian already displays the document title. Ensure `export.include_document_title` is `False` so the documented title is not redundant.
- **Breadcrumbs**: Obsidian already displays page breadcrumbs. Ensure `export.breadcrumbs` is `False` so the breadcrumbs are not redundant.

#### Azure DevOps (ADO) Wikis

- **Absolute Attachment Links**: Ensure `export.attachment_href` is set to `absolute`.
- **Attachment Path Template**: Set `export.attachment_path` to `.attachments/{attachment_file_id}{attachment_extension}` so ADO Wiki can find attachments.
- **Filename sanitizing**:
  - Set `export.filename_encoding` to `" ":"-","\"":"%22","*":"%2A","-":"%2D",":":"%3A","<":"%3C",">":"%3E","?":"%3F","|":"%7C","\\":"_","#":"_","/":"_","\u0000":"_"`
    for ADO compatibility (spaces become `-`, dashes become `%2D`, and forbidden characters become `_`)
  - Set `export.filename_length` to `200`

### Custom Config File Location

By default, configuration is stored in a platform-specific application directory. You can override the config file location by setting the `CME_CONFIG_PATH` environment variable to the desired file path. If set, the application will read and write config from this file instead. Example:

```sh
export CME_CONFIG_PATH=/path/to/your/custom_config.json
```

This is useful for using different configs for different environments or for scripting.

## Update

Update python package via pip.

```sh
pip install confluence-markdown-exporter --upgrade
```

## Compatibility

This package is not tested extensively. Please check all output and report any issue [here](https://github.com/Spenhouet/confluence-markdown-exporter/issues).
It generally was tested on:

- Confluence Cloud 1000.0.0-b5426ab8524f (2025-05-28)
- Confluence Server 8.5.20

## Known Issues

1. **Missing Attachment File ID on Server**: For some Confluence Server version/configuration the attachment file ID might not be provided (https://github.com/Spenhouet/confluence-markdown-exporter/issues/39). In the default configuration, this is used for the export path. Solution: Adjust the attachment path in the export config and use the `{attachment_id}` or `{attachment_title}` instead.
2. **Connection Issues when behind Proxy or VPN**: There might be connection issues if your Confluence Server is behind a proxy or VPN (https://github.com/Spenhouet/confluence-markdown-exporter/issues/38). If you experience issues, help to fix this is appreciated.

## Contributing

If you would like to contribute, please read [our contribution guideline](CONTRIBUTING.md).

## License

This tool is an open source project released under the [MIT License](LICENSE).
