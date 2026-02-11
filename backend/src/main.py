from typing import List, Optional

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.db.session import get_db
from infrastructure.db.repositories import UserRepository, BookRepository
from infrastructure.services.book_service import LibraryService
from infrastructure.services.image_service import image_service
from domain.schemas import (
    BookCreate, BookRead, BookUpdate, ReservationRequest,
    ApproveRequest, RejectRequest, BookHistoryRead
)
from domain.domain_models import BookStatus

app = FastAPI(title="Library Bot API v2.0 (Refactored)")


# Helper to get service
def get_service(db: AsyncSession = Depends(get_db)) -> LibraryService:
    return LibraryService(db)


# --- USERS ---
@app.post("/users/auth")
async def auth_user(tg_id: int, full_name: str, username: str = None, db: AsyncSession = Depends(get_db)):
    repo = UserRepository(db)
    return await repo.get_or_create_user(tg_id, full_name, username)


# --- CATALOG ---
@app.get("/books", response_model=List[BookRead])
async def search_books(
        title: Optional[str] = None,
        genre: Optional[str] = None,
        status: Optional[BookStatus] = None,
        db: AsyncSession = Depends(get_db)
):
    repo = BookRepository(db)
    # ТЗ 3.1: Интерактивное меню с фильтрами, полнотекстовый поиск
    return await repo.search_books(title, genre, status)


@app.get("/books/my/{user_id}", response_model=List[BookRead])
async def get_my_books(user_id: int, db: AsyncSession = Depends(get_db)):
    # ТЗ 3.3.1: "Мои книги"
    repo = BookRepository(db)
    return await repo.get_my_books(user_id)


@app.get("/books/{book_id}", response_model=BookRead)
async def get_book_detail(book_id: int, db: AsyncSession = Depends(get_db)):
    repo = BookRepository(db)
    book = await repo.get_book_by_id(book_id)
    if not book or book.is_deleted:
        raise HTTPException(status_code=404, detail="Book not found")
    return book


@app.get("/books/{book_id}/history", response_model=List[BookHistoryRead])
async def get_book_history(book_id: int, db: AsyncSession = Depends(get_db)):
    # ТЗ 4.4: Журнал истории
    repo = BookRepository(db)
    return await repo.get_history(book_id)


# --- WIZARD & MANAGEMENT ---
@app.post("/books", response_model=BookRead, status_code=201)
async def create_book_endpoint(book_in: BookCreate, service: LibraryService = Depends(get_service)):
    # ТЗ 1: Итог визарда
    book_id = await service.create_book(book_in)
    repo = BookRepository(service.db)
    return await repo.get_book_by_id(book_id)


@app.patch("/books/{book_id}", response_model=BookRead)
async def edit_book_endpoint(book_id: int, update: BookUpdate, user_id: int = Query(...),
                             service: LibraryService = Depends(get_service)):
    # ТЗ 4.1: Редактирование
    return await service.edit_book(book_id, user_id, update)


@app.delete("/books/{book_id}")
async def delete_book_endpoint(book_id: int, user_id: int = Query(...), service: LibraryService = Depends(get_service)):
    await service.delete_book(book_id, user_id)
    return {"status": "deleted"}


@app.post("/media/upload")
async def upload_media(file: UploadFile = File(...)):
    # ТЗ 1 (Шаг 3): Загрузка фото
    path = await image_service.process_and_save(file)
    return {"path": path}


# --- FLOW: RESERVATION & RETURN ---

@app.post("/borrowings/request")
async def request_reservation(payload: ReservationRequest, book_id: int = Query(...),
                              service: LibraryService = Depends(get_service)):
    # ТЗ 3.2.2: Запрос на бронь
    return await service.request_reservation(book_id, payload.user_id, payload.days)


@app.post("/borrowings/approve")
async def approve_reservation(payload: ApproveRequest, book_id: int = Query(...),
                              service: LibraryService = Depends(get_service)):
    # ТЗ 3.2.3: Админ подтверждает
    return await service.approve_reservation(book_id, payload.admin_id, payload.due_date)


@app.post("/borrowings/reject")
async def reject_reservation(payload: RejectRequest, book_id: int = Query(...),
                             service: LibraryService = Depends(get_service)):
    # ТЗ 3.2.3: Админ отклоняет
    await service.reject_reservation(book_id, payload.admin_id, payload.reason)
    return {"status": "rejected"}


@app.post("/borrowings/return")
async def return_book_endpoint(
        book_id: int,
        user_id: int = Query(...),
        is_admin: bool = Query(False),
        photo: Optional[UploadFile] = File(None),
        service: LibraryService = Depends(get_service)
):
    # ТЗ 3.3: Возврат
    return await service.return_book(book_id, user_id, is_admin, photo)


@app.post("/books/{book_id}/waitlist")
async def join_waitlist(book_id: int, user_id: int, db: AsyncSession = Depends(get_db)):
    # ТЗ 3.2: Кнопка "Уведомить меня"
    repo = BookRepository(db)
    book = await repo.get_book_by_id(book_id)
    if not book or book.status == BookStatus.AVAILABLE:
        raise HTTPException(status_code=400, detail="Книга доступна, можно брать")

    await repo.add_to_waitlist(book_id, user_id)
    return {"message": "Вы добавлены в лист ожидания"}