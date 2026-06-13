import os
import sys
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from medai_api import langfuse_tracing


class LangfuseTracingTest(unittest.TestCase):
    def test_configure_is_disabled_without_credentials(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(langfuse_tracing.langfuse_configured())

    def test_configure_can_be_disabled_explicitly(self) -> None:
        env = {
            "LANGFUSE_ENABLED": "0",
            "LANGFUSE_PUBLIC_KEY": "pk-test",
            "LANGFUSE_SECRET_KEY": "sk-test",
            "LANGFUSE_BASE_URL": "https://cloud.langfuse.com",
        }
        with patch.dict(os.environ, env, clear=True):
            self.assertFalse(langfuse_tracing.langfuse_configured())

    def test_configure_accepts_host_alias(self) -> None:
        env = {
            "LANGFUSE_PUBLIC_KEY": "pk-test",
            "LANGFUSE_SECRET_KEY": "sk-test",
            "LANGFUSE_HOST": "https://cloud.langfuse.com",
        }
        with patch.dict(os.environ, env, clear=True):
            self.assertTrue(langfuse_tracing.langfuse_configured())
            self.assertEqual(
                langfuse_tracing.langfuse_base_url(),
                "https://cloud.langfuse.com",
            )
            self.assertEqual(os.environ["LANGFUSE_BASE_URL"], "https://cloud.langfuse.com")

    def test_flush_is_safe_before_configuration(self) -> None:
        langfuse_tracing.flush_langfuse()

    def test_configure_keeps_openai_batch_processor_alongside_langfuse(self) -> None:
        """OpenAIAgentsInstrumentor() replaces the default trace processor list with
        its own OTel-based one, which silently kills exports to platform.openai.com.
        configure_langfuse must re-add BatchTraceProcessor so both destinations
        receive spans.
        """
        env = {
            "LANGFUSE_PUBLIC_KEY": "pk-test",
            "LANGFUSE_SECRET_KEY": "sk-test",
            "LANGFUSE_BASE_URL": "https://cloud.langfuse.com",
            "OPENAI_API_KEY": "sk-test-openai",
        }

        from agents.tracing import set_trace_processors
        from agents.tracing.setup import get_trace_provider

        # Mock instrumentor that replicates the real behavior: replaces the
        # SDK's processor list with a single OTel-based processor.
        class FakeOTelProcessor:
            def on_trace_start(self, t): pass
            def on_trace_end(self, t): pass
            def on_span_start(self, s): pass
            def on_span_end(self, s): pass
            def shutdown(self): pass
            def force_flush(self): pass

        def fake_instrument():
            set_trace_processors([FakeOTelProcessor()])

        fake_module = types.SimpleNamespace(
            OpenAIAgentsInstrumentor=lambda: types.SimpleNamespace(instrument=fake_instrument)
        )
        fake_langfuse = types.SimpleNamespace(get_client=lambda: object())

        provider = get_trace_provider()
        multi = getattr(provider, "_multi_processor", None) or getattr(
            provider, "_processor", None
        )
        original_processors = list(multi._processors)

        try:
            with patch.dict(os.environ, env, clear=False):
                with patch.dict(
                    sys.modules,
                    {
                        "openinference.instrumentation.openai_agents": fake_module,
                        "langfuse": fake_langfuse,
                    },
                ):
                    with patch.object(langfuse_tracing, "_instrumented", False):
                        self.assertTrue(langfuse_tracing.configure_langfuse())

            processor_types = {type(p).__name__ for p in multi._processors}
            self.assertIn(
                "BatchTraceProcessor",
                processor_types,
                f"OpenAI BatchTraceProcessor must remain registered after Langfuse"
                f" instrumentation; got {processor_types}",
            )
        finally:
            set_trace_processors(original_processors)

    def test_langfuse_attributes_preserves_application_exceptions(self) -> None:
        @contextmanager
        def fake_propagate_attributes(**_kwargs):
            yield

        fake_langfuse = types.SimpleNamespace(propagate_attributes=fake_propagate_attributes)

        with patch.object(langfuse_tracing, "_client", object()):
            with patch.dict(sys.modules, {"langfuse": fake_langfuse}):
                with self.assertRaisesRegex(ValueError, "boom"):
                    with langfuse_tracing.langfuse_attributes():
                        raise ValueError("boom")
