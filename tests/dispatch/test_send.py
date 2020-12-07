import json
import time
from pathlib import Path
from subprocess import CalledProcessError

from common.monitor import configure

from dispatch.send import execute
from dispatch.status import is_ready_for_sending

monitor = configure("","", "0.0.0.0:8080")

import pytest
@pytest.mark.skip(reason="no way of currently testing this")
def test_execute_successful_case(fs, mocker):
    source = "/var/data/source/a"
    success = "/var/data/success/"
    error = "/var/data/error"

    fs.create_dir(source)
    fs.create_dir(success)
    fs.create_file("/var/data/source/a/one.dcm")
    target = {"target_ip": "0.0.0.0", "target_aet_target": "a", "target_port": 90}
    fs.create_file("/var/data/source/a/target.json", contents=json.dumps(target))

    mocker.patch("dispatch.send.run", return_value=0)
    execute(target, Path(source), Path(success), error, 1, 1, monitor)

    assert not Path(source).exists()
    assert (Path(success) / "a").exists()
    assert (Path(success) / "a" / "target.json").exists()
    assert (Path(success) / "a" / "one.dcm").exists()

@pytest.mark.skip(reason="no way of currently testing this")
def test_execute_error_case(fs, mocker):
    """ This case simulates a dcmsend error. After that the retry counter 
    gets increased but the data stays in the folder. """
    source = "/var/data/source/a"
    success = "/var/data/success/"
    error = "/var/data/error"

    fs.create_dir(source)
    fs.create_dir(success)
    fs.create_dir(error)
    fs.create_file("/var/data/source/a/one.dcm")
    target = {"target_ip": "0.0.0.0", "target_aet_target": "a", "target_port": 90}
    fs.create_file("/var/data/source/a/target.json", contents=json.dumps(target))

    mocker.patch(
        "dispatch.send.run", side_effect=CalledProcessError("Mock", cmd="None")
    )
    execute(target, Path(source), Path(success), Path(error), 10, 1, monitor)

    with open("/var/data/source/a/target.json", "r") as f:
        modified_target = json.load(f)

    assert Path(source).exists()
    assert (Path(source) / "target.json").exists()
    assert (Path(source) / "one.dcm").exists()
    assert modified_target["retries"] == 1
    assert is_ready_for_sending(source)


@pytest.mark.skip(reason="no way of currently testing this")
def test_execute_error_case_max_retries_reached(fs, mocker):
    """ This case simulates a dcmsend error. Max number of retries is reached
    and the data is moved to the error folder.
    """
    source = "/var/data/source/a"
    success = "/var/data/success/"
    error = "/var/data/error"

    fs.create_dir(source)
    fs.create_dir(success)
    fs.create_dir(error)
    fs.create_file("/var/data/source/a/one.dcm")
    target = {
        "target_ip": "0.0.0.0",
        "target_aet_target": "a",
        "target_port": 90,
        "retries": 5,
    }
    fs.create_file("/var/data/source/a/target.json", contents=json.dumps(target))

    mocker.patch(
        "dispatch.send.run", side_effect=CalledProcessError("Mock", cmd="None")
    )
    execute(target, Path(source), Path(success), Path(error), 5, 1, monitor)

    with open("/var/data/error/a/target.json", "r") as f:
        modified_target = json.load(f)

    assert (Path(error) / "a" / "target.json").exists()
    assert (Path(error) / "a" / "one.dcm").exists()
    assert modified_target["retries"] == 5
    
@pytest.mark.skip(reason="no way of currently testing this")
def test_execute_error_case_delay_is_not_over(fs, mocker):
    """ This case simulates a dcmsend error. Should not run anything because
    the next_retry_at time has not been passed.
    """
    source = "/var/data/source/a"
    success = "/var/data/success/"
    error = "/var/data/error"

    fs.create_dir(source)
    fs.create_dir(success)
    fs.create_dir(error)
    fs.create_file("/var/data/source/a/one.dcm")
    target = {
        "target_ip": "0.0.0.0",
        "target_aet_target": "a",
        "target_port": 90,
        "retries": 5,
        "next_retry_at": time.time() + 500
    }
    fs.create_file("/var/data/source/a/target.json", contents=json.dumps(target))

    mock = mocker.patch(
        "dispatch.send.run", side_effect=CalledProcessError("Mock", cmd="None")
    )
    
    execute(target, Path(source), Path(success), Path(error), 5, 1, monitor)
    assert not mock.called
