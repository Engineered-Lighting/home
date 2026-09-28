import pytest

from app.personal_memory_storage import restore_preference_record
from .test_personal_memory_contract import fixture


def test_stored_review_request_and_authority_round_trip_without_relaxing_types():
    _,request,authority,_,review,_ = fixture()
    for value in (request,authority,review):
        assert restore_preference_record(type(value),value.model_dump(mode="json")) == value


@pytest.mark.parametrize("timestamp", ["2026-09-28", "2026-09-28T00:00:00", 42, None])
def test_retained_review_requires_aware_timestamp(timestamp):
    _,_,_,_,review,_ = fixture()
    with pytest.raises(ValueError):
        restore_preference_record(type(review),{**review.model_dump(mode="json"),"expires_at":timestamp})


def test_retained_record_still_rejects_extra_authority_and_wrong_version():
    _,request,_,_,_,_ = fixture()
    for patch in ({"confirmed":True},{"version":True},{"operation_id":"not-an-id"}):
        with pytest.raises(ValueError):
            restore_preference_record(type(request),{**request.model_dump(mode="json"),**patch})
