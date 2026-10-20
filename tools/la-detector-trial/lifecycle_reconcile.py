"""Operator CLI: fail an expired indeterminate lease whose launch never proved a unit.

Run on LA only after stopping the lifecycle supervisor; the ledger owner lock
refuses to open while it runs. There is deliberately no HTTP route. It never
starts, stops or kills a unit: an active or populated worker keeps the lease
indeterminate for manual diagnosis.
"""
import argparse
import json
import os
import sys
from lifecycle import LifecycleError


def reconcile(lifecycle, launcher, request_id):
    return lifecycle.reconcile_unlaunched(request_id, launcher.absence)


def open_ledger():
    from lifecycle import Lifecycle
    from lifecycle_app import private
    from lifecycle_launcher import Launcher
    from session_budget import provisioned_budget
    from start_live import BASE
    os.umask(0o077)
    root=private(BASE/'lifecycle-private',directory=True)
    for name in ('lifecycle.sqlite','lifecycle.sqlite.owner','lifecycle.sqlite-journal'):
        if (root/name).exists() or (root/name).is_symlink():private(root/name)
    launcher=Launcher()
    return Lifecycle(root/'lifecycle.sqlite',launcher,lease_seconds=provisioned_budget()['lease_seconds']),launcher


def main(argv=None, *, opener=open_ledger):
    parser=argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('request_id')
    args=parser.parse_args(argv)
    lifecycle,launcher=opener()
    try:
        print(json.dumps(reconcile(lifecycle,launcher,args.request_id),sort_keys=True))
        return 0
    except LifecycleError as error:
        print(json.dumps({'reason':error.reason}),file=sys.stderr)
        return 1
    finally:
        # Not Lifecycle.close(): its final tick may stop units; release only the ledger.
        lifecycle.db.close();lifecycle.owner_file.close()


if __name__=='__main__':raise SystemExit(main())
