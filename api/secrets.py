"""Populates SPACETRACK_USERNAME/PASSWORD into the process environment from
SSM Parameter Store (SecureString) at Lambda cold start, so ingest/spacetrack.py
(which reads plain env vars -- see docs/decisions.md) doesn't need AWS-specific
code, and credentials are never a plaintext Lambda environment variable
(those are visible in the Lambda console/API to anyone with read access to
the function, unlike a SecureString parameter which requires explicit KMS
decrypt permission).

Deployment note: this reads the parameters, it doesn't create them. Set the
real values out-of-band (never in the SAM template or this repo):

    aws ssm put-parameter --name /chronos/spacetrack/username \\
        --type String --value "you@example.com"
    aws ssm put-parameter --name /chronos/spacetrack/password \\
        --type SecureString --value "..."
"""
from __future__ import annotations

import os

import boto3

USERNAME_PARAM_ENV = "SPACETRACK_USERNAME_SSM_PARAM"
PASSWORD_PARAM_ENV = "SPACETRACK_PASSWORD_SSM_PARAM"
DEFAULT_USERNAME_PARAM = "/chronos/spacetrack/username"
DEFAULT_PASSWORD_PARAM = "/chronos/spacetrack/password"

_loaded = False


def load_spacetrack_credentials_into_env() -> None:
    global _loaded
    if _loaded or os.environ.get("SPACETRACK_USERNAME"):
        return  # already populated (repeat warm-start invocation, or local dev env)

    ssm = boto3.client("ssm")
    username_param = os.environ.get(USERNAME_PARAM_ENV, DEFAULT_USERNAME_PARAM)
    password_param = os.environ.get(PASSWORD_PARAM_ENV, DEFAULT_PASSWORD_PARAM)

    os.environ["SPACETRACK_USERNAME"] = ssm.get_parameter(Name=username_param)["Parameter"]["Value"]
    os.environ["SPACETRACK_PASSWORD"] = ssm.get_parameter(
        Name=password_param, WithDecryption=True,
    )["Parameter"]["Value"]
    _loaded = True
