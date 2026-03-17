from pydantic import BaseModel
from datetime import datetime
from typing import Optional


class CertificateStudentOut(BaseModel):
    name: Optional[str]
    usn: Optional[str]
    college: Optional[str]
    branch: Optional[str]


class CertificateEventOut(BaseModel):
    id: int
    name: Optional[str]


class CertificateVerifyOut(BaseModel):
    valid: bool
    certificate_no: Optional[str] = None
    issued_at: Optional[datetime] = None
    student: Optional[CertificateStudentOut] = None
    event: Optional[CertificateEventOut] = None
    reason: Optional[str] = None

class StudentCertificateOut(BaseModel):
    id: int
    certificate_no: str
    issued_at: datetime
    event_id: int
    submission_id: int
    activity_type_id: Optional[int] = None
    pdf_url: Optional[str] = None

    class Config:
        from_attributes = True