from datetime import UTC, datetime, timedelta

import pytest
from pydantic import BaseModel, ValidationError, field_validator

from app.database.models import BookingStatus, Role


class BookingInput(BaseModel):
    starts_at: datetime
    duration_minutes: int

    @field_validator("starts_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timezone required")
        return value

    @field_validator("duration_minutes")
    @classmethod
    def require_positive_duration(cls, value: int) -> int:
        if value < 1:
            raise ValueError("duration must be positive")
        return value


def test_booking_input_accepts_timezone_and_positive_duration(valid_duration: int) -> None:
    data = BookingInput(starts_at=datetime.now(UTC), duration_minutes=valid_duration)
    assert data.starts_at.tzinfo is not None
    assert data.starts_at + timedelta(minutes=data.duration_minutes) > data.starts_at


def test_booking_input_rejects_naive_time() -> None:
    with pytest.raises(ValidationError):
        BookingInput(starts_at=datetime(2030, 1, 1, 10), duration_minutes=30)


def test_booking_input_rejects_invalid_duration() -> None:
    with pytest.raises(ValidationError):
        BookingInput(starts_at=datetime.now(UTC), duration_minutes=0)


def test_roles_and_statuses_are_explicit() -> None:
    assert {role.value for role in Role} == {"client", "specialist", "admin"}
    assert BookingStatus.CONFIRMED.value == "confirmed"
    assert BookingStatus.CANCELLED.value == "cancelled"


@pytest.mark.asyncio
async def test_cancel_booking_rejects_another_clients_booking() -> None:
    from unittest.mock import AsyncMock
    from uuid import uuid4

    from app.core.exceptions import AccessDenied
    from app.database.models import Booking, BookingStatus
    from app.services.booking_service import BookingService

    owner_id, other_id, booking_id = uuid4(), uuid4(), uuid4()
    booking = Booking(
        id=booking_id,
        client_id=owner_id,
        specialist_id=uuid4(),
        service_id=uuid4(),
        starts_at=datetime.now(UTC),
        ends_at=datetime.now(UTC) + timedelta(minutes=30),
        status=BookingStatus.CONFIRMED,
    )
    session = AsyncMock()
    session.get.return_value = booking
    with pytest.raises(AccessDenied):
        await BookingService().cancel_booking(session, booking_id, other_id)
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_booking_changes_status_for_owner() -> None:
    from unittest.mock import AsyncMock
    from uuid import uuid4

    from app.database.models import Booking, BookingStatus
    from app.services.booking_service import BookingService

    owner_id, booking_id = uuid4(), uuid4()
    booking = Booking(
        id=booking_id,
        client_id=owner_id,
        specialist_id=uuid4(),
        service_id=uuid4(),
        starts_at=datetime.now(UTC),
        ends_at=datetime.now(UTC) + timedelta(minutes=30),
        status=BookingStatus.CONFIRMED,
    )
    session = AsyncMock()
    session.get.return_value = booking
    result = await BookingService().cancel_booking(session, booking_id, owner_id)
    assert result.status == BookingStatus.CANCELLED
    session.commit.assert_awaited_once()
