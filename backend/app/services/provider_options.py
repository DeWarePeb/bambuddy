"""Per-printer settings that only one provider has (fork).

`printers.provider_options` has existed as a nullable JSON column since A0 and
had no reader until now. It is where a knob lives when it is meaningful for one
transport and meaningless for the others — a Klipper install names its own
objects, so which one is the chamber is a property of that printer's
`printer.cfg`, not something Bambuddy can know or a Bambu printer has.

A JSON blob rather than a column per knob because the alternative is a
migration every time a provider grows a setting, on a table upstream also
changes. The API stays typed: routes read and write named fields and this
module is the only place that knows the storage is JSON.

Everything here is forgiving on the way in. The column is free-form text that a
hand-edited database or an older build could leave in any shape, and a printer
that fails to load is worse than a printer with default options.
"""

import json


def load(raw: str | None) -> dict:
    """Parse the stored options, answering an empty dict for anything unusable.

    Never raises: a malformed blob means "no options set", not a printer that
    cannot be constructed.
    """
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def get_str(raw: str | None, key: str) -> str | None:
    """One string option, or None when unset, blank or the wrong type."""
    value = load(raw).get(key)
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def merge(raw: str | None, updates: dict) -> str | None:
    """Fold *updates* into the stored options and re-serialise.

    A key set to None is removed rather than stored as null, so "clear this
    setting" and "this setting was never set" are the same state on the way
    back out — there is no third meaning for a provider knob. Returns None when
    nothing is left, keeping the column NULL rather than an empty object.
    """
    options = load(raw)
    for key, value in updates.items():
        if value is None:
            options.pop(key, None)
        else:
            options[key] = value
    return json.dumps(options, sort_keys=True) if options else None
