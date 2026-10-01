from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from caption import chatgpt


def page(url: str, *, name: str = "", focused: bool = False, visible: bool = False):
    result = Mock()
    result.url = url

    def evaluate(script: str):
        return {
            "window.name": name,
            "document.hasFocus()": focused,
            "document.visibilityState": "visible" if visible else "hidden",
        }[script]

    result.evaluate.side_effect = evaluate
    return result


class CurrentChatGPTTabTests(unittest.TestCase):
    def test_visible_locator_ignores_hidden_duplicates(self) -> None:
        browser_page = Mock()
        visible_match = Mock()
        browser_page.locator.return_value.filter.return_value.first = visible_match

        result = chatgpt._visible(browser_page, chatgpt.COMPOSER)

        self.assertIs(result, visible_match)
        browser_page.locator.assert_called_once_with(chatgpt.COMPOSER)
        browser_page.locator.return_value.filter.assert_called_once_with(visible=True)

    def test_file_input_is_scoped_to_the_visible_composer_form(self) -> None:
        composer = Mock()
        form = Mock()
        composer.locator.return_value = form

        result = chatgpt._composer_form(composer)

        self.assertIs(result, form)
        composer.locator.assert_called_once_with("xpath=ancestor::form[1]")

    def test_attachment_must_appear_in_the_visible_composer_before_send(self) -> None:
        form = Mock()
        buttons = Mock()
        form.locator.return_value = buttons
        buttons.evaluate_all.return_value = ["Remove file transcript.txt"]

        chatgpt._wait_attachments_visible(form, [Path("transcript.txt")], timeout_s=0.01)

        form.locator.assert_called_once_with("button[aria-label^='Remove ']")

    def test_prefers_the_focused_open_chatgpt_tab(self) -> None:
        older = page("https://chatgpt.com/c/old", visible=True)
        focused = page("https://chatgpt.com/c/current", focused=True, visible=True)
        browser = SimpleNamespace(contexts=[SimpleNamespace(pages=[older, focused])])

        chosen, created = chatgpt._current_chatgpt_tab(browser)

        self.assertIs(chosen, focused)
        self.assertFalse(created)

    def test_does_not_reuse_the_dedicated_vocab_tab(self) -> None:
        voca = page(
            "https://chatgpt.com/c/voca",
            name=chatgpt.VOCAB_TAB,
            focused=True,
            visible=True,
        )
        ordinary = page("https://chatgpt.com/c/chat", visible=False)
        browser = SimpleNamespace(contexts=[SimpleNamespace(pages=[voca, ordinary])])

        chosen, created = chatgpt._current_chatgpt_tab(browser)

        self.assertIs(chosen, ordinary)
        self.assertFalse(created)

    def test_creates_a_tab_when_only_voca_is_open(self) -> None:
        voca = page("https://chatgpt.com/c/voca", name=chatgpt.VOCAB_TAB, focused=True)
        browser = SimpleNamespace(contexts=[SimpleNamespace(pages=[voca])])
        created_page = Mock()

        with patch.object(chatgpt, "_open_background_tab", return_value=created_page):
            chosen, created = chatgpt._current_chatgpt_tab(browser)

        self.assertIs(chosen, created_page)
        self.assertTrue(created)


if __name__ == "__main__":
    unittest.main()
