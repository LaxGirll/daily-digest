#!/usr/bin/env python3
"""
daily_digest.py
Daily email digest pipeline — designed to run in GitHub Actions.

1. Fetches emails from the last 24 hours via Gmail API
2. Detects newsletters vs. regular email
3. Asks Claude to categorize, summarize, and flag action items
4. Archives newsletters, labels regular emails "AI-Summarized"
5. Pipes the digest text through publish_digest.py → writes index.html

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
BODY_LIMIT   = 2500   # chars per email sent to Claude
CLAUDE_MODEL = 'claude-opus-4-6'

NEWSLETTER_HEADERS = {'list-unsubscribe', 'list-id', 'x-mailchimp-id',
                      'x-campaign', 'x-mailer-recvtype'}
NEWSLETTER_PRECEDENCE = {'bulk', 'list', 'junk'}
SUMMARIZED_LABEL = 'AI-Summarized'


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
    # Force a token refresh so we have a valid access token
    creds.refresh(Request())
    return build('gmail', 'v1', credentials=creds, cache_discovery=False)


def fetch_recent_message_ids(gmail):
    since_ts = int((datetime.now(timezone.utc) - timedelta(hours=24)).timestamp())
    result = gmail.users().messages().list(
        userId='me',
        q=f'after:{since_ts}',
        maxResults=MAX_EMAILS,
    ).execute()
    return result.get('messages', [])


def get_message_detail(gmail, msg_id):
    msg = gmail.users().messages().get(
        userId='me', id=msg_id, format='full'
    ).execute()

    headers = {h['name'].lower(): h['value']
               for h in msg['payload'].get('headers', [])}

    subject = headers.get('subject', '(no subject)')
    from_   = headers.get('from', '')
    date_   = headers.get('date', '')

    # Newsletter detection: header keys or Precedence value
    is_newsletter = bool(NEWSLETTER_HEADERS & set(headers.keys()))
    if not is_newsletter:
        is_newsletter = headers.get('precedence', '').lower() in NEWSLETTER_PRECEDENCE

    body = _extract_body(msg['payload'])[:BODY_LIMIT]

    return {
        'id':            msg_id,
        'subject':       subject,
        'from':          from_,
        'date':          date_,
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


def apply_inbox_actions(gmail, emails, summarized_label_id):
    for email in emails:
        if 'INBOX' not in email['label_ids']:
            continue
        try:
            if email['is_newsletter']:
                gmail.users().messages().modify(
                    userId='me', id=email['id'],
                    body={'removeLabelIds': ['INBOX']}
                ).execute()
            else:
                gmail.users().messages().modify(
                    userId='me', id=email['id'],
                    body={'addLabelIds': [summarized_label_id]}
                ).execute()
        except HttpError as e:
            print(f'  Warning: could not modify {email["id"]}: {e}', file=sys.stderr)


# ── Claude summarization ────────────────────────────────────────────────────────

def build_digest_text(emails):
    client = anthropic.Anthropic(api_key=os.environ['ANTHROPIC_API_KEY'])
    date_str = datetime.now().strftime('%A, %B %-d, %Y')
    total    = len(emails)

    # Build the email list for Claude
    lines = []
    for i, e in enumerate(emails, 1):
        lines.append(
            f"[{i}] FROM: {e['from']}\n"
            f"SUBJECT: {e['subject']}\n"
            f"TYPE: {'Newsletter/Bulk' if e['is_newsletter'] else 'Regular'}\n"
            f"BODY:\n{e['body']}\n"
        )
    emails_text = '\n---\n'.join(lines)

    prompt = f"""You are generating a daily email digest for {date_str}.
Here are {total} emails received in the last 24 hours.

{emails_text}

Produce a digest in EXACTLY the following format.
Sections are separated by a line containing only three dashes (---).
Do not add any text before the first section or after the last.

=== FORMAT ===

DAILY DIGEST — {date_str}
{total} emails scanned, last 24 hours

EMAIL COUNT BY CATEGORY
Newsletters: N emails
Work: N emails
Personal: N emails
Promotions: N emails
Notifications: N emails
Other: N emails

---

NEEDS ATTENTION
- [One specific, actionable item per line — things genuinely requiring a reply or decision today]
- [Skip this section entirely if nothing needs attention, but keep the --- separator]

---

NEWSLETTERS — Summaries
[For each newsletter with substantive content use this exact line pattern:]
Sender Name -- "Subject or topic"
2-3 sentence summary of the key content or insight.

[Repeat for each newsletter. Skip promotional/empty ones.]

---

REGULAR EMAIL SUMMARY
[Short grouped summary of non-newsletter emails that don't need action.
Use bullet points grouped by theme, e.g. Work Updates, Notifications, etc.]

=== END FORMAT ===

Rules:
- Keep summaries tight and useful.
- Only flag real action items (replies needed, decisions, deadlines).
- Ignore promotional emails and automated notifications in the newsletter section.
- Use the exact --- separator lines between sections.
"""

    # Use streaming (large input) and get the final message text
    with client.messages.stream(
        model=CLAUDE_MODEL,
        max_tokens=4096,
        thinking={'type': 'adaptive'},
        messages=[{'role': 'user', 'content': prompt}],
    ) as stream:
        final = stream.get_final_message()

    return ''.join(b.text for b in final.content if b.type == 'text')


# ── HTML generation ─────────────────────────────────────────────────────────────

def write_index_html(digest_text):
    result = subprocess.run(
        [sys.executable, 'publish_digest.py'],
        input=digest_text,
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
        date_str    = datetime.now().strftime('%A, %B %-d, %Y')
        digest_text = (
            f'DAILY DIGEST — {date_str}\n'
            '0 emails scanned, last 24 hours\n\n'
            'No emails received in the last 24 hours.'
        )
    else:
        print('Fetching email details...')
        emails = []
        for ref in message_refs:
            try:
                emails.append(get_message_detail(gmail, ref['id']))
            except Exception as e:
                print(f'  Warning: skipping {ref["id"]}: {e}', file=sys.stderr)
        print(f'  Fetched {len(emails)} emails\n')

        print('Applying inbox actions...')
        label_id = get_or_create_label(gmail, SUMMARIZED_LABEL)
        apply_inbox_actions(gmail, emails, label_id)
        newsletters = sum(1 for e in emails if e['is_newsletter'])
        print(f'  Archived {newsletters} newsletters, labeled {len(emails)-newsletters} regular emails\n')

        print('Generating digest with Claude...')
        digest_text = build_digest_text(emails)
        print('  Done\n')

    print('Building encrypted index.html...')
    write_index_html(digest_text)

    print('\nAll done.')


if __name__ == '__main__':
    main()
