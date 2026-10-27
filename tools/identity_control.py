"""Local host administration of durable Tensor Master identities."""
import argparse
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app.config import get_settings
from backend.app.identity import authority


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['status', 'backup', 'repair-exports', 'revoke-worker'])
    parser.add_argument('--worker')
    args = parser.parse_args()
    identity = authority(get_settings().token_file)
    if args.action == 'backup':
        destination = identity.path.with_name('identity.backup.sqlite3')
        with identity.connection() as source, closing(sqlite3.connect(destination)) as target:
            source.backup(target)
        print('Identity backup saved in the protected credential directory.')
    elif args.action == 'repair-exports':
        identity.export()
        print('Provisioning exports repaired; farm and device identities retained.')
    elif args.action == 'revoke-worker':
        if not args.worker:
            parser.error('--worker is required')
        print(f'Revoked {identity.revoke_worker(args.worker)} credential(s) for {args.worker}.')
    else:
        with identity.connection() as db:
            integrity = db.execute('PRAGMA quick_check').fetchone()[0]
            for row in db.execute('SELECT role,count(*) AS count FROM credentials WHERE revoked IS NULL GROUP BY role'):
                print(f"{row['role']}: {row['count']} active credential(s)")
            print('Identity store integrity: ' + integrity)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
