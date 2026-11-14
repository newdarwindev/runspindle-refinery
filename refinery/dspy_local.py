"""Pinned DSPy Predict compatibility over a deterministic, network-free local LM.

Executed only by the separately prepared Linux arm64 lane. Synthetic completions
and costs remain synthetic; no provider fallback or optimizer quality claim.
"""

import json
from types import SimpleNamespace

from .evaluation import ScriptedProvider, Completion
from .lineage import digest


class LocalDSPyProvider(ScriptedProvider):
    provider_id = "dspy.local.v1"

    def __init__(self, script, *, clock, dependency_sha256):
        from .evaluation import _hash

        _hash(dependency_sha256)
        super().__init__(script, clock=clock)
        self.dependency_sha256 = dependency_sha256
        self.lm_calls = []

    @property
    def sha256(self):
        return digest(
            json.dumps(
                {
                    "adapter": self.provider_id,
                    "dspy": "3.1.0",
                    "dependencies": self.dependency_sha256,
                    "script": super().sha256,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        )

    def complete(self, request):
        import dspy

        if dspy.__version__ != "3.1.0":
            raise ValueError("unqualified DSPy version")
        reply = super().complete(request)
        if reply.outcome != "completed" or type(reply.content) is not bytes:
            return reply
        owner = self
        count = 0

        class LocalLM(dspy.BaseLM):
            def __init__(self):
                super().__init__(
                    model="synthetic/local", cache=False, temperature=0, max_tokens=4096
                )

            def forward(self, prompt=None, messages=None, **kwargs):
                nonlocal count
                count += 1
                if count != 1:
                    raise ValueError("unexpected unbudgeted DSPy retry")
                owner.lm_calls.append(
                    {
                        "request": request,
                        "message_sha256": digest(
                            json.dumps(messages, sort_keys=True).encode()
                        ),
                    }
                )
                text = (
                    "[[ ## answer ## ]]\n"
                    + reply.content.decode("utf-8")
                    + "\n[[ ## completed ## ]]"
                )
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
                    usage={"prompt_tokens": 0, "completion_tokens": reply.tokens},
                    model="synthetic/local",
                )

        # ChatAdapter uses the supported BaseLM path; history and fallback are disabled.
        adapter = dspy.ChatAdapter(use_native_function_calling=False)
        with dspy.context(
            lm=LocalLM(), adapter=adapter, disable_history=True, callbacks=[]
        ):
            prediction = dspy.Predict("case -> answer")(
                case=json.dumps(vars(request), sort_keys=True)
            )
        if count != 1:
            raise ValueError("DSPy call count changed")
        return Completion(
            prediction.answer.encode(),
            reply.tokens,
            reply.cost_microunits,
            reply.safety,
            reply.latency_ms,
            reply.outcome,
        )
