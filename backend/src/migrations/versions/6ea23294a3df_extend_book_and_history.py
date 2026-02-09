"""extend_book_and_history

Revision ID: 6ea23294a3df
Revises: f8fc3947edbe
Create Date: 2026-02-09 12:59:09.937745
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '6ea23294a3df'
down_revision: Union[str, None] = 'f8fc3947edbe'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # Создаём ENUM только если его нет
    bookstatus_enum = postgresql.ENUM(
        'AVAILABLE', 'RESERVED', 'BORROWED', 'OVERDUE',
        name='bookstatus'
    )
    bookstatus_enum.create(conn, checkfirst=True)

    # Создаём таблицу истории книг
    op.create_table(
        'book_history',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('book_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('status', sa.Enum(
            'AVAILABLE', 'RESERVED', 'BORROWED', 'OVERDUE',
            name='bookstatus',
            create_type=False  # тип уже создан
        ), nullable=False),
        sa.Column('comment', sa.String(length=255), nullable=True),
        sa.Column('timestamp', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['book_id'], ['books.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
    )

    # Дополнительные колонки для таблицы books
    op.add_column('books', sa.Column('genre', sa.String(length=100), nullable=True))
    op.add_column('books', sa.Column('description', sa.Text(), nullable=True))
    op.add_column('books', sa.Column('borrower_id', sa.Integer(), nullable=True))
    op.add_column('books', sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False))
    op.add_column('books', sa.Column('return_due_date', sa.DateTime(), nullable=True))
    op.alter_column('books', 'owner_id',
               existing_type=sa.BIGINT(),
               type_=sa.Integer(),
               existing_nullable=False)
    op.create_index(op.f('ix_books_author'), 'books', ['author'], unique=False)
    op.create_index(op.f('ix_books_genre'), 'books', ['genre'], unique=False)
    op.create_index(op.f('ix_books_status'), 'books', ['status'], unique=False)
    op.create_foreign_key(None, 'books', 'users', ['borrower_id'], ['id'])

    # Обновление таблицы users
    op.add_column('users', sa.Column('name', sa.String(length=128), nullable=False))
    op.alter_column('users', 'id',
               existing_type=sa.BIGINT(),
               type_=sa.Integer(),
               existing_nullable=False,
               autoincrement=True)
    op.drop_column('users', 'username')
    op.drop_column('users', 'full_name')


def downgrade() -> None:
    # Возврат изменений
    op.add_column('users', sa.Column('full_name', sa.VARCHAR(length=255), autoincrement=False, nullable=False))
    op.add_column('users', sa.Column('username', sa.VARCHAR(length=32), autoincrement=False, nullable=True))
    op.alter_column('users', 'id',
               existing_type=sa.Integer(),
               type_=sa.BIGINT(),
               existing_nullable=False,
               autoincrement=True)
    op.drop_column('users', 'name')
    op.drop_constraint(None, 'books', type_='foreignkey')
    op.drop_index(op.f('ix_books_status'), table_name='books')
    op.drop_index(op.f('ix_books_genre'), table_name='books')
    op.drop_index(op.f('ix_books_author'), table_name='books')
    op.alter_column('books', 'owner_id',
               existing_type=sa.Integer(),
               type_=sa.BIGINT(),
               existing_nullable=False)
    op.drop_column('books', 'return_due_date')
    op.drop_column('books', 'created_at')
    op.drop_column('books', 'borrower_id')
    op.drop_column('books', 'description')
    op.drop_column('books', 'genre')
    op.drop_table('book_history')

    # Удаляем ENUM только если он есть
    bookstatus_enum = postgresql.ENUM(
        'AVAILABLE', 'RESERVED', 'BORROWED', 'OVERDUE',
        name='bookstatus'
    )
    bookstatus_enum.drop(op.get_bind(), checkfirst=True)
