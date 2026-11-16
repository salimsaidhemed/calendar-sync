"""Explicit local operator CLI for the new foundation; never starts providers."""

import argparse
import json
import sqlite3

from calendar_sync.control import ControlError, ControlStore, OwnerContext, create_key, private_file
from calendar_sync.control_migration import import_legacy, restore_bundle, snapshot_bundle
import os


def read_assignments(path):
    def unique(items):
        result = {}
        for key,value in items:
            if key in result:
                raise ControlError('control_manifest_duplicate')
            result[key] = value
        return result
    with os.fdopen(private_file(path),'r') as stream:
        content = stream.read(65537)
    if len(content)>65536:
        raise ControlError('control_manifest_invalid')
    try:
        data = json.loads(content,object_pairs_hook=unique)
    except (ValueError,UnicodeError):
        raise ControlError('control_manifest_invalid') from None
    if not isinstance(data,dict) or len(data)>1000:
        raise ControlError('control_manifest_invalid')
    return data


def main(argv=None):
    parser = argparse.ArgumentParser(description='Offline control-store administration; no provider requests')
    parser.add_argument('--key-file',required=True)
    parser.add_argument('--database')
    commands = parser.add_subparsers(dest='command',required=True)
    commands.add_parser('init-key')
    commands.add_parser('init')
    owner = commands.add_parser('register-owner')
    owner.add_argument('--issuer',required=True)
    owner.add_argument('--subject',required=True)
    migration = commands.add_parser('import-legacy')
    migration.add_argument('--engine-file',required=True)
    migration.add_argument('--assignments',required=True)
    migration.add_argument('--apply',action='store_true',help='Default is transactional preview/rollback')
    rotation = commands.add_parser('rotate-key')
    rotation.add_argument('--new-key-file',required=True)
    backup = commands.add_parser('snapshot')
    backup.add_argument('--engine-file',required=True)
    backup.add_argument('--destination',required=True)
    restore = commands.add_parser('restore')
    restore.add_argument('--source',required=True)
    restore.add_argument('--destination',required=True)
    args = parser.parse_args(argv)
    if args.command not in ('init-key','restore') and not args.database:
        parser.error('--database required for this command')
    try:
        if args.command == 'init-key':
            create_key(args.key_file)
            result = {'key_created':True}
        elif args.command == 'restore':
            result = restore_bundle(args.source,args.destination,args.key_file)
        else:
            with ControlStore(args.database,args.key_file,maintenance=True) as store:
                if args.command == 'init':
                    result = {'control_schema':1}
                elif args.command == 'register-owner':
                    # Subject must be independently verified in Keycloak by operator.
                    result = {'owner_id':store.register_owner(OwnerContext(args.issuer,args.subject))}
                elif args.command == 'import-legacy':
                    result = import_legacy(store,args.engine_file,read_assignments(args.assignments),apply=args.apply)
                elif args.command == 'rotate-key':
                    store.rotate_key(args.new_key_file)
                    result = {'key_rotated':True}
                else:
                    result = snapshot_bundle(store,args.engine_file,args.destination)
        print(json.dumps(result,sort_keys=True))
        return 0
    except (ControlError,OSError,sqlite3.Error,ValueError,TypeError):
        # Never print raw JSON, paths, crypto/SQLite errors or provider values.
        import sys
        error = sys.exception()
        print(str(error) if isinstance(error,ControlError) else 'control_operation_failed')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
