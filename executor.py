#!/usr/bin/env python3
import curses
import subprocess
import argparse
import sys
import os
import shlex
from time import sleep

# --- Configuration ---
PLACEHOLDER = "$object"
STATUS_PENDING = "[ ]"
STATUS_SUCCESS = "[✔]"
STATUS_FAIL = "[✖]"
STATUS_CURRENT = "[▶]"

# --- Main TUI Application ---
def draw_title(win, title):
    """Draws a title bar on a window."""
    win.erase()
    h, w = win.getmaxyx()
    win.border(0)
    win.addstr(0, 2, f" {title} ", curses.A_BOLD)

def draw_list(win, objects, statuses, current_index, scroll_offset):
    """Draws the list of objects and their statuses, handling scrolling."""
    draw_title(win, "Objects")
    h, w = win.getmaxyx()
    visible_lines = h - 2

    for i in range(visible_lines):
        obj_index = scroll_offset + i
        if obj_index >= len(objects):
            break

        status_symbol = statuses[obj_index]

        # Determine color and style
        color = curses.color_pair(0) # Default
        if status_symbol == STATUS_SUCCESS:
            color = curses.color_pair(1) # Green
        elif status_symbol == STATUS_FAIL:
            color = curses.color_pair(2) # Red

        style = curses.A_NORMAL
        if obj_index == current_index:
            style = curses.A_REVERSE
            status_symbol = STATUS_CURRENT

        # Truncate object name if it's too long
        max_len = w - len(status_symbol) - 4
        obj = objects[obj_index]
        display_obj = (obj[:max_len] + '..') if len(obj) > max_len else obj

        win.addstr(i + 1, 2, f"{status_symbol} {display_obj}", color | style)
    win.refresh()


def draw_output(win, content):
    """Draws content to the output window, handling scrolling."""
    h, w = win.getmaxyx()
    win.addstr(content)
    win.refresh()

def show_prompt(stdscr, message):
    """Displays a blocking prompt at the bottom of the screen."""
    h, w = stdscr.getmaxyx()
    prompt_win = curses.newwin(3, w, h - 3, 0)
    prompt_win.border()
    prompt_win.addstr(1, 2, message, curses.A_BOLD | curses.color_pair(3))
    prompt_win.refresh()
    return prompt_win.getch()


def tui_main(stdscr, args):
    """The main function to run the TUI."""
    # --- Curses Setup ---
    curses.curs_set(0)  # Hide the cursor
    stdscr.nodelay(True) # Non-blocking getch
    curses.start_color()
    curses.init_pair(1, curses.COLOR_GREEN, curses.COLOR_BLACK) # Success
    curses.init_pair(2, curses.COLOR_RED, curses.COLOR_BLACK)   # Fail
    curses.init_pair(3, curses.COLOR_YELLOW, curses.COLOR_BLACK)# Prompt

    # --- Read Objects ---
    try:
        with open(args.list, 'r') as f:
            objects = [line.strip() for line in f if line.strip()]
        if not objects:
            raise ValueError("Object list file is empty.")
    except (IOError, ValueError) as e:
        return f"Error: Cannot read or parse object list file.\n{e}"

    statuses = [STATUS_PENDING] * len(objects)
    scroll_offset = 0

    # --- Main Loop ---
    for i, obj in enumerate(objects):
        # --- Window Layout ---
        h, w = stdscr.getmaxyx()
        list_w = w // 4
        output_w = w - list_w

        list_win = stdscr.subwin(h, list_w, 0, 0)
        output_win = stdscr.subwin(h, output_w, 0, list_w)

        # --- Adjust scroll offset to keep current item in view ---
        list_h = list_win.getmaxyx()[0]
        visible_lines = list_h - 2
        if i >= scroll_offset + visible_lines:
            scroll_offset = i - visible_lines + 1
        if i < scroll_offset:
            scroll_offset = i

        # --- Draw Initial State for Current Object ---
        draw_list(list_win, objects, statuses, i, scroll_offset)
        draw_title(output_win, f"Live Output: {obj}")

        # Create an inset window for the scrolling content
        output_content_win = output_win.derwin(h - 2, output_w - 2, 1, 1)
        output_content_win.scrollok(True)
        output_content_win.idlok(True)

        # --- Execute Command using shlex for safety ---
        try:
            # Quote the object to handle spaces and special chars safely.
            quoted_obj = shlex.quote(obj)
            # Replace placeholder and then split the command.
            command_str = args.command.replace(PLACEHOLDER, quoted_obj)
            command_args = shlex.split(command_str)

            process = subprocess.Popen(
                command_args,
                shell=False, # IMPORTANT: shell=False is safer
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )

            # Stream output in real-time
            while True:
                line = process.stdout.readline()
                if not line:
                    break
                draw_output(output_content_win, line)
                # Allow for keyboard input to quit mid-process
                key = stdscr.getch()
                if key in [ord('q'), ord('Q')]:
                    process.terminate()
                    return "Execution aborted by user."

            process.wait()

            if process.returncode == 0:
                statuses[i] = STATUS_SUCCESS
            else:
                statuses[i] = STATUS_FAIL

        except Exception as e:
            statuses[i] = STATUS_FAIL
            draw_output(output_content_win, f"--- SCRIPT ERROR ---{e}")

        # --- Update final status and redraw ---
        draw_list(list_win, objects, statuses, i, scroll_offset)

        # --- Handle Failure ---
        if statuses[i] == STATUS_FAIL:
            choice = show_prompt(stdscr, "Command failed. (s)kip to next object or (e)xit?")
            if choice in [ord('e'), ord('E')]:
                return "Execution aborted due to failure."

    # --- End of Execution ---
    show_prompt(stdscr, "All jobs completed. Press any key to exit.")
    stdscr.nodelay(False) # Blocking getch
    stdscr.getch()
    return "Execution finished successfully."


# --- Argument Parser and Entry Point ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="A TUI wrapper to execute a command over a list of objects.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=f'''
Example Usage:
  ./executor.py --list servers.txt --command 'ssh {PLACEHOLDER} "echo $HOSTNAME ; df -h /"'

Details:
  - The command string must contain the placeholder '{PLACEHOLDER}'.
  - The script uses `shlex` to parse the command, which prevents the local
    shell from expanding variables like `$HOSTNAME`. Variables will be correctly
    expanded on the remote machine.
  - Object names with spaces or special characters are handled automatically.
  - Press 'q' during a command's execution to abort the entire script.
'''
    )
    parser.add_argument(
        "-l", "--list",
        required=True,
        help="Path to a text file containing a list of objects (one per line)."
    )
    parser.add_argument(
        "-c", "--command",
        required=True,
        help=f"The command to execute. Use '{PLACEHOLDER}' as a placeholder for the object."
    )

    if len(sys.argv) == 1:
        parser.print_help(sys.stderr)
        sys.exit(1)

    args = parser.parse_args()

    if not os.path.exists(args.list):
        print(f"Error: The file '{args.list}' does not exist.", file=sys.stderr)
        sys.exit(1)

    if PLACEHOLDER not in args.command:
        print(f"Error: The command string must include the placeholder '{PLACEHOLDER}'.", file=sys.stderr)
        sys.exit(1)

    try:
        final_message = curses.wrapper(tui_main, args)
        print(final_message)
    except curses.error as e:
        print(f"A terminal error occurred: {e}")
        print("Please ensure your terminal is large enough and supports colors.")
    except KeyboardInterrupt:
        print("\nExecution cancelled by user.")
