#!/usr/bin/env bash
# Pick a working Python 3.10+; never consume the hook event during a probe.
set -u
probe='import sys; print("NA_PYTHON_OK" if sys.version_info >= (3, 10) else "NA_PYTHON_OLD")'
for interpreter in python3 python; do
    if [ "$("$interpreter" -c "$probe" </dev/null 2>/dev/null)" = "NA_PYTHON_OK" ]; then
        exec "$interpreter" -X utf8 "$@"
    fi
done
if [ "$(py -3 -c "$probe" </dev/null 2>/dev/null)" = "NA_PYTHON_OK" ]; then
    exec py -3 -X utf8 "$@"
fi
printf '%s\n' 'negotiation-analysis: Python 3.10+ not found (tried python3, python, py -3).' >&2
exit 1
