from swarm_alignment.client import split_thinking


def test_split_thinking():
    assert split_thinking("<think>\nplan A\n</think>\n\nhello") == ("plan A", "hello")
    assert split_thinking("<think>cut off mid tho") == ("cut off mid tho", "")
    assert split_thinking("plain answer") == ("", "plain answer")
    assert split_thinking(None) == ("", "")
    assert split_thinking("<think>\n\n</think>\n\n") == ("", "")
