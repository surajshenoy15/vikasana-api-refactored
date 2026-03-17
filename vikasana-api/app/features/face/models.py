import json
from sqlalchemy import Column, Integer, DateTime, ForeignKey, Text, func
from sqlalchemy.orm import relationship

from app.core.database import Base


class StudentFaceEmbedding(Base):
    __tablename__ = "student_face_embeddings"

    id = Column(Integer, primary_key=True, index=True)

    student_id = Column(
        Integer,
        ForeignKey("students.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,  # one averaged embedding per student
        index=True,
    )

    embedding = Column(Text, nullable=False)  # stored as JSON string
    photo_count = Column(Integer, default=0)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    student = relationship("Student", back_populates="face_embeddings")

    def get_embedding(self) -> list:
        return json.loads(self.embedding)

    def set_embedding(self, emb: list):
        self.embedding = json.dumps(emb)