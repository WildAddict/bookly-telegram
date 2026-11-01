"""Move pending booking reminders to one hour before the appointment."""

from alembic import op

revision = "0003_reminders_one_hour_before"
down_revision = "0002_telegram_ids_bigint"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        UPDATE notifications AS notification
        SET due_at = GREATEST(booking.starts_at - INTERVAL '1 hour', now())
        FROM bookings AS booking
        WHERE notification.booking_id = booking.id
          AND notification.kind = 'reminder'
          AND notification.sent_at IS NULL
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE notifications AS notification
        SET due_at = GREATEST(booking.starts_at - INTERVAL '24 hours', now())
        FROM bookings AS booking
        WHERE notification.booking_id = booking.id
          AND notification.kind = 'reminder'
          AND notification.sent_at IS NULL
    """)
