from enum import Enum


class BookStatus(str, Enum):
    AVAILABLE = "available"   # 🟢 Свободна
    RESERVED  = "reserved"    # 🟡 Забронирована (ждёт подтверждения админа)
    BORROWED  = "borrowed"    # 🔴 Выдана
    OVERDUE   = "overdue"     # ⏳ Просрочена (ставится фоновой задачей)
    DELETED   = "deleted"     # 🗑️  Удалена (soft-delete)


class NotificationType(str, Enum):
    BOOK_RETURNED             = "book_returned"
    WAITLIST_AVAILABLE        = "waitlist_available"
    RESERVATION_APPROVED      = "reservation_approved"
    RESERVATION_REJECTED      = "reservation_rejected"
    OVERDUE                   = "overdue"
    NEW_BOOK                  = "new_book"
    BOOK_DELETED              = "book_deleted"
    DUE_DATE_REMINDER         = "due_date_reminder"
    ADMIN_RESERVATION_REQUEST = "admin_reservation_request"
