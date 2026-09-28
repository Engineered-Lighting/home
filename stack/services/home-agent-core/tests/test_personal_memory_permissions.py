import pytest
from app.personal_memory_permissions import apply_permissions


class Connection:
    def __init__(self,state): self.state=state;self.calls=[]
    def execute(self,statement): self.calls.append(str(statement));return self
    def one(self): return self.state


@pytest.mark.parametrize("state",[
    ("postgres","postgres","serializable",["0047_personal_pref_authority_v1"]),
    ("home_agent_api","home_agent_api","serializable",["0047_personal_pref_authority_v1"]),
    ("home_agent_owner","home_agent_owner","read committed",["0047_personal_pref_authority_v1"]),
    ("home_agent_owner","home_agent_owner","serializable",["0031_relationship_uniqueness_e5r"]),
])
def test_activation_rejects_wrong_operator_or_transaction_before_any_grant(state):
    connection=Connection(state)
    with pytest.raises(ValueError,match="exact migrated owner transaction"):
        apply_permissions(connection,enabled=True)
    assert len(connection.calls)==1
    assert connection.calls[0].startswith("SELECT ")
