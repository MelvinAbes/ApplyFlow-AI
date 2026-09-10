from typing import Generic, TypeVar

from sqlmodel import Session, SQLModel

ModelT = TypeVar("ModelT", bound=SQLModel)


class Repository(Generic[ModelT]):
    def __init__(self, session: Session, model: type[ModelT]):
        self.session = session
        self.model = model

    def get(self, entity_id: str) -> ModelT | None:
        return self.session.get(self.model, entity_id)

    def add(self, entity: ModelT) -> ModelT:
        self.session.add(entity)
        self.session.commit()
        self.session.refresh(entity)
        return entity
