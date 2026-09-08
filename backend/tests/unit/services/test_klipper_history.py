"""Voron patch series: Moonraker history -> archive rows."""

from datetime import datetime

from backend.app.services.klipper_history import archive_from_job, fetch_history

_JOB = {
    "job_id": "000042",
    "filename": "voron/benchy.gcode",
    "status": "completed",
    "start_time": 1615764496.6,
    "end_time": 1615768496.6,
    "print_duration": 3600.4,
    "total_duration": 3980.0,
    "filament_used": 7834.2,
    "metadata": {
        "layer_count": 212,
        "layer_height": 0.2,
        "nozzle_diameter": 0.4,
        "first_layer_bed_temp": 100,
        "first_layer_extr_temp": 250,
        "filament_weight_total": 23.4,
        "filament_type": "ABS",
    },
}


def test_maps_a_completed_job():
    archive = archive_from_job(3, _JOB)

    assert archive is not None
    assert archive.printer_id == 3
    assert archive.filename == "benchy.gcode"
    assert archive.print_name == "benchy"
    assert archive.status == "completed"
    assert archive.print_time_seconds == 3600
    assert archive.total_layers == 212
    assert archive.filament_used_grams == 23.4
    assert archive.filament_type == "ABS"
    assert archive.bed_temperature == 100
    assert archive.nozzle_temperature == 250
    # No file is fetched, and the row says so the way upstream's fallback does.
    assert archive.file_path == ""
    assert archive.file_size == 0
    assert archive.extra_data["no_3mf_available"] is True
    assert archive.extra_data["filament_used_mm"] == 7834.2


def test_subtask_id_is_the_import_identity():
    assert archive_from_job(1, _JOB).subtask_id == "moonraker:000042"


def test_timestamps_are_naive_utc():
    archive = archive_from_job(1, _JOB)

    assert archive.started_at == datetime(2021, 3, 14, 23, 28, 16, 600000)
    assert archive.completed_at is not None
    assert archive.started_at.tzinfo is None


def test_status_vocabulary():
    def status_for(moonraker_status):
        archive = archive_from_job(1, {**_JOB, "status": moonraker_status})
        return archive.status if archive else None

    assert status_for("completed") == "completed"
    assert status_for("cancelled") == "aborted"
    assert status_for("interrupted") == "aborted"
    assert status_for("error") == "failed"
    assert status_for("klippy_shutdown") == "failed"
    # A job still running belongs to the live path, not to the importer.
    assert status_for("in_progress") is None
    assert status_for("something_new") is None


def test_filament_grams_stay_empty_without_a_slicer_weight():
    """Millimetres are not grams without a diameter and a density we do not have."""
    job = {**_JOB, "metadata": {k: v for k, v in _JOB["metadata"].items() if k != "filament_weight_total"}}

    archive = archive_from_job(1, job)

    assert archive.filament_used_grams is None
    assert archive.extra_data["filament_used_mm"] == 7834.2


def test_fetch_history_pages_until_the_printer_runs_out():
    calls: list[str] = []

    class FakeClient:
        def _get(self, path):
            calls.append(path)
            start = int(path.split("start=")[1].split("&")[0])
            # 150 jobs on the printer, 100 per page.
            remaining = max(0, 150 - start)
            return {"jobs": [{"job_id": str(start + i)} for i in range(min(100, remaining))]}

    jobs = fetch_history(FakeClient(), limit=500)

    assert len(jobs) == 150
    assert len(calls) == 2


def test_fetch_history_respects_the_limit():
    class FakeClient:
        def _get(self, path):  # noqa: ARG002 - always a full page
            return {"jobs": [{"job_id": str(i)} for i in range(100)]}

    assert len(fetch_history(FakeClient(), limit=10)) == 10
