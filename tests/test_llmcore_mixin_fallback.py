"""Regression tests for llmcore MixinSession fallback handling."""
import os
import sys
import unittest


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


class _Backend:
    def __init__(self, name, mode):
        self.name = name
        self.mode = mode
        self.history = [{"role": "user", "content": "retry from backend history"}]
        self.max_retries = 99

    def raw_ask(self, *args, **kwargs):
        mode = self.mode

        def gen():
            if mode == "yield_broken_return_ok":
                yield "[!!! 流异常中断] provider stream broke"
                return ["provider_returned_ok_anyway"]
            if mode == "fallback_ok":
                yield "fallback chunk"
                return ["fallback return"]
            if mode == "return_stream_broken":
                yield "prefix"
                return ["prefix", "[!!! 流异常中断] provider stream broke"]
            raise AssertionError(f"unexpected mode: {mode}")

        return gen()


class _Wrapper:
    def __init__(self, backend):
        self.backend = backend


class TestMixinSessionFallback(unittest.TestCase):
    def _make_mixin(self, primary_mode):
        from llmcore import MixinSession

        primary = _Backend("primary", primary_mode)
        fallback = _Backend("fallback", "fallback_ok")
        return MixinSession(
            [_Wrapper(primary), _Wrapper(fallback)],
            {"llm_nos": ["primary", "fallback"], "max_retries": 1, "base_delay": 0},
        )

    @staticmethod
    def _drain(gen):
        chunks = []
        try:
            while True:
                chunks.append(next(gen))
        except StopIteration as e:
            return chunks, e.value

    def test_last_chunk_stream_broken_triggers_fallback_even_when_return_value_is_normal(self):
        mixin = self._make_mixin("yield_broken_return_ok")

        chunks, return_value = self._drain(mixin._raw_ask([{"role": "user", "content": "hi"}]))

        self.assertEqual(chunks, ["[!!! 流异常中断] provider stream broke", "fallback chunk"])
        self.assertEqual(return_value, ["fallback return"])
        self.assertEqual(mixin.model_trace["actual"], "fallback")
        self.assertEqual(mixin.model_trace["fallback_count"], 1)

    def test_return_value_stream_broken_still_triggers_fallback(self):
        mixin = self._make_mixin("return_stream_broken")

        chunks, return_value = self._drain(mixin._raw_ask([{"role": "user", "content": "hi"}]))

        self.assertEqual(chunks, ["prefix", "fallback chunk"])
        self.assertEqual(return_value, ["fallback return"])
        self.assertEqual(mixin.model_trace["actual"], "fallback")
        self.assertEqual(mixin.model_trace["fallback_count"], 1)


if __name__ == "__main__":
    unittest.main()
