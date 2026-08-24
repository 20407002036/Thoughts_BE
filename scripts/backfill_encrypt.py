"""One-shot backfill: encrypt existing plaintext journal rows in place.

Usage (requires SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY and a generated
JOURNAL_ENCRYPTION_KEY — see README):
    python -m scripts.backfill_encrypt            # apply
    python -m scripts.backfill_encrypt --dry-run  # count only

Rows are updated one at a time; safe to re-run because it only touches rows
where data_encrypted is null.
"""

import argparse
import sys

from supabase import create_client

from app.core.crypto import JournalCipher
from app.core.settings import get_settings
from app.repositories.journal_repository import SENSITIVE_FIELDS


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--batch-size", type=int, default=200)
    args = parser.parse_args()

    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key:
        print("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required", file=sys.stderr)
        return 2

    cipher = JournalCipher(settings)
    if not cipher.enabled:
        print("JOURNAL_ENCRYPTION_KEY must be configured before backfilling", file=sys.stderr)
        return 2

    client = create_client(settings.supabase_url, settings.supabase_service_role_key)
    table = client.table(settings.supabase_journals_table)

    processed = 0
    while True:
        result = (
            table.select("*")
            .is_("data_encrypted", "null")
            .limit(args.batch_size)
            .execute()
        )
        rows = result.data or []
        if not rows:
            break

        for row in rows:
            user_id = str(row.get("user_id", ""))
            sensitive = {field: row.get(field) for field in SENSITIVE_FIELDS}
            blob = cipher.encrypt_fields(sensitive, user_id)
            if not args.dry_run:
                table.update({"data_encrypted": blob, "enc_version": 1}).eq("id", row["id"]).execute()
            processed += 1

        if args.dry_run:
            break

    mode = "would encrypt" if args.dry_run else "encrypted"
    print(f"{mode} {processed} journal row(s)")
    if not args.dry_run:
        print("Next: verify reads, then drop the legacy plaintext columns in a follow-up migration.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
