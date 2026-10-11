import pytest
from livekit.agents import llm

from agent.confirm import check_yes, is_clear_yes, user_turns


@pytest.mark.parametrize(
    "text",
    [
        "Yes",
        "Yes please.",
        "Haan ji, kar do",
        "हाँ जी",
        "theek hai",
        "Yeah, that's correct.",
        "Okay, book it",
        "haan na",
    ],
)
def test_clear_yes(text):
    assert is_clear_yes(text)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "hmm",
        "No",
        "Yes but make it six",  # a change, not a yes
        "Okay wait",
        "haan, lekin Tuesday",
        "Actually Tuesday is better",
        "nahi",
        "Can you tell me the address first and then also the fees please okay yes",  # too long
    ],
)
def test_not_a_clear_yes(text):
    assert not is_clear_yes(text)


def chat(*turns: tuple[str, str, bool]) -> llm.ChatContext:
    return llm.ChatContext(
        items=[llm.ChatMessage(role=r, content=[t], interrupted=i) for r, t, i in turns]
    )


READ_BACK = ("assistant", "Dr. Sharma, Monday at five PM. Shall I book it?", False)


def test_yes_after_a_full_read_back_is_accepted():
    h = chat(("user", "Five PM please", False), READ_BACK, ("user", "Yes", False))
    assert user_turns(h) == 2
    assert check_yes(h, after_user_turn=1).ok


def test_the_turn_that_chose_the_time_is_not_the_yes():
    h = chat(("user", "Yes, five PM", False))
    assert not check_yes(h, after_user_turn=1).ok


def test_yes_to_an_interrupted_read_back_is_refused():
    cut = ("assistant", "Dr. Sharma, Monday at", True)
    h = chat(("user", "Five PM please", False), cut, ("user", "Haan", False))
    result = check_yes(h, after_user_turn=1)
    assert not result.ok and "interrupted" in result.reason


def test_mixed_answer_is_refused():
    h = chat(("user", "Five PM", False), READ_BACK, ("user", "Yes but six is better", False))
    assert not check_yes(h, after_user_turn=1).ok
