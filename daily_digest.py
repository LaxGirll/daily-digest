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

def build_digest_text(emails):
    """Returns (digest_text, ai_newsletter_ids) where ai_newsletter_ids is a set of
    email IDs Claude identified as AI-focused newsletters to archive."""
    client = anthropic.Anthropic(api_key=os.environ['ANTHROPIC_API_KEY'])
    date_str = datetime.now().strftime('%A, %B %-d, %Y')
    total    = len(emails)

    # Build the email list for Claude, keyed by index
    lines = []
    for i, e in enumerate(emails, 1):
        lines.append(
            f"[{i}] FROM: {e['from']}\n"
            f"SUBJECT: {e['subject']}\n"
            f"BODY:\n{e['body']}\n"
        )
    emails_text = '\n---\n'.join(lines)

    prompt = f"""You are generating a daily email digest for {date_str}.

ABOUT ME: I am a Product Manager at Vanguard building AI-powered tools for Portfolio Managers.
I am also an AI practitioner who wants to stay current on what's new, cool, and useful in AI —
models, tools, frameworks, agents, anything that could make my work better or faster.

Here are {total} emails received in the last 24 hours:

{emails_text}

Your response must start with a machine-readable classification line, then the digest.

=== STEP 1: CLASSIFICATION LINE (required, first line of your response) ===

Output exactly one line in this format — the indices of emails that are AI-focused newsletters:
AI_NEWSLETTERS: 2,7,11

- AI newsletters = any newsletter/bulk email primarily about AI, LLMs, machine learning,
  AI agents, AI tools, or AI in business/finance. Examples: The Rundown AI, TLDR AI,
  McKinsey AI insights, Ben's Bites, Import AI, The Batch, AI Breakfast, etc.
- Do NOT include: cooking newsletters, book deals, school emails, general news,
  financial news without AI focus, retail/shopping, or personal emails.
- If no AI newsletters, output: AI_NEWSLETTERS: none

=== STEP 2: DIGEST (immediately after the classification line, no blank line) ===

Produce the digest in EXACTLY the following format.
Sections are separated by a line containing only three dashes (---).
Omit any section (including its --- separator) if there is nothing to put in it.

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

NEEDS ATTENTION
- [Specific, actionable items only — replies needed, decisions, deadlines]

---

AI NEWSLETTERS — What You Need to Know
[Only AI-focused newsletters go here. Two lenses for every summary:]
[1. PM at Vanguard building tools for Portfolio Managers — what's relevant to your work?]
[2. AI Practitioner — what's new/cool/useful you'd want to know about?]

Sender Name -- "Subject or topic"
[PM angle: 1-2 sentences on how this applies to your work at Vanguard]
[AI angle: 1-2 sentences on what's cool or new for an AI practitioner]

---

BOOKS & READING
Source Name -- "Title or Deal"
Brief note — genre, why interesting, price if a deal.

---

FOOD & RECIPES
Source Name -- "Dish or Topic"
1-2 sentence description.

---

KIDS & FAMILY
- [Bullet point per relevant item]

---

REGULAR EMAIL SUMMARY
[Remaining emails grouped by theme, bullet points]

Rules:
- Keep everything tight and useful.
- Only flag real action items in NEEDS ATTENTION.
- Skip pure promotional emails with no real content.
- Use exact --- separators between sections.
- Omit empty sections entirely.
"""

    with client.messages.stream(
        model=CLAUDE_MODEL,
        max_tokens=4096,
        thinking={'type': 'adaptive'},
        messages=[{'role': 'user', 'content': prompt}],
    ) as stream:
        final = stream.get_final_message()

    raw = ''.join(b.text for b in final.content if b.type == 'text')

    # Parse the AI_NEWSLETTERS classification line off the top
    ai_newsletter_ids = set()
    lines_out = raw.splitlines()
    digest_lines = []
    for i, line in enumerate(lines_out):
        if line.startswith('AI_NEWSLETTERS:'):
            value = line.split(':', 1)[1].strip()
            if value.lower() != 'none':
                try:
                    indices = [int(x.strip()) for x in value.split(',') if x.strip().isdigit()]
                    # Map 1-based indices back to email IDs
                    for idx in indices:
                        if 1 <= idx <= len(emails):
                            ai_newsletter_ids.add(emails[idx - 1]['id'])
                except ValueError:
                    pass
            digest_lines = lines_out[i + 1:]
            break
    else:
        digest_lines = lines_out  # No classification line found, use everything

    digest_text = '\n'.join(digest_lines).lstrip('\n')
    return digest_text, ai_newsletter_ids


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

        print('Generating digest with Claude...')
        digest_text, ai_newsletter_ids = build_digest_text(emails)
        print(f'  Done — {len(ai_newsletter_ids)} AI newsletters identified\n')

        print('Applying inbox actions...')
        label_id = get_or_create_label(gmail, SUMMARIZED_LABEL)
        apply_inbox_actions(gmail, emails, label_id, ai_newsletter_ids)
        print()

    print('Building encrypted index.html...')
    write_index_html(digest_text)

    print('\nAll done.')


if __name__ == '__main__':
    main()
