"""Compact UUID4 identifiers compatible with Getnet's 24-character ticket limit."""

import uuid


def new_ticket():
    # Match the ten-character length of the working manual ticket while the
    # firmware's rejection of the longer ticket is investigated.
    # This is a UUID4-derived identifier, not a reversible/full UUID encoding.
    # Journal collision checks remain necessary for this shortened identifier.
    return str(uuid.uuid4().hex[:10].upper())
