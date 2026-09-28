# SVC-C2-134 — output guard: prohibited floor, assertive lexicon, markers.

from src.services import output_guard


def test_find_prohibited_keys_nested():
    obj = {"issues": [{"three_way": [{"penalty": "x"}]}]}
    assert "penalty" in output_guard.find_prohibited_keys(obj, set(output_guard.PROHIBITED_FLOOR))


def test_no_prohibited_keys_clean():
    obj = {"issues": [{"issue_summary": "neutral"}]}
    assert output_guard.find_prohibited_keys(obj, set(output_guard.PROHIBITED_FLOOR)) == []


def test_assertive_phrase_trips():
    v = output_guard.assertive_violations([("p", "Team Bravo is disqualified.")])
    assert v and v[0]["path"] == "p"


def test_neutral_question_does_not_trip():
    # a question containing the bare word 'disqualified' must NOT trip (phrase-based).
    assert output_guard.assertive_violations([("p", "Should the player be disqualified?")]) == []


def test_japanese_assertive_phrase_trips():
    assert output_guard.assertive_violations([("p", "この選手は失格とする。")])


def test_extract_markers():
    assert output_guard.extract_markers("see [c1] and [c2] here") == ["c1", "c2"]


def test_extract_markers_none():
    assert output_guard.extract_markers("no markers here") == []
