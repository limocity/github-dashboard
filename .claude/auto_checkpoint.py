#!/usr/bin/env python3
"""
auto_checkpoint.py - Portable time-gated WIP checkpoint for Claude Code hooks.

Wired in .claude/settings.json as:
  Stop       -> python .claude/auto_checkpoint.py            (every 30 min of active work)
  PreCompact -> python .claude/auto_checkpoint.py --force    (right before compaction)

Purpose: never lose more than ~30 minutes of active work if a conversation dies
or hits the context limit. The Stop hook fires at the end of every turn but only
commits once >= INTERVAL minutes of ACTIVE work have elapsed (Stop only fires
while Claude is working, so walking away triggers nothing). PreCompact commits
right before context compaction, ignoring the timer.

Pure git, no third-party dependencies. The timestamp lives in .git/ so it never
shows up as a change to commit. Always exits 0 so it can never block a turn;
emits a systemMessage JSON only when it actually commits.
"""
import os
import sys
import json
import subprocess
from datetime import datetime, timezone

INTERVAL_MIN = 30


def git(root, *args, timeout=120):
    r = subprocess.run(['git', '-C', root, *args],
                       capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout or '').strip(), (r.stderr or '').strip()


def repo_root():
    here = os.path.dirname(os.path.abspath(__file__))
    code, out, _ = git(here, 'rev-parse', '--show-toplevel')
    return out if code == 0 and out else os.path.dirname(here)


def main():
    force = '--force' in sys.argv[1:]

    # Drain hook stdin so the pipe never blocks, then ignore it.
    try:
        if not sys.stdin.isatty():
            sys.stdin.read()
    except Exception:
        pass

    root = repo_root()
    stamp = os.path.join(root, '.git', 'auto_checkpoint_stamp')
    now = datetime.now(timezone.utc)

    def read_stamp():
        try:
            with open(stamp) as f:
                return datetime.fromisoformat(f.read().strip())
        except Exception:
            return None

    def write_stamp(ts):
        try:
            with open(stamp, 'w') as f:
                f.write(ts.isoformat())
        except Exception:
            pass

    last = read_stamp()
    if not force:
        # First ever run: start the clock, commit nothing yet.
        if last is None:
            write_stamp(now)
            sys.exit(0)
        elapsed = (now - last).total_seconds() / 60.0
        if elapsed < INTERVAL_MIN:
            sys.exit(0)
    else:
        elapsed = (now - last).total_seconds() / 60.0 if last else 0.0

    # Anything to save? (respects .gitignore)
    code, status, _ = git(root, 'status', '--porcelain')
    if code != 0 or not status.strip():
        if force:
            write_stamp(now)
        # Non-force with nothing to commit: leave the clock so the next Stop with
        # real changes checkpoints promptly instead of waiting another 30 min.
        sys.exit(0)

    label = "pre-compaction" if force else f"{int(elapsed)} min active work"
    msg = f"auto-checkpoint: WIP ({label}) {now.strftime('%Y-%m-%d %H:%M UTC')}"

    git(root, 'add', '-A')
    code, _, _ = git(root, 'commit', '-m', msg)
    if code != 0:
        sys.exit(0)  # nothing actually committed

    write_stamp(now)  # restart the 30-min clock from this commit

    # Push the current branch; failure is non-fatal (work is safe locally).
    bcode, branch, _ = git(root, 'rev-parse', '--abbrev-ref', 'HEAD')
    pushed = False
    if bcode == 0 and branch and branch != 'HEAD':
        pcode, _, _ = git(root, 'push', 'origin', branch)
        pushed = pcode == 0

    where = 'committed + pushed' if pushed else 'committed locally (push pending)'
    print(json.dumps({
        "systemMessage": f"Auto-checkpoint: WIP {where} ({label}).",
        "suppressOutput": True,
    }))
    sys.exit(0)


if __name__ == '__main__':
    main()
