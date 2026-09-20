from pydantic import BaseModel, Field


class IngestDocumentRequest(BaseModel):
    source_type: str = Field(alias="sourceType")
    source_id: str = Field(alias="sourceId")
    title: str | None = None
    text: str
    visibility: str = "staff"
    school_level: str | None = Field(default=None, alias="schoolLevel")
    subject_id: str | None = Field(default=None, alias="subjectId")
    class_id: str | None = Field(default=None, alias="classId")

    model_config = {"populate_by_name": True}


class IngestDocumentResponse(BaseModel):
    document_id: str = Field(alias="documentId")
    chunk_count: int = Field(alias="chunkCount")
    status: str

    model_config = {"populate_by_name": True}
