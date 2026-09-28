"""Compose the existing account-linking flow with durable recovery journals.

The private server owns this context for its entire lifetime, drains requests
before exit, and supplies live Core/restore and journal-retention admission.
This module does not start a listener, grant permissions, or approve a link.
"""
import asyncio
import hmac
from contextlib import AsyncExitStack, asynccontextmanager
from inspect import iscoroutinefunction
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy.engine import make_url

from .crypto import FieldCipher
from .shared_auth_proof import SharedLinkProofDatabase
from .shared_link_commitments import SharedLinkCommitments
from .shared_link_confirmation import SharedLinkConfirmationDatabase
from .shared_link_confirmation_journal import SharedLinkConfirmationJournal
from .shared_link_confirmation_preparation import SharedLinkConfirmationPreparer
from .shared_link_issuance import SharedLinkIssuanceDatabase
from .shared_link_issuance_api import LinkIssuanceBinding, create_link_issuance_ingress
from .shared_link_issuance_service import SharedLinkIssuanceService
from .shared_link_journal import SharedLinkJournal
from .shared_link_review_api import LinkReviewBinding, create_link_review_ingress
from .shared_link_review_assembly import SharedLinkReviewAssembly
from .shared_link_review_service import SharedLinkReviewService
from .startup_admission import admit_at_startup


@asynccontextmanager
async def open_link_coordinator(*, issuance_binding, review_binding,
        coordinator_database_url, echo_proof_database_url, victoria_proof_database_url,
        issuance_journal_path, confirmation_journal_path, commitment_key,
        confirmation_key, issuance_journal_key, confirmation_journal_key,
        commitment_key_id, confirmation_key_id, admission):
    if (type(issuance_binding) is not LinkIssuanceBinding or
            type(review_binding) is not LinkReviewBinding or
            issuance_binding.credential == review_binding.credential or
            not iscoroutinefunction(admission)):
        raise ValueError("dedicated coordinator bindings and admission required")
    keys = (commitment_key, confirmation_key, issuance_journal_key, confirmation_journal_key)
    if any(type(key) is not bytes or len(key) != 32 for key in keys) or len(set(keys)) != 4:
        raise ValueError("distinct recoverable coordinator keys required")
    paths = (issuance_journal_path, confirmation_journal_path)
    if (any(not isinstance(path, Path) or not path.is_absolute() or path.is_symlink() for path in paths)
            or paths[0].resolve() == paths[1].resolve()):
        raise ValueError("distinct durable coordinator journals required")
    urls = [make_url(raw) for raw in (coordinator_database_url, echo_proof_database_url, victoria_proof_database_url)]
    if len({(url.host, url.port or 5432, url.database) for url in urls}) != 1:
        raise ValueError("coordinator adapters must share the admitted database")
    commitments = SharedLinkCommitments(commitment_key, key_id=commitment_key_id)
    preparer = SharedLinkConfirmationPreparer(confirmation_key, key_id=confirmation_key_id)
    async with AsyncExitStack() as cleanup:
        # Establish admission before creating private journal files.
        await admit_at_startup(admission)
        issuance = SharedLinkIssuanceDatabase(coordinator_database_url, commitments=commitments)
        cleanup.push_async_callback(issuance.close)
        confirmation = SharedLinkConfirmationDatabase(coordinator_database_url)
        cleanup.push_async_callback(confirmation.close)
        proofs = []
        for site, url in (("echo", echo_proof_database_url), ("victoria", victoria_proof_database_url)):
            proof = SharedLinkProofDatabase(url, issuer_id=f"home-assistant:{site}")
            cleanup.push_async_callback(proof.close)
            proofs.append(proof)
        journal = SharedLinkJournal(paths[0], cipher=FieldCipher(issuance_journal_key))
        cleanup.callback(journal.close)
        reviews = SharedLinkConfirmationJournal(paths[1], cipher=FieldCipher(confirmation_journal_key))
        cleanup.callback(reviews.close)
        issue_service = SharedLinkIssuanceService(journal=journal, database=issuance)
        review_service = SharedLinkReviewService(journal=reviews, preparer=preparer,
                                                 issuance=issuance, confirmation=confirmation)
        assembly = SharedLinkReviewAssembly(issuance=issue_service, review=review_service,
                                            echo_proofs=proofs[0], victoria_proofs=proofs[1])
        issue_app = create_link_issuance_ingress(binding=issuance_binding, service=issue_service)
        review_app = create_link_review_ingress(binding=review_binding, service=review_service, assembly=assembly)
        app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
        credentials = {}
        for child, binding in ((issue_app, issuance_binding), (review_app, review_binding)):
            for route in child.routes:
                credentials[route.path] = binding.credential
            app.router.routes.extend(child.routes)
        active = 0
        available = True

        @app.middleware("http")
        async def guard(request, call_next):
            nonlocal active
            expected = credentials.get(request.url.path)
            if expected is None:
                return await call_next(request)
            def unavailable(code, status=503):
                return JSONResponse({"error": code}, status_code=status, headers={"Cache-Control": "no-store"})
            if not available:
                return unavailable("coordinator_unavailable")
            authorization = request.headers.getlist("authorization")
            if (request.scope.get("scheme") != "https" or "origin" in request.headers or
                    "cookie" in request.headers or len(authorization) != 1 or
                    not hmac.compare_digest(authorization[0].encode("latin-1"), b"Bearer " + expected)):
                return await call_next(request)
            if active >= 2:
                return unavailable("coordinator_busy", 429)
            active += 1
            try:
                try:
                    async with asyncio.timeout(10):
                        await admission()
                except Exception:
                    return unavailable("coordinator_admission_unavailable")
                return await call_next(request)
            finally:
                active -= 1

        try:
            yield app
        finally:
            available = False
