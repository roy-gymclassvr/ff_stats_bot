import asyncio
import json
import math
import os
import re
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv()

import discord
from discord.ext import commands
from google.oauth2 import service_account
import requests
from google.auth.transport.requests import AuthorizedSession
from googleapiclient.discovery import build

# --- Config ---
THREAD_ID = 1049862491628376095
SPREADSHEET_ID = '1yxGmKTN27i9XtOefErIXKgcbfi1EXJHYWH7wZn_Cnok'
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
SERVICE_ACCOUNT_FILE = "service_account.json"
STATE_PATH = "run_state.json"
DRY_RUN = "--dry-run" in sys.argv or os.environ.get("DRY_RUN") == "1"

# --- Constants ---
EMOJI_DONE = "😌"
RANK_ROLE_IDS = {1379598755560685568, 1379598705283432509, 1379598636068896828}
EMOJI_INCLUDE = {"✅", "➖"}
EMOJI_REJECT = "❌"
RETRYABLE_ERRORS = ("Rate Limit", "Quota", "429", "503")
SHEET_PATTERN = re.compile(r'^S(\d+)W(\d+)$')
WEEKS_PER_SEASON = 4
STAT_COLUMNS = 5  # Points, Blocks, Steals, Wins, Games Played (sheet columns B-F)
MIN_GAMES_FOR_MMR = 5
DISCORD_ID_SHEET_NAME = "Discord IDs"
SNOWFLAKE_RE = re.compile(r'^\d{17,19}$')

# --- Discord setup ---
intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)


def get_date_range():
    """Return (start, end) Eastern datetimes: last Wednesday through today (Tuesday)."""
    eastern = ZoneInfo("America/New_York")
    today = datetime.now(eastern).replace(hour=0, minute=0, second=0, microsecond=0)
    days_back = (today.weekday() - 2) % 7  # Wednesday = weekday 2
    start = today - timedelta(days=days_back)
    end = today.replace(hour=23, minute=59, second=59)
    return start, end


def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            return json.load(f)
    return {}


def save_state(week_start, sheet_name, season_sheet_name):
    with open(STATE_PATH, 'w') as f:
        json.dump({'week_start': week_start, 'sheet_name': sheet_name, 'season_sheet_name': season_sheet_name}, f)


def get_next_sheet_name(sheet_names):
    """
    Find the latest S#W# sheet and return the next (week_sheet, season_sheet).
    E.g. if the latest is S5W4, returns ('S6W1', 'Season 6').
    """
    season_week_pairs = [
        (int(m.group(1)), int(m.group(2)))
        for name in sheet_names
        if (m := SHEET_PATTERN.match(name))
    ]
    if not season_week_pairs:
        return "S1W1", "Season 1"

    season, week = max(season_week_pairs)
    if week >= WEEKS_PER_SEASON:
        season += 1
        week = 1
    else:
        week += 1

    return f"S{season}W{week}", f"Season {season}"


class _HttpResponse:
    """Wraps requests.Response to match the httplib2.Response interface."""
    def __init__(self, resp):
        self.status = resp.status_code
        self.reason = resp.reason
        self._headers = {k.lower(): v for k, v in resp.headers.items()}

    def __getitem__(self, key):
        return self._headers[key.lower()]

    def get(self, key, default=None):
        return self._headers.get(key.lower(), default)

    def items(self):
        return self._headers.items()


class _RequestsHttp:
    """httplib2-compatible HTTP adapter using requests to avoid SSL issues."""
    def __init__(self, session):
        self._session = session

    def request(self, uri, method='GET', body=None, headers=None, **kwargs):
        resp = self._session.request(method, uri, data=body, headers=headers or {})
        return _HttpResponse(resp), resp.content


def authenticate_google_sheets():
    """Authenticate and return the Sheets API service using a service account key."""
    try:
        if not os.path.exists(SERVICE_ACCOUNT_FILE):
            print(f"Service account key not found at '{SERVICE_ACCOUNT_FILE}'.")
            return None

        creds = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, scopes=SCOPES
        )
        return build('sheets', 'v4', http=_RequestsHttp(AuthorizedSession(creds)))

    except Exception as e:
        print(f"Authentication error: {e}")
        return None


def _retry_api_call(fn, max_retries=5):
    """Call fn() with exponential backoff on retryable errors. Returns result or None."""
    delay = 1.0
    for attempt in range(max_retries):
        try:
            return fn()
        except Exception as e:
            if any(code in str(e) for code in RETRYABLE_ERRORS):
                print(f"Rate limit hit, retry {attempt + 1}/{max_retries} in {delay:.1f}s...")
                time.sleep(delay)
                delay *= 2
            else:
                print(f"API error (no retry): {e}")
                raise
    print(f"API call failed after {max_retries} retries.")
    return None


def create_sheet(service, sheet_name, existing_names, template_sheet_id, num_rows=0, col_count=26, frozen_rows=1):
    """Create a new sheet with exact dimensions, copying formatting from the template."""
    if sheet_name in existing_names:
        print(f"Sheet '{sheet_name}' already exists, skipping.")
        return
    if template_sheet_id is None:
        print("Template sheet not found, cannot create sheet.")
        return
    if DRY_RUN:
        print(f"[dry-run] Would create sheet '{sheet_name}' with {num_rows} data row(s) from template.")
        return

    sheets_api = service.spreadsheets()
    keep_rows = max(2, num_rows + 1)

    response = _retry_api_call(lambda: sheets_api.batchUpdate(
        spreadsheetId=SPREADSHEET_ID,
        body={'requests': [{'addSheet': {'properties': {
            'title': sheet_name,
            'gridProperties': {
                'rowCount': keep_rows,
                'columnCount': col_count,
                'frozenRowCount': frozen_rows,
            },
        }}}]}
    ).execute())
    if response is None:
        print(f"Failed to create sheet '{sheet_name}'.")
        return

    new_sheet_id = response['replies'][0]['addSheet']['properties']['sheetId']
    src = {'sheetId': template_sheet_id, 'startRowIndex': 0, 'endRowIndex': keep_rows,
           'startColumnIndex': 0, 'endColumnIndex': col_count}
    dst = {'sheetId': new_sheet_id, 'startRowIndex': 0, 'endRowIndex': keep_rows,
           'startColumnIndex': 0, 'endColumnIndex': col_count}

    result = _retry_api_call(lambda: sheets_api.batchUpdate(
        spreadsheetId=SPREADSHEET_ID,
        body={'requests': [
            {'copyPaste': {'source': src, 'destination': dst, 'pasteType': 'PASTE_NORMAL'}},
            {'updateDimensionProperties': {
                'range': {'sheetId': new_sheet_id, 'dimension': 'COLUMNS', 'startIndex': 0, 'endIndex': 1},
                'properties': {'pixelSize': 200}, 'fields': 'pixelSize',
            }},
            {'updateDimensionProperties': {
                'range': {'sheetId': new_sheet_id, 'dimension': 'COLUMNS', 'startIndex': 1, 'endIndex': col_count},
                'properties': {'pixelSize': 80}, 'fields': 'pixelSize',
            }},
        ]}
    ).execute())
    if result is None:
        print(f"Sheet '{sheet_name}' created but template formatting failed.")
        return

    print(f"Sheet '{sheet_name}' created from template with {keep_rows} row(s) (1 header + {num_rows} data).")


def parse_line(line):
    """
    Parse a line into [name, num1, ..., num5] (cols A-F).
    The last 5 tokens must be whole numbers; everything before is the name.
    Returns None if the line doesn't match.
    """
    parts = line.split()
    if len(parts) < 6 or not all(p.isdigit() for p in parts[-5:]):
        return None
    return [" ".join(parts[:-5])] + [int(p) for p in parts[-5:]]


async def filter_messages(messages):
    """
    Categorise messages by reaction into (included, rejected_count, skipped_count).
    Stamps included messages with EMOJI_DONE.
    """
    included = []
    rejected_count = 0
    skipped_count = 0
    for msg in messages:
        emojis = {str(r.emoji) for r in msg.reactions}
        if EMOJI_DONE in emojis:
            skipped_count += 1
        elif emojis & EMOJI_INCLUDE:
            if DRY_RUN:
                print(f"[dry-run] Would stamp {EMOJI_DONE} on message {msg.id}: {msg.jump_url}")
                included.append(msg)
                continue
            for attempt in range(5):
                try:
                    await msg.add_reaction(EMOJI_DONE)
                    included.append(msg)
                    break
                except discord.errors.DiscordServerError as e:
                    delay = 2 ** attempt
                    print(f"Discord 5xx on add_reaction (attempt {attempt + 1}/5), retrying in {delay}s: {e}")
                    await asyncio.sleep(delay)
            else:
                print(f"Failed to stamp message {msg.id} after 5 attempts, skipping to avoid double-count.")
        elif EMOJI_REJECT in emojis:
            rejected_count += 1
            if DRY_RUN:
                print(f"[dry-run] Would reply to {msg.author} on message {msg.id}: {msg.jump_url}")
            else:
                await msg.reply(f"{msg.author.mention} Please fix: {msg.jump_url}")
    return included, rejected_count, skipped_count


def parse_messages(messages):
    """Extract sheet rows from a list of Discord messages."""
    rows = []
    for msg in messages:
        for line in msg.content.splitlines():
            row = parse_line(line)
            if row:
                rows.append(row)
    return rows


def aggregate_rows(rows):
    """
    Collapse rows by name (case-insensitive), summing the 5 stat columns.
    Mirrors the old appendQueryOutputToActiveSheet Apps Script, including its
    quirk of uppercasing the output name.
    """
    grouped = {}
    for row in rows:
        key = str(row[0]).upper()
        if not key:
            continue
        if key not in grouped:
            grouped[key] = [key] + [0] * STAT_COLUMNS
        existing = grouped[key]
        for i in range(1, STAT_COLUMNS + 1):
            existing[i] += row[i] if i < len(row) else 0
    return list(grouped.values())


def calculate_mmr(row):
    """
    Compute MMR for a [name, points, blocks, steals, wins, games_played] row.
    Ported from the old calculateAndSortMMR Apps Script formula.
    """
    points, blocks, steals, wins, games_played = row[1:6]
    if games_played >= MIN_GAMES_FOR_MMR:
        raw_mmr = (6 * (
            ((1 + (wins / games_played)) * 200) +
            ((1 + (points / games_played)) * 12) +
            ((1 + (blocks / games_played)) * 1.5) +
            ((1 + (steals / games_played)) * 1.5) +
            (math.log(games_played) * 55)
        )) ** 1.08 - 2000
        return max(0, round(raw_mmr))
    games_left = MIN_GAMES_FOR_MMR - games_played
    return f"Play {games_left} more {'game' if games_left == 1 else 'games'} to be rated!"


def sort_by_mmr(rows):
    """Sort rows (MMR in the last column) descending; non-numeric MMR sinks to the bottom."""
    def key(row):
        mmr = row[-1]
        return mmr if isinstance(mmr, (int, float)) else float('-inf')
    return sorted(rows, key=key, reverse=True)


def _normalize_existing_row(row):
    """Coerce a raw sheet row (strings) into [name, int, int, int, int, int], or None if malformed."""
    if not row or not row[0]:
        return None
    padded = row + [0] * (6 - len(row))
    try:
        return [padded[0]] + [int(padded[i]) if padded[i] not in ('', None) else 0 for i in range(1, 6)]
    except (ValueError, TypeError):
        return None


def build_discord_id_map(id_rows):
    """
    Build {lowercased name: discord_id} from the 'Discord IDs' sheet's raw rows (header excluded).
    Ported from the old matchDiscordIDs Apps Script: later rows win on duplicate names,
    and only valid 17-19 digit snowflakes are kept.
    """
    id_map = {}
    for row in id_rows:
        if len(row) < 2:
            continue
        raw_name, raw_id = row[0], row[1]
        if not raw_name or not raw_id:
            continue
        id_str = str(raw_id).strip()
        if not SNOWFLAKE_RE.match(id_str):
            continue
        id_map[str(raw_name).strip().lower()] = id_str
    return id_map


async def load_discord_id_map(service):
    """Read the 'Discord IDs' sheet and build the name-to-ID lookup map."""
    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(
            None,
            lambda: service.spreadsheets().values().get(
                spreadsheetId=SPREADSHEET_ID,
                range=f"{DISCORD_ID_SHEET_NAME}!A2:B"
            ).execute()
        )
        return build_discord_id_map(result.get('values', []))
    except Exception as e:
        print(f"Could not read '{DISCORD_ID_SHEET_NAME}' sheet, Discord IDs will be left blank: {e}")
        return {}


async def merge_rank_and_write(service, sheet_name, new_rows, discord_id_map, max_retries=5):
    """
    Merge new_rows into the sheet's existing A:F data, dedupe by name, recompute MMR,
    sort descending by MMR, attach each row's Discord ID, and overwrite the sheet's
    A2:H range in one call.
    """
    loop = asyncio.get_running_loop()
    sheets_api = service.spreadsheets()

    try:
        result = await loop.run_in_executor(
            None,
            lambda: sheets_api.values().get(
                spreadsheetId=SPREADSHEET_ID,
                range=f"{sheet_name}!A2:F"
            ).execute()
        )
        existing_rows = result.get('values', [])
    except Exception as e:
        print(f"[{sheet_name}] Could not read existing data for merge: {e}")
        existing_rows = []

    normalized_existing = [r for r in (_normalize_existing_row(row) for row in existing_rows) if r]
    aggregated = aggregate_rows(normalized_existing + new_rows)
    for row in aggregated:
        row.append(calculate_mmr(row))
    ranked = sort_by_mmr(aggregated)
    for row in ranked:
        row.append(discord_id_map.get(str(row[0]).strip().lower(), ""))

    if DRY_RUN:
        print(f"[dry-run] Would overwrite '{sheet_name}!A2:H{len(ranked) + 1}' with {len(ranked)} deduped/ranked row(s):")
        for row in ranked:
            print(f"    {row}")
        return

    delay = 1.0
    for attempt in range(max_retries):
        try:
            await loop.run_in_executor(
                None,
                lambda: sheets_api.values().update(
                    spreadsheetId=SPREADSHEET_ID,
                    range=f"{sheet_name}!A2",
                    valueInputOption="RAW",
                    body={'values': ranked}
                ).execute()
            )
            print(f"[{sheet_name}] Wrote {len(ranked)} deduped/ranked row(s).")
            break
        except Exception as e:
            if any(code in str(e) for code in RETRYABLE_ERRORS):
                print(f"[{sheet_name}] Retry {attempt + 1}/{max_retries}: waiting {delay:.1f}s...")
                await asyncio.sleep(delay)
                delay *= 2
            else:
                print(f"[{sheet_name}] Fatal error: {e}")
                raise
    else:
        print(f"[{sheet_name}] Failed to write merged data after {max_retries} retries.")


async def update_attendance(service, included_messages, date_label):
    """Update the attendance sheet with submission counts per official for a given date."""
    submission_counts = {}
    for msg in included_messages:
        discord_id = str(msg.author.id)
        name = getattr(msg.author, 'display_name', msg.author.name)
        if discord_id not in submission_counts:
            submission_counts[discord_id] = {'name': name, 'count': 0}
        submission_counts[discord_id]['count'] += 1

    if not submission_counts:
        return

    loop = asyncio.get_running_loop()
    sheets_api = service.spreadsheets()

    try:
        result = await loop.run_in_executor(
            None,
            lambda: sheets_api.values().get(
                spreadsheetId=SPREADSHEET_ID,
                range="attendance"
            ).execute()
        )
        existing = result.get('values', [])
    except Exception as e:
        print(f"Could not read attendance sheet: {e}")
        return

    if not existing:
        existing = [["Name", "Discord ID"]]

    header_row = existing[0]

    if date_label in header_row:
        date_col_idx = header_row.index(date_label)
    else:
        date_col_idx = len(header_row)
        header_row.append(date_label)

    id_to_row = {}
    for i in range(1, len(existing)):
        row = existing[i]
        if len(row) >= 2 and row[1]:
            id_to_row[row[1]] = i

    for discord_id, info in submission_counts.items():
        if discord_id in id_to_row:
            row_idx = id_to_row[discord_id]
        else:
            existing.append([info['name'], discord_id])
            row_idx = len(existing) - 1
            id_to_row[discord_id] = row_idx

        row = existing[row_idx]
        while len(row) <= date_col_idx:
            row.append('')
        try:
            prior_count = int(row[date_col_idx]) if row[date_col_idx] != '' else 0
        except (ValueError, TypeError):
            prior_count = 0
        row[date_col_idx] = prior_count + info['count']

    if DRY_RUN:
        print(f"[dry-run] Would update attendance column '{date_label}' for {len(submission_counts)} official(s):")
        for discord_id, info in submission_counts.items():
            row = existing[id_to_row[discord_id]]
            print(f"    {info['name']} ({discord_id}): {row[date_col_idx]} (+{info['count']} new)")
        return

    try:
        await loop.run_in_executor(
            None,
            lambda: sheets_api.values().update(
                spreadsheetId=SPREADSHEET_ID,
                range="attendance!A1",
                valueInputOption="RAW",
                body={'values': existing}
            ).execute()
        )
        print(f"Attendance updated: {len(submission_counts)} official(s) for {date_label}.")
    except Exception as e:
        print(f"Failed to update attendance sheet: {e}")


async def strip_rank_roles(guild):
    """Remove all rank roles from every member in the guild (season reset)."""
    removed = 0
    for member in guild.members:
        roles_to_remove = [r for r in member.roles if r.id in RANK_ROLE_IDS]
        if not roles_to_remove:
            continue
        if DRY_RUN:
            print(f"[dry-run] Would remove roles {[r.name for r in roles_to_remove]} from {member.display_name}")
            removed += 1
            continue
        for attempt in range(3):
            try:
                await member.remove_roles(*roles_to_remove, reason="Season reset (Week 1)")
                removed += 1
                await asyncio.sleep(0.5)
                break
            except discord.Forbidden:
                print(f"Missing permissions to remove roles from {member.display_name}")
                break
            except discord.HTTPException as e:
                retry_after = getattr(e, 'retry_after', 5.0) or 5.0
                print(f"HTTP {e.status} for {member.display_name}, retrying in {retry_after:.1f}s (attempt {attempt + 1}/3)...")
                await asyncio.sleep(retry_after)
        else:
            print(f"Gave up removing roles from {member.display_name} after 3 attempts.")
    print(f"Rank roles stripped from {removed} member(s).")


@bot.event
async def on_ready():
    if DRY_RUN:
        print("=== DRY RUN: no sheet writes, reactions, replies, or role changes will be made ===")
    print(f"Logged in as {bot.user}!")
    loop = asyncio.get_running_loop()

    service = await loop.run_in_executor(None, authenticate_google_sheets)
    if service is None:
        print("Exiting: could not authenticate with Google Sheets.")
        await bot.close()
        return

    spreadsheet = await loop.run_in_executor(
        None, lambda: service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
    )
    existing_names = set()
    template_sheet_id = None
    template_col_count = 26
    template_frozen_rows = 1
    for sheet in spreadsheet['sheets']:
        title = sheet['properties']['title']
        existing_names.add(title)
        if title == "template":
            template_sheet_id = sheet['properties']['sheetId']
            grid = sheet['properties'].get('gridProperties', {})
            template_col_count = grid.get('columnCount', 26)
            template_frozen_rows = grid.get('frozenRowCount', 1)

    start_date, end_date = get_date_range()
    state = load_state()
    if state.get('week_start') == str(start_date.date()):
        sheet_name = state['sheet_name']
        season_sheet_name = state['season_sheet_name']
        print("Resuming existing week sheets.")
    else:
        sheet_name, season_sheet_name = get_next_sheet_name(existing_names)
        if DRY_RUN:
            print(f"[dry-run] Would save run_state.json: week_start={start_date.date()}, sheet_name={sheet_name}, season_sheet_name={season_sheet_name}")
        else:
            save_state(str(start_date.date()), sheet_name, season_sheet_name)
    print(f"Date range  : {start_date.date()} (Wed) → {end_date.date()} (Tue)")
    print(f"Week sheet  : {sheet_name}")
    print(f"Season sheet: {season_sheet_name}")

    channel = bot.get_channel(THREAD_ID)
    if not isinstance(channel, discord.Thread):
        print("The specified ID does not correspond to a thread.")
        await bot.close()
        return

    print(f"Fetching messages from: {channel.name}")
    messages = [msg async for msg in channel.history(limit=None, after=start_date, before=end_date)]
    print(f"Total messages fetched: {len(messages)}")

    included, rejected_count, skipped_count = await filter_messages(messages)
    print(f"Included: {len(included)} | Rejected (❌): {rejected_count} | Already processed: {skipped_count}")

    rows = parse_messages(included)

    await update_attendance(service, included, str(start_date.date()))

    # Check the previous week in this season for newly approved stats
    m = SHEET_PATTERN.match(sheet_name)
    current_week = int(m.group(2)) if m else 1
    if current_week > 1:
        prev_start = start_date - timedelta(weeks=1)
        prev_end = start_date
        prev_messages = [msg async for msg in channel.history(limit=None, after=prev_start, before=prev_end)]
        prev_included, _, _ = await filter_messages(prev_messages)
        late_rows = parse_messages(prev_included)
        if late_rows:
            print(f"Late approvals from 1 week ago: {len(late_rows)} row(s)")
            rows.extend(late_rows)
            await update_attendance(service, prev_included, str(prev_start.date()))

    print(f"Total rows to upload: {len(rows)}")

    discord_id_map = await load_discord_id_map(service)
    print(f"Discord ID lookup: {len(discord_id_map)} valid ID(s) loaded.")

    await asyncio.gather(
        loop.run_in_executor(None, create_sheet, service, sheet_name, existing_names, template_sheet_id, len(rows), template_col_count, template_frozen_rows),
        loop.run_in_executor(None, create_sheet, service, season_sheet_name, existing_names, template_sheet_id, len(rows), template_col_count, template_frozen_rows),
    )

    await merge_rank_and_write(service, sheet_name, rows, discord_id_map)
    await merge_rank_and_write(service, season_sheet_name, rows, discord_id_map)

    print("Upload complete.")

    if m and int(m.group(2)) == 1:
        print("Week 1 detected — stripping rank roles from all members...")
        await strip_rank_roles(channel.guild)

    await bot.close()


bot.run(os.environ["DISCORD_BOT_TOKEN"])
