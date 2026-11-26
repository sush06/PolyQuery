from copy import deepcopy
from unittest.mock import Mock

import pytest

from benchmarks.strong_model_binding import NativeClient


def request(client):
    return client.create(model="test", messages=[{"role":"system","content":"return typed output"},
                                                   {"role":"user","content":"test"}],
                         temperature=0,max_tokens=100,
                         response_format={"json_schema":{"schema":{"type":"object"}}})


def test_shared_provider_error_does_not_create_an_extra_call(monkeypatch):
    provider = Mock(side_effect=TimeoutError("provider timeout"))
    monkeypatch.setattr("benchmarks.strong_model_binding.api",provider)
    first, paired = NativeClient(True), NativeClient(True)
    with pytest.raises(TimeoutError):
        request(first)
    paired.replay=first.draws[0]
    with pytest.raises(TimeoutError):
        request(paired)
    provider.assert_called_once()
    assert not paired.draws


def test_replayed_generation_preserves_usage_and_payload(monkeypatch):
    provider = Mock(return_value={"message":{"content":"{}","thinking":"discard"},
                                   "done_reason":"stop","prompt_eval_count":5,"eval_count":8})
    monkeypatch.setattr("benchmarks.strong_model_binding.api",provider)
    first, paired = NativeClient(True), NativeClient(True)
    expected=request(first)
    paired.replay=deepcopy(first.draws[0])
    actual=request(paired)
    assert expected == actual
    assert actual.usage.total_tokens == 13
    assert first.draws[0]["thinking"] is True
    assert "discard" not in str(first.draws[0])
    assert not paired.draws
    provider.assert_called_once()
