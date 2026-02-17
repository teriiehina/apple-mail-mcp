# Plan: Per-Account Configurable Inbox Mailbox Name

## Problem

The inbox mailbox name is hardcoded as `"INBOX"` / `"Inbox"` throughout the codebase.
On macOS with Apple Mail configured in French (or other locales), the inbox mailbox
is named differently (e.g., `"Boîte de réception"` in French, `"Posteingang"` in German).
The current dual-fallback (`INBOX` then `Inbox`) doesn't handle this.

Worse, a user may have **multiple accounts with different locales** (e.g., Gmail using
`"INBOX"` and iCloud using `"Boîte de réception"`). A single global setting is insufficient:
**the inbox name must be configurable per account**.

## Current State

The inbox name appears in **4 distinct patterns** across the codebase:

### Pattern 1: `inbox_mailbox_script()` helper (core.py:98-105)
Used by tools that loop over all accounts in AppleScript: `list_inbox_emails`,
`get_unread_count`, `get_inbox_overview` (inbox.py), `reply_to_email` (compose.py),
`list_email_attachments`, `save_email_attachment`, `_get_recent_emails_structured` (analytics.py).

```python
def inbox_mailbox_script(...):
    # Tries "INBOX", falls back to "Inbox"
```

### Pattern 2: Default parameter `mailbox: str = "INBOX"` + inline INBOX fallback
Used by tools that accept a specific account + mailbox: `get_email_with_content`,
`search_emails`, `search_by_sender`, `search_email_content`, `get_recent_from_sender`,
`get_email_thread` (search.py), `forward_email` (compose.py), `move_email`,
`update_email_status`, `manage_trash` (manage.py), `export_emails` (analytics.py).

```python
def some_tool(mailbox: str = "INBOX"):
    ...
    if "{escaped_mailbox}" is "INBOX" then
        set ... to mailbox "Inbox" of ...
```

### Pattern 3: Hardcoded name check in `get_newsletters` (search.py:714)
```applescript
if mailboxName is "INBOX" or mailboxName is "Inbox" then
```

### Pattern 4: Hardcoded search in `search_all_accounts` (search.py:1201-1210)
```applescript
set inboxMailbox to mailbox "INBOX" of acct
...
if mbName is "INBOX" or mbName is "Inbox" then
```

## Key Design Challenge

There are two categories of tools:

- **Category A — Account known at Python time**: Tools that receive `account: str` as a
  parameter (e.g., `get_recent_emails`, `search_emails`, `forward_email`). The inbox name
  can be resolved in Python before generating the AppleScript.

- **Category B — Account iterated in AppleScript**: Tools that loop over all accounts
  inside AppleScript (e.g., `list_inbox_emails`, `get_unread_count`, `get_inbox_overview`,
  `search_all_accounts`, `get_newsletters`). The account name is only known at AppleScript
  runtime, so the per-account mapping must be embedded in the generated AppleScript.

The solution must handle both categories cleanly.

## Solution Design

### Configuration Format

Two environment variables working together:

| Variable | Format | Purpose |
|----------|--------|---------|
| `INBOX_MAILBOX_NAME` | Plain string | Global default (fallback for all accounts). Default: `"INBOX"` |
| `INBOX_MAILBOX_NAMES` | JSON object | Per-account overrides. Keys = account names, values = inbox names |

**Example — Claude Desktop config:**

```json
{
  "mcpServers": {
    "apple-mail": {
      "command": "/path/to/venv/bin/python3",
      "args": ["/path/to/apple_mail_mcp.py"],
      "env": {
        "INBOX_MAILBOX_NAME": "INBOX",
        "INBOX_MAILBOX_NAMES": "{\"iCloud\": \"Boîte de réception\", \"Travail\": \"Posteingang\"}"
      }
    }
  }
}
```

Resolution order for a given account:
1. `INBOX_MAILBOX_NAMES[account]` if the account key exists
2. `INBOX_MAILBOX_NAME` (global default)
3. Standard fallbacks: `"INBOX"`, `"Inbox"`

### Architecture: `server.py`

```python
import json

INBOX_MAILBOX_NAME = os.environ.get("INBOX_MAILBOX_NAME", "INBOX")

_raw = os.environ.get("INBOX_MAILBOX_NAMES", "")
INBOX_MAILBOX_NAMES: dict[str, str] = json.loads(_raw) if _raw else {}
```

### Architecture: `core.py` — Two new helpers

#### 1. `get_inbox_name(account: str) -> str` (Python-side resolution for Category A)

```python
from apple_mail_mcp.server import INBOX_MAILBOX_NAME, INBOX_MAILBOX_NAMES

def get_inbox_name(account: str) -> str:
    """Return the configured inbox mailbox name for a given account."""
    return INBOX_MAILBOX_NAMES.get(account, INBOX_MAILBOX_NAME)
```

Used by tools that know the account at Python time to resolve the correct default.

#### 2. `inbox_name_handler_script()` (AppleScript handler for Category B)

Generate an AppleScript `on getInboxName(accountName)` handler that embeds the full
per-account mapping. This handler is injected into scripts that loop over accounts.

```python
def inbox_name_handler_script() -> str:
    """Return AppleScript handler that resolves inbox name per account."""
    default = escape_applescript(INBOX_MAILBOX_NAME)
    lines = []
    first = True
    for acct, name in INBOX_MAILBOX_NAMES.items():
        keyword = "if" if first else "else if"
        lines.append(
            f'        {keyword} accountName is "{escape_applescript(acct)}" then\n'
            f'            return "{escape_applescript(name)}"'
        )
        first = False
    if lines:
        lines.append(f'        else\n            return "{default}"')
        lines.append('        end if')
        body = "\n".join(lines)
    else:
        body = f'        return "{default}"'
    return f'''
    on getInboxName(accountName)
{body}
    end getInboxName
'''
```

**Example output** (for iCloud = "Boîte de réception", default = "INBOX"):
```applescript
on getInboxName(accountName)
    if accountName is "iCloud" then
        return "Boîte de réception"
    else
        return "INBOX"
    end if
end getInboxName
```

#### 3. Updated `inbox_mailbox_script()` (uses the handler at AppleScript runtime)

```python
def inbox_mailbox_script(var_name="inboxMailbox", account_var="anAccount") -> str:
    """Return AppleScript snippet to resolve inbox mailbox with per-account + fallback."""
    return f'''
                set inboxName to my getInboxName(name of {account_var})
                try
                    set {var_name} to mailbox inboxName of {account_var}
                on error
                    try
                        set {var_name} to mailbox "INBOX" of {account_var}
                    on error
                        set {var_name} to mailbox "Inbox" of {account_var}
                    end try
                end try'''
```

Scripts that call `inbox_mailbox_script()` must also include `inbox_name_handler_script()`
in the generated AppleScript (outside the `tell application "Mail"` block, since AppleScript
handlers are defined at the script top level).

### How Each Pattern Gets Fixed

#### Pattern 1 (inbox_mailbox_script — Category B tools)
- Each tool that uses `inbox_mailbox_script()` adds `inbox_name_handler_script()` to its
  generated AppleScript preamble (alongside `LOWERCASE_HANDLER` when present).
- `inbox_mailbox_script()` itself calls `my getInboxName(...)` at runtime.

#### Pattern 2 (default `mailbox` param — Category A tools)
- Change signature: `mailbox: str = "INBOX"` → `mailbox: Optional[str] = None`
- At function start: `if mailbox is None: mailbox = get_inbox_name(account)`
- The inline AppleScript fallback (`if "..." is "INBOX" then try "Inbox"`) becomes:
  check against `get_inbox_name(account)` + standard "INBOX"/"Inbox" fallbacks.
- Add a new shared helper `mailbox_resolve_script()` in `core.py` to generate the
  try/fallback chain for a known mailbox name.

#### Pattern 3 (get_newsletters hardcoded check)
- Replace `if mailboxName is "INBOX" or mailboxName is "Inbox"` with a call to
  `my getInboxName(accountName)` and compare:
  `if mailboxName is inboxName or mailboxName is "INBOX" or mailboxName is "Inbox"`

#### Pattern 4 (search_all_accounts hardcoded access)
- Replace the inline `mailbox "INBOX"` + fallback loop with `inbox_mailbox_script()` +
  the handler preamble, like all other Category B tools.

## Files to Modify

| File | Changes |
|------|---------|
| `apple_mail_mcp/server.py` | Add `INBOX_MAILBOX_NAME` + `INBOX_MAILBOX_NAMES` env vars |
| `apple_mail_mcp/core.py` | Add `get_inbox_name()`, `inbox_name_handler_script()`, `mailbox_resolve_script()`; update `inbox_mailbox_script()` |
| `apple_mail_mcp/tools/inbox.py` | Add handler preamble to 4 tools that use `inbox_mailbox_script()` |
| `apple_mail_mcp/tools/search.py` | Update 6 default params + inline fallbacks (Cat A); fix `get_newsletters` + `search_all_accounts` (Cat B) |
| `apple_mail_mcp/tools/compose.py` | Update `forward_email` default param (Cat A); add handler preamble to `reply_to_email` (Cat B via inbox_mailbox_script) |
| `apple_mail_mcp/tools/manage.py` | Update 3 default params + inline fallbacks; add handler preamble to `save_email_attachment` |
| `apple_mail_mcp/tools/analytics.py` | Update `export_emails` + `get_statistics` default params; add handler to `list_email_attachments`, `_get_recent_emails_structured` |
| `claude_desktop_config_example.json` | Add env examples |
| `README.md` | Document per-account inbox configuration |

## Implementation Steps

### Step 1: `server.py` — Add config variables

```python
import json

INBOX_MAILBOX_NAME = os.environ.get("INBOX_MAILBOX_NAME", "INBOX")

_raw_inbox_names = os.environ.get("INBOX_MAILBOX_NAMES", "")
INBOX_MAILBOX_NAMES: dict[str, str] = (
    json.loads(_raw_inbox_names) if _raw_inbox_names else {}
)
```

### Step 2: `core.py` — Add helpers and update inbox_mailbox_script

1. Import `INBOX_MAILBOX_NAME`, `INBOX_MAILBOX_NAMES` from `server.py`
2. Add `get_inbox_name(account)` for Category A (Python-side lookup)
3. Add `inbox_name_handler_script()` for Category B (AppleScript handler)
4. Rewrite `inbox_mailbox_script()` to call the handler
5. Add `mailbox_resolve_script(var_name, escaped_mailbox, account_var, inbox_name)` for
   the inline try/fallback pattern used in Category A tools

### Step 3: Update Category B tools (account-looping)

For each tool that iterates accounts in AppleScript and uses `inbox_mailbox_script()`:

- **inbox.py**: `list_inbox_emails`, `get_unread_count`, `get_inbox_overview`
- **compose.py**: `reply_to_email`
- **analytics.py**: `list_email_attachments`, `save_email_attachment`, `_get_recent_emails_structured`
- **search.py**: `get_newsletters`, `search_all_accounts`

**Change**: Add `{inbox_name_handler_script()}` to the AppleScript preamble, before
the `tell application "Mail"` block.

### Step 4: Update Category A tools (account-specific)

For each tool that takes `account: str` and has `mailbox: str = "INBOX"`:

- **search.py**: `get_email_with_content`, `search_emails`, `search_by_sender`,
  `search_email_content`, `get_recent_from_sender`, `get_email_thread`
- **compose.py**: `forward_email`
- **manage.py**: `move_email` (from_mailbox), `update_email_status`, `manage_trash`
- **analytics.py**: `export_emails`, `get_statistics`
- **inbox.py**: `get_recent_emails`

**Changes**:
1. Signature: `mailbox: Optional[str] = None`
2. Body start: `if mailbox is None: mailbox = get_inbox_name(account)`
3. Replace inline `if "..." is "INBOX" then try "Inbox"` with
   `mailbox_resolve_script(...)` that includes the per-account name in the fallback chain
4. Update docstrings: `(default: configured inbox name per account, typically "INBOX")`

### Step 5: Update configuration and documentation

- `claude_desktop_config_example.json`: Add `INBOX_MAILBOX_NAME` + `INBOX_MAILBOX_NAMES`
- `README.md`: Add a "Per-Account Inbox Name" section under Configuration explaining
  both variables, with a concrete example for French locale

## Testing Checklist

- [ ] No `INBOX_MAILBOX_NAME` / `INBOX_MAILBOX_NAMES` set → behaves exactly as before
- [ ] Global `INBOX_MAILBOX_NAME=Boîte de réception` → all accounts use it
- [ ] Per-account `INBOX_MAILBOX_NAMES={"iCloud": "Boîte de réception"}` → iCloud uses
      French name, other accounts use "INBOX" default
- [ ] Mixed: global + per-account → per-account wins, others get global default
- [ ] Tools with `mailbox` param explicitly set by user → user value takes precedence
- [ ] AppleScript handler escapes special characters in account/mailbox names correctly
