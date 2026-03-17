from pydantic import BaseModel
from typing import Any


class FailedRow(BaseModel):
    row_number: int
    error: str


class FacultyImportResponse(BaseModel):
    created_count: int
    failed_count: int
    activation_email_sent_count: int = 0
    failed_rows: list[FailedRow] = []
    created_faculty: list[Any] = []