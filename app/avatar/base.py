from abc import ABC, abstractmethod


class AvatarSessionProvider(ABC):
    @abstractmethod
    async def get_session(self) -> dict: ...
