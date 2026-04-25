"""Abstract base class for LLM clients."""

from abc import ABC, abstractmethod


class BaseLLMClient(ABC):
    """Abstract interface for language model clients."""

    @abstractmethod
    def complete(self, prompt: str, max_tokens: int = 2048) -> str:
        """Send a prompt and return the model response text.

        Returns empty string on failure or when API key is absent.
        """
