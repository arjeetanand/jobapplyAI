"""OCI Generative AI provider.

Supports two serving modes:
- Cohere (CohereChatRequest)  — default when OCI_GENAI_MODEL_ID points to a Cohere model
- OpenAI-compatible (GenericChatRequest) — used when OCI_GENAI_MODEL_ID contains "gpt" or "openai"

Falls back gracefully: if OCI is unconfigured or the call fails, callers receive a RuntimeError
that the resume tailoring service catches and handles with its template fallback.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Settings, get_settings


@dataclass
class OCIStatus:
    configured: bool
    message: str


class OCIGenerativeAIProvider:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def status(self) -> OCIStatus:
        missing = []
        if not self.settings.oci_compartment_ocid:
            missing.append("OCI_COMPARTMENT_OCID")
        if not (self.settings.oci_genai_model_id or self.settings.oci_genai_endpoint_id):
            missing.append("OCI_GENAI_MODEL_ID or OCI_GENAI_ENDPOINT_ID")
        if missing:
            return OCIStatus(False, f"Missing OCI settings: {', '.join(missing)}")
        try:
            import oci  # noqa: F401
        except Exception as exc:
            return OCIStatus(False, f"OCI SDK unavailable: {exc}")
        return OCIStatus(True, "OCI Generative AI provider is configured.")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _is_openai_model(self) -> bool:
        model_id = (self.settings.oci_genai_model_id or "").lower()
        return "gpt" in model_id or "openai" in model_id

    def _build_client(self, oci):  # type: ignore[valid-type]
        from oci.generative_ai_inference import GenerativeAiInferenceClient

        config = oci.config.from_file(self.settings.oci_config_file, self.settings.oci_profile)
        region = self.settings.oci_region or config.get("region")
        endpoint = f"https://inference.generativeai.{region}.oci.oraclecloud.com" if region else None
        client_kwargs = {"service_endpoint": endpoint} if endpoint else {}
        return GenerativeAiInferenceClient(config, **client_kwargs), config

    def _serving_mode(self, oci_models):  # type: ignore[valid-type]
        if self.settings.oci_genai_endpoint_id:
            return oci_models.DedicatedServingMode(
                serving_type="DEDICATED",
                endpoint_id=self.settings.oci_genai_endpoint_id,
            )
        return oci_models.OnDemandServingMode(
            serving_type="ON_DEMAND",
            model_id=self.settings.oci_genai_model_id,
        )

    # ------------------------------------------------------------------
    # Public chat interface
    # ------------------------------------------------------------------

    def chat(self, prompt: str) -> str:
        status = self.status()
        if not status.configured:
            raise RuntimeError(status.message)

        if self._is_openai_model():
            return self._chat_openai_compatible(prompt)
        return self._chat_cohere(prompt)

    def _chat_cohere(self, prompt: str) -> str:
        import oci
        from oci.generative_ai_inference import models

        client, _ = self._build_client(oci)
        serving_mode = self._serving_mode(models)
        request = models.CohereChatRequest(
            api_format="COHERE",
            message=prompt,
            is_stream=False,
            temperature=0.2,
            max_tokens=2000,
        )
        details = models.ChatDetails(
            compartment_id=self.settings.oci_compartment_ocid,
            serving_mode=serving_mode,
            chat_request=request,
        )
        response = client.chat(details)
        chat_response = response.data.chat_response
        if getattr(chat_response, "text", None):
            return chat_response.text
        choices = getattr(chat_response, "choices", None) or []
        if choices and getattr(choices[0], "message", None):
            message = choices[0].message
            content = getattr(message, "content", None)
            if isinstance(content, list) and content:
                return getattr(content[0], "text", str(content[0]))
            return str(message)
        raise RuntimeError("OCI Cohere response did not include text content.")

    def _chat_openai_compatible(self, prompt: str) -> str:
        """Use OCI's OpenAI-compatible endpoint (e.g. openai.gpt-4o hosted on OCI)."""
        import oci
        from oci.generative_ai_inference import models

        client, _ = self._build_client(oci)
        serving_mode = self._serving_mode(models)

        # GenericChatRequest wraps OpenAI-style messages
        messages = [models.GenericChatUserMessage(role="USER", content=[models.ChatContent(type="TEXT", text=prompt)])]
        request = models.GenericChatRequest(
            api_format="GENERIC",
            messages=messages,
            is_stream=False,
            temperature=0.2,
            max_tokens=2000,
        )
        details = models.ChatDetails(
            compartment_id=self.settings.oci_compartment_ocid,
            serving_mode=serving_mode,
            chat_request=request,
        )
        response = client.chat(details)
        chat_response = response.data.chat_response
        choices = getattr(chat_response, "choices", None) or []
        if choices:
            message = getattr(choices[0], "message", None)
            if message:
                content = getattr(message, "content", None)
                if isinstance(content, list) and content:
                    return getattr(content[0], "text", str(content[0]))
                if isinstance(content, str):
                    return content
        raise RuntimeError("OCI OpenAI-compatible response did not include text content.")
