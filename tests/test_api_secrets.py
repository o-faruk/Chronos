import boto3
import pytest
from moto import mock_aws


@pytest.fixture
def aws(monkeypatch):
    monkeypatch.delenv("SPACETRACK_USERNAME", raising=False)
    monkeypatch.delenv("SPACETRACK_PASSWORD", raising=False)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        ssm = boto3.client("ssm", region_name="us-east-1")
        ssm.put_parameter(Name="/chronos/spacetrack/username", Type="String", Value="tester@example.com")
        ssm.put_parameter(Name="/chronos/spacetrack/password", Type="SecureString", Value="hunter2")
        yield


def test_loads_credentials_from_ssm_into_env(aws, monkeypatch):
    import api.secrets as secrets
    monkeypatch.setattr(secrets, "_loaded", False)

    secrets.load_spacetrack_credentials_into_env()

    import os
    assert os.environ["SPACETRACK_USERNAME"] == "tester@example.com"
    assert os.environ["SPACETRACK_PASSWORD"] == "hunter2"


def test_does_not_refetch_once_loaded(aws, monkeypatch):
    import api.secrets as secrets
    monkeypatch.setattr(secrets, "_loaded", False)
    secrets.load_spacetrack_credentials_into_env()

    monkeypatch.setenv("SPACETRACK_PASSWORD", "changed-locally")
    secrets.load_spacetrack_credentials_into_env()

    import os
    assert os.environ["SPACETRACK_PASSWORD"] == "changed-locally"
