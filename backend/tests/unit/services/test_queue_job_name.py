"""Fork: a queue item's own label names the file uploaded to the printer.

The printer's screen shows the file it was handed, so renaming the upload is
how a label like an order number gets in front of you at the machine. These
cover the naming composition the dispatcher uses; the dispatcher itself just
picks which string goes in.

The obvious alternative — sending a different ``subtask_name`` on the MQTT
print command and leaving the file alone — is not what this does, because
``subtask_name`` is what this codebase resolves the 3MF, the cover image, the
archive name and the running-print match by. A label there would split the
print's identity from its file.
"""

import pytest

from backend.app.services.moonraker_dispatch import moonraker_remote_filename
from backend.app.utils.filename import derive_remote_filename, safe_path_component

MAX_LABEL_BYTES = 240


def upload_source(job_name: str | None, filename: str) -> str:
    """The dispatcher's choice of the string both derivers work from."""
    if job_name:
        return safe_path_component(job_name, fallback=filename, max_bytes=MAX_LABEL_BYTES)
    return filename


# ------------------------------------------------------------------ Bambu


def test_no_label_keeps_the_file_name():
    assert derive_remote_filename(upload_source(None, "benchy.gcode.3mf")) == "benchy.3mf"


def test_label_names_the_upload():
    assert derive_remote_filename(upload_source("Order 1234", "benchy.gcode.3mf")) == "Order_1234.3mf"


def test_label_spaces_become_underscores():
    """The firmware parses ``ftp://{filename}`` as a URL, so spaces cannot survive."""
    assert " " not in derive_remote_filename(upload_source("two words", "f.3mf"))


@pytest.mark.parametrize("label", ["A/B", "A\\B", "A:B", "A|B", "A?B", "A*B", 'A"B', "A<B", "A>B"])
def test_separators_and_reserved_characters_cannot_survive(label):
    """A label is free text. A slash in one is a path separator, and the SD card
    rejects the rest outright."""
    name = derive_remote_filename(upload_source(label, "f.3mf"))
    assert name == "A-B.3mf"


def test_a_label_of_only_separators_survives_as_dashes():
    """Reserved characters are replaced, not dropped, so a label made only of
    them still yields a legal name rather than falling back. Ugly, but the file
    is named and nothing downstream has to cope with an empty component."""
    assert derive_remote_filename(upload_source("///", "benchy.3mf")) == "---.3mf"


def test_dot_only_label_falls_back():
    assert derive_remote_filename(upload_source("..", "benchy.3mf")) == "benchy.3mf"


def test_whitespace_label_is_treated_as_no_label():
    # Falsy after the schema strips it is the normal case; a stray "  " must not
    # produce a file called "_.3mf".
    assert derive_remote_filename(upload_source("   ", "benchy.3mf")) == "benchy.3mf"


def test_long_label_leaves_room_for_the_suffix():
    """The budget is short of the filesystem cap so the appended extension fits."""
    name = derive_remote_filename(upload_source("x" * 400, "f.3mf"))
    assert len(name.encode("utf-8")) <= 255
    assert name.endswith(".3mf")


def test_label_that_looks_like_a_print_file_is_not_double_suffixed():
    assert derive_remote_filename(upload_source("Order 9.gcode.3mf", "f.3mf")) == "Order_9.3mf"


# ---------------------------------------------------------------- Klipper


def test_label_names_the_moonraker_upload():
    """Spaces stay, unlike the Bambu path. Moonraker takes the name over an HTTP
    multipart upload, so there is no ``ftp://`` URL to be parsed and no reason
    to mangle the label the user typed."""
    assert moonraker_remote_filename(upload_source("Order 1234", "benchy.3mf")) == "Order 1234.gcode"


def test_moonraker_keeps_the_plate_suffix_on_a_label():
    """Plate disambiguation still has to survive, or two plates of one job
    overwrite each other in the gcodes root."""
    assert moonraker_remote_filename(upload_source("Order 1234", "benchy.3mf"), 2) == "Order 1234_plate2.gcode"
