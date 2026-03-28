#!/usr/bin/env python3
"""
get_refresh_token.py
One-time local script to authorize Gmail access and print the secrets
you'll need to add to GitHub.

Usage:
  pip install -r requirements.txt
  python get_refresh_token.py
"""
import json
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ['https://www.googleapis.com/auth/gmail.modify']


def main():
    print("\nGmail OAuth Setup")
    print("=" * 50)
    print("This opens your browser once to authorize Gmail access.")
    print("After that you'll never need to do it again.\n")

    creds_path = input("Path to your downloaded credentials JSON file\n(e.g. C:\\Users\\stant\\Downloads\\client_secret_....json): ").strip().strip('"')

    # Pull client_id / client_secret out of the file so we can print them
    with open(creds_path) as f:
        raw = json.load(f)
    client_info = raw.get('installed') or raw.get('web')
    if not client_info:
        print("\nERROR: Unrecognized credentials file format.")
        return

    client_id     = client_info['client_id']
    client_secret = client_info['client_secret']

    # Run the local OAuth flow — opens browser automatically
    flow = InstalledAppFlow.from_client_secrets_file(creds_path, SCOPES)
    creds = flow.run_local_server(port=0)

    print("\n" + "=" * 50)
    print("SUCCESS! Add these 4 secrets to your GitHub repo:")
    print("  (Settings → Secrets and variables → Actions → New repository secret)")
    print("=" * 50)
    print(f"\nGMAIL_CLIENT_ID\n  {client_id}")
    print(f"\nGMAIL_CLIENT_SECRET\n  {client_secret}")
    print(f"\nGMAIL_REFRESH_TOKEN\n  {creds.refresh_token}")
    print("\nANTHROPIC_API_KEY\n  <your Anthropic API key>")
    print("\nDIGEST_PASSWORD\n  <the password you use to unlock your digest page>")
    print("\n" + "=" * 50)
    print("Done! You can delete the credentials JSON file now if you like.\n")


if __name__ == '__main__':
    main()
