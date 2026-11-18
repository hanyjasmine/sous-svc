from functools import cache

import anthropic


@cache
def get_client() -> anthropic.Anthropic:
    # reads ANTHROPIC_API_KEY from the env
    return anthropic.Anthropic()
