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


READ_BACK_TEXT = (
    "Just to confirm: Doctor Anil Sharma, on Tuesday, at five in the evening. Shall I book it?"
)
READ_BACK = ("assistant", READ_BACK_TEXT, False)


def check(h, after=1):
    return check_yes(h, after_user_turn=after, read_back=READ_BACK_TEXT)


def test_yes_after_the_exact_read_back_is_accepted():
    h = chat(("user", "Five PM please", False), READ_BACK, ("user", "Yes", False))
    assert user_turns(h) == 2
    assert check(h).ok


def test_a_yes_from_before_the_read_back_does_not_count():
    h = chat(("user", "Yes, five PM", False))
    assert not check(h).ok


def test_yes_to_an_interrupted_read_back_means_read_it_again():
    cut = ("assistant", READ_BACK_TEXT, True)
    h = chat(("user", "Five PM please", False), cut, ("user", "Haan", False))
    result = check(h)
    assert not result.ok and result.read_back_again


def test_yes_to_a_paraphrase_means_read_it_again():
    # The LLM said its own version (maybe the wrong day): the yes isn't to what will be written.
    llm_version = ("assistant", "So Wednesday at six with Dr. Sharma, okay?", False)
    h = chat(("user", "Five PM", False), llm_version, ("user", "Yes", False))
    result = check(h)
    assert not result.ok and result.read_back_again


def test_yes_after_another_answer_means_read_it_again():
    h = chat(
        ("user", "Five PM", False),
        READ_BACK,
        ("user", "What's the fee?", False),
        ("assistant", "It is five hundred rupees.", False),
        ("user", "Okay, yes", False),
    )
    assert check(h).read_back_again


def test_mixed_answer_is_refused_without_reading_again():
    h = chat(("user", "Five PM", False), READ_BACK, ("user", "Yes but six is better", False))
    result = check(h)
    assert not result.ok and not result.read_back_again


def test_tts_spacing_and_punctuation_do_not_matter():
    spoken = ("assistant", READ_BACK_TEXT.replace(",", "").replace(":", " ..."), False)
    h = chat(("user", "Five PM", False), spoken, ("user", "Yes", False))
    assert check(h).ok
