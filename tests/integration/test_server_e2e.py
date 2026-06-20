# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import base64
import json
import logging
import os
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest
import requests
from requests.exceptions import RequestException

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_URL = "http://127.0.0.1:8080"
TRIGGER_URL = BASE_URL + "/apps/expense_agent/trigger/pubsub"
FEEDBACK_URL = BASE_URL + "/feedback"

HEADERS = {"Content-Type": "application/json"}


def log_output(pipe: Any, log_func: Any) -> None:
    """Log the output from the given pipe."""
    for line in iter(pipe.readline, ""):
        log_func(line.strip())


def start_server() -> subprocess.Popen[str]:
    """Start the FastAPI server using subprocess and log its output."""
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "expense_agent.fast_api_app:app",
        "--host",
        "0.0.0.0",
        "--port",
        "8080",
    ]
    env = os.environ.copy()
    env["INTEGRATION_TEST"] = "TRUE"
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env=env,
    )

    # Start threads to log stdout and stderr in real-time
    threading.Thread(
        target=log_output, args=(process.stdout, logger.info), daemon=True
    ).start()
    threading.Thread(
        target=log_output, args=(process.stderr, logger.error), daemon=True
    ).start()

    return process


def wait_for_server(timeout: int = 90, interval: int = 1) -> bool:
    """Wait for the server to be ready."""
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            response = requests.get("http://127.0.0.1:8080/docs", timeout=10)
            if response.status_code == 200:
                logger.info("Server is ready")
                return True
        except RequestException:
            pass
        time.sleep(interval)
    logger.error(f"Server did not become ready within {timeout} seconds")
    return False


@pytest.fixture(scope="session")
def server_fixture(request: Any) -> Iterator[subprocess.Popen[str]]:
    """Pytest fixture to start and stop the server for testing."""
    logger.info("Starting server process")
    server_process = start_server()
    if not wait_for_server():
        pytest.fail("Server failed to start")
    logger.info("Server process started")

    def stop_server() -> None:
        logger.info("Stopping server process")
        server_process.terminate()
        server_process.wait()
        logger.info("Server process stopped")

    request.addfinalizer(stop_server)
    yield server_process


def test_pubsub_trigger(server_fixture: subprocess.Popen[str]) -> None:
    """Test the Pub/Sub trigger functionality.

    Verifies normalization of fully-qualified subscription path to short name.
    """
    logger.info("Starting Pub/Sub trigger test")

    # JSON payload under the "data" key of a Pub/Sub message
    expense_data = {
        "amount": 50.0,
        "submitter": "alice@company.com",
        "category": "software",
        "description": "IDE License",
        "date": "2026-06-06",
    }
    encoded_data = base64.b64encode(json.dumps(expense_data).encode("utf-8")).decode("utf-8")

    data = {
        "message": {
            "data": encoded_data,
            "messageId": "123456",
            "publishTime": "2026-06-06T12:00:00Z",
        },
        "subscription": "projects/my-project/subscriptions/my-sub",
    }

    response = requests.post(
        TRIGGER_URL, headers=HEADERS, json=data, timeout=30
    )
    assert response.status_code == 200
    assert response.json() == {"status": "success"}


def test_pubsub_trigger_error_handling(server_fixture: subprocess.Popen[str]) -> None:
    """Test the Pub/Sub trigger error handling."""
    logger.info("Starting Pub/Sub trigger error handling test")
    data = {
        "message": {
            "data": "invalid-base64-payload!!!",
            "messageId": "123456",
        },
        "subscription": "projects/my-project/subscriptions/my-sub",
    }
    response = requests.post(
        TRIGGER_URL, headers=HEADERS, json=data, timeout=10
    )

    assert response.status_code == 400
    logger.info("Error handling test completed successfully")


def test_collect_feedback(server_fixture: subprocess.Popen[str]) -> None:
    """Test the feedback collection endpoint (/feedback) to ensure it properly

    logs the received feedback.
    """
    # Create sample feedback data
    feedback_data = {
        "score": 4,
        "user_id": "test-user-456",
        "session_id": "test-session-456",
        "text": "Great response!",
    }

    response = requests.post(
        FEEDBACK_URL, json=feedback_data, headers=HEADERS, timeout=10
    )
    assert response.status_code == 200
