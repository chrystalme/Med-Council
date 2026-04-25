import os
import sys
import types
import unittest
from pathlib import Path
from contextlib import contextmanager
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import langfuse_tracing


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
