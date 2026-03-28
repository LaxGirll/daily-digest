#!/usr/bin/env python3
"""
daily_digest.py
Daily email digest pipeline — designed to run in GitHub Actions.

1. Fetches emails from the last 24 hours via Gmail API
2. Auto-trashes known junk senders (e.g. Solidcore reminders)
3. Asks Claude to classify and summarize
4. Archives AI newsletters, labels everything else "AI-Summarized"
5. Passes structured JSON to publish_digest.py → writes index.html

Required environment variables:
  GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN
  ANTHROPIC_API_KEY
  DIGEST_PASSWORD  (used by publish_digest.py)
"""

import base64
import json
import os
import re
import sys
import subprocess
from datetime import datetime, timedelta, timezone

import anthropic
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ── Config ─────────────────────────────────────────────────────────────────────
SCOPES       = ['https://www.googleapis.com/auth/gmail.modify']
MAX_EMAILS   = 100
BODY_LIMIT   = 2500
CLAUDE_MODEL = 'claude-opus-4-6'
SUMMARIZED_LABEL = 'AI-Summarized'

NEWSLETTER_HEADERS   = {'list-unsubscribe', 'list-id', 'x-mailchimp-id',
                        'x-campaign', 'x-mailer-recvtype'}
NEWSLETTER_PRECEDENCE = {'bulk', 'list', 'junk'}

# Emails from these senders are automatically trashed (case-insensitive substring match)
AUTO_TRASH_PATTERNS = [
    'solidcore',
]


# ── Gmail helpers ───────────────────────────────────────────────────────────────

def get_gmail_service():
    creds = Credentials(
        token=None,
        refresh_token=os.environ['GMAIL_REFRESH_TOKEN'],
        client_id=os.environ['GMAIL_CLIENT_ID'],
        client_secret=os.environ['GMAIL_CLIENT_SECRET'],
        token_uri='https://oauth2.googleapis.com/token',
        scopes=SCOPES,
    )
    creds.refresh(Request())
    return build('gmail', 'v1', credentials=creds, cache_discovery=False)


def fetch_recent_message_ids(gmail):
    since_ts = int((datetime.now(timezone.utc) - timedelta(hours=24)).timestamp())
    result = gmail.users().messages().list(
        userId='me',
        q=f'after:{since_ts} -in:spam -in:trash',
        maxResults=MAX_EMAILS,
    ).execute()
    return result.get('messages', [])


def get_message_detail(gmail, msg_id):
    msg = gmail.users().messages().get(
        userId='me', id=msg_id, format='full'
    ).execute()
    headers = {h['name'].lower(): h['value']
               for h in msg['payload'].get('headers', [])}
    subject    = headers.get('subject', '(no subject)')
    from_      = headers.get('from', '')
    is_newsletter = bool(NEWSLETTER_HEADERS & set(headers.keys()))
    if not is_newsletter:
        is_newsletter = headers.get('precedence', '').lower() in NEWSLETTER_PRECEDENCE
    body = _extract_body(msg['payload'])[:BODY_LIMIT]
    return {
        'id':            msg_id,
        'subject':       subject,
        'from':          from_,
        'is_newsletter': is_newsletter,
        'body':          body,
        'label_ids':     msg.get('labelIds', []),
    }


def _extract_body(payload):
    mime = payload.get('mimeType', '')
    data = payload.get('body', {}).get('data', '')
    if mime == 'text/plain' and data:
        return base64.urlsafe_b64decode(data).decode('utf-8', errors='replace')
    if mime == 'text/html' and data:
        html = base64.urlsafe_b64decode(data).decode('utf-8', errors='replace')
        return re.sub(r'<[^>]+>', ' ', html)
    for part in payload.get('parts', []):
        text = _extract_body(part)
        if text:
            return text
    return ''


def fetch_user_labels(gmail):
    """Returns dict of label_name -> label_id for user-created labels only."""
    labels = gmail.users().labels().list(userId='me').execute().get('labels', [])
    return {
        lbl['name']: lbl['id']
        for lbl in labels
        if lbl.get('type') == 'user' and lbl['name'] != SUMMARIZED_LABEL
    }


def auto_trash_emails(gmail, emails):
    """Trash emails matching AUTO_TRASH_PATTERNS. Returns filtered list."""
    keep    = []
    trashed = 0
    for email in emails:
        from_lower = email['from'].lower()
        if any(p in from_lower for p in AUTO_TRASH_PATTERNS):
            try:
                gmail.users().messages().trash(userId='me', id=email['id']).execute()
                trashed += 1
            except HttpError as e:
                print(f'  Warning: could not trash {email["id"]}: {e}', file=sys.stderr)
                keep.append(email)
        else:
            keep.append(email)
    if trashed:
        print(f'  Auto-trashed {trashed} junk emails')
    return keep


def get_or_create_label(gmail, name):
    labels = gmail.users().labels().list(userId='me').execute().get('labels', [])
    for lbl in labels:
        if lbl['name'] == name:
            return lbl['id']
    created = gmail.users().labels().create(
        userId='me', body={'name': name, 'labelListVisibility': 'labelShow',
                           'messageListVisibility': 'show'}
    ).execute()
    return created['id']


def apply_inbox_actions(gmail, emails, summarized_label_id, ai_newsletter_ids):
    """Archive AI newsletters; label everything else that's in the inbox."""
    archived = 0
    labeled  = 0
    for email in emails:
        if 'INBOX' not in email['label_ids']:
            continue
        try:
            if email['id'] in ai_newsletter_ids:
                gmail.users().messages().modify(
                    userId='me', id=email['id'],
                    body={'removeLabelIds': ['INBOX']}
                ).execute()
                archived += 1
            else:
                gmail.users().messages().modify(
                    userId='me', id=email['id'],
                    body={'addLabelIds': [summarized_label_id]}
                ).execute()
                labeled += 1
        except HttpError as e:
            print(f'  Warning: could not modify {email["id"]}: {e}', file=sys.stderr)
    print(f'  Archived {archived} AI newsletters, labeled {labeled} other emails')


# ── Claude summarization ────────────────────────────────────────────────────────

def build_digest(emails):
    """Returns (digest_text, ai_newsletter_ids, needs_attention, promotions, notifications)."""
    client   = anthropic.Anthropic(api_key=os.environ['ANTHROPIC_API_KEY'])
    date_str = datetime.now().strftime('%A, %B %-d, %Y')
    total    = len(emails)

    lines = []
    for i, e in enumerate(emails, 1):
        lines.append(
            f"[{i}] FROM: {e['from']}\n"
            f"SUBJECT: {e['subject']}\n"
            f"BODY:\n{e['body']}\n"
        )
    emails_text = '\n---\n'.join(lines)

    prompt = f"""You are generating a daily email digest for {date_str}.

ABOUT ME: Product Manager at Vanguard building AI-powered tools for Portfolio Managers.
Also an AI practitioner who wants to stay current on new AI models, tools, agents, and frameworks.

Here are {total} emails received in the last 24 hours:

{emails_text}

=== PART 1: MACHINE-READABLE HEADER (output this first, exactly as shown) ===

AI_NEWSLETTERS: [comma-separated email indices that are AI-focused newsletters, or 'none']
NEEDS_ATTENTION:
[index]|[one-line description of the action required]
PROMOTIONS:
[index]|[Sender] -- [deal or offer, include any discount amount/deadline]
NOTIFICATIONS:
[index]|[Sender] -- [what the notification is about]
END_HEADER

Classification rules:
- AI_NEWSLETTERS: newsletters primarily about AI, LLMs, ML, agents, AI tools, AI in business.
  Examples: The Rundown AI, TLDR AI, McKinsey AI, Ben's Bites, The Batch, Import AI, etc.
  NOT: general news, cooking, books, finance without AI focus.
- NEEDS_ATTENTION: emails genuinely needing a reply or decision TODAY. Be selective.
- PROMOTIONS: sales, discount codes, retail offers, deal emails.
- NOTIFICATIONS: automated alerts — bank statements, shipping updates, receipts, app alerts.
- Each email goes in at most ONE category. Omit a section if empty.

=== PART 2: DIGEST TEXT (output immediately after END_HEADER) ===

Do NOT include Needs Attention, Promotions, or Notifications here — those are handled separately.
Sections separated by lines with only three dashes (---). Omit empty sections entirely.

DAILY DIGEST — {date_str}
{total} emails scanned, last 24 hours

EMAIL COUNT BY CATEGORY
AI Newsletters: N emails
Work: N emails
Personal: N emails
Books & Reading: N emails
Food & Recipes: N emails
Kids & Family: N emails
Promotions: N emails
Notifications: N emails
Other: N emails

---

AI NEWSLETTERS — What You Need to Know
[Only AI-focused newsletters. For each, provide TWO angles:]
Sender Name -- "Subject or topic"
PM angle: How this applies to building AI tools at Vanguard for Portfolio Managers.
AI angle: What's new or cool here for an AI practitioner.

---

BOOKS & READING
[BookBub deals, NYT books, reading recommendations, library emails.]
Source -- "Title or Deal"
Brief note — genre, why interesting, price if a deal.

---

FOOD & RECIPES
[NYT Cooking, recipe newsletters, restaurant content, meal ideas.]
Source -- "Dish or Topic"
1-2 sentence description.

---

KIDS & FAMILY
[School emails, kids activities, family events, parenting content.]
- Bullet per item

---

REGULAR EMAIL SUMMARY
[Everything else — work updates, personal, misc. Bullet points by theme.]
"""

    with client.messages.stream(
        model=CLAUDE_MODEL,
        max_tokens=4096,
        thinking={'type': 'adaptive'},
        messages=[{'role': 'user', 'content': prompt}],
    ) as stream:
        final = stream.get_final_message()

    raw = ''.join(b.text for b in final.content if b.type == 'text')
    return _parse_claude_output(raw, emails)


def _parse_claude_output(raw, emails):
    lines = raw.splitlines()

    ai_newsletter_ids = set()
    needs_attention   = []
    promotions        = []
    notifications     = []
    section           = None
    header_end        = len(lines)

    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith('AI_NEWSLETTERS:'):
            val = s.split(':', 1)[1].strip()
            if val.lower() != 'none':
                for part in val.split(','):
                    try:
                        idx = int(part.strip())
                        if 1 <= idx <= len(emails):
                            ai_newsletter_ids.add(emails[idx - 1]['id'])
                    except ValueError:
                        pass
        elif s == 'NEEDS_ATTENTION:':
            section = 'na'
        elif s == 'PROMOTIONS:':
            section = 'promo'
        elif s == 'NOTIFICATIONS:':
            section = 'notif'
        elif s == 'END_HEADER':
            header_end = i + 1
            break
        elif '|' in s and section:
            parts = s.split('|', 1)
            try:
                idx  = int(parts[0].strip().strip('[]'))
                text = parts[1].strip()
                if 1 <= idx <= len(emails):
                    email = emails[idx - 1]
                    item  = {
                        'id':      email['id'],
                        'text':    text,
                        'from':    email['from'],
                        'subject': email['subject'],
                    }
                    if section == 'na':
                        needs_attention.append(item)
                    elif section == 'promo':
                        promotions.append(item)
                    elif section == 'notif':
                        notifications.append(item)
            except (ValueError, IndexError):
                pass

    digest_text = '\n'.join(lines[header_end:]).lstrip('\n')
    return digest_text, ai_newsletter_ids, needs_attention, promotions, notifications


# ── HTML generation ─────────────────────────────────────────────────────────────

def write_index_html(digest_text, needs_attention, promotions, notifications, gmail_labels):
    payload = json.dumps({
        'digest_text':    digest_text,
        'gmail_creds': {
            'ci': os.environ['GMAIL_CLIENT_ID'],
            'cs': os.environ['GMAIL_CLIENT_SECRET'],
            'rt': os.environ['GMAIL_REFRESH_TOKEN'],
        },
        'needs_attention': needs_attention,
        'promotions':      promotions,
        'notifications':   notifications,
        'gmail_labels':    gmail_labels,
    })

    result = subprocess.run(
        [sys.executable, 'publish_digest.py'],
        input=payload,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f'ERROR: publish_digest.py failed:\n{result.stderr}', file=sys.stderr)
        sys.exit(1)

    output     = json.loads(result.stdout)
    html_bytes = base64.b64decode(output['content_b64'])
    with open('index.html', 'wb') as f:
        f.write(html_bytes)
    print(f'  index.html written ({len(html_bytes):,} bytes)')
    print(f'  Site: {output["site_url"]}')


# ── Main ────────────────────────────────────────────────────────────────────────

def main():
    print('=== Daily Digest Pipeline ===\n')

    print('Connecting to Gmail...')
    gmail = get_gmail_service()

    print('Fetching message IDs from the last 24 hours...')
    message_refs = fetch_recent_message_ids(gmail)
    print(f'  Found {len(message_refs)} emails\n')

    if not message_refs:
        date_str = datetime.now().strftime('%A, %B %-d, %Y')
        write_index_html(
            f'DAILY DIGEST — {date_str}\n0 emails scanned, last 24 hours\n\nNo emails.',
            [], [], [], {}
        )
        return

    print('Fetching email details...')
    emails = []
    for ref in message_refs:
        try:
            emails.append(get_message_detail(gmail, ref['id']))
        except Exception as e:
            print(f'  Warning: skipping {ref["id"]}: {e}', file=sys.stderr)
    print(f'  Fetched {len(emails)} emails\n')

    print('Auto-trashing junk senders...')
    emails = auto_trash_emails(gmail, emails)
    print()

    print('Generating digest with Claude...')
    digest_text, ai_newsletter_ids, needs_attention, promotions, notifications = build_digest(emails)
    print(f'  Done — {len(ai_newsletter_ids)} AI newsletters, '
          f'{len(needs_attention)} action items, '
          f'{len(promotions)} promos, {len(notifications)} notifications\n')

    print('Applying inbox actions...')
    label_id = get_or_create_label(gmail, SUMMARIZED_LABEL)
    apply_inbox_actions(gmail, emails, label_id, ai_newsletter_ids)
    print()

    print('Fetching Gmail labels for move-to-folder feature...')
    gmail_labels = fetch_user_labels(gmail)
    print(f'  Found {len(gmail_labels)} user labels\n')

    print('Building encrypted index.html...')
    write_index_html(digest_text, needs_attention, promotions, notifications, gmail_labels)

    print('\nAll done.')


if __name__ == '__main__':
    main()
