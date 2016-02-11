"""source_json[urls] made list

Revision ID: 52e3bc0e7efe
Revises: 1f7de2a50abf
Create Date: 2016-02-09 13:43:51.244439

"""

# revision identifiers, used by Alembic.
revision = '52e3bc0e7efe'
down_revision = '1f7de2a50abf'

from alembic import op
import sqlalchemy as sa
import json


def upgrade():
    build_table = sa.Table(
        'build',
        sa.MetaData(),
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('source_json', sa.Text()),
        sa.Column('source_type', sa.Integer()),
    )

    bind = op.get_bind()
    connection = bind.connect()

    for build in connection.execute(build_table.select().where(build_table.c.source_type == 1)):
        source_json = json.loads(build.source_json)
        source_json['urls'] = [source_json['url']]
        source_json.pop('url')
        source_json_str = json.dumps(source_json)
        connection.execute( # is there a better way to do the update directly from the build object
            build_table.update().where(build_table.c.id == build.id).values(
                source_json = source_json_str,
            )
        )


def downgrade():
    build_table = sa.Table(
        'build',
        sa.MetaData(),
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('source_json', sa.Text()),
        sa.Column('source_type', sa.Integer()),
    )

    bind = op.get_bind()
    connection = bind.connect()

    for build in connection.execute(build_table.select().where(build_table.c.source_type == 1)):
        source_json = json.loads(build.source_json)
        source_json['url'] = source_json['urls'][0]
        source_json.pop('urls')
        source_json_str = json.dumps(source_json)
        connection.execute( # is there a better way to do the update directly from the build object
            build_table.update().where(build_table.c.id == build.id).values(
                source_json = source_json_str,
            )
        )
