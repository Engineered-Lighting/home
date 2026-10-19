"""Local publication outcomes: a refusal is different from an unknown write.

Only a bounded, authenticated journal response establishes a definitive refusal.
Socket loss, an invalid response and a lost ACK retain exact retry state.
"""


class PublicationRefusal(ValueError):
    def __init__(self, reason, *, http_status=None):
        super().__init__(reason)
        self.reason = reason
        self.terminal = True
        self.http_status = http_status
