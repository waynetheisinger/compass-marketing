"""Interactive TUI using prompt_toolkit."""

from typing import List, Dict, Optional
from prompt_toolkit import prompt
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.formatted_text import FormattedText


class MatchSelector:
    """Interactive selector for choosing matches."""

    def __init__(self, batch_size: int = 5):
        """Initialize selector with batch size."""
        self.batch_size = batch_size
        self.current_selection = 0
        self.current_page = 0
        self.matches = []

    def display_matches(
        self,
        sku_a: str,
        title_a: str,
        matches: List[Dict],
        page: int = 0,
        selected: int = 0
    ) -> str:
        """Format and display matches for the current page."""
        start_idx = page * self.batch_size
        end_idx = min(start_idx + self.batch_size, len(matches))
        page_matches = matches[start_idx:end_idx]

        output = [f"\nA: [{sku_a}] \"{title_a}\""]
        output.append("")

        for i, match in enumerate(page_matches):
            global_idx = start_idx + i
            marker = "→" if global_idx == selected else " "
            sku_b = match['sku_b']
            title_b = match['title_b']
            score = match['score']

            line = f"{marker} {global_idx + 1}. [{sku_b}] {title_b[:60]}"
            line += f" (score {score:.0f})"
            output.append(line)

        output.append("")
        output.append("─" * 80)
        output.append("Navigation: ↑/↓ = move  • Enter = select  • n = next  • p = prev")
        output.append("Actions:    s = skip    • u = unmatched   • q = quit and save progress")
        output.append("─" * 80)

        total_pages = (len(matches) + self.batch_size - 1) // self.batch_size
        output.append(f"\nPage {page + 1}/{total_pages} | Showing {start_idx + 1}-{end_idx} of {len(matches)}")

        return "\n".join(output)

    def select_match(
        self,
        sku_a: str,
        title_a: str,
        matches: List[Dict]
    ) -> Optional[Dict]:
        """
        Interactive selection interface.

        Returns:
            - Selected match dict
            - {'action': 'skip'} if skipped
            - {'action': 'unmatched'} if marked unmatched
            - {'action': 'quit'} if user quits
            - None if no selection made
        """
        if not matches:
            print(f"\n{'='*80}")
            print(f"No matches found for [{sku_a}] \"{title_a}\"")
            print(f"{'='*80}")
            response = input("\n[y] Mark as unmatched | [n] Skip | [q] Quit: ").strip().lower()
            if response == 'y':
                return {'action': 'unmatched'}
            elif response == 'q' or response == 'quit':
                return {'action': 'quit'}
            return {'action': 'skip'}

        self.matches = matches
        self.current_selection = 0
        self.current_page = 0

        while True:
            print("\033[2J\033[H")

            display = self.display_matches(
                sku_a,
                title_a,
                matches,
                self.current_page,
                self.current_selection
            )
            print(display)

            try:
                action = input("\nChoice: ").strip().lower()

                if action == 'q':
                    return {'action': 'quit'}

                elif action == 's':
                    return {'action': 'skip'}

                elif action == 'u':
                    return {'action': 'unmatched'}

                elif action == 'n':
                    total_pages = (len(matches) + self.batch_size - 1) // self.batch_size
                    if self.current_page < total_pages - 1:
                        self.current_page += 1
                        self.current_selection = self.current_page * self.batch_size

                elif action == 'p':
                    if self.current_page > 0:
                        self.current_page -= 1
                        self.current_selection = self.current_page * self.batch_size

                elif action == 'up' or action == 'k':
                    if self.current_selection > 0:
                        self.current_selection -= 1
                        self.current_page = self.current_selection // self.batch_size

                elif action == 'down' or action == 'j':
                    if self.current_selection < len(matches) - 1:
                        self.current_selection += 1
                        self.current_page = self.current_selection // self.batch_size

                elif action == '' or action == 'enter':
                    selected_match = matches[self.current_selection]
                    return selected_match

                elif action.isdigit():
                    idx = int(action) - 1
                    if 0 <= idx < len(matches):
                        return matches[idx]
                    else:
                        print(f"Invalid selection: {action}")
                        input("Press Enter to continue...")

            except (KeyboardInterrupt, EOFError):
                return {'action': 'quit'}
