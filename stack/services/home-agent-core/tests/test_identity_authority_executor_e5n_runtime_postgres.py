from __future__ import annotations

import base64
import os
import uuid

import pytest

from app.identity_authority_executor import (
    AuthorityRequest,
    OPERATIONS,
    execute,
)


CUTOVER_DATABASE_ENV = "TEST_PHASE3_IDENTITY_CUTOVER_E4_DATABASE_URL"
SUCCESS_DOCUMENT_ENV = "TEST_PHASE3_IDENTITY_CUTOVER_E4_DOCUMENT_B64"
SUCCESS_ADMISSION_ENV = "TEST_PHASE3_IDENTITY_CUTOVER_E4_ADMISSION_ID"
HOSTED_GATE_SENTINEL_ENV = "TEST_PHASE3_IDENTITY_ERASURE_E1_RUN_SENTINEL"


@pytest.mark.asyncio
@pytest.mark.skipif(
    not all(
        os.getenv(name)
        for name in (
            HOSTED_GATE_SENTINEL_ENV,
            CUTOVER_DATABASE_ENV,
            SUCCESS_DOCUMENT_ENV,
            SUCCESS_ADMISSION_ENV,
        )
    ),
    reason="E5n isolated authority executor fixture is not configured",
)
async def test_e5n_cutover_executor_replays_only_the_admitted_private_document() -> None:
    document = base64.b64decode(
        os.environ[SUCCESS_DOCUMENT_ENV],
        validate=True,
    )
    admission_id = uuid.UUID(os.environ[SUCCESS_ADMISSION_ENV])

    result = await execute(
        os.environ[CUTOVER_DATABASE_ENV],
        OPERATIONS["cutover"],
        AuthorityRequest(admission_id=admission_id, document=document),
    )

    assert isinstance(result, uuid.UUID)
    assert result.version == 7
