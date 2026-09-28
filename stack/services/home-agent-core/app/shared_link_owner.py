"""Internal owner lookup result; never an authentication or authorization grant."""
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SharedLinkOwnerAnchor(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)
    principal_id: UUID = Field(repr=False)
    person_id: UUID = Field(repr=False)
    legacy_binding_id: UUID = Field(repr=False)
