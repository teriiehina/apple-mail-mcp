"""FastMCP server instance and user preferences."""

import json
import os
from mcp.server.fastmcp import FastMCP

# Initialize FastMCP server
mcp = FastMCP("Apple Mail MCP")

# Load user preferences from environment
USER_PREFERENCES = os.environ.get("USER_EMAIL_PREFERENCES", "")

# Inbox mailbox name configuration (per-account and global default)
INBOX_MAILBOX_NAME = os.environ.get("INBOX_MAILBOX_NAME", "INBOX")

_raw_inbox_names = os.environ.get("INBOX_MAILBOX_NAMES", "")
INBOX_MAILBOX_NAMES: dict[str, str] = (
    json.loads(_raw_inbox_names) if _raw_inbox_names else {}
)
