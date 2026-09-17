"""Source-qualified token observations; unknown values are never zeros."""
from __future__ import annotations

import hashlib
import importlib.metadata
from pathlib import Path


def observation(value, source, status=None):
    return {"value": value, "source": source, "status": status or ("known" if value is not None else "missing")}


class TokenCounter:
    def __init__(self, mappings, loaders=None, *, tokenizer=None):
        self.mappings = mappings
        self.tokenizer = tokenizer
        self.cache = dict(loaders or {})
        self.identities = {}
        self.errors = {}

    @classmethod
    def from_config(cls, config):
        # Historical captures retain their original per-model configuration.
        return cls(config.get("tokenizers", {}), tokenizer=config.get("tokenizer"))

    def prepare(self):
        if self.tokenizer is not None:
            self._load(None)

    def _key(self, model):
        return "run" if self.tokenizer is not None else model

    def _load(self, model):
        model = self._key(model)
        if model in self.cache:
            return self.cache[model]
        spec = self.tokenizer if self.tokenizer is not None else self.mappings.get(model)
        if not spec or not spec.get("path"):
            return None
        try:
            from transformers import AutoTokenizer
            path = Path(spec["path"])
            hasher = hashlib.sha256()
            for name in ("tokenizer.json", "tokenizer_config.json", "special_tokens_map.json", "vocab.json", "merges.txt", "tokenizer.model"):
                file = path / name
                if file.is_file():
                    hasher.update(name.encode())
                    with file.open("rb") as stream:
                        for chunk in iter(lambda: stream.read(1048576), b""):
                            hasher.update(chunk)
            tokenizer = AutoTokenizer.from_pretrained(path, local_files_only=True, trust_remote_code=False)
            self.cache[model] = tokenizer
            self.identities[model] = {"path": str(path), "files_sha256": hasher.hexdigest(),
                                     "transformers": importlib.metadata.version("transformers"),
                                     "add_special_tokens": False}
            return tokenizer
        except Exception as exc:
            self.errors[model] = type(exc).__name__
            self.cache[model] = None
            return None

    def measure(self, response, request_model):
        model = response.get("model") or request_model
        if not isinstance(model, str):
            model = None
        tokenizer = self._load(model)
        def count(text, status=None):
            if status:
                return observation(None, "local_reencode", status)
            if not isinstance(text, str):
                return observation(None, "local_reencode", "not_observable")
            if tokenizer is None:
                return observation(None, "local_reencode", "tokenizer_unavailable")
            try:
                return observation(len(tokenizer.encode(text, add_special_tokens=False)), "local_reencode")
            except Exception:
                return observation(None, "local_reencode", "tokenizer_error")
        per_choice = []
        for choice in response.get("choices", []):
            msg = choice["message"]
            functions = [t["function"] for t in msg.get("tool_calls", [])]
            if "function_call" in msg:
                functions.append(msg["function_call"])
            content = msg.get("content")
            metrics = {"reasoning": count(choice["reasoning"], "ambiguous" if "ambiguous_reasoning" in choice["warnings"] else None),
                       "content": count("" if content is None else content)}
            for key, field in (("tool_name", "name"), ("tool_arguments", "arguments")):
                values = [count(fn.get(field)) for fn in functions] or [count("")]
                metrics[key] = observation(sum(v["value"] for v in values), "local_component_sum") if all(v["value"] is not None for v in values) else next(v for v in values if v["value"] is None)
            values = [v["value"] for v in metrics.values()]
            total = sum(values) if all(v is not None for v in values) else None
            metrics["output_text"] = observation(total, "local_component_sum")
            metrics["visible_reasoning_share"] = metrics["reasoning"]["value"] / total if total else None
            metrics["choice_index"] = choice["index"]
            per_choice.append(metrics)
        api = {}
        usage = response.get("usage") or {}
        paths = {"prompt": ("prompt_tokens",), "completion": ("completion_tokens",), "total": ("total_tokens",),
                 "cached_prompt": ("prompt_tokens_details", "cached_tokens"),
                 "reasoning": ("completion_tokens_details", "reasoning_tokens")}
        for name, path in paths.items():
            value = usage
            for key in path:
                value = value.get(key) if isinstance(value, dict) else None
            valid = type(value) is int and value >= 0
            api[name] = observation(value if valid else None, "usage." + ".".join(path),
                                    "known" if valid else "missing" if value is None else "invalid")
        return {"model": model, "tokenizer_ref": self.identities.get(self._key(model)), "api": api,
                "choices": per_choice, "hidden_probe_tokens": None, "total_model_generated_tokens": None}
