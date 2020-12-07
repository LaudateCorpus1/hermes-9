import json
from pathlib import Path


def is_ready_for_sending(folder):
    """Checks if a case in the outgoing folder is ready for sending by the dispatcher.

    No lock file (.lock) should be in sending folder and no error file (.error),
    if there is one copy/move is not done yet. Also at least some dicom files
    should be there for sending. Also checks for a target.json file and if it is
    valid.
    """
    path = Path(folder)
    folder_status = (
        not (path / ".lock").exists()
        and not (path / ".error").exists()
        and not (path / ".sending").exists()
        and len(list(path.glob("*.dcm"))) > 0
    )
    content = is_target_json_valid(folder)
    if folder_status and content:
        return content
    return False


def has_been_send(folder):
    """Checks if the given folder has already been sent."""
    return (Path(folder) / "sent.txt").exists()


def is_target_json_valid(folder):
    """
    Checks if the target.json file exists and is also valid. Mandatory
    keys are target_ip, target_port and target_aet_target.
    """
    path = Path(folder) / "target.json"
    if not path.exists():
        return None

    with open(path, "r") as f:
        target = json.load(f)

    if not all(
        [key in target for key in ["target_ip", "target_port", "target_aet_target"]]
    ):
        """
        send_series_event(
            s_events.ERROR,
            target.get("series_uid", None),
            0,
            target.get("target_name", None),
            f"target.json is missing a mandatory key {target}",
        )
        """
        return None
    return target
