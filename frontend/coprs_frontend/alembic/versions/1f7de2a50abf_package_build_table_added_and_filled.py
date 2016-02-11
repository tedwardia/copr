"""Package<->Build relationship made m:n (from 1:n)

Revision ID: 1f7de2a50abf
Revises: 573044986ee9
Create Date: 2016-02-08 03:17:40.374429

"""

# revision identifiers, used by Alembic.
revision = '1f7de2a50abf'
down_revision = '573044986ee9'

from alembic import op
import sqlalchemy as sa

def upgrade():
    op.create_table(
        'package_build',
        sa.Column('id', sa.Integer(), nullable=False, primary_key=True),
        sa.Column('package_id', sa.Integer(), sa.ForeignKey('package.id', ondelete='CASCADE'), nullable=False),
        sa.Column('build_id', sa.Integer(), sa.ForeignKey('build.id', ondelete='CASCADE'), nullable=False),
        sa.Column('version', sa.Text()),
        sa.Column('git_hash', sa.Text()),
    )

    package_build_table = sa.Table(
        'package_build',
        sa.MetaData(),
        sa.Column('id', sa.Integer(), nullable=False, primary_key=True),
        sa.Column('package_id', sa.Integer(), sa.ForeignKey('package.id', ondelete='CASCADE'), nullable=False),
        sa.Column('build_id', sa.Integer(), sa.ForeignKey('build.id', ondelete='CASCADE'), nullable=False),
        sa.Column('version', sa.Text()),
        sa.Column('git_hash', sa.Text()),
    )

    build_table = sa.Table(
        'build',
        sa.MetaData(),
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('package_id', sa.Integer()),
        sa.Column('pkg_version', sa.Text()),
    )

    build_table = sa.Table(
        'build',
        sa.MetaData(),
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('package_id', sa.Integer()),
        sa.Column('pkg_version', sa.Text()),
    )

    build_chroot_table = sa.Table(
        'build_chroot',
        sa.MetaData(),
        sa.Column('build_id', sa.Integer(), nullable=False),
        sa.Column('git_hash', sa.Text()),
    )

    bind = op.get_bind()
    connection = bind.connect()

    for build in connection.execute(build_table.select().where(build_table.c.package_id != None)):
        build_chroot = connection.execute(
            build_chroot_table.select().where(build_chroot_table.c.build_id == build.id)
        ).first()
        connection.execute(
            package_build_table.insert().values(
                package_id = build.package_id,
                build_id = build.id,
                version = build.pkg_version,
                git_hash = build_chroot.git_hash
            )
        )

    op.drop_column('build', 'package_id')
    op.drop_column('build', 'pkg_version')
    op.drop_column('build_chroot', 'git_hash')


def downgrade():
    op.add_column('build', sa.Column('package_id', sa.Integer(), sa.ForeignKey('package.id')))
    op.add_column('build', sa.Column('pkg_version', sa.Text()))
    op.add_column('build_chroot', sa.Column('git_hash', sa.Text()))

    package_build_table = sa.Table(
        'package_build',
        sa.MetaData(),
        sa.Column('id', sa.Integer(), nullable=False, primary_key=True),
        sa.Column('package_id', sa.Integer(), sa.ForeignKey('package.id', ondelete='CASCADE'), nullable=False),
        sa.Column('build_id', sa.Integer(), sa.ForeignKey('build.id', ondelete='CASCADE'), nullable=False),
        sa.Column('version', sa.Text()),
        sa.Column('git_hash', sa.Text()),
    )

    build_table = sa.Table(
        'build',
        sa.MetaData(),
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('package_id', sa.Integer()),
        sa.Column('pkg_version', sa.Text()),
    )

    build_chroot_table = sa.Table(
        'build_chroot',
        sa.MetaData(),
        sa.Column('build_id', sa.Integer(), nullable=False),
        sa.Column('git_hash', sa.Text()),
    )

    bind = op.get_bind()
    connection = bind.connect()

    for package_build in connection.execute(
            sa.select([
                package_build_table.c.build_id,
                sa.func.max(package_build_table.c.package_id),
                sa.func.max(package_build_table.c.version),
                sa.func.max(package_build_table.c.git_hash)]
            ).group_by(package_build_table.c.build_id)):
        connection.execute(
            build_table.update().where(build_table.c.id == package_build[0]).values(
                package_id = package_build[1],
                pkg_version = package_build[2],
            )
        )
        connection.execute(
            build_chroot_table.update().where(build_chroot_table.c.build_id == package_build[0]).values(
                git_hash = package_build[3],
            )
        )

    op.drop_table('package_build')
