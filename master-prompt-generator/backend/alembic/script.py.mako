"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op
from sqlalchemy import Text
from sqlalchemy.dialects import postgresql

# Text and postgresql are imported unconditionally because autogenerate renders
# the models' JSON().with_variant(JSONB, "postgresql") columns as
# postgresql.JSONB(astext_type=Text()). Without them a generated revision
# raises NameError partway through, having already created some tables.

revision: str = ${repr(up_revision)}
down_revision: Union[str, None] = ${repr(down_revision)}
branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels)}
depends_on: Union[str, Sequence[str], None] = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
