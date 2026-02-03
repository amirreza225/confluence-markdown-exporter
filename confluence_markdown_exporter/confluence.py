"""Confluence API documentation.

https://developer.atlassian.com/cloud/confluence/rest/v1/intro
"""

import functools
import json
import logging
import mimetypes
import os
import re
import sys
import urllib.parse
from collections.abc import Set
from os import PathLike
from pathlib import Path
from string import Template
from typing import Literal
from typing import TypeAlias
from typing import cast
from urllib.parse import unquote
from urllib.parse import urlparse

import yaml
from atlassian.errors import ApiError
from atlassian.errors import ApiNotFoundError
from bs4 import BeautifulSoup
from bs4 import Tag
from markdownify import ATX
from markdownify import MarkdownConverter
from pydantic import BaseModel
from requests import HTTPError
from tqdm import tqdm

from confluence_markdown_exporter.api_clients import get_confluence_instance
from confluence_markdown_exporter.api_clients import get_jira_instance
from confluence_markdown_exporter.utils.app_data_store import get_settings
from confluence_markdown_exporter.utils.app_data_store import set_setting
from confluence_markdown_exporter.utils.drawio_converter import load_and_parse_drawio
from confluence_markdown_exporter.utils.export import sanitize_filename
from confluence_markdown_exporter.utils.export import sanitize_key
from confluence_markdown_exporter.utils.export import save_file
from confluence_markdown_exporter.utils.table_converter import TableConverter
from confluence_markdown_exporter.utils.type_converter import str_to_bool

JsonResponse: TypeAlias = dict
StrPath: TypeAlias = str | PathLike[str]

DEBUG: bool = str_to_bool(os.getenv("DEBUG", "False"))

logger = logging.getLogger(__name__)

# Increase recursion limit to handle deeply nested Confluence page structures
# Default is 1000, we increase to 5000 to handle complex pages
# Pages that still exceed this will be caught and skipped with a RecursionError handler
sys.setrecursionlimit(5000)

settings = get_settings()
confluence = get_confluence_instance()


class JiraIssue(BaseModel):
    key: str
    summary: str
    description: str | None
    status: str

    @classmethod
    def from_json(cls, data: JsonResponse) -> "JiraIssue":
        fields = data.get("fields", {})
        return cls(
            key=data.get("key", ""),
            summary=fields.get("summary", ""),
            description=fields.get("description", ""),
            status=fields.get("status", {}).get("name", ""),
        )

    @classmethod
    @functools.lru_cache(maxsize=100)
    def from_key(cls, issue_key: str) -> "JiraIssue":
        issue_data = cast("JsonResponse", get_jira_instance().get_issue(issue_key))
        return cls.from_json(issue_data)


class User(BaseModel):
    account_id: str
    username: str
    display_name: str
    public_name: str
    email: str

    @classmethod
    def from_json(cls, data: JsonResponse) -> "User":
        return cls(
            account_id=data.get("accountId", ""),
            username=data.get("username", ""),
            display_name=data.get("displayName", ""),
            public_name=data.get("publicName", ""),
            email=data.get("email", ""),
        )

    @classmethod
    @functools.lru_cache(maxsize=100)
    def from_username(cls, username: str) -> "User":
        return cls.from_json(
            cast("JsonResponse", confluence.get_user_details_by_username(username))
        )

    @classmethod
    @functools.lru_cache(maxsize=100)
    def from_userkey(cls, userkey: str) -> "User":
        return cls.from_json(cast("JsonResponse", confluence.get_user_details_by_userkey(userkey)))

    @classmethod
    @functools.lru_cache(maxsize=100)
    def from_accountid(cls, accountid: str) -> "User":
        return cls.from_json(
            cast("JsonResponse", confluence.get_user_details_by_accountid(accountid))
        )


class Version(BaseModel):
    number: int
    by: User
    when: str
    friendly_when: str

    @classmethod
    def from_json(cls, data: JsonResponse) -> "Version":
        return cls(
            number=data.get("number", 0),
            by=User.from_json(data.get("by", {})),
            when=data.get("when", ""),
            friendly_when=data.get("friendlyWhen", ""),
        )


class Organization(BaseModel):
    spaces: list["Space"]

    @property
    def pages(self) -> list[int]:
        return [page for space in self.spaces for page in space.pages]

    def export(self) -> None:
        export_pages(self.pages)

    @classmethod
    def from_json(cls, data: JsonResponse) -> "Organization":
        return cls(
            spaces=[Space.from_json(space) for space in data.get("results", [])],
        )

    @classmethod
    @functools.lru_cache(maxsize=100)
    def from_api(cls) -> "Organization":
        return cls.from_json(
            cast(
                "JsonResponse",
                confluence.get_all_spaces(
                    space_type="global", space_status="current", expand="homepage"
                ),
            )
        )


class Space(BaseModel):
    key: str
    name: str
    description: str
    homepage: int | None

    @property
    def pages(self) -> list[int]:
        if self.homepage is None:
            logger.warning(
                f"Space '{self.name}' (key: {self.key}) has no homepage. No pages will be exported."
            )
            return []

        homepage = Page.from_id(self.homepage)
        return [self.homepage, *homepage.descendants]

    def export(self) -> None:
        export_pages(self.pages)

    @classmethod
    def from_json(cls, data: JsonResponse) -> "Space":
        return cls(
            key=data.get("key", ""),
            name=data.get("name", ""),
            description=data.get("description", {}).get("plain", {}).get("value", ""),
            homepage=data.get("homepage", {}).get("id"),
        )

    @classmethod
    @functools.lru_cache(maxsize=100)
    def from_key(cls, space_key: str) -> "Space":
        return cls.from_json(
            cast("JsonResponse", confluence.get_space(space_key, expand="homepage"))
        )


class Label(BaseModel):
    id: str
    name: str
    prefix: str

    @classmethod
    def from_json(cls, data: JsonResponse) -> "Label":
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            prefix=data.get("prefix", ""),
        )


class Document(BaseModel):
    title: str
    space: Space
    ancestors: list[int]

    @property
    def _template_vars(self) -> dict[str, str]:
        return {
            "space_key": sanitize_filename(self.space.key),
            "space_name": sanitize_filename(self.space.name),
            "homepage_id": str(self.space.homepage),
            "homepage_title": sanitize_filename(Page.from_id(self.space.homepage).title),
            "ancestor_ids": "/".join(str(a) for a in self.ancestors),
            "ancestor_titles": "/".join(
                sanitize_filename(Page.from_id(a).title) for a in self.ancestors
            ),
        }


class Attachment(Document):
    id: str
    file_size: int
    media_type: str
    media_type_description: str
    file_id: str
    collection_name: str
    download_link: str
    comment: str
    version: Version

    @property
    def extension(self) -> str:
        if self.comment == "draw.io diagram" and self.media_type == "application/vnd.jgraph.mxfile":
            return ".drawio"
        if self.comment == "draw.io preview" and self.media_type == "image/png":
            return ".drawio.png"

        return mimetypes.guess_extension(self.media_type) or ""

    @property
    def filename(self) -> str:
        return f"{self.file_id}{self.extension}"

    @property
    def _template_vars(self) -> dict[str, str]:
        return {
            **super()._template_vars,
            "attachment_id": str(self.id),
            "attachment_title": sanitize_filename(self.title),
            # file_id is a GUID and does not need sanitized.
            "attachment_file_id": self.file_id,
            "attachment_extension": self.extension,
        }

    @property
    def export_path(self) -> Path:
        if settings.docusaurus.enabled:
            return self._get_docusaurus_attachment_path()

        filepath_template = Template(settings.export.attachment_path.replace("{", "${"))
        return Path(filepath_template.safe_substitute(self._template_vars))

    def _get_docusaurus_attachment_path(self) -> Path:
        """Generate Docusaurus-compatible attachment path (static/img/ or static/files/)."""
        # Determine if this is an image or other file
        image_extensions = ('.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.drawio.png')
        extension = self.extension if self.extension else ''
        is_image = extension.lower() in image_extensions

        # Choose subfolder based on file type
        subfolder = "img" if is_image else "files"

        # Build filename - use file_id if available, otherwise use sanitized title
        if self.file_id:
            filename = self.filename
        else:
            # Fallback to title-based filename when file_id is missing
            filename = sanitize_filename(self.title) + extension

        # Build path: static/img/space-name/filename or static/files/space-name/filename
        space_folder = sanitize_filename(self.space.key.lower())
        return Path(settings.docusaurus.static_folder) / subfolder / space_folder / filename

    @classmethod
    def from_json(cls, data: JsonResponse) -> "Attachment":
        extensions = data.get("extensions", {})
        container = data.get("container", {})
        return cls(
            id=data.get("id", ""),
            title=data.get("title", ""),
            space=Space.from_key(data.get("_expandable", {}).get("space", "").split("/")[-1]),
            file_size=extensions.get("fileSize", 0),
            media_type=extensions.get("mediaType", ""),
            media_type_description=extensions.get("mediaTypeDescription", ""),
            file_id=extensions.get("fileId", ""),
            collection_name=extensions.get("collectionName", ""),
            download_link=data.get("_links", {}).get("download", ""),
            comment=extensions.get("comment", ""),
            ancestors=[
                *[ancestor.get("id") for ancestor in container.get("ancestors", [])],
                container.get("id"),
            ][1:],
            version=Version.from_json(data.get("version", {})),
        )

    @classmethod
    def from_page_id(cls, page_id: int) -> list["Attachment"]:
        attachments = []
        start = 0
        paging_limit = 50
        size = paging_limit  # Initialize to limit to enter the loop

        while size >= paging_limit:
            response = cast(
                "JsonResponse",
                confluence.get_attachments_from_content(
                    page_id,
                    start=start,
                    limit=paging_limit,
                    expand="container.ancestors,version",
                ),
            )

            attachments.extend([cls.from_json(att) for att in response.get("results", [])])

            size = response.get("size", 0)
            start += size

        return attachments

    def export(self) -> None:
        filepath = settings.export.output_path / self.export_path
        if filepath.exists():
            return

        try:
            response = confluence._session.get(str(confluence.url + self.download_link))
            response.raise_for_status()  # Raise error if request fails
        except HTTPError:
            logger.warning(f"There is no attachment with title '{self.title}'. Skipping export.")
            return

        save_file(
            filepath,
            response.content,
        )


class Page(Document):
    id: int
    body: str
    body_export: str
    editor2: str
    labels: list["Label"]
    attachments: list["Attachment"]

    @property
    def descendants(self) -> list[int]:
        url = "rest/api/content/search"
        params = {
            "cql": f"type=page AND ancestor={self.id}",
            "limit": 100,
        }
        results = []

        try:
            response = confluence.get(url, params=params)
            results.extend(response.get("results", []))
            next_path = response.get("_links").get("next")

            while next_path:
                response = confluence.get(next_path)
                results.extend(response.get("results", []))
                next_path = response.get("_links").get("next")

        except HTTPError as e:
            if e.response.status_code == 404:  # noqa: PLR2004
                logger.warning(
                    f"Content with ID {self.id} not found (404) when fetching descendants."
                )
                return []
            return []
        except Exception:
            logger.exception(
                f"Unexpected error when fetching descendants for content ID {self.id}."
            )
            return []

        return [result["id"] for result in results]

    @property
    def _template_vars(self) -> dict[str, str]:
        return {
            **super()._template_vars,
            "page_id": str(self.id),
            "page_title": sanitize_filename(self.title),
        }

    @property
    def export_path(self) -> Path:
        if settings.docusaurus.enabled:
            return self._get_docusaurus_page_path()

        filepath_template = Template(settings.export.page_path.replace("{", "${"))
        return Path(filepath_template.safe_substitute(self._template_vars))

    def _get_docusaurus_page_path(self) -> Path:
        """Generate Docusaurus-compatible page path (docs/space-name/...)."""
        # Start with docs folder
        path = Path(settings.docusaurus.docs_folder)

        # Add space name as top-level folder
        space_folder = sanitize_filename(self.space.key.lower())
        path = path / space_folder

        # Add ancestor hierarchy (folders)
        for ancestor_id in self.ancestors:
            ancestor = Page.from_id(ancestor_id)
            folder_name = sanitize_filename(ancestor.title)
            path = path / folder_name

        # Add page filename (slugified title)
        slug = self._generate_docusaurus_slug(self.title)
        filename = f"{slug}.md"

        return path / filename

    @staticmethod
    def _generate_docusaurus_slug(title: str) -> str:
        """Generate URL-safe slug from title for Docusaurus."""
        slug = title.lower()
        slug = re.sub(r'[^a-z0-9\s-]', '', slug)
        slug = re.sub(r'\s+', '-', slug)
        slug = slug.strip('-')
        return slug if slug else 'untitled'

    @property
    def html(self) -> str:
        if settings.export.include_document_title:
            return f"<h1>{self.title}</h1>{self.body}"
        return self.body

    @property
    def markdown(self) -> str:
        return self.Converter(self).markdown

    def export(self) -> None:
        if self.title == "Page not accessible":
            logger.warning(f"Skipping export for inaccessible page with ID {self.id}")
            return

        logger.info(f"Exporting page: '{self.title}' (ID: {self.id})")

        if DEBUG:
            self.export_body()
        # Export attachments first so the files can be utilized during markdown conversion
        self.export_attachments()

        try:
            self.export_markdown()
            logger.info(f"Successfully exported: '{self.title}' (ID: {self.id})")
        except RecursionError:
            logger.error(
                f"RecursionError while exporting page '{self.title}' (ID: {self.id}). "
                f"This page has deeply nested HTML structures that exceed Python's recursion limit. "
                f"Skipping this page and continuing with export."
            )
            # Create a placeholder file to indicate the page was skipped
            placeholder_content = (
                f"---\n"
                f"title: {self.title}\n"
                f"id: {self.id}\n"
                f"---\n\n"
                f"# {self.title}\n\n"
                f"> **Export Error**: This page could not be exported due to deeply nested HTML structures "
                f"that caused a recursion error during conversion.\n\n"
                f"Please export this page manually from Confluence or simplify its structure.\n\n"
                f"Page ID: {self.id}\n"
            )
            save_file(
                settings.export.output_path / self.export_path,
                placeholder_content,
            )
        except Exception as e:
            logger.error(
                f"Unexpected error while exporting page '{self.title}' (ID: {self.id}): {e}",
                exc_info=True
            )
            # Re-raise unexpected errors
            raise

    def export_with_descendants(self) -> None:
        export_pages([self.id, *self.descendants])

    def export_body(self) -> None:
        soup = BeautifulSoup(self.html, "html.parser")
        save_file(
            settings.export.output_path
            / self.export_path.parent
            / f"{self.export_path.stem}_body_view.html",
            str(soup.prettify()),
        )
        soup = BeautifulSoup(self.body_export, "html.parser")
        save_file(
            settings.export.output_path
            / self.export_path.parent
            / f"{self.export_path.stem}_body_export_view.html",
            str(soup.prettify()),
        )
        save_file(
            settings.export.output_path
            / self.export_path.parent
            / f"{self.export_path.stem}_body_editor2.xml",
            str(self.editor2),
        )

    def export_markdown(self) -> None:
        save_file(
            settings.export.output_path / self.export_path,
            self.markdown,
        )

    def export_attachments(self) -> None:
        if settings.export.attachment_export_all:
            for attachment in self.attachments:
                attachment.export()
        else:
            for attachment in self.attachments:
                if (
                    attachment.filename.endswith(".drawio")
                    and f"diagramName={attachment.title}" in self.body
                ):
                    attachment.export()
                    continue
                if (
                    attachment.filename.endswith(".drawio.png")
                    or attachment.filename.endswith(".drawio")
                ) and attachment.title.replace(" ", "%20") in self.body_export:
                    attachment.export()
                    continue
                if attachment.file_id in self.body:
                    attachment.export()
                    continue

    def get_attachment_by_id(self, attachment_id: str) -> Attachment | None:
        """Get the Attachment object by its ID.

        Confluence Server sometimes stores attachments without a file_id.
        Fall back to the plain attachment.id and return None if nothing matches.
        """
        for a in self.attachments:
            if attachment_id in a.id:
                return a
            if a.file_id and attachment_id in a.file_id:
                return a
        return None

    def get_attachment_by_file_id(self, file_id: str) -> Attachment | None:
        for a in self.attachments:
            if a.file_id and file_id in a.file_id:
                return a
        return None

    def get_attachments_by_title(self, title: str) -> list[Attachment]:
        return [attachment for attachment in self.attachments if attachment.title == title]

    @classmethod
    def from_json(cls, data: JsonResponse) -> "Page":
        return cls(
            id=data.get("id", 0),
            title=data.get("title", ""),
            space=Space.from_key(data.get("_expandable", {}).get("space", "").split("/")[-1]),
            body=data.get("body", {}).get("view", {}).get("value", ""),
            body_export=data.get("body", {}).get("export_view", {}).get("value", ""),
            editor2=data.get("body", {}).get("editor2", {}).get("value", ""),
            labels=[
                Label.from_json(label)
                for label in data.get("metadata", {}).get("labels", {}).get("results", [])
            ],
            attachments=Attachment.from_page_id(data.get("id", 0)),
            ancestors=[ancestor.get("id") for ancestor in data.get("ancestors", [])][1:],
        )

    @classmethod
    @functools.lru_cache(maxsize=1000)
    def from_id(cls, page_id: int) -> "Page":
        try:
            return cls.from_json(
                cast(
                    "JsonResponse",
                    confluence.get_page_by_id(
                        page_id,
                        expand="body.view,body.export_view,body.editor2,metadata.labels,"
                        "metadata.properties,ancestors",
                    ),
                )
            )
        except (ApiError, HTTPError):
            logger.warning(f"Could not access page with ID {page_id}")
            # Return a minimal page object with error information
            return cls(
                id=page_id,
                title="Page not accessible",
                space=Space(key="", name="", description="", homepage=0),
                body="",
                body_export="",
                editor2="",
                labels=[],
                attachments=[],
                ancestors=[],
            )

    @classmethod
    def from_url(cls, page_url: str) -> "Page":
        """Retrieve a Page object given a Confluence page URL."""
        url = urllib.parse.urlparse(page_url)
        hostname = url.hostname
        if hostname and hostname not in str(settings.auth.confluence.url):
            global confluence  # noqa: PLW0603
            set_setting("auth.confluence.url", f"{url.scheme}://{hostname}/")
            confluence = get_confluence_instance()  # Refresh instance with new URL

        path = url.path.rstrip("/")
        if match := re.search(r"/wiki/.+?/pages/(\d+)", path):
            page_id = match.group(1)
            return Page.from_id(int(page_id))

        if match := re.search(r"^/([^/]+?)/([^/]+)$", path):
            space_key = urllib.parse.unquote_plus(match.group(1))
            page_title = urllib.parse.unquote_plus(match.group(2))
            page_data = cast(
                "JsonResponse",
                confluence.get_page_by_title(space=space_key, title=page_title, expand="version"),
            )
            return Page.from_id(page_data["id"])

        msg = f"Could not parse page URL {page_url}."
        raise ValueError(msg)

    class Converter(TableConverter, MarkdownConverter):
        """Create a custom MarkdownConverter for Confluence HTML to Markdown conversion."""

        class Options(MarkdownConverter.DefaultOptions):
            bullets = "-"
            heading_style = ATX
            macros_to_ignore: Set[str] = frozenset(["qc-read-and-understood-signature-box"])
            front_matter_indent = 2

        def __init__(self, page: "Page", **options) -> None:  # noqa: ANN003
            super().__init__(**options)
            self.page = page
            self.page_properties = {}

        @property
        def markdown(self) -> str:
            md_body = self.convert(self.page.html)

            # Apply MDX escaping if Docusaurus mode is enabled
            if settings.docusaurus.enabled:
                if DEBUG:
                    print(f"\n{'='*80}")
                    print(f"MDX ESCAPING for page: {self.page.title}")
                    print(f"Docusaurus enabled: {settings.docusaurus.enabled}")
                    print(f"{'='*80}\n")
                # Simple approach: Replace ALL angle brackets and curly braces outside of code blocks
                lines = []
                in_code_block = False
                line_num = 0
                for line in md_body.split('\n'):
                    line_num += 1
                    # Track code blocks
                    if line.strip().startswith('```'):
                        in_code_block = not in_code_block
                        if DEBUG:
                            print(f"Line {line_num}: CODE BLOCK {'OPENED' if in_code_block else 'CLOSED'}")
                        lines.append(line)
                        continue

                    if in_code_block:
                        if DEBUG and ('<' in line or '>' in line):
                            print(f"Line {line_num}: SKIPPING (inside code block): {line[:100]}")
                        lines.append(line)
                        continue

                    # Outside code blocks: escape ALL angle brackets and curly braces
                    # This is aggressive but guaranteed to work - escapes even in inline code
                    # which is safe since HTML entities render correctly in MDX inline code
                    has_angle = '<' in line or '>' in line
                    escaped_line = (line
                        .replace('<', '&lt;')
                        .replace('>', '&gt;')
                        .replace('{', '&#123;')
                        .replace('}', '&#125;'))
                    if DEBUG and has_angle:
                        print(f"Line {line_num}: ESCAPING (outside code block)")
                        print(f"  BEFORE: {line[:100]}")
                        print(f"  AFTER:  {escaped_line[:100]}")
                    lines.append(escaped_line)
                md_body = '\n'.join(lines)

                if DEBUG:
                    # Count remaining unescaped brackets
                    unescaped_lt = md_body.count('<') - md_body.count('&lt;')
                    unescaped_gt = md_body.count('>') - md_body.count('&gt;')
                    print(f"\n{'-'*80}")
                    print(f"ESCAPING COMPLETE for {self.page.title}")
                    print(f"Total lines processed: {line_num}")
                    print(f"Remaining '<' after escaping: {unescaped_lt}")
                    print(f"Remaining '>' after escaping: {unescaped_gt}")
                    print(f"{'-'*80}\n")

            markdown = f"{self.front_matter}\n"
            if settings.export.page_breadcrumbs:
                markdown += f"{self.breadcrumbs}\n"
            markdown += f"{md_body}\n"
            return markdown

        def _escape_mdx_special_chars(self, content: str) -> str:
            """Escape special characters for MDX compatibility.

            MDX interprets curly braces {} as JSX expressions and < > as JSX tags.
            This method escapes these characters when they appear in regular text
            (not in code blocks, inline code, or valid markdown syntax).
            """
            lines = content.split('\n')
            result_lines = []
            in_code_block = False
            code_block_marker = ''

            for line in lines:
                # Track code blocks to avoid escaping content inside them
                if line.strip().startswith('```'):
                    in_code_block = not in_code_block
                    if in_code_block:
                        code_block_marker = line.strip()
                    result_lines.append(line)
                    continue

                if in_code_block:
                    # Don't escape anything in code blocks
                    result_lines.append(line)
                    continue

                # Escape curly braces and HTML tags outside of inline code and markdown syntax
                # Split by inline code markers to preserve inline code
                parts = []
                current_pos = 0
                in_inline_code = False
                backtick_pos = 0

                while current_pos < len(line):
                    # Find next backtick
                    next_backtick = line.find('`', current_pos)

                    if next_backtick == -1:
                        # No more backticks, process remaining text
                        if not in_inline_code:
                            text_part = line[current_pos:]
                            # Escape curly braces and HTML-like tags
                            text_part = self._escape_mdx_text(text_part)
                            parts.append(text_part)
                        else:
                            parts.append(line[current_pos:])
                        break

                    # Process text before the backtick
                    if not in_inline_code:
                        text_part = line[current_pos:next_backtick]
                        # Escape curly braces and HTML-like tags in regular text
                        text_part = self._escape_mdx_text(text_part)
                        parts.append(text_part)
                    else:
                        # Inside inline code, don't escape
                        parts.append(line[current_pos:next_backtick])

                    # Add the backtick
                    parts.append('`')
                    in_inline_code = not in_inline_code
                    current_pos = next_backtick + 1

                result_lines.append(''.join(parts))

            return '\n'.join(result_lines)

        def _escape_mdx_text(self, text: str) -> str:
            """Escape MDX special characters in text while preserving valid markdown.

            This escapes:
            - Curly braces {} (interpreted as JSX expressions)
            - HTML-like tags <tag> (interpreted as JSX components)

            But preserves:
            - Markdown image syntax ![alt](url)
            - Markdown link syntax [text](url)
            - Comparison operators in regular text (< and >)
            """
            # Escape curly braces with HTML entities (not backslashes)
            text = text.replace('{', '&#123;').replace('}', '&#125;')

            # Escape angle brackets that could be interpreted as JSX tags
            # This includes patterns like <word>, <redis_url>, <redis\_url> (with escaped underscores)
            # We need to be more aggressive because markdownify adds backslashes that break our previous regex

            # Strategy: Find all < followed by non-whitespace and ending with >
            # But exclude markdown image syntax and comparison operators

            def replace_angle_brackets(match):
                content = match.group(0)
                # Don't escape if it looks like a comparison (e.g., "< 10" or "x > 5")
                if content.startswith('< ') or content.endswith(' >'):
                    return content
                # Escape the angle brackets with HTML entities
                return content.replace('<', '&lt;').replace('>', '&gt;')

            # Match anything that looks like it could be interpreted as a JSX tag:
            # - <word> - simple tag
            # - <word_with_underscore> - placeholder
            # - <word\_escaped> - placeholder with escaped underscore from markdownify
            # - </word> - closing tag
            # - <word/> - self-closing tag
            # Pattern: < followed by non-whitespace content and ending with >
            # Exclude < followed by space (comparison operators)
            text = re.sub(r'<(?! )[^>]+>', replace_angle_brackets, text)

            return text

        @property
        def front_matter(self) -> str:
            indent = self.options["front_matter_indent"]

            # Set basic properties
            self.set_page_properties(tags=self.labels)

            # Add Docusaurus-specific frontmatter if enabled
            if settings.docusaurus.enabled:
                self._add_docusaurus_frontmatter()

            if not self.page_properties:
                return ""

            yml = yaml.dump(self.page_properties, indent=indent).strip()
            # Indent the root level list items
            yml = re.sub(r"^( *)(- )", r"\1" + " " * indent + r"\2", yml, flags=re.MULTILINE)
            return f"---\n{yml}\n---\n"

        def _add_docusaurus_frontmatter(self) -> None:
            """Add Docusaurus-specific frontmatter fields."""
            # Generate ID from title (slugified)
            doc_id = self._generate_slug(self.page.title)
            self.set_page_properties(id=doc_id)

            # Extract description from first paragraph
            description = self._extract_description(self.page.body)
            if description:
                self.set_page_properties(description=description)

            # Sidebar label (shortened title or from page properties)
            sidebar_label = self._get_sidebar_label()
            if sidebar_label != self.page.title:  # Only add if different from title
                self.set_page_properties(sidebar_label=sidebar_label)

            # Auto-generate sidebar position if enabled
            if settings.docusaurus.auto_sidebar_position:
                # Calculate position based on ancestor depth and order
                position = len(self.page.ancestors) * settings.docusaurus.sidebar_position_increment
                self.set_page_properties(sidebar_position=position)

            # Convert Confluence labels to Docusaurus tags
            if self.page.labels:
                tags = self._convert_labels_to_tags()
                self.set_page_properties(tags=tags)

        def _generate_slug(self, title: str) -> str:
            """Convert title to URL-safe slug for Docusaurus ID."""
            # Convert to lowercase
            slug = title.lower()
            # Remove special characters (keep alphanumeric and spaces)
            slug = re.sub(r'[^a-z0-9\s-]', '', slug)
            # Replace spaces with hyphens
            slug = re.sub(r'\s+', '-', slug)
            # Remove leading/trailing hyphens
            slug = slug.strip('-')
            return slug if slug else 'untitled'

        def _extract_description(self, html_content: str, max_length: int = 160) -> str:
            """Extract first paragraph from HTML content for description."""
            soup = BeautifulSoup(html_content, "html.parser")

            # Skip the title if present
            for h1 in soup.find_all('h1'):
                h1.decompose()

            # Find first paragraph
            first_p = soup.find('p')
            if not first_p:
                return ""

            # Get text content
            text = first_p.get_text(strip=True)

            # Truncate if needed
            if len(text) > max_length:
                text = text[:max_length].rsplit(' ', 1)[0] + '...'

            return text

        def _get_sidebar_label(self) -> str:
            """Get sidebar label - use title, potentially shortened."""
            # Could check for custom property in the future
            # For now, use the title limited to 50 characters
            title = self.page.title
            if len(title) > 50:
                return title[:47] + '...'
            return title

        def _convert_labels_to_tags(self) -> list[str]:
            """Convert Confluence labels to Docusaurus tags."""
            # Remove # prefix and format as tags
            tags = []
            for label in self.page.labels:
                # Clean label name
                tag = label.name.lower().replace(' ', '-')
                # Remove special characters
                tag = re.sub(r'[^a-z0-9-]', '', tag)
                if tag:
                    tags.append(tag)
            return tags

        @property
        def breadcrumbs(self) -> str:
            return (
                " > ".join([self.convert_page_link(ancestor) for ancestor in self.page.ancestors])
                + "\n"
            )

        @property
        def labels(self) -> list[str]:
            return [f"#{label.name}" for label in self.page.labels]

        def set_page_properties(self, **props: list[str] | str | None) -> None:
            for key, value in props.items():
                if value:
                    self.page_properties[sanitize_key(key)] = value

        def convert_page_properties(
            self, el: BeautifulSoup, text: str, parent_tags: list[str]
        ) -> None:
            rows = [
                cast("list[Tag]", tr.find_all(["th", "td"]))
                for tr in cast("list[Tag]", el.find_all("tr"))
                if tr
            ]
            if not rows:
                return

            props = {
                row[0].get_text(strip=True): self.convert(str(row[1])).strip()
                for row in rows
                if len(row) == 2  # noqa: PLR2004
            }

            self.set_page_properties(**props)

        def convert_alert(self, el: BeautifulSoup, text: str, parent_tags: list[str]) -> str:
            """Convert Confluence info macros to Markdown alerts.

            Converts to either Docusaurus admonitions (:::note) or GitHub style alerts (> [!NOTE])
            depending on the Docusaurus mode setting.
            """
            macro_name = str(el["data-macro-name"])

            if settings.docusaurus.enabled:
                return self._convert_to_docusaurus_admonition(el, text, macro_name, parent_tags)
            else:
                return self._convert_to_github_alert(el, text, macro_name, parent_tags)

        def _convert_to_docusaurus_admonition(
            self, el: BeautifulSoup, text: str, macro_name: str, parent_tags: list[str]
        ) -> str:
            """Convert to Docusaurus admonition syntax (:::note, :::tip, etc.)."""
            # Map Confluence macro names to Docusaurus admonition types
            admonition_type_map = {
                "info": "info",
                "panel": "note",
                "tip": "tip",
                "note": "note",
                "warning": "warning",
            }

            admonition_type = admonition_type_map.get(
                macro_name, settings.docusaurus.default_admonition_type
            )

            # Extract title if present (from the first child element or data attribute)
            title = None
            # Check for title in data attributes
            if el.has_attr("data-title"):
                title = str(el["data-title"])
            # Or check for a title element
            elif title_el := el.find(class_="title"):
                title = title_el.get_text(strip=True)

            # Use the already-processed text content (passed as parameter)
            # This avoids recursion issues
            content = text.strip()

            # Build Docusaurus admonition
            result = f"\n:::{admonition_type}"
            if title:
                result += f" {title}"
            result += f"\n\n{content}\n\n:::\n\n"

            return result

        def _convert_to_github_alert(
            self, el: BeautifulSoup, text: str, macro_name: str, parent_tags: list[str]
        ) -> str:
            """Convert to GitHub-style alert syntax (> [!NOTE]).

            GitHub specific alert types: https://docs.github.com/en/get-started/writing-on-github/getting-started-with-writing-and-formatting-on-github/basic-writing-and-formatting-syntax#alerts
            """
            alert_type_map = {
                "info": "IMPORTANT",
                "panel": "NOTE",
                "tip": "TIP",
                "note": "WARNING",
                "warning": "CAUTION",
            }

            alert_type = alert_type_map.get(macro_name, "NOTE")

            blockquote = super().convert_blockquote(el, text, parent_tags)
            return f"\n> [!{alert_type}]{blockquote}"

        def convert_div(self, el: BeautifulSoup, text: str, parent_tags: list[str]) -> str:
            # Handle Confluence macros
            if el.has_attr("data-macro-name"):
                macro_name = str(el["data-macro-name"])
                if macro_name in self.options["macros_to_ignore"]:
                    return ""

                macro_handlers = {
                    "panel": self.convert_alert,
                    "info": self.convert_alert,
                    "note": self.convert_alert,
                    "tip": self.convert_alert,
                    "warning": self.convert_alert,
                    "details": self.convert_page_properties,
                    "drawio": self.convert_drawio,
                    "plantuml": self.convert_plantuml,
                    "scroll-ignore": self.convert_hidden_content,
                    "toc": self.convert_toc,
                    "jira": self.convert_jira_table,
                    "attachments": self.convert_attachments,
                }
                if macro_name in macro_handlers:
                    return macro_handlers[macro_name](el, text, parent_tags)

            class_handlers = {
                "expand-container": self.convert_expand_container,
                "columnLayout": self.convert_column_layout,
            }
            for class_name, handler in class_handlers.items():
                if class_name in str(el.get("class", "")):
                    return handler(el, text, parent_tags)

            return super().convert_div(el, text, parent_tags)

        def convert_expand_container(
            self, el: BeautifulSoup, text: str, parent_tags: list[str]
        ) -> str:
            """Convert expand-container div to HTML details element."""
            # Extract summary text from expand-control-text
            summary_element = el.find("span", class_="expand-control-text")
            summary_text = (
                summary_element.get_text().strip() if summary_element else "Click here to expand..."
            )

            # Extract content from expand-content
            content_element = el.find("div", class_="expand-content")
            # Recursively convert the content
            content = (
                self.process_tag(content_element, parent_tags).strip() if content_element else ""
            )

            # Return as details element
            return f"\n<details>\n<summary>{summary_text}</summary>\n\n{content}\n\n</details>\n\n"

        def convert_span(self, el: BeautifulSoup, text: str, parent_tags: list[str]) -> str:
            if el.has_attr("data-macro-name"):
                if el["data-macro-name"] == "jira":
                    return self.convert_jira_issue(el, text, parent_tags)

            return text

        def convert_attachments(self, el: BeautifulSoup, text: str, parent_tags: list[str]) -> str:
            file_header = el.find("th", {"class": "filename-column"})
            file_header_text = file_header.text.strip() if file_header else "File"

            modified_header = el.find("th", {"class": "modified-column"})
            modified_header_text = modified_header.text.strip() if modified_header else "Modified"

            def _get_path(p: Path) -> str:
                attachment_path = self._get_path_for_href(p, settings.export.attachment_href)
                return attachment_path.replace(" ", "%20")

            rows = [
                {
                    "file": f"[{att.title}]({_get_path(att.export_path)})",
                    "modified": f"{att.version.friendly_when} by {self.convert_user(att.version.by)}",  # noqa: E501
                }
                for att in self.page.attachments
            ]

            html = f"""<table>
            <tr><th>{file_header_text}</th><th>{modified_header_text}</th></tr>
            {"".join(f"<tr><td>{row['file']}</td><td>{row['modified']}</td></tr>" for row in rows)}
            </table>"""

            return (
                f"\n\n{self.convert_table(BeautifulSoup(html, 'html.parser'), text, parent_tags)}\n"
            )

        def convert_column_layout(
            self, el: BeautifulSoup, text: str, parent_tags: list[str]
        ) -> str:
            cells = el.find_all("div", {"class": "cell"})

            if len(cells) < 2:  # noqa: PLR2004
                return super().convert_div(el, text, parent_tags)

            html = f"<table><tr>{''.join([f'<td>{cell!s}</td>' for cell in cells])}</tr></table>"

            return self.convert_table(BeautifulSoup(html, "html.parser"), text, parent_tags)

        def convert_jira_table(self, el: BeautifulSoup, text: str, parent_tags: list[str]) -> str:
            jira_tables = BeautifulSoup(self.page.body_export, "html.parser").find_all(
                "div", {"class": "jira-table"}
            )

            if len(jira_tables) == 0:
                logger.warning("No Jira table found. Ignoring.")
                return text

            if len(jira_tables) > 1:
                logger.exception("Multiple Jira tables are not supported. Ignoring.")
                return text

            return self.process_tag(jira_tables[0], parent_tags)

        def convert_toc(self, el: BeautifulSoup, text: str, parent_tags: list[str]) -> str:
            tocs = BeautifulSoup(self.page.body_export, "html.parser").find_all(
                "div", {"class": "toc-macro"}
            )

            if len(tocs) == 0:
                logger.warning("Could not find TOC macro. Ignoring.")
                return text

            if len(tocs) > 1:
                logger.exception("Multiple TOC macros are not supported. Ignoring.")
                return text

            return self.process_tag(tocs[0], parent_tags)

        def convert_hidden_content(
            self, el: BeautifulSoup, text: str, parent_tags: list[str]
        ) -> str:
            content = super().convert_p(el, text, parent_tags)
            return f"\n<!--{content}-->\n"

        def convert_jira_issue(self, el: BeautifulSoup, text: str, parent_tags: list[str]) -> str:
            issue_key = el.get("data-jira-key")
            link = cast("BeautifulSoup", el.find("a", {"class": "jira-issue-key"}))
            if not link:
                return text
            if not issue_key:
                return self.process_tag(link, parent_tags)

            try:
                issue = JiraIssue.from_key(str(issue_key))
                return f"[[{issue.key}] {issue.summary}]({link.get('href')})"
            except HTTPError:
                return f"[[{issue_key}]]({link.get('href')})"

        def convert_pre(self, el: BeautifulSoup, text: str, parent_tags: list[str]) -> str:
            if not text:
                return ""

            code_language = ""
            if el.has_attr("data-syntaxhighlighter-params"):
                match = re.search(r"brush:\s*([^;]+)", str(el["data-syntaxhighlighter-params"]))
                if match:
                    code_language = match.group(1)

            return f"\n\n```{code_language}\n{text}\n```\n\n"

        def convert_sub(self, el: BeautifulSoup, text: str, parent_tags: list[str]) -> str:
            return f"<sub>{text}</sub>"

        def convert_sup(self, el: BeautifulSoup, text: str, parent_tags: list[str]) -> str:
            """Convert superscript to Markdown footnotes."""
            if el.previous_sibling is None:
                return f"[^{text}]:"  # Footnote definition
            return f"[^{text}]"  # f"<sup>{text}</sup>"

        def convert_a(self, el: BeautifulSoup, text: str, parent_tags: list[str]) -> str:  # noqa: PLR0911
            if "user-mention" in str(el.get("class")):
                return self.convert_user_mention(el, text, parent_tags)
            if "createpage.action" in str(el.get("href")) or "createlink" in str(el.get("class")):
                logger.warning(
                    f"Broken link detected: '{text}' on page '{self.page.title}' "
                    f"(ID: {self.page.id}). This is likely a Confluence bug. "
                    f"Please report this issue to Atlassian Support."
                )
                if fallback := BeautifulSoup(self.page.editor2, "html.parser").find(
                    "a", string=text
                ):
                    # Prevent infinite recursion if fallback is the same element
                    if isinstance(fallback, Tag) and fallback.get("href") != el.get("href"):
                        return self.convert_a(fallback, text, parent_tags)  # type: ignore -
                return f"[[{text}]]"
            if "page" in str(el.get("data-linked-resource-type")):
                page_id = str(el.get("data-linked-resource-id", ""))
                if page_id and page_id != "null":
                    return self.convert_page_link(int(page_id))
            if "attachment" in str(el.get("data-linked-resource-type")):
                link = self.convert_attachment_link(el, text, parent_tags)
                # convert_attachment_link may return None if the attachment meta is incomplete
                return link or f"[{text}]({el.get('href')})"
            # Handle /wiki/.../pages/123 pattern
            if match := re.search(r"/wiki/.+?/pages/(\d+)", str(el.get("href", ""))):
                page_id = match.group(1)
                return self.convert_page_link(int(page_id))
            # Handle /pages/viewpage.action?pageId=123 pattern
            if match := re.search(r"[/]pages/viewpage\.action\?pageId=(\d+)", str(el.get("href", ""))):
                page_id = match.group(1)
                return self.convert_page_link(int(page_id))
            if str(el.get("href", "")).startswith("#"):
                # Handle heading links
                return f"[{text}](#{sanitize_key(text, '-')})"

            return super().convert_a(el, text, parent_tags)

        def convert_page_link(self, page_id: int) -> str:
            if not page_id:
                msg = "Page link does not have valid page_id."
                raise ValueError(msg)

            page = Page.from_id(page_id)

            if page.title == "Page not accessible":
                logger.warning(
                    f"Confluence page link (ID: {page_id}) is not accessible, "
                    f"referenced from page '{self.page.title}' (ID: {self.page.id})"
                )
                return f"[Page not accessible (ID: {page_id})]"

            page_path = self._get_path_for_href(page.export_path, settings.export.page_href)

            return f"[{page.title}]({page_path.replace(' ', '%20')})"

        def convert_attachment_link(
            self, el: BeautifulSoup, text: str, parent_tags: list[str]
        ) -> str:
            """Build a Markdown link for an attachment.

            If the attachment metadata is missing,
            return the original Confluence URL instead of crashing.
            """
            attachment = None
            if fid := el.get("data-linked-resource-file-id"):
                attachment = self.page.get_attachment_by_file_id(str(fid))
            if not attachment and (fid := el.get("data-media-id")):
                attachment = self.page.get_attachment_by_file_id(str(fid))
            if not attachment and (aid := el.get("data-linked-resource-id")):
                attachment = self.page.get_attachment_by_id(str(aid))

            if attachment is None:
                href = el.get("href") or text
                return f"[{text}]({href})"

            path = self._get_path_for_href(attachment.export_path, settings.export.attachment_href)
            return f"[{attachment.title}]({path.replace(' ', '%20')})"

        def convert_time(self, el: BeautifulSoup, text: str, parent_tags: list[str]) -> str:
            if el.has_attr("datetime"):
                return f"{el['datetime']}"

            return f"{text}"

        def convert_user_mention(self, el: BeautifulSoup, text: str, parent_tags: list[str]) -> str:
            if aid := el.get("data-account-id"):
                try:
                    return self.convert_user(User.from_accountid(str(aid)))
                except ApiNotFoundError:
                    logger.warning(f"User {aid} not found. Using text instead.")

            return self.convert_user_name(text)

        def convert_user(self, user: User) -> str:
            return self.convert_user_name(user.display_name)

        def convert_user_name(self, name: str) -> str:
            return name.removesuffix("(Unlicensed)").removesuffix("(Deactivated)").strip()

        def convert_li(self, el: BeautifulSoup, text: str, parent_tags: list[str]) -> str:
            md = super().convert_li(el, text, parent_tags)
            bullet = self.options["bullets"][0]

            # Convert Confluence task lists to GitHub task lists
            if el.has_attr("data-inline-task-id"):
                is_checked = el.has_attr("class") and "checked" in el["class"]
                return md.replace(f"{bullet} ", f"{bullet} {'[x]' if is_checked else '[ ]'} ", 1)

            return md

        def convert_img(self, el: BeautifulSoup, text: str, parent_tags: list[str]) -> str:
            attachment = None
            if fid := el.get("data-media-id"):
                attachment = self.page.get_attachment_by_file_id(str(fid))

            url_src = str(el.get("src", ""))

            if ".drawio.png" in url_src:
                filename = unquote(urlparse(url_src).path.split("/")[-1])
                drawio_result = self._convert_drawio_embedded_mermaid(filename)
                if drawio_result:
                    return drawio_result
                # If no mermaid diagram extracted, use PNG as attachment fallback
                if attachment is None:
                    drawio_images = self.page.get_attachments_by_title(filename)
                    if len(drawio_images) > 0:
                        attachment = drawio_images[0]

            # Try to extract attachment from Confluence URLs if attachment is still None
            if attachment is None and url_src:
                # Try to extract attachment ID from /download/attachments/<id>/filename or /download/thumbnails/<id>/filename
                if match := re.search(r"/download/(?:attachments|thumbnails)/(\d+)/([^?]+)", url_src):
                    attachment_id = match.group(1)
                    filename = unquote(match.group(2))
                    # Try to find attachment by ID
                    attachment = self.page.get_attachment_by_id(attachment_id)
                    # If not found by ID, try by filename
                    if attachment is None:
                        filename_without_ext = filename.rsplit('.', 1)[0] if '.' in filename else filename
                        matching_attachments = self.page.get_attachments_by_title(filename_without_ext)
                        if matching_attachments:
                            attachment = matching_attachments[0]

            if attachment is None:
                # Log warning about missing attachment
                if url_src and ("/download/" in url_src):
                    logger.warning(
                        f"Could not find attachment for image URL '{url_src}' "
                        f"on page '{self.page.title}' (ID: {self.page.id}). "
                        f"Image may not be accessible in the exported markdown."
                    )

                    # In Docusaurus mode, don't output broken Confluence URLs
                    # Convert to text placeholder instead
                    if settings.docusaurus.enabled:
                        # Extract filename from URL if possible
                        filename_match = re.search(r'/([^/?]+)(?:\?|$)', url_src)
                        filename = filename_match.group(1) if filename_match else "image"
                        return f"[Image: {filename}]"

                href = el.get("href") or text
                if href and not href.startswith("/download/"):
                    return f"![{text}]({href})"
                if url_src and not url_src.startswith("/download/"):
                    return f"![{text}]({url_src})"
                return text

            path = self._get_path_for_href(attachment.export_path, settings.export.attachment_href)
            el["src"] = path.replace(" ", "%20")
            if "_inline" in parent_tags:
                parent_tags.remove("_inline")  # Always show images.
            return super().convert_img(el, text, parent_tags)

        def _convert_drawio_embedded_mermaid(self, filename: str) -> str | None:
            """Extract mermaid diagram from DrawIO PNG preview image.

            Args:
                filename: The filename of the drawio diagram image.

            Returns:
                Markdown formatted mermaid diagram or None if not found.
            """
            drawio_title = filename.removesuffix(".png")
            drawio_attachments = self.page.get_attachments_by_title(drawio_title)

            if len(drawio_attachments) == 0:
                return None

            drawio_filepath = settings.export.output_path / drawio_attachments[0].export_path
            if not drawio_filepath.exists():
                return None

            # Extract mermaid diagram from DrawIO file
            return load_and_parse_drawio(str(drawio_filepath))

        def convert_drawio(self, el: BeautifulSoup, text: str, parent_tags: list[str]) -> str:
            if match := re.search(r"\|diagramName=(.+?)\|", str(el)):
                drawio_name = match.group(1)
                preview_name = f"{drawio_name}.png"
                drawio_attachments = self.page.get_attachments_by_title(drawio_name)
                preview_attachments = self.page.get_attachments_by_title(preview_name)

                if not drawio_attachments or not preview_attachments:
                    return f"\n<!-- Drawio diagram `{drawio_name}` not found -->\n\n"

                drawio_path = self._get_path_for_href(
                    drawio_attachments[0].export_path, settings.export.attachment_href
                )
                preview_path = self._get_path_for_href(
                    preview_attachments[0].export_path, settings.export.attachment_href
                )

                drawio_image_embedding = f"![{drawio_name}]({preview_path.replace(' ', '%20')})"
                drawio_link = f"[{drawio_image_embedding}]({drawio_path.replace(' ', '%20')})"
                return f"\n{drawio_link}\n\n"

            return ""

        def convert_plantuml(self, el: BeautifulSoup, text: str, parent_tags: list[str]) -> str: # noqa: PLR0911
            """Convert PlantUML diagrams from editor2 XML to Markdown code blocks.

            PlantUML diagrams are stored in the editor2 XML as structured macros with
            the UML definition in a JSON structure inside CDATA sections.
            """
            # Parse the editor2 XML to find the PlantUML macro
            # The editor2 content is an XML fragment without a root element, so wrap it
            wrapped_editor2 = f"<root>{self.page.editor2}</root>"
            soup_editor2 = BeautifulSoup(wrapped_editor2, "xml")

            # Get the macro-id from the current element to match it in editor2
            macro_id = el.get("data-macro-id")
            if not macro_id:
                logger.warning("PlantUML macro found but no macro-id attribute")
                return "\n<!-- PlantUML diagram (no macro-id found) -->\n\n"

            # Find the corresponding macro in editor2 XML
            # BeautifulSoup with lxml strips namespace prefixes from both
            # element and attribute names
            # So ac:structured-macro becomes structured-macro, ac:name becomes name, etc.
            plantuml_macros = soup_editor2.find_all("structured-macro")
            plantuml_macro = None
            for macro in plantuml_macros:
                if macro.get("name") == "plantuml" and macro.get("macro-id") == macro_id:
                    plantuml_macro = macro
                    break

            if not plantuml_macro:
                logger.warning(f"PlantUML macro with id {macro_id} not found in editor2 XML")
                return "\n<!-- PlantUML diagram (not found in editor2) -->\n\n"

            # Extract the plain-text-body containing the JSON
            plain_text_body = plantuml_macro.find("plain-text-body")
            if not plain_text_body:
                logger.warning(f"PlantUML macro {macro_id} has no plain-text-body")
                return "\n<!-- PlantUML diagram (no content found) -->\n\n"

            # Extract the JSON from CDATA
            cdata_content = plain_text_body.get_text(strip=True)
            if not cdata_content:
                logger.warning(f"PlantUML macro {macro_id} has empty content")
                return "\n<!-- PlantUML diagram (empty content) -->\n\n"

            # Parse the JSON to get the umlDefinition
            try:
                plantuml_data = json.loads(cdata_content)
                uml_definition = plantuml_data.get("umlDefinition", "")

                if not uml_definition:
                    logger.warning(f"PlantUML macro {macro_id} has no umlDefinition")
                    return "\n<!-- PlantUML diagram (no UML definition) -->\n\n"

            except json.JSONDecodeError:
                logger.exception(f"Failed to parse PlantUML JSON for macro {macro_id}")
                return "\n<!-- PlantUML diagram (invalid JSON) -->\n\n"
            else:
                # Return as a Markdown code block with plantuml syntax
                return f"\n```plantuml\n{uml_definition}\n```\n\n"

        def convert_table(self, el: BeautifulSoup, text: str, parent_tags: list[str]) -> str:
            if el.has_attr("class") and "metadata-summary-macro" in el["class"]:
                return self.convert_page_properties_report(el, text, parent_tags)

            return super().convert_table(el, text, parent_tags)

        def convert_page_properties_report(
            self, el: BeautifulSoup, text: str, parent_tags: list[str]
        ) -> str:
            data_cql = el.get("data-cql")
            if not data_cql:
                return ""
            soup = BeautifulSoup(self.page.body_export, "html.parser")
            table = soup.find("table", {"data-cql": data_cql})
            if not table:
                return ""
            return super().convert_table(table, "", parent_tags)  # type: ignore -

        def _get_path_for_href(self, path: Path, style: Literal["absolute", "relative"]) -> str:
            """Get the path to use in href attributes based on settings."""
            # In Docusaurus mode, use absolute paths for static assets
            if settings.docusaurus.enabled:
                path_str = str(path)
                # Check if this is a static asset (img or files)
                if path_str.startswith(settings.docusaurus.static_folder):
                    # Extract the part after static/
                    # e.g., static/img/space/file.png -> /img/space/file.png
                    relative_to_static = path_str.split(f"{settings.docusaurus.static_folder}/", 1)
                    if len(relative_to_static) > 1:
                        return "/" + relative_to_static[1]

                # For page links, use relative paths
                result = os.path.relpath(path, self.page.export_path.parent)
                return result

            # Legacy mode
            if style == "absolute":
                # Note that usually absolute would be
                # something like this: (settings.export.output_path / path).absolute()
                # In this case the URL will be "absolute" to the export path.
                # This is useful for local file links.
                result = "/" + str(path).lstrip("/")
            else:
                result = os.path.relpath(path, self.page.export_path.parent)
            return result


class CategoryFileGenerator:
    """Generate _category_.json files for Docusaurus sidebar organization."""

    def __init__(self) -> None:
        self.category_data: dict[str, dict] = {}

    def collect_category_info(self, page: "Page", position_index: int = 0) -> None:
        """Collect information about folders/categories during page processing."""
        if not settings.docusaurus.enabled or not settings.docusaurus.generate_category_files:
            return

        # Get the parent folder path
        parent_folder = page.export_path.parent

        # Skip if this is the root docs folder or space folder
        if str(parent_folder) in [settings.docusaurus.docs_folder,
                                   str(Path(settings.docusaurus.docs_folder) / sanitize_filename(page.space.key.lower()))]:
            return

        # Convert to absolute path for consistency
        folder_key = str(parent_folder)

        if folder_key not in self.category_data:
            # Get category label from parent page title
            category_label = parent_folder.name

            # Try to get a better label from the parent page
            if page.ancestors:
                parent_page_id = page.ancestors[-1]
                try:
                    parent_page = Page.from_id(parent_page_id)
                    category_label = parent_page.title
                except Exception:
                    pass

            # Calculate position based on hierarchy depth
            depth = len(page.ancestors)
            position = depth * settings.docusaurus.sidebar_position_increment

            self.category_data[folder_key] = {
                'label': category_label,
                'position': position,
                'pages': [],
            }

        # Add page to category
        self.category_data[folder_key]['pages'].append({
            'title': page.title,
            'position': position_index
        })

    def generate_category_files(self) -> None:
        """Generate all _category_.json files after processing all pages."""
        if not settings.docusaurus.enabled or not settings.docusaurus.generate_category_files:
            return

        for folder_path, category_info in self.category_data.items():
            category_file_path = Path(folder_path) / "_category_.json"

            # Prepare category content
            page_count = len(category_info['pages'])
            category_content = {
                "label": category_info['label'],
                "position": category_info['position'],
                "link": {
                    "type": "generated-index",
                    "description": f"This section contains {page_count} page(s)."
                },
                "collapsed": False,
                "collapsible": True
            }

            # Write category file
            full_path = settings.export.output_path / category_file_path
            full_path.parent.mkdir(parents=True, exist_ok=True)

            with open(full_path, 'w', encoding='utf-8') as f:
                json.dump(category_content, f, indent=2, ensure_ascii=False)

            logger.info(f"Generated category file: {category_file_path}")


# Global category generator instance
_category_generator: CategoryFileGenerator | None = None


def get_category_generator() -> CategoryFileGenerator:
    """Get or create the global category generator instance."""
    global _category_generator  # noqa: PLW0603
    if _category_generator is None:
        _category_generator = CategoryFileGenerator()
    return _category_generator


def export_page(page_id: int) -> None:
    """Export a Confluence page to Markdown.

    Args:
        page_id: The page id.
        output_path: The output path.
    """
    page = Page.from_id(page_id)
    page.export()

    # Collect category info for Docusaurus
    if settings.docusaurus.enabled:
        get_category_generator().collect_category_info(page)


def export_pages(page_ids: list[int]) -> None:
    """Export a list of Confluence pages to Markdown.

    Args:
        page_ids: List of pages to export.
        output_path: The output path.
    """
    # Reset category generator for fresh export
    global _category_generator  # noqa: PLW0603
    _category_generator = CategoryFileGenerator()

    for page_id in (pbar := tqdm(page_ids, smoothing=0.05)):
        pbar.set_postfix_str(f"Exporting page {page_id}")
        export_page(page_id)

    # Generate category files at the end
    if settings.docusaurus.enabled:
        get_category_generator().generate_category_files()
