from datetime import datetime

from pydantic import BaseModel, Field


class TutorRegisterRequest(BaseModel):
    display_name: str = Field(alias="displayName")
    bio: str | None = None
    subjects: list[str] = Field(default_factory=list)
    hourly_rate: float = Field(default=0, alias="hourlyRate")
    currency: str = "NGN"
    visibility: str = "school"
    is_external: bool = Field(default=False, alias="isExternal")

    model_config = {"populate_by_name": True}


class TutorUpdateRequest(BaseModel):
    display_name: str | None = Field(default=None, alias="displayName")
    bio: str | None = None
    subjects: list[str] | None = None
    hourly_rate: float | None = Field(default=None, alias="hourlyRate")
    visibility: str | None = None

    model_config = {"populate_by_name": True}


class TutorProfileResponse(BaseModel):
    tutor_id: str = Field(alias="tutorId")
    user_id: str = Field(alias="userId")
    display_name: str = Field(alias="displayName")
    bio: str | None = None
    subjects: list[str] = Field(default_factory=list)
    hourly_rate: float = Field(alias="hourlyRate")
    currency: str
    visibility: str
    is_school_teacher: bool = Field(alias="isSchoolTeacher")
    is_external: bool = Field(alias="isExternal")
    status: str
    rating_avg: float = Field(alias="ratingAvg")
    session_count: int = Field(alias="sessionCount")

    model_config = {"populate_by_name": True}


class TutorSearchResponse(BaseModel):
    items: list[TutorProfileResponse] = Field(default_factory=list)
    total: int = 0


class BookingCreateRequest(BaseModel):
    tutor_id: str = Field(alias="tutorId")
    student_id: str | None = Field(default=None, alias="studentId")
    parent_id: str | None = Field(default=None, alias="parentId")
    external_guest_name: str | None = Field(default=None, alias="externalGuestName")
    subject: str
    topic: str | None = None
    scheduled_at: datetime | None = Field(default=None, alias="scheduledAt")
    duration_minutes: int = Field(default=60, alias="durationMinutes")
    include_ai_prep: bool = Field(default=True, alias="includeAiPrep")
    notes: str | None = None

    model_config = {"populate_by_name": True}


class BookingResponse(BaseModel):
    booking_id: str = Field(alias="bookingId")
    tutor_id: str = Field(alias="tutorId")
    status: str
    subject: str
    topic: str | None = None
    scheduled_at: datetime | None = Field(default=None, alias="scheduledAt")
    include_ai_prep: bool = Field(alias="includeAiPrep")
    payment_id: str | None = Field(default=None, alias="paymentId")

    model_config = {"populate_by_name": True}


class BookingListItem(BookingResponse):
    tutor_name: str = Field(alias="tutorName")
    student_id: str | None = Field(default=None, alias="studentId")
    payment_status: str | None = Field(default=None, alias="paymentStatus")
    amount: float | None = None
    currency: str | None = None
    ai_prep_summary: str | None = Field(default=None, alias="aiPrepSummary")

    model_config = {"populate_by_name": True}


class BookingListResponse(BaseModel):
    items: list[BookingListItem] = Field(default_factory=list)
    total: int = 0


class PaymentIntentRequest(BaseModel):
    booking_id: str = Field(alias="bookingId")

    model_config = {"populate_by_name": True}


class PaymentIntentResponse(BaseModel):
    payment_id: str = Field(alias="paymentId")
    amount: float
    platform_fee: float = Field(alias="platformFee")
    tutor_payout: float = Field(alias="tutorPayout")
    currency: str
    status: str
    provider_ref: str | None = Field(default=None, alias="providerRef")

    model_config = {"populate_by_name": True}


class AiPrepResponse(BaseModel):
    session_id: str = Field(alias="sessionId")
    ai_session_id: str | None = Field(default=None, alias="aiSessionId")
    prep_summary: str = Field(alias="prepSummary")
    booking_id: str = Field(alias="bookingId")

    model_config = {"populate_by_name": True}
